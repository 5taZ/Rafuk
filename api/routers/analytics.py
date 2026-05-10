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
from sqlalchemy import case, func, select
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

        # Funnel counts via GROUP BY — avoids loading all lead ORM objects.
        funnel_rows = await session.execute(
            select(LeadItem.status, func.count(LeadItem.id))
            .where(LeadItem.user_id == user_id)
            .group_by(LeadItem.status)
        )
        funnel_counter: Counter[str] = Counter(dict(funnel_rows.all()))

        funnel = [
            LeadFunnelStage(status=status, label=label, count=funnel_counter.get(status, 0))
            for status, label in _FUNNEL_STATUS_ORDER
        ]

        # Bulk expense lookup (used for per-lead ROI).
        expense_rows = await session.execute(
            select(DealExpense.lead_id, func.sum(DealExpense.amount_byn))
            .where(DealExpense.user_id == user_id)
            .group_by(DealExpense.lead_id)
        )
        expenses_by_lead: dict[int, float] = {
            row[0]: float(row[1] or 0) for row in expense_rows
        }

        # Window-scoped metrics via SQL aggregation (no full ORM load).
        window_pursued = LeadItem.status.in_(list(_PURSUED_STATUSES))
        window_won = LeadItem.sold_price_byn.isnot(None)
        window_where = [
            LeadItem.user_id == user_id,
            window_pursued,
            LeadItem.created_at >= cutoff,
        ]

        agg = await session.execute(
            select(
                func.count(LeadItem.id).label("pursued_count"),
                func.count(case((window_won, 1))).label("won_count"),
                func.count(case((LeadItem.status == "skipped", 1))).label("skipped_count"),
                func.coalesce(
                    func.sum(case((window_won, LeadItem.sold_price_byn))), 0
                ).label("total_revenue"),
                func.coalesce(
                    func.sum(case((window_won, LeadItem.buy_price_byn))), 0
                ).label("total_buy_cost"),
            ).where(*window_where)
        )
        agg_row = agg.one()
        pursued_count = agg_row.pursued_count
        won_count = agg_row.won_count or 0
        skipped_count = agg_row.skipped_count or 0
        total_revenue = float(agg_row.total_revenue or 0)
        total_buy_cost = float(agg_row.total_buy_cost or 0)

        # Per-lead ROI + days-to-close (columns only, no ORM hydration).
        # BE-H10: stream rows with yield_per(500) so we don't buffer
        # the entire won-set in memory for users with thousands of
        # deals. The aggregate stats above already gave us the totals;
        # this loop only collects ROI percentile inputs and
        # days-to-close samples — both fine to compute incrementally.
        won_stream = await session.stream(
            select(
                LeadItem.id,
                LeadItem.buy_price_byn,
                LeadItem.sold_price_byn,
                LeadItem.sold_at,
                LeadItem.created_at,
            )
            .where(*window_where, window_won)
            .execution_options(yield_per=500)
        )
        won_expenses_total = 0.0
        roi_values: list[float] = []
        days_to_close: list[float] = []
        async for lid, buy, sold, sold_at_raw, created_at_raw in won_stream:
            buy_price = float(buy) if buy is not None else 0.0
            sold_price = float(sold or 0)
            expenses = expenses_by_lead.get(lid, 0.0)
            cost = buy_price + expenses
            won_expenses_total += expenses
            if cost > 0:
                roi_values.append(((sold_price - cost) / cost) * 100.0)
            sold_at = _ensure_aware(sold_at_raw)
            created_at = _ensure_aware(created_at_raw)
            if sold_at and created_at:
                delta_days = (sold_at - created_at).total_seconds() / 86400.0
                if delta_days >= 0:
                    days_to_close.append(delta_days)

        total_cost = total_buy_cost + won_expenses_total
        total_profit = total_revenue - total_cost
        total_expenses = sum(expenses_by_lead.values())
        win_rate = (won_count / pursued_count) * 100.0 if pursued_count else 0.0

        # Monthly revenue/profit via SQL GROUP BY with expense subquery.
        expense_subq = (
            select(
                DealExpense.lead_id,
                func.sum(DealExpense.amount_byn).label("total_expense"),
            )
            .where(DealExpense.user_id == user_id)
            .group_by(DealExpense.lead_id)
            .subquery()
        )
        month_year = func.extract(
            "year", func.coalesce(LeadItem.sold_at, LeadItem.created_at)
        )
        month_num = func.extract(
            "month", func.coalesce(LeadItem.sold_at, LeadItem.created_at)
        )
        monthly_rows = await session.execute(
            select(
                month_year.label("yr"),
                month_num.label("mo"),
                func.count(LeadItem.id).label("sold_count"),
                func.coalesce(func.sum(LeadItem.sold_price_byn), 0).label("revenue_byn"),
                (
                    func.coalesce(func.sum(LeadItem.sold_price_byn), 0)
                    - func.coalesce(func.sum(LeadItem.buy_price_byn), 0)
                    - func.coalesce(func.sum(expense_subq.c.total_expense), 0)
                ).label("profit_byn"),
            )
            .outerjoin(expense_subq, LeadItem.id == expense_subq.c.lead_id)
            .where(*window_where, window_won)
            .group_by(month_year, month_num)
        )
        monthly = [
            LeadMonthlyStat(
                month=f"{int(row.yr):04d}-{int(row.mo):02d}",
                sold_count=int(row.sold_count),
                revenue_byn=round(float(row.revenue_byn or 0), 2),
                profit_byn=round(float(row.profit_byn or 0), 2),
            )
            for row in monthly_rows
            if row.yr is not None and row.mo is not None
        ]

    _active_keys = ("new", "in_progress", "researching", "bought")
    active_leads = sum(funnel_counter.get(k, 0) for k in _active_keys)

    total_leads = sum(funnel_counter.values())

    return LeadAnalyticsResponse(
        period_days=days,
        total_leads=total_leads,
        pursued_leads=pursued_count,
        sold_leads=won_count,
        skipped_leads=skipped_count,
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
