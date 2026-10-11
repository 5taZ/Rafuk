from __future__ import annotations

from collections import defaultdict

from fastapi import APIRouter, Depends

from api.config import Settings
from api.dependencies import get_cache, get_currency_service, get_settings_dependency
from api.schemas import GeographyRegionPoint, GeographyResponse
from api.services.aggregator import (
    DATASET_CURRENCY,
    ad_price_byn,
    compute_price_stats,
    is_placeholder_price,
    response_cache_key,
)
from api.services.cache import CacheBackend
from api.services.currency_service import CurrencyService
from api.services.kufar_client import KufarClient
from api.services.market_signals import ad_region_id, region_label
from api.services.query_pipeline import load_query_dataset
from api.validators import DEFAULT_CURRENCY, CurrencyCode, SearchQuery

router = APIRouter(tags=["analytics"])

MAX_REGIONS = 8


@router.get("/geography", response_model=GeographyResponse)
async def get_geography(
    query: SearchQuery,
    currency: CurrencyCode = DEFAULT_CURRENCY,
    strict_search: bool = False,
    settings: Settings = Depends(get_settings_dependency),
    cache: CacheBackend = Depends(get_cache),
    currency_service: CurrencyService = Depends(get_currency_service),
) -> GeographyResponse:
    cache_key = response_cache_key("geography", query, strict_search, currency)
    cached = await cache.get_json(cache_key)
    if cached:
        return GeographyResponse(**{**cached, "query": query})

    dataset = await load_query_dataset(
        query=query,
        currency=DATASET_CURRENCY,
        strict_search=strict_search,
        settings=settings,
        client_factory=KufarClient,
    )
    # Placeholders are decided once, by the market-wide cutoff, before grouping: a region
    # with a few lots must not fall back to a median made of "1 BYN" prices.
    market_stats = dataset.price_stats
    grouped: dict[int, list[tuple[dict, float]]] = defaultdict(list)
    for ad in dataset.ads:
        region_id = ad_region_id(ad)
        price_byn = ad_price_byn(ad)
        if region_id is None or price_byn is None:
            continue
        if is_placeholder_price(price_byn, market_stats):
            continue
        grouped[region_id].append((ad, price_byn))

    rates_payload = await currency_service.get_rates()
    rates = rates_payload["rates"]
    # Shares are of the lots with a known region, so they add up to 100% across regions;
    # total_analyzed stays the market-wide count (lots without a region included).
    located_total = sum(len(items) for items in grouped.values())
    regions: list[GeographyRegionPoint] = []

    ordered = sorted(grouped.items(), key=lambda item: (-len(item[1]), item[0]))
    for region_id, items in ordered[:MAX_REGIONS]:
        stats = compute_price_stats([price for _, price in items], drop_placeholders=False)
        label = next(
            (name for ad, _ in items if (name := region_label(ad))),
            f"Регион {region_id}",
        )
        regions.append(
            GeographyRegionPoint(
                region_id=region_id,
                region_name=label,
                count=stats.count,
                share_percent=round(stats.count / located_total * 100.0, 1),
                mean=currency_service.convert_from_byn(stats.mean, currency, rates),
                median=currency_service.convert_from_byn(stats.median, currency, rates),
                min=currency_service.convert_from_byn(stats.min, currency, rates),
                max=currency_service.convert_from_byn(stats.max, currency, rates),
            )
        )

    payload = GeographyResponse(
        query=query,
        currency=currency,
        total_analyzed=market_stats.count,
        regions=regions,
    )
    await cache.set_json(cache_key, payload.model_dump(), ttl=settings.cache_ttl_seconds)
    return payload
