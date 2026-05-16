from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, desc, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api.models import LeadItem, LeadItemPriceSnapshot, User

PRICE_SNAPSHOT_RETENTION_DAYS = 90


async def ensure_user(
    session: AsyncSession,
    *,
    telegram_user_id: int,
    first_name: str = "",
    username: str | None = None,
) -> int:
    """Return the ``users.id`` for a Telegram user, creating the row if missing.

    Returns the internal auto-increment ``users.id``, **not** the Telegram user ID.

    The previous implementation did SELECT-then-INSERT, which races with
    concurrent first-time requests for the same telegram_user_id and
    raises IntegrityError on the loser. We now use the dialect's atomic
    ``INSERT ... ON CONFLICT DO NOTHING RETURNING id`` — Postgres and
    SQLite both support the construct via different SQLAlchemy
    ``insert`` factories, so we pick the right one based on the bound
    engine. SEC-HIGH / BE-HIGH (issues §1.1, §2.3): the previous
    unconditional ``pg_insert`` rendered Postgres-only SQL that
    aiosqlite (test stand and any SQLite fallback deploy) rejects with
    ``OperationalError: near "ON": syntax error``. ``api/dependencies``
    already does the same dialect detect; this brings ``workflow_store``
    in line.
    """
    bind = session.get_bind()
    dialect_name = getattr(getattr(bind, "dialect", None), "name", "") or ""
    insert_factory = sqlite_insert if dialect_name == "sqlite" else pg_insert
    stmt = (
        insert_factory(User)
        .values(
            telegram_user_id=telegram_user_id,
            first_name=first_name,
            username=username,
        )
        .on_conflict_do_nothing(index_elements=["telegram_user_id"])
        .returning(User.id)
    )
    inserted_id = await session.scalar(stmt)
    if inserted_id is not None:
        return int(inserted_id)
    # Lost the race (or row already existed) — fetch the existing id.
    existing_id = await session.scalar(
        select(User.id).where(User.telegram_user_id == telegram_user_id)
    )
    if existing_id is None:
        # Should never happen — INSERT either succeeded or hit a conflict.
        # Surface loudly rather than returning a bogus id.
        raise RuntimeError(
            f"ensure_user: telegram_user_id={telegram_user_id} neither "
            "inserted nor present after upsert"
        )
    return int(existing_id)


async def resolve_user_id(session: AsyncSession, telegram_user_id: int) -> int | None:
    """Return ``users.id`` for a Telegram user, or ``None`` if not found."""
    row = await session.scalar(select(User.id).where(User.telegram_user_id == telegram_user_id))
    return row


async def load_last_snapshot_prices(
    session: AsyncSession,
    lead_item_ids: list[int],
) -> dict[int, float]:
    """Bulk-load the latest snapshot price per lead_item_id.

    Used by callers that need to record snapshots for many items in
    one tick (watchlist refresh) — prevents a per-item SELECT inside
    ``record_price_snapshot``. Returns ``{}`` when the input list is
    empty so the caller can blindly pass an empty list.

    Implementation: a window-function row-number over snapped_at desc
    isn't available on every SQLAlchemy backend without extra work,
    so we rely on a simple GROUP BY MAX(id) — snapshot ids are
    monotonically increasing so the row with the max id is the most
    recent one written, with one round-trip total.
    """
    if not lead_item_ids:
        return {}
    latest_id_subq = (
        select(func.max(LeadItemPriceSnapshot.id))
        .where(LeadItemPriceSnapshot.lead_item_id.in_(lead_item_ids))
        .group_by(LeadItemPriceSnapshot.lead_item_id)
        .scalar_subquery()
    )
    rows = await session.execute(
        select(LeadItemPriceSnapshot.lead_item_id, LeadItemPriceSnapshot.price_byn)
        .where(LeadItemPriceSnapshot.id.in_(latest_id_subq))
    )
    return {row.lead_item_id: float(row.price_byn) for row in rows}


async def prune_price_snapshots(
    session: AsyncSession,
    lead_item_ids: list[int],
    *,
    days: int = PRICE_SNAPSHOT_RETENTION_DAYS,
    now: datetime | None = None,
) -> int:
    if not lead_item_ids:
        return 0
    cutoff = (now or datetime.now(UTC)) - timedelta(days=days)
    result = await session.execute(
        delete(LeadItemPriceSnapshot).where(
            LeadItemPriceSnapshot.lead_item_id.in_(lead_item_ids),
            LeadItemPriceSnapshot.snapped_at < cutoff,
        )
    )
    return int(result.rowcount or 0)


def make_price_snapshot(
    *,
    lead_item: LeadItem,
    price_byn: float | None,
    snapped_at: datetime | None = None,
    epsilon: float = 0.5,
    last_known_price: float | None = None,
) -> dict[str, Any] | None:
    """Build a price snapshot dict if the price has actually moved.

    Returns ``None`` when the price hasn't changed enough or is invalid.
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

    if (
        last_known_price is not None
        and last_known_price >= 0
        and abs(last_known_price - price_value) < epsilon
    ):
        return None
    # When last_known_price is None the caller hasn't pre-fetched;
    # bulk callers should always pre-fetch to avoid per-row SELECTs.

    return {
        "lead_item_id": lead_item.id,
        "price_byn": price_value,
        "snapped_at": snapped_at or datetime.now(UTC),
    }


async def record_price_snapshot(
    session: AsyncSession,
    *,
    lead_item: LeadItem,
    price_byn: float | None,
    snapped_at: datetime | None = None,
    epsilon: float = 0.5,
    last_known_price: float | None = None,
) -> LeadItemPriceSnapshot | None:
    """Append a price snapshot to ``lead_item_price_snapshots`` if the
    price has actually moved since the last recorded point.

    No snapshot is written when:
      * price is None or non-positive (the listing is missing / had no
        price at refresh time — those gaps shouldn't pollute the chart)
      * |price - last_snapshot_price| < epsilon (default 0.5 BYN — kills
        rounding noise from currency normalisation)
      * the row has no id yet (the caller must flush() first)

    Pass ``last_known_price`` to skip the per-row SELECT — useful when
    the caller has already bulk-loaded the latest prices via
    ``load_last_snapshot_prices`` (watchlist refresh does this to
    avoid an N+1).

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

    if last_known_price is not None:
        # Caller pre-fetched — sentinel value < 0 means "no prior
        # snapshot exists for this row", so we always record.
        if last_known_price >= 0 and abs(last_known_price - price_value) < epsilon:
            return None
    else:
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
    existing.version = int(existing.version or 1) + 1
    return existing
