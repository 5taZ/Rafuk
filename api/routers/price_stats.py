from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.config import Settings
from api.dependencies import (
    get_cache,
    get_currency_service,
    get_session_factory_dependency,
    get_settings_dependency,
)
from api.schemas import PriceStatsResponse
from api.services.aggregator import (
    apply_search_mode,
    build_query_key,
    compute_price_stats,
    extract_prices,
)
from api.services.cache import CacheBackend
from api.services.currency_service import CurrencyService
from api.services.history_service import snapshot_bucket, upsert_query_snapshot
from api.services.kufar_client import KufarClient

router = APIRouter(tags=["analytics"])


@router.get("/price-stats", response_model=PriceStatsResponse)
async def get_price_stats(
    query: str,
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

    client = KufarClient(settings)
    try:
        response = await client.search_all_ads(query=query, currency=currency)
    finally:
        await client.aclose()

    ads = apply_search_mode(response.get("ads", []), query, strict_search)
    prices_byn = extract_prices(ads)
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
    payload = PriceStatsResponse(
        query=query,
        currency=currency,
        count=stats.count,
        total_results=len(ads),
        analyzed_count=stats.count,
        **converted,
    )
    async with session_factory() as session:
        await upsert_query_snapshot(
            session,
            query=build_query_key(query, strict_search),
            ads=ads,
            total_results=payload.total_results,
            bucket_at=snapshot_bucket(datetime.now(UTC)),
        )
        await session.commit()
    await cache.set_json(cache_key, payload.model_dump(), ttl=settings.cache_ttl_seconds)
    return payload
