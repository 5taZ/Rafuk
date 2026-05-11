from __future__ import annotations

import statistics
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api.models import QueryListingState, QuerySnapshot
from api.services.aggregator import (
    compute_price_stats,
    extract_prices,
    normalize_price_byn,
)


@dataclass(slots=True, frozen=True)
class TrendReversal:
    """Median-price trend reversal signal: price was falling, now rising again.

    All prices are in BYN. ``decline_pct`` is the size of the original
    drop, ``rebound_pct`` is how far the latest median has bounced back
    above the recent low. ``low_at`` is the date the recent low was
    observed. Use these values to compose the user-facing message.
    """
    low_byn: float
    today_byn: float
    pre_high_byn: float
    decline_pct: float
    rebound_pct: float
    low_at: date


@dataclass(slots=True)
class QuerySyncResult:
    stats_count: int
    total_results: int
    new_listings: list[QueryListingState] = field(default_factory=list)
    price_drops: list[tuple[QueryListingState, float]] = field(default_factory=list)


def snapshot_bucket(now: datetime) -> datetime:
    return now.astimezone(UTC).replace(minute=0, second=0, microsecond=0)


async def load_query_snapshots(
    session: AsyncSession,
    *,
    query: str,
    days: int,
) -> list[QuerySnapshot]:
    since = datetime.now(UTC) - timedelta(days=max(days, 1))
    result = await session.execute(
        select(QuerySnapshot)
        .where(
            QuerySnapshot.query == query,
            QuerySnapshot.snapshot_at >= since,
        )
        .order_by(QuerySnapshot.snapshot_at.asc())
    )
    return list(result.scalars())


async def upsert_query_snapshot(
    session: AsyncSession,
    *,
    query: str,
    ads: list[dict[str, Any]],
    total_results: int,
    bucket_at: datetime,
) -> QuerySnapshot:
    stats = compute_price_stats(extract_prices(ads))
    existing = await session.scalar(
        select(QuerySnapshot).where(
            QuerySnapshot.query == query,
            QuerySnapshot.snapshot_at == bucket_at,
        )
    )
    if existing is None:
        existing = QuerySnapshot(query=query, snapshot_at=bucket_at)
        session.add(existing)
        try:
            async with session.begin_nested():
                # BE-01: passing the list-of-objects to Session.flush()
                # is deprecated since SQLAlchemy 1.4 and slated for
                # removal. begin_nested() above already scopes the
                # write to the savepoint, so an unparameterised flush()
                # is equivalent (the only pending change inside this
                # savepoint is `existing`).
                await session.flush()
        except IntegrityError:
            # begin_nested() already rolled back the savepoint —
            # no session.rollback() needed (that would kill the outer tx).
            existing = await session.scalar(
                select(QuerySnapshot).where(
                    QuerySnapshot.query == query,
                    QuerySnapshot.snapshot_at == bucket_at,
                )
            )
            if existing is None:
                raise

    existing.total_results = total_results
    existing.analyzed_count = stats.count
    existing.mean_byn = stats.mean
    existing.median_byn = stats.median
    existing.min_byn = stats.min
    existing.max_byn = stats.max
    return existing


def detect_trend_reversal_up(
    snapshots: list[QuerySnapshot],
    *,
    min_days: int = 5,
    window_days: int = 7,
    min_decline_pct: float = 3.0,
    min_rebound_pct: float = 3.0,
) -> TrendReversal | None:
    """Detect a "price was falling, now bouncing back up" reversal.

    Buckets snapshots by UTC day, takes the median of each day's
    snapshots, then looks for the recent-low day in the last 4 buckets.
    The latest day must have rebounded ``min_rebound_pct`` above the
    low, and the pre-low high must be at least ``min_decline_pct``
    above the low. Returns ``None`` when there's not enough data or
    the trend isn't a reversal — caller can suppress a false alarm by
    checking for ``None``.

    Why median-of-medians per day: tracker ticks may run every 5-30
    min, so a single day yields multiple snapshots. We collapse them
    so a noisy one-off snapshot doesn't drive the signal.
    """
    if not snapshots:
        return None
    by_date: dict[date, list[float]] = {}
    for snap in snapshots:
        if snap.median_byn is None or snap.median_byn <= 0:
            continue
        day = snap.snapshot_at.astimezone(UTC).date()
        by_date.setdefault(day, []).append(float(snap.median_byn))
    if len(by_date) < min_days:
        return None
    daily = sorted(by_date.items())[-window_days:]
    if len(daily) < min_days:
        return None
    series = [statistics.median(values) for _, values in daily]
    low_idx = min(range(len(series)), key=lambda i: series[i])
    if low_idx >= len(series) - 1:
        return None
    if low_idx < len(series) - 4:
        return None
    today = series[-1]
    low = series[low_idx]
    if today <= low or low <= 0:
        return None
    rebound_pct = (today - low) / low * 100.0
    if rebound_pct < min_rebound_pct:
        return None
    pre_low = series[: low_idx + 1]
    if len(pre_low) < 2:
        return None
    pre_high = max(pre_low[:-1])
    if pre_high <= low:
        return None
    decline_pct = (pre_high - low) / pre_high * 100.0
    if decline_pct < min_decline_pct:
        return None
    return TrendReversal(
        low_byn=round(low, 2),
        today_byn=round(today, 2),
        pre_high_byn=round(pre_high, 2),
        decline_pct=round(decline_pct, 1),
        rebound_pct=round(rebound_pct, 1),
        low_at=daily[low_idx][0],
    )


async def load_listing_states(
    session: AsyncSession,
    *,
    query: str,
) -> dict[int, QueryListingState]:
    result = await session.execute(
        select(QueryListingState).where(QueryListingState.query == query)
    )
    rows = list(result.scalars())
    return {row.ad_id: row for row in rows}


def listing_candidates(ads: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    for ad in ads:
        ad_id = int(ad.get("ad_id", 0))
        if ad_id <= 0:
            continue
        selected.append(ad)
    return selected


async def sync_query_listing_states(
    session: AsyncSession,
    *,
    query: str,
    ads: list[dict[str, Any]],
    observed_at: datetime,
    total_results: int,
) -> QuerySyncResult:
    stats = compute_price_stats(extract_prices(ads))
    existing_by_id = await load_listing_states(session, query=query)
    seen_ids: set[int] = set()
    new_listings: list[QueryListingState] = []
    price_drops: list[tuple[QueryListingState, float]] = []

    for ad in listing_candidates(ads):
        ad_id = int(ad.get("ad_id", 0))
        seen_ids.add(ad_id)
        # Pass the raw ad so detect_price_type can distinguish "free"
        # (price=0, giveaway phrasing) from "negotiable" (price unknown).
        # Without this, both are stored as last_price_byn=NULL and the
        # bot announces free items as "договорная" instead of "бесплатно".
        price_byn = normalize_price_byn(ad.get("price_byn"), ad)
        if price_byn is None:
            price_type = "negotiable"
        elif price_byn == 0.0:
            price_type = "free"
        else:
            price_type = "fixed"
        title = str(ad.get("subject", ""))
        link = str(ad.get("ad_link", ""))
        list_time = ad.get("list_time")

        existing = existing_by_id.get(ad_id)
        if existing is None:
            existing = QueryListingState(
                query=query,
                ad_id=ad_id,
                title=title,
                link=link,
                last_price_byn=price_byn,
                price_type=price_type,
                list_time=list_time,
                active=True,
                first_seen_at=observed_at,
                last_seen_at=observed_at,
            )
            session.add(existing)
            try:
                async with session.begin_nested():
                    # BE-01: see note on QuerySnapshot insert above — drop
                    # the deprecated flush([obj]) shape.
                    await session.flush()
            except IntegrityError:
                # begin_nested() already rolled back the savepoint —
                # no session.rollback() needed (that would kill the outer tx).
                existing = await session.scalar(
                    select(QueryListingState).where(
                        QueryListingState.query == query,
                        QueryListingState.ad_id == ad_id,
                    )
                )
                if existing is None:
                    raise
            existing_by_id[ad_id] = existing
            new_listings.append(existing)
            continue

        # BE-M11: ``existing.last_price_byn`` comes back as ``Decimal`` from
        # the Numeric(12,2) column under PostgreSQL, while ``price_byn`` is
        # a ``float`` from ``normalize_price_byn``. Mixing them raises
        # ``TypeError: unsupported operand type(s) for -: 'Decimal' and
        # 'float'`` at runtime — SQLite happens to dodge it because the
        # NUMERIC affinity returns the value as the type it was inserted
        # with, hiding the bug from the test-suite. Coerce both to Decimal
        # for the comparison and the delta we hand back.
        if existing.last_price_byn is not None and price_byn is not None:
            last_dec = (
                existing.last_price_byn
                if isinstance(existing.last_price_byn, Decimal)
                else Decimal(str(existing.last_price_byn))
            )
            price_dec = Decimal(str(price_byn))
            if price_dec < last_dec and (last_dec - price_dec) >= Decimal("0.5"):
                price_drops.append((existing, last_dec - price_dec))

        existing.title = title
        existing.link = link
        existing.list_time = list_time
        existing.last_price_byn = price_byn
        existing.price_type = price_type
        existing.last_seen_at = observed_at
        existing.active = True

    for ad_id, state in existing_by_id.items():
        if ad_id not in seen_ids:
            state.active = False

    return QuerySyncResult(
        stats_count=stats.count,
        total_results=total_results,
        new_listings=new_listings,
        price_drops=price_drops,
    )
