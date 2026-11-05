from __future__ import annotations

import logging
from typing import Any

from api.schemas import FlipEstimate
from api.services.aggregator import MAX_PRICE_BYN, PriceStats, normalize_price_byn

logger = logging.getLogger(__name__)


def compute_flip_estimates(
    ad: dict[str, Any],
    market_stats: PriceStats,
    *,
    expenses_byn: float = 0.0,
) -> list[FlipEstimate]:
    """Compute three resale-target tiers (quick / market / optimal).

    E-FIND-08: ``expenses_byn`` is the user's recorded out-of-pocket
    cost (delivery, repair, packaging, customs, …). Subtracting it
    from each tier's profit makes the result a *net* projection
    instead of gross — a 200 BYN gross flip after 150 BYN of
    fix-up costs is a 50 BYN net deal, and the UI should reflect
    that. The argument is keyword-only and defaults to 0.0 so
    existing callers stay binary-compatible (the projection just
    keeps showing gross until the caller wires expenses through).
    """
    price_byn = normalize_price_byn(ad.get("price_byn"))
    if price_byn is None or market_stats.count == 0:
        return []
    # H6: guard against kopecks being passed instead of BYN.
    # BE-02: the old 100 000 cut-off rejected real BYN medians (flats, cars)
    # and raised, turning /listing-detail into a 500. Stats are built from
    # prices capped at MAX_PRICE_BYN, so only a median above that cap means
    # kopecks — and then the card just goes without flip estimates.
    if market_stats.median > MAX_PRICE_BYN:
        logger.warning("PriceStats.median=%s looks like kopecks, not BYN", market_stats.median)
        return []
    expenses = max(0.0, float(expenses_byn or 0.0))
    cost_basis = price_byn + expenses

    def build(label: str, target: float) -> FlipEstimate:
        profit = round(target - cost_basis, 2)
        # Profit-percent denominator stays at price_byn so the % stays
        # comparable to the listing price rather than the user-specific
        # expenses stack. Switching to cost_basis would conflate
        # "this listing's flip potential" with "this user's planned
        # rework" and confuse the comparison across users.
        profit_percent = round((profit / price_byn) * 100.0, 2) if price_byn else 0.0
        return FlipEstimate(
            label=label,
            target_price=round(target, 2),
            profit_byn=profit,
            profit_percent=profit_percent,
        )

    quick_target = max(market_stats.q1, market_stats.median * 0.94)
    market_target = market_stats.median
    optimal_target = max(market_stats.q3, market_stats.median * 1.06)
    return [
        build("Быстро", quick_target),
        build("По рынку", market_target),
        build("Оптимально", optimal_target),
    ]
