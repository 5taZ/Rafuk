from __future__ import annotations

import hashlib
import math
import re
import statistics
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel

KOPECKS = 100
MAX_PRICE_BYN = 100_000.0
# Stats are computed from price_byn, so Kufar is always queried in one currency and
# responses are converted afterwards: switching BYN/USD never refetches the dataset.
DATASET_CURRENCY = "BYN"
# Snapshot and listing-state keys live in VARCHAR(255) columns.
QUERY_KEY_MAX_LENGTH = 255
QUERY_KEY_HASH_LENGTH = 12
# Prices below this share of the median are placeholders ("1 BYN", "обмен", "торг"):
# they are dropped from mean/min/q1 and never become deals.
LOW_OUTLIER_MEDIAN_RATIO = 0.25
MIN_PRICES_FOR_LOW_CUTOFF = 4
# A listing time this far in the future is treated as unknown rather than fresh.
FUTURE_LIST_TIME_TOLERANCE_HOURS = 0.25

STRICT_VARIANT_TOKENS = {
    "pro",
    "max",
    "plus",
    "mini",
    "ultra",
    "air",
    "lite",
    "note",
    "fe",
    "flip",
    "fold",
    "studio",
    "slim",
    "fat",
}

# Applied in order on word boundaries, so longer phrases go before their prefixes.
SEARCH_ALIASES = {
    "айфон": "iphone",
    "макбук": "macbook",
    "мак бук": "macbook",
    "playstation": "ps",
    "play station": "ps",
    "плейстейшен": "ps",
    "пс": "ps",
    "пс5": "ps5",
    "пс4": "ps4",
    "ps 5": "ps5",
    "ps 4": "ps4",
    "слим": "slim",
    "phat": "fat",
    "фат": "fat",
    "про макс": "pro max",
    "promax": "pro max",
    "про": "pro",
}
# Generic words that mean a specific variant only next to a PlayStation token.
PLAYSTATION_CONTEXT_ALIASES = {
    "обычная": "fat",
    "обычный": "fat",
}

_WORD_CHARS = "0-9a-zа-яё"
_ALIAS_PATTERNS = [
    (re.compile(rf"(?<![{_WORD_CHARS}]){re.escape(source)}(?![{_WORD_CHARS}])"), target)
    for source, target in SEARCH_ALIASES.items()
]
_PLAYSTATION_CONTEXT_PATTERNS = [
    (re.compile(rf"(?<![{_WORD_CHARS}]){re.escape(source)}(?![{_WORD_CHARS}])"), target)
    for source, target in PLAYSTATION_CONTEXT_ALIASES.items()
]
_PLAYSTATION_TOKEN_RE = re.compile(rf"(?<![{_WORD_CHARS}])ps[345]?(?![{_WORD_CHARS}])")

CONDITION_ALIASES = {
    # Kufar codes, as formatCondition in the Mini App reads them: "1" = used, "2" = new.
    "new": {"2", "new", "новый", "новое", "новая", "новые"},
    "used": {"1", "used", "б/у", "бу", "б у", "б/у."},
}
SELLER_TYPE_ALIASES = {
    "private": {"частное лицо", "частник", "private", "owner", "search_owner"},
    "shop": {"магазин", "компания", "company", "shop", "business", "search_business"},
}
_CONDITION_LOOKUP = {
    alias: canonical for canonical, aliases in CONDITION_ALIASES.items() for alias in aliases
}
_SELLER_TYPE_LOOKUP = {
    alias: canonical for canonical, aliases in SELLER_TYPE_ALIASES.items() for alias in aliases
}


class PriceStats(BaseModel):
    mean: float
    median: float
    q1: float
    q3: float
    min: float
    max: float
    count: int


def utc_now() -> datetime:
    """Current time; tests monkeypatch it to freeze scoring that depends on now()."""
    return datetime.now(UTC)


def normalize_search_text(value: str) -> str:
    text = str(value or "").casefold()
    for pattern, target in _ALIAS_PATTERNS:
        text = pattern.sub(target, text)
    if _PLAYSTATION_TOKEN_RE.search(text):
        for pattern, target in _PLAYSTATION_CONTEXT_PATTERNS:
            text = pattern.sub(target, text)
    text = re.sub(r"(\d+)\s*gb\b", r"\1", text)
    text = re.sub(r"(\d+)\s*гб\b", r"\1", text)
    text = re.sub(r"(\d+)\s*/\s*(\d+)", r"\1 \2", text)
    text = re.sub(r"\b([12])\s*(?:tb|тб)\b", lambda match: str(int(match.group(1)) * 1024), text)
    text = re.sub(r"[^a-zа-я0-9]+", " ", text, flags=re.IGNORECASE)
    return " ".join(text.split())


def tokenize_search_text(value: str) -> list[str]:
    normalized = normalize_search_text(value)
    return normalized.split() if normalized else []


def build_query_key(query: str, strict_search: bool) -> str:
    """Return the snapshot/listing-state key, always at most QUERY_KEY_MAX_LENGTH chars.

    Short keys look like ``broad::<normalized query>``. Longer ones keep a truncated
    normalized part followed by ``#`` and a stable hash of the full normalized query.
    """
    prefix = "strict::" if strict_search else "broad::"
    normalized = normalize_search_text(query)
    key = f"{prefix}{normalized}"
    if len(key) <= QUERY_KEY_MAX_LENGTH:
        return key
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:QUERY_KEY_HASH_LENGTH]
    room = QUERY_KEY_MAX_LENGTH - len(prefix) - len(digest) - 1
    return f"{prefix}{normalized[:room].rstrip()}#{digest}"


def response_cache_key(endpoint: str, query: str, strict_search: bool, *parts: object) -> str:
    """Cache key for a market response: the normalized query key plus response-shaping params."""
    return ":".join([endpoint, build_query_key(query, strict_search), *map(str, parts)])


def is_strict_match(title: str, query: str) -> bool:
    """All query tokens are in the title (any order) and the title has no extra variants."""
    query_tokens = tokenize_search_text(query)
    title_tokens = tokenize_search_text(title)
    if not query_tokens or not title_tokens:
        return False

    title_set = set(title_tokens)
    if not all(token in title_set for token in query_tokens):
        return False

    query_variants = {token for token in query_tokens if token in STRICT_VARIANT_TOKENS}
    title_variants = title_set & STRICT_VARIANT_TOKENS
    return not title_variants - query_variants


def apply_search_mode(
    ads: list[dict[str, Any]],
    query: str,
    strict_search: bool,
) -> list[dict[str, Any]]:
    if not strict_search:
        return ads
    return [
        ad
        for ad in ads
        if isinstance(ad, dict) and is_strict_match(str(ad.get("subject") or ""), query)
    ]


def coerce_ad_id(value: Any) -> int | None:
    """Return a positive integer ad id, or None for missing or malformed values."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, float):
        if math.isfinite(value) and value.is_integer() and value > 0:
            return int(value)
        return None
    if isinstance(value, str) and value.strip().isdigit():
        number = int(value.strip())
        return number if number > 0 else None
    return None


def coerce_region_id(value: Any) -> int | None:
    return coerce_ad_id(value)


def parse_list_time(value: Any) -> datetime | None:
    """Parse a Kufar ``list_time`` into an aware UTC datetime; None when unusable."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def listing_age_hours(value: Any, *, now: datetime | None = None) -> float | None:
    """Age of a listing in hours; None when unknown or clearly in the future."""
    parsed = parse_list_time(value)
    if parsed is None:
        return None
    current = now or utc_now()
    age = (current - parsed).total_seconds() / 3600
    if age < -FUTURE_LIST_TIME_TOLERANCE_HOURS:
        return None
    return max(age, 0.0)


def normalize_price_byn(raw_price: Any) -> float | None:
    if raw_price in (None, "", 0, 0.0) or isinstance(raw_price, bool):
        return None
    try:
        numeric = float(raw_price)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(numeric) or numeric <= 0:
        return None
    price_byn = numeric / KOPECKS
    if price_byn > MAX_PRICE_BYN:
        return None
    return price_byn


def ad_price_byn(ad: dict[str, Any]) -> float | None:
    return normalize_price_byn(ad.get("price_byn")) if isinstance(ad, dict) else None


def extract_prices(ads: list[dict[str, Any]]) -> list[float]:
    prices: list[float] = []
    for ad in ads:
        price_byn = ad_price_byn(ad)
        if price_byn is None:
            continue
        prices.append(price_byn)
    return prices


def low_price_cutoff(median: float) -> float:
    """Prices below this value (BYN) are treated as placeholders."""
    return median * LOW_OUTLIER_MEDIAN_RATIO if median and median > 0 else 0.0


def is_low_price_outlier(price_byn: float | None, median: float) -> bool:
    return price_byn is not None and price_byn < low_price_cutoff(median)


def drop_low_outliers(prices: list[float]) -> list[float]:
    """Drop placeholder prices far below the median of a large enough sample."""
    if len(prices) < MIN_PRICES_FOR_LOW_CUTOFF:
        return list(prices)
    cutoff = low_price_cutoff(statistics.median(prices))
    return [price for price in prices if price >= cutoff]


def _percentile(data: list[float], percentile: float) -> float:
    if not data:
        return 0.0
    if len(data) == 1:
        return data[0]
    k = (len(data) - 1) * percentile / 100
    floor_idx = int(k)
    ceil_idx = min(floor_idx + 1, len(data) - 1)
    return data[floor_idx] + (k - floor_idx) * (data[ceil_idx] - data[floor_idx])


def compute_price_stats(prices: list[float]) -> PriceStats:
    sorted_prices = sorted(drop_low_outliers(prices))
    if not sorted_prices:
        return PriceStats(mean=0.0, median=0.0, q1=0.0, q3=0.0, min=0.0, max=0.0, count=0)
    return PriceStats(
        mean=round(statistics.mean(sorted_prices), 2),
        median=round(statistics.median(sorted_prices), 2),
        q1=round(_percentile(sorted_prices, 25), 2),
        q3=round(_percentile(sorted_prices, 75), 2),
        min=round(sorted_prices[0], 2),
        max=round(sorted_prices[-1], 2),
        count=len(sorted_prices),
    )


def compute_price_vs_median(ad: dict[str, Any], median: float) -> float:
    if not median:
        return 0.0
    price_byn = ad_price_byn(ad)
    if price_byn is None:
        return 0.0
    return round((price_byn - median) / median * 100.0, 2)


def filter_deal_ads(
    ads: list[dict[str, Any]],
    median: float,
    discount_from_percent: float,
    discount_to_percent: float | None = None,
) -> list[dict[str, Any]]:
    if not median:
        return []

    lower_bound = abs(discount_from_percent)
    upper_bound = abs(discount_to_percent) if discount_to_percent is not None else None
    if upper_bound is not None and upper_bound < lower_bound:
        lower_bound, upper_bound = upper_bound, lower_bound

    filtered = []
    for ad in ads:
        price_byn = ad_price_byn(ad)
        if price_byn is None or is_low_price_outlier(price_byn, median):
            continue
        delta = compute_price_vs_median(ad, median)
        if delta >= 0:
            continue
        discount = abs(delta)
        if discount < lower_bound:
            continue
        if upper_bound is not None and discount > upper_bound:
            continue
        filtered.append(ad)
    return filtered


_EPOCH = datetime.min.replace(tzinfo=UTC)


def _list_time_sort_key(ad: dict[str, Any]) -> tuple[bool, datetime]:
    parsed = parse_list_time(ad.get("list_time"))
    return parsed is not None, parsed or _EPOCH


def sort_listings(ads: list[dict[str, Any]], sort: str, median: float) -> list[dict[str, Any]]:
    """Sort ads; listings without a price always go last for price-based sorts."""
    if sort in {"cheap", "price_asc", "price_desc", "near_median"}:
        priced = [(ad, price) for ad in ads if (price := ad_price_byn(ad)) is not None]
        unpriced = [ad for ad in ads if ad_price_byn(ad) is None]
        if sort == "cheap":
            priced.sort(key=lambda pair: (compute_price_vs_median(pair[0], median), pair[1]))
        elif sort == "price_asc":
            priced.sort(key=lambda pair: pair[1])
        elif sort == "price_desc":
            priced.sort(key=lambda pair: pair[1], reverse=True)
        else:
            priced.sort(key=lambda pair: abs(pair[1] - median))
        return [ad for ad, _ in priced] + unpriced
    return sorted(ads, key=_list_time_sort_key, reverse=True)


def iter_ad_parameters(ad: dict[str, Any]) -> list[dict[str, Any]]:
    params = ad.get("ad_parameters") if isinstance(ad, dict) else None
    if not isinstance(params, list):
        return []
    return [param for param in params if isinstance(param, dict)]


def _scalar_text(value: Any) -> str | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, int | float):
        return str(value)
    return None


def get_param(ad: dict[str, Any], name: str) -> str | None:
    """Return the raw value ("v") of an ad parameter, falling back to its label ("vl")."""
    for param in iter_ad_parameters(ad):
        if param.get("p") != name:
            continue
        value = _scalar_text(param.get("v"))
        if value is not None:
            return value
        label = _scalar_text(param.get("vl"))
        if label is not None:
            return label
    return None


def get_param_values(ad: dict[str, Any], *names: str) -> list[str]:
    """Every non-empty code ("v") and label ("vl") of the named parameters."""
    values: list[str] = []
    for param in iter_ad_parameters(ad):
        if param.get("p") not in names:
            continue
        for key in ("v", "vl"):
            value = _scalar_text(param.get(key))
            if value is not None and value.strip():
                values.append(value.strip())
    return values


def canonical_condition(value: Any) -> str | None:
    text = _scalar_text(value)
    return _CONDITION_LOOKUP.get(text.strip().casefold()) if text else None


def canonical_seller_type(value: Any) -> str | None:
    text = _scalar_text(value)
    return _SELLER_TYPE_LOOKUP.get(text.strip().casefold()) if text else None


def ad_condition(ad: dict[str, Any]) -> str | None:
    """Canonical condition of an ad ("new" / "used") from its code or label."""
    for value in get_param_values(ad, "condition", "cnd"):
        canonical = canonical_condition(value)
        if canonical:
            return canonical
    return None


def ad_seller_type(ad: dict[str, Any]) -> str | None:
    """Canonical seller type ("private" / "shop") from parameters or ``company_ad``."""
    for value in get_param_values(ad, "seller_type"):
        canonical = canonical_seller_type(value)
        if canonical:
            return canonical
    company_ad = ad.get("company_ad") if isinstance(ad, dict) else None
    if isinstance(company_ad, bool):
        return "shop" if company_ad else "private"
    return None


def compute_segments(ads: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[float]] = {}

    for ad in ads:
        price_byn = ad_price_byn(ad)
        if price_byn is None:
            continue
        condition = ad_condition(ad)
        seller_type = ad_seller_type(ad)
        if condition and seller_type:
            grouped.setdefault(f"{condition}_{seller_type}", []).append(price_byn)

    return {name: compute_price_stats(prices).model_dump() for name, prices in grouped.items()}
