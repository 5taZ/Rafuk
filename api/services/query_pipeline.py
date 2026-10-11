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
    compute_price_stats,
    extract_prices,
)
from api.services.currency_service import CurrencyService
from api.services.kufar_client import KUFAR_PRICE_CURRENCY, KufarClient

logger = logging.getLogger(__name__)

PRICE_STATS_FIELDS = ("mean", "median", "q1", "q3", "min", "max")
SEGMENT_TASKS = (
    ("new_private", {"condition": "new", "seller_type": "search_owner"}),
    ("new_shop", {"condition": "new", "seller_type": "search_business"}),
    ("used_private", {"condition": "used", "seller_type": "search_owner"}),
    ("used_shop", {"condition": "used", "seller_type": "search_business"}),
)
DEFAULT_DATASET_CACHE_TTL_SECONDS = 120.0
# A truncated pagination (page limit, deadline, failed later page) depends on the load of the
# moment, so it is reused only briefly.
DEFAULT_TRUNCATED_DATASET_TTL_SECONDS = 30.0
DEFAULT_DATASET_CACHE_MAX_ENTRIES = 64
# Memory bound of the dataset cache: ads summed over every entry (a full pagination holds up
# to 5000 normalized ads, tens of MB).
DEFAULT_DATASET_CACHE_MAX_ADS = 20_000
# search_all_ads arguments that only bound a load (or are fixed to BYN) and never select other
# ads, so they stay out of the dataset cache key.
_CACHE_KEY_IGNORED_KWARGS = frozenset({"currency", "deadline_seconds"})


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

    `truncated` is True when the pagination stopped early (page limit, deadline or a failed
    later page): the ads and the statistics then cover only the newest part of the market.
    `kufar_total` is the result count Kufar reported, None when it did not report one.
    """

    query: str
    currency: str
    strict_search: bool
    response: dict[str, Any]
    ads: list[dict[str, Any]]
    unidentified_ads: list[dict[str, Any]] = field(default_factory=list)
    truncated: bool = False
    kufar_total: int | None = None
    _prices_byn: list[float] | None = field(default=None, init=False, repr=False)
    _price_stats: PriceStats | None = field(default=None, init=False, repr=False)

    @property
    def fetched_count(self) -> int:
        """Ads actually fetched (after the search-mode filter): what the statistics cover."""
        return len(self.ads) + len(self.unidentified_ads)

    @property
    def total_results(self) -> int:
        """Market size: Kufar's total for a truncated broad search, else the fetched count.

        Kufar's total counts unfiltered results, so strict mode always uses the fetched count.
        A complete pagination is the most exact count and is used as is.
        """
        fetched = self.fetched_count
        if (
            self.truncated
            and not self.strict_search
            and self.kufar_total is not None
            and self.kufar_total > fetched
        ):
            return self.kufar_total
        return fetched

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
        truncated=_is_truncated(response),
        kufar_total=KufarClient.extract_total(response),
    )


def _is_truncated(response: dict[str, Any]) -> bool:
    return response.get("truncated") is True


# --- Dataset cache ----------------------------------------------------------------------------


def _count_ads(value: dict[str, Any]) -> int:
    ads = value.get("ads") if isinstance(value, dict) else None
    return len(ads) if isinstance(ads, list) else 0


@dataclass(slots=True)
class _CacheEntry:
    expires_at: float
    size: int
    value: dict[str, Any]


class DatasetCache:
    """In-process cache of normalized Kufar responses.

    Entries expire after `ttl_seconds` (`truncated_ttl_seconds` for a truncated pagination)
    and expired entries are purged on every access. The cache is bounded both by the number of
    entries (`max_entries`) and by the ads summed over all entries (`max_ads`), evicting the
    least recently used entries first; a single response larger than `max_ads` is not cached.
    Concurrent loads of the same key share one Kufar pagination.
    """

    def __init__(
        self,
        *,
        ttl_seconds: float = DEFAULT_DATASET_CACHE_TTL_SECONDS,
        truncated_ttl_seconds: float = DEFAULT_TRUNCATED_DATASET_TTL_SECONDS,
        max_entries: int = DEFAULT_DATASET_CACHE_MAX_ENTRIES,
        max_ads: int = DEFAULT_DATASET_CACHE_MAX_ADS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._ttl_seconds = ttl_seconds
        self._truncated_ttl_seconds = min(truncated_ttl_seconds, ttl_seconds)
        self._max_entries = max(1, max_entries)
        self._max_ads = max(0, max_ads)
        self._clock = clock
        self._entries: OrderedDict[str, _CacheEntry] = OrderedDict()
        self._total_ads = 0
        self._inflight: dict[str, asyncio.Task[dict[str, Any]]] = {}

    def __len__(self) -> int:
        return len(self._entries)

    @property
    def total_ads(self) -> int:
        """Ads held by all cached entries."""
        return self._total_ads

    def clear(self) -> None:
        self._entries.clear()
        self._total_ads = 0

    def _remove(self, key: str) -> None:
        entry = self._entries.pop(key, None)
        if entry is not None:
            self._total_ads -= entry.size

    def _evict_oldest(self) -> None:
        _, entry = self._entries.popitem(last=False)
        self._total_ads -= entry.size

    def purge_expired(self) -> int:
        """Drop every expired entry, so stale datasets do not stay in memory until evicted."""
        now = self._clock()
        expired = [key for key, entry in self._entries.items() if entry.expires_at <= now]
        for key in expired:
            self._remove(key)
        return len(expired)

    def get(self, key: str) -> dict[str, Any] | None:
        self.purge_expired()
        entry = self._entries.get(key)
        if entry is None:
            return None
        self._entries.move_to_end(key)
        return entry.value

    def ttl_for(self, value: dict[str, Any]) -> float:
        if isinstance(value, dict) and _is_truncated(value):
            return self._truncated_ttl_seconds
        return self._ttl_seconds

    def put(self, key: str, value: dict[str, Any]) -> None:
        self._remove(key)
        self.purge_expired()
        ttl_seconds = self.ttl_for(value)
        if ttl_seconds <= 0:
            return
        size = _count_ads(value)
        if size > self._max_ads:
            logger.info("Not caching a dataset of %s ads (cache limit %s)", size, self._max_ads)
            return
        self._entries[key] = _CacheEntry(self._clock() + ttl_seconds, size, value)
        self._total_ads += size
        # The new entry fits on its own, so eviction never removes it.
        while len(self._entries) > self._max_entries or self._total_ads > self._max_ads:
            self._evict_oldest()

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


def normalize_cache_query(query: str) -> str:
    """Light normalization of the raw query for cache keys: casefold, collapsed whitespace.

    Unlike build_query_key it applies no aliases and drops no characters, so two keys are
    equal only when Kufar is sent the same search text.
    """
    return " ".join(str(query).casefold().split())


def dataset_cache_key(query: str, strict_search: bool, search_kwargs: dict[str, Any]) -> str:
    """Cache key: the lightly normalized raw query plus the Kufar filters that select ads.

    The cache holds the response before the search-mode filter, so `strict_search` does not
    change the cached value and is not part of the key (it is accepted for callers' sake).
    Currency (always BYN) and `deadline_seconds` (a time budget) are left out as well.
    """
    del strict_search
    filters = {
        name: value
        for name, value in search_kwargs.items()
        if name not in _CACHE_KEY_IGNORED_KWARGS and value is not None
    }
    return json.dumps(
        [normalize_cache_query(query), filters],
        default=str,
        ensure_ascii=False,
        sort_keys=True,
    )


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
    """Load (or reuse from the dataset cache) the full Kufar result set of a query.

    `search_kwargs` are forwarded to KufarClient.search_all_ads: Kufar filters (region,
    condition, seller_type, ...) and the pagination budget (`max_pages`, `deadline_seconds`),
    so an endpoint that loads several datasets can share one time budget between them.
    """
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
    search_kwargs: dict[str, Any] | None = None,
) -> dict[str, QueryDataset]:
    """Load the four condition/seller segments; `search_kwargs` go to every pagination."""
    extra = dict(search_kwargs or {})
    extra.pop("currency", None)
    extra.pop("query", None)
    segment_params = [{**extra, **task_params} for _, task_params in SEGMENT_TASKS]
    tasks = [
        {
            "query": query,
            "currency": KUFAR_PRICE_CURRENCY,
            **params,
        }
        for params in segment_params
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
                        dataset_cache_key(query, strict_search, params),
                        lambda task=task: load_one(task),
                    )
                    for task, params in zip(tasks, segment_params, strict=True)
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
