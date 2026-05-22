"""User intent profiles injected into AI prompts."""

from __future__ import annotations

BUYER_GOAL_BALANCED = "balanced"
BUYER_GOAL_SAFE = "safe_buy"
BUYER_GOAL_RESALE = "resale"
SELLER_GOAL_BALANCED = "balanced"
SELLER_GOAL_FAST = "sell_fast"
SELLER_GOAL_MAX_PRICE = "maximize_price"

_BUYER_INTENTS: dict[str, str] = {
    BUYER_GOAL_SAFE: (
        "## ЦЕЛЬ ПОЛЬЗОВАТЕЛЯ\n"
        "Покупатель хочет безопасную покупку. Усиль проверку на мошенничество, "
        "документы, скрытые дефекты, условия встречи, подмену товара и признаки "
        "давления со стороны продавца."
    ),
    BUYER_GOAL_RESALE: (
        "## ЦЕЛЬ ПОЛЬЗОВАТЕЛЯ\n"
        "Пользователь оценивает перепродажу. Усиль блоки: цена входа, маржа, ликвидность, "
        "срок продажи, риск зависнуть с товаром и минимальную цену торга."
    ),
}

_SELLER_INTENTS: dict[str, str] = {
    SELLER_GOAL_FAST: (
        "## ЦЕЛЬ ПРОДАВЦА\n"
        "Продавец хочет быструю продажу. Делай цену и текст под минимум трения: "
        "честное состояние, простые условия встречи, понятные фото и небольшой запас "
        "для торга."
    ),
    SELLER_GOAL_MAX_PRICE: (
        "## ЦЕЛЬ ПРОДАВЦА\n"
        "Продавец хочет максимальную цену. Делай текст под терпеливую продажу: "
        "сильное позиционирование, аргументы ценности, качественные фото и аккуратный "
        "нижний порог торга."
    ),
}


def buyer_intent_text(goal: str | None) -> str:
    return _BUYER_INTENTS.get(goal or "", "")


def seller_intent_text(goal: str | None) -> str:
    return _SELLER_INTENTS.get(goal or "", "")
