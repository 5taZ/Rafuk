"""AI service — OpenAI-compatible API (Google Gemini, Together AI, etc.).

Configure via .env:
  AI_API_KEY=<key>
  AI_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai
  AI_MODEL=gemini-3-flash
"""

from __future__ import annotations

import asyncio
import functools
import json
import logging
import re
from typing import Any
from urllib.parse import urlparse

import httpx

from api.config import get_settings
from api.services.ai_category_data import (  # noqa: F401
    _VALID_CONDITION_LABELS,
    CATEGORY_HINTS,
    CATEGORY_KEYWORDS,
    _normalize_condition_label,
    detect_category,
    normalize_condition_label,
)

logger = logging.getLogger(__name__)

_KUFAR_IMAGE_HOST_RE = re.compile(r"^rms\d*\.kufar\.by$")
_MAX_AI_IMAGE_BYTES = 5_000_000
_MAX_AI_REDIRECTS = 2


def _entry_price_guidance(
    *,
    market_median: float | None,
    market_q1: float | None,
    market_q3: float | None,
    similar_listings: list[dict] | None,
) -> tuple[int, int] | None:
    priced = [
        float(item.get("price_byn") or 0)
        for item in (similar_listings or [])
        if float(item.get("price_byn") or 0) > 0
    ]
    priced = sorted(priced)

    low_anchor = market_q1 if market_q1 and market_q1 > 0 else (priced[0] if priced else None)
    high_anchor = market_median if market_median and market_median > 0 else None
    if high_anchor is None and priced:
        high_anchor = priced[min(len(priced) - 1, max(0, len(priced) // 2))]
    if low_anchor is None and market_q3 and market_q3 > 0:
        low_anchor = market_q3 * 0.9
    if high_anchor is None and market_q3 and market_q3 > 0:
        high_anchor = market_q3

    if low_anchor is None or high_anchor is None:
        return None
    if high_anchor < low_anchor:
        low_anchor, high_anchor = high_anchor, low_anchor

    return int(round(low_anchor)), int(round(high_anchor))


# ── Parallel analysis sub-prompts ────────────────────────────────────────
# The monolithic analyze_listing call is split into two parallel calls:
# TODO: Extract prompt templates to separate .txt/.md files under
#   api/services/prompts/ and load at startup.  This file is ~1500 lines
#   largely due to inline prompts, which makes maintenance difficult.
#
#   Call A — Price & Market: fair_price, resale_potential, market_context,
#            negotiation_tips, best_pick
#   Call B — Condition & Risks: condition, watch_out, meeting_checklist,
#            red_flags, recommendation, summary
# Both receive the same listing context (text + images when available) and
# produce different JSON sections, using 3200/4000 max_tokens for Gemini
# 2.5 Flash (which uses thinking tokens). Running concurrently means
# wall-clock time ≈ max(A, B), not A + B.

_PRICE_MARKET_PROMPT_TEMPLATE = """\
Ты — Rafuk AI, эксперт-аналитик объявлений Kufar.by. Отвечай ТОЛЬКО на русском.

Оцени ЦЕНУ и РЫНОК. Правила:
- Конкретно: суммы в BYN, сроки, проценты. Без воды.
- fair_price.from/to — диапазон для ЭТОГО товара в ЕГО состоянии.
  reasoning: сравни с конкретными аналогами из АЛЬТЕРНАТИВ (ad_id, цена, отличие).
  НЕ выдумывай диапазон — опирайся на медиану, Q1-Q3 и аналоги из контекста.
  Если аналогов нет — честно укажи, что данных мало.
- negotiation_tips — МИНИМУМ 3 аргумента с BYN-суммами. Опирайся на разницу
  с аналогами и дефекты из condition.notes/watch_out.
- resale_potential: fast/market/optimal — три цены перепродажи в BYN.
  fast — ниже рынка для быстрой продажи; market — справедливая; optimal — максимум.
  reasoning: за сколько можно перепродать и почему, с учётом состояния и спроса.
- market_context — 2-3 предложения: позиция цены, лучший аналог, тренд.
- best_pick — ad_id лучшей альтернативы из АЛЬТЕРНАТИВ (или null).

ФОТО: Если приложены фотографии — учитывай видимые дефекты при расчёте
fair_price и resale_potential. Состояние на фото влияет на цену.

Цена товара {price_position_label}. {category_hints} {bargain_hint}
Ответь строго JSON:
"""

_PRICE_MARKET_SCHEMA = """{
  "fair_price": {
    "from": число_BYN,
    "to": число_BYN,
    "reasoning": "обоснование: аналог [ad_id] стоит X BYN в состоянии Y, этот — потому что..."
  },
  "resale_potential": {
    "fast_price": {"label": "Быстро", "price_byn": число, "reasoning": "почему"},
    "market_price": {"label": "По рынку", "price_byn": число, "reasoning": "почему"},
    "optimal_price": {"label": "Оптимально", "price_byn": число, "reasoning": "почему"},
    "reasoning": "общее обоснование: за сколько можно перепродать и почему"
  },
  "negotiation_tips": ["аргумент: 'Скиньте X BYN, потому что...' с суммой"],
  "market_context": "2-3 предложения: позиция цены, конкретные аналоги, тренд",
  "best_pick": {"ad_id": номер_или_null, "reason": "почему именно этот вариант лучше"}
}"""

_CONDITION_RISKS_PROMPT_TEMPLATE = """\
Ты — Rafuk AI, эксперт-аналитик объявлений Kufar.by. Отвечай ТОЛЬКО на русском.

Оцени СОСТОЯНИЕ и РИСКИ. Правила:
- Конкретно: дефекты, модели, проценты. Без воды.
- condition.notes — МИНИМУМ 2 наблюдения по фото/описанию.
  Если есть БЫСТРЫЙ ФОТО-ОСМОТР — НЕ повторяй его дословно. Добавляй НОВЫЕ детали.
- watch_out — МИНИМУМ 3 конкретных риска (point + why). Не дублируй condition.notes.
- meeting_checklist — МИНИМУМ 5 проверок при встрече, специфичных для категории.
- red_flags — максимум 3 реальных признака мошенничества/проблем, не очевидности.
  Учитывай hot words из РИСК-СИГНАЛОВ (кредит, перекуп, автохаус), но не выдумывай.
- scam_analysis: risk_level (low/medium/high), indicators, photo_issues, advice.
- photo_authenticity: сток/водяной знак/скриншот/дубликат — проверяй все.
- recommendation.verdict — строго: worth_it / think_twice / overpriced.
  Если цена договорная — опирайся на реалистичный диапазон входа после торга.
- summary — 1-2 предложения: вердикт + ключевая причина.

ПРОВЕРЯЙ НА МОШЕННИЧЕСТВО (Kufar.by):
- Цена >30% ниже медианы без обоснования — подозрительно.
- Мало фото (1-2) или только стоковые/каталожные фото без реальных снимков.
- Скопированное описание, несоответствие модели заявленной.
- Водяные знаки других сайтов (olx, av.by, 21vek, 5element).
- «Под заказ», «доставка из-за границы» — типичная схема на Kufar.
- Отсутствие параметров (пробег, год, объём) для дорогих товаров.
- Несовпадение названия города в описании и регионе объявления.
- Если ПРИЛОЖЕНЫ ФОТО — проверяй: сток/скриншот/водяной знак/дубликат/размытие.

ФОТО-АНАЛИЗ: Если к сообщению приложены фотографии — используй их для:
- condition.notes: конкретные дефекты на фото (царапины, сколы, вмятины).
- photo_authenticity: стоковые фото, скриншоты, водяные знаки, дубликаты.
- scam_analysis: несоответствие фото описанию, каталожные фото вместо реальных.
- watch_out: дефекты, которые не видны на фото, но вероятны для категории.

Цена товара {price_position_label}. {category_hints} {bargain_hint}
Ответь строго JSON:
"""

_CONDITION_RISKS_SCHEMA = """{
  "condition": {
    "label": "строго на русском: Отличное, Хорошее, Удовлетворительное или Требует внимания",
    "confidence": 0.0-1.0,
    "notes": ["конкретное наблюдение с фото или описания — что именно видно"]
  },
  "watch_out": [
    {"point": "конкретная проблема", "why": "почему важно и как проверить"}
  ],
  "meeting_checklist": ["конкретное действие — что нажать, подключить, проверить"],
  "red_flags": ["конкретный признак мошенничества или проблемы"],
  "scam_analysis": {
    "risk_level": "low или medium или high",
    "indicators": ["конкретный признак мошенничества"],
    "photo_issues": ["конкретная проблема с фото: сток, водяной знак, скриншот"],
    "seller_warnings": ["предупреждение о продавце"],
    "advice": "рекомендация по безопасности сделки"
  },
  "photo_authenticity": {
    "stock_photo_detected": true/false,
    "duplicate_image_detected": true/false,
    "watermark_detected": true/false,
    "screenshot_detected": true/false,
    "issues": ["конкретная проблема с фото"],
    "confidence": 0.0-1.0
  },
  "recommendation": {
    "verdict": "worth_it или think_twice или overpriced (строго одно из трёх)",
    "text": "рекомендация с суммой и действием на русском"
  },
  "summary": "1-2 предложения: вердикт + ключевая причина"
}"""


LISTING_ASSISTANT_PROMPT = """\
Ты — Rafuk AI, помощник продавцу на белорусском Kufar. Ты помогаешь
составить КОНКРЕТНОЕ объявление: заголовок, описание, цену и план торга.

═══ КРИТИЧЕСКИЕ ПРАВИЛА ═══

АНТИ-ГАЛЛЮЦИНАЦИЯ (САМОЕ ВАЖНОЕ ПРАВИЛО):
- ЗАПРЕЩЕНО выдумывать характеристики, которых нет в исходных данных.
- Запрещено: год выпуска (2023, 2024 и т.д.), поколение, ёмкость батареи,
  пробег, дату покупки, страну сборки, версию прошивки, вес, габариты
  и любые технические характеристики — ЕСЛИ их не указал продавец.
- Пример: если продавец написал "Realme Buds Air 8" — НЕ добавляй
  "выпущены в 2023 году" или "Bluetooth 5.3" или "до 36 часов работы".
  Пиши только то, что знаешь наверняка из названия и фото.
- Если на фото видна коробка/упаковка — используй только то, что на ней
  ЧЁТКО и однозначно написано. Нечитаемый текст = нет данных.
- Если чего-то не хватает — НЕ придумывай. Вместо этого добавь
  в selling_points пункт "Уточни: ..." чтобы продавец сам заполнил.
- Модель определяется ТОЛЬКО по тому, что написал продавец в названии
  или что видимо на фото. Если продавец написал "наушники" без модели —
  не добавляй модель от себя.

ЕСТЕСТВЕННЫЙ ТЕКСТ:
- Пиши описание так, как обычный человек составляет объявление на Kufar.
- НЕ используй типичные ИИ-штампы: "идеальное состояние", "отличный
  экземпляр", "превосходное качество", "не упустите возможность",
  "отличный выбор", "надёжный помощник", "именно то, что вы ищете".
- Вместо рекламных формулировок — конкретные факты: "пользовался 8
  месяцев", "без царапин", "все функции работают", "включается, звук
  чистый".
- Описание должно звучать как от реального человека, а не маркетолога.
  Разговорный белорусский/русский, без пафоса.
- selling_points тоже пиши человеческим языком, не лозунгами.

═══ ОСНОВНЫЕ ПРАВИЛА ═══

- Аудитория — Беларусь, Kufar. Все цены в BYN.
- Заголовок: 50-80 символов, без CAPSLOCK, без эмодзи. Основное в начале
  (модель / марка / ключевая характеристика). Ниша Kufar — поиск
  по ключевым словам, поэтому модель и параметры важнее эпитетов.
- Описание: 350-650 символов, абзацы по 1-2 предложения, без рекламной воды,
  с конкретикой. В конце — условия встречи (район, возможность торга,
  оплата). Без телефонов и личных данных.
- selling_points: 3-5 буллетов «что выгодно подсветить покупателю».
- Цена: ВСЕГДА три tier'a — fast / market / patient — и обязательное
  floor_byn (минимум, ниже которого нельзя падать). Цены целые, в BYN.
  ПРАВИЛО ЦЕНООБРАЗОВАНИЯ:
  1. Цены ВСЕГДА привязаны к разделу ТОП КОНКУРЕНТОВ.
   2. fast ≈ нижний квартиль (Q1) рынка × 0.90-0.95 (быстрая продажа,
      но не демпинг).
  3. market ≈ среднее арифметическое цен конкурентов.
   4. patient ≈ верхний квартиль (Q3) рынка × 1.0-1.10.
  5. floor_byn ≈ fast × 0.90.
  6. Если конкурентов 1-2 — fast не ниже самого дешёвого конкурента
     минус 10%. Если конкурент один за 300 BYN — fast ≈ 270, market ≈ 300.
  7. ЗАПРЕЩЕНО: предлагать цену ниже 80% от самого дешёвого конкурента.
  8. reasoning в каждом tier'е: укажи от какой цены конкурента
     отталкиваешься.
  Если черновая цена продавца сильно занижена/завышена — скажи это явно
  в reasoning соответствующего tier'a.
- weeks_to_sell — словами: «1-2 недели», «3-4 недели», «1-2 месяца».
- Anti-lowball: 3-4 сценария «что отвечать, когда…». Сценарии должны быть
  конкретными к данному товару (не «отказывайтесь вежливо»). Используй
  реальные суммы из рыночного контекста: «Если предлагают 1200 при медиане
  1400 — ответь: ...». Суммы в сценариях должны быть РЕАЛИСТИЧНЫМИ —
  основывайся на ценах конкурентов, а не на абстрактных процентах.
  Если на рынке всего 2 конкурента за 250 и 300 BYN — торг идёт
  в диапазоне 200-300, а не 100-150. Отвечать тоже как живой человек —
  без формальностей.
- photo_tips: 2-4 коротких совета по фото (свет, ракурсы, что обязательно
  показать). Без банальностей вроде «фотографируйте красиво». Если фото
  ПРИЛОЖЕНЫ — обязательно прокомментируй их: что снято хорошо, что
  переснять, чего не хватает.
- Если фото приложены — используй их при оценке состояния и selling_points
  («Видно скол на левом верхнем углу» и т.п.). Если заявленное состояние
  расходится с фото — отметь это в selling_points через «Уточни …».
- market_summary: 1-2 предложения, что происходит на рынке. Честно и без
  общих фраз.

КОНКУРЕНТЫ (обязательное поле):
- competitors: список до 4 ближайших конкурентов из рынка. Для каждого:
  title — название объявления конкурента (из контекста),
  price_byn — цена в BYN (из контекста),
  advantage — одно короткое предложение, чем ВАШЕ объявление лучше
  или хуже (честно). Пиши как продавец бы объяснил другу.
- Если конкуренты есть в контексте — обязательно используй их данные.
  НЕ выдумывай цены конкурентов — бери только из раздела ТОП КОНКУРЕНТОВ.

SEO-ОПТИМИЗАЦИЯ ПОД KUFAR:
- Поиск Kufar работает по полнотекстовому совпадению ключевых слов.
  Заголовок — самый важный фактор ранжирования.
- Включи в заголовок: бренд + модель + ключевой параметр + состояние.
  Порядок: самое важное слово ПЕРВЫМ (покупатели видят обрезанный текст).
- В описание добавь СИНОНИМЫ и АЛЬТЕРНАТИВНЫЕ НАЗВАНИЯ товара для
  попадания в больше поисковых запросов. Примеры:
  * iPhone → "iPhone / айфон"
  * MacBook → "MacBook / макбук"
  * PlayStation → "PlayStation / плойка / PS"
  * Samsung Galaxy → "Samsung Galaxy / самсунг"
- Упомяни город/район в описании — Kufar фильтрует по региону, но
  текстовое упоминание повышает кликабельность.
- НЕ используй спецсимволы (★, ✅, ❗) — Kufar может обрезать их
  или пессимизировать в поиске.
- Добавь в description_short ключевые слова для превью-сниппета.

Отвечай ТОЛЬКО JSON без markdown. Все строки — на русском.

Структура:
{
  "title_suggestion": "...",
  "description": "...",
  "description_short": "...",
  "selling_points": ["...", "..."],
  "pricing": {
    "fast":    {"label": "Быстро",    "price_byn": 1200, "weeks_to_sell": "1-2 нед"},
    "market":  {"label": "Рынок",     "price_byn": 1350, "weeks_to_sell": "3-4 нед"},
    "patient": {"label": "Терпеливо", "price_byn": 1500, "weeks_to_sell": "1-2 мес"},
    "floor_byn": 1100
  },
  "negotiation_playbook": [
    {"scenario": "Предлагают 1100 при медиане 1350", "response": "..."},
    ...
  ],
  "photo_tips": ["...", "..."],
  "competitors": [
    {"title": "Название конкурирующего объявления", "price_byn": 130,
     "advantage": "У нас дешевле на 20 BYN, плюс чехол в комплекте"}
  ],
  "market_summary": "..."
}
"""


QUICK_CONDITION_PROMPT = """\
Ты — Rafuk AI. Оцени состояние товара по фото.
Выбери РОВНО ОДНО значение condition из списка:
- Отличное
- Хорошее
- Удовлетворительное
- Требует внимания

ДЕТЕКЦИЯ ДЕФЕКТОВ:
- Внимательно осмотри фото на наличие дефектов: сколы, царапины, трещины,
  вмятины, потёртости, пятна, следы ремонта, следы воды/коррозии.
- Для электроники: проверь экран на битые пиксели/царапины, разъёмы,
  клавиатуру, петли крышки, состояние батареи (если видна).
- Для авто/мото: проверь кузов, колёса, салон, приборную панель.
- Для мебели: проверь обивку, ножки, фурнитуру, поверхность.
- Если дефект найден — обязательно укажи его в notes с конкретным
  расположением: «скол на правом нижнем углу», «царапина на задней
  панели слева», а не просто «есть дефект».

notes:
- 1-3 коротких конкретных наблюдения по фото
- не используй шаблоны вроде "заметка1"
- если найден дефект — опиши его максимально конкретно
Если по фото нельзя уверенно судить, выбирай "Удовлетворительное" и объясняй почему.
Ответь ТОЛЬКО JSON (без markdown):
{
  "condition": "Хорошее",
  "notes": ["Есть реальные фото устройства", "На корпусе видны лёгкие потёртости"]
}
Никаких личных данных. Отвечай на русском.
"""



_PROMPT_ROLE_MARKERS = re.compile(
    r"(?i)("
    # Markdown-style role headers: "### system:", "## assistant:" etc.
    # Allowed anywhere in the text — a malicious listing title can put
    # them mid-line just as easily as at the start.
    r"###?\s*(?:system|instruction|user|assistant)\s*[:\-—]?\s*"
    # Chat-template markers from open-source models (chatml, llama, etc.).
    r"|<\|(?:im_start|im_end|system|user|assistant)\|>"
    # Bracketed role tags: "[system]", "[ASSISTANT]" etc.
    r"|\[\s*(?:system|instruction|user|assistant)\s*\]\s*[:\-—]?\s*"
    r")"
)
_PROMPT_INJECTION_PATTERNS = re.compile(
    r"(?i)("
    r"ignore\s+(?:all|any|the)?\s*(?:previous|prior|above)?\s*instructions"
    r"|disregard\s+(?:all|any|the)?\s*(?:previous|prior|above)?\s*instructions"
    r"|you\s+(?:are|act as|will now|must)\s+(?:no longer|now)\s+"
    r"|игнориру(?:й|йте)\s+(?:все\s+)?(?:предыдущие|прошлые)\s+"
    r"(?:инструкции|правила|сообщени)"
    r"|забудь\s+(?:все\s+)?(?:предыдущие|прошлые)\s+"
    r"|новая\s+инструкция[:\-]"
    r"|жестк(?:ая|ие)\s+инструкци(?:я|и)"
    r"|reveal\s+(?:your|the)\s+(?:system\s+)?prompt"
    r"|jailbreak"
    r")"
)
_TRIPLE_BACKTICK_RE = re.compile(r"```+|~~~+")
_MULTILINE_COLLAPSE_RE = re.compile(r"\n{3,}")


def sanitize_user_text(
    value: str | None,
    *,
    max_length: int = 1200,
    context: str = "user_text",
) -> str | None:
    """Strip prompt-injection-shaped sequences from user-supplied free text.

    Telegram users can paste anything into the listing assistant's notes /
    title fields. Without sanitization a determined user could try to make
    Gemini ignore our system prompt by writing things like
    `### system:\nIgnore all previous instructions...`.

    This isn't a hard security boundary (the model is the actual decision
    maker), but it's a cheap layer that:
      - removes obvious role/instruction markers,
      - flattens triple-backtick code fences,
      - normalises whitespace,
      - hard-caps length again on the server.
      - **logs every actual hit** so we have telemetry on injection
        attempts; the audit (SEC-H4) called out that the silent regex
        had no observability and could be bypassed via Unicode
        homoglyphs without anyone noticing. We can't catch every
        homoglyph here cheaply, but we can at least see when the
        ASCII patterns are tripped — and use those signals for
        rate-limit / abuse review later.

    The ``context`` argument is included in injection logs so the team
    can tell which surface area the attempt came from (listing title,
    seller notes, etc.).

    Returns None if input is None/empty after cleaning.
    """
    if value is None:
        return None
    text = str(value)
    if not text.strip():
        return None
    # Use subn() so we can count hits per pattern and log the totals.
    text, role_hits = _PROMPT_ROLE_MARKERS.subn("", text)
    text, injection_hits = _PROMPT_INJECTION_PATTERNS.subn("[удалено]", text)
    text = _TRIPLE_BACKTICK_RE.sub("`", text)
    text = _MULTILINE_COLLAPSE_RE.sub("\n\n", text)
    # Strip control characters but keep newlines and tabs.
    text = "".join(ch for ch in text if ch == "\n" or ch == "\t" or ord(ch) >= 0x20)
    text = text.strip()
    if role_hits or injection_hits:
        # WARNING level so it shows up in standard log scrapers; do
        # NOT include the cleaned text — could itself contain attacker-
        # controlled content. Just counts + context.
        logger.warning(
            "ai.prompt_injection_detected context=%s role_hits=%d injection_hits=%d",
            context, role_hits, injection_hits,
        )
    if not text:
        return None
    if max_length > 0 and len(text) > max_length:
        text = text[:max_length].rstrip()
    return text




def _clean_photo_notes(notes: list[Any] | None) -> list[str]:
    cleaned: list[str] = []
    seen: set[str] = set()
    for item in notes or []:
        text = re.sub(r"\s+", " ", str(item or "").strip(" .;"))
        if not text:
            continue
        lowered = text.lower()
        if lowered in {"заметка1", "заметка 1", "note1", "note 1"}:
            continue
        if "|" in text and any(token in lowered for token in _VALID_CONDITION_LABELS):
            continue
        key = lowered
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(text)
    return cleaned[:4]


def _repair_truncated_json(text: str) -> dict:
    """Try to repair a truncated JSON response from the AI model.

    When max_tokens cuts off the response mid-JSON, we try to close
    open braces/brackets and parse what we have, preserving as many
    sections as possible.
    """
    start = text.find("{")
    if start == -1:
        return {"summary": text.strip()[:500], "condition": None, "fair_price": None}

    fragment = text[start:]

    # Count open braces/brackets and close them
    open_braces = 0
    open_brackets = 0
    in_string = False
    escape_next = False
    for ch in fragment:
        if escape_next:
            escape_next = False
            # Escaped char inside string — skip, but stay in_string
            continue
        if ch == "\\" and in_string:
            escape_next = True
            continue
        if ch == '"' and not escape_next:
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            open_braces += 1
        elif ch == "}":
            open_braces -= 1
        elif ch == "[":
            open_brackets += 1
        elif ch == "]":
            open_brackets -= 1

    # Close any open string
    if in_string:
        fragment += '"'
    # Strip trailing incomplete key/value pairs to make repair viable.
    # After closing the open string we may have: ..., "red_fla"  or
    # ..., "key": 123  without closing brace.  Try progressively
    # aggressive cleanup so we preserve as many valid keys as possible.
    for pattern in (
        r',\s*"[^"]*"\s*:\s*$',            # trailing "key": (no value)
        r',\s*"[^"]*"\s*$',                # trailing "key" (no colon)
        r',\s*"[^"]*$',                     # trailing "incomplete_key
        r':\s*[^}\]"\dtruefalsenul.+-]+\s*$',  # trailing : garbage
    ):
        candidate = re.sub(pattern, "", fragment)
        if candidate != fragment:
            fragment = candidate
            break
    # Close open brackets and braces
    fragment += "]" * max(0, open_brackets)
    fragment += "}" * max(0, open_braces)

    try:
        result = json.loads(fragment)
        logger.info("Repaired truncated JSON: recovered keys=%s", list(result.keys()))
        return result
    except json.JSONDecodeError:
        logger.warning("Could not repair truncated JSON")
        return {"summary": text.strip()[:500], "condition": None, "fair_price": None}


# ─────────────────────────────────────────────────────────────────────
# Dedupe helpers — both Gemini 2.5 Flash and the legacy Gemma fallback
# sometimes paraphrase the same observation across `condition.notes`,
# `watch_out`, and `red_flags`, producing visible duplicates in the
# analysis modal (e.g. "Лакокрасочное покрытие без видимых значительных
# дефектов" + "Лакокрасочное покрытие без видимых значительных
# дефектов, диски чистые"). We collapse them server-side so the UI
# doesn't need to repeat the logic and the behavior is model-agnostic.
# ─────────────────────────────────────────────────────────────────────


def _normalize_for_dedupe(text: str) -> str:
    """Lowercase + collapse whitespace + strip trailing punctuation."""
    if not isinstance(text, str):
        return ""
    return " ".join(text.lower().split()).rstrip(".,;:!?·…—–-").strip()


def _is_paraphrase(short: str, longer: str, *, min_prefix: int = 25) -> bool:
    """True when `short` is essentially a truncated version of `longer`.

    Used to drop "Автомобиль выглядит чистым и ухоженным" when we
    already kept "Автомобиль выглядит чистым и ухоженным на всех 18
    фото" — it's the same observation, just truncated.

    The prefix match must end on a word boundary so we don't collapse
    different words that share a stem (e.g. "покрытие" vs "покрытием").
    """
    if not short or not longer:
        return False
    if len(short) > len(longer):
        return False
    if len(short) < min_prefix:
        return short == longer
    if not longer.startswith(short):
        return False
    if len(longer) == len(short):
        return True
    next_char = longer[len(short)]
    # Allow whitespace, punctuation, and common separators; reject letters/digits
    # which would mean the prefix cuts a word in half.
    return not next_char.isalnum()


def _dedupe_text_list(items: list, *, seen: set[str] | None = None) -> list:
    """Drop near-duplicate strings from a list, preserving order.

    Keeps the longer, more informative wording when two items are
    paraphrases of the same observation. Pre-existing entries in
    ``seen`` (normalized form) are also filtered out — used for
    cross-field dedupe.
    """
    if not isinstance(items, list):
        return items
    seen = set(seen) if seen else set()
    kept: list[str] = []
    kept_norm: list[str] = []
    for raw in items:
        text = raw if isinstance(raw, str) else ""
        norm = _normalize_for_dedupe(text)
        if not norm or norm in seen:
            continue
        # If a previously-kept item is a paraphrase of this one (or
        # vice versa), keep the longer form.
        replaced = False
        for i, existing_norm in enumerate(kept_norm):
            if _is_paraphrase(existing_norm, norm):
                kept[i] = raw
                kept_norm[i] = norm
                seen.discard(existing_norm)
                seen.add(norm)
                replaced = True
                break
            if _is_paraphrase(norm, existing_norm):
                replaced = True
                break
        if replaced:
            continue
        kept.append(raw)
        kept_norm.append(norm)
        seen.add(norm)
    return kept


def _dedupe_dict_list(items: list, *, key_fields: tuple[str, ...]) -> list:
    """Drop near-duplicate dicts based on the joined text of key fields.

    Used for ``watch_out`` (objects with ``point`` and ``why``).
    """
    if not isinstance(items, list):
        return items
    kept: list = []
    kept_norm: list[str] = []
    for entry in items:
        if not isinstance(entry, dict):
            kept.append(entry)
            continue
        joined = " ".join(
            str(entry.get(field) or "").strip() for field in key_fields
        ).strip()
        norm = _normalize_for_dedupe(joined)
        if not norm:
            continue
        replaced = False
        for i, existing_norm in enumerate(kept_norm):
            if _is_paraphrase(existing_norm, norm):
                kept[i] = entry
                kept_norm[i] = norm
                replaced = True
                break
            if _is_paraphrase(norm, existing_norm):
                replaced = True
                break
        if replaced:
            continue
        kept.append(entry)
        kept_norm.append(norm)
    return kept


def _dedupe_listing_payload(result: dict) -> dict:
    """Collapse duplicates inside the seller-side listing draft.

    Same paraphrase logic as the analysis dedupe, applied to the
    list-of-strings (`selling_points`, `photo_tips`) and list-of-dicts
    (`negotiation_playbook`) fields the listing assistant returns.
    """
    if not isinstance(result, dict):
        return result
    for key in ("selling_points", "photo_tips"):
        value = result.get(key)
        if isinstance(value, list):
            result[key] = _dedupe_text_list(value)
    playbook = result.get("negotiation_playbook")
    if isinstance(playbook, list):
        result["negotiation_playbook"] = _dedupe_dict_list(
            playbook, key_fields=("scenario", "response")
        )
    return result


def dedupe_analysis_payload(merged: dict) -> dict:
    """Collapse paraphrase duplicates inside the AI analysis payload.

    Cross-field rule: anything already covered in ``condition.notes``
    is removed from ``watch_out`` and ``red_flags`` so the same
    observation doesn't render in both the "Состояние по фото" block
    and the "Что перепроверить" / "Красные флаги" blocks.
    """
    cond = merged.get("condition")
    if isinstance(cond, dict) and isinstance(cond.get("notes"), list):
        cond["notes"] = _dedupe_text_list(cond["notes"])

    condition_notes_norm: set[str] = set()
    if isinstance(cond, dict):
        condition_notes_norm = {
            _normalize_for_dedupe(n) for n in cond.get("notes") or [] if isinstance(n, str)
        }
        condition_notes_norm.discard("")

    watch_out = merged.get("watch_out")
    if isinstance(watch_out, list):
        # Cross-field: drop watch_out items that just restate condition notes.
        filtered_watch: list = []
        for entry in watch_out:
            if isinstance(entry, dict):
                point = _normalize_for_dedupe(str(entry.get("point") or ""))
                why = _normalize_for_dedupe(str(entry.get("why") or ""))
                if any(
                    _is_paraphrase(cn, point) or _is_paraphrase(point, cn)
                    or _is_paraphrase(cn, why) or _is_paraphrase(why, cn)
                    for cn in condition_notes_norm
                ):
                    continue
            filtered_watch.append(entry)
        merged["watch_out"] = _dedupe_dict_list(
            filtered_watch, key_fields=("point", "why")
        )

    for key in ("meeting_checklist", "red_flags", "negotiation_tips"):
        value = merged.get(key)
        if isinstance(value, list):
            merged[key] = _dedupe_text_list(value, seen=condition_notes_norm)

    return merged


class AIService:
    """AI service using OpenAI-compatible chat completions API."""

    def __init__(self) -> None:
        settings = get_settings()
        self._api_key = settings.ai_api_key
        self._base_url = (settings.ai_base_url or "https://api.together.xyz/v1").rstrip("/")
        self._model = settings.ai_model or "gemini-3-flash"
        self._max_images = settings.ai_max_images
        self._proxy_url = settings.ai_proxy_url
        self._httpx_client: httpx.AsyncClient | None = None
        self._client_lock = asyncio.Lock()

    @property
    def available(self) -> bool:
        return self._api_key is not None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._httpx_client is None or self._httpx_client.is_closed:
            async with self._client_lock:
                if self._httpx_client is None or self._httpx_client.is_closed:
                    kwargs: dict = {
                        "timeout": httpx.Timeout(connect=15, read=45, write=20, pool=10),
                        "max_redirects": _MAX_AI_REDIRECTS,
                        "limits": httpx.Limits(max_connections=20, max_keepalive_connections=10),
                    }
                    if self._proxy_url:
                        kwargs["proxy"] = self._proxy_url
                    self._httpx_client = httpx.AsyncClient(**kwargs)
        return self._httpx_client

    @property
    def _is_gemini(self) -> bool:
        """Gemini models served via Google's OpenAI-compatible endpoint."""
        return (
            self._model.lower().startswith("gemini")
            or "generativelanguage.googleapis.com" in self._base_url
        )

    @property
    def _is_gemma_legacy(self) -> bool:
        """Legacy Gemma 4 / Gemma 3 chat-completion via Together AI.

        These models put output in `reasoning` instead of `content` when
        `response_format` is set, so we have to leave it off and parse
        whatever shows up.
        """
        model = self._model.lower()
        return model.startswith(("google/gemma-", "google/gemma_")) or "gemma-3n" in model

    async def _chat(
        self,
        *,
        system: str,
        content: list[dict] | str,
        max_tokens: int = 1200,
        reasoning_effort: str | None = None,
    ) -> dict:
        """Call OpenAI-compatible /chat/completions endpoint."""
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": content},
        ]
        body: dict = {
            "model": self._model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": 0.2,
        }
        # Gemini 2.5 Flash/Pro spend output budget on internal "thinking"
        # tokens before producing the JSON. `reasoning_effort: "medium"`
        # gives enough depth for grounded price comparisons and condition
        # analysis without the 30-60s latency of "high".
        #
        # max_tokens=3200/4000 for analysis, 4800 for listing assistant
        # (larger JSON output: title + description + 3 pricing tiers +
        # negotiation playbook + competitors + photo tips).
        # At medium effort, thinking uses ~300-600 tokens, leaving
        # ~4200+ for the JSON payload — sufficient for all sections.
        if self._is_gemini:
            body["reasoning_effort"] = reasoning_effort or "medium"
            # Gemini honours response_format properly — ask for JSON to
            # cut down on stray markdown fences and prose around the JSON.
            body["response_format"] = {"type": "json_object"}
        api_key = self._api_key.get_secret_value() if self._api_key else ""
        client = await self._get_client()
        logger.info(
            "AI _chat: model=%s, base_url=%s, proxy=%s, content_parts=%d",
            self._model,
            self._base_url,
            "yes" if self._proxy_url else "no",
            len(content) if isinstance(content, list) else 1,
        )
        try:
            resp = await client.post(
                f"{self._base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=body,
            )
        except httpx.HTTPError as e:
            logger.error("AI _chat request failed: %s: %s", type(e).__name__, e)
            raise
        logger.info("AI _chat response: status=%d", resp.status_code)
        if resp.status_code == 429:
            raise RuntimeError("429 RATE_LIMITED")
        if resp.status_code == 402:
            raise RuntimeError("Insufficient balance")
        resp.raise_for_status()
        data = resp.json()
        choices = data.get("choices") or []
        if not choices:
            raise RuntimeError(f"AI returned empty choices (status={resp.status_code})")
        choice = choices[0]
        finish_reason = choice.get("finish_reason", "")
        message = choice.get("message")
        if not message:
            raise RuntimeError("AI returned no message in choice")
        if finish_reason == "length":
            logger.warning(
                "AI _chat: output truncated (finish_reason=length, max_tokens=%d)",
                max_tokens,
            )
        # Some models (Gemma 4 on Together) return output in `reasoning`
        # field while `content` is empty — handle both.
        text = message.get("content") or ""
        if not text.strip() and message.get("reasoning"):
            logger.info(
                "AI _chat: content empty, using reasoning field (%d chars)",
                len(message["reasoning"]),
            )
            # Reasoning may contain the JSON at the end after the thought chain
            reasoning = message["reasoning"]
            # Try to extract JSON from reasoning
            text = reasoning
        return self._parse_json(text)

    # ── Public methods ──────────────────────────────────────────

    async def analyze_listing_parallel(
        self,
        *,
        title: str,
        description: str | None,
        price_byn: float,
        is_negotiable_price: bool = False,
        condition: str | None,
        parameters: list[dict],
        market_median: float | None,
        market_count: int,
        similar_listings: list[dict] | None = None,
        market_q1: float | None = None,
        market_q3: float | None = None,
        market_min: float | None = None,
        market_max: float | None = None,
        seller_type: str | None = None,
        photo_count: int = 0,
        listing_age_days: int | None = None,
        risk_context_summary: str | None = None,
        risk_context_flags: list[str] | None = None,
        anomaly_flags: list[str] | None = None,
        deal_score: float | None = None,
        deal_verdict: str | None = None,
        photo_condition_label: str | None = None,
        photo_condition_notes: list[str] | None = None,
        image_urls: list[str] | None = None,
    ) -> dict:
        """Parallel AI analysis — splits work into 2 concurrent sub-calls.

        Call A (Price & Market): fair_price, resale_potential, market_context,
            negotiation_tips, best_pick
        Call B (Condition & Risks): condition, watch_out, meeting_checklist,
            red_flags, recommendation, summary

        Both calls share the same listing context (text + images) and produce
        different JSON sections, using 3200/4000 max_tokens for Gemini 2.5
        Flash. Running concurrently means wall-clock time ≈ max(A, B), not A+B.
        """
        category = detect_category(title, parameters)
        hints = CATEGORY_HINTS.get(category, CATEGORY_HINTS["default"])
        price_position_label = self._price_position_label(
            price_byn,
            market_median,
            market_q1,
            market_q3,
            is_negotiable_price=is_negotiable_price,
        )
        negotiable_hint = (
            "Цена в объявлении указана как договорная. "
            "Не считай, что цена покупки равна 0 BYN. "
            "Опирайся на рыночный диапазон и похожие объявления."
            if is_negotiable_price
            else ""
        )

        # Build shared context once
        context = self._build_listing_context(
            title=title,
            description=description,
            price_byn=price_byn,
            is_negotiable_price=is_negotiable_price,
            condition=condition,
            parameters=parameters,
            market_median=market_median,
            market_count=market_count,
            market_q1=market_q1,
            market_q3=market_q3,
            market_min=market_min,
            market_max=market_max,
            seller_type=seller_type,
            photo_count=photo_count,
            similar_listings=similar_listings,
            listing_age_days=listing_age_days,
            risk_context_summary=risk_context_summary,
            risk_context_flags=risk_context_flags,
            anomaly_flags=anomaly_flags,
            deal_score=deal_score,
            deal_verdict=deal_verdict,
            photo_condition_label=photo_condition_label,
            photo_condition_notes=photo_condition_notes,
        )

        # Fetch listing images for multimodal analysis (scam detection,
        # photo authenticity, condition assessment). Fetched in parallel
        # to minimise latency before the two AI sub-calls start.
        image_content: list[dict] = []
        if image_urls:
            fetch_tasks = [
                self._fetch_image_b64(url) for url in image_urls[:self._max_images]
            ]
            fetched = await asyncio.gather(*fetch_tasks)
            for img in fetched:
                if img:
                    image_content.append(img)
            if image_content:
                logger.info(
                    "AI analyze: fetched %d/%d images for multimodal analysis",
                    len(image_content),
                    len(image_urls),
                )

        # Build multimodal content: text + images (when available)
        if image_content:
            call_content: list[dict] | str = [
                {"type": "text", "text": context},
                *image_content,
            ]
        else:
            call_content = context

        # Build system prompts for each sub-call
        system_a = (
            _PRICE_MARKET_PROMPT_TEMPLATE.format(
                category_hints=hints["category_hints"],
                bargain_hint=hints["bargain_hint"],
                price_position_label=price_position_label,
            )
            + "\n"
            + negotiable_hint
            + "\n"
            + _PRICE_MARKET_SCHEMA
        )
        system_b = (
            _CONDITION_RISKS_PROMPT_TEMPLATE.format(
                category_hints=hints["category_hints"],
                bargain_hint=hints["bargain_hint"],
                price_position_label=price_position_label,
            )
            + "\n"
            + negotiable_hint
            + "\n"
            + _CONDITION_RISKS_SCHEMA
        )

        # Gemini 2.5 Flash with reasoning_effort="medium" uses ~300-600 thinking
        # tokens. Each sub-call needs enough room for thinking + ~5-6 JSON
        # sections. 3200/4000 tokens per call gives Flash room to reason
        # through price comparisons without truncating the JSON output.
        # (The old 2200/2800 values were tuned for Gemma 4 which had no
        # thinking tokens but was prone to empty content with response_format.)
        async def _call_a():
            return await self._chat(system=system_a, content=call_content, max_tokens=3200)

        async def _call_b():
            # Stagger by 1s to avoid Together AI rate-limit (429) on concurrent requests
            await asyncio.sleep(1.0)
            # Call B includes scam_analysis + photo_authenticity — needs more tokens
            return await self._chat(system=system_b, content=call_content, max_tokens=4000)

        logger.warning("AI analyze_listing_parallel: starting staggered sub-calls")
        # Use return_exceptions so one failure doesn't kill the other
        results = await asyncio.gather(_call_a(), _call_b(), return_exceptions=True)

        result_a = results[0] if not isinstance(results[0], Exception) else {}
        result_b = results[1] if not isinstance(results[1], Exception) else {}
        if isinstance(results[0], Exception):
            logger.warning(
                "AI parallel Call A failed: %s: %s",
                type(results[0]).__name__,
                results[0],
            )
        if isinstance(results[1], Exception):
            logger.warning(
                "AI parallel Call B failed: %s: %s",
                type(results[1]).__name__,
                results[1],
            )
        # If BOTH failed, re-raise the first error so the router fallback path kicks in
        if isinstance(results[0], Exception) and isinstance(results[1], Exception):
            raise results[0]

        # Merge: Call B sections take priority for overlapping keys,
        # Call A sections fill in the rest.
        merged: dict = {}
        # fair_price, resale_potential, negotiation_tips, market_context, best_pick
        merged.update(result_a)
        # condition, watch_out, meeting_checklist, red_flags, recommendation, summary
        merged.update(result_b)
        # Ensure all expected keys exist even if a sub-call returned partial data
        merged.setdefault("fair_price", None)
        merged.setdefault("resale_potential", None)
        merged.setdefault("negotiation_tips", [])
        merged.setdefault("market_context", "")
        merged.setdefault("best_pick", {"ad_id": None, "reason": ""})
        merged.setdefault("condition", None)
        merged.setdefault("watch_out", [])
        merged.setdefault("meeting_checklist", [])
        merged.setdefault("red_flags", [])
        merged.setdefault("scam_analysis", None)
        merged.setdefault("photo_authenticity", None)
        merged.setdefault("recommendation", None)
        merged.setdefault("summary", "")
        return dedupe_analysis_payload(merged)

    async def generate_listing(
        self,
        *,
        title: str,
        condition: str | None,
        is_negotiable: bool,
        draft_price_byn: float | None,
        extra_notes: str | None,
        market_median: float | None,
        market_q1: float | None,
        market_q3: float | None,
        market_min: float | None,
        market_max: float | None,
        market_count: int,
        similar_listings: list[dict] | None,
        category_hint: str | None,
        category_bargain_hint: str | None,
        photo_data_urls: list[str] | None = None,
    ) -> dict:
        """Generate seller-side listing draft (title, description, pricing, playbook).

        The prompt receives concrete market anchors (median + Q1/Q3) so the
        model can produce numerically-grounded fast/market/patient tiers
        instead of guesses.
        """

        # User-supplied text fields go through sanitize_user_text first to
        # neutralise prompt-injection markers like `### system:` / role
        # tags / "ignore all previous instructions" before they reach the
        # prompt. Length caps are enforced again here as defence-in-depth.
        safe_title = sanitize_user_text(title, max_length=200) or ""
        safe_condition = sanitize_user_text(condition, max_length=64) if condition else None
        safe_notes = sanitize_user_text(extra_notes, max_length=600) if extra_notes else None

        ctx_lines: list[str] = [f"## ТОВАР: {safe_title}"]
        if safe_condition:
            ctx_lines.append(f"Состояние (как видит продавец): {safe_condition}")
        if draft_price_byn and draft_price_byn > 0:
            ctx_lines.append(f"Черновая цена продавца: {int(round(draft_price_byn))} BYN")
        elif is_negotiable:
            ctx_lines.append("Цена черновая: договорная")
        if safe_notes:
            ctx_lines.append(f"Заметки продавца: {safe_notes}")

        ctx_lines.append("")
        ctx_lines.append("## РЫНОК (Kufar.by, BYN)")
        if market_median:
            ctx_lines.append(f"Медиана: {market_median:.0f}")
        if market_q1 and market_q3:
            ctx_lines.append(f"Q1-Q3: {market_q1:.0f}-{market_q3:.0f}")
        if market_min is not None and market_max is not None:
            ctx_lines.append(f"Min-Max: {market_min:.0f}-{market_max:.0f}")
        ctx_lines.append(f"Количество объявлений в выборке: {market_count}")

        if similar_listings:
            ctx_lines.append("")
            ctx_lines.append("## ТОП КОНКУРЕНТОВ (до 6)")
            for item in similar_listings[:6]:
                price = item.get("price_byn") or 0
                cond = item.get("condition") or item.get("condition_label") or ""
                seller = item.get("seller_type") or ""
                title_text = sanitize_user_text(
                    (item.get("title") or "").strip().replace("\n", " "),
                    max_length=120,
                ) or ""
                params_list = item.get("parameters") or []
                bits: list[str] = []
                if price:
                    bits.append(f"{int(round(float(price)))} BYN")
                if cond:
                    bits.append(str(cond))
                if seller:
                    bits.append(str(seller))
                if params_list:
                    bits.append(", ".join(str(p) for p in params_list))
                meta = " · ".join(bits)
                if title_text:
                    ctx_lines.append(f"- {title_text} ({meta})" if meta else f"- {title_text}")
                elif meta:
                    ctx_lines.append(f"- {meta}")

        if category_hint:
            ctx_lines.append("")
            ctx_lines.append("## КАТЕГОРИЯ-ПОДСКАЗКИ")
            ctx_lines.append(category_hint)
        if category_bargain_hint:
            ctx_lines.append("")
            ctx_lines.append("## АРГУМЕНТЫ ТОРГА (категория)")
            ctx_lines.append(category_bargain_hint)

        user_text = "\n".join(ctx_lines)

        photos = [
            url
            for url in (photo_data_urls or [])
            if isinstance(url, str) and url.startswith("data:image/")
        ][: self._max_images]

        if photos:
            content: list[dict] = [{"type": "text", "text": user_text}]
            for data_url in photos:
                content.append({"type": "image_url", "image_url": {"url": data_url}})
            result = await self._chat(
                system=LISTING_ASSISTANT_PROMPT,
                content=content,
                max_tokens=4800,
                reasoning_effort="medium",
            )
            return _dedupe_listing_payload(result)

        result = await self._chat(
            system=LISTING_ASSISTANT_PROMPT,
            content=user_text,
            max_tokens=4800,
            reasoning_effort="medium",
        )
        return _dedupe_listing_payload(result)

    async def quick_condition(self, image_urls: list[str]) -> dict:
        """Quick condition assessment from photos only."""
        content: list[dict] = [
            {"type": "text", "text": "Оцени состояние товара на фото."},
        ]
        for url in image_urls[: self._max_images]:
            img = await self._fetch_image_b64(url)
            if img:
                content.append(img)
        if len(content) == 1:
            raise ValueError("Не удалось загрузить фото для анализа")
        result = await self._chat(system=QUICK_CONDITION_PROMPT, content=content, max_tokens=1200)
        notes = _clean_photo_notes(result.get("notes"))
        return {
            "condition": normalize_condition_label(result.get("condition")),
            # Dedupe in case the AI rephrased the same observation
            # twice (e.g. "лёгкие потёртости" + "лёгкие потёртости на
            # корпусе"). Model-agnostic — applies to both Gemini and
            # the Gemma fallback.
            "notes": _dedupe_text_list(notes) if isinstance(notes, list) else notes,
        }

    # ── Helpers ─────────────────────────────────────────────────

    @staticmethod
    def _price_position_label(
        price: float,
        median: float | None,
        q1: float | None,
        q3: float | None,
        *,
        is_negotiable_price: bool = False,
    ) -> str:
        if is_negotiable_price:
            if median:
                return "не указана продавцом; ориентир — рыночная медиана"
            return "не указана продавцом"
        if not median:
            return "неизвестна"
        if q1 and q3:
            if price <= q1:
                return "ниже Q1 — это дешёвое предложение на рынке"
            if price <= median:
                return "между Q1 и медианой — ниже средней"
            if price <= q3:
                return "между медианой и Q3 — в нормальном диапазоне"
            return "выше Q3 — это дорогое предложение"
        delta = (price - median) / median * 100
        if delta < -10:
            return f"на {abs(delta):.0f}% ниже медианы — дешёвое"
        if delta < 10:
            return "около медианы — среднерыночная"
        return f"на {delta:.0f}% выше медианы — дорогое"

    def _build_listing_context(
        self,
        *,
        title: str,
        description: str | None,
        price_byn: float,
        is_negotiable_price: bool,
        condition: str | None,
        parameters: list[dict],
        market_median: float | None,
        market_count: int,
        market_q1: float | None = None,
        market_q3: float | None = None,
        market_min: float | None = None,
        market_max: float | None = None,
        seller_type: str | None = None,
        photo_count: int = 0,
        similar_listings: list[dict] | None = None,
        listing_age_days: int | None = None,
        risk_context_summary: str | None = None,
        risk_context_flags: list[str] | None = None,
        anomaly_flags: list[str] | None = None,
        deal_score: float | None = None,
        deal_verdict: str | None = None,
        photo_condition_label: str | None = None,
        photo_condition_notes: list[str] | None = None,
    ) -> str:
        # Title and description come from a Kufar listing — i.e. an
        # arbitrary user wrote them. Pass them through the same sanitiser
        # used for seller assistant inputs so a malicious listing can't
        # smuggle role-marker headers ("### system: ignore previous…")
        # into our analyse prompt.
        safe_title = sanitize_user_text(title, max_length=240) or ""
        parts = [f"## ОБЪЯВЛЕНИЕ: {safe_title}"]

        # Three-state price classification:
        #   is_negotiable_price  -> цена не указана продавцом ("договорная")
        #   is_free_price        -> явно отдают даром (price_byn == 0)
        #   neither              -> обычная фиксированная цена
        is_free_price = (not is_negotiable_price) and price_byn == 0

        # Price position
        price_pos = "позиция неизвестна"
        price_delta_pct = None
        if is_negotiable_price:
            price_pos = "цена договорная, точная сумма не указана"
        elif is_free_price:
            price_pos = "товар отдают бесплатно (полная скидка к рынку)"
        elif market_median and market_q1 and market_q3:
            price_delta_pct = (price_byn - market_median) / market_median * 100
            if price_byn <= market_q1:
                price_pos = f"НИЖЕ Q1 ({market_q1:.0f} BYN) — дешёвое"
            elif price_byn <= market_median:
                price_pos = "между Q1 и медианой — ниже средней"
            elif price_byn <= market_q3:
                price_pos = "между медианой и Q3 — средняя цена"
            else:
                price_pos = f"ВЫШЕ Q3 ({market_q3:.0f} BYN) — дорогое"
        elif market_median:
            price_delta_pct = (price_byn - market_median) / market_median * 100
            if price_delta_pct < -10:
                price_pos = f"на {abs(price_delta_pct):.0f}% ниже медианы"
            elif price_delta_pct < 0:
                price_pos = "немного ниже медианы"
            elif price_delta_pct < 10:
                price_pos = "около медианы"
            else:
                price_pos = f"на {price_delta_pct:.0f}% выше медианы"

        if is_negotiable_price:
            parts.append(f"Цена: договорная ({price_pos})")
            if market_median:
                parts.append(f"Рыночный ориентир: медиана {market_median:.0f} BYN")
        elif is_free_price:
            parts.append("Цена: 0 BYN — БЕСПЛАТНО (отдают даром)")
            if market_median:
                parts.append(f"Рыночная медиана: {market_median:.0f} BYN — это и есть ориентир выгоды.")
        else:
            parts.append(f"Цена: {price_byn:.0f} BYN ({price_pos})")
            if price_delta_pct is not None:
                parts.append(f"Отклонение от медианы: {price_delta_pct:+.0f}%")

        if condition:
            parts.append(f"Состояние (заявлено): {condition}")
        if seller_type:
            seller_label = "магазин/дилер" if seller_type == "shop" else "частное лицо"
            parts.append(f"Продавец: {seller_label}")
        if listing_age_days is not None:
            if listing_age_days == 0:
                parts.append("Опубликовано: сегодня")
            elif listing_age_days == 1:
                parts.append("Опубликовано: вчера")
            else:
                parts.append(f"Опубликовано: {listing_age_days} дн. назад")
        if photo_count:
            parts.append(f"Количество фото: {photo_count}")
        if risk_context_summary:
            parts.append(f"Риск-контекст площадки: {risk_context_summary}")
        if photo_condition_label or photo_condition_notes:
            parts.append("\n## БЫСТРЫЙ ФОТО-ОСМОТР")
            if photo_condition_label:
                parts.append(f"Состояние по фото: {photo_condition_label}")
            if photo_condition_notes:
                parts.append(
                    "Наблюдения: "
                    + "; ".join(str(note)[:140] for note in photo_condition_notes[:4])
                )

        # Market statistics
        if market_median:
            parts.append("\n## РЫНОК")
            # Cluster-aware comparison note: when the listing belongs to
            # a specific variant (e.g. "Polo VI поколение"), the median is
            # computed from similar listings only — not from all search
            # results. The count reflects the cluster size.
            if market_count and market_count < 30:
                parts.append(
                    "Сравнение с похожими объявлениями (поколение/модификация), "
                    f"не со всеми результатами поиска ({market_count} шт.)"
                )
            if market_q1 and market_q3:
                parts.append(
                    f"Медиана: {market_median:.0f} BYN | "
                    f"Объявлений: {market_count} | "
                    f"Q1={market_q1:.0f} | Q3={market_q3:.0f} BYN"
                )
            else:
                parts.append(f"Медиана: {market_median:.0f} BYN | Объявлений: {market_count}")
            if market_min and market_max:
                parts.append(f"Диапазон: {market_min:.0f} — {market_max:.0f} BYN")

            # Pre-computed fair range guidance
            if market_q1 and market_q3:
                iqr = market_q3 - market_q1
                if iqr > 0:
                    parts.append(
                        f"Справедливый диапазон (Q1-Q3): {market_q1:.0f} — {market_q3:.0f} BYN"
                    )
                    if is_negotiable_price:
                        parts.append(
                            "Цена не указана, поэтому сравнивай объявление с этим диапазоном "
                            "и оцени, насколько выгодной будет сделка после торга."
                        )
                    elif price_byn < market_q1:
                        parts.append(
                            f"Цена НА {market_q1 - price_byn:.0f} BYN ниже "
                            f"справедливого диапазона — хорошая сделка или есть причины"
                        )
                    elif price_byn > market_q3:
                        parts.append(
                            f"Цена НА {price_byn - market_q3:.0f} BYN выше "
                            f"справедливого диапазона — продавец хочет больше рынка"
                        )

            entry_guidance = _entry_price_guidance(
                market_median=market_median,
                market_q1=market_q1,
                market_q3=market_q3,
                similar_listings=similar_listings,
            )
            if is_negotiable_price and entry_guidance is not None:
                entry_from, entry_to = entry_guidance
                parts.append(f"Разумный вход после торга: {entry_from} — {entry_to} BYN.")
                parts.append(
                    "Для recommendation и negotiation_tips используй этот диапазон как "
                    "рабочий ориентир цены входа, если нет более сильных аналогов."
                )

            # Resale instruction
            if is_negotiable_price:
                parts.append(
                    "\nПЕРЕПРОДАЖА: цена покупки ещё не согласована. "
                    "Оцени resale_potential по рынку и укажи, при какой цене входа "
                    "сделка выглядит разумной."
                )
            elif is_free_price:
                parts.append(
                    "\nПЕРЕПРОДАЖА: товар достаётся бесплатно (0 BYN). "
                    "Любая ненулевая цена перепродажи — это чистая прибыль; "
                    "оцени resale_potential по рынку и обрати внимание прежде "
                    "всего на состояние и логистику самовывоза."
                )
            elif price_byn:
                parts.append(
                    f"\nПЕРЕПРОДАЖА: цена покупки {price_byn:.0f} BYN. "
                    f"Оцени resale_potential — за сколько потенциально можно перепродать."
                )

        # Anomaly flags
        if anomaly_flags:
            parts.append(f"\n## АНОМАЛИИ: {', '.join(anomaly_flags)}")
        if deal_score is not None:
            parts.append(f"Оценка сделки: {deal_score:.0f}/100 ({deal_verdict or '?'})")
        if risk_context_flags:
            parts.append(f"\n## РИСК-СИГНАЛЫ: {'; '.join(risk_context_flags[:4])}")

        # All parameters (not truncated)
        if parameters:
            params_str = ", ".join(
                f"{sanitize_user_text(str(p.get('label', '')), max_length=40) or ''}: "
                f"{sanitize_user_text(str(p.get('value', '')), max_length=60) or ''}"
                for p in parameters
            )
            parts.append(f"\n## ПАРАМЕТРЫ: {params_str}")

        # Full description — sanitised + truncated. Reads as
        # "untrusted UGC", so role markers / injection phrases are stripped.
        if description:
            safe_description = sanitize_user_text(description, max_length=700)
            if safe_description:
                parts.append(f"\n## ОПИСАНИЕ ПРОДАВЦА:\n{safe_description}")

        # Similar listings — enriched with price deltas
        if similar_listings:
            compact_similar = similar_listings[:5]
            parts.append(f"\n## АЛЬТЕРНАТИВЫ ({len(compact_similar)} вариантов):")
            for i, sl in enumerate(compact_similar, 1):
                if is_negotiable_price:
                    if market_median:
                        price_diff = sl.get("price_byn", 0) - market_median
                        if price_diff > 0:
                            diff_str = f"на {price_diff:.0f} BYN выше медианы"
                        elif price_diff < 0:
                            diff_str = f"на {abs(price_diff):.0f} BYN ниже медианы"
                        else:
                            diff_str = "около медианы"
                    else:
                        diff_str = "рыночный ориентир"
                else:
                    price_diff = sl.get("price_byn", 0) - price_byn
                    if price_diff > 0:
                        diff_str = f"на {price_diff:.0f} BYN дороже"
                    elif price_diff < 0:
                        diff_str = f"на {abs(price_diff):.0f} BYN дешевле"
                    else:
                        diff_str = "та же цена"
                age_str = ""
                ad = sl.get("age_days")
                if ad is not None:
                    age_str = f", {ad} дн." if ad > 0 else ", сегодня"
                deal_str = ""
                ds = sl.get("deal_score")
                if ds is not None:
                    deal_str = f", оценка: {ds:.0f}/100"

                parts.append(
                    f"  {i}. [{sl.get('ad_id')}] "
                    f"{sl.get('title', '')[:80]} — "
                    f"{sl.get('price_byn', 0):.0f} BYN ({diff_str}), "
                    f"{sl.get('condition') or 'не указано'}, "
                    f"{sl.get('seller_type', '?')}{age_str}{deal_str}"
                )
                params = sanitize_user_text(
                    str(sl.get("parameters") or ""), max_length=120
                ) or ""
                if params:
                    parts.append(f"     Параметры: {params[:120]}")
                desc = (sl.get("description") or "").strip()
                if desc:
                    parts.append(f"     Описание: {desc[:100]}")

        return "\n".join(parts)

    async def _fetch_image_b64(self, url: str) -> dict | None:
        """Download image, resize to max 768px, compress, return as vision content."""
        data = await self._fetch_image_bytes(url)
        if not data:
            return None
        import base64

        # Try to resize for faster AI processing
        compressed = self._compress_image(data)
        if compressed:
            data = compressed

        mime = "image/jpeg"
        if ".png" in url.lower():
            mime = "image/png"
        elif ".webp" in url.lower():
            mime = "image/webp"
        b64 = base64.b64encode(data).decode()
        return {
            "type": "image_url",
            "image_url": {"url": f"data:{mime};base64,{b64}"},
        }

    async def _fetch_image_bytes(self, url: str) -> bytes | None:
        """Download image bytes using shared httpx client.

        Uses follow_redirects=True with a max_redirects limit to avoid
        manual redirect handling. Streams the response to enforce the
        byte limit without loading the entire body into memory first.
        """
        if not self._is_allowed_image_url(url):
            logger.warning("Skipped AI image fetch from unsupported host: %s", url[:80])
            return None
        try:
            client = await self._get_client()
            # Use streaming to check size before loading entire body
            async with client.stream(
                "GET", url, timeout=8, follow_redirects=True,
            ) as resp:
                if resp.status_code != 200:
                    return None
                content_type = resp.headers.get("content-type", "")
                if not content_type.lower().startswith("image/"):
                    logger.warning("Skipped AI image fetch with content-type=%s", content_type)
                    return None
                content_length = resp.headers.get("content-length")
                if content_length:
                    try:
                        if int(content_length) > _MAX_AI_IMAGE_BYTES:
                            logger.warning("Skipped AI image fetch larger than byte limit")
                            return None
                    except ValueError:
                        pass
                # Read in chunks, enforcing the byte limit
                chunks: list[bytes] = []
                total = 0
                async for chunk in resp.aiter_bytes(chunk_size=65536):
                    total += len(chunk)
                    if total > _MAX_AI_IMAGE_BYTES:
                        logger.warning("Skipped AI image fetch larger than byte limit (streaming)")
                        return None
                    chunks.append(chunk)
                return b"".join(chunks)
        except httpx.HTTPError as e:
            logger.warning("Failed to fetch image %s: %s", url[:80], e)
        return None

    @staticmethod
    def _is_allowed_image_url(url: str) -> bool:
        try:
            parsed = urlparse(url)
        except ValueError:
            return False
        hostname = parsed.hostname or ""
        return parsed.scheme == "https" and bool(_KUFAR_IMAGE_HOST_RE.fullmatch(hostname))

    @staticmethod
    def _compress_image(data: bytes, max_dim: int = 768, quality: int = 75) -> bytes | None:
        """Resize and compress image to reduce AI processing time."""
        try:
            from io import BytesIO

            from PIL import Image

            img = Image.open(BytesIO(data))
            if img.mode in ("RGBA", "P"):
                img = img.convert("RGB")

            w, h = img.size
            if max(w, h) > max_dim:
                ratio = max_dim / max(w, h)
                img = img.resize((int(w * ratio), int(h * ratio)), Image.LANCZOS)

            buf = BytesIO()
            img.save(buf, format="JPEG", quality=quality, optimize=True)
            return buf.getvalue()
        except (ImportError, OSError, ValueError) as e:
            logger.debug("Image compression skipped: %s", e)
            return None

    @staticmethod
    def _parse_json(text: str) -> dict:
        """Parse JSON from model response, handling reasoning chains and code blocks."""
        text = text.strip()

        # Remove markdown code fences
        if text.startswith("```"):
            text = text.split("\n", 1)[-1]
        if text.endswith("```"):
            text = text.rsplit("```", 1)[0]

        # Strategy 1: direct parse
        try:
            return json.loads(text.strip())
        except json.JSONDecodeError:
            pass

        # Strategy 2: parse the last balanced JSON object while respecting
        # braces inside quoted strings.
        for candidate in reversed(AIService._json_object_candidates(text)):
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                continue

        # Strategy 3: non-greedy regex (original behavior, but non-greedy
        # to avoid capturing across multiple JSON objects)
        json_match = re.search(r"\{.*?\}", text.strip(), flags=re.DOTALL)
        if json_match:
            candidate = json_match.group(0)
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                pass

        logger.warning("AI returned non-JSON (%d chars): %s", len(text), text[:500])
        # Try to extract partial data from truncated JSON
        return _repair_truncated_json(text)

    @staticmethod
    def _json_object_candidates(text: str) -> list[str]:
        candidates: list[str] = []
        start: int | None = None
        depth = 0
        in_string = False
        escaped = False
        for index, char in enumerate(text):
            if escaped:
                escaped = False
                continue
            if char == "\\" and in_string:
                escaped = True
                continue
            if char == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if char == "{":
                if depth == 0:
                    start = index
                depth += 1
            elif char == "}" and depth:
                depth -= 1
                if depth == 0 and start is not None:
                    candidates.append(text[start : index + 1])
                    start = None
        return candidates

    async def close(self) -> None:
        """Close the underlying httpx client, if it was created."""
        if self._httpx_client is not None and not self._httpx_client.is_closed:
            await self._httpx_client.aclose()
            self._httpx_client = None

    async def chat_json(
        self, *, system: str, content: list[dict] | str, max_tokens: int = 1200,
    ) -> dict:
        """Public wrapper around _chat for simple JSON responses."""
        return await self._chat(system=system, content=content, max_tokens=max_tokens)


@functools.lru_cache(maxsize=1)
def get_ai_service() -> AIService:
    return AIService()
