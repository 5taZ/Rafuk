"""AI Tools router — negotiate price and price advice endpoints."""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import ValidationError

from api.config import get_settings
from api.dependencies import get_cache, get_kufar_client, get_telegram_user
from api.routers.ai_analysis import (
    _check_ai_available,
    _check_ai_consent,
    _check_rate_limit,
    _coerce_string_list,
    _log_ai_audit,
)
from api.schemas import (
    AINegotiateRequest,
    AINegotiateResponse,
    AIPriceAdviceRequest,
    AIPriceAdviceResponse,
)
from api.services.ai_service import sanitize_user_text
from api.services.cache import digest_cache_key
from api.services.client_ip import get_client_ip

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ai", tags=["ai"])


# ── System prompts ────────────────────────────────────────────────────────


_NEGOTIATE_SYSTEM = """\
Ты — Rafuk AI, помощник покупателя на белорусском Kufar. Ты помогаешь
сформулировать текст для торга с продавцом.

Правила:
- Аудитория — Беларусь, Kufar. Все цены в BYN.
- Будь вежлив, но настойчив. Цель — получить скидку или обосновать цену.
- Упоминай конкретные аргументы: рыночную цену, состояние, аналоги.
- Не выдумывай факты — опирайся только на предоставленные данные.
- Генерируй текст на русском, готовый для копирования в чат Kufar.

Ответь строго JSON:
{
  "opening_line": "приветствие с указанием интереса к товару",
  "counter_offer_text": "текст предложения со скидкой и обоснованием",
  "fallback_text": "текст если продавец отказал — компромисс",
  "tips": ["конкретный совет по переговорам"]
}
"""

_PRICE_ADVICE_SYSTEM = """\
Ты — Rafuk AI, аналитик цен на белорусском Kufar. Ты помогаешь покупателю
решить: купить сейчас или подождать другого объявления.

КРИТИЧЕСКИ ВАЖНО: Твоя оценка НЕ является инвестиционной рекомендацией.
Ты анализируешь только текущий рыночный срез объявлений Kufar.
Рыночные цены могут изменяться непредсказуемо.

Правила:
- Аудитория — Беларусь, Kufar. Все цены в BYN.
- Опирайся на предоставленные рыночные данные (медиана, квартиль, количество).
- Не утверждай, что цены растут, падают или стабильны во времени: временного ряда нет.
- Если данных недостаточно для вывода — честно скажи "neutral".
- advice — строго одно из: "buy_now" (цена выгодная, редкий товар),
  "wait" (текущая цена выше сопоставимого среза), "neutral" (недостаточно данных).
- Будь конкретен: указывай суммы и проценты относительно текущего среза.
- НЕ гарантируй снижение или рост цены и не делай выводов о динамике рынка.

Ответь строго JSON:
{
  "advice": "buy_now или wait или neutral",
  "reasoning": "обоснование с конкретными данными",
  "market_context": "1-2 предложения о текущем рыночном срезе без динамики во времени",
  "confidence": 0.0-1.0
}
"""


def _ai_price_advice_cache_key(payload: AIPriceAdviceRequest) -> str:
    return digest_cache_key(
        "ai_price_advice",
        {
            "contract": "current-market-v1",
            "query": payload.query,
            "current_price_byn": payload.current_price_byn,
            "category": payload.category,
        },
    )


# ── Negotiate ─────────────────────────────────────────────────────────────


@router.post("/negotiate", response_model=AINegotiateResponse)
async def negotiate_price(
    payload: AINegotiateRequest,
    request: Request,
    _user=Depends(get_telegram_user),
):
    """Generate negotiation text for a buyer — counter-offer and tips."""
    ai = _check_ai_available()
    await _check_ai_consent(request, _user.user_id)
    await _check_rate_limit(request, _user.user_id, endpoint="negotiate")

    # AI audit trail (OPUS-17: capture client IP).
    await _log_ai_audit(
        request.app.state.session_factory,
        telegram_user_id=_user.user_id,
        endpoint="negotiate",
        ad_id=str(payload.ad_id),
        query=payload.query,
        model=get_settings().ai_model,
        ip_address=get_client_ip(request),
    )

    cache = get_cache(request)
    cache_key = f"ai_negotiate:{payload.ad_id}:{payload.my_offer_byn}:{payload.asking_price_byn}"
    cached = await cache.get_json(cache_key)
    if isinstance(cached, dict):
        try:
            return AINegotiateResponse.model_validate(cached)
        except ValidationError:
            pass

    # Build context
    safe_query = sanitize_user_text(payload.query) or ""
    safe_condition = sanitize_user_text(payload.condition) or ""
    safe_market = sanitize_user_text(payload.market_context) or ""
    user_content = (
        f"Товар: {safe_query}\n"
        f"Цена продавца: {payload.asking_price_byn} BYN\n"
        f"Моя цена: {payload.my_offer_byn} BYN\n"
    )
    if safe_condition:
        user_content += f"Состояние: {safe_condition}\n"
    if safe_market:
        user_content += f"Рыночный контекст: {safe_market}\n"

    try:
        result = await asyncio.wait_for(
            ai.chat_json(
                system=_NEGOTIATE_SYSTEM,
                content=user_content,
                max_tokens=800,
            ),
            timeout=60,
        )
    except TimeoutError:
        raise HTTPException(status_code=504, detail="AI перегружен, попробуйте позже") from None

    response = AINegotiateResponse(
        opening_line=str(result.get("opening_line") or "")[:300],
        counter_offer_text=str(result.get("counter_offer_text") or "")[:600],
        fallback_text=str(result.get("fallback_text") or "")[:400],
        tips=_coerce_string_list(result.get("tips"), limit=4, max_len=140),
    )

    await cache.set_json(cache_key, response.model_dump(mode="json"), ttl=1800)
    return response


# ── Price Advice ──────────────────────────────────────────────────────────


@router.post("/price-advice", response_model=AIPriceAdviceResponse)
async def price_advice(
    payload: AIPriceAdviceRequest,
    request: Request,
    _user=Depends(get_telegram_user),
):
    """Price timing advice — should I buy now or wait? NOT an investment recommendation."""
    ai = _check_ai_available()
    await _check_ai_consent(request, _user.user_id)
    await _check_rate_limit(request, _user.user_id, endpoint="price_advice")

    # AI audit trail (OPUS-17: capture client IP).
    await _log_ai_audit(
        request.app.state.session_factory,
        telegram_user_id=_user.user_id,
        endpoint="price_advice",
        query=payload.query,
        model=get_settings().ai_model,
        ip_address=get_client_ip(request),
    )

    cache = get_cache(request)
    cache_key = _ai_price_advice_cache_key(payload)
    cached = await cache.get_json(cache_key)
    if isinstance(cached, dict):
        try:
            return AIPriceAdviceResponse.model_validate(cached)
        except ValidationError:
            pass

    # Fetch market data for context
    kufar_client = get_kufar_client(request)
    from api.services.query_pipeline import load_query_dataset

    dataset = await load_query_dataset(
        query=payload.query,
        currency="byn",
        strict_search=True,
        settings=get_settings(),
        client=kufar_client,
        category=payload.category,
    )

    # Build context with market data
    stats = dataset.price_stats if dataset else None
    market_info = ""
    current_market_context = "Недостаточно текущих рыночных данных для уверенного сравнения."
    if stats:
        market_info = (
            f"Медиана рынка: {stats.median:.0f} BYN\n"
            f"Q1 (25%): {stats.q1:.0f} BYN\n"
            f"Q3 (75%): {stats.q3:.0f} BYN\n"
            f"Количество объявлений: {stats.count}\n"
            f"Минимальная цена: {stats.min:.0f} BYN\n"
            f"Максимальная цена: {stats.max:.0f} BYN\n"
        )
        current_market_context = (
            f"Текущий рыночный срез: медиана {stats.median:.0f} BYN, "
            f"межквартильный диапазон {stats.q1:.0f}–{stats.q3:.0f} BYN, "
            f"выборка {stats.count} объявлений."
        )

    safe_query = sanitize_user_text(payload.query) or ""
    user_content = (
        f"Запрос: {safe_query}\n"
        f"Текущая цена: {payload.current_price_byn} BYN\n"
    )
    if market_info:
        user_content += f"\nРыночные данные:\n{market_info}"

    try:
        result = await asyncio.wait_for(
            ai.chat_json(
                system=_PRICE_ADVICE_SYSTEM,
                content=user_content,
                max_tokens=800,
            ),
            timeout=60,
        )
    except TimeoutError:
        raise HTTPException(status_code=504, detail="AI перегружен, попробуйте позже") from None

    valid_advice_values = {"buy_now", "wait", "neutral"}
    raw_advice = str(result.get("advice") or "neutral")[:20]
    advice = raw_advice if raw_advice in valid_advice_values else "neutral"

    response = AIPriceAdviceResponse(
        advice=advice,
        reasoning=str(result.get("reasoning") or "")[:500],
        market_context=current_market_context[:400],
        confidence=float(result.get("confidence") or 0.0),
    )

    await cache.set_json(cache_key, response.model_dump(mode="json"), ttl=3600)
    return response
