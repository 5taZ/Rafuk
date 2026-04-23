from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.config import Settings
from api.dependencies import (
    get_currency_service,
    get_session_factory_dependency,
    get_settings_dependency,
)
from api.limiter import limiter
from api.schemas import CompareRequestItem, CompareResponse
from api.services.aggregator import build_query_key, compute_category_price_stats, filter_deal_ads
from api.services.currency_service import CurrencyService
from api.services.history_service import load_query_snapshots
from api.services.kufar_client import KufarClient
from api.services.listing_mapper import build_listing_item
from api.services.query_pipeline import load_query_dataset
from api.services.reseller_tools import analyze_query_text
from api.validators import MAX_QUERY_LENGTH

router = APIRouter(tags=["analytics"])


def _split_compare_queries(values: list[str]) -> list[str]:
    items: list[str] = []
    for value in values:
        for part in value.replace("\n", ",").split(","):
            query = part.strip()
            if query:
                items.append(query)
    unique: list[str] = []
    seen: set[str] = set()
    for item in items:
        key = item.casefold()
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique[:2]


async def _build_compare_item(
    query: str,
    *,
    currency: str,
    strict_search: bool,
    category: int | None,
    settings: Settings,
    currency_service: CurrencyService,
    session_factory: async_sessionmaker[AsyncSession],
) -> CompareRequestItem:
    dataset = await load_query_dataset(
        query=query,
        currency=currency,
        strict_search=strict_search,
        settings=settings,
        client_factory=KufarClient,
        category=category,
    )
    rates_payload = await currency_service.get_rates()
    category_price_stats = compute_category_price_stats(dataset.ads)
    deal_ads = filter_deal_ads(
        dataset.ads,
        dataset.price_stats.median,
        5.0,
        market_stats=dataset.price_stats,
        category_price_stats=category_price_stats,
    )
    source_ads = deal_ads or dataset.ads[:20]
    listing_items = [
        build_listing_item(
            ad,
            query=query,
            currency=currency,
            rates=rates_payload["rates"],
            currency_service=currency_service,
            median_byn=dataset.price_stats.median,
            market_stats=dataset.price_stats,
            category_price_stats=category_price_stats,
        )
        for ad in source_ads
    ]
    listing_items.sort(key=lambda item: (-float(item.deal_score or 0.0), float(item.price or 0.0)))

    trend_percent: float | None = None
    async with session_factory() as session:
        snapshots = await load_query_snapshots(
            session,
            query=build_query_key(query, strict_search),
            days=30,
        )
    if len(snapshots) >= 2 and snapshots[0].median_byn:
        oldest = float(snapshots[0].median_byn)
        latest = float(snapshots[-1].median_byn)
        trend_percent = round(((latest - oldest) / oldest) * 100.0, 2)

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
@limiter.limit("20/minute")
async def compare_queries(
    request: Request,
    base_query: str = Query(
        ...,
        min_length=1,
        max_length=MAX_QUERY_LENGTH,
        description="Base query",
    ),
    compare_query: list[str] | None = Query(default=None, description="Queries to compare"),
    currency: str = "BYN",
    strict_search: bool = False,
    category: int | None = None,
    settings: Settings = Depends(get_settings_dependency),
    currency_service: CurrencyService = Depends(get_currency_service),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory_dependency),
) -> CompareResponse:
    compare_queries = _split_compare_queries(compare_query or [])
    queries = [base_query.strip(), *compare_queries]
    batches = await asyncio.gather(
        *[
            _build_compare_item(
                query,
                currency=currency,
                strict_search=strict_search,
                category=category,
                settings=settings,
                currency_service=currency_service,
                session_factory=session_factory,
            )
            for query in queries
            if query
        ]
    )
    return CompareResponse(currency=currency, base_query=base_query, items=batches)
