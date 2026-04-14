from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.models import QueryListingState, QuerySnapshot
from api.services.aggregator import compute_price_stats, extract_prices, normalize_price_byn


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

    existing.total_results = total_results
    existing.analyzed_count = stats.count
    existing.mean_byn = stats.mean
    existing.median_byn = stats.median
    existing.min_byn = stats.min
    existing.max_byn = stats.max
    return existing


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
        price_byn = normalize_price_byn(ad.get("price_byn"))
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
            and price_byn < existing.last_price_byn
            and (existing.last_price_byn - price_byn) >= 0.5
        ):
            price_drops.append((existing, existing.last_price_byn - price_byn))

        existing.title = title
        existing.link = link
        existing.list_time = list_time
        existing.last_price_byn = price_byn
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
