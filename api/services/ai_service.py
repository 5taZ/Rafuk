"""AI service — OpenAI-compatible API (Together AI, DeepSeek, OpenAI, etc.).

Configure via .env:
  AI_API_KEY=<key>
  AI_BASE_URL=https://api.together.xyz/v1
  AI_MODEL=google/gemma-4-31B-it
"""

from __future__ import annotations

import asyncio
import json
import logging
import re

import httpx

from api.config import get_settings

logger = logging.getLogger(__name__)

AI_QUICK_PHOTO_DEADLINE_S = 20
AI_TEXT_DEADLINE_S = 45

SYSTEM_PROMPT_TEMPLATE = """\
Ты — Rafuks AI, эксперт-аналитик объявлений Kufar.by. \
Отвечай ТОЛЬКО на русском.

ПРАВИЛА ОТВЕТА:
- Будь конкретен: указывай суммы в BYN, сроки, модели, проценты.
- НЕ пиши общие фразы типа "сравните с аналогами" или "проверьте товар".
- Каждый пункт — действие или факт, а не пожелание.
- fair_price.from/to — реалистичный диапазон для ЭТОГО товара в ЕГО состоянии.
- reasoning в fair_price — почему именно этот диапазон (сравнение с конкретными аналогами).
- negotiation_tips — конкретные аргументы с BYN-суммами скидки
  ("попросите скидку X BYN, потому что...").
- watch_out — конкретные дефекты, которые ты видишь или предполагаешь с обоснованием.
- meeting_checklist — пошаговая проверка при встрече (5-7 пунктов, специфичных для категории).
- red_flags — только реальные признаки мошенничества/проблем, не очевидные вещи.
- red_flags — максимум 3 коротких пункта, самые важные сначала.
- Если во входном контексте есть явные hot words или risk signals вроде кредита,
  рассрочки, перекупа, автохауса, площадки, магазина или reseller-поведения,
  учитывай их в red_flags или market_context, но не выдумывай факты сверх контекста.
- Если цена договорная, recommendation и negotiation_tips должны опираться на
  реалистичный диапазон входа после торга в BYN, а не на абстрактную формулировку.
- market_context — 2-3 предложения: позиция цены, сравнение с лучшим аналогом и 1 ключевой вывод.
- summary — 1-2 предложения с вердиктом и ключевой причиной.
- resale_potential — за сколько потенциально можно перепродать этот товар.
  fast_price: цена для быстрой продажи (ниже рынка, быстрый отчёт).
  market_price: справедливая рыночная цена перепродажи.
  optimal_price: максимальная реалистичная цена (продажа терпеливо, в идеальном состоянии).
  Учитывай состояние товара, спрос и конкретные аналоги. Укажи конкретные суммы BYN.

ПРИЗНАКИ МОШЕННИЧЕСТВА — проверяй:
- Цена значительно ниже рынка (>30% ниже медианы) без обоснования
- Мало фото или фото низкого качества / с водяными знаками других сайтов
- Описание скопировано, шаблонно или не соответствует фото
- Нет реальных фото товара (только стоковые/промо изображения)
- Несоответствие: в описании одна модель, в параметрах другая

Цена товара {price_position_label}. {category_hints} {bargain_hint}
Ответь строго JSON:
"""

JSON_SCHEMA = """{
  "condition": {
    "label": "строго на русском: Отличное, Хорошее, Удовлетворительное или Требует внимания",
    "confidence": 0.0-1.0,
    "notes": ["конкретное наблюдение с фото или описания — что именно видно"]
  },
  "fair_price": {
    "from": число_BYN,
    "to": число_BYN,
    "reasoning": "обоснование: аналог X стоит Y BYN в состоянии Z, этот — потому что..."
  },
  "resale_potential": {
    "fast_price": {"label": "Быстро", "price_byn": число, "reasoning": "почему"},
    "market_price": {"label": "По рынку", "price_byn": число, "reasoning": "почему"},
    "optimal_price": {"label": "Оптимально", "price_byn": число, "reasoning": "почему"},
    "reasoning": "общее обоснование: за сколько можно перепродать и почему"
  },
  "watch_out": [
    {"point": "конкретная проблема", "why": "почему важно и как проверить"}
  ],
  "meeting_checklist": ["конкретное действие — что нажать, подключить, проверить"],
  "negotiation_tips": ["аргумент: 'Скиньте X BYN, потому что...' с суммой"],
  "red_flags": ["конкретный признак мошенничества или проблемы"],
  "market_context": "2-3 предложения: позиция цены, конкретные аналоги, тренд",
  "best_pick": {"ad_id": номер_или_null, "reason": "почему именно этот вариант лучше"},
  "recommendation": {
    "verdict": "worth_it или think_twice или overpriced (строго одно из трёх)",
    "text": "рекомендация с суммой и действием на русском"
  },
  "summary": "1-2 предложения: вердикт + ключевая причина"
}"""


def _build_system_prompt(
    category_hints: str,
    bargain_hint: str,
    price_position_label: str,
    negotiable_hint: str,
) -> str:
    """Build system prompt with category-specific hints injected."""
    return (
        SYSTEM_PROMPT_TEMPLATE.format(
            category_hints=category_hints,
            bargain_hint=bargain_hint,
            price_position_label=price_position_label,
        )
        + "\n"
        + negotiable_hint
        + "\n"
        + JSON_SCHEMA
    )


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


# Category-specific analysis hints injected into the prompt
CATEGORY_HINTS: dict[str, dict[str, str]] = {
    "phone": {
        "category_hints": (
            "Проверь батарею (ёмкость, циклы зарядки), экран "
            "(битые пиксели, олеофобку, равномерность), камеры (все линзы), "
            "разъём зарядки (люфт), Touch ID/Face ID (работает ли), "
            "Activation Lock (отвязан ли от Apple ID), "
            "звук (динамики, микрофон), кнопки (качество отклика). "
            "Обрати внимание на коробку и комплект — оригинальная коробка + чек = +5% к ценности."
        ),
        "bargain_hint": (
            "Износ батареи ниже 80% — аргумент для скидки 50-100 BYN. "
            "Царапины на экране — ещё 30-80 BYN скидки. "
            "Отсутствие коробки/чека — 20-50 BYN. "
            "Сравни с конкретными аналогами — укажи разницу в BYN. "
            "ВАЖНО: в negotiation_tips укажи конкретные суммы в BYN."
        ),
    },
    "laptop": {
        "category_hints": (
            "Проверь батарею (циклы, время автономной работы), "
            "клавиатуру (все клавиши, подсветка), тачпад (клик, жесты), "
            "порты (USB-C, HDMI, SD — все работают), "
            "экран (битые пиксели, равномерность подсветки, петли), "
            "вентиляторы (шум под нагрузкой), SSD/HDD здоровье (SMART), "
            "Wi-Fi и Bluetooth, веб-камеру."
        ),
        "bargain_hint": (
            "Износ батареи >500 циклов — аргумент для скидки 100-200 BYN. "
            "Устаревшие порты, царапины на корпусе — 50-100 BYN. "
            "Отсутствие гарантии — 80-150 BYN скидки. "
            "Сравни с новыми моделями — укажи разницу в BYN. "
            "ВАЖНО: в negotiation_tips укажи конкретные суммы в BYN."
        ),
    },
    "tablet": {
        "category_hints": (
            "Проверь экран (царапины, битые пиксели, олеофобку), "
            "батарею (ёмкость, циклы), разъём зарядки, "
            "камеры, совместимость со стилусом (Apple Pencil и т.д.), "
            "кнопки (качество отклика), звук (все динамики)."
        ),
        "bargain_hint": (
            "Износ батареи — -10-15%. Царапины на экране — -5-10%. "
            "Отсутствие стилуса/чехла из комплекта — -5-10%. "
            "Сравни с новой моделью предыдущего поколения."
        ),
    },
    "auto": {
        "category_hints": (
            "Проверь VIN (расшифровка), пробег (реальность по состоянию), "
            "кузов (толщиномер: шпаклёвка, перекрас, зазоры), "
            "ходовая (люфты, стуки на ходу), двигатель (масло, компрессия, звуки), "
            "КПП (переключения, сцепление), салон (износ, электрика), "
            "ДТП история, техосмотр, документы (ОСАГО, регистрация)."
        ),
        "bargain_hint": (
            "Каждый выявленный дефект — аргумент для торга. "
            "Возраст: каждый год сверх среднего — 200-500 BYN. "
            "Необходимость ремонта — скидка = стоимость ремонта + 20%. "
            "Сезонность: зимой кабриолеты/мото дешевле, весной — дороже. "
            "ВАЖНО: в negotiation_tips укажи конкретные суммы в BYN для каждого дефекта."
        ),
    },
    "motorcycle": {
        "category_hints": (
            "Проверь цепь и звёзды (износ), тормоза (диски, колодки), "
            "подвеску (пыльники, течи), двигатель (масло, компрессия, звуки), "
            "раму (трещины, сварка), пробег, ДТП, резину (остаток протектора), "
            "электрику (фары, поворотники), выхлоп (ржавчина, повреждения)."
        ),
        "bargain_hint": (
            "Сезонность: зимой цены ниже на 15-25%. "
            "Пробег >30k км — аргумент для -15%. "
            "Необходимость ТО — скидка на стоимость + 15%."
        ),
    },
    "tv": {
        "category_hints": (
            "Проверь матрицу (битые пиксели, полосы,_uniformity), "
            "подсветку (равномерность, засветы по краям), "
            "порты HDMI/USB (все работают), пульт (оригинальный), "
            "звук (динамики без хрипа), подставка/крепление, "
            "SMART TV (работает ли, не зависает ли)."
        ),
        "bargain_hint": (
            "Возраст модели: каждый год — -10-15%. "
            "Отсутствие SMART TV — -15%. "
            'Разрешение ниже 4K для диагонали >50" — аргумент для скидки.'
        ),
    },
    "headphones": {
        "category_hints": (
            "Проверь звук (оба канала, шумы, искажения), микрофон, "
            "Bluetooth (стабильность подключения), батарею (время работы), "
            "амбушюры (износ кожзи substituting), оголовье (трещины), "
            "оригинальность (подделки Audio-Technica, Sony, Apple, JBL "
            "очень распространены — сравни с официальными фото)."
        ),
        "bargain_hint": (
            "Износ амбушюр — -5-10%. Уменьшение ёмкости батареи — -10-15%. "
            "Подделка — не покупай. Запроси чек и серийный номер для проверки. "
            "Отсутствие кейса/зарядного кабеля — -5-10%."
        ),
    },
    "console": {
        "category_hints": (
            "Проверь дисковод (если есть — читает ли диски), "
            "контроллеры (стики без дрифта, триггеры, вибрация), "
            "HDMI (изображение без артефактов), вентилятор (шум), "
            "статус бана онлайн (не забанена ли консоль), "
            "гарантию (действует ли), прошивку (homedog/CFW — риск бана)."
        ),
        "bargain_hint": (
            "Поколение консоли: предыдущее — -30-50% от нового. "
            "Наличие игр в комплекте — +5-10% к ценности. "
            "Состояние контроллеров: дрифт стиков — -10-15%. "
            "Риск бана онлайн — -20-30%."
        ),
    },
    "watch": {
        "category_hints": (
            "Проверь экран (царапины, олеофобку), батарею (время работы), "
            "ремешок (износ, застёжка), водозащиту (тест погружением), "
            "оригинальность (подделки Apple Watch распространены — "
            "сравни интерфейс с официальным), датчики (пульс, SpO2)."
        ),
        "bargain_hint": (
            "Износ батареи — главный аргумент: при ёмкости <80% — -15-20%. "
            "Царапины на экране — -10%. "
            "Устаревшая модель (на 2+ поколения старше текущей) — -30-40%."
        ),
    },
    "camera": {
        "category_hints": (
            "Проверь матрицу (пыль, битые пиксели — тест на закрытой диафрагме), "
            "затвор (количество срабатываний — resource), объектив "
            "(грибок, царапины на линзах, люфт), стабилизацию (работает ли), "
            "карты памяти (слот), вспышку, видео (запись без артефактов)."
        ),
        "bargain_hint": (
            "Количество срабатываний затвора: каждый 10k сверх среднего — -5%. "
            "Состояние объектива: грибок/царапины — -15-25%. "
            "Возраст модели: каждый год — -8-12%. "
            "Сравни с новой моделью этого же класса."
        ),
    },
    "bicycle": {
        "category_hints": (
            "Проверь раму (трещины, вмятины, коррозия), "
            "вилку (люфт, ход, масло), колёса (восьмёрка, спицы, hubs), "
            "тормоза (колодки, диски, гидравлика), "
            "переключатели (точность, люфт цепи), "
            "седло и руль (износ, регулировки), резину (остаток протектора)."
        ),
        "bargain_hint": (
            "Износ цепи и кассеты — замена стоит 50-100 BYN. "
            "Восьмёрка колёс — правка 20-40 BYN. "
            "Сезонность: весной дороже, осенью дешевле 15-20%. "
            "Каждый сезон использования — -10-15%."
        ),
    },
    "appliance": {
        "category_hints": (
            "Проверь корпус (вмятины, ржавчина), электрику (кабель, вилка), "
            "работоспособность всех режимов, шумы при работе, "
            "уплотнители (для холодильников/стиралок), "
            "фильтры (для пылесосов/кондиционеров), гарантию."
        ),
        "bargain_hint": (
            "Возраст техники: каждый год — -10-15% от цены новой. "
            "Отсутствие гарантии — -10%. "
            "Необходимость замены расходников — скидка на их стоимость."
        ),
    },
    "furniture": {
        "category_hints": (
            "Проверь обивку (пятна, потёртости, разрывы), "
            "каркас (люфт, скрип, целостность), "
            "механизмы (раскладывание, ящики, петли), "
            "фурнитуру (ручки, ножки), размеры (подойдёт ли)."
        ),
        "bargain_hint": (
            "Повреждения обивки — -15-30% (химчистка стоит дорого). "
            "Скрип каркаса — -10-20%. "
            "Сравни с новой мебелью аналогичного класса. "
            "Транспортные расходы — аргумент для скидки."
        ),
    },
    "default": {
        "category_hints": (
            "Осмотри все фото внимательно: дефекты корпуса, экрана, "
            "комплектацию, оригинальность. "
            "Сравни визуальное состояние с заявленным в описании."
        ),
        "bargain_hint": (
            "Используй рыночную медиану и состояние товара "
            "для обоснования скидки. Каждый выявленный дефект — "
            "аргумент для -5-15% от цены."
        ),
    },
}

# Keywords for category detection from title/params
CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "phone": [
        "iphone",
        "samsung galaxy",
        "xiaomi",
        "pixel",
        "huawei",
        "honor",
        "oneplus",
        "телефон",
        "смартфон",
        "phone",
        "redmi",
        "note ",
        "pro max",
        "pro plus",
        "galaxy s",
        "galaxy a",
        "galaxy m",
        "iphone 1",
        "iphone se",
        "poco ",
    ],
    "laptop": [
        "macbook",
        "ноутбук",
        "laptop",
        "thinkpad",
        "lenovo ",
        "asus ",
        "hp ",
        "acer ",
        "msi ",
        "dell ",
        "surface",
        "пк",
        "компьютер",
        "игровой ноутбук",
        "ultrabook",
        "legion",
        "rog ",
        "predator ",
        "vivobook",
        "ideapad",
    ],
    "tablet": [
        "ipad",
        "tablet",
        "планшет",
        "galaxy tab",
        "tab s",
        "matepad",
        "ipad air",
        "ipad pro",
        "ipad mini",
    ],
    "auto": [
        "автомобиль",
        "седан",
        "хэтчбек",
        "универсал",
        "кроссовер",
        "внедорожник",
        "кузов",
        "двигатель",
        "пробег",
        "л.с.",
        "vin",
        "bmw ",
        "audi ",
        "mercedes",
        "volkswagen",
        "toyota",
        "honda",
        "ford ",
        "hyundai",
        "kia ",
        "nissan",
        "skoda",
        "mazda",
        "opel ",
        "renault",
        "peugeot",
        "объём",
        "куб.см",
        "легковой",
        "минивэн",
        "пикап",
    ],
    "motorcycle": [
        "мотоцикл",
        "скутер",
        "мопед",
        "эндуро",
        "кросс",
        "мотард",
        "chopper",
        "квадроцикл",
        "байк",
        "мотороллер",
    ],
    "tv": [
        "телевизор",
        "tv ",
        "smart tv",
        "oled",
        "qled",
        "led tv",
        "samsung q",
        "lg oled",
        "панель",
        "монитор ",
        "monitor",
    ],
    "headphones": [
        "наушники",
        "headphones",
        "airpods",
        "galaxy buds",
        "sony wh",
        "bose",
        "jbl ",
        "маршал",
        "marshall",
        "airpods pro",
        "airpods max",
        "beats ",
        "sennheiser",
        "audio-technica",
        "наушник",
    ],
    "console": [
        "playstation",
        "xbox",
        "nintendo",
        "switch",
        "ps5",
        "ps4",
        "приставка",
        "консоль",
        "геймпад",
        "ps ",
        "xbox one",
        "xbox series",
    ],
    "watch": [
        "часы",
        "apple watch",
        "galaxy watch",
        "smartwatch",
        "умные часы",
        "фитнес-браслет",
        "mi band",
        "garmin",
        "часы ",
        "watch ",
    ],
    "camera": [
        "фотоаппарат",
        "камера",
        "camera",
        "canon ",
        "nikon ",
        "sony alpha",
        "объектив",
        "lens",
        "зеркалка",
        "беззеркалн",
        "фото",
        "goPro",
    ],
    "bicycle": [
        "велосипед",
        "bike",
        "велик",
        "горный велосипед",
        "шоссейный",
        "bmx",
        "кросс-кантри",
        "двухподвес",
        "хардтейл",
        "электровелосипед",
    ],
    "appliance": [
        "холодильник",
        "стиральн",
        "пылесос",
        "микроволнов",
        "печь",
        "духовой шкаф",
        "посудомо",
        "кондиционер",
        "бойлер",
        "утюг",
        "фен",
        "блендер",
        "кофемашина",
        "кофеварка",
        "тостер",
        "мультиварка",
        "roboclean",
        "робот-пылесос",
        "сушильн",
        "варочн",
        "вытяжк",
        "морозильн",
        "dishwasher",
        "refrigerator",
    ],
    "furniture": [
        "диван",
        "кровать",
        "шкаф",
        "стол ",
        "стул ",
        "кресло",
        "тумба",
        "комод",
        "полка",
        "стеллаж",
        "обеденн",
        "журнальн",
        "кухонный гарнитур",
        "мебель",
        "софа",
        "пуфик",
        "матрас",
    ],
}


def detect_category(title: str, parameters: list[dict] | None = None) -> str:
    """Detect product category from title and parameters."""
    text = title.lower()
    # Also check parameter values for auto-specific fields
    if parameters:
        param_text = " ".join(str(p.get("value", "")) for p in parameters).lower()
        text = f"{text} {param_text}"

    best_match = "default"
    best_count = 0
    for category, keywords in CATEGORY_KEYWORDS.items():
        matches = sum(1 for kw in keywords if kw in text)
        if matches > best_count:
            best_count = matches
            best_match = category
    return best_match


QUICK_CONDITION_PROMPT = """\
Ты — Rafuks AI. Оцени состояние товара по фото.
Выбери РОВНО ОДНО значение condition из списка:
- Отличное
- Хорошее
- Удовлетворительное
- Требует внимания
notes:
- 1-3 коротких конкретных наблюдения по фото
- не используй шаблоны вроде "заметка1"
Если по фото нельзя уверенно судить, выбирай "Удовлетворительное" и объясняй почему.
Ответь ТОЛЬКО JSON (без markdown):
{"condition": "Хорошее", "notes": ["Есть реальные фото устройства", "На корпусе видны лёгкие потёртости"]}
Никаких личных данных. Отвечай на русском.
"""

_VALID_CONDITION_LABELS = {
    "отличное": "Отличное",
    "хорошее": "Хорошее",
    "удовлетворительное": "Удовлетворительное",
    "требует внимания": "Требует внимания",
    "excellent": "Отличное",
    "good": "Хорошее",
    "fair": "Удовлетворительное",
    "poor": "Требует внимания",
}


def _normalize_condition_label(value: str | None) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    normalized = text.lower()
    if "|" in normalized:
        for token in ("хорошее", "удовлетворительное", "требует внимания", "отличное"):
            if token in normalized:
                return _VALID_CONDITION_LABELS[token]
    for key, label in _VALID_CONDITION_LABELS.items():
        if key == normalized or key in normalized:
            return label
    return ""


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
            continue
        if ch == "\\":
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


class AIService:
    """AI service using OpenAI-compatible chat completions API."""

    def __init__(self) -> None:
        settings = get_settings()
        self._api_key = settings.ai_api_key
        self._base_url = (settings.ai_base_url or "https://api.together.xyz/v1").rstrip("/")
        self._model = settings.ai_model or "google/gemma-4-31B-it"
        self._max_images = settings.ai_max_images
        self._proxy_url = settings.ai_proxy_url
        self._httpx_client: httpx.AsyncClient | None = None

    @property
    def available(self) -> bool:
        return self._api_key is not None

    def _get_client(self) -> httpx.AsyncClient:
        if self._httpx_client is None or self._httpx_client.is_closed:
            kwargs: dict = {
                "timeout": httpx.Timeout(connect=15, read=90, write=20, pool=10),
            }
            if self._proxy_url:
                kwargs["proxy"] = self._proxy_url
            self._httpx_client = httpx.AsyncClient(**kwargs)
        return self._httpx_client

    async def _chat(
        self,
        *,
        system: str,
        content: list[dict] | str,
        max_tokens: int = 1200,
    ) -> dict:
        """Call OpenAI-compatible /chat/completions endpoint."""
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": content},
        ]
        # Gemma 4 uses reasoning tokens internally — increase budget so both
        # chain-of-thought AND the actual JSON response fit.
        # response_format omitted: some models put output in `reasoning` with it.
        body: dict = {
            "model": self._model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": 0.2,
        }
        api_key = self._api_key.get_secret_value() if self._api_key else ""
        client = self._get_client()
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
        message = data["choices"][0]["message"]
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

    async def analyze_listing(
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
        image_urls: list[str],
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
    ) -> dict:
        """Full AI analysis of a listing with optional comparison to alternatives."""
        # Build category-aware system prompt
        category = detect_category(title, parameters)
        hints = CATEGORY_HINTS.get(category, CATEGORY_HINTS["default"])
        system = _build_system_prompt(
            category_hints=hints["category_hints"],
            bargain_hint=hints["bargain_hint"],
            price_position_label=self._price_position_label(
                price_byn,
                market_median,
                market_q1,
                market_q3,
                is_negotiable_price=is_negotiable_price,
            ),
            negotiable_hint=(
                "Цена в объявлении указана как договорная. "
                "Не считай, что цена покупки равна 0 BYN. "
                "Опирайся на рыночный диапазон и похожие объявления."
                if is_negotiable_price
                else ""
            ),
        )

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
        logger.warning("AI analyze_listing: starting compact text-only report")
        return await asyncio.wait_for(
            self._chat(system=system, content=context, max_tokens=1600),
            timeout=AI_TEXT_DEADLINE_S,
        )

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
        result = await self._chat(system=QUICK_CONDITION_PROMPT, content=content, max_tokens=220)
        return {
            "condition": _normalize_condition_label(result.get("condition")),
            "notes": _clean_photo_notes(result.get("notes")),
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
        parts = [f"## ОБЪЯВЛЕНИЕ: {title}"]

        # Price position
        price_pos = "позиция неизвестна"
        price_delta_pct = None
        if is_negotiable_price:
            price_pos = "цена договорная, точная сумма не указана"
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
                    "Наблюдения: " + "; ".join(str(note)[:140] for note in photo_condition_notes[:4])
                )

        # Market statistics
        if market_median:
            parts.append("\n## РЫНОК")
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
                parts.append(
                    f"Разумный вход после торга: {entry_from} — {entry_to} BYN."
                )
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
                f"{p.get('label', '')}: {p.get('value', '')}" for p in parameters
            )
            parts.append(f"\n## ПАРАМЕТРЫ: {params_str}")

        # Full description
        if description:
            parts.append(f"\n## ОПИСАНИЕ ПРОДАВЦА:\n{description[:700]}")

        # Similar listings — enriched with price deltas
        if similar_listings:
            compact_similar = similar_listings[:3]
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

                parts.append(
                    f"  {i}. [{sl.get('ad_id')}] "
                    f"{sl.get('title', '')[:60]} — "
                    f"{sl.get('price_byn', 0):.0f} BYN ({diff_str}), "
                    f"{sl.get('condition') or 'не указано'}, "
                    f"{sl.get('seller_type', '?')}{age_str}"
                )
                desc = (sl.get("description") or "").strip()
                if desc:
                    parts.append(f"     Описание: {desc[:120]}")
                params = (sl.get("parameters") or "").strip()
                if params:
                    parts.append(f"     Параметры: {params[:90]}")

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
        """Download image bytes using shared httpx client."""
        try:
            client = self._get_client()
            resp = await client.get(url, timeout=8, follow_redirects=True)
            if resp.status_code == 200:
                await resp.aread()
                return resp.content
        except httpx.HTTPError as e:
            logger.warning("Failed to fetch image %s: %s", url[:80], e)
        return None

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

        # Strategy 2: find the last balanced JSON object using brace counting
        # This handles cases where reasoning text precedes the JSON output
        last_brace = text.rfind("}")
        if last_brace != -1:
            # Walk backwards to find the matching opening brace
            depth = 0
            for i in range(last_brace, -1, -1):
                if text[i] == "}":
                    depth += 1
                elif text[i] == "{":
                    depth -= 1
                    if depth == 0:
                        candidate = text[i : last_brace + 1]
                        try:
                            return json.loads(candidate)
                        except json.JSONDecodeError:
                            break

        # Strategy 3: greedy regex (original behavior)
        json_match = re.search(r"\{.*\}", text.strip(), flags=re.DOTALL)
        if json_match:
            candidate = json_match.group(0)
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                pass

        logger.warning("AI returned non-JSON (%d chars): %s", len(text), text[:500])
        # Try to extract partial data from truncated JSON
        return _repair_truncated_json(text)

    async def close(self) -> None:
        """Close the underlying httpx client, if it was created."""
        if self._httpx_client is not None and not self._httpx_client.is_closed:
            await self._httpx_client.aclose()
            self._httpx_client = None


# Singleton
_ai_service: AIService | None = None


def get_ai_service() -> AIService:
    global _ai_service
    if _ai_service is None:
        _ai_service = AIService()
    return _ai_service
