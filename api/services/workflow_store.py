from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.models import LeadItem, User, WatchlistItem


async def ensure_user(
    session: AsyncSession,
    *,
    telegram_user_id: int,
    first_name: str = "",
    username: str | None = None,
) -> int:
    """Return the ``users.id`` for a Telegram user, creating the row if missing.

    Returns the internal auto-increment ``users.id``, **not** the Telegram user ID.
    """
    existing = await session.scalar(
        select(User).where(User.telegram_user_id == telegram_user_id)
    )
    if existing is not None:
        return existing.id
    user = User(
        telegram_user_id=telegram_user_id,
        first_name=first_name,
        username=username,
    )
    session.add(user)
    await session.flush()
    return user.id


async def resolve_user_id(session: AsyncSession, telegram_user_id: int) -> int | None:
    """Return ``users.id`` for a Telegram user, or ``None`` if not found."""
    row = await session.scalar(
        select(User.id).where(User.telegram_user_id == telegram_user_id)
    )
    return row


async def upsert_lead(
    session: AsyncSession,
    *,
    user_id: int,
    ad_id: int,
    query: str,
    title: str,
    link: str,
    price_byn: float | None,
    thumbnail: str | None = None,
    target_resale_byn: float | None = None,
    status: str = "new",
    source: str = "manual",
) -> LeadItem:
    existing = await session.scalar(
        select(LeadItem).where(LeadItem.user_id == user_id, LeadItem.ad_id == ad_id)
    )
    if existing is None:
        existing = LeadItem(
            user_id=user_id,
            ad_id=ad_id,
            query=query,
            title=title,
            link=link,
            price_byn=price_byn,
            thumbnail=thumbnail,
            target_resale_byn=target_resale_byn,
            status=status,
            source=source,
        )
        session.add(existing)
        return existing

    existing.query = query
    existing.title = title
    existing.link = link
    existing.price_byn = price_byn
    existing.thumbnail = thumbnail or existing.thumbnail
    if target_resale_byn is not None:
        existing.target_resale_byn = target_resale_byn
    existing.status = status or existing.status
    existing.source = source or existing.source
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
    thumbnail: str | None = None,
    market_median_byn: float | None = None,
    notes: str | None = None,
) -> WatchlistItem:
    existing = await session.scalar(
        select(WatchlistItem).where(WatchlistItem.user_id == user_id, WatchlistItem.ad_id == ad_id)
    )
    if existing is None:
        existing = WatchlistItem(
            user_id=user_id,
            ad_id=ad_id,
            query=query,
            title=title,
            link=link,
            thumbnail=thumbnail,
            initial_price_byn=price_byn,
            current_price_byn=price_byn,
            market_median_byn=market_median_byn,
            notes=notes,
            last_seen_at=datetime.now(UTC),
        )
        session.add(existing)
        return existing

    existing.query = query
    existing.title = title
    existing.link = link
    existing.thumbnail = thumbnail or existing.thumbnail
    existing.current_price_byn = price_byn
    existing.last_seen_at = datetime.now(UTC)
    if market_median_byn is not None:
        existing.market_median_byn = market_median_byn
    if notes is not None:
        existing.notes = notes
    if existing.initial_price_byn is None:
        existing.initial_price_byn = price_byn
    return existing
