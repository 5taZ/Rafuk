from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status

from api.config import Settings
from api.dependencies import get_cache, get_currency_service, get_settings_dependency
from api.rate_limit import limiter
from api.schemas import ListingDetailResponse
from api.services.aggregator import DATASET_CURRENCY, coerce_ad_id, response_cache_key
from api.services.cache import CacheBackend
from api.services.currency_service import CurrencyService
from api.services.deal_workflow import compute_liquidity_insight
from api.services.kufar_client import KufarClient
from api.services.listing_mapper import build_listing_detail
from api.services.market_signals import duplicate_counts
from api.services.query_pipeline import load_query_dataset
from api.validators import DEFAULT_CURRENCY, CurrencyCode, SearchQuery

router = APIRouter(tags=["analytics"])


@router.get("/listing-detail", response_model=ListingDetailResponse)
@limiter.limit("30/minute")
async def get_listing_detail(
    request: Request,
    ad_id: int,
    query: SearchQuery,
    currency: CurrencyCode = DEFAULT_CURRENCY,
    strict_search: bool = False,
    settings: Settings = Depends(get_settings_dependency),
    cache: CacheBackend = Depends(get_cache),
    currency_service: CurrencyService = Depends(get_currency_service),
) -> ListingDetailResponse:
    cache_key = response_cache_key("listing-detail", query, strict_search, ad_id, currency)
    cached = await cache.get_json(cache_key)
    if cached:
        return ListingDetailResponse(**{**cached, "query": query})

    dataset = await load_query_dataset(
        query=query,
        currency=DATASET_CURRENCY,
        strict_search=strict_search,
        settings=settings,
        client_factory=KufarClient,
    )
    ad = next((item for item in dataset.ads if coerce_ad_id(item.get("ad_id")) == ad_id), None)
    if ad is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Объявление не найдено в выдаче по этому запросу",
        )

    median_byn = dataset.price_stats.median
    duplicate_index = duplicate_counts(dataset.ads)
    liquidity = compute_liquidity_insight(dataset.ads, dataset.price_stats)
    rates_payload = await currency_service.get_rates()
    payload = build_listing_detail(
        ad=ad,
        query=query,
        currency=currency,
        rates=rates_payload["rates"],
        currency_service=currency_service,
        median_byn=median_byn,
        market_stats=dataset.price_stats,
        liquidity=liquidity,
        duplicate_count=duplicate_index.get(ad_id, 0),
    )
    await cache.set_json(cache_key, payload.model_dump(), ttl=settings.cache_ttl_seconds)
    return payload
