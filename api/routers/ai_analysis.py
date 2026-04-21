"""AI Analysis router — listing analysis, quick condition, search by photo."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile

from api.dependencies import get_cache, get_telegram_user
from api.schemas import (
    AIAnalysisRequest,
    AIAnalysisResponse,
    AIQuickConditionRequest,
    AIQuickConditionResponse,
    AISearchByPhotoResponse,
)
from api.services.aggregator import normalize_price_byn
from api.services.ai_service import get_ai_service
from api.services.kufar_client import KufarClient
from api.services.listing_mapper import first_image_url
from api.services.photo_search_service import PhotoSearchError, get_photo_search_service
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

    try:
        result = await ai.analyze_listing(
            title=title,
            description=description,
            price_byn=price_byn,
            condition=condition,
            parameters=parameters,
            market_median=median,
            market_count=count,
            image_urls=images,
        )
    except Exception as exc:
        logger.error("AI analysis failed: %s", exc)
        err_msg = "AI сервис недоступен. Попробуйте позже."
        err_str = str(exc)
        if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str or "quota" in err_str.lower():
            err_msg = "Лимит AI-анализов исчерпан. Попробуйте через минуту."
        raise HTTPException(status_code=502, detail=err_msg) from None

    similar = []
    if median and price_byn > 0:
        price_min = price_byn * 0.7
        price_max = price_byn * 1.15
        for ad in dataset.ads:
            ad_id = int(ad.get("ad_id", 0))
            if ad_id == payload.ad_id:
                continue
            ad_price = normalize_price_byn(ad.get("price_byn")) or 0.0
            if ad_price <= 0:
                continue
            if price_min <= ad_price <= price_max:
                similar.append(
                    {
                        "ad_id": ad_id,
                        "title": ad.get("subject", "")[:80],
                        "price_byn": ad_price,
                        "image_url": first_image_url(ad),
                        "link": ad.get("ad_link", f"https://www.kufar.by/item/{ad_id}"),
                        "deal_score": 0.0,
                    }
                )
        similar.sort(key=lambda item: item["price_byn"])
        similar = similar[:5]

    best_alternative = similar[0] if similar else None

    response = AIAnalysisResponse(
        ad_id=payload.ad_id,
        condition=result.get("condition"),
        fair_price=result.get("fair_price"),
        watch_out=result.get("watch_out", []),
        recommendation=result.get("recommendation"),
        similar_listings=similar,
        best_alternative=best_alternative,
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


@router.post("/search-by-photo", response_model=AISearchByPhotoResponse)
async def search_by_photo(
    request: Request,
    photo: UploadFile = File(..., description="Photo of the item to search for"),
    _user=Depends(get_telegram_user),
):
    """Upload a photo -> AI or OCR identifies the item -> search Kufar."""
    await _check_rate_limit(request, _user.user_id)

    allowed_types = {"image/jpeg", "image/png", "image/webp", "image/jpg"}
    content_type = photo.content_type or ""
    if content_type not in allowed_types:
        raise HTTPException(
            status_code=422,
            detail="Поддерживаются только фото (JPEG, PNG, WebP)",
        )

    image_bytes = await photo.read()
    if len(image_bytes) > 10 * 1024 * 1024:
        raise HTTPException(status_code=422, detail="Фото слишком большое (максимум 10 МБ)")
    if len(image_bytes) < 100:
        raise HTTPException(status_code=422, detail="Файл слишком маленький")

    mime_type = "image/jpeg" if content_type == "image/jpg" else content_type
    photo_search = get_photo_search_service()
    ai_service = get_ai_service()

    try:
        identified = await photo_search.identify_from_bytes(image_bytes, mime_type, ai_service)
    except PhotoSearchError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    except Exception as exc:
        logger.error("Search by photo failed: %s", exc)
        raise HTTPException(status_code=502, detail="Не удалось обработать фото") from None

    settings = getattr(request.app.state, "settings", None)
    if settings is None:
        from api.config import get_settings

        settings = get_settings()

    client = KufarClient(settings)
    try:
        raw = await client.search(query=identified.query, limit=30)
    except Exception as exc:
        logger.error("Kufar search failed: %s", exc)
        raise HTTPException(status_code=502, detail="Не удалось выполнить поиск") from None
    finally:
        await client.aclose()

    ads = raw.get("ads", [])
    listings = []
    for ad in ads[:15]:
        price_byn = normalize_price_byn(ad.get("price_byn")) or 0.0
        listings.append(
            {
                "ad_id": int(ad.get("ad_id", 0)),
                "title": ad.get("subject", "")[:100],
                "price_byn": price_byn,
                "image_url": first_image_url(ad),
                "link": ad.get(
                    "ad_link",
                    f"https://www.kufar.by/item/{ad.get('ad_id', 0)}",
                ),
                "list_time": ad.get("list_time"),
            }
        )

    return AISearchByPhotoResponse(
        query=identified.query,
        description=identified.description,
        listings=listings,
        total=len(listings),
        source=identified.source,
        recognized_text=identified.recognized_text,
    )
