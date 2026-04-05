from __future__ import annotations

from fastapi import APIRouter, Depends

from api.config import Settings
from api.dependencies import get_cache, get_currency_service, get_settings_dependency
from api.schemas import PriceStatsResponse
from api.services.aggregator import compute_price_stats, extract_prices
from api.services.cache import CacheBackend
from api.services.currency_service import CurrencyService
from api.services.kufar_client import KufarClient

router = APIRouter(tags=["analytics"])


@router.get("/price-stats", response_model=PriceStatsResponse)
async def get_price_stats(
    query: str,
    currency: str = "USD",
    settings: Settings = Depends(get_settings_dependency),
    cache: CacheBackend = Depends(get_cache),
    currency_service: CurrencyService = Depends(get_currency_service),
) -> PriceStatsResponse:
    cache_key = f"price-stats:{query}:{currency}"
    cached = await cache.get_json(cache_key)
    if cached:
        return PriceStatsResponse(**cached)

    client = KufarClient(settings)
    try:
        response = await client.search(query=query, currency=currency)
    finally:
        await client.aclose()

    prices_byn = extract_prices(response.get("ads", []))
    stats = compute_price_stats(prices_byn)
    rates_payload = await currency_service.get_rates()
    converted = {
        field: currency_service.convert_from_byn(
            getattr(stats, field),
            currency,
            rates_payload["rates"],
        )
        for field in ("mean", "median", "q1", "q3", "min", "max")
    }
    payload = PriceStatsResponse(query=query, currency=currency, count=stats.count, **converted)
    await cache.set_json(cache_key, payload.model_dump(), ttl=settings.cache_ttl_seconds)
    return payload
