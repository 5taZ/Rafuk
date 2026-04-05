from __future__ import annotations

import statistics
from typing import Any

from pydantic import BaseModel

KOPECKS = 100
MAX_PRICE_BYN = 100_000.0


class PriceStats(BaseModel):
    mean: float
    median: float
    q1: float
    q3: float
    min: float
    max: float
    count: int


def normalize_price_byn(raw_price: Any) -> float | None:
    if raw_price in (None, "", 0, 0.0):
        return None
    try:
        numeric = float(raw_price)
    except (TypeError, ValueError):
        return None
    if numeric <= 0:
        return None
    price_byn = numeric / KOPECKS
    if price_byn > MAX_PRICE_BYN:
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


def compute_price_stats(prices: list[float]) -> PriceStats:
    if not prices:
        return PriceStats(mean=0.0, median=0.0, q1=0.0, q3=0.0, min=0.0, max=0.0, count=0)
    sorted_prices = sorted(prices)
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


def sort_listings(ads: list[dict[str, Any]], sort: str, median: float) -> list[dict[str, Any]]:
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
