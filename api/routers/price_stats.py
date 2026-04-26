from __future__ import annotations

import asyncio
import logging
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
    extract_search_refinements,
)
from api.services.cache import CacheBackend
from api.services.currency_service import CurrencyService
from api.services.history_service import snapshot_bucket, upsert_query_snapshot
from api.services.kufar_client import KufarClient
from api.services.query_pipeline import (
    KUFAR_CATEGORY_LABELS,
    convert_price_stats,
    fetch_category_totals,
    load_query_dataset,
)
from api.services.reseller_tools import analyze_query_text
from api.validators import MAX_QUERY_LENGTH

logger = logging.getLogger(__name__)
router = APIRouter(tags=["analytics"])


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

    # Kufar pagination (~1s) and NBRB rates fetch (~600ms cold) are
    # independent — overlap them so the wall-clock cost is the slower
    # of the two instead of the sum.
    dataset, rates_payload = await asyncio.gather(
        load_query_dataset(
            query=query,
            currency=currency,
            strict_search=strict_search,
            settings=settings,
            client=kufar_client,
            category=category,
            cache=cache,
        ),
        currency_service.get_rates(),
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

    # Replace per-category counts with the *post-filter* count Kufar
    # would actually surface for `cat=<id>` (mirrors kufar.by sidebar)
    # AND add sibling subcategories that didn't appear in the first
    # 200 ads but are still meaningful for the query (e.g. "Легковые
    # авто" for a query whose top 200 ads are mostly "Запчасти").
    if category is None:
        seed_ids = [int(c["id"]) for c in category_distribution if c.get("id") is not None]
        # Sibling expansion + per-category fan-out is the dominant
        # cost on cold queries (~2s for 7 categories @ 0.3s rate
        # limit). Two fast paths:
        #
        #  1. Only one category present → fan-out is pointless; the
        #     chip count is just dataset.total_results.
        #  2. One category is overwhelmingly dominant (≥80% of the
        #     200-ad sample, e.g. "iphone 13" → 92% Мобильные
        #     телефоны) → skip sibling expansion. Fetch real totals
        #     ONLY for the minor cats already in the dataset, and
        #     credit the dominant cat with dataset.total_results
        #     (a known-good number, no extra Kufar call). This drops
        #     the fan-out from 7 → 2-3 calls on the typical query.
        only_one_category = (
            len(category_distribution) == 1
            and len(seed_ids) == 1
        )
        total_dataset_ads = sum(c["count"] for c in category_distribution) or 1
        dominant_share = (
            category_distribution[0]["count"] / total_dataset_ads
            if category_distribution
            else 0.0
        )
        dominant_id = seed_ids[0] if seed_ids else None
        if only_one_category and dominant_id is not None:
            cat_ids: list[int] = []
            totals_by_id: dict[int, int] = {
                dominant_id: dataset.total_results or category_distribution[0]["count"]
            }
        elif dominant_share >= 0.80 and dominant_id is not None:
            # In-dataset minor cats only — no sibling expansion.
            minor_ids = [cid for cid in seed_ids if cid != dominant_id]
            cat_ids = minor_ids
            minor_totals = await fetch_category_totals(
                query=query,
                currency=currency,
                strict_search=strict_search,
                client=kufar_client,
                category_ids=minor_ids,
                cache=cache,
            )
            totals_by_id = {dominant_id: dataset.total_results, **minor_totals}
        else:
            # Diverse query (no dominant cat): just fan out to the
            # cats that already appeared organically in the dataset.
            # Sibling expansion is skipped here — siblings only
            # mattered when the dataset was monopolised by a single
            # over-narrow cat (e.g. all-parts result for "Audi Q7"),
            # which is now caught by the >=80% dominant fast path.
            # In-dataset-only fan-out trims ноутбук from 18 → 10
            # cats and drops cold-cache wall-clock by ~1 s.
            cat_ids = seed_ids
            totals_by_id = await fetch_category_totals(
                query=query,
                currency=currency,
                strict_search=strict_search,
                client=kufar_client,
                category_ids=cat_ids,
                cache=cache,
            )
        # Build a lookup of existing chips by id so we can update or
        # extend in place. ``totals_by_id`` already covers both the
        # in-dataset chips (fast-path) and the expanded siblings
        # (full fan-out).
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
        total_results=dataset.total_results,
        analyzed_count=stats.count,
        fair_price_from=converted.get("q1"),
        fair_price_to=converted.get("q3"),
        categories=category_distribution,
        suggested_refinements=suggested_refinements,
        **converted,
    )
    # Persist snapshot in the background — it feeds the price-history
    # chart but is irrelevant for *this* response. Detaching it from
    # the request task drops ~80-150 ms off the user-perceived latency
    # on cold queries.
    asyncio.create_task(
        _persist_snapshot_safe(
            session_factory,
            query_key=build_query_key(query, strict_search),
            ads=dataset.ads,
            total_results=payload.total_results,
            bucket_at=snapshot_bucket(datetime.now(UTC)),
        )
    )
    await cache.set_json(cache_key, payload.model_dump(), ttl=settings.cache_ttl_seconds)
    return payload
