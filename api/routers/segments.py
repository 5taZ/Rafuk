from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from api.config import Settings
from api.dependencies import get_cache, get_currency_service, get_settings_dependency
from api.rate_limit import limiter
from api.schemas import SegmentsResponse
from api.services.aggregator import DATASET_CURRENCY, response_cache_key
from api.services.cache import CacheBackend
from api.services.currency_service import CurrencyService
from api.services.kufar_client import KufarClient
from api.services.parallel_kufar import parallel_search_all
from api.services.query_pipeline import convert_price_stats, load_segment_datasets
from api.validators import DEFAULT_CURRENCY, CurrencyCode, SearchQuery

router = APIRouter(tags=["analytics"])


@router.get("/segments", response_model=SegmentsResponse)
@limiter.limit("10/minute")
async def get_segments(
    request: Request,
    query: SearchQuery,
    currency: CurrencyCode = DEFAULT_CURRENCY,
    strict_search: bool = False,
    settings: Settings = Depends(get_settings_dependency),
    cache: CacheBackend = Depends(get_cache),
    currency_service: CurrencyService = Depends(get_currency_service),
) -> SegmentsResponse:
    cache_key = response_cache_key("segments", query, strict_search, currency)
    cached = await cache.get_json(cache_key)
    if cached:
        return SegmentsResponse(**{**cached, "query": query})

    datasets = await load_segment_datasets(
        query=query,
        currency=DATASET_CURRENCY,
        strict_search=strict_search,
        settings=settings,
        client_factory=KufarClient,
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
