from __future__ import annotations

from collections import defaultdict

from fastapi import APIRouter, Depends, Query

from api.config import Settings
from api.dependencies import get_cache, get_currency_service, get_settings_dependency
from api.schemas import GeographyRegionPoint, GeographyResponse
from api.services.aggregator import compute_price_stats, extract_prices
from api.services.cache import CacheBackend
from api.services.currency_service import CurrencyService
from api.services.kufar_client import KufarClient
from api.services.market_signals import region_label
from api.services.query_pipeline import load_query_dataset
from api.validators import MAX_QUERY_LENGTH

router = APIRouter(tags=["analytics"])


@router.get("/geography", response_model=GeographyResponse)
async def get_geography(
    query: str = Query(..., min_length=1, max_length=MAX_QUERY_LENGTH, description="Search query"),
    currency: str = "BYN",
    strict_search: bool = False,
    settings: Settings = Depends(get_settings_dependency),
    cache: CacheBackend = Depends(get_cache),
    currency_service: CurrencyService = Depends(get_currency_service),
) -> GeographyResponse:
    cache_key = f"geography:{query}:{currency}:{strict_search}"
    cached = await cache.get_json(cache_key)
    if cached:
        return GeographyResponse(**cached)

    dataset = await load_query_dataset(
        query=query,
        currency=currency,
        strict_search=strict_search,
        settings=settings,
        client_factory=KufarClient,
    )
    grouped: dict[int, list[dict]] = defaultdict(list)
    for ad in dataset.ads:
        region_id = ad.get("region_id")
        if not isinstance(region_id, int) or region_id <= 0:
            continue
        grouped[region_id].append(ad)

    rates_payload = await currency_service.get_rates()
    rates = rates_payload["rates"]
    total_analyzed = max(1, dataset.price_stats.count)
    regions: list[GeographyRegionPoint] = []

    for region_id, ads in sorted(grouped.items(), key=lambda item: len(item[1]), reverse=True):
        stats = compute_price_stats(extract_prices(ads))
        if stats.count == 0:
            continue
        label = next((region_label(ad) for ad in ads if region_label(ad)), f"Регион {region_id}")
        regions.append(
            GeographyRegionPoint(
                region_id=region_id,
                region_name=label,
                count=stats.count,
                share_percent=round(stats.count / total_analyzed * 100.0, 1),
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
