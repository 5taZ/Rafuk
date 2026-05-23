"""Server-side guardrails for the listing-assistant AI response.

Gemini occasionally returns pricing tiers that are non-monotonic
(`fast > market`, `floor > fast`) or clearly out of bounds compared to
the real Kufar market. We can't trust the model to police itself, so we
defensively normalise the pricing block before returning it to the
frontend. This is the same idea as `ai_guardrails.apply_ai_market_guardrails`
but for the seller-side endpoint.
"""

from __future__ import annotations

import logging
from copy import deepcopy
from typing import Any

logger = logging.getLogger(__name__)

_CANONICAL_TIER_LABELS = {"fast": "Быстро", "market": "Рыночная", "patient": "Терпеливо"}


def _coerce_positive(value: Any) -> float | None:
    if value is None:
        return None
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    if num <= 0:
        return None
    return num


def _round(value: float | None) -> float | None:
    if value is None:
        return None
    return round(value, 2)


def _bounds_from_market(
    *,
    median: float | None,
    q1: float | None,
    q3: float | None,
    min_price: float | None,
    max_price: float | None,
    market_count: int,
) -> tuple[float, float] | None:
    """Return a (low, high) clamp range derived from real market data.

    The range is intentionally a bit wider than Q1-Q3 so the AI can still
    reason about reasonable upside (e.g. brand new vs. average condition),
    but tight enough to catch obviously bogus tiers.
    """
    if market_count < 3:
        if min_price is not None and max_price is not None and min_price > 0 and max_price > 0:
            low = float(min_price) * 0.80
            high = float(max_price) * 1.20
            if low >= high:
                return None
            return low, high
        return None

    floor = q1 if q1 and q1 > 0 else min_price
    ceil_ = q3 if q3 and q3 > 0 else max_price

    if floor is None and median:
        floor = median * 0.7
    if ceil_ is None and median:
        ceil_ = median * 1.4

    if floor is None or ceil_ is None:
        return None

    low = float(floor) * 0.65
    high = float(ceil_) * 1.35
    if low >= high:
        return None
    return low, high


def normalize_listing_pricing(
    pricing: dict[str, Any],
    *,
    market_median: float | None,
    market_q1: float | None,
    market_q3: float | None,
    market_min: float | None,
    market_max: float | None,
    market_count: int,
) -> dict[str, Any]:
    """Validate and clamp a listing-assistant pricing block in place.

    Returns a *new* dict — never mutates the input. The result keeps the
    same shape (`{fast, market, patient, floor_byn, ...}`) but with:
      - fast <= market <= patient enforced (sorts by price if violated),
      - floor_byn <= fast (clamps if too high, defaults to 0.85*fast),
      - each tier price clamped into market_count >=3 bounds.

    If the input is missing or unparseable we just hand it back untouched
    — the frontend already tolerates partial pricing.
    """
    if not isinstance(pricing, dict):
        return pricing

    out = deepcopy(pricing)
    bounds = _bounds_from_market(
        median=market_median,
        q1=market_q1,
        q3=market_q3,
        min_price=market_min,
        max_price=market_max,
        market_count=market_count,
    )
    low, high = bounds if bounds else (None, None)

    def clamp(value: float | None) -> float | None:
        if value is None or low is None or high is None:
            return value
        return max(low, min(high, value))

    tiers = []
    for key in ("fast", "market", "patient"):
        tier = out.get(key)
        if isinstance(tier, dict):
            price = _coerce_positive(tier.get("price_byn") or tier.get("price"))
            if price is None:
                continue
            tiers.append((key, tier, price))

    # Reorder if non-monotonic. We trust the model's labels (`fast` etc)
    # but if its numbers don't match the labels' intent, re-sort by price
    # ascending and rewrite labels accordingly.
    if len(tiers) >= 2:
        sorted_tiers = sorted(tiers, key=lambda t: t[2])
        # If sorting changed order, rewrite each tier's label/key so the
        # cheapest is "fast", the middle is "market", the priciest is "patient".
        labels_in_input = [t[0] for t in tiers]
        labels_sorted = [t[0] for t in sorted_tiers]
        if labels_in_input != labels_sorted:
            order = ["fast", "market", "patient"]
            for idx, (_orig_key, tier_dict, price) in enumerate(sorted_tiers):
                if idx >= len(order):
                    break
                target_key = order[idx]
                clamped_price = clamp(price)
                out[target_key] = {
                    **tier_dict,
                    "price_byn": _round(clamped_price),
                    "label": _CANONICAL_TIER_LABELS.get(target_key, target_key.title()),
                }
            logger.info(
                "Listing assistant pricing tiers reordered: %s -> %s",
                labels_in_input,
                labels_sorted,
            )

    for key in ("fast", "market", "patient"):
        if key not in out:
            out[key] = {"price_byn": 0, "label": _CANONICAL_TIER_LABELS.get(key, key.title())}

    # Clamp each tier price into market bounds (no-op if no bounds).
    for key in ("fast", "market", "patient"):
        tier = out.get(key)
        if not isinstance(tier, dict):
            continue
        price = _coerce_positive(tier.get("price_byn") or tier.get("price"))
        if price is None:
            continue
        clamped = clamp(price)
        if clamped is not None and clamped != price:
            tier["price_byn"] = _round(clamped)

    # Floor: must be <= fast tier price.
    floor = _coerce_positive(out.get("floor_byn") or out.get("floor"))
    fast_price = None
    fast_tier = out.get("fast")
    if isinstance(fast_tier, dict):
        fast_price = _coerce_positive(fast_tier.get("price_byn") or fast_tier.get("price"))

    if fast_price is not None:
        if floor is None or floor > fast_price:
            # Default floor at 0.90 of the fast tier — a sensible minimum
            # for resellers when AI didn't supply one.
            out["floor_byn"] = _round(fast_price * 0.90)
        else:
            out["floor_byn"] = _round(floor)
    elif floor is not None:
        out["floor_byn"] = _round(floor)

    floor_byn = _coerce_positive(out.get("floor_byn"))
    if low is not None and floor_byn is not None and floor_byn < low:
        out["floor_byn"] = _round(low)

    return out


def thin_market_warning(market_count: int) -> str | None:
    """Return a user-visible warning when the market sample is too small.

    Sellers should know when AI's price tiers are based on 2-3 ads vs. 50.
    """
    if market_count <= 0:
        return (
            "Похожих объявлений на Kufar сейчас не нашлось — цена и план "
            "торга строятся из общих знаний AI. Добавь больше деталей в "
            "поле «Характеристики» или попробуй уточнить название."
        )
    if market_count < 5:
        return (
            f"На Kufar нашлось всего {market_count} похожих объявлений — "
            "выборка тонкая, AI учёл это, но рекомендации проверь сам."
        )
    return None
