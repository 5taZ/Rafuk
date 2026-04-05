from __future__ import annotations

from fastapi import APIRouter, Depends

from api.config import Settings
from api.dependencies import get_cache, get_currency_service, get_settings_dependency
from api.schemas import ListingItem, ListingsResponse
from api.services.aggregator import (
    compute_price_stats,
    compute_price_vs_median,
    extract_prices,
    get_param,
    normalize_price_byn,
    sort_listings,
)
from api.services.cache import CacheBackend
from api.services.currency_service import CurrencyService
from api.services.kufar_client import KufarClient

router = APIRouter(tags=["analytics"])


@router.get("/listings", response_model=ListingsResponse)
async def get_listings(
    query: str,
    sort: str = "newest",
    currency: str = "USD",
    settings: Settings = Depends(get_settings_dependency),
    cache: CacheBackend = Depends(get_cache),
    currency_service: CurrencyService = Depends(get_currency_service),
) -> ListingsResponse:
    cache_key = f"listings:{query}:{sort}:{currency}"
    cached = await cache.get_json(cache_key)
    if cached:
        return ListingsResponse(**cached)

    client = KufarClient(settings)
    try:
        response = await client.search(query=query, currency=currency)
    finally:
        await client.aclose()

    ads = response.get("ads", [])
    median_byn = compute_price_stats(extract_prices(ads)).median
    rates_payload = await currency_service.get_rates()
    rates = rates_payload["rates"]

    listings = []
    for ad in sort_listings(ads, sort, median_byn):
        price_byn = normalize_price_byn(ad.get("price_byn")) or 0.0
        listings.append(
            ListingItem(
                ad_id=int(ad.get("ad_id", 0)),
                subject=str(ad.get("subject", "")),
                price=currency_service.convert_from_byn(price_byn, currency, rates),
                currency=currency,
                ad_link=str(ad.get("ad_link", "")),
                list_time=ad.get("list_time"),
                region_id=ad.get("region_id"),
                condition=get_param(ad, "condition"),
                seller_type=get_param(ad, "seller_type"),
                price_vs_median=compute_price_vs_median(ad, median_byn),
            )
        )

    payload = ListingsResponse(
        query=query,
        currency=currency,
        sort=sort,
        total=len(listings),
        listings=listings,
    )
    await cache.set_json(
        cache_key,
        payload.model_dump(),
        ttl=settings.cache_ttl_seconds,
    )
    return payload
