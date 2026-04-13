from __future__ import annotations

import re
import statistics
from typing import Any

from pydantic import BaseModel

KOPECKS = 100
MAX_PRICE_BYN = 100_000.0
MIN_PRICE_BYN = 0.5  # Ignore listings priced below 0.50 BYN (kopecks remainder / spam)
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
    "ti",
    "super",
    "se",
    "xs",
    "xr",
}
# Tokens shorter than 3 chars are too ambiguous for variant matching and cause
# false positives (e.g. "s" matching inside "s24", "x" matching "xs").
# They are excluded from the variant-extras check but still used for token matching.

SEARCH_ALIASES = {
    "айфон": "iphone",
    "макбук": "macbook",
    "мак бук": "macbook",
    "mac book": "macbook",
    "playstation": "ps",
    "play station": "ps",
    "плейстейшен": "ps",
    "плей стейшен": "ps",
    "пс": "ps",
    "пс5": "ps5",
    "пс4": "ps4",
    "ps 5": "ps5",
    "ps 4": "ps4",
    "слим": "slim",
    "phat": "fat",
    "фат": "fat",
    "обычная": "fat",
    "обычный": "fat",
    "про макс": "pro max",
    "promax": "pro max",
    "самсунг": "samsung",
    "галакси": "galaxy",
    "гэлакси": "galaxy",
    "сяоми": "xiaomi",
    "редми": "redmi",
    "поко": "poco",
    "поук": "poco",
    "найк": "nike",
    "адидас": "adidas",
    "нот": "note",
    "нубия": "nubia",
    "хуавей": "huawei",
    "хонор": "honor",
}


class PriceStats(BaseModel):
    mean: float
    median: float
    q1: float
    q3: float
    min: float
    max: float
    count: int


def normalize_search_text(value: str) -> str:
    text = value.casefold()
    for source, target in SEARCH_ALIASES.items():
        text = text.replace(source, target)
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
    mode = "strict" if strict_search else "broad"
    return f"{mode}::{normalize_search_text(query)}"


def is_strict_match(title: str, query: str) -> bool:
    """Check if a listing title strictly matches the search query.

    A strict match requires:
    1. Every query token must appear in the title (set-based containment).
    2. The title must not contain variant tokens (pro/max/ultra/etc.) that
       the query does not also contain. This prevents "iphone 15 pro" from
       matching a search for just "iphone 15".
    3. Numeric storage/ram values in the query must appear in the title.
    """
    query_tokens = tokenize_search_text(query)
    title_tokens = tokenize_search_text(title)
    if not query_tokens or not title_tokens:
        return False

    title_set = set(title_tokens)
    if not all(token in title_set for token in query_tokens):
        return False

    query_variants = {token for token in query_tokens if token in STRICT_VARIANT_TOKENS}
    title_variants = {token for token in title_tokens if token in STRICT_VARIANT_TOKENS}
    extra_variants = title_variants - query_variants
    return not extra_variants


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
        if is_strict_match(str(ad.get("subject", "")), query)
    ]


def normalize_price_byn(raw_price: Any) -> float | None:
    if raw_price in (None, "", 0, 0.0):
        return None
    try:
        numeric = float(raw_price)
    except (TypeError, ValueError):
        return None
    if numeric <= 0:
        return None
    # Kufar API returns prices in kopecks — convert to BYN
    price_byn = round(numeric / 100, 2)
    if price_byn > MAX_PRICE_BYN:
        return None
    if price_byn < MIN_PRICE_BYN:
        return None
    return price_byn


def extract_prices(ads: list[dict[str, Any]]) -> list[float]:
    prices: list[float] = []
    for ad in ads:
        price_byn = normalize_price_byn(ad.get("price_byn"))
        if price_byn is None:
            continue
        prices.append(price_byn)
    return prices


def _percentile(data: list[float], percentile: float) -> float:
    if not data:
        return 0.0
    if len(data) == 1:
        return data[0]
    k = (len(data) - 1) * percentile / 100
    floor_idx = int(k)
    ceil_idx = min(floor_idx + 1, len(data) - 1)
    return data[floor_idx] + (k - floor_idx) * (data[ceil_idx] - data[floor_idx])


def _remove_outliers(prices: list[float]) -> list[float]:
    """Remove statistical outliers using the IQR method.

    Prices outside [Q1 - 2.5*IQR, Q3 + 2.5*IQR] are excluded. This prevents
    bogus listings (e.g. 1 BYN phones or 99999 BYN accessories) from polluting
    median and mean calculations. Only applied when there are enough data points
    for IQR to be meaningful (>= 8).
    """
    if len(prices) < 8:
        return prices
    sorted_prices = sorted(prices)
    q1 = _percentile(sorted_prices, 25)
    q3 = _percentile(sorted_prices, 75)
    iqr = q3 - q1
    if iqr <= 0:
        return prices
    lower = q1 - 2.5 * iqr
    upper = q3 + 2.5 * iqr
    return [p for p in prices if lower <= p <= upper]


def compute_price_stats(prices: list[float]) -> PriceStats:
    if not prices:
        return PriceStats(mean=0.0, median=0.0, q1=0.0, q3=0.0, min=0.0, max=0.0, count=0)
    cleaned = _remove_outliers(prices)
    sorted_prices = sorted(cleaned)
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
    price_byn = normalize_price_byn(ad.get("price_byn"))
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


def sort_listings(ads: list[dict[str, Any]], sort: str, median: float) -> list[dict[str, Any]]:
    if sort == "cheap":
        return sorted(
            ads,
            key=lambda ad: (
                compute_price_vs_median(ad, median),
                normalize_price_byn(ad.get("price_byn")) or 0.0,
            ),
        )
    if sort == "price_asc":
        return sorted(ads, key=lambda ad: normalize_price_byn(ad.get("price_byn")) or 0.0)
    if sort == "price_desc":
        return sorted(
            ads,
            key=lambda ad: normalize_price_byn(ad.get("price_byn")) or 0.0,
            reverse=True,
        )
    if sort == "near_median":
        return sorted(
            ads,
            key=lambda ad: abs((normalize_price_byn(ad.get("price_byn")) or 0.0) - median),
        )
    return sorted(ads, key=lambda ad: ad.get("list_time", ""), reverse=True)


def get_param(ad: dict[str, Any], name: str) -> str | None:
    for param in ad.get("ad_parameters", []):
        if param.get("p") == name and isinstance(param.get("v"), str):
            return param["v"]
    return None


def compute_segments(ads: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    condition_map = {"Новый": "new", "Б/у": "used"}
    seller_map = {"Частное лицо": "private", "Магазин": "shop"}
    grouped: dict[str, list[float]] = {}

    for ad in ads:
        price_byn = normalize_price_byn(ad.get("price_byn"))
        if price_byn is None:
            continue
        condition = condition_map.get(get_param(ad, "condition") or "")
        seller_type = seller_map.get(get_param(ad, "seller_type") or "")
        if condition and seller_type:
            grouped.setdefault(f"{condition}_{seller_type}", []).append(price_byn)

    return {name: compute_price_stats(prices).model_dump() for name, prices in grouped.items()}
