from __future__ import annotations

import asyncio
import contextlib
import logging
import secrets
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

from api.config import Settings
from api.metrics import (
    observe_query_dataset_event_with_backend,
    observe_query_dataset_upstream_fetch_with_backend,
)
from api.services.aggregator import (
    PriceStats,
    apply_search_mode,
    compute_price_stats,
    extract_prices,
)
from api.services.cache import digest_cache_key
from api.services.currency_service import CurrencyService

logger = logging.getLogger(__name__)

PRICE_STATS_FIELDS = ("mean", "median", "q1", "q3", "min", "max")
CATEGORY_TOTAL_MAX_CALLS = 8
_CATEGORY_TOTAL_MAX_CALLS = CATEGORY_TOTAL_MAX_CALLS
LOW_RESULT_FALLBACK_THRESHOLD = 3
LOW_RESULT_FALLBACK_MIN_LOOSE_RESULTS = 8
LOW_RESULT_FALLBACK_MULTIPLIER = 4

# Condition-based API tasks — seller_type filtering is done client-side
# because Kufar API no longer accepts the "otype" parameter.
#
# Kufar's `cnd` parameter switched from string ("new"/"used") to numeric
# codes (2=new, 1=used) some time after 2026 — the string variants now
# silently return 0 results. Sending the int codes restores segments.
_API_CONDITION_TASKS = (
    ("new", {"condition": "2"}),
    ("used", {"condition": "1"}),
)


def _cache_digest(prefix: str, payload: dict[str, Any]) -> str:
    return digest_cache_key(prefix, payload)


# Seller type classification: company_ad==True means shop, else private
def _is_shop_ad(ad: dict[str, Any]) -> bool:
    return bool(ad.get("company_ad"))


SEGMENT_NAMES = ("new_private", "new_shop", "used_private", "used_shop")


class SupportsSearchAllAds(Protocol):
    async def search_all_ads(self, **kwargs: Any) -> dict[str, Any]: ...
    async def aclose(self) -> None: ...
    # Optional: per-category totals call. Not all test fakes implement
    # it — `fetch_category_totals` checks for the attribute at runtime.
    async def search(self, **kwargs: Any) -> dict[str, Any]: ...


class SupportsParallelSearch(Protocol):
    async def __call__(
        self,
        client: SupportsSearchAllAds,
        tasks: list[dict[str, Any]],
        settings: Settings,
    ) -> list[dict[str, Any]]: ...


def _normalize_response_ads(response: dict[str, Any]) -> dict[str, Any]:
    """Normalise ``price_byn`` values in a raw Kufar response to kopecks.

    Production Kufar responses always use kopecks (e.g. 150000 for 1500 BYN),
    but some test fixtures pass direct BYN values (e.g. 1500). This function
    detects the latter and multiplies by 100 so downstream code (which always
    divides by 100) works correctly.

    The heuristic is deliberately conservative:
    - If *any* ad has a non-zero ``price_usd`` the response is assumed to be
      already in kopecks (the production format).
    - Otherwise, if ALL prices are divisible by 100 AND at least one price
      exceeds 1000, the values are assumed to already be kopecks — this
      matches the common pattern of items priced in double-digit BYN where
      kopeck values are always multiples of 100 and typically > 1000.
    - Otherwise (values not all divisible by 100, or all ≤ 1000) the
      response is treated as direct BYN and multiplied by 100.
    """
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

    all_divisible_by_100 = all(p % 100 == 0 for p in raw_prices)
    any_above_1000 = any(p > 1000 for p in raw_prices)
    already_kopecks = all_divisible_by_100 and any_above_1000
    if already_kopecks:
        logger.warning(
            "Price normalization heuristic triggered: all prices divisible by "
            "100 and max=%.0f > 1000 — leaving price_byn values unchanged",
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


# Single-flight registry — concurrent callers asking for the SAME
# (query, currency, category, …) tuple wait on one shared Future
# instead of each starting its own Kufar pagination. Without this,
# the 6 parallel endpoints fired by a single search of "samsung"
# each issue 8+ Kufar calls on cold cache, queue behind the
# client's 2-concurrency semaphore + 1-second delay, and the user
# waits ~9 seconds for the slowest endpoint to drain. With it, the
# first arriver paginates once, the others await the same Future,
# and the whole search completes in roughly one pagination's worth
# of time.
#
# This process-local registry is the fast path. RedisCache adds a
# second distributed lock layer for WORKERS>1: one worker owns the cold
# Kufar fetch, sibling workers poll the shared dataset cache, and a
# bounded timeout falls back to a safe duplicate fetch if the owner
# stalls or Redis lock coordination is unavailable.
_inflight_dataset_futures: dict[str, asyncio.Future[dict[str, Any]]] = {}
_MAX_INFLIGHT = 500
_INFLIGHT_STALE_SECONDS = 300  # prune futures older than 5 minutes
_DISTRIBUTED_SINGLEFLIGHT_LOCK_TTL_SECONDS = 30
_DISTRIBUTED_SINGLEFLIGHT_WAIT_SECONDS = 20.0
_DISTRIBUTED_SINGLEFLIGHT_POLL_SECONDS = 0.1

# Protects every read/write of _inflight_dataset_futures. Without this
# two concurrent callers can both miss the dict lookup, both create
# futures, and both run the (slow) pagination — defeating the whole
# point of the pattern and doubling Kufar load. The lock is held only
# for the tiny check-then-insert section; the actual await on the
# future happens OUTSIDE the lock so other keys can be registered.
_inflight_lock: asyncio.Lock | None = None


def _get_inflight_lock() -> asyncio.Lock:
    """Lazy-init the lock so it binds to the current running loop.

    We can't just do `_inflight_lock = asyncio.Lock()` at module level
    because pre-Python-3.10 it would bind to the loop running at import
    time (which might not be the serving loop in tests / reloads).
    """
    global _inflight_lock
    if _inflight_lock is None:
        _inflight_lock = asyncio.Lock()
    return _inflight_lock


def _prune_stale_inflight_futures() -> None:
    """Remove futures from a different event loop or already done.

    Caller must hold `_get_inflight_lock()` — this function mutates
    the shared dict. It intentionally is not async so it can run
    inside the lock without yielding to the event loop.
    """
    loop = asyncio.get_running_loop()
    stale = [
        key for key, fut in _inflight_dataset_futures.items()
        if fut.done() or fut.get_loop() is not loop
    ]
    for key in stale:
        _inflight_dataset_futures.pop(key, None)


def _dataset_cache_key(
    query: str,
    currency: str,
    category: int | None,
    extra: dict[str, Any],
) -> str:
    """Key for caching the raw Kufar response dict.

    Strict-mode filtering happens AFTER the cache (it's just a Python
    list comprehension on the already-fetched ads), so the key
    deliberately omits ``strict_search``. Same goes for sort — Kufar
    returns the same payload regardless of how the frontend wants
    it ordered, so multiple sort options share one cache entry.
    """
    payload: dict[str, Any] = {
        "query": query.strip().casefold(),
        "currency": currency,
        "category": category,
        "extra": {},
    }
    for key in sorted(extra):
        value = extra[key]
        if value in (None, "", [], {}):
            continue
        payload["extra"][key] = value
    return _cache_digest("kufar:dataset", payload)


def _dataset_singleflight_lock_key(dataset_cache_key: str) -> str:
    return f"{dataset_cache_key}:singleflight"


async def _fetch_dataset_response(
    *,
    query: str,
    currency: str,
    effective_kwargs: dict[str, Any],
    settings: Settings,
    client: SupportsSearchAllAds,
    cache: Any | None,
    cache_key: str,
) -> dict[str, Any]:
    fetch_started_at = time.monotonic()
    try:
        response = await client.search_all_ads(
            query=query,
            currency=currency,
            **effective_kwargs,
        )
        await observe_query_dataset_upstream_fetch_with_backend(
            cache,
            status="success",
            duration_seconds=time.monotonic() - fetch_started_at,
        )
        response = _normalize_response_ads(response)
        if cache is not None:
            cache_ttl = getattr(settings, "cache_ttl_seconds", 300) or 300
            await cache.set_json(cache_key, response, ttl=cache_ttl)
        return response
    except BaseException:
        await observe_query_dataset_upstream_fetch_with_backend(
            cache,
            status="error",
            duration_seconds=time.monotonic() - fetch_started_at,
        )
        raise


async def _fetch_dataset_response_with_distributed_singleflight(
    *,
    query: str,
    currency: str,
    effective_kwargs: dict[str, Any],
    settings: Settings,
    client: SupportsSearchAllAds,
    cache: Any | None,
    cache_key: str,
) -> dict[str, Any]:
    acquire_lock = getattr(cache, "try_acquire_lock", None)
    release_lock = getattr(cache, "release_lock", None)
    if cache is None or not callable(acquire_lock) or not callable(release_lock):
        return await _fetch_dataset_response(
            query=query,
            currency=currency,
            effective_kwargs=effective_kwargs,
            settings=settings,
            client=client,
            cache=cache,
            cache_key=cache_key,
        )

    lock_key = _dataset_singleflight_lock_key(cache_key)
    token = secrets.token_urlsafe(24)
    acquired = await acquire_lock(
        lock_key,
        token,
        ttl_seconds=_DISTRIBUTED_SINGLEFLIGHT_LOCK_TTL_SECONDS,
    )
    if acquired is True:
        await observe_query_dataset_event_with_backend(
            cache, "distributed_singleflight_owner"
        )
        try:
            return await _fetch_dataset_response(
                query=query,
                currency=currency,
                effective_kwargs=effective_kwargs,
                settings=settings,
                client=client,
                cache=cache,
                cache_key=cache_key,
            )
        finally:
            await release_lock(lock_key, token)
    if acquired is None:
        await observe_query_dataset_event_with_backend(
            cache, "distributed_singleflight_unavailable"
        )
        return await _fetch_dataset_response(
            query=query,
            currency=currency,
            effective_kwargs=effective_kwargs,
            settings=settings,
            client=client,
            cache=cache,
            cache_key=cache_key,
        )

    await observe_query_dataset_event_with_backend(cache, "distributed_singleflight_wait")
    deadline = time.monotonic() + _DISTRIBUTED_SINGLEFLIGHT_WAIT_SECONDS
    while time.monotonic() < deadline:
        await asyncio.sleep(
            min(
                _DISTRIBUTED_SINGLEFLIGHT_POLL_SECONDS,
                max(deadline - time.monotonic(), 0),
            )
        )
        cached = await cache.get_json(cache_key)
        if cached is not None:
            await observe_query_dataset_event_with_backend(
                cache, "distributed_singleflight_cache_hit"
            )
            return cached

    await observe_query_dataset_event_with_backend(cache, "distributed_singleflight_timeout")
    return await _fetch_dataset_response(
        query=query,
        currency=currency,
        effective_kwargs=effective_kwargs,
        settings=settings,
        client=client,
        cache=cache,
        cache_key=cache_key,
    )


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
    cache: Any | None = None,
    force_refresh: bool = False,
) -> QueryDataset:
    """Build a QueryDataset for one query.

    When ``cache`` is provided, the raw Kufar response is cached
    under a key derived from (query, currency, category, extras).
    The 6 endpoints fired in parallel by a single search request
    (price-stats, listings, segments, geography, …) all hit the
    same key, so only the FIRST one pays the Kufar fetch cost —
    the others see a cache hit. This is the dominant speed-up on
    a fresh search since Kufar pagination + the 1s rate-limit
    delay otherwise dominate every response.
    """
    owns_client = client is None
    if client is None:
        if client_factory is None:
            raise ValueError("Either client or client_factory must be provided")
        client = client_factory(settings)
    try:
        effective_kwargs = dict(search_kwargs or {})
        if category is not None:
            effective_kwargs["category"] = category

        # Even without a Redis cache we singleflight by the same key
        # — concurrent in-process callers shouldn't each trigger
        # their own pagination chain.
        sf_key = _dataset_cache_key(
            query=query,
            currency=currency,
            category=category,
            extra=effective_kwargs,
        )

        response: dict[str, Any] | None = None
        if cache is not None and not force_refresh:
            response = await cache.get_json(sf_key)
            await observe_query_dataset_event_with_backend(
                cache, "cache_hit" if response is not None else "cache_miss"
            )
        elif force_refresh:
            await observe_query_dataset_event_with_backend(cache, "force_refresh")
        else:
            await observe_query_dataset_event_with_backend(cache, "cache_disabled")

        if response is None:
            # Register-or-attach under the lock. We must decide
            # "do I own the fetch or am I a follower?" atomically —
            # without the lock two coroutines would both see a cache
            # miss, both decide to own, and we'd pay the Kufar cost
            # twice.
            lock = _get_inflight_lock()
            async with lock:
                _prune_stale_inflight_futures()
                if len(_inflight_dataset_futures) > _MAX_INFLIGHT:
                    logger.warning(
                        "Purging %d stale inflight futures",
                        len(_inflight_dataset_futures),
                    )
                    _inflight_dataset_futures.clear()
                inflight = _inflight_dataset_futures.get(sf_key)
                if inflight is not None and not inflight.done():
                    owns_future = False
                    future = inflight
                else:
                    loop = asyncio.get_running_loop()
                    future = loop.create_future()
                    _inflight_dataset_futures[sf_key] = future
                    owns_future = True

            if not owns_future:
                await observe_query_dataset_event_with_backend(cache, "singleflight_wait")
                # Wait outside the lock so the owner can finish and
                # other keys can register meanwhile.
                response = await future
            else:
                await observe_query_dataset_event_with_backend(cache, "singleflight_owner")
                try:
                    response = await _fetch_dataset_response_with_distributed_singleflight(
                        query=query,
                        currency=currency,
                        effective_kwargs=effective_kwargs,
                        settings=settings,
                        client=client,
                        cache=cache,
                        cache_key=sf_key,
                    )
                    if not future.done():
                        future.set_result(response)
                except BaseException as exc:
                    if not future.done():
                        future.set_exception(exc)
                    raise
                finally:
                    # Clear under the lock so prune/re-entry don't
                    # race with us. `.pop(key, None)` is a no-op if a
                    # later cycle already rotated the key out.
                    async with lock:
                        _inflight_dataset_futures.pop(sf_key, None)
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


@dataclass(slots=True)
class DatasetWithFallback:
    dataset: QueryDataset
    fallback_used: bool = False


@dataclass(slots=True)
class DatasetContextWithFallback:
    context: QueryDatasetContext
    fallback_used: bool = False


def _should_use_low_result_fallback(strict_count: int, loose_count: int) -> bool:
    if strict_count == 0:
        return True
    return (
        strict_count < LOW_RESULT_FALLBACK_THRESHOLD
        and loose_count >= LOW_RESULT_FALLBACK_MIN_LOOSE_RESULTS
        and loose_count >= strict_count * LOW_RESULT_FALLBACK_MULTIPLIER
    )


async def load_query_dataset_with_fallback(
    **kwargs: Any,
) -> DatasetWithFallback:
    """Try strict search first, fall back to loose if 0 results."""
    allow_low_result_fallback = kwargs.pop("allow_low_result_fallback", True)
    dataset = await load_query_dataset(**kwargs)
    if (
        allow_low_result_fallback
        and kwargs.get("strict_search")
        and len(dataset.ads) < LOW_RESULT_FALLBACK_THRESHOLD
    ):
        loose_kwargs = {**kwargs, "strict_search": False}
        loose_dataset = await load_query_dataset(**loose_kwargs)
        if _should_use_low_result_fallback(len(dataset.ads), len(loose_dataset.ads)):
            return DatasetWithFallback(dataset=loose_dataset, fallback_used=True)
    return DatasetWithFallback(dataset=dataset, fallback_used=False)


async def load_query_dataset_context_with_fallback(
    **kwargs: Any,
) -> DatasetContextWithFallback:
    """Try strict search first, fall back to loose if 0 results."""
    allow_low_result_fallback = kwargs.pop("allow_low_result_fallback", True)
    ctx = await load_query_dataset_context(**kwargs)
    if (
        allow_low_result_fallback
        and kwargs.get("strict_search")
        and len(ctx.visible.ads) < LOW_RESULT_FALLBACK_THRESHOLD
    ):
        loose_kwargs = {**kwargs, "strict_search": False}
        loose_ctx = await load_query_dataset_context(**loose_kwargs)
        if _should_use_low_result_fallback(len(ctx.visible.ads), len(loose_ctx.visible.ads)):
            return DatasetContextWithFallback(context=loose_ctx, fallback_used=True)
    return DatasetContextWithFallback(context=ctx, fallback_used=False)


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
    search_kwargs: dict[str, Any] | None = None,
    cache: Any | None = None,
    force_refresh: bool = False,
) -> QueryDatasetContext:
    """Build the visible/reference dataset pair for a query.

    When a category filter is active we issue a real `cat=` query to
    Kufar so the visible listings + total match exactly what the
    kufar.by sidebar shows for that category. (For example, "Audi Q7
    4L" in "Легковые авто" → Kufar returns 11 ads, even though only
    5 ads in the unfiltered response carry that category id.) The
    reference dataset stays unfiltered when `reference_context` is
    `base_query` so price comparisons can still use the broader
    market context.
    """
    owns_client = client is None and client_factory is not None
    if client is None and client_factory is not None:
        client = client_factory(settings)  # type: ignore[misc]

    if reference_context != "base_query" or category is None:
        try:
            dataset = await load_query_dataset(
                query=query,
                currency=currency,
                strict_search=strict_search,
                settings=settings,
                client=client,
                search_kwargs=search_kwargs,
                category=category,
                cache=cache,
                force_refresh=force_refresh,
            )
        finally:
            if owns_client and client is not None:
                await client.aclose()
        return QueryDatasetContext(visible=dataset, reference=dataset)

    try:
        reference_dataset = await load_query_dataset(
            query=query,
            currency=currency,
            strict_search=strict_search,
            settings=settings,
            client=client,
            search_kwargs=search_kwargs,
            cache=cache,
            force_refresh=force_refresh,
        )
        visible_dataset = await load_query_dataset(
            query=query,
            currency=currency,
            strict_search=strict_search,
            settings=settings,
            search_kwargs=search_kwargs,
            category=category,
            client=client,
            cache=cache,
            force_refresh=force_refresh,
        )
    finally:
        if owns_client and client is not None:
            await client.aclose()

    return QueryDatasetContext(visible=visible_dataset, reference=reference_dataset)


async def fetch_category_totals(
    *,
    query: str,
    currency: str,
    strict_search: bool,
    client: SupportsSearchAllAds,
    category_ids: list[int],
    cache: Any | None = None,
) -> dict[int, int]:
    """Fetch the per-category total that Kufar shows in its sidebar.

    For each id we issue a `cat=<id>&size=200` query to Kufar (in
    parallel) and trust the raw ``total`` when it is available. The
    frontend separately discloses when our strict/local sample is smaller
    than the official Kufar count.

    When ``cache`` is provided, the {cat_id -> total} map is cached
    under a key that includes the query + the sorted ids the caller
    asked about. On a warm hit this skips the 2-3 second Kufar
    fan-out entirely, which dominates price-stats latency on cold
    Kufar dataset hits.

    Skips quietly if the client doesn't expose a low-level `.search()`
    method (test fakes that only stub `search_all_ads`).
    """
    if not category_ids:
        return {}
    search_method = getattr(client, "search", None)
    if not callable(search_method):
        return {}
    unique_category_ids = list(dict.fromkeys(category_ids))
    selected_category_ids = unique_category_ids[:CATEGORY_TOTAL_MAX_CALLS]
    if len(unique_category_ids) > len(selected_category_ids):
        logger.info(
            "Kufar category-total fan-out capped at %s/%s categories",
            len(selected_category_ids),
            len(unique_category_ids),
        )

    cache_key: str | None = None
    if cache is not None:
        cache_key = _cache_digest("cat-totals", {
            "query": query.strip().casefold(),
            "currency": currency,
            "strict_search": bool(strict_search),
            "category_ids": sorted(selected_category_ids),
            "semantics": 2,
        })
        cached = await cache.get_json(cache_key)
        if isinstance(cached, dict):
            # Redis serialises ints as JSON strings; coerce back.
            try:
                return {int(k): int(v) for k, v in cached.items()}
            except (TypeError, ValueError):
                pass

    async def _fetch_one(cat_id: int) -> tuple[int, int | None]:
        try:
            resp = await search_method(
                query=query,
                size=200,
                currency=currency,
                category=cat_id,
            )
        except Exception as exc:  # noqa: BLE001 — log + degrade
            logger.warning("Kufar category-total fetch failed cat=%s: %s", cat_id, exc)
            return cat_id, None
        if not isinstance(resp, dict):
            return cat_id, None
        ads = resp.get("ads") or []
        kufar_total = resp.get("total")
        if isinstance(kufar_total, int) and kufar_total > 0:
            return cat_id, kufar_total
        filtered = apply_search_mode(ads, query, strict_search)
        return cat_id, len(filtered)

    started_at = time.monotonic()
    results = await asyncio.gather(
        *[_fetch_one(cid) for cid in selected_category_ids],
        return_exceptions=True,
    )
    out: dict[int, int] = {}
    for r in results:
        if isinstance(r, tuple) and r[1] is not None:
            out[r[0]] = r[1]
    if cache is not None and cache_key is not None and out:
        # 5-minute TTL aligns with the dataset cache; chip totals
        # don't need to lead the dataset by much, and a stale chip
        # count for ~5 min is harmless next to the latency win.
        with contextlib.suppress(Exception):
            await cache.set_json(cache_key, out, ttl=300)
    logger.info(
        "Kufar category-total fan-out finished categories=%s hits=%s duration_ms=%s",
        len(selected_category_ids),
        len(out),
        int((time.monotonic() - started_at) * 1000),
    )
    return out


# Top-level Kufar category ids → ordered list of known sub-category
# ids that share the same parent group. Extracted from the kufar.by
# `__NEXT_DATA__` payload + observed Kufar API responses. Used to
# expose chips that don't appear in the unfiltered first-200 ads
# distribution but are still meaningful for the query (e.g.
# "Легковые авто" — 2010 — for "Audi Q7 4L 2015" where the first 200
# Kufar ads are all "Запчасти" (2040)).
KUFAR_CATEGORY_FAMILY: dict[int, tuple[int, ...]] = {
    # 2000 — Авто и запчасти
    2000: (2010, 2020, 2030, 2040, 2050, 2060, 2070, 2075, 2080),
    # 1000 — Недвижимость (rare in mini-app, kept for completeness)
    1000: (1010, 1020, 1030, 1040, 1050, 1060),
    # 17000 — Телефоны и планшеты
    17000: (17010, 17020, 17030, 17040, 17050, 17060),
    # 5000 — Электроника
    5000: (5010, 5020, 5030, 5040, 5050, 5060),
}


# Fallback labels for category ids that were added by the family
# expansion but don't have an ad in the first 200 ads to read the
# label from. Sourced from kufar.by's `__NEXT_DATA__`.
KUFAR_CATEGORY_LABELS: dict[int, str] = {
    2000: "Авто и запчасти",
    2010: "Легковые авто",
    2020: "Грузовики и спецтехника",
    2030: "Мото",
    2040: "Запчасти",
    2050: "Спецтехника",
    2060: "Водный транспорт",
    2070: "Аксессуары",
    2075: "Шины, диски",
    2080: "Инструмент, оборудование",
}


def expand_with_known_siblings(category_ids: list[int]) -> list[int]:
    """Add known sibling subcategories from the same Kufar parent group.

    For each id in ``category_ids`` whose parent (id // 1000 * 1000)
    has a hardcoded family in :data:`KUFAR_CATEGORY_FAMILY`, return a
    list that includes every sibling. Preserves the original order and
    de-duplicates. If no family is known, the input is returned as-is.
    """
    seen: set[int] = set()
    out: list[int] = []
    for cid in category_ids:
        if cid in seen:
            continue
        seen.add(cid)
        out.append(cid)
        parent = (cid // 1000) * 1000
        for sibling in KUFAR_CATEGORY_FAMILY.get(parent, ()):
            if sibling in seen:
                continue
            seen.add(sibling)
            out.append(sibling)
    return out


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
