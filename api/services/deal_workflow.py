from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from api.schemas import FlipEstimate, LiquidityInsight
from api.services.aggregator import PriceStats, filter_deal_ads, normalize_price_byn


def _age_hours(list_time: str | None) -> float | None:
    if not list_time:
        return None
    try:
        parsed = datetime.fromisoformat(list_time)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return (datetime.now(UTC) - parsed.astimezone(UTC)).total_seconds() / 3600


def compute_liquidity_insight(
    ads: list[dict[str, Any]],
    market_stats: PriceStats,
) -> LiquidityInsight:
    total = len(ads)
    fresh_count = sum(1 for ad in ads if (_age := _age_hours(ad.get("list_time"))) is not None and _age <= 72)
    deal_count = len(filter_deal_ads(ads, market_stats.median, 8.0)) if market_stats.median else 0
    score = 35.0
    reasons: list[str] = []

    if total >= 25:
        score += 22
        reasons.append("много предложений")
    elif total >= 10:
        score += 12
        reasons.append("рынок живой")
    elif total <= 4:
        score -= 10
        reasons.append("рынок тонкий")

    if fresh_count >= 8:
        score += 18
        reasons.append("много свежих лотов")
    elif fresh_count >= 3:
        score += 8
        reasons.append("свежие лоты есть")
    else:
        score -= 8
        reasons.append("мало свежих лотов")

    if deal_count >= 4:
        score += 12
        reasons.append("есть движение по дешёвым лотам")
    elif deal_count == 0 and total > 0:
        score -= 6
        reasons.append("мало выгодных входов")

    if market_stats.count <= 3:
        score -= 8
        reasons.append("выборка маленькая")

    score = round(max(0.0, min(100.0, score)), 1)
    if score >= 72:
        label = "Высокая"
    elif score >= 50:
        label = "Средняя"
    else:
        label = "Осторожно"
    return LiquidityInsight(score=score, label=label, reasons=list(dict.fromkeys(reasons))[:4])


def compute_flip_estimates(
    ad: dict[str, Any],
    market_stats: PriceStats,
) -> list[FlipEstimate]:
    price_byn = normalize_price_byn(ad.get("price_byn"))
    if price_byn is None or market_stats.count == 0:
        return []

    def build(label: str, target: float) -> FlipEstimate:
        profit = round(target - price_byn, 2)
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
