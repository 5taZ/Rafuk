from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request

from api.config import Settings
from api.dependencies import (
    get_cache,
    get_currency_service,
    get_kufar_client,
    get_settings_dependency,
)
from api.limiter import limiter
from api.schemas import SegmentsResponse
from api.services.aggregator import PriceStats, compute_segments
from api.services.cache import CacheBackend
from api.services.currency_service import CurrencyService
from api.services.kufar_client import KufarClient
from api.services.query_pipeline import convert_price_stats, load_query_dataset
from api.validators import MAX_QUERY_LENGTH

router = APIRouter(tags=["analytics"])


_EMPTY_SEGMENT = {
    "mean": 0.0, "median": 0.0, "q1": 0.0, "q3": 0.0,
    "min": 0.0, "max": 0.0, "count": 0,
}


@router.get("/segments", response_model=SegmentsResponse)
@limiter.limit("30/minute")
async def get_segments(
    request: Request,
    query: str = Query(..., min_length=1, max_length=MAX_QUERY_LENGTH, description="Search query"),
    currency: str = "BYN",
    strict_search: bool = False,
    category: int | None = None,
    settings: Settings = Depends(get_settings_dependency),
    cache: CacheBackend = Depends(get_cache),
    currency_service: CurrencyService = Depends(get_currency_service),
    kufar_client: KufarClient = Depends(get_kufar_client),
) -> SegmentsResponse:
    cache_key = f"segments:{query}:{currency}:{strict_search}:{category}"
    cached = await cache.get_json(cache_key)
    if cached:
        return SegmentsResponse(**cached)

    # Compute the 4 segment baskets directly from the singleflighted
    # dataset instead of issuing 2 extra Kufar calls (cnd=new and
    # cnd=used). Both the condition value (numeric code in
    # ad_parameters) and the seller type (top-level company_ad) are
    # already on every ad — no extra API round-trips needed.
    #
    # We lose the per-condition 200-ad sample size in exchange for
    # the dataset's combined 200-ad sample. For the typical query
    # that still gives 30-150 ads per non-empty segment, which is
    # more than enough for stable medians. The wall-clock saving on
    # cold cache is ~1.5-2 s.
    dataset = await load_query_dataset(
        query=query,
        currency=currency,
        strict_search=strict_search,
        settings=settings,
        client=kufar_client,
        category=category,
        cache=cache,
    )
    raw_segments = compute_segments(dataset.ads)

    rates_payload = await currency_service.get_rates()
    rates = rates_payload["rates"]

    def _segment_payload(name: str) -> dict:
        seg = raw_segments.get(name)
        if not seg:
            return dict(_EMPTY_SEGMENT)
        stats = PriceStats(**seg)
        return convert_price_stats(
            stats,
            currency=currency,
            rates=rates,
            currency_service=currency_service,
        )

    payload = SegmentsResponse(
        query=query,
        currency=currency,
        new_private=_segment_payload("new_private"),
        new_shop=_segment_payload("new_shop"),
        used_private=_segment_payload("used_private"),
        used_shop=_segment_payload("used_shop"),
    )
    await cache.set_json(cache_key, payload.model_dump(), ttl=settings.cache_ttl_seconds)
    return payload
