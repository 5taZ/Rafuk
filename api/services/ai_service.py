"""AI service — supports Gemini API and OpenAI-compatible providers.

Switch provider by changing .env:
  # Gemini 2.5 Flash (via proxy for BY/RU)
  AI_API_KEY=AQ.xxx
  AI_BASE_URL=https://generativelanguage.googleapis.com
  AI_MODEL=gemini-2.5-flash
  AI_PROXY_URL=http://user:pass@host:port

  # DeepSeek / GLM / OpenAI
  AI_API_KEY=sk-xxx
  AI_BASE_URL=https://api.deepseek.com
  AI_MODEL=deepseek-chat
"""

from __future__ import annotations

import base64
import json
import logging
import re

import httpx

from api.config import get_settings

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
Ты — Rafuks AI, аналитический помощник маркетплейса Kufar.by (Беларусь).
Ты анализируешь объявления и помогаешь покупателю принять решение.

КРИТИЧЕСКИЕ ПРАВИЛА:
- Ты ИНФОРМАЦИОННЫЙ сервис, НЕ финансовый консультант
- Никогда не гарантируй прибыль и не обещай финансовый результат
- Используй формулировки: "ориентировочно", "на основе рыночных данных", "аналитическая оценка"
- Никогда не раскрывай имена продавцов, номера телефонов, названия ИП
- Отвечай ТОЛЬКО на русском языке

Что нужно оценить:
1. За сколько реально можно купить этот товар — укажи справедливый диапазон цен
2. На что обратить внимание при покупке — дефекты на фото, \
подозрительные моменты в описании, важные параметры
3. Рекомендации — стоит ли покупать, на что согласиться, \
где уступить, что проверить при встрече

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
  "recommendation": {
    "verdict": "worth_it|think_twice|overpriced",
    "text": "2-3 предложения с рекомендациями: стоит ли брать, \
за какую цену, что проверить при встрече"
  },
  "summary": "Краткое резюме в 1-2 предложениях"
}
"""

QUICK_CONDITION_PROMPT = """\
Ты — Rafuks AI. Оцени состояние товара по фото.
Ответь ТОЛЬКО JSON (без markdown):
{"condition": "Отличное|Хорошее|Удовлетворительное|Требует внимания", "notes": ["заметка1"]}
Никаких личных данных. Отвегай на русском.
"""

SEARCH_BY_PHOTO_PROMPT = """\
Ты — Rafuks AI. Опиши товар на фото в формате поискового запроса для маркетплейса Kufar.by.
Ответь ТОЛЬКО JSON (без markdown):
{"query": "поисковый запрос на русском", "description": "краткое описание товара"}
Будь конкретен: укажи бренд, модель, характеристики если видны. Отвечай на русском.
"""


def _is_gemini(base_url: str) -> bool:
    return "generativelanguage.googleapis.com" in base_url or "googleapis.com" in base_url


class AIService:
    """Unified AI service — Gemini SDK or OpenAI-compatible API."""

    def __init__(self) -> None:
        settings = get_settings()
        self._api_key = settings.ai_api_key
        self._base_url = (
            settings.ai_base_url or "https://api.deepseek.com"
        ).rstrip("/")
        self._model = settings.ai_model or "deepseek-chat"
        self._max_images = settings.ai_max_images
        self._proxy_url = settings.ai_proxy_url
        self._use_gemini = _is_gemini(self._base_url)
        self._httpx_client: httpx.AsyncClient | None = None

    @property
    def available(self) -> bool:
        return self._api_key is not None

    def _get_httpx(self) -> httpx.AsyncClient:
        if self._httpx_client is None or self._httpx_client.is_closed:
            self._httpx_client = httpx.AsyncClient(
                timeout=120,
                proxy=self._proxy_url or None,
            )
        return self._httpx_client

    # ── Gemini native REST API ──────────────────────────────────

    async def _gemini_chat(
        self,
        *,
        system: str,
        text: str,
        image_parts: list[dict] | None = None,
        max_tokens: int = 1200,
    ) -> dict:
        """Call Gemini generateContent REST API via proxy."""
        parts: list[dict] = []
        if image_parts:
            parts.extend(image_parts)
        parts.append({"text": text})

        # Gemini 2.5 Flash is a thinking model — thoughts count toward
        # maxOutputTokens, so we need a much higher limit than the
        # actual desired output size.
        gemini_max_tokens = max(max_tokens * 6, 8192)

        body: dict = {
            "contents": [{"role": "user", "parts": parts}],
            "systemInstruction": {"parts": [{"text": system}]},
            "generationConfig": {
                "maxOutputTokens": gemini_max_tokens,
                "responseMimeType": "application/json",
            },
        }

        api_key = self._api_key.get_secret_value() if self._api_key else ""
        url = (
            f"{self._base_url}/v1beta/models/{self._model}"
            f":generateContent?key={api_key}"
        )

        client = self._get_httpx()
        resp = await client.post(
            url,
            headers={"Content-Type": "application/json"},
            json=body,
        )

        if resp.status_code == 429:
            raise Exception("429 RESOURCE_EXHAUSTED")
        resp.raise_for_status()

        data = resp.json()
        resp_text = self._extract_gemini_text(data)
        return self._parse_json(resp_text)

    # ── OpenAI-compatible API ───────────────────────────────────

    async def _openai_chat(
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
        client = self._get_httpx()
        resp = await client.post(
            f"{self._base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=body,
        )

        if resp.status_code == 429:
            raise Exception("429 RESOURCE_EXHAUSTED")
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
    ) -> dict:
        """Full AI analysis of a listing."""
        context = self._build_listing_context(
            title=title,
            description=description,
            price_byn=price_byn,
            condition=condition,
            parameters=parameters,
            market_median=market_median,
            market_count=market_count,
        )

        if self._use_gemini:
            img_parts = await self._fetch_images_gemini(
                image_urls[: self._max_images]
            )
            return await self._gemini_chat(
                system=SYSTEM_PROMPT,
                text=context,
                image_parts=img_parts or None,
                max_tokens=1200,
            )

        # OpenAI-compatible — with vision fallback
        content: list[dict] = [{"type": "text", "text": context}]
        for url in image_urls[: self._max_images]:
            img = await self._fetch_image_b64(url)
            if img:
                content.append(img)

        try:
            return await self._openai_chat(
                system=SYSTEM_PROMPT,
                content=content if len(content) > 1 else context,
                max_tokens=1200,
            )
        except Exception as e:
            err = str(e).lower()
            if len(content) > 1 and (
                "image" in err or "vision" in err or "multimodal" in err
            ):
                logger.warning("Vision not supported, retrying text-only: %s", e)
                return await self._openai_chat(
                    system=SYSTEM_PROMPT, content=context, max_tokens=1200
                )
            raise

    async def quick_condition(self, image_urls: list[str]) -> dict:
        """Quick condition assessment from photos only."""
        if self._use_gemini:
            img_parts = await self._fetch_images_gemini(
                image_urls[: self._max_images]
            )
            if not img_parts:
                raise Exception("Не удалось загрузить фото для анализа")
            return await self._gemini_chat(
                system=QUICK_CONDITION_PROMPT,
                text="Оцени состояние товара на фото.",
                image_parts=img_parts,
                max_tokens=256,
            )

        # OpenAI-compatible
        content: list[dict] = [
            {"type": "text", "text": "Оцени состояние товара на фото."},
        ]
        for url in image_urls[: self._max_images]:
            img = await self._fetch_image_b64(url)
            if img:
                content.append(img)
        if len(content) == 1:
            raise Exception("Не удалось загрузить фото для анализа")
        return await self._openai_chat(
            system=QUICK_CONDITION_PROMPT, content=content, max_tokens=256
        )

    async def search_by_photo_from_bytes(
        self, image_bytes: bytes, mime_type: str
    ) -> dict:
        """Generate search query from uploaded photo bytes."""
        if self._use_gemini:
            img_part = {
                "inlineData": {
                    "mimeType": mime_type,
                    "data": base64.b64encode(image_bytes).decode(),
                }
            }
            return await self._gemini_chat(
                system=SEARCH_BY_PHOTO_PROMPT,
                text="Опиши товар на фото для поиска на маркетплейсе.",
                image_parts=[img_part],
                max_tokens=256,
            )

        # OpenAI-compatible
        b64 = base64.b64encode(image_bytes).decode()
        content = [
            {"type": "text", "text": "Опиши товар на фото для поиска на маркетплейсе."},
            {
                "type": "image_url",
                "image_url": {"url": f"data:{mime_type};base64,{b64}"},
            },
        ]
        return await self._openai_chat(
            system=SEARCH_BY_PHOTO_PROMPT, content=content, max_tokens=256
        )

    async def search_by_photo(self, image_urls: list[str]) -> dict:
        """Generate search query from photo URLs."""
        if self._use_gemini:
            img_parts = await self._fetch_images_gemini(
                image_urls[: self._max_images]
            )
            return await self._gemini_chat(
                system=SEARCH_BY_PHOTO_PROMPT,
                text="Опиши товар на фото для поиска на маркетплейсе.",
                image_parts=img_parts or None,
                max_tokens=256,
            )

        # OpenAI-compatible
        content: list[dict] = [
            {"type": "text", "text": "Опиши товар на фото для поиска на маркетплейсе."},
        ]
        for url in image_urls[: self._max_images]:
            img = await self._fetch_image_b64(url)
            if img:
                content.append(img)
        return await self._openai_chat(
            system=SEARCH_BY_PHOTO_PROMPT, content=content, max_tokens=256
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
        return "\n".join(parts)

    async def _fetch_images_gemini(self, urls: list[str]) -> list[dict]:
        """Download images and return Gemini inlineData parts."""
        parts: list[dict] = []
        for url in urls:
            data = await self._fetch_image_bytes(url)
            if data:
                mime = "image/jpeg"
                if ".png" in url.lower():
                    mime = "image/png"
                elif ".webp" in url.lower():
                    mime = "image/webp"
                parts.append({
                    "inlineData": {
                        "mimeType": mime,
                        "data": base64.b64encode(data).decode(),
                    }
                })
        return parts

    async def _fetch_image_b64(self, url: str) -> dict | None:
        """Download image and return as OpenAI vision content dict."""
        data = await self._fetch_image_bytes(url)
        if not data:
            return None
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
        """Download image bytes (no proxy needed for Kufar CDN)."""
        try:
            async with httpx.AsyncClient(
                timeout=10, follow_redirects=True
            ) as client:
                resp = await client.get(url)
                if resp.status_code == 200:
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

    def _extract_gemini_text(self, payload: dict) -> str:
        prompt_feedback = payload.get("promptFeedback") or {}
        block_reason = prompt_feedback.get("blockReason")
        if block_reason:
            raise Exception(f"Gemini blocked response: {block_reason}")

        candidates = payload.get("candidates") or []
        if not candidates:
            raise Exception("AI model returned an empty response")

        parts = candidates[0].get("content", {}).get("parts", [])
        texts = [str(part.get("text", "")).strip() for part in parts if part.get("text")]
        if texts:
            return "\n".join(texts)

        finish_reason = candidates[0].get("finishReason") or "unknown"
        raise Exception(f"AI model returned no text ({finish_reason})")


# Singleton
_ai_service: AIService | None = None


def get_ai_service() -> AIService:
    global _ai_service
    if _ai_service is None:
        _ai_service = AIService()
    return _ai_service
