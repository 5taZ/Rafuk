from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.config import Settings
from api.dependencies import (
    get_cache,
    get_currency_service,
    get_kufar_client,
    get_session_factory_dependency,
    get_settings_dependency,
)
from api.limiter import limiter
from api.schemas import PriceStatsResponse
from api.services.aggregator import (
    build_query_key,
    extract_category_distribution,
)
from api.services.cache import CacheBackend
from api.services.currency_service import CurrencyService
from api.services.history_service import snapshot_bucket, upsert_query_snapshot
from api.services.kufar_client import KufarClient
from api.services.query_pipeline import (
    convert_price_stats,
    fetch_category_totals,
    load_query_dataset,
)
from api.services.reseller_tools import analyze_query_text
from api.validators import MAX_QUERY_LENGTH

router = APIRouter(tags=["analytics"])


@router.get("/price-stats", response_model=PriceStatsResponse)
@limiter.limit("30/minute")
async def get_price_stats(
    request: Request,
    query: str = Query(..., min_length=1, max_length=MAX_QUERY_LENGTH, description="Search query"),
    currency: str = "BYN",
    strict_search: bool = False,
    category: int | None = None,
    settings: Settings = Depends(get_settings_dependency),
    cache: CacheBackend = Depends(get_cache),
    currency_service: CurrencyService = Depends(get_currency_service),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory_dependency),
    kufar_client: KufarClient = Depends(get_kufar_client),
) -> PriceStatsResponse:
    cache_key = f"price-stats:{query}:{currency}:{strict_search}:{category}"
    cached = await cache.get_json(cache_key)
    if cached:
        return PriceStatsResponse(**cached)

    dataset = await load_query_dataset(
        query=query,
        currency=currency,
        strict_search=strict_search,
        settings=settings,
        client=kufar_client,
        category=category,
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
    category_distribution = extract_category_distribution(dataset.ads)

    # Override per-category counts with the real `total` Kufar reports
    # for cat=<id> queries (mirrors what kufar.by sidebar shows).
    # Skips silently if Kufar is unreachable — chips fall back to the
    # local distribution count.
    if category_distribution and category is None:
        cat_ids = [int(c["id"]) for c in category_distribution if c.get("id") is not None]
        totals_by_id = await fetch_category_totals(
            query=query,
            currency=currency,
            client=kufar_client,
            category_ids=cat_ids,
        )
        for entry in category_distribution:
            real_total = totals_by_id.get(int(entry["id"]))
            if real_total is not None:
                entry["count"] = real_total
        # Re-sort by the (possibly enlarged) counts so the most popular
        # category stays on top.
        category_distribution.sort(key=lambda c: c["count"], reverse=True)

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
        categories=category_distribution,
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
