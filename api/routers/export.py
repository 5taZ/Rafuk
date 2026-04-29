from __future__ import annotations

import csv
import io
from collections import defaultdict
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.dependencies import get_session_factory_dependency, get_telegram_user
from api.limiter import limiter
from api.middleware.telegram_auth import TelegramInitData
from api.models import DealExpense, LeadItem
from api.services.workflow_store import resolve_user_id

router = APIRouter(tags=["export"])

CSV_HEADERS = [
    "id",
    "query",
    "title",
    "link",
    "price_byn",
    "target_resale_byn",
    "status",
    "source",
    "sold_price_byn",
    "sold_at",
    "total_expenses",
    "actual_profit",
    "roi_percent",
    "created_at",
    "updated_at",
]

# CSV injection protection: cells starting with these characters are
# interpreted as formulas by Excel/LibreOffice. Prefix with a single
# quote (the standard mitigation) so the cell is treated as text.
# https://owasp.org/www-community/attacks/CSV_Injection
_CSV_FORMULA_TRIGGERS = ("=", "+", "-", "@", "\t", "\r")


def _csv_safe(value: object) -> object:
    """Sanitize a value for CSV output to prevent CSV injection."""
    if not isinstance(value, str):
        return value
    if value and value[0] in _CSV_FORMULA_TRIGGERS:
        return "'" + value
    return value


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


def _row_for_lead(lead: LeadItem, lead_expenses: dict[int, float]) -> list[Any]:
    """Build the per-lead row payload shared by the CSV and XLSX writers."""
    buy_price = float(lead.buy_price_byn) if lead.buy_price_byn is not None else 0.0
    sold_price = float(lead.sold_price_byn or 0)
    total_expenses = lead_expenses.get(lead.id, 0.0)
    total_cost = buy_price + total_expenses

    actual_profit = (sold_price - total_cost) if sold_price > 0 else None
    roi_percent = (
        (actual_profit / total_cost * 100)
        if actual_profit is not None and total_cost > 0
        else None
    )
    return [
        lead.id,
        lead.query,
        lead.title,
        lead.link,
        float(lead.price_byn) if lead.price_byn is not None else None,
        float(lead.target_resale_byn) if lead.target_resale_byn is not None else None,
        lead.status,
        lead.source,
        float(lead.sold_price_byn) if lead.sold_price_byn is not None else None,
        lead.sold_at.isoformat() if lead.sold_at else None,
        round(total_expenses, 2),
        round(actual_profit, 2) if actual_profit is not None else None,
        round(roi_percent, 2) if roi_percent is not None else None,
        lead.created_at.isoformat(),
        lead.updated_at.isoformat(),
    ]


def _build_xlsx_workbook(leads: list[LeadItem], lead_expenses: dict[int, float]) -> bytes:
    """Render leads to a styled .xlsx workbook in memory.

    openpyxl is a runtime dep — added in pyproject.toml. Header is
    a bold accent row; profit / ROI columns get number formatting so
    the user can pivot in Excel without re-typing.
    """
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    ws = wb.active
    ws.title = "Leads"

    header_font = Font(bold=True, color="FFFFFF", size=11)
    header_fill = PatternFill("solid", fgColor="1F2937")
    header_align = Alignment(vertical="center")
    ws.append(CSV_HEADERS)
    for cell in ws[1]:
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = header_align
    ws.row_dimensions[1].height = 22

    # Numeric columns: price_byn, target_resale_byn, sold_price_byn,
    # total_expenses, actual_profit, roi_percent (1-based, post-header).
    numeric_cols = (5, 6, 9, 11, 12, 13)

    for lead in leads:
        ws.append(_row_for_lead(lead, lead_expenses))

    # Format numbers — money to 2 decimals, ROI to 2 decimals + "%".
    for col in numeric_cols:
        for row_idx in range(2, ws.max_row + 1):
            cell = ws.cell(row=row_idx, column=col)
            if cell.value is None:
                continue
            cell.number_format = "0.00"
    # ROI specifically gets a percent suffix.
    for row_idx in range(2, ws.max_row + 1):
        cell = ws.cell(row=row_idx, column=13)
        if cell.value is not None:
            cell.number_format = '0.00"%"'

    # Reasonable column widths so the export is usable on first open.
    widths = [6, 24, 36, 38, 12, 14, 14, 12, 14, 22, 14, 14, 10, 22, 22]
    for i, width in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = width

    ws.freeze_panes = "A2"

    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


_FORMAT = Literal["csv", "xlsx"]


@router.get("/leads/export")
@limiter.limit("10/minute")
async def export_leads(
    request: Request,
    fmt: _FORMAT = Query("csv", alias="format"),
    telegram_user: TelegramInitData = Depends(get_telegram_user),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory_dependency),
) -> Response:
    """Export leads to a file. Supports CSV (default) and XLSX."""
    if fmt not in ("csv", "xlsx"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="format must be 'csv' or 'xlsx'",
        )

    async with session_factory() as session:
        user_id = await resolve_user_id(session, telegram_user.user_id)
        if user_id is None:
            if fmt == "csv":
                return _empty_csv_response()
            # Empty xlsx fallback — header only, still a valid workbook.
            content = _build_xlsx_workbook([], {})
            return Response(
                content=content,
                media_type=(
                    "application/vnd.openxmlformats-officedocument."
                    "spreadsheetml.sheet"
                ),
                headers={
                    "Content-Disposition": (
                        'attachment; filename="leads_export.xlsx"'
                    )
                },
            )

        result = await session.execute(
            select(LeadItem)
            .where(LeadItem.user_id == user_id)
            .order_by(LeadItem.created_at.desc())
        )
        leads = list(result.scalars())

        lead_ids = [lead.id for lead in leads]
        lead_expenses: dict[int, float] = defaultdict(float)
        if lead_ids:
            expenses_result = await session.execute(
                select(DealExpense.lead_id, DealExpense.amount_byn).where(
                    DealExpense.lead_id.in_(lead_ids),
                    DealExpense.user_id == user_id,
                )
            )
            for lid, amount in expenses_result:
                lead_expenses[lid] += float(amount)

    if fmt == "csv":
        if not leads:
            return _empty_csv_response()
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(CSV_HEADERS)
        for lead in leads:
            row = _row_for_lead(lead, lead_expenses)
            # CSV-injection guard for free-text columns (query, title,
            # link, status, source).
            for idx in (1, 2, 3, 6, 7):
                row[idx] = _csv_safe(row[idx]) if row[idx] is not None else ""
            row = [
                "" if value is None
                else (
                    f"{value:.2f}" if isinstance(value, float) else value
                )
                for value in row
            ]
            writer.writerow(row)
        content = output.getvalue()
        output.close()
        return Response(
            content=content,
            media_type="text/csv",
            headers={
                "Content-Disposition": 'attachment; filename="leads_export.csv"'
            },
        )

    # xlsx
    content_bytes = _build_xlsx_workbook(leads, lead_expenses)
    return Response(
        content=content_bytes,
        media_type=(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ),
        headers={
            "Content-Disposition": 'attachment; filename="leads_export.xlsx"'
        },
    )
