from __future__ import annotations

from fastapi import APIRouter, Depends

from api.config import Settings
from api.dependencies import get_cache, get_currency_service, get_settings_dependency
from api.schemas import SegmentsResponse
from api.services.aggregator import compute_price_stats, extract_prices
from api.services.cache import CacheBackend
from api.services.currency_service import CurrencyService
from api.services.kufar_client import KufarClient
from api.services.parallel_kufar import parallel_search_all

router = APIRouter(tags=["analytics"])


def _convert_segment(
    stats: dict[str, float | int],
    currency: str,
    rates: dict[str, float],
    currency_service: CurrencyService,
) -> dict[str, float | int]:
    return {
        **stats,
        "mean": currency_service.convert_from_byn(float(stats["mean"]), currency, rates),
        "median": currency_service.convert_from_byn(float(stats["median"]), currency, rates),
        "q1": currency_service.convert_from_byn(float(stats["q1"]), currency, rates),
        "q3": currency_service.convert_from_byn(float(stats["q3"]), currency, rates),
        "min": currency_service.convert_from_byn(float(stats["min"]), currency, rates),
        "max": currency_service.convert_from_byn(float(stats["max"]), currency, rates),
    }


@router.get("/segments", response_model=SegmentsResponse)
async def get_segments(
    query: str,
    currency: str = "USD",
    settings: Settings = Depends(get_settings_dependency),
    cache: CacheBackend = Depends(get_cache),
    currency_service: CurrencyService = Depends(get_currency_service),
) -> SegmentsResponse:
    cache_key = f"segments:{query}:{currency}"
    cached = await cache.get_json(cache_key)
    if cached:
        return SegmentsResponse(**cached)

    client = KufarClient(settings)
    tasks = [
        {
            "query": query,
            "currency": currency,
            "condition": "new",
            "seller_type": "search_owner",
        },
        {
            "query": query,
            "currency": currency,
            "condition": "new",
            "seller_type": "search_business",
        },
        {
            "query": query,
            "currency": currency,
            "condition": "used",
            "seller_type": "search_owner",
        },
        {
            "query": query,
            "currency": currency,
            "condition": "used",
            "seller_type": "search_business",
        },
    ]
    try:
        responses = await parallel_search_all(client, tasks, settings)
    finally:
        await client.aclose()

    rates_payload = await currency_service.get_rates()
    rates = rates_payload["rates"]
    segment_names = ("new_private", "new_shop", "used_private", "used_shop")
    segment_values = []
    for response in responses:
        stats = compute_price_stats(extract_prices(response.get("ads", []))).model_dump()
        segment_values.append(_convert_segment(stats, currency, rates, currency_service))

    payload = SegmentsResponse(
        query=query,
        currency=currency,
        **dict(zip(segment_names, segment_values, strict=True)),
    )
    await cache.set_json(cache_key, payload.model_dump(), ttl=settings.cache_ttl_seconds)
    return payload
