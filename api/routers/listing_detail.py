from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from api.config import Settings
from api.dependencies import (
    get_cache,
    get_currency_service,
    get_kufar_client,
    get_settings_dependency,
)
from api.limiter import limiter
from api.schemas import ListingDetailResponse
from api.services.aggregator import compute_category_price_stats
from api.services.cache import CacheBackend
from api.services.currency_service import CurrencyService
from api.services.deal_workflow import compute_liquidity_insight
from api.services.kufar_client import KufarClient
from api.services.listing_mapper import build_listing_detail
from api.services.query_pipeline import load_query_dataset_context
from api.validators import MAX_QUERY_LENGTH

router = APIRouter(tags=["analytics"])


@router.get("/listing-detail", response_model=ListingDetailResponse)
@limiter.limit("30/minute")
async def get_listing_detail(
    request: Request,
    ad_id: int,
    query: str = Query(..., min_length=1, max_length=MAX_QUERY_LENGTH, description="Search query"),
    currency: str = "BYN",
    strict_search: bool = False,
    category: int | None = None,
    reference_context: Literal["current", "base_query"] = "current",
    settings: Settings = Depends(get_settings_dependency),
    cache: CacheBackend = Depends(get_cache),
    currency_service: CurrencyService = Depends(get_currency_service),
    kufar_client: KufarClient = Depends(get_kufar_client),
) -> ListingDetailResponse:
    cache_key = (
        f"listing-detail:{query}:{ad_id}:{currency}:{strict_search}:{category}:{reference_context}"
    )
    cached = await cache.get_json(cache_key)
    if cached:
        return ListingDetailResponse(**cached)

    context = await load_query_dataset_context(
        query=query,
        currency=currency,
        strict_search=strict_search,
        settings=settings,
        client=kufar_client,
        reference_context=reference_context,
        category=category,
    )
    visible_dataset = context.visible
    reference_dataset = context.reference
    ad = next((item for item in visible_dataset.ads if int(item.get("ad_id", 0)) == ad_id), None)
    if ad is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Listing not found for this query",
        )

    median_byn = visible_dataset.price_stats.median
    category_price_stats = compute_category_price_stats(reference_dataset.ads)
    liquidity = compute_liquidity_insight(visible_dataset.ads, visible_dataset.price_stats, ad=ad)
    rates_payload = await currency_service.get_rates()
    payload = build_listing_detail(
        ad=ad,
        query=query,
        currency=currency,
        rates=rates_payload["rates"],
        currency_service=currency_service,
        median_byn=median_byn,
        market_stats=reference_dataset.price_stats,
        category_price_stats=category_price_stats,
        liquidity=liquidity,
    )
    await cache.set_json(cache_key, payload.model_dump(), ttl=settings.cache_ttl_seconds)
    return payload
