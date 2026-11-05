"""Comparable Kufar listings for market estimates and AI pricing."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from functools import lru_cache
from itertools import chain
from typing import Any

import httpx

from api.config import Settings
from api.schemas import WatchlistComparison
from api.services.aggregator import (
    MIN_CLUSTER_SIZE,
    PriceStats,
    compute_price_stats,
    compute_price_vs_median,
    get_category_id,
    get_category_label,
    is_strict_match,
    normalize_price_byn,
    normalize_search_text,
    product_cluster_key,
)
from api.services.cache import CacheBackend
from api.services.kufar_client import KufarAPIError
from api.services.query_pipeline import QueryDataset, SupportsSearchAllAds, load_query_dataset
from api.services.reseller_tools import analyze_query_text

_CAR_CATEGORY = 2010
_PHONE_CATEGORY = 17010
_DEVICE_CATEGORIES = {16040, _PHONE_CATEGORY}
_YEAR = re.compile(r"\b(?:19\d{2}|20\d{2})\b")
_IPHONE = re.compile(r"^iphone \d{1,2}(?: (?:pro max|pro|plus|mini|air|e))?\b")
_PHONE_TITLE_PREFIX = re.compile(r"^(?:(?:apple|смартфон|мобильный|телефон) )+")
_MACBOOK = re.compile(r"\bmacbook (?:air|pro)\b")
_MAC_CHIP = re.compile(r"\bm\d+(?: (?:pro|max))?\b")
_MACBOOK_SCREEN = re.compile(r"""\b(13|14|15|16)(?:[.,]\d+)?\s*(?:["″]|'')""")
_MACBOOK_SCREEN_AFTER_FAMILY = re.compile(r"\bmacbook (?:air|pro) (13|14|15|16)\b(?!\s*/)")
_MACBOOK_STORAGE = {"128", "256", "512", "1024", "2048", "4096", "8192"}
_MACBOOK_REQUIRED = {"screen_size", "ram", "storage"}
_MODEL_CODE = re.compile(r"\b([a-z]{1,8})\s?(\d{2,}[a-z0-9]*)\b")
_RENTAL_WORDS = {"аренда", "аренду", "прокат", "сдам", "посуточно", "rent", "rental"}
_GENERATION = re.compile(r"\b(?:mk\s*([1-9])|([ivx]+|[1-9])\s+(?:поколение|рестайлинг))\b")
_MULTIWORD_MAKES = {"mercedes benz", "land rover", "alfa romeo", "great wall"}
_TITLE_CONNECTORS = {"к", "для", "на", "for"}
_CAR_TITLE_PREFIXES = {"продажа", "продам", "продаю", "продается", "продаётся", "автомобиль"}
MARKET_ESTIMATE_TTL = 60
MARKET_SEARCH_OPTIONS = {"page_limit": 8}
_MAX_MARKET_CATEGORIES = 4
logger = logging.getLogger(__name__)
NO_COMPARABLE_PRICES = "Недостаточно сопоставимых объявлений для надёжной оценки цены."
_SPECS = {
    "brand", "model", "condition", "generation", "generation_name",
    "storage", "ram", "memory", "cars_engine", "cars_capacity",
    "cars_gearbox", "cars_type", "cars_generation", "spares_type", "spares_passenger_group",
    "processor", "cpu", "gpu", "screen_size", "diagonal", "rooms", "total_area",
    "frame_size", "wheel_size", "size",
    "бренд", "модель", "поколение", "диагональ экрана", "процессор",
    "оперативная память", "встроенная память",
    "объем встроенной памяти", "объём встроенной памяти", "объем накопителя",
}
_DEVICE_CAPACITY_KEYS = {
    "phablet_phones_memory": "storage",
    "phablet_phones_ram": "ram",
    "computer_equipment_laptops_ram": "ram",
    "computers_laptop_hdd_volume": "storage",
}
_VARIANT_SPECS = {
    "condition", "storage", "ram", "memory", "generation", "generation_name", "поколение",
    "cars_engine", "cars_capacity", "cars_gearbox", "cars_type", "cars_generation",
    "processor", "cpu", "процессор", "gpu", "rooms",
}


def _specs(ad: dict[str, Any]) -> dict[str, str]:
    result = {}
    category = get_category_id(ad)
    title = _title(ad)
    macbook = _macbook_model(title)
    is_device = category in _DEVICE_CATEGORIES or _iphone_model(title)
    if is_device:
        config = analyze_query_text(title)
        if config.storage_gb:
            result["storage"] = str(config.storage_gb)
        if config.ram_gb:
            result["ram"] = str(config.ram_gb)
    if macbook:
        screen = _macbook_screen(title)
        if screen:
            result["screen_size"] = screen
    params = ad.get("ad_parameters")
    for param in params if isinstance(params, (list, tuple)) else ():
        if not isinstance(param, dict):
            continue
        key = str(param.get("p") or "")
        key = _DEVICE_CAPACITY_KEYS.get(key, key)
        label = normalize_search_text(str(param.get("pl") or ""))
        value = param.get("vl") or param.get("v")
        if not isinstance(value, (int, float, str)) or value in ("", "-"):
            continue
        if macbook and key == "computers_laptop_diagonal":
            screen = _macbook_screen(str(value))
            if screen:
                result.setdefault("screen_size", screen)
            continue
        if macbook and key == "computers_laptop_processor":
            continue
        if key in _SPECS or key == "regdate":
            normalized = normalize_search_text(str(value))
            if key == "condition" and normalized in {
                "б у", "used", "с пробегом", "отличное", "хорошее", "удовлетворительное",
                "требует внимания",
            }:
                normalized = "б у"
            elif key == "condition" and normalized in {"новая", "новое", "new", "без пробега"}:
                normalized = "новый"
            result[key] = normalized
        elif label in _SPECS and not (category == _PHONE_CATEGORY and label == "диагональ экрана"):
            normalized = normalize_search_text(str(value))
            if normalized not in {"другое", "другая", "другой", "не указано"}:
                result[label] = normalized
    if macbook:
        advertised_storage = _MACBOOK_STORAGE.intersection(normalize_search_text(title).split())
        if result.get("storage") not in _MACBOOK_STORAGE or len(advertised_storage) > 1:
            result.pop("storage", None)
    return {key: value for key, value in result.items() if value}


def _title(ad: dict[str, Any]) -> str:
    return str(ad.get("subject") or ad.get("title") or "")


def _year(ad: dict[str, Any], specs: dict[str, str]) -> int | None:
    match = _YEAR.search(specs.get("regdate") or _title(ad))
    return int(match[0]) if match else None


def _generation(title: str) -> str | None:
    match = _GENERATION.search(normalize_search_text(title))
    return (match[1] or match[2]) if match else None


@lru_cache(maxsize=4096)
def _iphone_model(title: str) -> str | None:
    model = _IPHONE.match(normalize_search_text(title))
    return model[0] if model else None


@lru_cache(maxsize=4096)
def _macbook_model(title: str) -> str | None:
    normalized = normalize_search_text(title)
    family = _MACBOOK.search(normalized)
    chip = _MAC_CHIP.search(normalized, family.end()) if family else None
    return f"{family[0]} {chip[0]}" if family and chip else None


def _macbook_screen(title: str) -> str | None:
    match = _MACBOOK_SCREEN.search(title) or _MACBOOK_SCREEN_AFTER_FAMILY.search(title.casefold())
    return match[1] if match else None


@lru_cache(maxsize=4096)
def _is_rental(title: str) -> bool:
    return not _RENTAL_WORDS.isdisjoint(normalize_search_text(title).split())


@lru_cache(maxsize=4096)
def _model_codes(title: str) -> frozenset[str]:
    return frozenset("".join(parts) for parts in _MODEL_CODE.findall(normalize_search_text(title)))


def _identity(ad: dict[str, Any], category: int) -> str:
    tokens = [
        token for token in normalize_search_text(_title(ad)).split()
        if token not in _TITLE_CONNECTORS
    ]
    if category == _CAR_CATEGORY:
        tokens = [token for token in tokens if not _YEAR.fullmatch(token)]
        while tokens and tokens[0] in _CAR_TITLE_PREFIXES:
            tokens = tokens[1:]
        make = " ".join(tokens[:2])
        length = 3 if make in _MULTIWORD_MAKES else 2
        return " ".join(tokens[:length]) if len(tokens) >= length else ""
    return " ".join(tokens)


def watchlist_comparison_query(ad: dict[str, Any]) -> str:
    if get_category_id(ad) == _CAR_CATEGORY:
        return _identity(ad, _CAR_CATEGORY)
    title = _title(ad)
    iphone = _iphone_model(title)
    if macbook := _macbook_model(title):
        return macbook
    return " ".join(filter(None, (iphone, _specs(ad).get("storage")))) if iphone else title


def liquidity_segment(ad: dict[str, Any]) -> str | None:
    category = get_category_id(ad)
    title = _title(ad)
    if category is None or not title or _is_rental(title):
        return None
    normalized_title = normalize_search_text(title)
    if set(normalized_title.split()) & {
        "обмен", "куплю", "нерабочий", "неисправный", "запчасти", "разбор", "trade",
    }:
        return None
    specs = _specs(ad)
    iphone = _iphone_model(_PHONE_TITLE_PREFIX.sub("", normalized_title))
    macbook = _macbook_model(title)
    identity = iphone or macbook or _identity(ad, category)
    if (
        not identity
        or (iphone and "storage" not in specs)
        or (macbook and not _MACBOOK_REQUIRED.issubset(specs))
        or ((iphone or macbook) and product_cluster_key(title, identity))
    ):
        return None
    if iphone:
        specs = {key: specs[key] for key in ("storage", "condition") if key in specs}
    elif macbook:
        specs = {key: specs[key] for key in _MACBOOK_REQUIRED | {"condition"} if key in specs}
    elif category == _CAR_CATEGORY:
        if (year := _year(ad, specs)) is None:
            return None
        specs["regdate"] = str(year)
        specs["title_generation"] = _generation(title) or ""
    # The exact signature keeps variants apart even after their raw ads leave the feed.
    fingerprint = json.dumps(
        [category, identity, bool(ad.get("company_ad")), specs], sort_keys=True,
    )
    return hashlib.sha256(fingerprint.encode()).hexdigest()


def listing_draft_target(
    title: str,
    condition: str | None,
    notes: str | None,
    category: int | None,
    ads: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    if category is None:
        normalized = normalize_search_text(title)
        identity = _iphone_model(title)
        exact, matching = set(), set()
        for ad in ads:
            name = _title(ad)
            candidate_category = get_category_id(ad)
            if candidate_category is None or product_cluster_key(name, title):
                continue
            if normalize_search_text(name) == normalized or (
                identity and _iphone_model(name) == identity
            ):
                exact.add(candidate_category)
            elif is_strict_match(name, title):
                matching.add(candidate_category)
        categories = exact or matching
        category = next(iter(categories)) if len(categories) == 1 else None

    params = [{"p": "condition", "v": condition}] if condition else []
    if notes and (category in _DEVICE_CATEGORIES or _iphone_model(title)):
        title_config = analyze_query_text(title)
        notes_config = analyze_query_text(notes)
        if not title_config.storage_gb and notes_config.storage_gb:
            params.append({"p": "storage", "v": str(notes_config.storage_gb)})
        if not title_config.ram_gb and notes_config.ram_gb:
            params.append({"p": "ram", "v": str(notes_config.ram_gb)})
        if (
            _macbook_model(title)
            and not _macbook_screen(title)
            and (screen := _macbook_screen(notes))
        ):
            params.append({"p": "screen_size", "v": screen})
    if category == _CAR_CATEGORY:
        text = f"{title} {notes or ''}"
        years = set(_YEAR.findall(text))
        if len(years) == 1:
            params.append({"p": "regdate", "v": next(iter(years))})
        normalized = normalize_search_text(text)
        choices = {
            "cars_gearbox": {
                "механика": "Механика", "мкпп": "Механика",
                "автомат": "Автоматическая", "акпп": "Автоматическая",
            },
            "cars_engine": {"бензин": "Бензин", "дизель": "Дизель", "гибрид": "Гибрид"},
            "cars_type": {"седан": "Седан", "хэтчбек": "Хэтчбек", "лифтбек": "Лифтбек"},
        }
        tokens = set(normalized.split())
        for key, labels in choices.items():
            values = {labels[token] for token in tokens & labels.keys()}
            if len(values) == 1:
                params.append({"p": key, "v": next(iter(values))})
        capacities = set(re.findall(r"\b([1-9][.,]\d)\s*(?:л\b|литр)", text.casefold()))
        if not capacities and tokens & choices["cars_engine"].keys():
            capacities = set(re.findall(r"\b([1-9][.,]\d)\b", text))
        if len(capacities) == 1:
            capacity = next(iter(capacities)).replace(",", ".")
            params.append({"p": "cars_capacity", "v": f"{capacity} л"})
    return {"subject": title, "category": category, "ad_parameters": params, "draft": True}


@dataclass(frozen=True, slots=True)
class MarketComparables:
    ads: list[dict[str, Any]]
    stats: PriceStats
    category_label: str | None = None

    @property
    def reliable(self) -> bool:
        return self.stats.count >= MIN_CLUSTER_SIZE and self.stats.q3 <= self.stats.q1 * 1.8


def build_market_comparables(
    target: dict[str, Any], ads: Iterable[dict[str, Any]],
    *, _specs_for: Callable[[dict[str, Any]], dict[str, str]] = _specs,
) -> MarketComparables:
    category = get_category_id(target)
    label = get_category_label(target)
    empty = MarketComparables([], compute_price_stats([]), label)
    if category is None:
        return empty
    title = _title(target)
    macbook = _macbook_model(title)
    device_model = macbook or _iphone_model(title)
    identity = device_model or _identity(target, category)
    specs = _specs_for(target)
    rental = _is_rental(title)
    model_codes = _model_codes(title)
    year = _year(target, specs) if category == _CAR_CATEGORY or "regdate" in specs else None
    generation = _generation(title) if category == _CAR_CATEGORY else None
    if (
        not identity
        or (category == _CAR_CATEGORY and year is None)
        or (macbook and not _MACBOOK_REQUIRED.issubset(specs))
    ):
        return empty

    seen = {str(target.get("ad_id"))}
    priced_ads = []
    prices = []
    unspecified_specs: dict[str, set[str]] = {}
    for ad in ads:
        ad_id = str(ad.get("ad_id") or "")
        if not ad_id or ad_id in seen or get_category_id(ad) != category:
            continue
        candidate_title = _title(ad)
        if _is_rental(candidate_title) != rental:
            continue
        if device_model:
            if (_macbook_model(candidate_title) or _iphone_model(candidate_title)) != device_model:
                continue
        else:
            if not is_strict_match(_identity(ad, category), identity):
                continue
            if (
                category != _CAR_CATEGORY
                and not {"model", "модель"} & specs.keys()
                and not _model_codes(candidate_title).issubset(model_codes)
            ):
                continue
        if product_cluster_key(candidate_title, title):
            continue
        candidate_specs = _specs_for(ad)
        if (
            not target.get("draft")
            and specs.get("condition") != candidate_specs.get("condition")
        ):
            continue
        if any(
            candidate_specs.get(key) != value for key, value in specs.items() if key != "regdate"
        ):
            continue
        if year is not None:
            candidate_year = _year(ad, candidate_specs)
            if candidate_year is None or abs(year - candidate_year) > 2:
                continue
        candidate_generation = _generation(candidate_title) if generation else None
        if candidate_generation and candidate_generation != generation:
            continue
        price = normalize_price_byn(ad.get("price_byn"))
        if price and price > 0:
            prices.append(price)
            priced_ads.append(ad)
            seen.add(ad_id)
            for key, value in candidate_specs.items():
                if key in _VARIANT_SPECS and key not in specs:
                    unspecified_specs.setdefault(key, set()).add(value)
    if any(len(values) > 1 for values in unspecified_specs.values()):
        return empty
    stats = compute_price_stats(prices)
    return MarketComparables(
        [
            ad for ad, price in zip(priced_ads, prices, strict=True)
            if stats.min <= price <= stats.max
        ],
        stats,
        label,
    )


def compare_listing_market(
    target: dict[str, Any], ads: Iterable[dict[str, Any]],
    *, _specs_for: Callable[[dict[str, Any]], dict[str, str]] = _specs,
) -> WatchlistComparison:
    result = build_market_comparables(target, ads, _specs_for=_specs_for)
    current_price = normalize_price_byn(target.get("price_byn"), target)
    return WatchlistComparison(
        ad_found=True,
        current_price_byn=current_price,
        category_label=result.category_label,
        comparable_count=result.stats.count,
        median_byn=result.stats.median if result.reliable else None,
        delta_percent=(
            compute_price_vs_median(target, result.stats.median)
            if current_price and result.reliable else None
        ),
    )


# The watchlist endpoint and its tests use this name.
compare_watchlist_ad = compare_listing_market


def compare_listing_page(
    targets: Iterable[dict[str, Any]], ads: Iterable[dict[str, Any]],
) -> dict[int, WatchlistComparison]:
    by_category: dict[int, list[dict[str, Any]]] = defaultdict(list)
    seen = set()
    for ad in ads:
        if not isinstance(ad, dict):
            continue
        ad_id = str(ad.get("ad_id") or "")
        if ad_id and ad_id not in seen and (category := get_category_id(ad)) is not None:
            by_category[category].append(ad)
            seen.add(ad_id)
    specs_by_object: dict[int, tuple[dict[str, Any], dict[str, str]]] = {}

    def specs_for(ad: dict[str, Any]) -> dict[str, str]:
        key = id(ad)
        if key not in specs_by_object:
            # Keep the ad alive for the page even when targets is a generator.
            specs_by_object[key] = (ad, _specs(ad))
        return specs_by_object[key][1]

    comparisons = {}
    for target in targets:
        category = get_category_id(target)
        if category is None or not normalize_price_byn(target.get("price_byn")):
            continue
        comparison = compare_listing_market(
            target, by_category.get(category, []), _specs_for=specs_for,
        )
        if comparison.delta_percent is not None:
            comparisons[int(target["ad_id"])] = comparison
    return comparisons


async def load_market_dataset(
    query: str,
    category: int,
    *,
    settings: Settings,
    client: SupportsSearchAllAds,
    cache: CacheBackend,
    loader=load_query_dataset,
) -> QueryDataset | None:
    try:
        return await loader(
            query=query, category=category, currency="BYN", strict_search=False,
            search_kwargs=MARKET_SEARCH_OPTIONS, settings=settings, client=client, cache=cache,
        )
    except (KufarAPIError, httpx.HTTPError) as exc:
        logger.warning("Nationwide comparison lookup failed: %s", type(exc).__name__)
        return None


def market_sample_limited(dataset: QueryDataset) -> bool:
    return bool(getattr(dataset, "response", {}).get("has_more"))


async def load_page_comparisons(
    targets: list[dict[str, Any]],
    *,
    query: str,
    settings: Settings,
    client: SupportsSearchAllAds,
    cache: CacheBackend,
) -> dict[int, WatchlistComparison]:
    frequencies = Counter(
        category for ad in targets
        if (category := get_category_id(ad)) is not None
        and normalize_price_byn(ad.get("price_byn"))
    )
    categories = [category for category, _ in frequencies.most_common(_MAX_MARKET_CATEGORIES)]
    datasets = await asyncio.gather(*(
        load_market_dataset(query, category, settings=settings, client=client, cache=cache)
        for category in categories
    ))
    available = {
        category: dataset for category, dataset in zip(categories, datasets, strict=True)
        if dataset is not None
    }
    comparisons = await asyncio.to_thread(
        compare_listing_page, targets,
        chain.from_iterable(dataset.ads for dataset in available.values()),
    )
    for ad in targets:
        comparison = comparisons.get(int(ad["ad_id"]))
        if comparison is not None:
            dataset = available.get(get_category_id(ad))
            if dataset is not None:
                comparison.sample_limited = market_sample_limited(dataset)
    return comparisons
