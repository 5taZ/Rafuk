"""Lightweight quality checks for AI advice text."""

from __future__ import annotations

import re
from collections.abc import Iterable

from api.services.ai_sanitize import sanitize_user_text

_WORD_RE = re.compile(r"[0-9a-zа-яё]+", re.IGNORECASE)

_GENERIC_PHRASES = (
    "обратите внимание",
    "обрати внимание",
    "проверь состояние товара",
    "проверьте состояние товара",
    "проверь детали",
    "уточните детали",
    "уточни детали",
    "попросите скидку",
    "попроси скидку",
    "сделайте хорошие фото",
    "сделай хорошие фото",
    "отличный товар",
    "хороший товар",
    "подойдет для всех",
    "подойдёт для всех",
)

_GENERIC_TOKENS = {
    "актуальный",
    "важно",
    "внимание",
    "вопросы",
    "детали",
    "качество",
    "отличный",
    "покупателя",
    "подойдет",
    "подойдёт",
    "проверь",
    "проверьте",
    "продавца",
    "продажи",
    "скидку",
    "состояние",
    "товар",
    "товара",
    "уточни",
    "уточните",
    "фото",
    "хорошие",
    "хороший",
}

_SPECIFIC_STEMS = (
    "activation",
    "imei",
    "lock",
    "smart",
    "ssd",
    "vin",
    "аккумуля",
    "батар",
    "байонет",
    "бирк",
    "вакцин",
    "вредител",
    "двигател",
    "диск",
    "документ",
    "заряд",
    "камер",
    "коренн",
    "корнев",
    "короб",
    "корпус",
    "креп",
    "кузов",
    "листь",
    "люфт",
    "маркир",
    "матриц",
    "молни",
    "памят",
    "петл",
    "пиксел",
    "порт",
    "привив",
    "пробег",
    "разъ",
    "ремеш",
    "серийн",
    "ткан",
    "трещин",
    "фурнит",
    "царап",
    "экран",
)


def _normalise(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").casefold()).strip()


def _tokens(value: str) -> list[str]:
    return _WORD_RE.findall(_normalise(value))


def _has_specific_anchor(value: str) -> bool:
    text = _normalise(value)
    if re.search(r"\d", text) or "byn" in text or "руб" in text:
        return True
    return any(stem in text for stem in _SPECIFIC_STEMS)


def is_generic_ai_advice(value: str | None) -> bool:
    text = _normalise(value or "")
    if len(text) < 8:
        return True
    has_anchor = _has_specific_anchor(text)
    if any(phrase in text for phrase in _GENERIC_PHRASES):
        return not has_anchor
    tokens = _tokens(text)
    if len(tokens) <= 4 and not has_anchor:
        return True
    return bool(tokens) and not has_anchor and all(token in _GENERIC_TOKENS for token in tokens)


def filter_specific_advice(items: Iterable[str], *, limit: int, max_len: int) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        text = sanitize_user_text(str(item or ""), max_length=max_len) or ""
        text = text.strip()
        if not text or is_generic_ai_advice(text):
            continue
        key = _normalise(text)
        if key in seen:
            continue
        seen.add(key)
        out.append(text)
        if len(out) >= limit:
            break
    return out


def _fill_specific(
    current: list[str],
    fallbacks: Iterable[str],
    *,
    limit: int,
    max_len: int,
) -> list[str]:
    out = list(current)
    seen = {_normalise(item) for item in out}
    for item in fallbacks:
        text = sanitize_user_text(str(item or ""), max_length=max_len) or ""
        text = text.strip()
        if not text or is_generic_ai_advice(text):
            continue
        key = _normalise(text)
        if key in seen:
            continue
        seen.add(key)
        out.append(text)
        if len(out) >= limit:
            break
    return out


def repair_seller_advice(
    *,
    title: str,
    condition: str | None,
    market_count: int,
    selling_points: Iterable[str],
    photo_tips: Iterable[str],
) -> tuple[list[str], list[str]]:
    safe_title = sanitize_user_text(title, max_length=80) or "товар"
    safe_condition = sanitize_user_text(condition, max_length=64) if condition else ""

    specific_points = filter_specific_advice(selling_points, limit=6, max_len=160)
    point_fallbacks = [
        f"Укажи точную модель в заголовке: {safe_title}.",
        (
            f"Опиши состояние как «{safe_condition}»: следы использования, батарею, "
            "экран или комплект."
            if safe_condition
            else "Добавь конкретику по состоянию: экран, батарея, комплект или маркировка."
        ),
        (
            f"Цена сверена с {market_count} похожими объявлениями Kufar."
            if market_count > 0 else ""
        ),
        "Уточни комплект, гарантию, причину продажи и удобные условия встречи.",
    ]
    specific_points = _fill_specific(
        specific_points,
        point_fallbacks,
        limit=6,
        max_len=160,
    )

    specific_photos = filter_specific_advice(photo_tips, limit=5, max_len=140)
    photo_fallbacks = [
        f"Покажи {safe_title}: общий вид, экран или корпус и маркировку крупно.",
        "Сними крупно следы использования, разъёмы, комплект и серийную маркировку.",
        "Добавь кадр при дневном свете без фильтров: общий план и 2-3 детали состояния.",
    ]
    specific_photos = _fill_specific(
        specific_photos,
        photo_fallbacks,
        limit=5,
        max_len=140,
    )
    return specific_points, specific_photos
