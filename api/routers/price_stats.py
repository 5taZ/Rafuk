from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.config import Settings
from api.dependencies import (
    get_cache,
    get_currency_service,
    get_kufar_client,
    get_session_factory_dependency,
    get_settings_dependency,
    get_telegram_user,
)
from api.limiter import limiter
from api.schemas import PriceStatsResponse
from api.services.aggregator import (
    build_query_key,
    extract_category_distribution,
    extract_search_refinements,
)
from api.services.cache import CacheBackend, digest_cache_key
from api.services.currency_service import CurrencyService
from api.services.history_service import snapshot_bucket, upsert_query_snapshot
from api.services.kufar_client import KufarClient
from api.services.query_pipeline import (
    CATEGORY_TOTAL_MAX_CALLS,
    KUFAR_CATEGORY_LABELS,
    convert_price_stats,
    fetch_category_totals,
    load_query_dataset_with_fallback,
)
from api.services.reseller_tools import analyze_query_text
from api.validators import MAX_QUERY_LENGTH

logger = logging.getLogger(__name__)
router = APIRouter(tags=["analytics"])


def _price_stats_cache_key(
    *,
    query: str,
    currency: str,
    strict_search: bool,
    category: int | None,
) -> str:
    return digest_cache_key(
        "price-stats",
        {
            "query": query,
            "currency": currency,
            "strict_search": strict_search,
            "category": category,
            "category_total_semantics": 2,
        },
    )


async def _persist_snapshot_safe(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    query_key: str,
    ads: list,
    total_results: int,
    bucket_at: datetime,
) -> None:
    """Background DB upsert. Must never raise into the request task."""
    try:
        async with session_factory() as session:
            await upsert_query_snapshot(
                session,
                query=query_key,
                ads=ads,
                total_results=total_results,
                bucket_at=bucket_at,
            )
            await session.commit()
    except Exception:  # noqa: BLE001 — never break user-facing stats
        logger.exception("query_snapshot upsert failed for %s", query_key)


@router.get("/price-stats", response_model=PriceStatsResponse)
@limiter.limit("30/minute")
async def get_price_stats(
    request: Request,
    background_tasks: BackgroundTasks,
    query: str = Query(..., min_length=1, max_length=MAX_QUERY_LENGTH, description="Search query"),
    currency: Literal["BYN", "USD", "EUR", "RUB"] = "BYN",
    strict_search: bool = True,
    category: int | None = None,
    force_refresh: bool = False,
    settings: Settings = Depends(get_settings_dependency),
    cache: CacheBackend = Depends(get_cache),
    currency_service: CurrencyService = Depends(get_currency_service),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory_dependency),
    kufar_client: KufarClient = Depends(get_kufar_client),
    _user=Depends(get_telegram_user),
) -> PriceStatsResponse:
    cache_key = _price_stats_cache_key(
        query=query,
        currency=currency,
        strict_search=strict_search,
        category=category,
    )
    cached = await cache.get_json(cache_key) if not force_refresh else None
    if cached:
        return PriceStatsResponse(**cached)

    # Kufar pagination (~1s) and NBRB rates fetch (~600ms cold) are
    # independent — overlap them so the wall-clock cost is the slower
    # of the two instead of the sum.
    async def _fetch_dataset():
        fb = await load_query_dataset_with_fallback(
            query=query,
            currency=currency,
            strict_search=strict_search,
            settings=settings,
            client=kufar_client,
            category=category,
            cache=cache,
            force_refresh=force_refresh,
        )
        return fb.dataset

    async def _fetch_rates():
        try:
            return await currency_service.get_rates()
        except Exception:
            return {"rates": {"BYN": 1.0}}

    dataset, rates_payload = await asyncio.gather(
        _fetch_dataset(),
        _fetch_rates(),
    )
    stats = dataset.price_stats
    converted = convert_price_stats(
        stats,
        currency=currency,
        rates=rates_payload["rates"],
        currency_service=currency_service,
    )
    converted.pop("count", None)
    insights = analyze_query_text(query)
    category_distribution = extract_category_distribution(dataset.ads)
    # When strict_search is on, the user-facing total must match the strict-filtered
    # count to stay consistent with the listings badge and category chip sums.
    effective_total = len(dataset.ads) if strict_search else dataset.total_results
    category_total_candidates = 0
    categories_limited = False

    # Replace sample-only category counts with the official per-category
    # totals Kufar returns for `cat=<id>` so the chips mirror kufar.by.
    if category is None:
        seed_ids = [int(c["id"]) for c in category_distribution if c.get("id") is not None]
        category_total_candidates = len(seed_ids)
        categories_limited = category_total_candidates > CATEGORY_TOTAL_MAX_CALLS
        totals_by_id = await fetch_category_totals(
            query=query,
            currency=currency,
            strict_search=strict_search,
            client=kufar_client,
            category_ids=seed_ids,
            cache=cache,
        )
        if len(seed_ids) == 1 and not totals_by_id:
            totals_by_id = {
                seed_ids[0]: effective_total or category_distribution[0]["count"]
            }
        # Build a lookup of existing chips by id so we can update or
        # extend in place. ``totals_by_id`` covers the in-dataset chips
        # that Kufar accepted before the fan-out cap.
        by_id: dict[int, dict] = {int(c["id"]): c for c in category_distribution}
        for cat_id, real_total in totals_by_id.items():
            if real_total is None or real_total <= 0:
                # Sibling that has no matches for this query — skip;
                # if it was already in the distribution we keep its
                # original count rather than zeroing it out.
                continue
            existing = by_id.get(cat_id)
            if existing is not None:
                existing["count"] = real_total
            else:
                # Sibling not present in the first-200 ads → look up
                # the label in the static KUFAR_CATEGORY_LABELS map.
                label = KUFAR_CATEGORY_LABELS.get(cat_id, f"Категория {cat_id}")
                category_distribution.append(
                    {"id": cat_id, "label": label, "count": real_total}
                )
        # Drop chips that ended up with 0 matches under the precise
        # filter — they're noise.
        category_distribution = [c for c in category_distribution if c.get("count", 0) > 0]
        category_distribution.sort(key=lambda c: c["count"], reverse=True)

    suggested_refinements = extract_search_refinements(dataset.ads, query)
    payload = PriceStatsResponse(
        query=query,
        currency=currency,
        normalized_query=insights.normalized_query,
        config_summary=insights.config_summary,
        storage_gb=insights.storage_gb,
        ram_gb=insights.ram_gb,
        count=stats.count,
        total_results=effective_total,
        analyzed_count=stats.count,
        fair_price_from=converted.get("q1"),
        fair_price_to=converted.get("q3"),
        categories=category_distribution,
        categories_limited=categories_limited,
        category_total_limit=CATEGORY_TOTAL_MAX_CALLS,
        category_total_candidates=category_total_candidates,
        suggested_refinements=suggested_refinements,
        **converted,
    )
    # Persist snapshot via FastAPI BackgroundTasks — runs after the
    # response is sent but is awaited by the framework before the
    # request fully tears down. That keeps cold-cache latency down
    # without leaking pending writes into pytest fixture teardowns
    # (which previously deadlocked on "database is locked" with the
    # bare asyncio.create_task variant).
    background_tasks.add_task(
        _persist_snapshot_safe,
        session_factory,
        query_key=build_query_key(query, strict_search, category),
        ads=dataset.ads,
        total_results=payload.total_results,
        bucket_at=snapshot_bucket(datetime.now(UTC)),
    )
    await cache.set_json(cache_key, payload.model_dump(), ttl=settings.cache_ttl_seconds)
    return payload
