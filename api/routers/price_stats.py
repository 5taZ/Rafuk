from __future__ import annotations

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Request
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.config import Settings
from api.dependencies import (
    get_cache,
    get_currency_service,
    get_session_factory_dependency,
    get_settings_dependency,
)
from api.rate_limit import limiter
from api.schemas import PriceStatsResponse
from api.services.aggregator import (
    DATASET_CURRENCY,
    build_query_key,
    response_cache_key,
)
from api.services.cache import CacheBackend
from api.services.currency_service import CurrencyService
from api.services.history_service import snapshot_bucket, upsert_query_snapshot
from api.services.kufar_client import KufarClient
from api.services.query_pipeline import convert_price_stats, load_query_dataset
from api.services.reseller_tools import analyze_query_text
from api.validators import DEFAULT_CURRENCY, CurrencyCode, SearchQuery

logger = logging.getLogger(__name__)

router = APIRouter(tags=["analytics"])


@router.get("/price-stats", response_model=PriceStatsResponse)
@limiter.limit("30/minute")
async def get_price_stats(
    request: Request,
    query: SearchQuery,
    currency: CurrencyCode = DEFAULT_CURRENCY,
    strict_search: bool = False,
    settings: Settings = Depends(get_settings_dependency),
    cache: CacheBackend = Depends(get_cache),
    currency_service: CurrencyService = Depends(get_currency_service),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory_dependency),
) -> PriceStatsResponse:
    cache_key = response_cache_key("price-stats", query, strict_search, currency)
    cached = await cache.get_json(cache_key)
    if cached:
        return PriceStatsResponse(**{**cached, "query": query})

    dataset = await load_query_dataset(
        query=query,
        currency=DATASET_CURRENCY,
        strict_search=strict_search,
        settings=settings,
        client_factory=KufarClient,
    )
    stats = dataset.price_stats
    rates_payload = await currency_service.get_rates()
    converted = convert_price_stats(
        stats,
        currency=currency,
        rates=rates_payload["rates"],
        currency_service=currency_service,
    )
    converted.pop("count", None)
    insights = analyze_query_text(query)
    payload = PriceStatsResponse(
        query=query,
        currency=currency,
        normalized_query=insights.normalized_query,
        config_summary=insights.config_summary,
        storage_gb=insights.storage_gb,
        ram_gb=insights.ram_gb,
        count=stats.count,
        total_results=dataset.total_results,
        analyzed_count=stats.count,
        fair_price_from=converted.get("q1"),
        fair_price_to=converted.get("q3"),
        **converted,
    )
    await _record_snapshot(
        session_factory,
        query_key=build_query_key(query, strict_search),
        ads=dataset.ads,
        total_results=payload.total_results,
    )
    await cache.set_json(cache_key, payload.model_dump(), ttl=settings.cache_ttl_seconds)
    return payload


async def _record_snapshot(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    query_key: str,
    ads: list[dict],
    total_results: int,
) -> None:
    """Upsert the hourly price snapshot; history is best-effort and never fails the stats."""
    try:
        async with session_factory() as session:
            await upsert_query_snapshot(
                session,
                query=query_key,
                ads=ads,
                total_results=total_results,
                bucket_at=snapshot_bucket(datetime.now(UTC)),
            )
            await session.commit()
    except SQLAlchemyError:
        logger.warning("Failed to record price snapshot for %r", query_key, exc_info=True)
