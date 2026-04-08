from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from api.config import Settings
from api.services.aggregator import (
    PriceStats,
    apply_search_mode,
    compute_price_stats,
    extract_prices,
)
from api.services.currency_service import CurrencyService

PRICE_STATS_FIELDS = ("mean", "median", "q1", "q3", "min", "max")
SEGMENT_TASKS = (
    ("new_private", {"condition": "new", "seller_type": "search_owner"}),
    ("new_shop", {"condition": "new", "seller_type": "search_business"}),
    ("used_private", {"condition": "used", "seller_type": "search_owner"}),
    ("used_shop", {"condition": "used", "seller_type": "search_business"}),
)


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


async def load_query_dataset(
    *,
    query: str,
    currency: str,
    strict_search: bool,
    settings: Settings,
    client_factory: type[SupportsSearchAllAds],
    search_kwargs: dict[str, Any] | None = None,
) -> QueryDataset:
    client = client_factory(settings)
    try:
        response = await client.search_all_ads(
            query=query,
            currency=currency,
            **(search_kwargs or {}),
        )
    finally:
        await client.aclose()

    ads = apply_search_mode(response.get("ads", []), query, strict_search)
    return QueryDataset(
        query=query,
        currency=currency,
        strict_search=strict_search,
        response=response,
        ads=ads,
    )


async def load_segment_datasets(
    *,
    query: str,
    currency: str,
    strict_search: bool,
    settings: Settings,
    client_factory: type[SupportsSearchAllAds],
    parallel_search: SupportsParallelSearch,
) -> dict[str, QueryDataset]:
    client = client_factory(settings)
    tasks = [
        {
            "query": query,
            "currency": currency,
            **task_params,
        }
        for _, task_params in SEGMENT_TASKS
    ]

    try:
        responses = await parallel_search(client, tasks, settings)
    finally:
        await client.aclose()

    datasets: dict[str, QueryDataset] = {}
    for (segment_name, _), response in zip(SEGMENT_TASKS, responses, strict=True):
        ads = apply_search_mode(response.get("ads", []), query, strict_search)
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
