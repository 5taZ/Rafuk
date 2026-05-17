from __future__ import annotations

import logging
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, Request

from api.config import Settings
from api.dependencies import (
    get_cache,
    get_currency_service,
    get_kufar_client,
    get_settings_dependency,
    get_telegram_user,
)
from api.limiter import limiter
from api.schemas import ListingsResponse
from api.services.aggregator import (
    compute_category_price_stats,
    filter_deal_ads,
    get_param,
    normalize_price_byn,
    precompute_cluster_stats,
    sort_listings,
)
from api.services.cache import CacheBackend, digest_cache_key
from api.services.currency_service import CurrencyService
from api.services.deal_workflow import compute_liquidity_insight
from api.services.kufar_client import KufarClient
from api.services.kufar_filters import build_kufar_search_filters
from api.services.listing_mapper import build_listing_item, compute_listing_sort_key
from api.services.market_signals import area_label, region_label
from api.services.query_pipeline import load_query_dataset_context_with_fallback
from api.services.reseller_tools import analyze_query_text
from api.validators import MAX_QUERY_LENGTH

logger = logging.getLogger(__name__)

router = APIRouter(tags=["analytics"])


# Server-side cap on a single page. Even at 200 cards the frontend
# struggles on slow connections because every card pulls a thumbnail
# through the image proxy. The default `limit` is intentionally lower
# (50) so the initial paint shows up quickly and subsequent pages
# come in as the user scrolls.
_MAX_LISTINGS_PAGE = 200
_DEFAULT_LISTINGS_PAGE = 50


def _listings_cache_key(
    *,
    query: str,
    sort: str,
    currency: str,
    discount_percent: float,
    effective_from: float,
    effective_to: float | None,
    strict_search: bool,
    category: int | None,
    reference_context: str,
    min_price: float | None,
    max_price: float | None,
    condition: str | None,
    seller_type: str | None,
    region_name: str | None,
    limit: int,
    offset: int,
) -> str:
    return digest_cache_key(
        "listings",
        {
            "query": query,
            "sort": sort,
            "currency": currency,
            "discount_percent": discount_percent,
            "effective_from": effective_from,
            "effective_to": effective_to,
            "strict_search": strict_search,
            "category": category,
            "reference_context": reference_context,
            "min_price": min_price,
            "max_price": max_price,
            "condition": condition,
            "seller_type": seller_type,
            "region_name": region_name,
            "limit": limit,
            "offset": offset,
            "total_semantics": 4,
        },
    )


def _normalized_filter_text(value: str | None) -> str:
    return " ".join(str(value or "").casefold().split())


def _matches_condition(ad: dict[str, Any], condition: str | None) -> bool:
    if condition is None:
        return True
    normalized = _normalized_filter_text(get_param(ad, "condition"))
    allowed = {
        "new": {"new", "новый", "2"},
        "used": {"used", "б/у", "бу", "1"},
    }.get(condition)
    return normalized in allowed if allowed else True


def _matches_seller_type(ad: dict[str, Any], seller_type: str | None) -> bool:
    if seller_type is None:
        return True
    raw = _normalized_filter_text(get_param(ad, "seller_type"))
    is_shop = bool(ad.get("company_ad")) or raw in {"магазин", "shop"}
    return is_shop if seller_type == "shop" else not is_shop


def _matches_price(ad: dict[str, Any], min_price: float | None, max_price: float | None) -> bool:
    if min_price is None and max_price is None:
        return True
    price = normalize_price_byn(ad.get("price_byn"), ad)
    if price is None:
        return False
    if min_price is not None and price < min_price:
        return False
    return not (max_price is not None and price > max_price)


def _matches_region(ad: dict[str, Any], region_name: str | None) -> bool:
    normalized = _normalized_filter_text(region_name)
    if not normalized:
        return True
    return normalized in {
        _normalized_filter_text(region_label(ad)),
        _normalized_filter_text(area_label(ad)),
    }


def _filter_visible_ads(
    ads: list[dict[str, Any]],
    *,
    min_price: float | None,
    max_price: float | None,
    condition: str | None,
    seller_type: str | None,
    region_name: str | None,
) -> list[dict[str, Any]]:
    if (
        min_price is None
        and max_price is None
        and condition is None
        and seller_type is None
        and not _normalized_filter_text(region_name)
    ):
        return ads
    return [
        ad for ad in ads
        if _matches_price(ad, min_price, max_price)
        and _matches_condition(ad, condition)
        and _matches_seller_type(ad, seller_type)
        and _matches_region(ad, region_name)
    ]


@router.get("/listings", response_model=ListingsResponse)
@limiter.limit("60/minute")
async def get_listings(
    request: Request,
    query: str = Query(..., min_length=1, max_length=MAX_QUERY_LENGTH, description="Search query"),
    sort: Literal[
        "newest", "cheap", "price_asc", "price_desc", "near_median", "deal_score",
    ] = "newest",
    currency: Literal["BYN", "USD", "EUR", "RUB"] = "BYN",
    strict_search: bool = True,
    discount_percent: float = 10.0,
    discount_from_percent: float | None = None,
    discount_to_percent: float | None = None,
    category: int | None = None,
    reference_context: Literal["current", "base_query"] = "current",
    min_price: float | None = Query(default=None, ge=0, le=9_999_999_999.99),
    max_price: float | None = Query(default=None, ge=0, le=9_999_999_999.99),
    condition: Literal["new", "used"] | None = None,
    seller_type: Literal["private", "shop"] | None = None,
    region_name: str | None = Query(default=None, max_length=128),
    force_refresh: bool = False,
    limit: int = Query(default=_DEFAULT_LISTINGS_PAGE, ge=1, le=_MAX_LISTINGS_PAGE),
    offset: int = Query(default=0, ge=0, le=_MAX_LISTINGS_PAGE * 10),
    settings: Settings = Depends(get_settings_dependency),
    cache: CacheBackend = Depends(get_cache),
    currency_service: CurrencyService = Depends(get_currency_service),
    kufar_client: KufarClient = Depends(get_kufar_client),
    _user=Depends(get_telegram_user),
) -> ListingsResponse:
    effective_from_source = (
        discount_from_percent if discount_from_percent is not None else discount_percent
    )
    effective_from = abs(effective_from_source)
    effective_to = abs(discount_to_percent) if discount_to_percent is not None else None
    if effective_to is not None and effective_to < effective_from:
        effective_from, effective_to = effective_to, effective_from
    if min_price is not None and max_price is not None and max_price < min_price:
        min_price, max_price = max_price, min_price
    normalized_region_name = _normalized_filter_text(region_name) or None
    listing_filters_active = (
        min_price is not None
        or max_price is not None
        or condition is not None
        or seller_type is not None
        or normalized_region_name is not None
    )
    upstream_filter_kwargs, unsupported_upstream_filters = build_kufar_search_filters(
        min_price=min_price,
        max_price=max_price,
        condition=condition,
        seller_type=seller_type,
        region_name=normalized_region_name,
    )
    allow_low_result_fallback = not listing_filters_active and category is None
    use_upstream_filter_total = (
        listing_filters_active and not strict_search and not unsupported_upstream_filters
    )

    fallback_used = False
    cache_key = _listings_cache_key(
        query=query,
        sort=sort,
        currency=currency,
        discount_percent=discount_percent,
        effective_from=effective_from,
        effective_to=effective_to,
        strict_search=strict_search,
        category=category,
        reference_context=reference_context,
        min_price=min_price,
        max_price=max_price,
        condition=condition,
        seller_type=seller_type,
        region_name=normalized_region_name,
        limit=limit,
        offset=offset,
    )
    cached = await cache.get_json(cache_key) if not force_refresh else None
    if cached:
        return ListingsResponse(**cached)

    fb = await load_query_dataset_context_with_fallback(
        query=query,
        currency=currency,
        strict_search=strict_search,
        settings=settings,
        client=kufar_client,
        reference_context=reference_context,
        category=category,
        search_kwargs=upstream_filter_kwargs or None,
        allow_low_result_fallback=allow_low_result_fallback,
        cache=cache,
        force_refresh=force_refresh,
    )
    context = fb.context
    fallback_used = fb.fallback_used
    visible_dataset = context.visible
    reference_dataset = context.reference
    filtered_visible_ads = _filter_visible_ads(
        visible_dataset.ads,
        min_price=min_price,
        max_price=max_price,
        condition=condition,
        seller_type=seller_type,
        region_name=normalized_region_name,
    )
    median_byn = visible_dataset.price_stats.median
    # Build the category reference table.
    # Default is the unfiltered broad query — that's the same data the
    # broad view uses, so the per-ad delta % stays stable when the user
    # toggles the category chip on/off (test_listings_…_stable_in_…).
    # For refined queries where the first ~5000 ads are dominated by
    # one bucket (e.g. "Audi Q7 4L 2015": 99% "Запчасти"), the broad
    # extraction won't have enough samples for the category we're
    # filtering on — that used to fall back to the broad market median
    # of parts (≈100 BYN) and produced a bogus "+78539% выше рынка"
    # badge on a 78 600 BYN car. When that happens, fall through to the
    # cat-scoped visible_dataset's stats (which always has every ad in
    # the chosen category).
    category_price_stats = compute_category_price_stats(reference_dataset.ads)
    if category is not None:
        broad_cat_stats = category_price_stats.get(category)
        if broad_cat_stats is None or broad_cat_stats.count < 3:
            category_price_stats = {
                **category_price_stats,
                category: visible_dataset.price_stats,
            }
    liquidity = compute_liquidity_insight(visible_dataset.ads, visible_dataset.price_stats)
    try:
        rates_payload = await currency_service.get_rates()
    except Exception:
        rates_payload = {"rates": {"BYN": 1.0}}
    rates = rates_payload["rates"]
    insights = analyze_query_text(query)

    deal_ads = (
        filter_deal_ads(
            filtered_visible_ads,
            reference_dataset.price_stats.median,
            effective_from,
            effective_to,
            market_stats=reference_dataset.price_stats,
            category_price_stats=category_price_stats,
        )
        if sort == "cheap"
        else filtered_visible_ads
    )
    effective_sort = "newest" if sort == "deal_score" else sort
    sorted_ads = sort_listings(
        deal_ads,
        effective_sort,
        median_byn,
        market_stats=reference_dataset.price_stats,
        category_price_stats=category_price_stats,
    )
    capped_ads = sorted_ads[:_MAX_LISTINGS_PAGE]
    cluster_cache = precompute_cluster_stats(capped_ads, query=query)
    # Build ListingItem only for the slice the client is going to
    # actually render — saves several ms per skipped ad on big
    # result sets, since build_listing_item normalises params,
    # computes deal_score, fetches flip_estimates, etc.
    #
    # For computed sorts, rank raw ads with the same deal-score inputs
    # before slicing so off-page cards don't pay the full ListingItem cost.
    page_capped_total = min(len(sorted_ads), _MAX_LISTINGS_PAGE)

    # BE-H7: build_listing_item touches a lot of subsystems
    # (price normalisation, category lookup, deal_score, flip
    # estimates, anomaly flags, image URL building). Any one of
    # those raising on a malformed Kufar payload would explode the
    # entire response — even though every other ad on the page is
    # fine. Wrap the per-ad call so a single bad ad is logged and
    # skipped instead of taking the whole listing endpoint down.
    def _safe_build(ad: dict[str, Any]):
        try:
            return build_listing_item(
                ad,
                query=query,
                currency=currency,
                rates=rates,
                currency_service=currency_service,
                median_byn=median_byn,
                market_stats=reference_dataset.price_stats,
                category_price_stats=category_price_stats,
                liquidity=liquidity,
                cluster_cache=cluster_cache,
            )
        except Exception as exc:  # noqa: BLE001 — graceful per-ad degrade
            logger.warning(
                "listings: failed to build item for ad_id=%s (%s: %s); skipping",
                ad.get("ad_id"), type(exc).__name__, exc,
                exc_info=True,
            )
            return None

    if sort in {"cheap", "deal_score"}:
        ranked_ads: list[tuple[tuple[float, float, str], dict[str, Any]]] = []
        for ad in capped_ads:
            try:
                ranked_ads.append((
                    compute_listing_sort_key(
                        ad,
                        query=query,
                        market_stats=reference_dataset.price_stats,
                        category_price_stats=category_price_stats,
                        cluster_cache=cluster_cache,
                    ),
                    ad,
                ))
            except Exception as exc:  # noqa: BLE001 — graceful per-ad degrade
                logger.warning(
                    "listings: failed to rank item for ad_id=%s (%s: %s); skipping",
                    ad.get("ad_id"), type(exc).__name__, exc,
                    exc_info=True,
                )
        ranked_ads.sort(key=lambda item: item[0])
        built = (
            _safe_build(ad)
            for _, ad in ranked_ads[offset : offset + limit]
        )
        listings = [item for item in built if item is not None]
    else:
        # newest / nearest_to_median etc. — sort_listings already
        # ordered the underlying ads, so we can slice before
        # building items.
        page_slice = sorted_ads[offset : offset + limit]
        # Don't extend past the cap.
        if offset >= _MAX_LISTINGS_PAGE:
            page_slice = []
        elif offset + limit > _MAX_LISTINGS_PAGE:
            page_slice = sorted_ads[offset:_MAX_LISTINGS_PAGE]
        built = (_safe_build(ad) for ad in page_slice)
        listings = [item for item in built if item is not None]

    # `total` is what the pill above the cards shows.
    #  - Cheap sort: show the post-discount count, not Kufar's raw
    #    broad total, otherwise the "Выгодные" tab repeats the "Новые"
    #    badge even though it renders a much smaller filtered set.
    #  - Non-strict, no filters: use Kufar's raw `total` so the pill
    #    matches kufar.by's header/sidebar.
    # When strict_search is on, Kufar's broad total (visible_dataset.total_results)
    # overcounts — the user sees only the strict-matched subset, so the badge
    # must reflect the rendered card count, not the pre-strict API total.
    if sort == "cheap":
        filtered_total = len(deal_ads)
    elif listing_filters_active:
        filtered_total = (
            visible_dataset.total_results if use_upstream_filter_total else len(sorted_ads)
        )
    elif strict_search:
        filtered_total = len(visible_dataset.ads)
    else:
        filtered_total = visible_dataset.total_results
    # `has_more` mirrors the obvious "is there a next page?" question
    # the frontend asks before triggering its IntersectionObserver.
    # We compare against the page-cap so we don't promise pages that
    # the server will refuse to serve anyway.
    served_so_far = offset + len(listings)
    has_more = served_so_far < min(filtered_total, page_capped_total)
    served_cap = min(filtered_total, page_capped_total)

    payload = ListingsResponse(
        query=query,
        currency=currency,
        normalized_query=insights.normalized_query,
        config_summary=insights.config_summary,
        storage_gb=insights.storage_gb,
        ram_gb=insights.ram_gb,
        sort=sort,
        total=filtered_total,
        returned=len(listings),
        offset=offset,
        limit=limit,
        has_more=has_more,
        discount_percent=effective_from if sort == "cheap" else None,
        discount_from_percent=effective_from if sort == "cheap" else None,
        discount_to_percent=effective_to if sort == "cheap" else None,
        fallback_used=fallback_used,
        result_cap=settings.kufar_max_ads_per_query,
        dataset_count=len(visible_dataset.ads),
        served_cap=served_cap,
        is_limited=filtered_total > served_cap,
        listings=listings,
    )
    await cache.set_json(
        cache_key,
        payload.model_dump(),
        ttl=settings.cache_ttl_seconds,
    )
    return payload
