"""AI Analysis router — listing analysis and quick condition assessment."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request

from api.dependencies import get_cache, get_telegram_user
from api.schemas import (
    AIAnalysisRequest,
    AIAnalysisResponse,
    AIQuickConditionRequest,
    AIQuickConditionResponse,
)
from api.services.aggregator import normalize_price_byn
from api.services.ai_service import get_ai_service
from api.services.kufar_client import KufarClient
from api.services.listing_mapper import first_image_url
from api.services.query_pipeline import load_query_dataset

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ai", tags=["ai"])

DISCLAIMER = "Анализ носит информационный характер. Результаты не являются гарантией."


def _check_ai_available():
    service = get_ai_service()
    if not service.available:
        raise HTTPException(status_code=503, detail="AI analysis is not configured")
    return service


async def _check_rate_limit(request: Request, user_id: int) -> None:
    """Simple per-user rate limit using the configured cache backend."""
    cache = get_cache(request)
    settings = getattr(request.app.state, "settings", None)
    hourly_limit = int(getattr(settings, "ai_hourly_limit", 10) or 10)

    key = f"ai_rate:{user_id}"
    raw_count = await cache.get(key)
    try:
        count = int(raw_count or 0)
    except (TypeError, ValueError):
        count = 0

    if count >= hourly_limit:
        raise HTTPException(
            status_code=429,
            detail=f"Превышен лимит AI-анализов ({hourly_limit} в час)",
        )

    await cache.set(key, str(count + 1), ttl=3600)


def _collect_similar_listings(
    dataset, target_ad_id: int, target_price: float, median: float | None
) -> list[dict]:
    """Collect similar listings with rich data for AI comparison."""
    if not median or target_price <= 0:
        return []

    price_min = target_price * 0.7
    price_max = target_price * 1.15
    similar: list[dict] = []

    for ad in dataset.ads:
        ad_id = int(ad.get("ad_id", 0))
        if ad_id == target_ad_id:
            continue
        ad_price = normalize_price_byn(ad.get("price_byn")) or 0.0
        if ad_price <= 0:
            continue
        if price_min <= ad_price <= price_max:
            ad_condition = None
            for param in ad.get("ad_parameters", []):
                if param.get("p") == "condition":
                    ad_condition = param.get("vl") or param.get("v")
                    break
            ad_desc = (ad.get("body", "") or ad.get("description", "")) or ""
            ad_images = []
            for img in (ad.get("images") or [])[:3]:
                path = img.get("path", "")
                if path:
                    ad_images.append(f"https://rms.kufar.by/v1/gallery/{path}")
            similar.append(
                {
                    "ad_id": ad_id,
                    "title": ad.get("subject", "")[:80],
                    "price_byn": ad_price,
                    "image_url": first_image_url(ad),
                    "image_urls": ad_images,
                    "link": ad.get("ad_link", f"https://www.kufar.by/item/{ad_id}"),
                    "deal_score": 0.0,
                    "condition": ad_condition,
                    "description": ad_desc[:200],
                }
            )
    similar.sort(key=lambda item: item["price_byn"])
    return similar[:5]


@router.post("/analyze", response_model=AIAnalysisResponse)
async def analyze_listing(
    payload: AIAnalysisRequest,
    request: Request,
    _user=Depends(get_telegram_user),
):
    """Full AI analysis of a listing."""
    ai = _check_ai_available()
    await _check_rate_limit(request, _user.user_id)

    cache = get_cache(request)
    cache_key = f"ai_analysis:{payload.ad_id}:{payload.query}"
    cached = await cache.get_json(cache_key)
    if cached:
        return AIAnalysisResponse(**cached)

    settings = getattr(request.app.state, "settings", None)
    if settings is None:
        from api.config import get_settings

        settings = get_settings()

    # Try strict search first — yields more relevant comparable listings
    logger.info(
        "AI analyze: loading dataset for ad_id=%d query=%s",
        payload.ad_id, payload.query[:50],
    )
    dataset = await load_query_dataset(
        query=payload.query,
        currency="BYN",
        strict_search=True,
        settings=settings,
        client_factory=KufarClient,
    )

    target_ad = next(
        (ad for ad in dataset.ads if int(ad.get("ad_id", 0)) == payload.ad_id),
        None,
    )

    # Fallback to non-strict if target not found in strict results
    if not target_ad:
        dataset = await load_query_dataset(
            query=payload.query,
            currency="BYN",
            strict_search=False,
            settings=settings,
            client_factory=KufarClient,
        )
        target_ad = next(
            (ad for ad in dataset.ads if int(ad.get("ad_id", 0)) == payload.ad_id),
            None,
        )

    if not target_ad:
        raise HTTPException(status_code=404, detail="Объявление не найдено")

    title = target_ad.get("subject", "") or target_ad.get("title", "")
    description = target_ad.get("body", "") or target_ad.get("description", "")
    price_byn = normalize_price_byn(target_ad.get("price_byn")) or 0.0

    condition = None
    for param in target_ad.get("ad_parameters", []):
        if param.get("p") == "condition":
            condition = param.get("vl") or param.get("v")
            break

    parameters = []
    for param in target_ad.get("ad_parameters", []):
        param_key = param.get("p", "")
        if param_key in {"condition", "currency", "price", "users_synonyms"}:
            continue
        label = param.get("pl") or param_key
        value = param.get("vl") or str(param.get("v", ""))
        if label and value:
            parameters.append({"label": label, "value": value})

    images = []
    for img in (target_ad.get("images") or [])[:3]:
        path = img.get("path", "")
        if path:
            images.append(f"https://rms.kufar.by/v1/gallery/{path}")

    median = dataset.price_stats.median if dataset.price_stats else None
    count = dataset.price_stats.count if dataset.price_stats else 0

    # Collect similar listings with rich data
    similar = _collect_similar_listings(dataset, payload.ad_id, price_byn, median)

    # Prepare similar listings for AI comparison (with images and descriptions)
    ai_similar_for_comparison = [
        {
            "ad_id": s["ad_id"],
            "title": s["title"],
            "price_byn": s["price_byn"],
            "condition": s.get("condition"),
            "description": s.get("description", ""),
            "image_urls": s.get("image_urls", []),
        }
        for s in similar
    ]

    try:
        logger.info(
            "AI analyze: calling ai.analyze_listing for '%s' (%d imgs, %d similar)",
            title[:50], len(images), len(ai_similar_for_comparison),
        )
        result = await ai.analyze_listing(
            title=title,
            description=description,
            price_byn=price_byn,
            condition=condition,
            parameters=parameters,
            market_median=median,
            market_count=count,
            image_urls=images,
            similar_listings=ai_similar_for_comparison,
        )
        logger.info("AI analyze: success, keys=%s", list(result.keys()))
    except Exception as exc:
        logger.error(
            "AI analysis failed: [%s] %s\nProxy: %s",
            type(exc).__name__, exc, "yes" if settings.ai_proxy_url else "no",
        )
        err_msg = "AI сервис недоступен. Попробуйте позже."
        err_str = str(exc).lower()
        if "429" in err_str or "resource_exhausted" in err_str or "quota" in err_str:
            err_msg = "Лимит AI-анализов исчерпан. Попробуйте через минуту."
        elif (
            "timeout" in err_str
            or "timed out" in err_str
            or "connect" in err_str
            or "connection" in err_str
        ):
            err_msg = (
                "Не удалось подключиться к AI-сервису. "
                "Возможно, требуется VPN на сервере."
            )
        # In debug mode, append actual error for diagnostics
        if settings and getattr(settings, "debug", False):
            err_msg += f" [{type(exc).__name__}: {exc}]"
        raise HTTPException(status_code=502, detail=err_msg) from None

    # Determine best alternative based on AI's best_pick
    best_pick_ad_id = None
    best_pick_reason = ""
    bp = result.get("best_pick") or {}
    if isinstance(bp, dict):
        best_pick_ad_id = bp.get("ad_id")
        best_pick_reason = bp.get("reason", "")

    best_alternative = None
    if best_pick_ad_id:
        best_alternative = next(
            (s for s in similar if s["ad_id"] == best_pick_ad_id), None
        )
    if not best_alternative and similar:
        # Fallback: cheapest similar listing
        best_alternative = similar[0]

    if best_alternative:
        best_alternative = {
            "ad_id": best_alternative["ad_id"],
            "title": best_alternative["title"],
            "price_byn": best_alternative["price_byn"],
            "image_url": best_alternative.get("image_url"),
            "link": best_alternative.get("link", ""),
            "deal_score": best_alternative.get("deal_score", 0.0),
            "condition": best_alternative.get("condition"),
            "ai_note": best_pick_reason if best_pick_ad_id == best_alternative["ad_id"] else "",
        }

    response = AIAnalysisResponse(
        ad_id=payload.ad_id,
        condition=result.get("condition"),
        fair_price=result.get("fair_price"),
        watch_out=result.get("watch_out", []),
        recommendation=result.get("recommendation"),
        similar_listings=similar,
        best_alternative=best_alternative,
        meeting_checklist=result.get("meeting_checklist", []),
        negotiation_tips=result.get("negotiation_tips", []),
        red_flags=result.get("red_flags", []),
        market_context=result.get("market_context", ""),
        best_pick_reason=best_pick_reason,
        summary=result.get("summary", ""),
        disclaimer=DISCLAIMER,
    )

    await cache.set_json(cache_key, response.model_dump(), ttl=3600)
    return response


@router.post("/quick-condition", response_model=AIQuickConditionResponse)
async def quick_condition(
    payload: AIQuickConditionRequest,
    request: Request,
    _user=Depends(get_telegram_user),
):
    """Quick condition assessment from listing photos."""
    ai = _check_ai_available()
    await _check_rate_limit(request, _user.user_id)

    settings = getattr(request.app.state, "settings", None)
    if settings is None:
        from api.config import get_settings

        settings = get_settings()

    dataset = await load_query_dataset(
        query=payload.query,
        currency="BYN",
        strict_search=False,
        settings=settings,
        client_factory=KufarClient,
    )

    target_ad = next(
        (ad for ad in dataset.ads if int(ad.get("ad_id", 0)) == payload.ad_id),
        None,
    )
    if not target_ad:
        raise HTTPException(status_code=404, detail="Объявление не найдено")

    images = []
    for img in (target_ad.get("images") or [])[:3]:
        path = img.get("path", "")
        if path:
            images.append(f"https://rms.kufar.by/v1/gallery/{path}")

    if not images:
        raise HTTPException(status_code=400, detail="Нет фото для анализа")

    try:
        result = await ai.quick_condition(images)
    except Exception as exc:
        logger.error("Quick condition failed: %s", exc)
        err_msg = "AI сервис недоступен"
        if "429" in str(exc) or "RESOURCE_EXHAUSTED" in str(exc):
            err_msg = "Лимит AI-анализов исчерпан. Попробуйте через минуту."
        raise HTTPException(status_code=502, detail=err_msg) from None

    return AIQuickConditionResponse(
        ad_id=payload.ad_id,
        condition=result.get("condition", ""),
        notes=result.get("notes", []),
    )
