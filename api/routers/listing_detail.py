from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from api.config import Settings
from api.dependencies import get_cache, get_currency_service, get_settings_dependency
from api.schemas import ListingDetailResponse
from api.services.aggregator import apply_search_mode, compute_price_stats, extract_prices
from api.services.cache import CacheBackend
from api.services.currency_service import CurrencyService
from api.services.kufar_client import KufarClient
from api.services.listing_mapper import build_listing_detail

router = APIRouter(tags=["analytics"])


@router.get("/listing-detail", response_model=ListingDetailResponse)
async def get_listing_detail(
    query: str,
    ad_id: int,
    currency: str = "BYN",
    strict_search: bool = False,
    settings: Settings = Depends(get_settings_dependency),
    cache: CacheBackend = Depends(get_cache),
    currency_service: CurrencyService = Depends(get_currency_service),
) -> ListingDetailResponse:
    cache_key = f"listing-detail:{query}:{ad_id}:{currency}:{strict_search}"
    cached = await cache.get_json(cache_key)
    if cached:
        return ListingDetailResponse(**cached)

    client = KufarClient(settings)
    try:
        response = await client.search_all_ads(query=query, currency=currency)
    finally:
        await client.aclose()

    ads = apply_search_mode(response.get("ads", []), query, strict_search)
    ad = next((item for item in ads if int(item.get("ad_id", 0)) == ad_id), None)
    if ad is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Listing not found for this query",
        )

    median_byn = compute_price_stats(extract_prices(ads)).median
    rates_payload = await currency_service.get_rates()
    payload = build_listing_detail(
        ad=ad,
        query=query,
        currency=currency,
        rates=rates_payload["rates"],
        currency_service=currency_service,
        median_byn=median_byn,
    )
    await cache.set_json(cache_key, payload.model_dump(), ttl=settings.cache_ttl_seconds)
    return payload
