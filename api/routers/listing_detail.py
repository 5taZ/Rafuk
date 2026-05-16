from __future__ import annotations

import logging
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import update
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
from api.models import LeadItem
from api.schemas import ListingDetailResponse
from api.services.aggregator import compute_category_price_stats, precompute_cluster_stats
from api.services.cache import CacheBackend, digest_cache_key
from api.services.currency_service import CurrencyService
from api.services.deal_workflow import compute_liquidity_insight
from api.services.kufar_client import KufarClient
from api.services.listing_mapper import build_listing_detail
from api.services.query_pipeline import load_query_dataset_context_with_fallback
from api.services.risk_detector import compute_risk_score, detect_risks
from api.services.workflow_store import ensure_user
from api.validators import MAX_QUERY_LENGTH

logger = logging.getLogger(__name__)

router = APIRouter(tags=["analytics"])


async def _backfill_lead_thumbnail(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    telegram_user_id: int,
    first_name: str | None,
    ad_id: int,
    thumbnail: str | None,
) -> None:
    """Backfill thumbnail on LeadItem rows with null thumbnail.

    Bot-callback adds (📌 В покупки / ⭐ В Избранное) used to land
    rows with thumbnail=NULL because the scheduler was reading the
    wrong field from the Kufar payload. Items added before the
    scheduler fix are already in the DB with null — when the user
    later opens detail and Kufar returns images, we patch the row
    so the card preview shows the photo too.

    Best-effort: a backfill failure must not break the detail GET.
    """
    if not thumbnail:
        return
    try:
        async with session_factory() as session:
            user_id = await ensure_user(
                session,
                telegram_user_id=telegram_user_id,
                first_name=first_name or "",
            )
            await session.execute(
                update(LeadItem)
                .where(
                    LeadItem.user_id == user_id,
                    LeadItem.ad_id == ad_id,
                    LeadItem.thumbnail.is_(None),
                )
                .values(thumbnail=thumbnail)
            )
            await session.commit()
    except Exception:  # noqa: BLE001
        logger.warning("Failed to backfill thumbnail for ad_id=%s", ad_id, exc_info=True)


@router.get("/listing-detail", response_model=ListingDetailResponse)
@limiter.limit("30/minute")
async def get_listing_detail(
    request: Request,
    ad_id: int,
    query: str = Query(..., min_length=1, max_length=MAX_QUERY_LENGTH, description="Search query"),
    currency: Literal["BYN", "USD", "EUR", "RUB"] = "BYN",
    strict_search: bool = True,
    category: int | None = None,
    reference_context: Literal["current", "base_query"] = "current",
    settings: Settings = Depends(get_settings_dependency),
    cache: CacheBackend = Depends(get_cache),
    currency_service: CurrencyService = Depends(get_currency_service),
    kufar_client: KufarClient = Depends(get_kufar_client),
    telegram_user=Depends(get_telegram_user),
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory_dependency),
) -> ListingDetailResponse:
    cache_key = digest_cache_key(
        "listing-detail",
        {
            "q": query,
            "ad": ad_id,
            "cur": currency,
            "strict": strict_search,
            "cat": category,
            "ref": reference_context,
        },
    )
    cached = await cache.get_json(cache_key)
    if cached:
        cached_response = ListingDetailResponse(**cached)
        # Backfill thumbnail on bot-added items even when serving from
        # cache — the cache TTL is much shorter than the lifetime of a
        # lead/watchlist row, so this stays useful across reloads.
        await _backfill_lead_thumbnail(
            session_factory,
            telegram_user_id=telegram_user.user_id,
            first_name=telegram_user.first_name,
            ad_id=ad_id,
            thumbnail=(cached_response.images[0] if cached_response.images else None),
        )
        return cached_response

    fb = await load_query_dataset_context_with_fallback(
        query=query,
        currency=currency,
        strict_search=strict_search,
        settings=settings,
        client=kufar_client,
        reference_context=reference_context,
        category=category,
        cache=cache,
    )
    context = fb.context
    visible_dataset = context.visible
    reference_dataset = context.reference
    ad = next((item for item in visible_dataset.ads if int(item.get("ad_id", 0)) == ad_id), None)
    if ad is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Listing not found for this query",
        )

    median_byn = visible_dataset.price_stats.median
    category_price_stats = compute_category_price_stats(reference_dataset.ads)
    cluster_cache = precompute_cluster_stats(visible_dataset.ads, query=query)
    liquidity = compute_liquidity_insight(visible_dataset.ads, visible_dataset.price_stats, ad=ad)
    # BE-MEDIUM (issues §2.2): NBRB outage shouldn't 500 the detail.
    try:
        rates_payload = await currency_service.get_rates()
    except Exception:  # noqa: BLE001
        rates_payload = {"rates": {"BYN": 1.0}}
    payload = build_listing_detail(
        ad=ad,
        query=query,
        currency=currency,
        rates=rates_payload["rates"],
        currency_service=currency_service,
        median_byn=median_byn,
        market_stats=reference_dataset.price_stats,
        category_price_stats=category_price_stats,
        liquidity=liquidity,
        cluster_stats=cluster_cache.get(ad_id),
    )
    # Risk detection — runs after the listing detail is built so we can
    # reuse the market stats already fetched for the query.
    market_stats_dict = {"median": reference_dataset.price_stats.median}
    risk_factors = detect_risks(ad, market_stats=market_stats_dict)
    risk_score = compute_risk_score(risk_factors)
    payload.risk_score = risk_score
    payload.risk_factors = risk_factors
    await cache.set_json(cache_key, payload.model_dump(), ttl=settings.cache_ttl_seconds)
    # Backfill thumbnail on bot-added LeadItem rows that landed with
    # null thumbnail (pre-fix scheduler events). Best-effort: failure
    # logged but the response still returns 200.
    await _backfill_lead_thumbnail(
        session_factory,
        telegram_user_id=telegram_user.user_id,
        first_name=telegram_user.first_name,
        ad_id=ad_id,
        thumbnail=(payload.images[0] if payload.images else None),
    )
    return payload
