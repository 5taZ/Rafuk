"""AI Analysis router — listing analysis and listing assistant."""

from __future__ import annotations

import asyncio
import logging
import re as _re
import secrets
import time as _time
from datetime import UTC, datetime
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ValidationError

from api.config import get_settings
from api.dependencies import get_cache, get_kufar_client, get_telegram_user
from api.schemas import (
    AIAnalysisRequest,
    AIAnalysisResponse,
    AIResalePotential,
    AIResalePrice,
)
from api.services.aggregator import (
    cluster_price_stats,
    compute_category_price_stats,
    compute_price_stats,
    extract_prices,
    filter_ads_for_accessory_category,
    normalize_price_byn,
    resolve_price_reference,
)
from api.services.ai_guardrails import apply_ai_market_guardrails
from api.services.ai_marketplace import (
    BestAlternativeDecision,
    MarketplaceRiskContext,
    build_fallback_analysis_result,
    build_market_context_fallback,
    build_marketplace_risk_context,
    choose_best_alternative,
    collect_similar_listings_from_cohorts,
    complete_analysis_sections,
    finalize_red_flags,
)
from api.services.ai_service import (
    dedupe_analysis_payload,
    detect_category,
    get_ai_service,
    normalize_condition_label,
)
from api.services.cache import CacheBackend
from api.services.kufar_client import KufarAPIError, KufarClient
from api.services.market_signals import anomaly_labels, detect_anomaly_flags
from api.services.query_pipeline import load_query_dataset
from api.services.reseller_tools import compute_deal_score

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ai", tags=["ai"])

_AI_ANALYSIS_ERRORS = (
    httpx.HTTPError,
    KufarAPIError,
    RuntimeError,
    ValidationError,
    ValueError,
    TypeError,
    KeyError,
)

# ── Async task store ──────────────────────────────────────────────────────
# Redis-backed when available, with in-process shadow fallback.
# NOTE: These dicts are safe under single-process asyncio (no preemption
# between await points). If deploying with multiple worker processes,
# the Redis path should be used exclusively via _TASKS_KEY/_EXPORTS_KEY.
_tasks: dict[str, dict] = {}
_exports: dict[str, dict] = {}

# Strong references to background asyncio tasks — without this the GC may
# drop a task before it finishes (documented behaviour in Python 3.12+ for
# fire-and-forget asyncio.create_task patterns). Tasks self-clean from
# this set in their done callback.
_bg_tasks: set[asyncio.Task[Any]] = set()


def _spawn_bg_task(coro: Any, *, name: str | None = None) -> asyncio.Task[Any]:
    """Spawn a background task that survives GC until completion."""
    task = asyncio.create_task(coro, name=name)
    _bg_tasks.add(task)
    task.add_done_callback(_bg_tasks.discard)
    return task


def _task_ttl() -> int:
    return int(getattr(get_settings(), "ai_task_ttl", 3600) or 3600)


def _export_ttl() -> int:
    return int(getattr(get_settings(), "ai_export_ttl", 900) or 900)


_MAX_SHADOW_ENTRIES = 200


def _prune_old_tasks_shadow() -> None:
    """Remove local shadow tasks older than TTL. Evicts oldest when over capacity."""
    now = datetime.now(UTC).timestamp()
    ttl = _task_ttl()
    expired = [k for k, v in _tasks.items() if now - v.get("_updated_ts", 0) > ttl]
    for k in expired:
        _tasks.pop(k, None)
    # Enforce upper bound — evict oldest entries first.
    # Snapshot keys to avoid RuntimeError from concurrent mutation.
    while len(_tasks) > _MAX_SHADOW_ENTRIES:
        oldest_key = min(
            list(_tasks.keys()),
            key=lambda k: _tasks[k].get("_updated_ts", 0),
            default=None,
        )
        if oldest_key is None:
            break
        _tasks.pop(oldest_key, None)


def clear_user_ai_data(telegram_user_id: int) -> None:
    """Clear in-memory AI task/export shadow entries for a user.

    Called from account deletion. Best-effort: shadow dicts don't
    always carry user_id, so we also clear all expired entries.
    """
    _prune_old_tasks_shadow()
    _prune_old_exports()
    # Remove any shadow entries that reference this user
    for task_id, task in list(_tasks.items()):
        if task.get("_telegram_user_id") == telegram_user_id:
            _tasks.pop(task_id, None)
    for export_id, export in list(_exports.items()):
        if export.get("_telegram_user_id") == telegram_user_id:
            _exports.pop(export_id, None)


async def periodic_prune_shadow_stores() -> None:
    """Background task: periodically prune in-memory shadow stores.

    Called from the API lifespan so that _tasks and _exports dicts
    don't grow unbounded between task creation events.
    """
    try:
        while True:
            await asyncio.sleep(300)  # every 5 minutes
            _prune_old_tasks_shadow()
            _prune_old_exports()
    except asyncio.CancelledError:
        pass  # Graceful shutdown


def _task_cache_key(task_id: str, *, user_id: int | None = None) -> str:
    # Namespace by user_id to prevent cross-user data access via shared Redis
    uid_part = f":u{user_id}" if user_id is not None else ""
    return f"ai_task{uid_part}:{task_id}"


def _task_version(task: dict[str, Any] | None) -> float:
    if not task:
        return 0.0
    try:
        return float(task.get("_updated_ts") or task.get("_created_ts") or 0.0)
    except (TypeError, ValueError):
        return 0.0


async def _get_task(
    cache: CacheBackend, task_id: str, *, user_id: int | None = None,
) -> dict[str, Any] | None:
    # Periodically prune shadow dict on reads too (not just on new task creation)
    if len(_tasks) > _MAX_SHADOW_ENTRIES:
        _prune_old_tasks_shadow()
    # Prefer user_id from shadow entry for namespaced cache lookup
    shadow_task = _tasks.get(task_id)
    # Ownership check: if user_id is provided, verify shadow entry belongs to this user
    if shadow_task and user_id is not None:
        shadow_uid = shadow_task.get("_telegram_user_id")
        if shadow_uid is not None and shadow_uid != user_id:
            shadow_task = None  # Deny access to another user's task
    effective_uid = user_id or (shadow_task.get("_telegram_user_id") if shadow_task else None)
    cached_task = await cache.get_json(_task_cache_key(task_id, user_id=effective_uid))

    if _task_version(shadow_task) > _task_version(cached_task):
        return shadow_task
    if cached_task:
        _tasks[task_id] = cached_task
        return cached_task
    return shadow_task


async def _set_task(cache: CacheBackend, task_id: str, task: dict[str, Any]) -> dict[str, Any]:
    if "_updated_ts" not in task:
        task["_updated_ts"] = datetime.now(UTC).timestamp()
    _tasks[task_id] = task
    user_id = task.get("_telegram_user_id")
    await cache.set_json(_task_cache_key(task_id, user_id=user_id), task, ttl=_task_ttl())
    return task


async def _update_task(
    cache: CacheBackend, task_id: str, *, user_id: int | None = None, **updates: Any,
) -> dict[str, Any]:
    task = await _get_task(cache, task_id, user_id=user_id) or {
        "status": "pending",
        "progress": 0,
        "result": None,
        "error": None,
        "_created_ts": datetime.now(UTC).timestamp(),
        "_updated_ts": datetime.now(UTC).timestamp(),
    }
    task.update(updates)
    task["_updated_ts"] = datetime.now(UTC).timestamp()
    return await _set_task(cache, task_id, task)


def _prune_old_exports() -> None:
    """Remove expired HTML exports. Evicts oldest when over capacity."""
    if not _exports:
        return
    now = datetime.now(UTC).timestamp()
    ttl = _export_ttl()
    expired = [token for token, item in _exports.items() if now - item.get("_created_ts", 0) > ttl]
    for token in expired:
        _exports.pop(token, None)
    # Enforce upper bound — evict oldest entries first
    while len(_exports) > _MAX_SHADOW_ENTRIES:
        oldest_key = min(_exports, key=lambda k: _exports[k].get("_created_ts", 0))
        _exports.pop(oldest_key, None)


@router.get("/task/{task_id}")
async def get_task_status(
    task_id: str,
    request: Request,
    _user=Depends(get_telegram_user),
):
    """Poll AI analysis task status."""
    task = await _get_task(get_cache(request), task_id, user_id=_user.user_id)
    if not task:
        raise HTTPException(status_code=404, detail="Задача не найдена")
    if task.get("_telegram_user_id") != _user.user_id:
        raise HTTPException(status_code=404, detail="Задача не найдена")
    resp: dict = {
        "status": task["status"],
        "progress": task.get("progress", 0),
        "stage": task.get("stage", ""),
    }
    if task["status"] == "done" and task.get("result"):
        resp["result"] = task["result"]
    elif task["status"] == "error" and task.get("error"):
        resp["error"] = task["error"]
    return resp


DISCLAIMER = (
    "AI-анализ носит исключительно информационно-справочный характер и не является "
    "финансовой, инвестиционной или юридической консультацией; гарантией прибыли, "
    "рыночной стоимости или ликвидности товара; рекомендацией к совершению или отказу "
    "от сделки; профессиональной оценкой товара. Все решения пользователь принимает "
    "самостоятельно на свой страх и риск. Рыночные данные основаны на открытых "
    "объявлениях kufar.by и могут не отражать реальные цены сделок."
)


class AIExportReportRequest(BaseModel):
    html: str


def _check_ai_available():
    service = get_ai_service()
    if not service.available:
        raise HTTPException(status_code=503, detail="AI analysis is not configured")
    return service


async def _check_rate_limit(
    request: Request,
    user_id: int,
    *,
    endpoint: str = "default",
) -> None:
    """Per-user rate limit: hourly per-endpoint + shared daily cap."""
    cache = get_cache(request)
    settings = getattr(request.app.state, "settings", None)
    hourly_limit = int(getattr(settings, "ai_hourly_limit", 10) or 10)
    daily_limit = int(getattr(settings, "ai_daily_limit", 50) or 50)

    hourly_key = f"ai_rate:{user_id}:{endpoint}"
    count = await cache.incr(hourly_key, ttl=3600)

    if count > hourly_limit:
        raise HTTPException(
            status_code=429,
            detail=f"Превышен лимит AI-анализов ({hourly_limit} в час)",
        )

    daily_key = f"ai_daily:{user_id}"
    daily_count = await cache.incr(daily_key, ttl=86400)
    if daily_count > daily_limit:
        raise HTTPException(
            status_code=429,
            detail=f"Превышен дневной лимит AI-анализов ({daily_limit})",
        )


async def _check_ai_consent(request: Request, user_id: int) -> None:
    """Verify the user has granted AI analysis and cross-border consent.

    Skipped in debug mode — same as Telegram auth bypass.
    """
    from api.config import get_settings

    if get_settings().debug:
        return

    from sqlalchemy import select

    from api.models import UserConsent

    session_factory = request.app.state.session_factory
    async with session_factory() as session:
        from api.services.workflow_store import resolve_user_id

        uid = await resolve_user_id(session, user_id)
        if uid is None:
            raise HTTPException(
                status_code=403,
                detail={
                    "error": "consent_required",
                    "consent_type": "ai_analysis",
                    "message": (
                        "Для использования AI-анализа необходимо"
                        " согласие на обработку данных. "
                        "Нажмите «Согласен» в модале согласия."
                    ),
                },
            )

        consents = (
            await session.execute(
                select(UserConsent.consent_type).where(
                    UserConsent.user_id == uid,
                    UserConsent.consent_type.in_(["ai_analysis", "cross_border"]),
                    UserConsent.revoked_at.is_(None),
                )
            )
        ).scalars().all()

        for consent_type in ("ai_analysis", "cross_border"):
            if consent_type not in consents:
                raise HTTPException(
                    status_code=403,
                    detail={
                        "error": "consent_required",
                        "consent_type": consent_type,
                        "message": (
                            "Для использования AI-анализа необходимо"
                            " согласие на обработку данных. "
                            "Нажмите «Согласен» в модале согласия."
                        ),
                    },
                )


async def _log_ai_audit(
    session_factory: Any,
    *,
    telegram_user_id: int,
    endpoint: str,
    ad_id: str | None = None,
    query: str | None = None,
    result_summary: str | None = None,
    model: str = "",
    latency_ms: int | None = None,
) -> None:
    """Write an AI audit log entry (Belarus Law No. 91-Z requirement).

    Best-effort: errors are logged but never propagated.
    """
    try:
        from api.models import AIAuditLog
        from api.services.workflow_store import resolve_user_id

        async with session_factory() as session:
            uid = await resolve_user_id(session, telegram_user_id)
            if uid is None:
                return
            entry = AIAuditLog(
                user_id=uid,
                endpoint=endpoint,
                ad_id=ad_id,
                query=(query or "")[:256] if query else None,
                result_summary=(result_summary or "")[:512] if result_summary else None,
                model=model,
                latency_ms=latency_ms,
            )
            session.add(entry)
            await session.commit()
    except Exception:
        logger.warning("Failed to write AI audit log", exc_info=True)


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


def _build_resale_potential(resale_data: Any) -> AIResalePotential | None:
    """Build the AIResalePotential pydantic model from a raw dict.

    Returns None when the input doesn't look like a valid resale block.
    Used in both the success and TimeoutError fallback paths to avoid
    duplicating ~20 lines of nested conditionals.
    """
    if not isinstance(resale_data, dict):
        return None
    try:
        return AIResalePotential(
            fast_price=AIResalePrice(**resale_data["fast_price"])
            if isinstance(resale_data.get("fast_price"), dict)
            else None,
            market_price=AIResalePrice(**resale_data["market_price"])
            if isinstance(resale_data.get("market_price"), dict)
            else None,
            optimal_price=AIResalePrice(**resale_data["optimal_price"])
            if isinstance(resale_data.get("optimal_price"), dict)
            else None,
            reasoning=resale_data.get("reasoning", ""),
        )
    except (KeyError, TypeError, ValueError):
        return None


def _build_fallback_response(
    *,
    payload: AIAnalysisRequest,
    price_byn: float,
    is_negotiable_price: bool,
    median: float | None,
    q1: float | None,
    q3: float | None,
    similar: list[dict[str, Any]],
    risk_context: MarketplaceRiskContext,
    reference: Any,
    photo_condition_label: str | None,
    photo_condition_notes: list[str],
    title: str,
    parameters: list[dict[str, Any]],
) -> AIAnalysisResponse:
    """Build a fallback AIAnalysisResponse when the AI call fails.

    Uses rule-based analysis instead of AI, so the user always gets
    a useful result rather than a raw error message.
    """
    fallback_best_decision = choose_best_alternative(
        similar,
        target_price=price_byn,
        is_negotiable_price=is_negotiable_price,
        ai_best_pick_ad_id=None,
    )
    fallback_best_alt = fallback_best_decision.item
    if fallback_best_alt:
        fallback_best_alt = {
            "ad_id": fallback_best_alt["ad_id"],
            "title": fallback_best_alt["title"],
            "price_byn": fallback_best_alt["price_byn"],
            "image_url": fallback_best_alt.get("image_url"),
            "link": fallback_best_alt.get("link", ""),
            "deal_score": fallback_best_alt.get("deal_score", 0.0),
            "condition": fallback_best_alt.get("condition"),
            "ai_note": fallback_best_decision.reason,
        }
    fallback_red_flags = finalize_red_flags([], risk_context)
    fallback_result = build_fallback_analysis_result(
        title=title or "",
        parameters=parameters,
        price_byn=price_byn,
        is_negotiable_price=is_negotiable_price,
        market_median=median,
        market_q1=q1,
        market_q3=q3,
        best_alternative=fallback_best_alt,
        similar_listings=similar,
        risk_context=risk_context,
        photo_condition_label=photo_condition_label or None,
        photo_condition_notes=photo_condition_notes or [],
        red_flags=fallback_red_flags,
    )
    fallback_result = dedupe_analysis_payload(fallback_result)
    fallback_market_ctx = build_market_context_fallback(
        price_byn=price_byn,
        is_negotiable_price=is_negotiable_price,
        market_median=median,
        similar_listings=similar,
        risk_context=risk_context,
        ai_market_context="",
        price_reference_scope=reference.scope,
        price_reference_label=reference.label,
    )
    response = AIAnalysisResponse(
        ad_id=payload.ad_id,
        condition=fallback_result.get("condition"),
        fair_price=fallback_result.get("fair_price"),
        resale_potential=None,
        watch_out=fallback_result.get("watch_out", []),
        recommendation=fallback_result.get("recommendation"),
        similar_listings=similar,
        best_alternative=fallback_best_alt,
        meeting_checklist=fallback_result.get("meeting_checklist", []),
        negotiation_tips=fallback_result.get("negotiation_tips", []),
        red_flags=fallback_red_flags,
        market_context=fallback_market_ctx,
        price_reference_scope=reference.scope,
        price_reference_label=reference.label,
        best_pick_reason=fallback_best_decision.reason,
        summary=fallback_result.get("summary", ""),
        disclaimer=DISCLAIMER,
    )
    response.resale_potential = _build_resale_potential(
        fallback_result.get("resale_potential")
    )
    return response


async def _run_analysis(
    task_id: str,
    payload: AIAnalysisRequest,
    settings,
    cache,
    kufar_client: KufarClient,
    *,
    user_id: int | None = None,
) -> None:
    """Background coroutine: does the full analysis and updates the task store."""
    ai = get_ai_service()

    # Read configurable timeouts from settings
    analysis_timeout = getattr(settings, "ai_analysis_timeout", 150)
    photo_precheck_timeout = getattr(settings, "ai_photo_precheck_timeout", 30)
    fallback_cache_ttl = getattr(settings, "ai_fallback_cache_ttl", 1800)

    safe_query = payload.query.replace("\n", " ")[:80]
    logger.info(
        "AI async task %s: starting for ad_id=%d query=%s",
        task_id,
        payload.ad_id,
        safe_query,
    )

    # Pre-initialize variables used by fallback handlers so that an
    # early exception (e.g. during search) does not cause NameError
    # in the except blocks below.
    price_byn = 0.0
    is_negotiable_price = True
    median = None
    q1 = None
    q3 = None
    similar = []
    risk_context = None
    reference = None
    photo_condition_label = None
    photo_condition_notes = None
    title = ""
    parameters = []

    try:
        await _update_task(
            cache,
            task_id,
            user_id=user_id,
            status="processing",
            progress=10,
            stage="loading_market_data",
            error=None,
        )
        logger.info("AI task %s stage=loading_market_data ad_id=%d", task_id, payload.ad_id)

        # Search strategies — run all in parallel for speed
        search_attempts = [
            {"strict_search": True, "category": payload.category},
            {"strict_search": False, "category": payload.category},
        ]
        if payload.category is not None:
            search_attempts.append({"strict_search": False, "category": None})

        async def _search_one(attempt: dict):
            ds = await load_query_dataset(
                query=payload.query,
                currency="BYN",
                settings=settings,
                client=kufar_client,
                **attempt,
            )
            target = next(
                (ad for ad in ds.ads if int(ad.get("ad_id", 0)) == payload.ad_id),
                None,
            )
            return ds, target

        results = await asyncio.gather(
            *[_search_one(a) for a in search_attempts],
            return_exceptions=True,
        )

        await _update_task(cache, task_id, user_id=user_id, progress=30, stage="search_ready")
        logger.info("AI task %s stage=search_ready", task_id)

        datasets_by_cohort: list[tuple[str, Any]] = []
        cohort_keys = [
            "strict_category",
            "broad_category",
            "broad_query",
        ]
        dataset = None
        target_ad = None
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.warning("Search strategy %d failed: %s", i, result)
                continue
            ds, target = result
            if i < len(cohort_keys):
                datasets_by_cohort.append((cohort_keys[i], ds))
            if target and target_ad is None:
                dataset = ds
                target_ad = target

        if dataset is None:
            for result in results:
                if isinstance(result, Exception):
                    continue
                ds, _ = result
                dataset = ds
                break

        if not target_ad:
            await _update_task(
                cache,
                task_id,
                user_id=user_id,
                status="error",
                stage="target_missing",
                error="Объявление не найдено",
            )
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
        risk_context = build_marketplace_risk_context(target_ad)
        all_images = target_ad.get("images") or []
        photo_count = len(all_images)
        listing_age_days = _parse_list_age_days(target_ad.get("list_time"))

        images = []
        for img in all_images[:3]:
            path = img.get("path", "")
            if path:
                images.append(f"https://rms.kufar.by/v1/gallery/{path}")

        stats = dataset.price_stats
        # Detect the product category so we can filter out irrelevant
        # listings (e.g. phones mixed into a "чехол" search).
        detected_cat = detect_category(title, parameters)
        # When the detected category is an accessory type, filter ads
        # by price cap so that phone-level prices don't skew the median.
        # This prevents a 20 BYN case being compared against 2000 BYN phones.
        filtered_ads = filter_ads_for_accessory_category(dataset.ads, detected_cat)
        if filtered_ads is not dataset.ads:
            # Recompute stats on the filtered subset
            filtered_prices = extract_prices(filtered_ads)
            if filtered_prices:
                stats = compute_price_stats(filtered_prices)
        # Compute category-aware price stats so that accessories (e.g. phone
        # cases) are compared against other accessories, not against phones.
        category_price_stats = compute_category_price_stats(filtered_ads)
        reference = resolve_price_reference(
            target_ad, stats, category_price_stats
        )
        effective_stats = reference.stats
        # Cluster-aware median: compare against listings that share
        # the same variant tokens (generation, body type, etc.) as
        # the target ad.  Falls back to the broad median when the
        # cluster is too small (< 3 similar listings).
        cluster_stats = cluster_price_stats(
            str(target_ad.get("subject", "")), filtered_ads,
        )
        if cluster_stats is not None:
            effective_stats = cluster_stats
        median = effective_stats.median if effective_stats else None
        count = effective_stats.count if effective_stats else 0
        q1 = effective_stats.q1 if effective_stats else None
        q3 = effective_stats.q3 if effective_stats else None
        price_min = effective_stats.min if effective_stats else None
        price_max = effective_stats.max if effective_stats else None

        similar = collect_similar_listings_from_cohorts(
            cohorts=datasets_by_cohort or [("broad_category", dataset)],
            query=payload.query,
            target_ad=target_ad,
            target_ad_id=payload.ad_id,
            target_price=price_byn,
            market_median=median,
        )

        ai_similar_for_comparison = [
            {
                "ad_id": s["ad_id"],
                "title": s["title"],
                "price_byn": s["price_byn"],
                "price_delta_byn": (
                    round(s["price_byn"] - price_byn, 0) if not is_negotiable_price else None
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
        photo_condition_label = ""
        photo_condition_notes: list[str] = []
        if effective_stats:
            raw_flags: list[Any] = []
            try:
                raw_flags = detect_anomaly_flags(target_ad, effective_stats)
            except (AttributeError, KeyError, TypeError, ValueError):
                logger.warning(
                    "Failed to compute anomaly flags for ad_id=%d",
                    payload.ad_id,
                    exc_info=True,
                )
            target_anomaly_labels = anomaly_labels(raw_flags)
            try:
                deal = compute_deal_score(
                    target_ad, query=payload.query, market_stats=effective_stats,
                )
                target_deal_score = deal.score
                target_deal_verdict = deal.verdict
            except (AttributeError, KeyError, TypeError, ValueError):
                logger.warning(
                    "Failed to compute deal score for ad_id=%d",
                    payload.ad_id,
                    exc_info=True,
                )

        if images:
            await _update_task(
                cache, task_id, user_id=user_id,
                progress=40, stage="photo_precheck",
            )
            logger.info("AI task %s stage=photo_precheck images=%d", task_id, len(images))
            try:
                quick_photo = await asyncio.wait_for(
                    ai.quick_condition(images[:3]), timeout=photo_precheck_timeout
                )
                photo_condition_label = str(quick_photo.get("condition") or "").strip()
                photo_condition_notes = [
                    str(note).strip()
                    for note in (quick_photo.get("notes") or [])
                    if str(note).strip()
                ][:4]
                logger.info(
                    "AI task %s photo_precheck ok label=%r notes=%d",
                    task_id,
                    photo_condition_label,
                    len(photo_condition_notes),
                )
            except TimeoutError:
                logger.warning(
                    "AI task %s photo_precheck timed out (30s) — will use text-only condition",
                    task_id,
                )
            except (httpx.HTTPError, RuntimeError, ValueError) as exc:
                logger.warning(
                    "AI task %s photo_precheck failed [%s]: %s — will use text-only condition",
                    task_id,
                    type(exc).__name__,
                    exc,
                )

        await _update_task(cache, task_id, user_id=user_id, progress=50, stage="calling_ai")
        logger.info(
            "AI task %s stage=calling_ai title=%r images=%d similar=%d",
            task_id,
            title[:60],
            len(images),
            len(ai_similar_for_comparison),
        )

        _t0 = _time.monotonic()
        logger.info(
            "AI async task %s: calling ai.analyze_listing_parallel for '%s' (%d imgs, %d similar)",
            task_id,
            title[:50],
            len(images),
            len(ai_similar_for_comparison),
        )
        # Wrap the AI call so that httpx transport-level timeouts
        # (ReadTimeout, ConnectTimeout) are converted to TimeoutError.
        # Without this, they fall into the generic _AI_ANALYSIS_ERRORS handler
        # and the user sees "AI сервис недоступен" instead of a fallback result.
        try:
            # Report intermediate progress while parallel sub-calls are running.
            # The parallel approach splits the monolithic call into 2 concurrent
            # sub-calls (Price & Market / Condition & Risks), so wall-clock
            # time ≈ max(A, B) instead of A + B.
            async def _run_parallel_with_progress():
                # Fire a background progress updater while the AI calls run.
                # Smoother steps prevent the bar from stalling at any
                # single milestone — the AI call typically takes 30-90s,
                # so we creep through (55, 60, 65, 70, 74, 78) over ~42s
                # with a final hold at 80 to avoid passing 85 prematurely.
                async def _progress_pump():
                    steps: list[tuple[int, float]] = [
                        (55, 6.0),
                        (60, 6.0),
                        (65, 7.0),
                        (70, 7.0),
                        (74, 8.0),
                        (78, 8.0),
                        (80, 12.0),
                    ]
                    for pct, delay in steps:
                        await asyncio.sleep(delay)
                        await _update_task(
                            cache, task_id, user_id=user_id,
                            progress=pct, stage="calling_ai",
                        )

                pump_task = asyncio.create_task(_progress_pump())
                try:
                    return await ai.analyze_listing_parallel(
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
                        similar_listings=ai_similar_for_comparison,
                        risk_context_summary=risk_context.summary,
                        risk_context_flags=risk_context.flags,
                        anomaly_flags=target_anomaly_labels,
                        deal_score=target_deal_score,
                        deal_verdict=target_deal_verdict,
                        photo_condition_label=photo_condition_label or None,
                        photo_condition_notes=photo_condition_notes or None,
                        image_urls=images,
                    )
                finally:
                    pump_task.cancel()

            result = await asyncio.wait_for(
                _run_parallel_with_progress(), timeout=analysis_timeout
            )
        except (httpx.ReadTimeout, httpx.ConnectTimeout) as exc:
            logger.warning(
                "AI task %s: httpx %s — converting to TimeoutError",
                task_id,
                type(exc).__name__,
            )
            raise TimeoutError(str(exc)) from exc
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

        await _update_task(cache, task_id, user_id=user_id, progress=85, stage="building_response")
        logger.info("AI task %s stage=building_response", task_id)

        # Build response
        best_pick_ad_id = None
        best_pick_reason = ""
        bp = result.get("best_pick") or {}
        if isinstance(bp, dict):
            best_pick_ad_id = bp.get("ad_id")
            best_pick_reason = bp.get("reason", "")

        best_decision: BestAlternativeDecision = choose_best_alternative(
            similar,
            target_price=price_byn,
            is_negotiable_price=is_negotiable_price,
            ai_best_pick_ad_id=best_pick_ad_id,
        )
        best_alternative = best_decision.item
        if not best_pick_reason and best_decision.reason:
            best_pick_reason = best_decision.reason

        if best_alternative:
            best_alternative = {
                "ad_id": best_alternative["ad_id"],
                "title": best_alternative["title"],
                "price_byn": best_alternative["price_byn"],
                "image_url": best_alternative.get("image_url"),
                "link": best_alternative.get("link", ""),
                "deal_score": best_alternative.get("deal_score", 0.0),
                "condition": best_alternative.get("condition"),
                "ai_note": best_pick_reason,
            }

        final_red_flags = finalize_red_flags(result.get("red_flags", []), risk_context)
        final_market_context = build_market_context_fallback(
            price_byn=price_byn,
            is_negotiable_price=is_negotiable_price,
            market_median=median,
            similar_listings=similar,
            risk_context=risk_context,
            ai_market_context=result.get("market_context", ""),
            price_reference_scope=reference.scope,
            price_reference_label=reference.label,
        )
        result = complete_analysis_sections(
            result=result,
            title=title,
            parameters=parameters,
            price_byn=price_byn,
            market_median=median,
            best_alternative=best_alternative,
            risk_context=risk_context,
            photo_condition_label=photo_condition_label,
            photo_condition_notes=photo_condition_notes,
            is_negotiable_price=is_negotiable_price,
            red_flags=final_red_flags,
            listing_condition=condition,
            market_q1=q1,
            market_q3=q3,
        )
        # complete_analysis_sections re-merges photo_condition_notes into
        # condition.notes (and into watch_out via _build_fallback_watch_out)
        # using only exact-match dedup. The AI often paraphrases the same
        # observation ("ЛКП имеет блеск" + "ЛКП имеет блеск, без вмятин"),
        # so we re-run the paraphrase-aware dedupe here as a final pass.
        result = dedupe_analysis_payload(result)

        resale_potential = _build_resale_potential(result.get("resale_potential"))

        response = AIAnalysisResponse(
            ad_id=payload.ad_id,
            condition=(
                result.get("condition")
                or (
                    {
                        "label": normalize_condition_label(photo_condition_label),
                        "confidence": 0.72 if photo_condition_label else 0.0,
                        "notes": photo_condition_notes,
                    }
                    if photo_condition_label or photo_condition_notes
                    else None
                )
            ),
            fair_price=result.get("fair_price"),
            resale_potential=resale_potential,
            watch_out=result.get("watch_out", []),
            recommendation=result.get("recommendation"),
            similar_listings=similar,
            best_alternative=best_alternative,
            meeting_checklist=result.get("meeting_checklist", []),
            negotiation_tips=result.get("negotiation_tips", []),
            red_flags=final_red_flags,
            market_context=final_market_context,
            price_reference_scope=reference.scope,
            price_reference_label=reference.label,
            best_pick_reason=best_pick_reason,
            summary=result.get("summary", ""),
            disclaimer=DISCLAIMER,
        )

        # Brief intermediate progress so the bar doesn't stall at 85→100
        await _update_task(cache, task_id, user_id=user_id, progress=95, stage="building_response")

        # Cache result (use cache passed from endpoint)
        cache_key = f"ai_analysis:v5:{payload.ad_id}:{payload.query}:cat={payload.category}"
        serialized = response.model_dump(by_alias=True)
        await cache.set_json(cache_key, serialized, ttl=_task_ttl())

        await _update_task(
            cache,
            task_id,
            user_id=user_id,
            status="done",
            progress=100,
            stage="done",
            result=serialized,
            error=None,
        )
        logger.info("AI task %s stage=done", task_id)

    except TimeoutError:
        try:
            response = _build_fallback_response(
                payload=payload,
                price_byn=price_byn,
                is_negotiable_price=is_negotiable_price,
                median=median,
                q1=q1,
                q3=q3,
                similar=similar,
                risk_context=risk_context,
                reference=reference,
                photo_condition_label=photo_condition_label,
                photo_condition_notes=photo_condition_notes,
                title=title,
                parameters=parameters,
            )
            cache_key = f"ai_analysis:v5:{payload.ad_id}:{payload.query}:cat={payload.category}"
            fallback_serialized = response.model_dump(by_alias=True)
            await cache.set_json(cache_key, fallback_serialized, ttl=fallback_cache_ttl)
            await _update_task(
                cache,
                task_id,
                user_id=user_id,
                status="done",
                progress=100,
                stage="done",
                result=fallback_serialized,
                error=None,
            )
            logger.warning(
                "AI task %s: fallback result delivered (timeout) for ad_id=%d",
                task_id,
                payload.ad_id,
            )
        except Exception as fallback_exc:
            logger.error(
                "Fallback build also failed for task %s: %s",
                task_id,
                fallback_exc,
                exc_info=True,
            )
            await _update_task(
                cache,
                task_id,
                user_id=user_id,
                status="error",
                stage="timeout",
                error="AI анализ занял слишком долго. Попробуйте ещё раз.",
            )
    except _AI_ANALYSIS_ERRORS as exc:
        logger.error("AI async task %s failed: [%s] %s", task_id, type(exc).__name__, exc)
        # Instead of showing a raw error, try to deliver a fallback result.
        # The user gets useful analysis even when the AI service is down.
        try:
            response = _build_fallback_response(
                payload=payload,
                price_byn=price_byn,
                is_negotiable_price=is_negotiable_price,
                median=median,
                q1=q1,
                q3=q3,
                similar=similar,
                risk_context=risk_context,
                reference=reference,
                photo_condition_label=photo_condition_label,
                photo_condition_notes=photo_condition_notes,
                title=title,
                parameters=parameters,
            )
            cache_key = f"ai_analysis:v5:{payload.ad_id}:{payload.query}:cat={payload.category}"
            fallback_serialized = response.model_dump(by_alias=True)
            await cache.set_json(cache_key, fallback_serialized, ttl=fallback_cache_ttl)
            # Add a warning to the fallback result so the user knows AI was
            # unavailable — but they still get analysis, not a blank error.
            fallback_serialized["_ai_warning"] = (
                "AI-сервис временно недоступен. Показан упрощённый анализ."
            )
            await _update_task(
                cache,
                task_id,
                user_id=user_id,
                status="done",
                progress=100,
                stage="done",
                result=fallback_serialized,
                error=None,
            )
            logger.warning(
                "AI task %s: fallback result delivered (error: %s) for ad_id=%d",
                task_id,
                type(exc).__name__,
                payload.ad_id,
            )
        except Exception as fallback_exc:
            logger.error(
                "Fallback build also failed for task %s: %s",
                task_id,
                fallback_exc,
                exc_info=True,
            )
            # Only show a raw error if even the fallback build fails
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
                    "Не удалось подключиться к AI-сервису."
                    " Возможно, требуется VPN на сервере."
                )
            elif "insufficient balance" in err_str:
                err_msg = "Баланс AI-сервиса исчерпан."
            if getattr(settings, "debug", False):
                import re as _re

                _safe_exc = _re.sub(r"https?://\S+", "[URL]", str(exc))
                _safe_exc = _re.sub(
                    r"(?:api[_-]?key|token|bearer)\s*[:=]\s*\S+",
                    "[REDACTED]", _safe_exc, flags=_re.IGNORECASE,
                )
                err_msg += f" [{type(exc).__name__}: {_safe_exc}]"
            await _update_task(
                cache, task_id, user_id=user_id,
                status="error", stage="error", error=err_msg,
            )


@router.post("/analyze")
async def analyze_listing(
    payload: AIAnalysisRequest,
    request: Request,
    _user=Depends(get_telegram_user),
    kufar_client: KufarClient = Depends(get_kufar_client),
):
    """Start async AI analysis. Returns task_id immediately for polling."""
    _check_ai_available()

    # Check cache BEFORE rate limit — cached results return immediately
    cache = get_cache(request)
    cache_key = f"ai_analysis:v5:{payload.ad_id}:{payload.query}:cat={payload.category}"
    cached = await cache.get_json(cache_key)
    if cached:
        return {"task_id": None, "cached": True, "result": cached}

    await _check_ai_consent(request, _user.user_id)
    await _check_rate_limit(request, _user.user_id, endpoint="analyze")

    # AI audit trail (Belarus Law No. 91-Z)
    await _log_ai_audit(
        request.app.state.session_factory,
        telegram_user_id=_user.user_id,
        endpoint="analyze",
        ad_id=str(payload.ad_id),
        query=payload.query,
        model=get_settings().ai_model,
    )

    settings = getattr(request.app.state, "settings", None)
    if settings is None:
        settings = get_settings()

    # Create background task
    task_id = secrets.token_urlsafe(16)
    _prune_old_tasks_shadow()
    task = {
        "status": "pending",
        "progress": 0,
        "stage": "queued",
        "result": None,
        "error": None,
        "_telegram_user_id": _user.user_id,
        "_created_ts": datetime.now(UTC).timestamp(),
        "_updated_ts": datetime.now(UTC).timestamp(),
    }
    await _set_task(cache, task_id, task)

    # Use _spawn_bg_task instead of bare asyncio.create_task — without a
    # strong reference Python's GC can drop the task before completion.
    _spawn_bg_task(
        _run_analysis(task_id, payload, settings, cache, kufar_client, user_id=_user.user_id),
        name=f"ai-analysis-{task_id[:8]}",
    )

    return {"task_id": task_id}


def _sanitize_export_html(html: str) -> str:
    import nh3

    return nh3.clean(
        html,
        tags={
            "div", "span", "p", "h1", "h2", "h3", "h4", "h5", "h6",
            "table", "thead", "tbody", "tr", "th", "td",
            "ul", "ol", "li", "strong", "em", "b", "i", "u",
            "br", "hr", "img", "a", "blockquote", "code", "pre",
        },
        attributes={
            "*": {"class"},
            "img": {"src", "alt", "width", "height"},
            "a": {"href", "target"},
            "td": {"colspan", "rowspan"},
            "th": {"colspan", "rowspan"},
        },
        clean_content_tags={"script", "style"},
        # Only allow https: URLs — blocks data: and javascript: schemes
        url_schemes={"https"},
    )


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

    html = _sanitize_export_html(html)

    _prune_old_exports()
    token = secrets.token_urlsafe(18)
    _exports[token] = {
        "html": html,
        "_telegram_user_id": _user.user_id,
        "_created_ts": datetime.now(UTC).timestamp(),
    }
    return {"url": str(request.url_for("get_export_report", token=token))}


@router.get(
    "/export-report/{token}",
    name="get_export_report",
    response_class=HTMLResponse,
)
async def get_export_report(
    token: str,
    _user=Depends(get_telegram_user),
):
    """Return previously created short-lived HTML export."""
    _prune_old_exports()
    item = _exports.get(token)
    if not item:
        raise HTTPException(status_code=404, detail="Экспорт не найден или истёк")
    if item.get("_telegram_user_id") != _user.user_id:
        raise HTTPException(status_code=404, detail="Экспорт не найден или истёк")
    return HTMLResponse(
        item["html"],
        headers={
            "Cache-Control": "no-store, max-age=0",
            "Content-Security-Policy": (
                "default-src 'none'; "
                "img-src https:; "
                "style-src 'unsafe-inline'; "
                "script-src 'none'; "
                "base-uri 'none'; "
                "form-action 'none'; "
                "frame-ancestors 'none'; "
                "connect-src 'none'"
            ),
        },
    )


# ── Shared helpers re-exported for ai_listing_assistant / ai_tools ────────


def _coerce_string_list(raw: Any, *, limit: int, max_len: int) -> list[str]:
    """Deduplicating string-list coercer shared with sub-routers."""
    items: list[str] = []
    if not isinstance(raw, list):
        return items
    seen: set[str] = set()
    for entry in raw:
        text = _re.sub(r"\s+", " ", str(entry or "").strip())
        if not text:
            continue
        text = text[:max_len]
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        items.append(text)
        if len(items) >= limit:
            break
    return items
