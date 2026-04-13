from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from api.config import Settings
from api.dependencies import get_cache, get_currency_service, get_settings_dependency
from api.schemas import ListingsResponse
from api.services.aggregator import (
    filter_deal_ads,
    sort_listings,
)
from api.services.cache import CacheBackend
from api.services.currency_service import CurrencyService
from api.services.deal_workflow import compute_liquidity_insight
from api.services.kufar_client import KufarClient
from api.services.listing_mapper import build_listing_item
from api.services.market_signals import duplicate_counts
from api.services.query_pipeline import load_query_dataset
from api.services.reseller_tools import analyze_query_text
from api.validators import MAX_QUERY_LENGTH

router = APIRouter(tags=["analytics"])


@router.get("/listings", response_model=ListingsResponse)
async def get_listings(
    query: str = Query(..., min_length=1, max_length=MAX_QUERY_LENGTH, description="Search query"),
    sort: str = "newest",
    currency: str = "USD",
    strict_search: bool = False,
    discount_percent: float = 10.0,
    discount_from_percent: float | None = None,
    discount_to_percent: float | None = None,
    category: int | None = None,
    settings: Settings = Depends(get_settings_dependency),
    cache: CacheBackend = Depends(get_cache),
    currency_service: CurrencyService = Depends(get_currency_service),
) -> ListingsResponse:
    effective_from_source = (
        discount_from_percent if discount_from_percent is not None else discount_percent
    )
    effective_from = abs(effective_from_source)
    effective_to = abs(discount_to_percent) if discount_to_percent is not None else None
    if effective_to is not None and effective_to < effective_from:
        effective_from, effective_to = effective_to, effective_from

    cache_key = (
        f"listings:{query}:{sort}:{currency}:{discount_percent}:"
        f"{effective_from}:{effective_to}:{strict_search}:{category}"
    )
    cached = await cache.get_json(cache_key)
    if cached:
        return ListingsResponse(**cached)

    dataset = await load_query_dataset(
        query=query,
        currency=currency,
        strict_search=strict_search,
        settings=settings,
        client_factory=KufarClient,
        category=category,
    )
    median_byn = dataset.price_stats.median
    duplicate_index = duplicate_counts(dataset.ads)
    liquidity = compute_liquidity_insight(dataset.ads, dataset.price_stats)
    rates_payload = await currency_service.get_rates()
    rates = rates_payload["rates"]
    insights = analyze_query_text(query)

    deal_ads = (
        filter_deal_ads(dataset.ads, median_byn, effective_from, effective_to)
        if sort == "cheap"
        else dataset.ads
    )
    effective_sort = "newest" if sort == "deal_score" else sort
    sorted_ads = sort_listings(deal_ads, effective_sort, median_byn)
    listings = []
    for ad in sorted_ads[:200]:
        duplicate_count = duplicate_index.get(int(ad.get("ad_id", 0)), 0)
        listings.append(
            build_listing_item(
                ad,
                query=query,
                currency=currency,
                rates=rates,
                currency_service=currency_service,
                median_byn=median_byn,
                market_stats=dataset.price_stats,
                liquidity=liquidity,
                duplicate_count=duplicate_count,
            )
        )
    if sort in {"cheap", "deal_score"}:
        listings.sort(
            key=lambda item: (
                -float(item.deal_score or 0.0),
                float(item.price_vs_median or 0.0),
                item.title,
            )
        )

    payload = ListingsResponse(
        query=query,
        currency=currency,
        normalized_query=insights.normalized_query,
        config_summary=insights.config_summary,
        storage_gb=insights.storage_gb,
        ram_gb=insights.ram_gb,
        sort=sort,
        total=dataset.total_results,
        returned=len(listings),
        discount_percent=effective_from if sort == "cheap" else None,
        discount_from_percent=effective_from if sort == "cheap" else None,
        discount_to_percent=effective_to if sort == "cheap" else None,
        listings=listings,
    )
    await cache.set_json(
        cache_key,
        payload.model_dump(),
        ttl=settings.cache_ttl_seconds,
    )
    return payload
