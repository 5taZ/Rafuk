from __future__ import annotations

from typing import Literal

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
from api.services.cache import CacheBackend, digest_cache_key
from api.services.currency_service import CurrencyService
from api.services.history_service import load_query_snapshots
from api.validators import MAX_QUERY_LENGTH

router = APIRouter(tags=["analytics"])


@router.get("/price-history", response_model=PriceHistoryResponse)
@limiter.limit("30/minute")
async def get_price_history(
    request: Request,
    query: str = Query(..., min_length=1, max_length=MAX_QUERY_LENGTH, description="Search query"),
    currency: Literal["BYN", "USD", "EUR", "RUB"] = "BYN",
    days: int = Query(default=7, ge=1, le=90),
    strict_search: bool = True,
    category: int | None = None,
    settings: Settings = Depends(get_settings_dependency),
    cache: CacheBackend = Depends(get_cache),
    currency_service: CurrencyService = Depends(get_currency_service),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory_dependency),
    _user=Depends(get_telegram_user),
) -> PriceHistoryResponse:
    bounded_days = max(1, min(days, 90))
    search_key = build_query_key(query, strict_search, category)
    cache_key = digest_cache_key(
        "price-history",
        {
            "search": search_key,
            "cur": currency,
            "days": bounded_days,
        },
    )
    cached = await cache.get_json(cache_key)
    if cached:
        return PriceHistoryResponse(**cached)

    async with session_factory() as session:
        snapshots = await load_query_snapshots(session, query=search_key, days=bounded_days)

    # BE-MEDIUM (issues §2.2): NBRB outage shouldn't 500 the chart.
    try:
        rates_payload = await currency_service.get_rates()
    except Exception:  # noqa: BLE001
        rates_payload = {"rates": {"BYN": 1.0}}
    rates = rates_payload["rates"]
    points = [
        PriceHistoryPoint(
            snapshot_at=snapshot.snapshot_at,
            mean=currency_service.convert_from_byn(float(snapshot.mean_byn), currency, rates),
            median=currency_service.convert_from_byn(float(snapshot.median_byn), currency, rates),
            q1=(
                currency_service.convert_from_byn(float(snapshot.q1_byn), currency, rates)
                if snapshot.q1_byn is not None else None
            ),
            q3=(
                currency_service.convert_from_byn(float(snapshot.q3_byn), currency, rates)
                if snapshot.q3_byn is not None else None
            ),
            min=currency_service.convert_from_byn(float(snapshot.min_byn), currency, rates),
            max=currency_service.convert_from_byn(float(snapshot.max_byn), currency, rates),
            analyzed_count=snapshot.analyzed_count,
            total_results=snapshot.total_results,
            fetched_count=snapshot.fetched_count,
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
