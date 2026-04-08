from __future__ import annotations

from collections import defaultdict
from typing import Any

from api.services.aggregator import (
    PriceStats,
    get_param,
    normalize_price_byn,
    normalize_search_text,
)


def region_label(ad: dict[str, Any]) -> str | None:
    for key in ("region_name", "location_name", "area_name", "locality_name"):
        value = ad.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    region_id = ad.get("region_id")
    if region_id in (None, "", 0):
        return None
    return f"Регион {region_id}"


def fair_price_band(price_vs_median: float | None) -> str | None:
    if price_vs_median is None:
        return None
    if price_vs_median <= -15:
        return "below_market"
    if price_vs_median <= 12:
        return "fair"
    if price_vs_median <= 30:
        return "above_market"
    return "high"


def fair_price_label(band: str | None) -> str | None:
    labels = {
        "below_market": "Ниже рынка",
        "fair": "По рынку",
        "above_market": "Выше рынка",
        "high": "Сильно выше рынка",
    }
    return labels.get(band)


def detect_anomaly_flags(ad: dict[str, Any], stats: PriceStats) -> list[str]:
    price_byn = normalize_price_byn(ad.get("price_byn"))
    if price_byn is None or stats.count < 4:
        return []

    flags: list[str] = []
    if price_byn < min(stats.q1 * 0.72, stats.median * 0.68):
        flags.append("too_cheap")
    if price_byn > max(stats.q3 * 1.35, stats.median * 1.45):
        flags.append("too_expensive")
    return flags


def duplicate_counts(ads: list[dict[str, Any]]) -> dict[int, int]:
    """Count how many near-duplicate listings each ad has.

    Two listings are considered duplicates if they have the same normalized
    title, same seller type, same region, and prices within 8% of each other.
    """
    grouped: dict[tuple[str, str, int | None], list[tuple[int, float | None]]] = defaultdict(list)

    for ad in ads:
        ad_id = int(ad.get("ad_id", 0))
        if ad_id <= 0:
            continue
        title = normalize_search_text(str(ad.get("subject", "")))
        if not title:
            continue
        seller_type = get_param(ad, "seller_type") or ""
        region_id = ad.get("region_id") if isinstance(ad.get("region_id"), int) else None
        grouped[(title, seller_type, region_id)].append(
            (ad_id, normalize_price_byn(ad.get("price_byn")))
        )

    counts: dict[int, int] = {}
    for items in grouped.values():
        if len(items) < 2:
            continue
        for ad_id, price in items:
            similar = 0
            for other_id, other_price in items:
                if other_id == ad_id:
                    continue
                if price is None or other_price is None:
                    similar += 1
                    continue
                if price <= 0 or other_price <= 0:
                    continue
                if abs(other_price - price) / price <= 0.08:
                    similar += 1
            if similar:
                counts[ad_id] = similar
    return counts


def anomaly_labels(flags: list[str]) -> list[str]:
    mapping = {
        "too_cheap": "Подозрительно дёшево",
        "too_expensive": "Подозрительно дорого",
    }
    return [mapping[flag] for flag in flags if flag in mapping]
