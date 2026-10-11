from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections import defaultdict
from typing import Any

from api.services.aggregator import (
    PriceStats,
    ad_price_byn,
    ad_seller_type,
    coerce_ad_id,
    coerce_region_id,
    get_param_values,
    normalize_search_text,
)

DUPLICATE_PRICE_TOLERANCE = 0.08
_REGION_NAME_KEYS = ("region_name", "location_name", "area_name", "locality_name")
_REGION_PARAMETER_KEYS = ("region", "area")
_SELLER_ID_KEYS = ("account_id", "user_id", "seller_id")


def ad_region_id(ad: dict[str, Any]) -> int | None:
    """Region id from the top-level ``region_id`` or the ``region`` ad parameter."""
    region_id = coerce_region_id(ad.get("region_id"))
    if region_id is not None:
        return region_id
    for value in get_param_values(ad, "region"):
        region_id = coerce_region_id(value)
        if region_id is not None:
            return region_id
    return None


def _region_parameter_labels(ad: dict[str, Any]) -> list[str]:
    labels: list[str] = []
    for value in get_param_values(ad, *_REGION_PARAMETER_KEYS):
        if not value.isdigit():
            labels.append(value)
    return labels


def region_label(ad: dict[str, Any]) -> str | None:
    names = region_names(ad)
    if names:
        return names[0]
    region_id = ad_region_id(ad)
    if region_id is None:
        return None
    return f"Регион {region_id}"


def region_names(ad: dict[str, Any]) -> list[str]:
    """Human-readable region names of an ad (top-level fields and parameter labels)."""
    names = [
        value.strip()
        for key in _REGION_NAME_KEYS
        if isinstance(value := ad.get(key), str) and value.strip()
    ]
    return names + _region_parameter_labels(ad)


def region_candidates(ad: dict[str, Any]) -> list[str]:
    """Every name or code an ad's region can be referred to by."""
    candidates = region_names(ad)
    candidates.extend(value for value in get_param_values(ad, "region") if value.isdigit())
    region_id = ad_region_id(ad)
    if region_id is not None:
        candidates.extend([f"Регион {region_id}", str(region_id)])
    return candidates


def ad_seller_id(ad: dict[str, Any]) -> str | None:
    for key in _SELLER_ID_KEYS:
        value = ad.get(key)
        if isinstance(value, bool) or value in (None, ""):
            continue
        if isinstance(value, str | int):
            return str(value).strip() or None
    return None


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
    price_byn = ad_price_byn(ad)
    if price_byn is None or stats.count < 4:
        return []

    flags: list[str] = []
    if price_byn < min(stats.q1 * 0.72, stats.median * 0.68):
        flags.append("too_cheap")
    if price_byn > max(stats.q3 * 1.35, stats.median * 1.45):
        flags.append("too_expensive")
    return flags


def duplicate_counts(ads: list[dict[str, Any]]) -> dict[int, int]:
    """Count likely reposts of each ad: same title, seller and region, price within 8%.

    Groups are keyed by (normalized title, seller type, region, seller account id when
    Kufar provides it). Inside a group prices are sorted and each ad counts its
    neighbours with binary search, so the whole pass is O(n log n).
    """
    grouped: dict[tuple[str, str, int | None, str], dict[int, float | None]] = defaultdict(dict)

    for ad in ads:
        if not isinstance(ad, dict):
            continue
        ad_id = coerce_ad_id(ad.get("ad_id"))
        if ad_id is None:
            continue
        title = normalize_search_text(str(ad.get("subject") or ""))
        if not title:
            continue
        key = (title, ad_seller_type(ad) or "", ad_region_id(ad), ad_seller_id(ad) or "")
        grouped[key].setdefault(ad_id, ad_price_byn(ad))

    counts: dict[int, int] = {}
    for items in grouped.values():
        if len(items) < 2:
            continue
        unpriced_count = sum(1 for price in items.values() if price is None)
        sorted_prices = sorted(price for price in items.values() if price is not None)
        for ad_id, price in items.items():
            if price is None:
                # Without a price we cannot tell reposts apart: every peer counts.
                similar = len(items) - 1
            else:
                low = bisect_left(sorted_prices, price * (1 - DUPLICATE_PRICE_TOLERANCE))
                high = bisect_right(sorted_prices, price * (1 + DUPLICATE_PRICE_TOLERANCE))
                similar = (high - low - 1) + unpriced_count
            if similar > 0:
                counts[ad_id] = similar
    return counts


def anomaly_labels(flags: list[str]) -> list[str]:
    mapping = {
        "too_cheap": "Подозрительно дёшево",
        "too_expensive": "Подозрительно дорого",
    }
    return [mapping[flag] for flag in flags if flag in mapping]
