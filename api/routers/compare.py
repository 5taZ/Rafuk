from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.config import Settings
from api.dependencies import (
    get_currency_service,
    get_session_factory_dependency,
    get_settings_dependency,
)
from api.models import QuerySnapshot
from api.rate_limit import limiter
from api.schemas import CompareRequestItem, CompareResponse
from api.services.aggregator import (
    DATASET_CURRENCY,
    build_query_key,
    coerce_ad_id,
    filter_deal_ads,
)
from api.services.currency_service import CurrencyService
from api.services.history_service import load_query_snapshots
from api.services.kufar_client import KufarClient
from api.services.listing_mapper import safe_build_listing_item
from api.services.market_signals import duplicate_counts
from api.services.query_pipeline import load_query_dataset
from api.services.reseller_tools import analyze_query_text
from api.validators import (
    DEFAULT_CURRENCY,
    MAX_QUERY_LENGTH,
    BaseSearchQuery,
    CurrencyCode,
    validate_query,
)

router = APIRouter(tags=["analytics"])

MAX_COMPARE_QUERIES = 2
TREND_WINDOW_DAYS = 30
# Two points closer than this measure noise, not a market trend.
MIN_TREND_SPAN = timedelta(hours=24)
# A snapshot built from far fewer prices than the fullest one in the window is a partial
# sample (older scheduler snapshots held only the newest few pages, a deadline can cut a
# search short): its median describes fresh lots, not the market, so it is not compared.
MIN_COMPARABLE_SAMPLE_RATIO = 0.5


def _split_compare_queries(
    values: list[str],
    *,
    base_query: str = "",
    strict_search: bool = False,
) -> list[str]:
    """Split, validate and deduplicate compare queries (also against the base query)."""
    items: list[str] = []
    for value in values:
        for part in value.replace("\n", ",").split(","):
            if not part.strip():
                continue
            try:
                items.append(validate_query(part))
            except ValueError as exc:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail=f"Запрос для сравнения: {exc}",
                ) from exc
    unique: list[str] = []
    seen: set[str] = set()
    if base_query:
        seen.add(build_query_key(base_query, strict_search))
    for item in items:
        key = build_query_key(item, strict_search)
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique[:MAX_COMPARE_QUERIES]


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def compute_trend_percent(snapshots: Sequence[QuerySnapshot]) -> float | None:
    """Median change between the oldest and newest comparable snapshots.

    Empty snapshots (analyzed_count == 0 or median 0) are ignored, and so are partial
    samples: only snapshots with at least MIN_COMPARABLE_SAMPLE_RATIO of the largest
    analyzed_count in the window are compared. The two points must be at least
    MIN_TREND_SPAN apart; otherwise there is no trend to report.
    """
    non_empty = [
        snapshot
        for snapshot in snapshots
        if (snapshot.analyzed_count or 0) > 0 and (snapshot.median_byn or 0) > 0
    ]
    if not non_empty:
        return None
    fullest = max(int(snapshot.analyzed_count) for snapshot in non_empty)
    usable = [
        snapshot
        for snapshot in non_empty
        if int(snapshot.analyzed_count) >= fullest * MIN_COMPARABLE_SAMPLE_RATIO
    ]
    if len(usable) < 2:
        return None
    usable.sort(key=lambda snapshot: _as_utc(snapshot.snapshot_at))
    oldest, latest = usable[0], usable[-1]
    if _as_utc(latest.snapshot_at) - _as_utc(oldest.snapshot_at) < MIN_TREND_SPAN:
        return None
    oldest_median = float(oldest.median_byn)
    latest_median = float(latest.median_byn)
    return round(((latest_median - oldest_median) / oldest_median) * 100.0, 2)


async def _build_compare_item(
    query: str,
    *,
    currency: str,
    strict_search: bool,
    settings: Settings,
    currency_service: CurrencyService,
    session_factory: async_sessionmaker[AsyncSession],
) -> CompareRequestItem:
    dataset = await load_query_dataset(
        query=query,
        currency=DATASET_CURRENCY,
        strict_search=strict_search,
        settings=settings,
        client_factory=KufarClient,
    )
    duplicate_index = duplicate_counts(dataset.ads)
    rates_payload = await currency_service.get_rates()
    deal_ads = filter_deal_ads(
        dataset.ads,
        dataset.price_stats.median,
        5.0,
        low_cutoff=dataset.price_stats.low_cutoff,
    )
    source_ads = deal_ads or dataset.ads[:20]
    listing_items = [
        item
        for ad in source_ads
        if (
            item := safe_build_listing_item(
                ad,
                query=query,
                currency=currency,
                rates=rates_payload["rates"],
                currency_service=currency_service,
                median_byn=dataset.price_stats.median,
                market_stats=dataset.price_stats,
                duplicate_count=duplicate_index.get(coerce_ad_id(ad.get("ad_id")) or 0, 0),
            )
        )
        is not None
    ]
    listing_items.sort(
        key=lambda item: (
            -float(item.deal_score or 0.0),
            item.price_byn is None,
            float(item.price or 0.0),
        )
    )

    async with session_factory() as session:
        snapshots = await load_query_snapshots(
            session,
            query=build_query_key(query, strict_search),
            days=TREND_WINDOW_DAYS,
        )
    trend_percent = compute_trend_percent(snapshots)

    insights = analyze_query_text(query)
    return CompareRequestItem(
        query=query,
        normalized_query=insights.normalized_query,
        config_summary=insights.config_summary,
        median=currency_service.convert_from_byn(
            dataset.price_stats.median,
            currency,
            rates_payload["rates"],
        ),
        cheap_count=len(deal_ads),
        total_results=dataset.total_results,
        trend_percent=trend_percent,
        best_listing=listing_items[0] if listing_items else None,
    )


@router.get("/compare", response_model=CompareResponse)
@limiter.limit("10/minute")
async def compare_queries(
    request: Request,
    base_query: BaseSearchQuery,
    compare_query: Annotated[
        list[str] | None,
        Query(
            description=(
                f"Queries to compare (comma-separated allowed), each up to {MAX_QUERY_LENGTH}"
                " characters"
            ),
        ),
    ] = None,
    currency: CurrencyCode = DEFAULT_CURRENCY,
    strict_search: bool = False,
    settings: Settings = Depends(get_settings_dependency),
    currency_service: CurrencyService = Depends(get_currency_service),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory_dependency),
) -> CompareResponse:
    compare_queries = _split_compare_queries(
        compare_query or [],
        base_query=base_query,
        strict_search=strict_search,
    )
    queries = [base_query, *compare_queries]
    batches = await asyncio.gather(
        *[
            _build_compare_item(
                query,
                currency=currency,
                strict_search=strict_search,
                settings=settings,
                currency_service=currency_service,
                session_factory=session_factory,
            )
            for query in queries
        ]
    )
    return CompareResponse(currency=currency, base_query=base_query, items=batches)
