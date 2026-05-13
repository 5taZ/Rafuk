from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from api.config import Settings
from api.dependencies import (
    get_cache,
    get_currency_service,
    get_kufar_client,
    get_settings_dependency,
    get_telegram_user,
)
from api.limiter import limiter
from api.schemas import ListingDetailResponse
from api.services.aggregator import compute_category_price_stats, precompute_cluster_stats
from api.services.cache import CacheBackend
from api.services.currency_service import CurrencyService
from api.services.deal_workflow import compute_liquidity_insight
from api.services.kufar_client import KufarClient
from api.services.listing_mapper import build_listing_detail
from api.services.query_pipeline import load_query_dataset_context_with_fallback
from api.services.risk_detector import compute_risk_score, detect_risks
from api.validators import MAX_QUERY_LENGTH

router = APIRouter(tags=["analytics"])


@router.get("/listing-detail", response_model=ListingDetailResponse)
@limiter.limit("30/minute")
async def get_listing_detail(
    request: Request,
    ad_id: int,
    query: str = Query(..., min_length=1, max_length=MAX_QUERY_LENGTH, description="Search query"),
    currency: Literal["BYN", "USD", "EUR", "RUB"] = "BYN",
    strict_search: bool = True,
    category: int | None = None,
    reference_context: Literal["current", "base_query"] = "current",
    settings: Settings = Depends(get_settings_dependency),
    cache: CacheBackend = Depends(get_cache),
    currency_service: CurrencyService = Depends(get_currency_service),
    kufar_client: KufarClient = Depends(get_kufar_client),
    _user=Depends(get_telegram_user),
) -> ListingDetailResponse:
    cache_key = (
        f"listing-detail:{query}:{ad_id}:{currency}:{strict_search}:{category}:{reference_context}"
    )
    cached = await cache.get_json(cache_key)
    if cached:
        return ListingDetailResponse(**cached)

    fb = await load_query_dataset_context_with_fallback(
        query=query,
        currency=currency,
        strict_search=strict_search,
        settings=settings,
        client=kufar_client,
        reference_context=reference_context,
        category=category,
        cache=cache,
    )
    context = fb.context
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
    cluster_cache = precompute_cluster_stats(visible_dataset.ads, query=query)
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
        cluster_stats=cluster_cache.get(ad_id),
    )
    # Risk detection — runs after the listing detail is built so we can
    # reuse the market stats already fetched for the query.
    market_stats_dict = {"median": reference_dataset.price_stats.median}
    risk_factors = detect_risks(ad, market_stats=market_stats_dict)
    risk_score = compute_risk_score(risk_factors)
    payload.risk_score = risk_score
    payload.risk_factors = risk_factors
    await cache.set_json(cache_key, payload.model_dump(), ttl=settings.cache_ttl_seconds)
    return payload
