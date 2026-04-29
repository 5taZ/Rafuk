from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.config import Settings
from api.dependencies import (
    get_cache,
    get_currency_service,
    get_session_factory_dependency,
    get_settings_dependency,
    get_telegram_user,
)
from api.limiter import limiter
from api.schemas import PriceHistoryPoint, PriceHistoryResponse
from api.services.aggregator import build_query_key
from api.services.cache import CacheBackend
from api.services.currency_service import CurrencyService
from api.services.history_service import load_query_snapshots
from api.validators import MAX_QUERY_LENGTH

router = APIRouter(tags=["analytics"])


@router.get("/price-history", response_model=PriceHistoryResponse)
@limiter.limit("30/minute")
async def get_price_history(
    request: Request,
    query: str = Query(..., min_length=1, max_length=MAX_QUERY_LENGTH, description="Search query"),
    currency: str = "BYN",
    days: int = 7,
    strict_search: bool = False,
    settings: Settings = Depends(get_settings_dependency),
    cache: CacheBackend = Depends(get_cache),
    currency_service: CurrencyService = Depends(get_currency_service),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory_dependency),
    _user=Depends(get_telegram_user),
) -> PriceHistoryResponse:
    bounded_days = max(1, min(days, 90))
    search_key = build_query_key(query, strict_search)
    cache_key = f"price-history:{search_key}:{currency}:{bounded_days}"
    cached = await cache.get_json(cache_key)
    if cached:
        return PriceHistoryResponse(**cached)

    async with session_factory() as session:
        snapshots = await load_query_snapshots(session, query=search_key, days=bounded_days)

    rates_payload = await currency_service.get_rates()
    rates = rates_payload["rates"]
    points = [
        PriceHistoryPoint(
            snapshot_at=snapshot.snapshot_at,
            mean=currency_service.convert_from_byn(float(snapshot.mean_byn), currency, rates),
            median=currency_service.convert_from_byn(float(snapshot.median_byn), currency, rates),
            min=currency_service.convert_from_byn(float(snapshot.min_byn), currency, rates),
            max=currency_service.convert_from_byn(float(snapshot.max_byn), currency, rates),
            analyzed_count=snapshot.analyzed_count,
            total_results=snapshot.total_results,
        )
        for snapshot in snapshots
    ]
    payload = PriceHistoryResponse(
        query=query,
        currency=currency,
        days=bounded_days,
        points=points,
    )
    await cache.set_json(
        cache_key,
        payload.model_dump(mode="json"),
        ttl=settings.cache_ttl_seconds,
    )
    return payload
