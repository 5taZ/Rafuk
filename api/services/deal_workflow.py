from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from api.schemas import FlipEstimate, LiquidityInsight
from api.services.aggregator import PriceStats, get_param, normalize_price_byn


def _age_hours(list_time: str | None) -> float | None:
    if not list_time:
        return None
    try:
        parsed = datetime.fromisoformat(list_time.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return (datetime.now(UTC) - parsed.astimezone(UTC)).total_seconds() / 3600


def _photo_count(ad: dict[str, Any]) -> int:
    photos = ad.get("images") or ad.get("photos") or []
    if isinstance(photos, list):
        return len(photos)
    if isinstance(photos, str):
        return 1
    return 0


def _market_spread(market_stats: PriceStats) -> float | None:
    median = market_stats.median
    if median <= 0 or market_stats.q3 < market_stats.q1:
        return None
    return (market_stats.q3 - market_stats.q1) / median


def _add_reason(reasons: list[tuple[float, int, str]], weight: float, text: str) -> None:
    reasons.append((abs(weight), len(reasons), text))


def _final_reasons(reasons: list[tuple[float, int, str]]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for _, _, text in sorted(reasons, key=lambda item: (-item[0], item[1])):
        if text in seen:
            continue
        seen.add(text)
        result.append(text)
        if len(result) == 4:
            break
    return result


def compute_liquidity_insight(
    ads: list[dict[str, Any]],
    market_stats: PriceStats,
    ad: dict[str, Any] | None = None,
) -> LiquidityInsight:
    total = len(ads)
    priced_count = min(max(0, int(market_stats.count or 0)), total)
    fresh_72h = sum(
        1 for a in ads if (_age := _age_hours(a.get("list_time"))) is not None and _age <= 72
    )
    fresh_share = fresh_72h / total if total else 0.0
    spread = _market_spread(market_stats)
    market_score = 0.0
    reasons: list[tuple[float, int, str]] = []

    if priced_count >= 40:
        market_score += 12
        _add_reason(reasons, 12, "много ценовых ориентиров")
    elif priced_count >= 15:
        market_score += 8
        _add_reason(reasons, 8, "рынок достаточно широкий")
    elif priced_count >= 6:
        market_score += 3
    elif priced_count > 0:
        market_score -= 8
        _add_reason(reasons, -8, "мало ценовых ориентиров")
    else:
        market_score -= 18
        _add_reason(reasons, -18, "нет ценовых ориентиров")

    if total >= 6 and fresh_share >= 0.35:
        market_score += 12
        _add_reason(reasons, 12, "рынок быстро обновляется")
    elif total >= 6 and fresh_share >= 0.15:
        market_score += 6
        _add_reason(reasons, 6, "свежие лоты появляются регулярно")
    elif total > 0 and fresh_72h == 0:
        market_score -= 8
        _add_reason(reasons, -8, "нет свежего движения")
    elif total < 6:
        market_score -= 8
        _add_reason(reasons, -8, "мало сопоставимых объявлений")

    if spread is not None and priced_count >= 6:
        if spread <= 0.20:
            market_score += 6
            _add_reason(reasons, 6, "цены предсказуемые")
        elif spread <= 0.40:
            market_score += 2
        elif spread >= 0.80:
            market_score -= 10
            _add_reason(reasons, -10, "разброс цен очень высокий")
        elif spread >= 0.55:
            market_score -= 5
            _add_reason(reasons, -5, "разброс цен высокий")

    if total >= 80 and fresh_share < 0.20:
        market_score -= 5
        _add_reason(reasons, -5, "много конкурентов без быстрого обновления")

    if ad is None:
        score = 50.0 + market_score
    else:
        item_score = 0.0
        price_byn = normalize_price_byn(ad.get("price_byn"), ad)
        median = market_stats.median
        ratio: float | None = None
        caps: list[float] = []

        if price_byn is not None and median and median > 0:
            ratio = price_byn / median
            if ratio <= 0.75:
                item_score += 26
                _add_reason(reasons, 26, "цена сильно ниже рынка")
            elif ratio <= 0.88:
                item_score += 18
                _add_reason(reasons, 18, "цена ниже рынка")
            elif ratio <= 0.98:
                item_score += 9
                _add_reason(reasons, 9, "цена чуть ниже рынка")
            elif ratio <= 1.05:
                item_score += 2
            elif ratio <= 1.15:
                item_score -= 12
                caps.append(58.0)
                _add_reason(reasons, -12, "цена выше рынка")
            else:
                item_score -= 26
                caps.append(42.0)
                _add_reason(reasons, -26, "цена сильно выше рынка")
        elif price_byn is None:
            item_score -= 18
            caps.append(45.0)
            _add_reason(reasons, -18, "цена не указана")
        elif not median:
            item_score -= 6
            caps.append(60.0)
            _add_reason(reasons, -6, "нет медианы рынка")

        age = _age_hours(ad.get("list_time"))
        if age is not None and age >= 0:
            if age <= 12:
                item_score += 8
                _add_reason(reasons, 8, "новое объявление")
            elif age <= 72:
                item_score += 4
                _add_reason(reasons, 4, "свежее объявление")
            elif age >= 336:
                item_score -= 18
                caps.append(50.0)
                _add_reason(reasons, -18, "залежалось больше двух недель")
            elif age >= 168:
                item_score -= 10
                _add_reason(reasons, -10, "залежалось больше недели")
            else:
                item_score -= 3

        photo_count = _photo_count(ad)
        if photo_count >= 5:
            item_score += 6
            _add_reason(reasons, 6, "много фото")
        elif photo_count >= 1:
            item_score += 2
        else:
            item_score -= 10
            caps.append(65.0)
            _add_reason(reasons, -10, "нет фото")

        condition = get_param(ad, "condition") or ""
        is_new = condition in ("2", "новый", "new")
        if is_new and (ratio is None or ratio <= 1.05):
            item_score += 4
            _add_reason(reasons, 4, "новый товар")
        elif is_new:
            item_score += 1

        score = 50.0 + market_score + item_score
        if priced_count < 3 or total < 3:
            caps.append(52.0)
            _add_reason(reasons, -10, "выборка маленькая")
        elif priced_count < 8 or total < 8:
            caps.append(68.0)
            _add_reason(reasons, -6, "оценка по небольшой выборке")
        if caps:
            score = min(score, min(caps))

    score = round(max(0.0, min(100.0, score)), 1)
    if score >= 75:
        label = "Высокая"
    elif score >= 55:
        label = "Средняя"
    elif score >= 40:
        label = "Низкая"
    else:
        label = "Очень низкая"
    return LiquidityInsight(score=score, label=label, reasons=_final_reasons(reasons))


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
    # PriceStats fields should be in BYN; if median > 100000 the caller
    # likely passed raw kopecks.
    if market_stats.median > 100_000:
        raise ValueError(
            f"PriceStats.median={market_stats.median} looks like kopecks, not BYN"
        )
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
