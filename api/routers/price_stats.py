from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.config import Settings
from api.dependencies import (
    get_cache,
    get_currency_service,
    get_session_factory_dependency,
    get_settings_dependency,
    get_telegram_user,
)
from api.schemas import PriceStatsResponse
from api.services.aggregator import (
    build_query_key,
)
from api.services.cache import CacheBackend
from api.services.currency_service import CurrencyService
from api.services.history_service import snapshot_bucket, upsert_query_snapshot
from api.services.kufar_client import KufarClient
from api.services.query_pipeline import convert_price_stats, load_query_dataset
from api.services.reseller_tools import analyze_query_text
from api.validators import MAX_QUERY_LENGTH

router = APIRouter(tags=["analytics"], dependencies=[Depends(get_telegram_user)])


@router.get("/price-stats", response_model=PriceStatsResponse)
async def get_price_stats(
    query: str = Query(..., min_length=1, max_length=MAX_QUERY_LENGTH, description="Search query"),
    currency: str = "USD",
    strict_search: bool = False,
    settings: Settings = Depends(get_settings_dependency),
    cache: CacheBackend = Depends(get_cache),
    currency_service: CurrencyService = Depends(get_currency_service),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory_dependency),
) -> PriceStatsResponse:
    cache_key = f"price-stats:{query}:{currency}:{strict_search}"
    cached = await cache.get_json(cache_key)
    if cached:
        return PriceStatsResponse(**cached)

    dataset = await load_query_dataset(
        query=query,
        currency=currency,
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
    async with session_factory() as session:
        await upsert_query_snapshot(
            session,
            query=build_query_key(query, strict_search),
            ads=dataset.ads,
            total_results=payload.total_results,
            bucket_at=snapshot_bucket(datetime.now(UTC)),
        )
        await session.commit()
    await cache.set_json(cache_key, payload.model_dump(), ttl=settings.cache_ttl_seconds)
    return payload
