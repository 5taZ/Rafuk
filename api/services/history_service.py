from __future__ import annotations

from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.ext.asyncio import AsyncSession

from api.models import QueryListingState, QuerySnapshot
from api.services.aggregator import compute_price_stats, extract_prices, normalize_price_byn

# A price drop is reported only when it is at least this large both in absolute
# terms and relative to the previous price. Smaller moves are mostly currency
# conversion noise of USD-priced listings.
PRICE_DROP_MIN_BYN = 1.0
PRICE_DROP_MIN_PERCENT = 1.0

# ad_ids per ``IN (...)`` lookup: stays far below the bind-parameter limits of SQLite
# (999 in old builds) and asyncpg (32767).
AD_ID_LOOKUP_CHUNK = 500

# ad_id of the placeholder state that marks a query key as initialized when its first
# sync found no listings at all; real Kufar ids are positive.
BASELINE_MARKER_AD_ID = 0

_SNAPSHOT_STAT_FIELDS = (
    "total_results",
    "analyzed_count",
    "mean_byn",
    "median_byn",
    "min_byn",
    "max_byn",
)


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
    only_if_missing: bool = False,
) -> bool:
    """Atomically write the hourly snapshot of ``query``; return True if a row was written.

    Empty samples are never stored. An existing snapshot is replaced only by stats
    computed from at least as many prices, so a partial sample cannot overwrite a
    fuller one. With ``only_if_missing`` the snapshot is inserted only into an empty
    bucket. The scheduler never writes snapshots: its sample of recent pages is not
    comparable with the full scans of /price-stats.
    """
    stats = compute_price_stats(extract_prices(ads))
    if stats.count == 0:
        return False

    values: dict[str, Any] = {
        "query": query,
        "snapshot_at": bucket_at,
        "total_results": max(int(total_results), stats.count),
        "analyzed_count": stats.count,
        "mean_byn": stats.mean,
        "median_byn": stats.median,
        "min_byn": stats.min,
        "max_byn": stats.max,
    }

    dialect_name = session.get_bind().dialect.name
    if dialect_name == "postgresql":
        insert_stmt = postgresql.insert(QuerySnapshot).values(**values)
    elif dialect_name == "sqlite":
        insert_stmt = sqlite.insert(QuerySnapshot).values(**values)
    else:
        return await _upsert_query_snapshot_fallback(
            session,
            values=values,
            only_if_missing=only_if_missing,
        )

    conflict_target = [QuerySnapshot.query, QuerySnapshot.snapshot_at]
    if only_if_missing:
        statement = insert_stmt.on_conflict_do_nothing(index_elements=conflict_target)
    else:
        excluded = insert_stmt.excluded
        statement = insert_stmt.on_conflict_do_update(
            index_elements=conflict_target,
            set_={name: excluded[name] for name in _SNAPSHOT_STAT_FIELDS},
            where=excluded.analyzed_count >= QuerySnapshot.analyzed_count,
        )
    # RETURNING yields a row only when the row was inserted or updated.
    result = await session.execute(statement.returning(QuerySnapshot.id))
    return result.first() is not None


async def _upsert_query_snapshot_fallback(
    session: AsyncSession,
    *,
    values: dict[str, Any],
    only_if_missing: bool,
) -> bool:
    """Non-atomic variant for dialects without INSERT ... ON CONFLICT support."""
    existing = await session.scalar(
        select(QuerySnapshot).where(
            QuerySnapshot.query == values["query"],
            QuerySnapshot.snapshot_at == values["snapshot_at"],
        )
    )
    if existing is None:
        session.add(QuerySnapshot(**values))
        return True
    if only_if_missing or existing.analyzed_count > values["analyzed_count"]:
        return False
    for name in _SNAPSHOT_STAT_FIELDS:
        setattr(existing, name, values[name])
    return True


def _unique_ids(ad_ids: Iterable[int]) -> list[int]:
    return sorted({int(ad_id) for ad_id in ad_ids})


def _chunks(values: Sequence[int], size: int = AD_ID_LOOKUP_CHUNK) -> Iterator[Sequence[int]]:
    for start in range(0, len(values), size):
        yield values[start : start + size]


async def load_listing_states(
    session: AsyncSession,
    *,
    query: str,
    ad_ids: Iterable[int] | None = None,
) -> dict[int, QueryListingState]:
    """Load remembered listings of ``query``, active or not.

    With ``ad_ids`` only the states of those listings are loaded, so the cost of a sync
    depends on the fetched sample and not on how many listings the key accumulated.
    """
    if ad_ids is None:
        result = await session.execute(
            select(QueryListingState).where(QueryListingState.query == query)
        )
        return {row.ad_id: row for row in result.scalars()}

    states: dict[int, QueryListingState] = {}
    for chunk in _chunks(_unique_ids(ad_ids)):
        result = await session.execute(
            select(QueryListingState).where(
                QueryListingState.query == query,
                QueryListingState.ad_id.in_(chunk),
            )
        )
        states.update({row.ad_id: row for row in result.scalars()})
    return states


async def load_known_ad_ids(
    session: AsyncSession,
    *,
    query: str,
    ad_ids: Iterable[int] | None = None,
) -> set[int]:
    """Return the ad_ids seen for ``query`` (active or not).

    With ``ad_ids`` the lookup is limited to those listings: the scheduler asks only
    about the page it has just fetched instead of reading every id of the key.
    """
    if ad_ids is None:
        result = await session.execute(
            select(QueryListingState.ad_id).where(QueryListingState.query == query)
        )
        return {int(ad_id) for ad_id in result.scalars()}

    known: set[int] = set()
    for chunk in _chunks(_unique_ids(ad_ids)):
        result = await session.execute(
            select(QueryListingState.ad_id).where(
                QueryListingState.query == query,
                QueryListingState.ad_id.in_(chunk),
            )
        )
        known.update(int(ad_id) for ad_id in result.scalars())
    return known


async def has_listing_states(session: AsyncSession, *, query: str) -> bool:
    """Whether any listing was ever remembered for ``query``."""
    result = await session.execute(
        select(QueryListingState.id).where(QueryListingState.query == query).limit(1)
    )
    return result.first() is not None


async def mark_query_baseline(
    session: AsyncSession,
    *,
    query: str,
    observed_at: datetime,
) -> None:
    """Make ``has_listing_states`` true for a key whose baseline found no listings.

    The marker is an inactive state with ``BASELINE_MARKER_AD_ID``; it never matches a
    fetched listing and is removed by the regular cleanup like any other state.
    """
    exists = await session.scalar(
        select(QueryListingState.id).where(
            QueryListingState.query == query,
            QueryListingState.ad_id == BASELINE_MARKER_AD_ID,
        )
    )
    if exists is not None:
        return
    session.add(
        QueryListingState(
            query=query,
            ad_id=BASELINE_MARKER_AD_ID,
            title="",
            link="",
            active=False,
            first_seen_at=observed_at,
            last_seen_at=observed_at,
        )
    )


def listing_ad_id(ad: dict[str, Any]) -> int:
    try:
        return int(ad.get("ad_id", 0) or 0)
    except (TypeError, ValueError):
        return 0


def listing_candidates(ads: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    for ad in ads:
        if listing_ad_id(ad) <= 0:
            continue
        selected.append(ad)
    return selected


def is_significant_price_drop(
    previous_price: float,
    current_price: float,
    *,
    min_drop_byn: float = PRICE_DROP_MIN_BYN,
    min_drop_percent: float = PRICE_DROP_MIN_PERCENT,
) -> bool:
    delta = previous_price - current_price
    if delta <= 0 or delta < min_drop_byn:
        return False
    return previous_price <= 0 or delta / previous_price * 100 >= min_drop_percent


async def sync_query_listing_states(
    session: AsyncSession,
    *,
    query: str,
    ads: list[dict[str, Any]],
    observed_at: datetime,
    total_results: int,
    min_drop_byn: float = PRICE_DROP_MIN_BYN,
    min_drop_percent: float = PRICE_DROP_MIN_PERCENT,
) -> QuerySyncResult:
    """Compare a fetched sample with the remembered listings of ``query``.

    A listing is new only if its ad_id has never been seen for this query: states
    that dropped out of the fetched window stay in the table (inactive) and do not
    produce a second "new listing" when they come back. Price drops below the
    ``min_drop_byn`` / ``min_drop_percent`` threshold are ignored.

    Only the states of the fetched listings are loaded; the other active states of the
    key are marked inactive with one bulk UPDATE.
    """
    stats = compute_price_stats(extract_prices(ads))
    candidates = listing_candidates(ads)
    existing_by_id = await load_listing_states(
        session,
        query=query,
        ad_ids=[listing_ad_id(ad) for ad in candidates],
    )
    seen_ids: set[int] = set()
    new_listings: list[QueryListingState] = []
    price_drops: list[tuple[QueryListingState, float]] = []

    for ad in candidates:
        ad_id = listing_ad_id(ad)
        if ad_id in seen_ids:
            continue
        seen_ids.add(ad_id)
        price_byn = normalize_price_byn(ad.get("price_byn"))
        # Truncate to the column sizes: Postgres rejects longer values.
        title = str(ad.get("subject") or "")[:255]
        link = str(ad.get("ad_link", ""))[:512]
        raw_list_time = ad.get("list_time")
        list_time = str(raw_list_time)[:64] if raw_list_time is not None else None

        existing = existing_by_id.get(ad_id)
        if existing is None:
            existing = QueryListingState(
                query=query,
                ad_id=ad_id,
                title=title,
                link=link,
                last_price_byn=price_byn,
                list_time=list_time,
                active=True,
                first_seen_at=observed_at,
                last_seen_at=observed_at,
            )
            session.add(existing)
            existing_by_id[ad_id] = existing
            new_listings.append(existing)
            continue

        if (
            existing.last_price_byn is not None
            and price_byn is not None
            and is_significant_price_drop(
                existing.last_price_byn,
                price_byn,
                min_drop_byn=min_drop_byn,
                min_drop_percent=min_drop_percent,
            )
        ):
            price_drops.append((existing, existing.last_price_byn - price_byn))

        existing.title = title
        existing.link = link
        existing.list_time = list_time
        existing.last_price_byn = price_byn
        existing.last_seen_at = observed_at
        existing.active = True

    # Every listing of the sample now has last_seen_at == observed_at; the remaining
    # active states of the key dropped out of the fetched window. The loaded objects all
    # belong to the sample, so none of them needs to be synchronized.
    await session.flush()
    await session.execute(
        update(QueryListingState)
        .where(
            QueryListingState.query == query,
            QueryListingState.active.is_(True),
            QueryListingState.last_seen_at < observed_at,
        )
        .values(active=False)
        .execution_options(synchronize_session=False)
    )

    return QuerySyncResult(
        stats_count=stats.count,
        total_results=total_results,
        new_listings=new_listings,
        price_drops=price_drops,
    )
