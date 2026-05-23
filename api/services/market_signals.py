from __future__ import annotations

from typing import Any

from api.services.aggregator import (
    PriceStats,
    normalize_price_byn,
)


def region_label(ad: dict[str, Any]) -> str | None:
    # Top-level fields first
    for key in ("region_name", "location_name", "area_name", "locality_name"):
        value = ad.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    # Fallback: extract from ad_parameters where p="region" (use vl for text label)
    for param in ad.get("ad_parameters", []):
        if param.get("p") == "region":
            vl = param.get("vl")
            if isinstance(vl, str) and vl.strip():
                return vl.strip()
    region_id = ad.get("region_id")
    if region_id in (None, "", 0):
        return None
    return f"Регион {region_id}"


def area_label(ad: dict[str, Any]) -> str | None:
    """Return city/district-level location, distinct from region."""
    # Top-level fields first
    for key in ("area_name", "locality_name", "location_name"):
        value = ad.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    # Fallback: extract from ad_parameters where p="area" (use vl for text label)
    for param in ad.get("ad_parameters", []):
        if param.get("p") == "area":
            vl = param.get("vl")
            if isinstance(vl, str) and vl.strip():
                return vl.strip()
    return None


# Symmetric thresholds for the fair-price band classification. Numbers are
# percent deviation of the listing's price from its reference median
# (per-category if the category has ≥ 3 ads, otherwise the whole query —
# see resolve_price_reference in aggregator.py).
#   < -25%   → too cheap (often a flag for scams, but also rare deals)
#   -25..-10 → below market (genuine discount worth checking)
#   -10..+10 → at market (fair)
#   +10..+25 → above market (overpaying a bit)
#   > +25%   → strongly above market (overpriced)
FAIR_BAND_DEEP_DISCOUNT = -25.0
FAIR_BAND_BELOW = -10.0
FAIR_BAND_ABOVE = 10.0
FAIR_BAND_DEEP_OVERPRICED = 25.0


def fair_price_band(price_vs_median: float | None) -> str | None:
    if price_vs_median is None:
        return None
    if price_vs_median < FAIR_BAND_DEEP_DISCOUNT:
        return "deep_discount"
    if price_vs_median < FAIR_BAND_BELOW:
        return "below_market"
    if price_vs_median <= FAIR_BAND_ABOVE:
        return "fair"
    if price_vs_median <= FAIR_BAND_DEEP_OVERPRICED:
        return "above_market"
    return "high"


def fair_price_label(band: str | None) -> str | None:
    labels = {
        "deep_discount": "Сильно ниже рынка",
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


def anomaly_labels(flags: list[str]) -> list[str]:
    mapping = {
        "too_cheap": "Подозрительно дёшево",
        "too_expensive": "Подозрительно дорого",
    }
    return [mapping[flag] for flag in flags if flag in mapping]
