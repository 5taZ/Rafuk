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
    ad: dict[str, Any] | None = None,
) -> LiquidityInsight:
    total = len(ads)
    fresh_count = sum(
        1 for a in ads
        if (_age := _age_hours(a.get("list_time"))) is not None and _age <= 72
    )
    deal_count = len(filter_deal_ads(ads, market_stats.median, 8.0)) if market_stats.median else 0

    # Market-level base score (0-40)
    market_score = 0.0
    reasons: list[str] = []

    if total >= 25:
        market_score += 20
        reasons.append("много предложений")
    elif total >= 10:
        market_score += 10
        reasons.append("рынок живой")
    elif total <= 4:
        market_score -= 8
        reasons.append("рынок тонкий")

    if fresh_count >= 8:
        market_score += 12
        reasons.append("много свежих лотов")
    elif fresh_count >= 3:
        market_score += 5
        reasons.append("свежие лоты есть")
    else:
        market_score -= 5
        reasons.append("мало свежих лотов")

    if deal_count >= 4:
        market_score += 8
        reasons.append("есть движение по дешёвым лотам")
    elif deal_count == 0 and total > 0:
        market_score -= 4
        reasons.append("мало выгодных входов")

    if market_stats.count <= 3:
        market_score -= 6
        reasons.append("выборка маленькая")

    # If no specific ad given, return market-level score only
    if ad is None:
        score = round(max(0.0, min(100.0, 30.0 + market_score)), 1)
    else:
        # Per-item score: market base + item-specific factors
        item_score = 0.0
        price_byn = normalize_price_byn(ad.get("price_byn"))
        median = market_stats.median

        # Price position relative to median (0-25 points)
        if price_byn and median and median > 0:
            ratio = price_byn / median
            if ratio <= 0.80:
                item_score += 25
                reasons.append("цена сильно ниже рынка")
            elif ratio <= 0.90:
                item_score += 18
                reasons.append("цена ниже рынка")
            elif ratio <= 0.97:
                item_score += 10
                reasons.append("цена чуть ниже рынка")
            elif ratio <= 1.03:
                item_score += 5
            elif ratio <= 1.10:
                item_score -= 5
                reasons.append("цена выше рынка")
            else:
                item_score -= 12
                reasons.append("цена сильно выше рынка")

        # Freshness of this specific ad (0-15 points)
        age = _age_hours(ad.get("list_time"))
        if age is not None:
            if age <= 6:
                item_score += 15
                reasons.append("только что выложено")
            elif age <= 24:
                item_score += 10
                reasons.append("свежее объявление")
            elif age <= 72:
                item_score += 4
            elif age <= 168:
                item_score -= 3
            else:
                item_score -= 8
                reasons.append("давно на рынке")

        # Photo count bonus (0-10 points)
        photos = ad.get("images") or ad.get("photos") or []
        if isinstance(photos, (list, str)):
            photo_count = len(photos) if isinstance(photos, list) else 1
        else:
            photo_count = 0
        if photo_count >= 5:
            item_score += 10
        elif photo_count >= 3:
            item_score += 6
        elif photo_count >= 1:
            item_score += 3
        else:
            item_score -= 5
            reasons.append("нет фото")

        # Condition bonus (0-10 points)
        condition = str(ad.get("condition", "")).lower()
        if condition in ("новый", "2", "new"):
            item_score += 10
            if "новый" not in " ".join(reasons).lower():
                reasons.append("новый товар")
        elif condition in ("б/у", "1", "used"):
            item_score += 3

        score = round(max(0.0, min(100.0, 25.0 + market_score + item_score)), 1)

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
