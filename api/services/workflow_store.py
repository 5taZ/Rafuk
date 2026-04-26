from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.models import LeadItem, User


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
    existing = await session.scalar(select(User).where(User.telegram_user_id == telegram_user_id))
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
    row = await session.scalar(select(User.id).where(User.telegram_user_id == telegram_user_id))
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
    market_median_byn: float | None = None,
    notes: str | None = None,
    track_initial_price: bool = False,
    update_last_seen: bool = False,
) -> LeadItem:
    """Upsert a LeadItem row.

    Watchlist semantics (``status='watching'``) is reached via the
    ``track_initial_price`` and ``update_last_seen`` flags — they ensure
    the item gets a baseline price snapshot and last-seen timestamp.
    """
    existing = await session.scalar(
        select(LeadItem).where(LeadItem.user_id == user_id, LeadItem.ad_id == ad_id)
    )
    status_value = status.value if hasattr(status, "value") else status
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
            status=status_value,
            source=source,
            market_median_byn=market_median_byn,
            notes=notes,
            initial_price_byn=price_byn if track_initial_price else None,
            last_seen_at=datetime.now(UTC) if update_last_seen else None,
        )
        session.add(existing)
        return existing

    # Idempotent guard: if a user already has an active lead for this ad,
    # don't accidentally demote it to 'watching' from a watchlist add. Just
    # update the freshness fields and keep the existing pipeline status.
    is_watchlist_add = status_value == "watching"
    keep_existing_status = is_watchlist_add and existing.status != "watching"

    existing.query = query
    existing.title = title
    existing.link = link
    existing.price_byn = price_byn
    existing.thumbnail = thumbnail or existing.thumbnail
    if target_resale_byn is not None:
        existing.target_resale_byn = target_resale_byn
    if market_median_byn is not None:
        existing.market_median_byn = market_median_byn
    if notes is not None:
        existing.notes = notes
    if track_initial_price and existing.initial_price_byn is None:
        existing.initial_price_byn = price_byn
    if update_last_seen:
        existing.last_seen_at = datetime.now(UTC)
    if not keep_existing_status:
        existing.status = status_value or existing.status
        existing.source = source or existing.source
    return existing
