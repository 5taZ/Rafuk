"""AI service — OpenAI-compatible API (Together AI, DeepSeek, OpenAI, etc.).

Configure via .env:
  AI_API_KEY=<key>
  AI_BASE_URL=https://api.together.xyz/v1
  AI_MODEL=google/gemma-4-31B-it
"""

from __future__ import annotations

import json
import logging
import re

import httpx

from api.config import get_settings

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
Ты — Rafuks AI, аналитический помощник маркетплейса Kufar.by (Беларусь).
Ты анализируешь объявления и помогаешь покупателю принять обоснованное решение.

КРИТИЧЕСКИЕ ПРАВИЛА:
- Ты ИНФОРМАЦИОННЫЙ сервис, НЕ финансовый консультант
- Никогда не гарантируй прибыль и не обещай финансовый результат
- Используй формулировки: "ориентировочно", "на основе рыночных данных", "аналитическая оценка"
- Никогда не раскрывай имена продавцов, номера телефонов, названия ИП
- Отвечай ТОЛЬКО на русском языке

Проведи глубокий анализ:
1. Реальное состояние товара — по фото и описанию определи износ, дефекты, комплектацию
2. Справедливая цена — за сколько реально можно купить с учётом состояния и рынка
3. На что обратить внимание — дефекты на фото, подозрительные моменты в описании
4. Чек-лист для встречи — что обязательно проверить при осмотре товара
5. Как торговаться — конкретные аргументы для получения скидки
6. Красные флаги — признаки мошенничества, проблемного товара или продавца
7. Сравнение с альтернативами — если есть похожие объявления, укажи лучший вариант
8. Контекст рынка — как это объявление выглядит на фоне остальных

Формат ответа — строго JSON (без markdown-обёрток):
{
  "condition": {
    "label": "Отличное|Хорошее|Удовлетворительное|Требует внимания",
    "confidence": 0.0-1.0,
    "notes": ["что видно на фото", "заметка о состоянии"]
  },
  "fair_price": {
    "from": число_в_BYN,
    "to": число_в_BYN,
    "reasoning": "почему такой диапазон, что учтено"
  },
  "watch_out": [
    {"point": "на что обратить внимание", "why": "почему это важно"}
  ],
  "meeting_checklist": [
    "конкретный пункт проверки при встрече с продавцом",
    "ещё один важный пункт"
  ],
  "negotiation_tips": [
    "конкретный аргумент для торга со ссылкой на состояние или рынок",
    "ещё один аргумент"
  ],
  "red_flags": [
    "конкретный признак проблемы или мошенничества"
  ],
  "market_context": "2-3 предложения о позиции товара на рынке, сколько \
похожих предложений, динамика цен",
  "best_pick": {
    "ad_id": номер_лучшего_из_альтернатив_или_null,
    "reason": "почему этот вариант лучше текущего, или null если текущий лучший"
  },
  "recommendation": {
    "verdict": "worth_it|think_twice|overpriced",
    "text": "3-4 предложения с рекомендациями: стоит ли брать, за какую цену, \
на что обратить особое внимание при встрече"
  },
  "summary": "Краткое резюме в 1-2 предложениях"
}
"""

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
                "timeout": httpx.Timeout(connect=15, read=180, write=10, pool=10),
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
        body: dict = {
            "model": self._model,
            "messages": messages,
            "max_tokens": max_tokens,
            "response_format": {"type": "json_object"},
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
        text = data["choices"][0]["message"]["content"]
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
    ) -> dict:
        """Full AI analysis of a listing with optional comparison to alternatives."""
        context = self._build_listing_context(
            title=title,
            description=description,
            price_byn=price_byn,
            condition=condition,
            parameters=parameters,
            market_median=market_median,
            market_count=market_count,
            similar_listings=similar_listings,
        )

        content: list[dict] = [{"type": "text", "text": context}]

        # Target listing images — limit to 2 to reduce timeout risk
        for url in image_urls[:2]:
            try:
                img = await self._fetch_image_b64(url)
                if img:
                    content.append(img)
            except Exception as e:
                logger.warning("Skipping target image (fetch error): %s", e)

        # Similar listing images — only 1 image from the top alternative
        if similar_listings:
            sl = similar_listings[0]
            sl_images = sl.get("image_urls") or []
            if sl_images:
                try:
                    img = await self._fetch_image_b64(sl_images[0])
                    if img:
                        content.append(
                            {"type": "text", "text": f"[Альтернатива: {sl.get('title', '')[:60]}]"}
                        )
                        content.append(img)
                except Exception as e:
                    logger.warning("Skipping similar image (fetch error): %s", e)

        try:
            return await self._chat(
                system=SYSTEM_PROMPT,
                content=content if len(content) > 1 else context,
                max_tokens=2000,
            )
        except Exception as e:
            err = str(e).lower()
            if len(content) > 1 and (
                "image" in err or "vision" in err or "multimodal" in err
            ):
                logger.warning("Vision not supported, retrying text-only: %s", e)
                return await self._chat(
                    system=SYSTEM_PROMPT, content=context, max_tokens=2000
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
        similar_listings: list[dict] | None = None,
    ) -> str:
        parts = [f"Объявление: {title}"]
        parts.append(f"Цена: {price_byn:.0f} BYN")
        if condition:
            parts.append(f"Состояние: {condition}")
        if market_median:
            delta_pct = (
                (price_byn - market_median) / market_median * 100
                if market_median
                else 0
            )
            parts.append(
                f"Медиана рынка: {market_median:.0f} BYN "
                f"(отклонение: {delta_pct:+.0f}%)"
            )
        parts.append(f"Объявлений на рынке: {market_count}")
        if parameters:
            params_str = ", ".join(
                f"{p.get('label', p.get('key', ''))}: "
                f"{p.get('value', '')}"
                for p in parameters[:10]
            )
            parts.append(f"Параметры: {params_str}")
        if description:
            desc = description[:500] + (
                "..." if len(description) > 500 else ""
            )
            parts.append(f"Описание: {desc}")
        if similar_listings:
            parts.append("\nДругие объявления для сравнения:")
            for i, sl in enumerate(similar_listings[:5], 1):
                sl_cond = sl.get("condition") or "не указано"
                sl_price = sl.get("price_byn", 0)
                line = (
                    f"  {i}. [{sl.get('ad_id')}] "
                    f"{sl.get('title', '')[:70]} — "
                    f"{sl_price:.0f} BYN, состояние: {sl_cond}"
                )
                sl_desc = sl.get("description", "")
                if sl_desc:
                    line += f"\n     Описание: {sl_desc[:150]}"
                parts.append(line)
        return "\n".join(parts)

    async def _fetch_image_b64(self, url: str) -> dict | None:
        """Download image using shared client and return as vision content dict."""
        data = await self._fetch_image_bytes(url)
        if not data:
            return None
        import base64

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

    def _parse_json(self, text: str) -> dict:
        """Parse JSON from model response."""
        text = text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[-1]
        if text.endswith("```"):
            text = text.rsplit("```", 1)[0]
        json_match = re.search(r"\{.*\}", text.strip(), flags=re.DOTALL)
        if json_match:
            text = json_match.group(0)
        try:
            return json.loads(text.strip())
        except json.JSONDecodeError:
            logger.warning("AI returned non-JSON: %s", text[:200])
            return {"summary": text.strip(), "condition": None, "fair_price": None}


# Singleton
_ai_service: AIService | None = None


def get_ai_service() -> AIService:
    global _ai_service
    if _ai_service is None:
        _ai_service = AIService()
    return _ai_service
