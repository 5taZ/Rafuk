from __future__ import annotations

import asyncio
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
    parts = [
        f"q={query.strip().casefold()}",
        f"cur={currency}",
        f"cat={category if category is not None else ''}",
    ]
    for key in sorted(extra):
        value = extra[key]
        if value in (None, "", [], {}):
            continue
        parts.append(f"{key}={value}")
    return "kufar:dataset:" + ":".join(parts)


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

        cache_key: str | None = None
        response: dict[str, Any] | None = None
        if cache is not None:
            cache_key = _dataset_cache_key(
                query=query,
                currency=currency,
                category=category,
                extra=effective_kwargs,
            )
            response = await cache.get_json(cache_key)

        if response is None:
            response = await client.search_all_ads(
                query=query,
                currency=currency,
                **effective_kwargs,
            )
            response = _normalize_response_ads(response)
            if cache is not None and cache_key is not None:
                # 5-minute TTL — long enough for the 6 parallel search
                # endpoints to share, short enough that fresh ads
                # show up promptly on the user's next search.
                await cache.set_json(cache_key, response, ttl=300)
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
    cache: Any | None = None,
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
                category=category,
                cache=cache,
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
            cache=cache,
        )
        visible_dataset = await load_query_dataset(
            query=query,
            currency=currency,
            strict_search=strict_search,
            settings=settings,
            category=category,
            client=client,
            cache=cache,
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
) -> dict[int, int]:
    """Fetch the per-category total that the listings page will show.

    For each id we issue a `cat=<id>&size=200` query to Kufar (in
    parallel) and count how many ads survive ``apply_search_mode`` —
    this is the *same* filter the /listings endpoint applies, so the
    chip count and the listings count line up exactly. Trusting the
    raw Kufar `total` would diverge for refined queries (e.g. "Audi
    Q7 4L 2015" + cat=2010: Kufar's total=11, but only 3 ads pass
    apply_search_mode).

    Skips quietly if the client doesn't expose a low-level `.search()`
    method (test fakes that only stub `search_all_ads`).
    """
    if not category_ids:
        return {}
    search_method = getattr(client, "search", None)
    if not callable(search_method):
        return {}

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
        # Mirror the listings filter so the chip and the cards agree.
        filtered = apply_search_mode(ads, query, strict_search)
        kufar_total = resp.get("total")
        # When the response hit our 200-ad cap and Kufar reports more,
        # trust Kufar's total (matches what kufar.by sidebar shows for
        # large categories like "Запчасти" with thousands of ads).
        # Otherwise the precise post-filter count is the right number.
        if (
            len(ads) >= 200
            and isinstance(kufar_total, int)
            and kufar_total > len(filtered)
        ):
            return cat_id, kufar_total
        return cat_id, len(filtered)

    results = await asyncio.gather(
        *[_fetch_one(cid) for cid in category_ids],
        return_exceptions=True,
    )
    out: dict[int, int] = {}
    for r in results:
        if isinstance(r, tuple) and r[1] is not None:
            out[r[0]] = r[1]
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
