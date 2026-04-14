from __future__ import annotations

import csv
import io
from collections import defaultdict

from fastapi import APIRouter, Depends, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.dependencies import get_session_factory_dependency, get_telegram_user
from api.middleware.telegram_auth import TelegramInitData
from api.models import DealExpense, LeadItem
from api.services.workflow_store import resolve_user_id

router = APIRouter(tags=["export"])

CSV_HEADERS = [
    "id", "query", "title", "link", "price_byn", "target_resale_byn",
    "status", "source", "notes", "sold_price_byn", "sold_at",
    "total_expenses", "actual_profit", "roi_percent", "created_at", "updated_at",
]


def _empty_csv_response() -> Response:
    output = io.StringIO()
    csv.writer(output).writerow(CSV_HEADERS)
    content = output.getvalue()
    output.close()
    return Response(
        content=content,
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="leads_export.csv"'},
    )


@router.get("/leads/export")
async def export_leads_csv(
    telegram_user: TelegramInitData = Depends(get_telegram_user),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory_dependency),
) -> Response:
    """Export leads to CSV file."""
    async with session_factory() as session:
        user_id = await resolve_user_id(session, telegram_user.user_id)
        if user_id is None:
            return _empty_csv_response()

        result = await session.execute(
            select(LeadItem)
            .where(LeadItem.user_id == user_id)
            .order_by(LeadItem.created_at.desc())
        )
        leads = list(result.scalars())
        if not leads:
            return _empty_csv_response()

        # Single query to fetch all expenses for all leads
        lead_ids = [lead.id for lead in leads]
        expenses_result = await session.execute(
            select(DealExpense.lead_id, DealExpense.amount_byn).where(
                DealExpense.lead_id.in_(lead_ids)
            )
        )
        lead_expenses: dict[int, float] = defaultdict(float)
        for lid, amount in expenses_result:
            lead_expenses[lid] += float(amount)

    # Generate CSV
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(CSV_HEADERS)

    for lead in leads:
        buy_price = float(lead.price_byn or 0)
        sold_price = float(lead.sold_price_byn or 0)
        total_expenses = lead_expenses.get(lead.id, 0.0)
        total_cost = buy_price + total_expenses

        actual_profit = (sold_price - total_cost) if sold_price > 0 else None
        roi_percent = (
            (actual_profit / total_cost * 100)
            if actual_profit is not None and total_cost > 0
            else None
        )

        writer.writerow([
            lead.id,
            lead.query,
            lead.title,
            lead.link,
            lead.price_byn or "",
            lead.target_resale_byn or "",
            lead.status,
            lead.source,
            getattr(lead, "notes", "") or "",
            lead.sold_price_byn or "",
            lead.sold_at.isoformat() if lead.sold_at else "",
            f"{total_expenses:.2f}",
            f"{actual_profit:.2f}" if actual_profit is not None else "",
            f"{roi_percent:.2f}" if roi_percent is not None else "",
            lead.created_at.isoformat(),
            lead.updated_at.isoformat(),
        ])

    content = output.getvalue()
    output.close()
    return Response(
        content=content,
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="leads_export.csv"'},
    )
