"""Lead analytics — profit and ROI for the deals dashboard.

A single endpoint replaces the per-lead JS aggregation we used to do
client-side, which couldn't see DealExpense rows (those live in a
separate table) and missed the user's full lead list whenever the
frontend only had a paginated slice. Server-side we walk every
LeadItem the user owns plus their expense rows in two queries, then
roll up the finance numbers into one dashboard payload.

The finance UI only surfaces profit and ROI, while the bot's /deals
summary still uses the status counts from this payload.
"""

from __future__ import annotations

import statistics
from collections import Counter

from fastapi import APIRouter, Depends, Request
from sqlalchemy import and_, case, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.dependencies import (
    get_session_factory_dependency,
    get_telegram_user,
)
from api.limiter import limiter
from api.middleware.telegram_auth import TelegramInitData
from api.models import DealExpense, LeadItem
from api.schemas import LeadAnalyticsResponse, LeadFunnelStage
from api.services.workflow_store import resolve_user_id

router = APIRouter(tags=["analytics"])


# Status order mirrors the kanban for the bot /deals summary.
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
# Statuses where the user has actively pursued the lead. ``watching``
# is excluded because it's essentially the wishlist.
_PURSUED_STATUSES = frozenset(
    {"new", "in_progress", "researching", "bought", "sold", "closed", "skipped"}
)


@router.get("/analytics/leads", response_model=LeadAnalyticsResponse)
@limiter.limit("30/minute")
async def get_lead_analytics(
    request: Request,
    telegram_user: TelegramInitData = Depends(get_telegram_user),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory_dependency),
) -> LeadAnalyticsResponse:
    async with session_factory() as session:
        user_id = await resolve_user_id(session, telegram_user_id=telegram_user.user_id)
        if user_id is None:
            return LeadAnalyticsResponse(
                total_leads=0,
                pursued_leads=0,
                sold_leads=0,
                skipped_leads=0,
                active_leads=0,
                total_revenue_byn=0.0,
                total_cost_byn=0.0,
                total_profit_byn=0.0,
                total_expenses_byn=0.0,
                total_projected_profit_byn=0.0,
                average_roi_percent=0.0,
                funnel=[],
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

        # E-FIND-09 follow-up: exclude incomplete cost-basis leads from
        # headline aggregates — consistent with per-lead ROI skip below.
        won = and_(
            LeadItem.sold_price_byn.isnot(None),
            LeadItem.buy_price_byn.isnot(None),
        )
        agg = await session.execute(
            select(
                func.count(case((won, 1))).label("won_count"),
                func.coalesce(
                    func.sum(case((won, LeadItem.sold_price_byn))), 0
                ).label("total_revenue"),
                func.coalesce(
                    func.sum(case((won, LeadItem.buy_price_byn))), 0
                ).label("total_buy_cost"),
            ).where(LeadItem.user_id == user_id)
        )
        agg_row = agg.one()
        won_count = agg_row.won_count or 0
        total_revenue = float(agg_row.total_revenue or 0)
        total_buy_cost = float(agg_row.total_buy_cost or 0)

        # Per-lead ROI (columns only, no ORM hydration).
        # BE-H10: stream rows with yield_per(500) so we don't buffer
        # the entire won-set in memory for users with thousands of
        # deals. The aggregate stats above already gave us the totals;
        # this loop only collects ROI inputs.
        won_stream = await session.stream(
            select(
                LeadItem.id,
                LeadItem.buy_price_byn,
                LeadItem.sold_price_byn,
            )
            .where(LeadItem.user_id == user_id, won)
            .execution_options(yield_per=500)
        )
        won_expenses_total = 0.0
        roi_values: list[float] = []
        async for lid, buy, sold in won_stream:
            # E-FIND-09: skip leads with missing buy_price from ROI calc.
            if buy is None:
                continue
            buy_price = float(buy)
            sold_price = float(sold or 0)
            expenses = expenses_by_lead.get(lid, 0.0)
            cost = buy_price + expenses
            won_expenses_total += expenses
            if cost > 0:
                roi_values.append(((sold_price - cost) / cost) * 100.0)

        total_cost = total_buy_cost + won_expenses_total
        total_profit = total_revenue - total_cost
        total_expenses = sum(expenses_by_lead.values())

        # E-FIND-01 follow-up: projected profit for unsold leads with target_resale.
        projected_filter = and_(
            LeadItem.sold_price_byn.is_(None),
            LeadItem.target_resale_byn.isnot(None),
        )
        proj_stream = await session.stream(
            select(LeadItem.id, LeadItem.buy_price_byn, LeadItem.target_resale_byn)
            .where(LeadItem.user_id == user_id, projected_filter)
            .execution_options(yield_per=500)
        )
        total_projected_profit = 0.0
        async for lid, buy, target in proj_stream:
            expenses = expenses_by_lead.get(lid, 0.0)
            total_projected_profit += float(target) - float(buy or 0) - expenses

    _active_keys = ("new", "in_progress", "researching", "bought")
    active_leads = sum(funnel_counter.get(k, 0) for k in _active_keys)
    total_leads = sum(funnel_counter.values())
    pursued_count = sum(funnel_counter.get(status, 0) for status in _PURSUED_STATUSES)
    skipped_count = funnel_counter.get("skipped", 0)

    return LeadAnalyticsResponse(
        total_leads=total_leads,
        pursued_leads=pursued_count,
        sold_leads=won_count,
        skipped_leads=skipped_count,
        active_leads=active_leads,
        total_revenue_byn=round(total_revenue, 2),
        total_cost_byn=round(total_cost, 2),
        total_profit_byn=round(total_profit, 2),
        total_expenses_byn=round(total_expenses, 2),
        total_projected_profit_byn=round(total_projected_profit, 2),
        average_roi_percent=round(statistics.fmean(roi_values), 1) if roi_values else 0.0,
        funnel=funnel,
    )
