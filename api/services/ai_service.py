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

SYSTEM_PROMPT_TEMPLATE = """\
Ты — Rafuks AI, аналитик Kufar.by. Отвечай на русском, будь конкретен.
Цена товара {price_position_label}. {category_hints} {bargain_hint}
Ответь строго JSON:
"""

JSON_SCHEMA = """{
  "condition": {"label": "Отличное|Хорошее|Удовлетворительное|Требует внимания", "confidence": 0.0-1.0, "notes": ["наблюдение"]},
  "fair_price": {"from": число_BYN, "to": число_BYN, "reasoning": "почему"},
  "watch_out": [{"point": "на что смотреть", "why": "почему"}],
  "meeting_checklist": ["что проверить при встрече"],
  "negotiation_tips": ["аргумент для скидки"],
  "red_flags": ["признак проблемы"],
  "market_context": "позиция на рынке",
  "best_pick": {"ad_id": номер_или_null, "reason": "почему"},
  "recommendation": {"verdict": "worth_it|think_twice|overpriced", "text": "рекомендация"},
  "summary": "резюме"
}"""


def _build_system_prompt(
    category_hints: str,
    bargain_hint: str,
    price_position_label: str,
) -> str:
    """Build system prompt with category-specific hints injected."""
    return (
        SYSTEM_PROMPT_TEMPLATE.format(
            category_hints=category_hints,
            bargain_hint=bargain_hint,
            price_position_label=price_position_label,
        )
        + JSON_SCHEMA
    )

# Category-specific analysis hints injected into the prompt
CATEGORY_HINTS: dict[str, dict[str, str]] = {
    "phone": {
        "category_hints": (
            "Проверь батарею (ёмкость, циклы зарядки), экран "
            "(битые пиксели, олеофобку, равномерность), камеры (все линзы), "
            "разъём зарядки (люфт), Touch ID/Face ID (работает ли), "
            "Activation Lock (отвязан ли от Apple ID), "
            "звук (динамики, микрофон), кнопки (качество отклика)."
        ),
        "bargain_hint": (
            "Износ батареи ниже 80% — аргумент для -10-15% цены. "
            "Царапины на экране — ещё -5-10%. "
            "Отсутствие коробки/чека — -5%. "
            "Сравни с ценой аналогов в таком же состоянии."
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
            "Износ батареи >500 циклов — аргумент для -15-20%. "
            "Устаревшие порты, царапины на корпусе — -5-10%. "
            "Отсутствие гарантии — -10%. "
            "Сравни с новыми моделями в этом ценовом диапазоне."
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
            "Возраст и пробег: каждый год сверх среднего — -5%. "
            "Необходимость ремонта — скидка на стоимость ремонта + 20%. "
            "Сезонность: зимой кабриолеты/мото дешевле, весной — дороже."
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
            "Разрешение ниже 4K для диагонали >50\" — аргумент для скидки."
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
    "phone": ["iphone", "samsung galaxy", "xiaomi", "pixel", "huawei", "honor", "oneplus",
              "телефон", "смартфон", "phone", "redmi", "note ", "pro max", "pro plus",
              "galaxy s", "galaxy a", "galaxy m", "iphone 1", "iphone se", "poco "],
    "laptop": ["macbook", "ноутбук", "laptop", "thinkpad", "lenovo ", "asus ", "hp ",
               "acer ", "msi ", "dell ", "surface", "пк", "компьютер", "игровой ноутбук",
               "ultrabook", "legion", "rog ", "predator ", "vivobook", "ideapad"],
    "tablet": ["ipad", "tablet", "планшет", "galaxy tab", "tab s", "matepad", "ipad air",
               "ipad pro", "ipad mini"],
    "auto": ["автомобиль", "седан", "хэтчбек", "универсал", "кроссовер", "внедорожник",
             "кузов", "двигатель", "пробег", "л.с.", "vin", "bmw ", "audi ",
             "mercedes", "volkswagen", "toyota", "honda", "ford ", "hyundai",
             "kia ", "nissan", "skoda", "mazda", "opel ", "renault", "peugeot",
             "объём", "куб.см", "легковой", "минивэн", "пикап"],
    "motorcycle": ["мотоцикл", "скутер", "мопед", "эндуро", "кросс", "мотард", "chopper",
                   "квадроцикл", "байк", "мотороллер"],
    "tv": ["телевизор", "tv ", "smart tv", "oled", "qled", "led tv",
           "samsung q", "lg oled", "панель", "монитор ", "monitor"],
    "headphones": ["наушники", "headphones", "airpods", "galaxy buds", "sony wh", "bose",
                   "jbl ", "маршал", "marshall", "airpods pro", "airpods max",
                   "beats ", "sennheiser", "audio-technica", "наушник"],
    "console": ["playstation", "xbox", "nintendo", "switch", "ps5", "ps4", "приставка",
                "консоль", "геймпад", "ps ", "xbox one", "xbox series"],
    "watch": ["часы", "apple watch", "galaxy watch", "smartwatch", "умные часы",
              "фитнес-браслет", "mi band", "garmin", "часы ", "watch "],
    "camera": ["фотоаппарат", "камера", "camera", "canon ", "nikon ", "sony alpha",
               "объектив", "lens", "зеркалка", "беззеркалн", "фото", "goPro"],
    "bicycle": ["велосипед", "bike", "велик", "горный велосипед", "шоссейный",
                "bmx", "кросс-кантри", "двухподвес", "хардтейл", "электровелосипед"],
    "appliance": ["холодильник", "стиральн", "пылесос", "микроволнов", "печь",
                  "духовой шкаф", "посудомо", "кондиционер", "бойлер", "утюг",
                  "фен", "блендер", "кофемашина", "кофеварка", "тостер",
                  "мультиварка", "roboclean", "робот-пылесос", "сушильн",
                  "варочн", "вытяжк", "морозильн", "dishwasher", "refrigerator"],
    "furniture": ["диван", "кровать", "шкаф", "стол ", "стул ", "кресло", "тумба",
                  "комод", "полка", "стеллаж", "обеденн", "журнальн",
                  "кухонный гарнитур", "мебель", "софа", "пуфик", "матрас"],
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
Ответь ТОЛЬКО JSON (без markdown):
{"condition": "Отличное|Хорошее|Удовлетворительное|Требует внимания", "notes": ["заметка1"]}
Никаких личных данных. Отвечай на русском.
"""


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
                "timeout": httpx.Timeout(connect=15, read=300, write=10, pool=10),
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
            "temperature": 0.3,
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
        except Exception as e:
            logger.error("AI _chat request failed: %s: %s", type(e).__name__, e)
            raise
        logger.info("AI _chat response: status=%d", resp.status_code)
        if resp.status_code == 429:
            raise Exception("429 RATE_LIMITED")
        if resp.status_code == 402:
            raise Exception("Insufficient balance")
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
    ) -> dict:
        """Full AI analysis of a listing with optional comparison to alternatives."""
        # Build category-aware system prompt
        category = detect_category(title, parameters)
        hints = CATEGORY_HINTS.get(category, CATEGORY_HINTS["default"])
        system = _build_system_prompt(
            category_hints=hints["category_hints"],
            bargain_hint=hints["bargain_hint"],
            price_position_label=self._price_position_label(
                price_byn, market_median, market_q1, market_q3
            ),
        )

        context = self._build_listing_context(
            title=title,
            description=description,
            price_byn=price_byn,
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
        )

        content: list[dict] = [{"type": "text", "text": context}]

        # Fetch up to 2 target images only (no similar listing images — too slow).
        # Gemma 4 on Together AI takes ~75s per image; sending more causes timeout.
        fetch_tasks: list[tuple[str, str | None, str | None]] = []
        for url in image_urls[:1]:
            fetch_tasks.append(("target", url, None))

        if fetch_tasks:
            results = await asyncio.gather(
                *(
                    self._fetch_image_b64(url)
                    for _, url, _ in fetch_tasks
                ),
                return_exceptions=True,
            )
            for (kind, _url, label), result in zip(fetch_tasks, results, strict=True):
                if isinstance(result, Exception):
                    logger.warning("Skipping %s image (fetch error): %s", kind, result)
                    continue
                if result is None:
                    continue
                content.append(result)

        try:
            return await self._chat(
                system=system,
                content=content if len(content) > 1 else context,
                max_tokens=2500,
            )
        except Exception as e:
            err = str(e).lower()
            if len(content) > 1 and (
                "image" in err or "vision" in err or "multimodal" in err
            ):
                logger.warning("Vision not supported, retrying text-only: %s", e)
                return await self._chat(
                    system=system, content=context, max_tokens=2500
                )
            raise

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
            raise Exception("Не удалось загрузить фото для анализа")
        return await self._chat(
            system=QUICK_CONDITION_PROMPT, content=content, max_tokens=256
        )

    # ── Helpers ─────────────────────────────────────────────────

    @staticmethod
    def _price_position_label(
        price: float,
        median: float | None,
        q1: float | None,
        q3: float | None,
    ) -> str:
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
    ) -> str:
        # Detect category for display in context
        category = detect_category(title, parameters)

        # Pre-compute price position relative to market
        price_pos = "позиция неизвестна"
        if market_median and market_q1 and market_q3:
            if price_byn <= market_q1:
                price_pos = "НИЖЕ Q1 — дешёвое предложение"
            elif price_byn <= market_median:
                price_pos = "между Q1 и медианой — ниже средней"
            elif price_byn <= market_q3:
                price_pos = "между медианой и Q3 — средняя цена"
            else:
                price_pos = "ВЫШЕ Q3 — дорогое предложение"
        elif market_median:
            delta = (price_byn - market_median) / market_median * 100
            if delta < -10:
                price_pos = "значительно ниже медианы"
            elif delta < 0:
                price_pos = "немного ниже медианы"
            elif delta < 10:
                price_pos = "около медианы"
            else:
                price_pos = "значительно выше медианы"

        parts = [f"Объявление: {title}"]
        parts.append(f"Цена: {price_byn:.0f} BYN ({price_pos})")
        if condition:
            parts.append(f"Состояние: {condition}")
        if seller_type:
            parts.append(f"Продавец: {'магазин' if seller_type == 'shop' else 'частное лицо'}")
        if market_median:
            delta_pct = (price_byn - market_median) / market_median * 100 if market_median else 0
            parts.append(f"Рынок: медиана {market_median:.0f} BYN ({delta_pct:+.0f}%), {market_count} объявлений")
            if market_q1 and market_q3:
                parts.append(f"Q1={market_q1:.0f} Q3={market_q3:.0f} BYN")
        if parameters:
            params_str = ", ".join(
                f"{p.get('label', '')}: {p.get('value', '')}" for p in parameters[:6]
            )
            parts.append(f"Параметры: {params_str}")
        if description:
            parts.append(f"Описание: {description[:400]}")
        if similar_listings:
            parts.append("Альтернативы:")
            for i, sl in enumerate(similar_listings[:3], 1):
                parts.append(
                    f"  {i}. [{sl.get('ad_id')}] {sl.get('title', '')[:50]} — "
                    f"{sl.get('price_byn', 0):.0f} BYN, "
                    f"{sl.get('condition') or 'не указано'}"
                )

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
        except Exception as e:
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
        except Exception as e:
            logger.debug("Image compression skipped: %s", e)
            return None

    def _parse_json(self, text: str) -> dict:
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

        logger.warning("AI returned non-JSON: %s", text[:300])
        return {"summary": text.strip(), "condition": None, "fair_price": None}


# Singleton
_ai_service: AIService | None = None


def get_ai_service() -> AIService:
    global _ai_service
    if _ai_service is None:
        _ai_service = AIService()
    return _ai_service
