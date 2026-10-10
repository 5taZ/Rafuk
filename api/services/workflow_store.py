from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from api.models import LeadItem, WatchlistItem
from api.services.users import (
    MAX_WATCHLIST_ITEMS_PER_USER,
    UserQuotaExceededError,
    count_user_rows,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

DEFAULT_LEAD_STATUS = "new"
DEFAULT_LEAD_SOURCE = "manual"
# A repeated "add" (Mini App button, alert button) may move a lead out of these statuses only;
# it must never downgrade a lead that is already in work, bought or sold.
LEAD_STATUSES_OPEN_TO_UPSERT = frozenset({"new", "deferred"})


async def _get_lead(session: AsyncSession, user_id: int, ad_id: int) -> LeadItem | None:
    return await session.scalar(
        select(LeadItem).where(LeadItem.user_id == user_id, LeadItem.ad_id == ad_id)
    )


async def _get_watchlist_item(
    session: AsyncSession,
    user_id: int,
    ad_id: int,
) -> WatchlistItem | None:
    return await session.scalar(
        select(WatchlistItem).where(WatchlistItem.user_id == user_id, WatchlistItem.ad_id == ad_id)
    )


async def upsert_lead(
    session: AsyncSession,
    *,
    user_id: int,
    ad_id: int,
    query: str,
    title: str,
    link: str,
    price_byn: float | None,
    target_resale_byn: float | None = None,
    status: str | None = None,
    source: str | None = None,
    notes: str | None = None,
) -> LeadItem:
    """Create a lead or refresh an existing one (no commit).

    A new lead gets ``status`` (default "new") and ``source`` (default "manual"). For an
    existing lead the listing data is refreshed, but status and source change only when an
    explicit non-default status is given and the lead is still "new" or "deferred".
    """
    existing = await _get_lead(session, user_id, ad_id)
    if existing is None:
        lead = LeadItem(
            user_id=user_id,
            ad_id=ad_id,
            query=query,
            title=title,
            link=link,
            price_byn=price_byn,
            target_resale_byn=target_resale_byn,
            status=status or DEFAULT_LEAD_STATUS,
            source=source or DEFAULT_LEAD_SOURCE,
            notes=notes,
        )
        try:
            async with session.begin_nested():
                session.add(lead)
            return lead
        except IntegrityError:
            # A concurrent request inserted the same (user_id, ad_id): update that row.
            existing = await _get_lead(session, user_id, ad_id)
            if existing is None:
                raise

    existing.query = query
    existing.title = title
    existing.link = link
    existing.price_byn = price_byn
    if target_resale_byn is not None:
        existing.target_resale_byn = target_resale_byn
    if notes is not None:
        existing.notes = notes
    explicit_status = status if status and status != DEFAULT_LEAD_STATUS else None
    if (
        explicit_status is not None
        and existing.status in LEAD_STATUSES_OPEN_TO_UPSERT
        and existing.status != explicit_status
    ):
        existing.status = explicit_status
        if source:
            existing.source = source
    return existing


async def upsert_watchlist(
    session: AsyncSession,
    *,
    user_id: int,
    ad_id: int,
    query: str,
    title: str,
    link: str,
    price_byn: float | None,
    notes: str | None = None,
    max_items: int | None = None,
) -> WatchlistItem:
    """Create or refresh a watchlist item (no commit).

    Raises ``UserQuotaExceededError`` when a new item would exceed ``max_items``
    (default ``MAX_WATCHLIST_ITEMS_PER_USER``).
    """
    existing = await _get_watchlist_item(session, user_id, ad_id)
    if existing is None:
        limit = MAX_WATCHLIST_ITEMS_PER_USER if max_items is None else max_items
        if await count_user_rows(session, WatchlistItem, user_id) >= limit:
            raise UserQuotaExceededError(
                f"В избранном уже {limit} лотов. Удалите ненужные, чтобы добавить новый.",
                limit=limit,
            )
        item = WatchlistItem(
            user_id=user_id,
            ad_id=ad_id,
            query=query,
            title=title,
            link=link,
            initial_price_byn=price_byn,
            current_price_byn=price_byn,
            notes=notes,
            last_seen_at=datetime.now(UTC),
        )
        try:
            async with session.begin_nested():
                session.add(item)
            return item
        except IntegrityError:
            # A concurrent request inserted the same (user_id, ad_id): update that row.
            existing = await _get_watchlist_item(session, user_id, ad_id)
            if existing is None:
                raise

    existing.query = query
    existing.title = title
    existing.link = link
    existing.current_price_byn = price_byn
    existing.last_seen_at = datetime.now(UTC)
    if notes is not None:
        existing.notes = notes
    if existing.initial_price_byn is None:
        existing.initial_price_byn = price_byn
    return existing
