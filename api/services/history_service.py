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
    # RV-SCH-01: every listing on the synced page. ``new_listings`` /
    # ``price_drops`` above are relative to the previous sync of the KEY,
    # which a tracker on its own schedule may never have seen —
    # ``changes_since`` re-derives them for one tracker's cursor.
    listings: list[QueryListingState] = field(default_factory=list)


# RV-SCH-01: price changes kept per listing state. A tracker checked at
# most every 12 h needs only the last one or two; the slack covers
# daily USD→BYN re-conversion drift on dollar-priced ads.
_PRICE_HISTORY_LIMIT = 8
_CENT = Decimal("0.01")


def as_utc(value: datetime) -> datetime:
    """SQLite hands timestamps back naive (UTC by convention)."""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def _epoch(value: datetime) -> int:
    return int(as_utc(value).timestamp())


def _to_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    return value if isinstance(value, Decimal) else Decimal(str(value))


def significant_price_drop(before: Any, after: Any) -> Decimal | None:
    """Return ``before - after`` when it is a real drop, else None.

    E-FIND-05: an absolute 0.50 BYN threshold meant a 5000 BYN listing
    fired a price_drop event on a 0.50 BYN flicker — 0.01% noise. Floor
    the threshold at the larger of 0.50 BYN (kopecks-rounding noise) and
    0.5% of the previous price (relative noise on expensive items).

    BE-M11: ``last_price_byn`` comes back as ``Decimal`` from the
    Numeric(12,2) column under PostgreSQL while live prices are floats
    from ``normalize_price_byn``; mixing them raises ``TypeError``. Both
    sides go through Decimal so the comparison is exact on either backend.
    """
    before_dec, after_dec = _to_decimal(before), _to_decimal(after)
    if before_dec is None or after_dec is None or after_dec >= before_dec:
        return None
    relative_floor = (before_dec * Decimal("0.005")).quantize(_CENT)
    delta = before_dec - after_dec
    return delta if delta >= max(Decimal("0.5"), relative_floor) else None


def _price_changed(old: Any, new: Any) -> bool:
    old_dec, new_dec = _to_decimal(old), _to_decimal(new)
    if old_dec is None or new_dec is None:
        return (old_dec is None) != (new_dec is None)
    return old_dec.quantize(_CENT) != new_dec.quantize(_CENT)


def _json_price(value: Any) -> float | None:
    return None if value is None else float(value)


def price_as_of(state: QueryListingState, when: datetime) -> Decimal | None:
    """RV-SCH-01: the price ``state`` had at ``when``.

    Without history the price has not changed since first_seen_at. A
    ``when`` older than the retained history falls back to the oldest
    retained price.
    """
    history = state.price_history
    if not history:
        return _to_decimal(state.last_price_byn)
    cutoff = _epoch(when)
    price = history[0][1]
    for changed_at, value in history:
        if changed_at > cutoff:
            break
        price = value
    return _to_decimal(price)


def changes_since(sync_result: QuerySyncResult, since: datetime) -> QuerySyncResult:
    """RV-SCH-01: new listings and price drops for a reader whose previous
    look at the key was ``since`` (a tracker's ``last_checked_at``).

    The key-level diff in ``sync_query_listing_states`` belongs to whichever
    tracker synced the key last; this one is per tracker, so a listing or a
    drop that appeared between two trackers' checks reaches both.
    """
    since = as_utc(since)
    new_listings: list[QueryListingState] = []
    price_drops: list[tuple[QueryListingState, Decimal]] = []
    for state in sync_result.listings:
        if as_utc(state.first_seen_at) > since:
            new_listings.append(state)
            continue
        delta = significant_price_drop(price_as_of(state, since), state.last_price_byn)
        if delta is not None:
            price_drops.append((state, delta))
    return QuerySyncResult(
        stats_count=sync_result.stats_count,
        total_results=sync_result.total_results,
        new_listings=new_listings,
        price_drops=price_drops,
        listings=sync_result.listings,
    )


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
    prices = extract_prices(ads)
    stats = compute_price_stats(prices)
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
    existing.q1_byn = stats.q1
    existing.q3_byn = stats.q3
    existing.min_byn = stats.min
    existing.max_byn = stats.max
    # B-10: raw pre-outlier sample size.
    existing.fetched_count = len(prices)
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
    # M13: truncate to the analysis window AFTER confirming the full dataset
    # has enough days. If window_days is small (7) and some days lack data,
    # the truncated window may have fewer days than min_days — but the
    # function intentionally analyses only recent data. Callers that need a
    # wider window should increase window_days accordingly.
    daily = sorted(by_date.items())[-window_days:]
    if len(daily) < min(min_days, len(by_date)):
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
    # BE-05: A popular query like "iphone" can accumulate tens of
    # thousands of QueryListingState rows over time as listings cycle
    # in and out of Kufar. The scheduler reaches for this map every
    # sync cycle — without a bound we end up materialising hundreds
    # of MB into memory just to diff a single page (~200 ads).
    #
    # 5000 most-recent rows is enough headroom for any realistic
    # query: the active sync window is "currently listed on Kufar"
    # (at most a few thousand ads per page-walk) plus a tail of
    # recently-deactivated ones used for re-activation detection.
    # Older states are stale and will be re-populated naturally on
    # next observation.
    result = await session.execute(
        select(QueryListingState)
        .where(QueryListingState.query == query)
        .order_by(QueryListingState.last_seen_at.desc())
        .limit(5000)
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
    candidates = listing_candidates(ads)
    # RV-SCH-01: a listing older than load_listing_states' recency window is
    # still known — fetch it by id rather than re-inserting it, which hit the
    # unique constraint and came back "new" with a stale price every sync.
    outside_window = {int(ad["ad_id"]) for ad in candidates} - existing_by_id.keys()
    if outside_window:
        known = await session.execute(
            select(QueryListingState).where(
                QueryListingState.query == query,
                QueryListingState.ad_id.in_(outside_window),
            )
        )
        existing_by_id.update({row.ad_id: row for row in known.scalars()})
    seen_ids: set[int] = set()
    new_listings: list[QueryListingState] = []
    price_drops: list[tuple[QueryListingState, float]] = []
    listings: list[QueryListingState] = []

    for ad in candidates:
        ad_id = int(ad.get("ad_id", 0))
        if ad_id in seen_ids:
            # A repeated ad on the page carries nothing new.
            continue
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
            created = QueryListingState(
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
            session.add(created)
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
                # RV-SCH-01: a concurrent writer won the insert — refresh
                # the row like any known listing below.
                existing_by_id[ad_id] = existing
            else:
                existing_by_id[ad_id] = created
                new_listings.append(created)
                listings.append(created)
                continue

        # BE-M11 / E-FIND-05: Decimal-exact drop with a noise floor, see
        # ``significant_price_drop``.
        delta = significant_price_drop(existing.last_price_byn, price_byn)
        if delta is not None:
            price_drops.append((existing, delta))
        if _price_changed(existing.last_price_byn, price_byn):
            # RV-SCH-01: record the change so trackers that have not looked
            # at this key since before it still see it as a drop.
            history = existing.price_history or [
                [_epoch(existing.first_seen_at), _json_price(existing.last_price_byn)],
            ]
            existing.price_history = [
                *history, [_epoch(observed_at), _json_price(price_byn)],
            ][-_PRICE_HISTORY_LIMIT:]

        existing.title = title
        existing.link = link
        existing.list_time = list_time
        existing.last_price_byn = price_byn
        existing.price_type = price_type
        existing.last_seen_at = observed_at
        existing.active = True
        listings.append(existing)

    for ad_id, state in existing_by_id.items():
        if ad_id not in seen_ids:
            state.active = False

    return QuerySyncResult(
        stats_count=stats.count,
        total_results=total_results,
        new_listings=new_listings,
        price_drops=price_drops,
        listings=listings,
    )
