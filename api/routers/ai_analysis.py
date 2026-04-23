"""AI Analysis router — listing analysis and quick condition assessment."""

from __future__ import annotations

import asyncio
import logging
import secrets
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from api.dependencies import get_cache, get_telegram_user
from api.schemas import (
    AIAnalysisRequest,
    AIAnalysisResponse,
    AIQuickConditionRequest,
    AIQuickConditionResponse,
)
from api.services.aggregator import normalize_price_byn
from api.services.ai_guardrails import apply_ai_market_guardrails
from api.services.ai_service import get_ai_service
from api.services.kufar_client import KufarClient
from api.services.listing_mapper import first_image_url
from api.services.query_pipeline import load_query_dataset

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ai", tags=["ai"])

# ── In-memory async task store ────────────────────────────────────────────
_tasks: dict[str, dict] = {}
_TASK_TTL = 3600  # 1 hour cleanup
_exports: dict[str, dict] = {}
_EXPORT_TTL = 900  # 15 minutes


def _prune_old_tasks() -> None:
    """Remove tasks older than TTL."""
    now = datetime.now(UTC).timestamp()
    expired = [tid for tid, t in _tasks.items()
               if now - t.get("_created_ts", 0) > _TASK_TTL]
    for tid in expired:
        _tasks.pop(tid, None)


def _prune_old_exports() -> None:
    """Remove expired HTML exports."""
    now = datetime.now(UTC).timestamp()
    expired = [
        token for token, item in _exports.items()
        if now - item.get("_created_ts", 0) > _EXPORT_TTL
    ]
    for token in expired:
        _exports.pop(token, None)


@router.get("/task/{task_id}")
async def get_task_status(task_id: str):
    """Poll AI analysis task status."""
    task = _tasks.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Задача не найдена")
    resp: dict = {"status": task["status"], "progress": task.get("progress", 0)}
    if task["status"] == "done" and task.get("result"):
        resp["result"] = task["result"]
    elif task["status"] == "error" and task.get("error"):
        resp["error"] = task["error"]
    return resp


DISCLAIMER = "Анализ носит информационный характер. Результаты не являются гарантией."


class AIExportReportRequest(BaseModel):
    html: str


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


def _parse_list_age_days(list_time_str: str | None) -> int | None:
    """Parse Kufar list_time to days-since-publication."""
    if not list_time_str:
        return None
    try:
        dt = datetime.fromisoformat(list_time_str.replace("Z", "+00:00"))
        # Make naive datetimes timezone-aware (assume UTC)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        now = datetime.now(UTC)
        delta = now - dt
        return max(0, delta.days)
    except (ValueError, TypeError):
        return None


def _collect_similar_listings(
    dataset,
    target_ad_id: int,
    target_price: float,
    median: float | None,
    target_condition: str | None = None,
) -> list[dict]:
    """Collect similar listings with rich data for AI comparison.

    Filters by price proximity (±30%) and prefers similar condition.
    For negotiable-price listings (price=0), uses median as reference.
    Returns up to 5 most relevant alternatives.
    """
    if not median:
        return []

    # Negotiable price: use median as reference point for price filtering
    ref_price = target_price if target_price > 0 else median
    price_min = ref_price * 0.7
    price_max = ref_price * 1.30
    similar: list[dict] = []

    for ad in dataset.ads:
        ad_id = int(ad.get("ad_id", 0))
        if ad_id == target_ad_id:
            continue
        ad_price = normalize_price_byn(ad.get("price_byn")) or 0.0
        if ad_price <= 0:
            continue
        if not (price_min <= ad_price <= price_max):
            continue

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
        ad_params = []
        for param in ad.get("ad_parameters", []):
            pk = param.get("p", "")
            if pk in {"condition", "currency", "price", "users_synonyms"}:
                continue
            pl = param.get("pl") or pk
            pv = param.get("vl") or str(param.get("v", ""))
            if pl and pv:
                ad_params.append(f"{pl}: {pv}")

        # Compute relevance score for sorting
        score = 0.0
        # Prefer similar condition
        if target_condition and ad_condition and target_condition == ad_condition:
            score += 10.0
        # Prefer closer to median
        if median:
            score -= abs(ad_price - median) / median * 5
        # Prefer private sellers (usually better deals)
        if not ad.get("company_ad"):
            score += 1.0
        # Prefer listings with photos
        if ad_images:
            score += 2.0
        # Prefer newer listings
        age_days = _parse_list_age_days(ad.get("list_time"))
        if age_days is not None and age_days <= 3:
            score += 1.5
        elif age_days is not None and age_days <= 7:
            score += 0.5

        similar.append(
            {
                "ad_id": ad_id,
                "title": ad.get("subject", "")[:80],
                "price_byn": ad_price,
                "image_url": first_image_url(ad),
                "image_urls": ad_images,
                "link": ad.get("ad_link", f"https://www.kufar.by/item/{ad_id}"),
                "deal_score": score,
                "condition": ad_condition,
                "description": ad_desc[:300],
                "seller_type": "shop" if ad.get("company_ad") else "private",
                "parameters": ", ".join(ad_params[:8]),
                "age_days": age_days,
            }
        )

    # Sort by relevance score (best first), then price
    similar.sort(key=lambda item: (-item["deal_score"], item["price_byn"]))
    return similar[:5]


async def _run_analysis(task_id: str, payload: AIAnalysisRequest, settings, cache) -> None:
    """Background coroutine: does the full analysis and updates the task store."""
    task = _tasks[task_id]
    ai = get_ai_service()

    safe_query = payload.query.replace("\n", " ")[:80]
    logger.info(
        "AI async task %s: starting for ad_id=%d query=%s",
        task_id,
        payload.ad_id,
        safe_query,
    )

    try:
        task["status"] = "processing"
        task["progress"] = 10

        # Search strategies — run all in parallel for speed
        search_attempts = [
            {"strict_search": True, "category": payload.category},
            {"strict_search": False, "category": payload.category},
        ]
        if payload.category is not None:
            search_attempts.append({"strict_search": False, "category": None})

        async def _search_one(attempt: dict):
            ds = await load_query_dataset(
                query=payload.query, currency="BYN", settings=settings,
                client_factory=KufarClient, **attempt,
            )
            target = next(
                (ad for ad in ds.ads if int(ad.get("ad_id", 0)) == payload.ad_id), None,
            )
            return ds, target

        results = await asyncio.gather(
            *[_search_one(a) for a in search_attempts], return_exceptions=True,
        )

        task["progress"] = 30

        dataset = None
        target_ad = None
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.warning("Search strategy %d failed: %s", i, result)
                continue
            ds, target = result
            if target:
                dataset = ds
                target_ad = target
                break

        if dataset is None:
            for result in results:
                if isinstance(result, Exception):
                    continue
                ds, _ = result
                dataset = ds
                break

        if not target_ad:
            task["status"] = "error"
            task["error"] = "Объявление не найдено"
            return

        title = target_ad.get("subject", "") or target_ad.get("title", "")
        description = target_ad.get("body", "") or target_ad.get("description", "")
        price_byn = normalize_price_byn(target_ad.get("price_byn")) or 0.0
        is_negotiable_price = price_byn <= 0

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

        is_company = target_ad.get("company_ad", False)
        seller_type = "shop" if is_company else "private"
        all_images = target_ad.get("images") or []
        photo_count = len(all_images)
        listing_age_days = _parse_list_age_days(target_ad.get("list_time"))

        images = []
        for img in all_images[:3]:
            path = img.get("path", "")
            if path:
                images.append(f"https://rms.kufar.by/v1/gallery/{path}")

        stats = dataset.price_stats
        median = stats.median if stats else None
        count = stats.count if stats else 0
        q1 = stats.q1 if stats else None
        q3 = stats.q3 if stats else None
        price_min = stats.min if stats else None
        price_max = stats.max if stats else None

        similar = _collect_similar_listings(
            dataset, payload.ad_id, price_byn, median, target_condition=condition,
        )

        ai_similar_for_comparison = [
            {
                "ad_id": s["ad_id"],
                "title": s["title"],
                "price_byn": s["price_byn"],
                "price_delta_byn": (
                    round(s["price_byn"] - price_byn, 0)
                    if not is_negotiable_price
                    else None
                ),
                "condition": s.get("condition"),
                "description": s.get("description", ""),
                "seller_type": s.get("seller_type"),
                "parameters": s.get("parameters", ""),
                "age_days": s.get("age_days"),
                "deal_score": round(s.get("deal_score", 0), 1),
            }
            for s in similar
        ]

        target_anomaly_labels: list[str] = []
        target_deal_score = 0.0
        target_deal_verdict = ""
        if stats:
            from api.services.market_signals import anomaly_labels as _anomaly_labels
            from api.services.reseller_tools import compute_deal_score as _compute_deal_score

            raw_flags = []
            try:
                from api.services.market_signals import detect_anomaly_flags
                raw_flags = detect_anomaly_flags(target_ad, stats)
            except Exception:
                logger.warning(
                    "Failed to compute anomaly flags for ad_id=%d",
                    payload.ad_id,
                    exc_info=True,
                )
            target_anomaly_labels = _anomaly_labels(raw_flags)
            try:
                deal = _compute_deal_score(target_ad, query=payload.query, market_stats=stats)
                target_deal_score = deal.score
                target_deal_verdict = deal.verdict
            except Exception:
                logger.warning(
                    "Failed to compute deal score for ad_id=%d",
                    payload.ad_id,
                    exc_info=True,
                )

        task["progress"] = 50

        import time as _time
        _t0 = _time.monotonic()
        logger.info(
            "AI async task %s: calling ai.analyze_listing for '%s' (%d imgs, %d similar)",
            task_id, title[:50], len(images), len(ai_similar_for_comparison),
        )
        result = await asyncio.wait_for(
            ai.analyze_listing(
                title=title,
                description=description,
                price_byn=price_byn,
                is_negotiable_price=is_negotiable_price,
                condition=condition,
                parameters=parameters,
                market_median=median,
                market_count=count,
                market_q1=q1,
                market_q3=q3,
                market_min=price_min,
                market_max=price_max,
                seller_type=seller_type,
                photo_count=photo_count,
                listing_age_days=listing_age_days,
                image_urls=images,
                similar_listings=ai_similar_for_comparison,
                anomaly_flags=target_anomaly_labels,
                deal_score=target_deal_score,
                deal_verdict=target_deal_verdict,
            ),
            timeout=300,
        )
        result = apply_ai_market_guardrails(
            result,
            title=title,
            description=description,
            market_median=median,
            market_q1=q1,
            market_q3=q3,
            market_count=count,
            similar_listings=similar,
            is_negotiable_price=is_negotiable_price,
        )
        logger.info("AI async task %s: AI done in %.1fs", task_id, _time.monotonic() - _t0)

        task["progress"] = 85

        # Build response
        best_pick_ad_id = None
        best_pick_reason = ""
        bp = result.get("best_pick") or {}
        if isinstance(bp, dict):
            best_pick_ad_id = bp.get("ad_id")
            best_pick_reason = bp.get("reason", "")

        best_alternative = None
        if best_pick_ad_id:
            best_alternative = next((s for s in similar if s["ad_id"] == best_pick_ad_id), None)
        if not best_alternative and similar:
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
                "ai_note": (
                    best_pick_reason
                    if best_pick_ad_id == best_alternative["ad_id"]
                    else ""
                ),
            }

        resale_data = result.get("resale_potential")
        resale_potential = None
        if resale_data and isinstance(resale_data, dict):
            from api.schemas import AIResalePotential, AIResalePrice
            resale_potential = AIResalePotential(
                fast_price=AIResalePrice(**resale_data["fast_price"])
                if resale_data.get("fast_price")
                and isinstance(resale_data["fast_price"], dict)
                else None,
                market_price=AIResalePrice(**resale_data["market_price"])
                if resale_data.get("market_price")
                and isinstance(resale_data["market_price"], dict)
                else None,
                optimal_price=AIResalePrice(**resale_data["optimal_price"])
                if resale_data.get("optimal_price")
                and isinstance(resale_data["optimal_price"], dict)
                else None,
                reasoning=resale_data.get("reasoning", ""),
            )

        response = AIAnalysisResponse(
            ad_id=payload.ad_id,
            condition=result.get("condition"),
            fair_price=result.get("fair_price"),
            resale_potential=resale_potential,
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

        # Cache result (use cache passed from endpoint)
        cache_key = f"ai_analysis:v2:{payload.ad_id}:{payload.query}:cat={payload.category}"
        await cache.set_json(cache_key, response.model_dump(), ttl=3600)

        task["status"] = "done"
        task["progress"] = 100
        task["result"] = response.model_dump()

    except TimeoutError:
        logger.error("AI async task %s timed out for ad_id=%d", task_id, payload.ad_id)
        task["status"] = "error"
        task["error"] = "AI анализ занял слишком долго. Попробуйте ещё раз."
    except Exception as exc:
        logger.error("AI async task %s failed: [%s] %s", task_id, type(exc).__name__, exc)
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
            err_msg = "Не удалось подключиться к AI-сервису. Возможно, требуется VPN на сервере."
        elif "insufficient balance" in err_str:
            err_msg = "Баланс AI-сервиса исчерпан."
        if getattr(settings, "debug", False):
            err_msg += f" [{type(exc).__name__}: {exc}]"
        task["status"] = "error"
        task["error"] = err_msg


@router.post("/analyze")
async def analyze_listing(
    payload: AIAnalysisRequest,
    request: Request,
    _user=Depends(get_telegram_user),
):
    """Start async AI analysis. Returns task_id immediately for polling."""
    _check_ai_available()

    # Check cache BEFORE rate limit — cached results return immediately
    cache = get_cache(request)
    cache_key = f"ai_analysis:v2:{payload.ad_id}:{payload.query}:cat={payload.category}"
    cached = await cache.get_json(cache_key)
    if cached:
        return {"task_id": None, "cached": True, "result": cached}

    await _check_rate_limit(request, _user.user_id)

    settings = getattr(request.app.state, "settings", None)
    if settings is None:
        from api.config import get_settings
        settings = get_settings()

    # Create background task
    task_id = secrets.token_urlsafe(16)
    _prune_old_tasks()
    _tasks[task_id] = {
        "status": "pending",
        "progress": 0,
        "result": None,
        "error": None,
        "_created_ts": datetime.now(UTC).timestamp(),
    }

    asyncio.create_task(_run_analysis(task_id, payload, settings, cache))

    return {"task_id": task_id}


@router.post("/export-report")
async def create_export_report(
    payload: AIExportReportRequest,
    request: Request,
    _user=Depends(get_telegram_user),
):
    """Create a short-lived HTML export with a real URL for Telegram/browser printing."""
    html = (payload.html or "").strip()
    if not html:
        raise HTTPException(status_code=400, detail="Пустой HTML отчёта")
    if len(html) > 300_000:
        raise HTTPException(status_code=400, detail="HTML отчёта слишком большой")

    _prune_old_exports()
    token = secrets.token_urlsafe(18)
    _exports[token] = {
        "html": html,
        "_created_ts": datetime.now(UTC).timestamp(),
    }
    return {"url": str(request.url_for("get_export_report", token=token))}


@router.get(
    "/export-report/{token}",
    name="get_export_report",
    response_class=HTMLResponse,
)
async def get_export_report(token: str):
    """Return previously created short-lived HTML export."""
    _prune_old_exports()
    item = _exports.get(token)
    if not item:
        raise HTTPException(status_code=404, detail="Экспорт не найден или истёк")
    return HTMLResponse(
        item["html"],
        headers={
            "Cache-Control": "no-store, max-age=0",
            "Content-Security-Policy": (
                "default-src 'none'; "
                "img-src https: data:; "
                "style-src 'unsafe-inline'; "
                "script-src 'unsafe-inline'; "
                "base-uri 'none'; "
                "form-action 'none'; "
                "frame-ancestors 'none'; "
                "connect-src 'none'"
            ),
        },
    )


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
        category=payload.category,
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
