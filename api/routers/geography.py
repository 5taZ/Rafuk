from __future__ import annotations

from collections import defaultdict
from typing import Literal

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
from api.schemas import GeographyRegionPoint, GeographyResponse
from api.services.aggregator import compute_price_stats, extract_prices
from api.services.cache import CacheBackend, digest_cache_key
from api.services.currency_service import CurrencyService
from api.services.kufar_client import KufarClient
from api.services.market_signals import region_label
from api.services.query_pipeline import load_query_dataset_with_fallback
from api.validators import MAX_QUERY_LENGTH

router = APIRouter(tags=["analytics"])


@router.get("/geography", response_model=GeographyResponse)
@limiter.limit("30/minute")
async def get_geography(
    request: Request,
    query: str = Query(..., min_length=1, max_length=MAX_QUERY_LENGTH, description="Search query"),
    currency: Literal["BYN", "USD", "EUR", "RUB"] = "BYN",
    strict_search: bool = True,
    category: int | None = None,
    force_refresh: bool = False,
    settings: Settings = Depends(get_settings_dependency),
    cache: CacheBackend = Depends(get_cache),
    currency_service: CurrencyService = Depends(get_currency_service),
    kufar_client: KufarClient = Depends(get_kufar_client),
    _user=Depends(get_telegram_user),
) -> GeographyResponse:
    cache_key = digest_cache_key(
        "geography",
        {
            "q": query,
            "cur": currency,
            "strict": strict_search,
            "cat": category,
        },
    )
    cached = await cache.get_json(cache_key) if not force_refresh else None
    if cached:
        return GeographyResponse(**cached)

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
    dataset = fb.dataset
    grouped: dict[int, list[dict]] = defaultdict(list)
    for ad in dataset.ads:
        region_id = ad.get("region_id")
        if not isinstance(region_id, int) or region_id <= 0:
            continue
        grouped[region_id].append(ad)

    # BE-MEDIUM (issues §2.2): NBRB upstream can fail; fall back to
    # BYN-only rates rather than 500 the entire endpoint.
    try:
        rates_payload = await currency_service.get_rates()
    except Exception:  # noqa: BLE001 — fallback to identity rates
        rates_payload = {"rates": {"BYN": 1.0}}
    rates = rates_payload["rates"]
    # B-05/D-3: use sum of per-region counts as denominator so share_percent
    # sums to ~100. total_analyzed (post-global-outlier) differs from the sum
    # of per-region counts (post-per-region-outlier + ungrouped ads skipped).
    region_stats_list: list[tuple] = []
    for region_id, ads in sorted(grouped.items(), key=lambda item: len(item[1]), reverse=True):
        stats = compute_price_stats(extract_prices(ads))
        if stats.count == 0:
            continue
        label = next((region_label(ad) for ad in ads if region_label(ad)), f"Регион {region_id}")
        region_stats_list.append((region_id, label, stats))

    total_for_share = max(1, sum(s.count for _, _, s in region_stats_list))
    regions: list[GeographyRegionPoint] = []

    for region_id, label, stats in region_stats_list:
        regions.append(
            GeographyRegionPoint(
                region_id=region_id,
                region_name=label,
                count=stats.count,
                share_percent=round(stats.count / total_for_share * 100.0, 1),
                mean=currency_service.convert_from_byn(stats.mean, currency, rates),
                median=currency_service.convert_from_byn(stats.median, currency, rates),
                min=currency_service.convert_from_byn(stats.min, currency, rates),
                max=currency_service.convert_from_byn(stats.max, currency, rates),
            )
        )

    payload = GeographyResponse(
        query=query,
        currency=currency,
        total_analyzed=dataset.price_stats.count,
        regions=regions[:8],
    )
    await cache.set_json(cache_key, payload.model_dump(), ttl=settings.cache_ttl_seconds)
    return payload
