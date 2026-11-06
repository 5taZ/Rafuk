from __future__ import annotations

import logging
from itertools import chain
from typing import Any, Literal

import httpx
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
from api.schemas import ListingDetailResponse, ListingMarketResponse
from api.services.aggregator import (
    compute_category_price_stats,
    normalize_price_byn,
    precompute_cluster_stats,
)
from api.services.cache import CacheBackend, digest_cache_key
from api.services.currency_service import CurrencyService, get_conversion_rates
from api.services.kufar_client import KufarClient
from api.services.listing_mapper import build_listing_detail
from api.services.market_liquidity import estimate_liquidity
from api.services.query_pipeline import (
    QueryDatasetContext,
    load_query_dataset_context_with_fallback,
)
from api.services.risk_detector import compute_risk_score, detect_risks
from api.services.watchlist_market import (
    MARKET_ESTIMATE_TTL,
    build_market_comparables,
    comparison_from_comparables,
    load_page_comparables,
)
from api.services.workflow_store import ensure_user
from api.validators import MAX_QUERY_LENGTH

logger = logging.getLogger(__name__)

router = APIRouter(tags=["analytics"])


@router.get("/listing-description")
@limiter.limit("30/minute")
async def get_listing_description(
    request: Request,
    ad_id: int = Query(..., ge=1, le=2**63 - 1),
    cache: CacheBackend = Depends(get_cache),
    kufar_client: KufarClient = Depends(get_kufar_client),
    _user=Depends(get_telegram_user),
) -> dict[str, str | None]:
    key = digest_cache_key("listing-description", {"ad": ad_id})
    cached = await cache.get_json(key)
    if cached is not None:
        return cached
    try:
        description = await kufar_client.get_listing_description(ad_id)
    except (httpx.HTTPError, TimeoutError) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Kufar description unavailable",
        ) from exc
    payload = {"description": description}
    await cache.set_json(key, payload, ttl=MARKET_ESTIMATE_TTL)
    return payload


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


async def _load_listing_context(
    ad_id: int, **kwargs: Any,
) -> tuple[dict[str, Any], QueryDatasetContext]:
    context = (await load_query_dataset_context_with_fallback(**kwargs)).context
    ad = next((item for item in context.visible.ads if int(item.get("ad_id", 0)) == ad_id), None)
    if ad is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Listing not found for this query",
        )
    return ad, context


async def _load_listing_market(
    ad: dict[str, Any],
    context: QueryDatasetContext,
    *,
    query: str,
    settings: Settings,
    client: KufarClient,
    cache: CacheBackend,
) -> ListingMarketResponse:
    market = await load_page_comparables(
        [ad], query=query, settings=settings, client=client, cache=cache,
    )
    peers = market.get(int(ad["ad_id"]))
    comparison = None
    if peers is None:
        peers = build_market_comparables(
            ad,
            chain(
                context.visible.response.get("ads", []), context.reference.response.get("ads", []),
            ),
        )
    else:
        comparison = comparison_from_comparables(ad, peers)
    return ListingMarketResponse(
        price_byn=normalize_price_byn(ad.get("price_byn"), ad),
        liquidity=estimate_liquidity(ad, peers),
        market_comparison=(
            comparison if comparison and comparison.delta_percent is not None else None
        ),
    )


@router.get("/listing-market", response_model=ListingMarketResponse)
@limiter.limit("30/minute")
async def get_listing_market(
    request: Request,
    ad_id: int,
    query: str = Query(..., min_length=1, max_length=MAX_QUERY_LENGTH, description="Search query"),
    currency: Literal["BYN", "USD", "EUR", "RUB"] = "BYN",
    strict_search: bool = True,
    category: int | None = None,
    reference_context: Literal["current", "base_query"] = "current",
    settings: Settings = Depends(get_settings_dependency),
    cache: CacheBackend = Depends(get_cache),
    kufar_client: KufarClient = Depends(get_kufar_client),
    _user=Depends(get_telegram_user),
) -> ListingMarketResponse:
    cache_key = digest_cache_key(
        "listing-market",
        {
            "ad": ad_id, "q": query, "cur": currency, "strict": strict_search,
            "cat": category, "ref": reference_context,
        },
    )
    cached = await cache.get_json(cache_key)
    if cached is not None:
        return ListingMarketResponse(**cached)

    ad, context = await _load_listing_context(
        ad_id, query=query, currency=currency, strict_search=strict_search, category=category,
        reference_context=reference_context, settings=settings, client=kufar_client, cache=cache,
    )
    payload = await _load_listing_market(
        ad, context, query=query, settings=settings, client=kufar_client, cache=cache,
    )
    await cache.set_json(
        cache_key, payload.model_dump(), ttl=min(MARKET_ESTIMATE_TTL, settings.cache_ttl_seconds),
    )
    return payload


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
    include_market: bool = True,
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
            "market_comparison": 2,
            "liquidity": 4,
            "include_market": include_market,
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

    ad, context = await _load_listing_context(
        ad_id, query=query, currency=currency, strict_search=strict_search, category=category,
        reference_context=reference_context, settings=settings, client=kufar_client, cache=cache,
    )
    visible_dataset = context.visible
    reference_dataset = context.reference

    median_byn = visible_dataset.price_stats.median
    category_price_stats = compute_category_price_stats(reference_dataset.ads)
    cluster_cache = await precompute_cluster_stats(
        visible_dataset.ads, query=query, target_ids={ad_id},
    )
    # BE-MEDIUM (issues §2.2): NBRB outage shouldn't 500 the detail.
    rates = await get_conversion_rates(currency_service, currency)
    payload = build_listing_detail(
        ad=ad,
        query=query,
        currency=currency,
        rates=rates,
        currency_service=currency_service,
        median_byn=median_byn,
        market_stats=reference_dataset.price_stats,
        category_price_stats=category_price_stats,
        cluster_stats=cluster_cache.get(ad_id),
    )
    # Risk detection — runs after the listing detail is built so we can
    # reuse the market stats already fetched for the query.
    market_stats_dict = {"median": reference_dataset.price_stats.median}
    risk_factors = detect_risks(ad, market_stats=market_stats_dict)
    risk_score = compute_risk_score(risk_factors)
    payload.risk_score = risk_score
    payload.risk_factors = risk_factors
    if include_market:
        market = await _load_listing_market(
            ad, context, query=query, settings=settings, client=kufar_client, cache=cache,
        )
        payload.liquidity = market.liquidity
        payload.market_comparison = market.market_comparison
    await cache.set_json(
        cache_key, payload.model_dump(), ttl=min(MARKET_ESTIMATE_TTL, settings.cache_ttl_seconds),
    )
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
