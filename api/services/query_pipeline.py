from __future__ import annotations

import asyncio
import json
import logging
import math
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from api.config import Settings
from api.services.aggregator import (
    PriceStats,
    apply_search_mode,
    build_query_key,
    compute_price_stats,
    extract_prices,
)
from api.services.currency_service import CurrencyService
from api.services.kufar_client import KUFAR_PRICE_CURRENCY

logger = logging.getLogger(__name__)

PRICE_STATS_FIELDS = ("mean", "median", "q1", "q3", "min", "max")
SEGMENT_TASKS = (
    ("new_private", {"condition": "new", "seller_type": "search_owner"}),
    ("new_shop", {"condition": "new", "seller_type": "search_business"}),
    ("used_private", {"condition": "used", "seller_type": "search_owner"}),
    ("used_shop", {"condition": "used", "seller_type": "search_business"}),
)
DEFAULT_DATASET_CACHE_TTL_SECONDS = 120.0
DEFAULT_DATASET_CACHE_MAX_ENTRIES = 64


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
    """Normalized ads of one query.

    `ads` holds only ads with a valid integer ad_id (everything that lists, links, deduplicates
    or tracks ads relies on it). Ads Kufar returned without a usable id are kept apart in
    `unidentified_ads`: they still count in `total_results` and in the price statistics.
    """

    query: str
    currency: str
    strict_search: bool
    response: dict[str, Any]
    ads: list[dict[str, Any]]
    unidentified_ads: list[dict[str, Any]] = field(default_factory=list)
    _prices_byn: list[float] | None = field(default=None, init=False, repr=False)
    _price_stats: PriceStats | None = field(default=None, init=False, repr=False)

    @property
    def total_results(self) -> int:
        return len(self.ads) + len(self.unidentified_ads)

    @property
    def prices_byn(self) -> list[float]:
        if self._prices_byn is None:
            self._prices_byn = extract_prices([*self.ads, *self.unidentified_ads])
        return self._prices_byn

    @property
    def price_stats(self) -> PriceStats:
        if self._price_stats is None:
            self._price_stats = compute_price_stats(self.prices_byn)
        return self._price_stats


# --- Ad normalization -------------------------------------------------------------------------


def _coerce_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if math.isfinite(value) and value.is_integer() else None
    if isinstance(value, str):
        text = value.strip()
        if text.lstrip("-").isdigit():
            return int(text)
    return None


def _coerce_price(value: Any) -> int | float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, str):
        try:
            numeric = float(value.strip())
        except ValueError:
            return None
        if not math.isfinite(numeric):
            return None
        return int(numeric) if numeric.is_integer() else numeric
    return None


def _dict_items(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def normalize_ad(ad: Any, *, require_ad_id: bool = True) -> dict[str, Any] | None:
    """Return a copy of a Kufar ad with the field types the rest of the code relies on.

    Returns None for an ad that cannot be used (not a dict, or without an integer ad_id when
    `require_ad_id` is set).
    """
    if not isinstance(ad, dict):
        return None
    normalized = dict(ad)
    ad_id = _coerce_int(ad.get("ad_id"))
    if ad_id is None or ad_id <= 0:
        if require_ad_id:
            return None
        normalized.pop("ad_id", None)
    else:
        normalized["ad_id"] = ad_id

    list_time = ad.get("list_time")
    normalized["list_time"] = (
        list_time if isinstance(list_time, str) else ("" if list_time is None else str(list_time))
    )
    normalized["images"] = _dict_items(ad.get("images"))
    normalized["ad_parameters"] = _dict_items(ad.get("ad_parameters"))
    normalized["region_id"] = _coerce_int(ad.get("region_id"))
    normalized["price_byn"] = _coerce_price(ad.get("price_byn"))
    return normalized


def normalize_ads(ads: Any, *, require_ad_id: bool = True) -> list[dict[str, Any]]:
    """Normalize every ad of a Kufar response, dropping the ones that cannot be used."""
    if not isinstance(ads, list):
        return []
    normalized: list[dict[str, Any]] = []
    dropped = 0
    for ad in ads:
        item = normalize_ad(ad, require_ad_id=require_ad_id)
        if item is None:
            dropped += 1
            continue
        normalized.append(item)
    if dropped:
        logger.warning("Dropped %s malformed Kufar ads", dropped)
    return normalized


def _normalize_response(response: Any) -> dict[str, Any]:
    if not isinstance(response, dict):
        response = {}
    # Ads without a usable id are kept (without the ad_id key) for the price statistics;
    # _build_dataset keeps them out of QueryDataset.ads.
    return {**response, "ads": normalize_ads(response.get("ads"), require_ad_id=False)}


def _build_dataset(
    *,
    query: str,
    currency: str,
    strict_search: bool,
    response: dict[str, Any],
) -> QueryDataset:
    ads = apply_search_mode(response["ads"], query, strict_search)
    return QueryDataset(
        query=query,
        currency=currency,
        strict_search=strict_search,
        response=response,
        ads=[ad for ad in ads if "ad_id" in ad],
        unidentified_ads=[ad for ad in ads if "ad_id" not in ad],
    )


# --- Dataset cache ----------------------------------------------------------------------------


class DatasetCache:
    """In-process cache of normalized Kufar responses.

    Entries expire after `ttl_seconds`, the cache keeps at most `max_entries` (LRU), and
    concurrent loads of the same key share one Kufar pagination.
    """

    def __init__(
        self,
        *,
        ttl_seconds: float = DEFAULT_DATASET_CACHE_TTL_SECONDS,
        max_entries: int = DEFAULT_DATASET_CACHE_MAX_ENTRIES,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._ttl_seconds = ttl_seconds
        self._max_entries = max(1, max_entries)
        self._clock = clock
        self._entries: OrderedDict[str, tuple[float, dict[str, Any]]] = OrderedDict()
        self._inflight: dict[str, asyncio.Task[dict[str, Any]]] = {}

    def __len__(self) -> int:
        return len(self._entries)

    def clear(self) -> None:
        self._entries.clear()

    def get(self, key: str) -> dict[str, Any] | None:
        entry = self._entries.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        if expires_at <= self._clock():
            del self._entries[key]
            return None
        self._entries.move_to_end(key)
        return value

    def put(self, key: str, value: dict[str, Any]) -> None:
        if self._ttl_seconds <= 0:
            return
        self._entries[key] = (self._clock() + self._ttl_seconds, value)
        self._entries.move_to_end(key)
        while len(self._entries) > self._max_entries:
            self._entries.popitem(last=False)

    async def get_or_load(
        self,
        key: str,
        loader: Callable[[], Awaitable[dict[str, Any]]],
    ) -> dict[str, Any]:
        cached = self.get(key)
        if cached is not None:
            return cached

        loop = asyncio.get_running_loop()
        task = self._inflight.get(key)
        if task is None or task.done() or task.get_loop() is not loop:
            task = loop.create_task(self._load(key, loader))
            task.add_done_callback(_consume_task_exception)
            self._inflight[key] = task
        # The shared load keeps running when one of the waiting requests is cancelled.
        return await asyncio.shield(task)

    async def _load(
        self,
        key: str,
        loader: Callable[[], Awaitable[dict[str, Any]]],
    ) -> dict[str, Any]:
        try:
            value = await loader()
            self.put(key, value)
            return value
        finally:
            if self._inflight.get(key) is asyncio.current_task():
                del self._inflight[key]


def _consume_task_exception(task: asyncio.Task[Any]) -> None:
    # Errors are re-raised to every waiter; this only silences "exception never retrieved"
    # when all waiters were cancelled.
    if not task.cancelled():
        task.exception()


_dataset_cache: DatasetCache | None = None


def configure_dataset_cache(cache: DatasetCache | None) -> None:
    """Enable (or disable with None) the process-wide dataset cache. Disabled by default."""
    global _dataset_cache
    _dataset_cache = cache


def get_dataset_cache() -> DatasetCache | None:
    return _dataset_cache


def dataset_cache_key(query: str, strict_search: bool, search_kwargs: dict[str, Any]) -> str:
    """Cache key: normalized query and mode plus the Kufar filters (currency excluded)."""
    filters = {
        name: value
        for name, value in sorted(search_kwargs.items())
        if name != "currency" and value is not None
    }
    return f"{build_query_key(query, strict_search)}|{json.dumps(filters, default=str)}"


# --- Loaders ----------------------------------------------------------------------------------


async def _fetch_normalized_response(
    *,
    query: str,
    settings: Settings,
    client_factory: type[SupportsSearchAllAds],
    search_kwargs: dict[str, Any],
) -> dict[str, Any]:
    client = client_factory(settings)
    try:
        response = await client.search_all_ads(
            query=query,
            currency=KUFAR_PRICE_CURRENCY,
            **search_kwargs,
        )
    finally:
        await client.aclose()
    return _normalize_response(response)


async def load_query_dataset(
    *,
    query: str,
    currency: str,
    strict_search: bool,
    settings: Settings,
    client_factory: type[SupportsSearchAllAds],
    search_kwargs: dict[str, Any] | None = None,
) -> QueryDataset:
    kwargs = dict(search_kwargs or {})
    kwargs.pop("currency", None)

    async def loader() -> dict[str, Any]:
        return await _fetch_normalized_response(
            query=query,
            settings=settings,
            client_factory=client_factory,
            search_kwargs=kwargs,
        )

    cache = _dataset_cache
    if cache is None:
        response = await loader()
    else:
        response = await cache.get_or_load(
            dataset_cache_key(query, strict_search, kwargs),
            loader,
        )

    return _build_dataset(
        query=query,
        currency=currency,
        strict_search=strict_search,
        response=response,
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
    tasks = [
        {
            "query": query,
            "currency": KUFAR_PRICE_CURRENCY,
            **task_params,
        }
        for _, task_params in SEGMENT_TASKS
    ]

    cache = _dataset_cache
    if cache is None:
        client = client_factory(settings)
        try:
            raw_responses = await parallel_search(client, tasks, settings)
        finally:
            await client.aclose()
        responses = [_normalize_response(response) for response in raw_responses]
    else:

        async def load_one(task: dict[str, Any]) -> dict[str, Any]:
            # Each cached load owns its client: a shared load may outlive the request that
            # started it.
            client = client_factory(settings)
            try:
                (response,) = await parallel_search(client, [task], settings)
            finally:
                await client.aclose()
            return _normalize_response(response)

        responses = list(
            await asyncio.gather(
                *(
                    cache.get_or_load(
                        dataset_cache_key(query, strict_search, task_params),
                        lambda task=task: load_one(task),
                    )
                    for task, (_, task_params) in zip(tasks, SEGMENT_TASKS, strict=True)
                )
            )
        )

    return {
        segment_name: _build_dataset(
            query=query,
            currency=currency,
            strict_search=strict_search,
            response=response,
        )
        for (segment_name, _), response in zip(SEGMENT_TASKS, responses, strict=True)
    }


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
