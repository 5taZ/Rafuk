from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from api.config import Settings
from api.dependencies import get_cache, get_currency_service, get_settings_dependency
from api.schemas import ListingsResponse
from api.services.aggregator import (
    DATASET_CURRENCY,
    coerce_ad_id,
    compute_price_vs_median,
    filter_deal_ads,
    response_cache_key,
    sort_listings,
)
from api.services.cache import CacheBackend
from api.services.currency_service import CurrencyService
from api.services.deal_workflow import compute_liquidity_insight
from api.services.kufar_client import KufarClient
from api.services.listing_mapper import safe_build_listing_item
from api.services.market_signals import duplicate_counts
from api.services.query_pipeline import load_query_dataset
from api.services.reseller_tools import DealScore, analyze_query_text, compute_deal_score
from api.validators import DEFAULT_CURRENCY, CurrencyCode, SearchQuery

router = APIRouter(tags=["analytics"])

MAX_LISTINGS = 200
SCORED_SORTS = {"cheap", "deal_score"}


def _rank_by_deal_score(
    ads: list[dict[str, Any]],
    *,
    query: str,
    median_byn: float,
    market_stats,
    duplicate_index: dict[int, int],
) -> list[tuple[dict[str, Any], DealScore]]:
    """Score every candidate before truncating, so older bargains are not cut off."""
    scored = [
        (
            ad,
            compute_deal_score(
                ad,
                query=query,
                market_stats=market_stats,
                duplicate_count=duplicate_index.get(coerce_ad_id(ad.get("ad_id")) or 0, 0),
            ),
        )
        for ad in ads
    ]
    scored.sort(
        key=lambda pair: (
            -pair[1].score,
            compute_price_vs_median(pair[0], median_byn),
            str(pair[0].get("subject") or ""),
        )
    )
    return scored


@router.get("/listings", response_model=ListingsResponse)
async def get_listings(
    query: SearchQuery,
    sort: str = "newest",
    currency: CurrencyCode = DEFAULT_CURRENCY,
    strict_search: bool = False,
    discount_percent: float = 10.0,
    discount_from_percent: float | None = None,
    discount_to_percent: float | None = None,
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

    # Discount bounds only shape the "cheap" response.
    discount_parts = (effective_from, effective_to) if sort == "cheap" else ()
    cache_key = response_cache_key(
        "listings", query, strict_search, sort, currency, *discount_parts
    )
    cached = await cache.get_json(cache_key)
    if cached:
        return ListingsResponse(**{**cached, "query": query})

    dataset = await load_query_dataset(
        query=query,
        currency=DATASET_CURRENCY,
        strict_search=strict_search,
        settings=settings,
        client_factory=KufarClient,
    )
    market_stats = dataset.price_stats
    median_byn = market_stats.median
    duplicate_index = duplicate_counts(dataset.ads)
    liquidity = compute_liquidity_insight(dataset.ads, market_stats)
    rates_payload = await currency_service.get_rates()
    rates = rates_payload["rates"]
    insights = analyze_query_text(query)

    if sort in SCORED_SORTS:
        candidates = (
            filter_deal_ads(dataset.ads, median_byn, effective_from, effective_to)
            if sort == "cheap"
            else dataset.ads
        )
        total = len(candidates) if sort == "cheap" else dataset.total_results
        ranked = _rank_by_deal_score(
            candidates,
            query=query,
            median_byn=median_byn,
            market_stats=market_stats,
            duplicate_index=duplicate_index,
        )
    else:
        total = dataset.total_results
        ranked = [(ad, None) for ad in sort_listings(dataset.ads, sort, median_byn)]

    listings = []
    for ad, deal_score in ranked:
        if len(listings) >= MAX_LISTINGS:
            break
        item = safe_build_listing_item(
            ad,
            query=query,
            currency=currency,
            rates=rates,
            currency_service=currency_service,
            median_byn=median_byn,
            market_stats=market_stats,
            liquidity=liquidity,
            duplicate_count=duplicate_index.get(coerce_ad_id(ad.get("ad_id")) or 0, 0),
            deal_score=deal_score,
        )
        if item is not None:
            listings.append(item)

    payload = ListingsResponse(
        query=query,
        currency=currency,
        normalized_query=insights.normalized_query,
        config_summary=insights.config_summary,
        storage_gb=insights.storage_gb,
        ram_gb=insights.ram_gb,
        sort=sort,
        total=total,
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
