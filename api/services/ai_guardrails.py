from __future__ import annotations

import re
import statistics
from copy import deepcopy

_FINANCING_BAIT_RE = re.compile(
    r"(без\s+взноса|в\s+кредит|кредит|лизинг|рассрочк|плат[её]ж|в\s+месяц|/мес)",
    re.IGNORECASE,
)


def contains_financing_bait(*texts: str | None) -> bool:
    combined = " ".join(text for text in texts if text)
    return bool(_FINANCING_BAIT_RE.search(combined))


def _extract_prices(similar_listings: list[dict] | None) -> list[float]:
    prices: list[float] = []
    for item in similar_listings or []:
        try:
            price = float(item.get("price_byn") or 0)
        except (TypeError, ValueError):
            continue
        if price > 0:
            prices.append(price)
    return sorted(prices)


def _round_price(value: float) -> int:
    return int(round(value))


def _market_anchor(
    *,
    market_median: float | None,
    market_q1: float | None,
    market_q3: float | None,
    similar_listings: list[dict] | None,
) -> tuple[float, float, float] | None:
    prices = _extract_prices(similar_listings)
    similar_median = statistics.median(prices) if prices else None

    low = market_q1 if market_q1 and market_q1 > 0 else None
    high = market_q3 if market_q3 and market_q3 > 0 else None
    anchor = similar_median or market_median

    if low is None and prices:
        low = prices[0]
    if high is None and prices:
        high = prices[-1]

    if low is None and anchor:
        low = anchor * 0.92
    if high is None and anchor:
        high = anchor * 1.08

    if low is None or high is None:
        return None

    if anchor is None:
        anchor = statistics.median([low, high])

    return float(low), float(high), float(anchor)


def _ai_midpoint(ai_from: float | None, ai_to: float | None) -> float | None:
    if ai_from and ai_to:
        return (ai_from + ai_to) / 2
    return ai_from or ai_to


def apply_ai_market_guardrails(
    result: dict,
    *,
    title: str,
    description: str | None,
    market_median: float | None,
    market_q1: float | None,
    market_q3: float | None,
    market_count: int,
    similar_listings: list[dict] | None,
    is_negotiable_price: bool,
) -> dict:
    """Clamp obviously ungrounded AI pricing to the observed market."""
    anchor = _market_anchor(
        market_median=market_median,
        market_q1=market_q1,
        market_q3=market_q3,
        similar_listings=similar_listings,
    )
    if anchor is None or market_count < 3:
        return result

    fair_price = result.get("fair_price")
    if not isinstance(fair_price, dict):
        return result

    ai_from = fair_price.get("from")
    ai_to = fair_price.get("to")
    try:
        ai_from_num = float(ai_from) if ai_from is not None else None
        ai_to_num = float(ai_to) if ai_to is not None else None
    except (TypeError, ValueError):
        return result

    ai_mid = _ai_midpoint(ai_from_num, ai_to_num)
    if ai_mid is None:
        return result

    low, high, market_anchor = anchor
    similar_prices = _extract_prices(similar_listings)
    comparable_max = max(similar_prices) if similar_prices else high
    suspicious_ceiling = max(high * 1.35, market_anchor * 1.45, comparable_max * 1.2)

    financing_bait = contains_financing_bait(title, description)
    should_clamp = ai_mid > suspicious_ceiling
    if financing_bait and is_negotiable_price and ai_mid > high * 1.15:
        should_clamp = True

    if not should_clamp:
        return result

    corrected = deepcopy(result)
    corrected["fair_price"] = {
        "from": _round_price(low),
        "to": _round_price(high),
        "reasoning": (
            f"Диапазон заземлён по текущему рынку: медиана около "
            f"{_round_price(market_anchor)} BYN, а сопоставимые объявления лежат "
            f"примерно в пределах {_round_price(low)}-{_round_price(high)} BYN."
        ),
    }

    fast_price = low * 0.96
    market_price = market_anchor
    optimal_price = min(high * 1.04, comparable_max * 1.08)
    corrected["resale_potential"] = {
        "fast_price": {
            "label": "Быстро",
            "price_byn": _round_price(fast_price),
            "reasoning": "Быстрая продажа с дисконтом к центру рынка.",
        },
        "market_price": {
            "label": "По рынку",
            "price_byn": _round_price(market_price),
            "reasoning": "Ориентир на медиану сопоставимых объявлений.",
        },
        "optimal_price": {
            "label": "Оптимально",
            "price_byn": _round_price(optimal_price),
            "reasoning": "Верхняя реалистичная точка без отрыва от текущего рынка.",
        },
        "reasoning": (
            "Потенциал перепродажи ограничен текущими предложениями в выборке; "
            "сильный выход за этот диапазон рынок не подтверждает."
        ),
    }

    corrected["market_context"] = (
        f"По текущей выборке рынок держится около {_round_price(market_anchor)} BYN, "
        f"а сопоставимые объявления попадают в диапазон {_round_price(low)}-"
        f"{_round_price(high)} BYN. "
        "Сильный выход за этот коридор по данным рынка не подтверждается."
    )
    corrected["summary"] = (
        f"Ориентир по рынку для этого объявления — примерно "
        f"{_round_price(low)}-{_round_price(high)} BYN. "
        "Цена заметно выше этого диапазона текущей выборкой не подтверждается."
    )

    recommendation = corrected.get("recommendation")
    if isinstance(recommendation, dict):
        recommendation["text"] = (
            f"Опирайтесь на рынок {_round_price(low)}-{_round_price(high)} BYN и "
            "не принимайте завышенную оценку без подтверждения аналогами."
        )

    return corrected
