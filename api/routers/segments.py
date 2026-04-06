from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from api.config import Settings
from api.dependencies import get_cache, get_currency_service, get_kufar_client, get_settings_dependency, get_telegram_user
from api.schemas import SegmentsResponse
from api.services.cache import CacheBackend
from api.services.currency_service import CurrencyService
from api.services.kufar_client import KufarClient
from api.services.parallel_kufar import parallel_search_all
from api.services.query_pipeline import convert_price_stats, load_segment_datasets
from api.validators import MAX_QUERY_LENGTH

router = APIRouter(tags=["analytics"], dependencies=[Depends(get_telegram_user)])
@router.get("/segments", response_model=SegmentsResponse)
async def get_segments(
    query: str = Query(..., min_length=1, max_length=MAX_QUERY_LENGTH, description="Search query"),
    currency: str = "USD",
    strict_search: bool = False,
    settings: Settings = Depends(get_settings_dependency),
    cache: CacheBackend = Depends(get_cache),
    currency_service: CurrencyService = Depends(get_currency_service),
    kufar_client: KufarClient = Depends(get_kufar_client),
) -> SegmentsResponse:
    cache_key = f"segments:{query}:{currency}:{strict_search}"
    cached = await cache.get_json(cache_key)
    if cached:
        return SegmentsResponse(**cached)

    datasets = await load_segment_datasets(
        query=query,
        currency=currency,
        strict_search=strict_search,
        settings=settings,
        client=kufar_client,
        parallel_search=parallel_search_all,
    )
    rates_payload = await currency_service.get_rates()
    rates = rates_payload["rates"]

    payload = SegmentsResponse(
        query=query,
        currency=currency,
        **{
            name: convert_price_stats(
                dataset.price_stats,
                currency=currency,
                rates=rates,
                currency_service=currency_service,
            )
            for name, dataset in datasets.items()
        },
    )
    await cache.set_json(cache_key, payload.model_dump(), ttl=settings.cache_ttl_seconds)
    return payload
