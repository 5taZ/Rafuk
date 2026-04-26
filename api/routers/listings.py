from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Query, Request

from api.config import Settings
from api.dependencies import (
    get_cache,
    get_currency_service,
    get_kufar_client,
    get_settings_dependency,
)
from api.limiter import limiter
from api.schemas import ListingsResponse
from api.services.aggregator import (
    compute_category_price_stats,
    filter_deal_ads,
    sort_listings,
)
from api.services.cache import CacheBackend
from api.services.currency_service import CurrencyService
from api.services.deal_workflow import compute_liquidity_insight
from api.services.kufar_client import KufarClient
from api.services.listing_mapper import build_listing_item
from api.services.query_pipeline import load_query_dataset_context
from api.services.reseller_tools import analyze_query_text
from api.validators import MAX_QUERY_LENGTH

router = APIRouter(tags=["analytics"])


@router.get("/listings", response_model=ListingsResponse)
@limiter.limit("30/minute")
async def get_listings(
    request: Request,
    query: str = Query(..., min_length=1, max_length=MAX_QUERY_LENGTH, description="Search query"),
    sort: str = "newest",
    currency: str = "BYN",
    strict_search: bool = False,
    discount_percent: float = 10.0,
    discount_from_percent: float | None = None,
    discount_to_percent: float | None = None,
    category: int | None = None,
    reference_context: Literal["current", "base_query"] = "current",
    settings: Settings = Depends(get_settings_dependency),
    cache: CacheBackend = Depends(get_cache),
    currency_service: CurrencyService = Depends(get_currency_service),
    kufar_client: KufarClient = Depends(get_kufar_client),
) -> ListingsResponse:
    effective_from_source = (
        discount_from_percent if discount_from_percent is not None else discount_percent
    )
    effective_from = abs(effective_from_source)
    effective_to = abs(discount_to_percent) if discount_to_percent is not None else None
    if effective_to is not None and effective_to < effective_from:
        effective_from, effective_to = effective_to, effective_from

    cache_key = (
        f"listings:{query}:{sort}:{currency}:{discount_percent}:"
        f"{effective_from}:{effective_to}:{strict_search}:{category}:{reference_context}"
    )
    cached = await cache.get_json(cache_key)
    if cached:
        return ListingsResponse(**cached)

    context = await load_query_dataset_context(
        query=query,
        currency=currency,
        strict_search=strict_search,
        settings=settings,
        client=kufar_client,
        reference_context=reference_context,
        category=category,
    )
    visible_dataset = context.visible
    reference_dataset = context.reference
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
    rates_payload = await currency_service.get_rates()
    rates = rates_payload["rates"]
    insights = analyze_query_text(query)

    deal_ads = (
        filter_deal_ads(
            visible_dataset.ads,
            reference_dataset.price_stats.median,
            effective_from,
            effective_to,
            market_stats=reference_dataset.price_stats,
            category_price_stats=category_price_stats,
        )
        if sort == "cheap"
        else visible_dataset.ads
    )
    effective_sort = "newest" if sort == "deal_score" else sort
    sorted_ads = sort_listings(
        deal_ads,
        effective_sort,
        median_byn,
        market_stats=reference_dataset.price_stats,
        category_price_stats=category_price_stats,
    )
    listings = []
    for ad in sorted_ads[:200]:
        listings.append(
            build_listing_item(
                ad,
                query=query,
                currency=currency,
                rates=rates,
                currency_service=currency_service,
                median_byn=median_byn,
                market_stats=reference_dataset.price_stats,
                category_price_stats=category_price_stats,
                liquidity=liquidity,
            )
        )
    if sort in {"cheap", "deal_score"}:
        listings.sort(
            key=lambda item: (
                -float(item.deal_score or 0.0),
                float(item.price_vs_median or 0.0),
                item.title,
            )
        )

    # `total` is what the pill above the cards shows.
    #  - Broad query (category=None): use Kufar's raw `total` so the
    #    pill matches kufar.by's header ("Polo → 33 776"), not the
    #    pagination cap.
    #  - Category-scoped query: prefer the precise post-filter count
    #    so refined queries are honest (e.g. cat=2010 + "Audi Q7 4L
    #    2015": Kufar fuzzy total=11, but only 3 ads pass our filter
    #    → pill shows 3, matching the cards). When we hit the
    #    pagination cap (≥200 ads), fall back to Kufar's `total` so
    #    big categories like "Polo + Легковые авто" still display
    #    the real number instead of a capped "200".
    if category is None:
        filtered_total = visible_dataset.total_results
    else:
        filtered_count = len(visible_dataset.ads)
        kufar_total = visible_dataset.total_results
        if filtered_count >= 200 and kufar_total > filtered_count:
            filtered_total = kufar_total
        else:
            filtered_total = filtered_count
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
        discount_percent=effective_from if sort == "cheap" else None,
        discount_from_percent=effective_from if sort == "cheap" else None,
        discount_to_percent=effective_to if sort == "cheap" else None,
        listings=listings,
    )
    await cache.set_json(
        cache_key,
        payload.model_dump(),
        ttl=settings.cache_ttl_seconds,
    )
    return payload
