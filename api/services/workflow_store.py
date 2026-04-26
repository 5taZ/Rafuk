from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import desc, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api.models import LeadItem, LeadItemPriceSnapshot, User


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


async def record_price_snapshot(
    session: AsyncSession,
    *,
    lead_item: LeadItem,
    price_byn: float | None,
    snapped_at: datetime | None = None,
    epsilon: float = 0.5,
) -> LeadItemPriceSnapshot | None:
    """Append a price snapshot to ``lead_item_price_snapshots`` if the
    price has actually moved since the last recorded point.

    No snapshot is written when:
      * price is None or non-positive (the listing is missing / had no
        price at refresh time — those gaps shouldn't pollute the chart)
      * |price - last_snapshot_price| < epsilon (default 0.5 BYN — kills
        rounding noise from currency normalisation)
      * the row has no id yet (the caller must flush() first)

    Returns the new snapshot, or None when nothing was written.
    """
    if lead_item is None or lead_item.id is None:
        return None
    if price_byn is None:
        return None
    try:
        price_value = float(price_byn)
    except (TypeError, ValueError):
        return None
    if price_value <= 0:
        return None

    last = await session.scalar(
        select(LeadItemPriceSnapshot)
        .where(LeadItemPriceSnapshot.lead_item_id == lead_item.id)
        .order_by(desc(LeadItemPriceSnapshot.snapped_at))
        .limit(1)
    )
    if last is not None and abs(float(last.price_byn) - price_value) < epsilon:
        return None

    snapshot = LeadItemPriceSnapshot(
        lead_item_id=lead_item.id,
        price_byn=price_value,
        snapped_at=snapped_at or datetime.now(UTC),
    )
    session.add(snapshot)
    return snapshot


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
        new_item = LeadItem(
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
        # Race-safe insert: if a concurrent request just created a row
        # for the same (user_id, ad_id), flush() will raise an
        # IntegrityError. We rollback the savepoint and fall through to
        # the update branch below using the row that the other request
        # committed.
        try:
            async with session.begin_nested():
                session.add(new_item)
                await session.flush()
            return new_item
        except IntegrityError:
            existing = await session.scalar(
                select(LeadItem).where(
                    LeadItem.user_id == user_id, LeadItem.ad_id == ad_id
                )
            )
            if existing is None:
                # Should not happen — the IntegrityError is *because* a
                # row exists. Re-raise so the caller sees a real 500.
                raise

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
