"""Lead analytics — ROI, win-rate, funnel, monthly trend.

A single endpoint replaces the per-lead JS aggregation we used to do
client-side, which couldn't see DealExpense rows (those live in a
separate table) and missed the user's full lead list whenever the
frontend only had a paginated slice. Server-side we walk every
LeadItem the user owns plus their expense rows in two queries, then
roll up the numbers into one dashboard payload.

Concepts:

* "Pursued" = leads whose status crossed beyond ``watching``. That's
  the denominator for win-rate; passively-watched listings don't
  count as a missed sale.
* "Won" = ``sold_price_byn`` is set (regardless of status string).
  Some users mark deals ``closed`` and never set ``sold``, so we
  fall back to anything with a sold price recorded.
* "Days to close" = sold_at − created_at, in days. Only the leads
  with both timestamps populated contribute.
* "Funnel" = count by status, ordered the way the kanban renders so
  the JS chart can plot the bars without re-sorting.

Period filter is a soft window: leads outside it still count for
funnel (they're the user's history), but the ROI / win-rate /
revenue numbers are scoped to ``leads.created_at >= cutoff``.
"""

from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.dependencies import (
    get_session_factory_dependency,
    get_telegram_user,
)
from api.limiter import limiter
from api.middleware.telegram_auth import TelegramInitData
from api.models import DealExpense, LeadItem
from api.schemas import LeadAnalyticsResponse, LeadFunnelStage, LeadMonthlyStat
from api.services.workflow_store import resolve_user_id

router = APIRouter(tags=["analytics"])


# Status order shown in the funnel chart — left to right roughly
# mirrors the kanban (watching → new → in_progress → researching →
# bought → sold/closed). ``skipped`` lives at the end since it's
# the explicit "no sale" terminal state.
_FUNNEL_STATUS_ORDER: tuple[tuple[str, str], ...] = (
    ("watching", "Слежу"),
    ("new", "Новые"),
    ("in_progress", "В работе"),
    ("researching", "Исследую"),
    ("bought", "Куплено"),
    ("sold", "Продано"),
    ("closed", "Закрыто"),
    ("skipped", "Пропущено"),
)
# Statuses where the user has actively pursued the lead — this is
# the win-rate denominator. ``watching`` is excluded because it's
# essentially the wishlist.
_PURSUED_STATUSES = frozenset(
    {"new", "in_progress", "researching", "bought", "sold", "closed", "skipped"}
)


def _ensure_aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


@router.get("/analytics/leads", response_model=LeadAnalyticsResponse)
@limiter.limit("30/minute")
async def get_lead_analytics(
    request: Request,
    days: int = Query(default=90, ge=7, le=365, description="Window for ROI/win-rate"),
    telegram_user: TelegramInitData = Depends(get_telegram_user),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory_dependency),
) -> LeadAnalyticsResponse:
    cutoff = datetime.now(UTC) - timedelta(days=days)

    async with session_factory() as session:
        user_id = await resolve_user_id(session, telegram_user_id=telegram_user.user_id)
        if user_id is None:
            return LeadAnalyticsResponse(
                period_days=days,
                total_leads=0,
                pursued_leads=0,
                sold_leads=0,
                skipped_leads=0,
                active_leads=0,
                win_rate_percent=0.0,
                total_revenue_byn=0.0,
                total_cost_byn=0.0,
                total_profit_byn=0.0,
                total_expenses_byn=0.0,
                average_roi_percent=0.0,
                average_days_to_close=0.0,
                median_days_to_close=0.0,
                funnel=[],
                monthly=[],
            )

        leads = list(
            (
                await session.execute(
                    select(LeadItem).where(LeadItem.user_id == user_id)
                )
            ).scalars()
        )
        # One bulk SUM — replaces the per-lead expense lookup.
        expense_rows = await session.execute(
            select(DealExpense.lead_id, func.sum(DealExpense.amount_byn))
            .where(DealExpense.user_id == user_id)
            .group_by(DealExpense.lead_id)
        )
        expenses_by_lead: dict[int, float] = {
            row[0]: float(row[1] or 0) for row in expense_rows
        }

    total_expenses = sum(expenses_by_lead.values())

    funnel_counter: Counter[str] = Counter()
    for lead in leads:
        funnel_counter[lead.status] += 1

    funnel = [
        LeadFunnelStage(status=status, label=label, count=funnel_counter.get(status, 0))
        for status, label in _FUNNEL_STATUS_ORDER
    ]

    # Window restriction for revenue/ROI/win-rate. Funnel is reported
    # over the full history because users want to see their lifetime
    # pipeline shape.
    in_window = [
        lead for lead in leads
        if (_ensure_aware(lead.created_at) or datetime.now(UTC)) >= cutoff
    ]

    pursued = [
        lead for lead in in_window if (lead.status or "") in _PURSUED_STATUSES
    ]
    won = [lead for lead in pursued if lead.sold_price_byn is not None]
    skipped = [lead for lead in pursued if (lead.status or "") == "skipped"]

    total_revenue = 0.0
    total_cost = 0.0
    total_profit = 0.0
    roi_values: list[float] = []
    days_to_close: list[float] = []

    for lead in won:
        sold_price = float(lead.sold_price_byn or 0)
        buy_price = float(lead.buy_price_byn) if lead.buy_price_byn is not None else 0.0
        expenses = expenses_by_lead.get(lead.id, 0.0)
        cost = buy_price + expenses
        profit = sold_price - cost
        total_revenue += sold_price
        total_cost += cost
        total_profit += profit
        if cost > 0:
            roi_values.append((profit / cost) * 100.0)
        sold_at = _ensure_aware(lead.sold_at)
        created_at = _ensure_aware(lead.created_at)
        if sold_at and created_at:
            delta_days = (sold_at - created_at).total_seconds() / 86400.0
            if delta_days >= 0:
                days_to_close.append(delta_days)

    win_rate = 0.0
    if pursued:
        win_rate = (len(won) / len(pursued)) * 100.0

    monthly_buckets: dict[str, dict[str, float]] = defaultdict(
        lambda: {"sold_count": 0.0, "revenue_byn": 0.0, "profit_byn": 0.0}
    )
    for lead in won:
        sold_at = _ensure_aware(lead.sold_at) or _ensure_aware(lead.created_at)
        if sold_at is None:
            continue
        bucket_key = sold_at.strftime("%Y-%m")
        bucket = monthly_buckets[bucket_key]
        bucket["sold_count"] += 1
        sold_price = float(lead.sold_price_byn or 0)
        cost = (
            float(lead.buy_price_byn or lead.price_byn or 0)
            + expenses_by_lead.get(lead.id, 0.0)
        )
        bucket["revenue_byn"] += sold_price
        bucket["profit_byn"] += sold_price - cost

    monthly = [
        LeadMonthlyStat(
            month=key,
            sold_count=int(values["sold_count"]),
            revenue_byn=round(values["revenue_byn"], 2),
            profit_byn=round(values["profit_byn"], 2),
        )
        for key, values in sorted(monthly_buckets.items())
    ]

    active_statuses = {"new", "in_progress", "researching", "bought"}
    active_leads = sum(1 for lead in leads if (lead.status or "") in active_statuses)

    return LeadAnalyticsResponse(
        period_days=days,
        total_leads=len(leads),
        pursued_leads=len(pursued),
        sold_leads=len(won),
        skipped_leads=len(skipped),
        active_leads=active_leads,
        win_rate_percent=round(win_rate, 1),
        total_revenue_byn=round(total_revenue, 2),
        total_cost_byn=round(total_cost, 2),
        total_profit_byn=round(total_profit, 2),
        total_expenses_byn=round(total_expenses, 2),
        average_roi_percent=round(statistics.fmean(roi_values), 1) if roi_values else 0.0,
        average_days_to_close=round(statistics.fmean(days_to_close), 1) if days_to_close else 0.0,
        median_days_to_close=round(statistics.median(days_to_close), 1) if days_to_close else 0.0,
        funnel=funnel,
        monthly=monthly,
    )
