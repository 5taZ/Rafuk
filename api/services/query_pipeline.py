from __future__ import annotations

import logging
import statistics
from dataclasses import dataclass, field
from typing import Any, Protocol

from api.config import Settings
from api.services.aggregator import (
    MAX_PRICE_BYN,
    PriceStats,
    apply_search_mode,
    compute_price_stats,
    extract_prices,
    get_category_id,
)
from api.services.currency_service import CurrencyService

logger = logging.getLogger(__name__)

PRICE_STATS_FIELDS = ("mean", "median", "q1", "q3", "min", "max")

# Condition-based API tasks — seller_type filtering is done client-side
# because Kufar API no longer accepts the "otype" parameter.
_API_CONDITION_TASKS = (
    ("new", {"condition": "new"}),
    ("used", {"condition": "used"}),
)


# Seller type classification: company_ad==True means shop, else private
def _is_shop_ad(ad: dict[str, Any]) -> bool:
    return bool(ad.get("company_ad"))


SEGMENT_NAMES = ("new_private", "new_shop", "used_private", "used_shop")


class SupportsSearchAllAds(Protocol):
    async def search_all_ads(self, **kwargs: Any) -> dict[str, Any]: ...
    async def aclose(self) -> None: ...


class SupportsParallelSearch(Protocol):
    async def __call__(
        self,
        client: SupportsSearchAllAds,
        tasks: list[dict[str, Any]],
        settings: Settings,
    ) -> list[dict[str, Any]]: ...


def _normalize_response_ads(response: dict[str, Any]) -> dict[str, Any]:
    ads = response.get("ads")
    if not isinstance(ads, list) or not ads:
        return response

    has_minor_currency_pair = any(ad.get("price_usd") not in (None, "", 0, 0.0) for ad in ads)
    if has_minor_currency_pair:
        return response

    raw_prices: list[float] = []
    for ad in ads:
        try:
            raw_price = float(ad.get("price_byn"))
        except (TypeError, ValueError):
            continue
        if raw_price > 0:
            raw_prices.append(raw_price)

    if not raw_prices:
        return response

    raw_median = statistics.median(raw_prices)
    likely_direct_byn = max(raw_prices) <= MAX_PRICE_BYN
    if not likely_direct_byn:
        logger.warning(
            "Price normalization heuristic triggered: raw_median=%.0f, "
            "max_raw=%.0f — leaving price_byn values unchanged",
            raw_median,
            max(raw_prices),
        )
        return response

    normalized_ads: list[dict[str, Any]] = []
    for ad in ads:
        cloned = dict(ad)
        try:
            raw_price = float(cloned.get("price_byn"))
        except (TypeError, ValueError):
            normalized_ads.append(cloned)
            continue
        if raw_price > 0:
            cloned["price_byn"] = int(round(raw_price * 100))
        normalized_ads.append(cloned)

    return {**response, "ads": normalized_ads}


@dataclass(slots=True)
class QueryDataset:
    query: str
    currency: str
    strict_search: bool
    response: dict[str, Any]
    ads: list[dict[str, Any]]
    _prices_byn: list[float] | None = field(default=None, init=False, repr=False)
    _price_stats: PriceStats | None = field(default=None, init=False, repr=False)

    @property
    def total_results(self) -> int:
        """Total results from Kufar API (before strict filtering)."""
        api_total = self.response.get("total")
        if isinstance(api_total, int) and api_total > 0:
            return api_total
        return len(self.ads)

    @property
    def prices_byn(self) -> list[float]:
        if self._prices_byn is None:
            self._prices_byn = extract_prices(self.ads)
        return self._prices_byn

    @property
    def price_stats(self) -> PriceStats:
        if self._price_stats is None:
            self._price_stats = compute_price_stats(self.prices_byn)
        return self._price_stats


@dataclass(slots=True)
class QueryDatasetContext:
    visible: QueryDataset
    reference: QueryDataset


async def load_query_dataset(
    *,
    query: str,
    currency: str,
    strict_search: bool,
    settings: Settings,
    client_factory: type[SupportsSearchAllAds] | None = None,
    search_kwargs: dict[str, Any] | None = None,
    category: int | None = None,
    client: SupportsSearchAllAds | None = None,
) -> QueryDataset:
    owns_client = client is None
    if client is None:
        if client_factory is None:
            raise ValueError("Either client or client_factory must be provided")
        client = client_factory(settings)
    try:
        effective_kwargs = dict(search_kwargs or {})
        if category is not None:
            effective_kwargs["category"] = category
        response = await client.search_all_ads(
            query=query,
            currency=currency,
            **effective_kwargs,
        )
        response = _normalize_response_ads(response)
    finally:
        if owns_client:
            await client.aclose()

    ads = apply_search_mode(response.get("ads", []), query, strict_search)
    return QueryDataset(
        query=query,
        currency=currency,
        strict_search=strict_search,
        response=response,
        ads=ads,
    )


async def load_query_dataset_context(
    *,
    query: str,
    currency: str,
    strict_search: bool,
    settings: Settings,
    client_factory: type[SupportsSearchAllAds] | None = None,
    client: SupportsSearchAllAds | None = None,
    reference_context: str = "current",
    category: int | None = None,
) -> QueryDatasetContext:
    """Build the visible/reference dataset pair for a query.

    Important invariant: when a category filter is active, the visible
    listings are filtered *client-side* from the unfiltered Kufar
    response. We do NOT re-query Kufar with `category=` because the
    Kufar API silently widens its match rules under category-scoped
    queries — that produces, for example, 11 "Audi Q7 4L" ads under
    "Легковые авто" while the unfiltered dataset has only 5 ads in
    that category. By filtering locally we keep the category chip
    count and the listings count in lockstep.
    """
    owns_client = client is None and client_factory is not None
    if client is None and client_factory is not None:
        client = client_factory(settings)  # type: ignore[misc]

    try:
        reference_dataset = await load_query_dataset(
            query=query,
            currency=currency,
            strict_search=strict_search,
            settings=settings,
            client=client,
        )
    finally:
        if owns_client and client is not None:
            await client.aclose()

    if category is None:
        return QueryDatasetContext(visible=reference_dataset, reference=reference_dataset)

    # Local filter: keep only ads whose category_id matches the chip
    # the user picked. Mirror the same numbers the chip badge shows.
    filtered_ads = [ad for ad in reference_dataset.ads if get_category_id(ad) == category]
    visible_response = {
        **reference_dataset.response,
        "ads": filtered_ads,
        "total": len(filtered_ads),
    }
    visible_dataset = QueryDataset(
        query=query,
        currency=currency,
        strict_search=strict_search,
        response=visible_response,
        ads=filtered_ads,
    )

    if reference_context == "base_query":
        return QueryDatasetContext(visible=visible_dataset, reference=reference_dataset)
    # `current` keeps reference == visible (price comparisons happen
    # only inside the chosen category).
    return QueryDatasetContext(visible=visible_dataset, reference=visible_dataset)


async def load_segment_datasets(
    *,
    query: str,
    currency: str,
    strict_search: bool,
    settings: Settings,
    client_factory: type[SupportsSearchAllAds] | None = None,
    client: SupportsSearchAllAds | None = None,
    parallel_search: SupportsParallelSearch,
    category: int | None = None,
) -> dict[str, QueryDataset]:
    owns_client = client is None and client_factory is not None
    if client is None and client_factory is not None:
        client = client_factory(settings)
    # Fetch by condition only (seller type filtered client-side)
    tasks = [
        {
            "query": query,
            "currency": currency,
            **task_params,
        }
        for _, task_params in _API_CONDITION_TASKS
    ]
    if category is not None:
        for task in tasks:
            task["category"] = category

    try:
        responses = await parallel_search(client, tasks, settings)
        responses = [_normalize_response_ads(response) for response in responses]
    finally:
        if owns_client and client is not None:
            await client.aclose()

    datasets: dict[str, QueryDataset] = {}
    for (cond_name, _), response in zip(_API_CONDITION_TASKS, responses, strict=True):
        all_ads = apply_search_mode(response.get("ads", []), query, strict_search)
        private_ads = [ad for ad in all_ads if not _is_shop_ad(ad)]
        shop_ads = [ad for ad in all_ads if _is_shop_ad(ad)]

        for suffix, ads in (("_private", private_ads), ("_shop", shop_ads)):
            segment_name = f"{cond_name}{suffix}"
            datasets[segment_name] = QueryDataset(
                query=query,
                currency=currency,
                strict_search=strict_search,
                response=response,
                ads=ads,
            )
    return datasets


def convert_price_stats(
    stats: PriceStats | dict[str, float | int],
    *,
    currency: str,
    rates: dict[str, float],
    currency_service: CurrencyService,
) -> dict[str, float | int]:
    values = stats.model_dump() if isinstance(stats, PriceStats) else dict(stats)
    return {
        **values,
        **{
            field: currency_service.convert_from_byn(float(values[field]), currency, rates)
            for field in PRICE_STATS_FIELDS
        },
    }
