from __future__ import annotations

from fastapi import APIRouter, Depends

from api.config import Settings
from api.dependencies import get_cache, get_currency_service, get_settings_dependency
from api.schemas import ListingItem, ListingsResponse
from api.services.aggregator import (
    apply_search_mode,
    compute_price_stats,
    compute_price_vs_median,
    extract_prices,
    filter_deal_ads,
    get_param,
    normalize_price_byn,
    sort_listings,
)
from api.services.cache import CacheBackend
from api.services.currency_service import CurrencyService
from api.services.kufar_client import KufarClient
from api.services.listing_mapper import first_image_url

router = APIRouter(tags=["analytics"])


@router.get("/listings", response_model=ListingsResponse)
async def get_listings(
    query: str,
    sort: str = "newest",
    currency: str = "USD",
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

    cache_key = (
        f"listings:{query}:{sort}:{currency}:{discount_percent}:"
        f"{effective_from}:{effective_to}:{strict_search}"
    )
    cached = await cache.get_json(cache_key)
    if cached:
        return ListingsResponse(**cached)

    client = KufarClient(settings)
    try:
        response = await client.search_all_ads(query=query, currency=currency)
    finally:
        await client.aclose()

    ads = apply_search_mode(response.get("ads", []), query, strict_search)
    median_byn = compute_price_stats(extract_prices(ads)).median
    rates_payload = await currency_service.get_rates()
    rates = rates_payload["rates"]

    deal_ads = (
        filter_deal_ads(ads, median_byn, effective_from, effective_to)
        if sort == "cheap"
        else ads
    )
    sorted_ads = sort_listings(deal_ads, sort, median_byn)
    listings = []
    for ad in sorted_ads[:200]:
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
                thumbnail=first_image_url(ad),
            )
        )

    payload = ListingsResponse(
        query=query,
        currency=currency,
        sort=sort,
        total=len(ads),
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
