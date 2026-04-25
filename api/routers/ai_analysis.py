"""AI Analysis router — listing analysis and quick condition assessment."""

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
    AIQuickConditionRequest,
    AIQuickConditionResponse,
    AIResalePotential,
    AIResalePrice,
)
from api.services.aggregator import normalize_price_byn
from api.services.ai_guardrails import apply_ai_market_guardrails
from api.services.ai_marketplace import (
    BestAlternativeDecision,
    build_fallback_analysis_result,
    build_market_context_fallback,
    build_marketplace_risk_context,
    choose_best_alternative,
    collect_similar_listings_from_cohorts,
    complete_analysis_sections,
    finalize_red_flags,
)
from api.services.ai_service import get_ai_service, normalize_condition_label
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


def _prune_old_tasks_shadow() -> None:
    """Remove local shadow tasks older than TTL."""
    now = datetime.now(UTC).timestamp()
    ttl = _task_ttl()
    expired = [tid for tid, t in _tasks.items()
               if now - t.get("_created_ts", 0) > ttl]
    for tid in expired:
        _tasks.pop(tid, None)


async def periodic_prune_shadow_stores() -> None:
    """Background task: periodically prune in-memory shadow stores.

    Called from the API lifespan so that _tasks and _exports dicts
    don't grow unbounded between task creation events.
    """
    while True:
        await asyncio.sleep(300)  # every 5 minutes
        _prune_old_tasks_shadow()
        _prune_old_exports()


def _task_cache_key(task_id: str) -> str:
    return f"ai_task:{task_id}"


def _task_version(task: dict[str, Any] | None) -> float:
    if not task:
        return 0.0
    try:
        return float(task.get("_updated_ts") or task.get("_created_ts") or 0.0)
    except (TypeError, ValueError):
        return 0.0


async def _get_task(cache: CacheBackend, task_id: str) -> dict[str, Any] | None:
    # Periodically prune shadow dict on reads too (not just on new task creation)
    if len(_tasks) > 200:
        _prune_old_tasks_shadow()
    cached_task = await cache.get_json(_task_cache_key(task_id))
    shadow_task = _tasks.get(task_id)

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
    await cache.set_json(_task_cache_key(task_id), task, ttl=_task_ttl())
    return task


async def _update_task(cache: CacheBackend, task_id: str, **updates: Any) -> dict[str, Any]:
    task = await _get_task(cache, task_id) or {
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
    """Remove expired HTML exports. Only scans when dict has entries."""
    if not _exports:
        return
    now = datetime.now(UTC).timestamp()
    ttl = _export_ttl()
    expired = [
        token for token, item in _exports.items()
        if now - item.get("_created_ts", 0) > ttl
    ]
    for token in expired:
        _exports.pop(token, None)


@router.get("/task/{task_id}")
async def get_task_status(task_id: str, request: Request):
    """Poll AI analysis task status."""
    task = await _get_task(get_cache(request), task_id)
    if not task:
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


async def _run_analysis(task_id: str, payload: AIAnalysisRequest, settings, cache, kufar_client: KufarClient) -> None:
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

    # Defaults so the fallback path can be entered even if AI call aborts
    # before the "full AI result" stage. Reset as the pipeline progresses.
    market_data_ready = False
    title = ""
    description = ""
    price_byn = 0.0
    is_negotiable_price = False
    condition: str | None = None
    parameters: list[dict[str, Any]] = []
    similar: list[dict[str, Any]] = []
    median: float | None = None
    q1: float | None = None
    q3: float | None = None
    risk_context = build_marketplace_risk_context({})
    photo_condition_label = ""
    photo_condition_notes: list[str] = []

    async def _deliver_fallback_result(reason: str) -> bool:
        """Build a deterministic fallback AIAnalysisResponse and mark task done.

        Returns True on success, False if the fallback itself could not be built
        (callers should then fall back to the normal error path).
        """
        if not market_data_ready:
            return False
        try:
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
            fallback_market_ctx = build_market_context_fallback(
                price_byn=price_byn,
                is_negotiable_price=is_negotiable_price,
                market_median=median,
                similar_listings=similar,
                risk_context=risk_context,
                ai_market_context="",
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
                best_pick_reason=fallback_best_decision.reason,
                summary=fallback_result.get("summary", ""),
                disclaimer=DISCLAIMER,
            )
            response.resale_potential = _build_resale_potential(
                fallback_result.get("resale_potential")
            )
            cache_key = f"ai_analysis:v4:{payload.ad_id}:{payload.query}:cat={payload.category}"
            fallback_serialized = response.model_dump(by_alias=True)
            # Short TTL so a recovered provider is re-hit sooner than the
            # full-AI cache TTL.
            await cache.set_json(cache_key, fallback_serialized, ttl=fallback_cache_ttl)
            await _update_task(
                cache,
                task_id,
                status="done",
                progress=100,
                stage="done",
                result=fallback_serialized,
                error=None,
            )
            logger.warning(
                "AI task %s: fallback result delivered (%s) for ad_id=%d",
                task_id,
                reason,
                payload.ad_id,
            )
            return True
        except Exception:
            logger.exception(
                "AI task %s: fallback build failed (%s) for ad_id=%d",
                task_id,
                reason,
                payload.ad_id,
            )
            return False

    try:
        await _update_task(
            cache,
            task_id,
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
                query=payload.query, currency="BYN", settings=settings,
                client=kufar_client, **attempt,
            )
            target = next(
                (ad for ad in ds.ads if int(ad.get("ad_id", 0)) == payload.ad_id), None,
            )
            return ds, target

        results = await asyncio.gather(
            *[_search_one(a) for a in search_attempts], return_exceptions=True,
        )

        await _update_task(cache, task_id, progress=30, stage="search_ready")
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
        median = stats.median if stats else None
        count = stats.count if stats else 0
        q1 = stats.q1 if stats else None
        q3 = stats.q3 if stats else None
        price_min = stats.min if stats else None
        price_max = stats.max if stats else None

        similar = collect_similar_listings_from_cohorts(
            cohorts=datasets_by_cohort or [("broad_category", dataset)],
            query=payload.query,
            target_ad=target_ad,
            target_ad_id=payload.ad_id,
            target_price=price_byn,
            market_median=median,
        )
        # From this point on we have enough market data to synthesise a full
        # deterministic fallback if the AI call fails or times out.
        market_data_ready = True

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
        photo_condition_label = ""
        photo_condition_notes: list[str] = []
        if stats:
            raw_flags: list[Any] = []
            try:
                raw_flags = detect_anomaly_flags(target_ad, stats)
            except (AttributeError, KeyError, TypeError, ValueError):
                logger.warning(
                    "Failed to compute anomaly flags for ad_id=%d",
                    payload.ad_id,
                    exc_info=True,
                )
            target_anomaly_labels = anomaly_labels(raw_flags)
            try:
                deal = compute_deal_score(target_ad, query=payload.query, market_stats=stats)
                target_deal_score = deal.score
                target_deal_verdict = deal.verdict
            except (AttributeError, KeyError, TypeError, ValueError):
                logger.warning(
                    "Failed to compute deal score for ad_id=%d",
                    payload.ad_id,
                    exc_info=True,
                )

        if images:
            await _update_task(cache, task_id, progress=40, stage="photo_precheck")
            logger.info("AI task %s stage=photo_precheck images=%d", task_id, len(images))
            try:
                quick_photo = await asyncio.wait_for(ai.quick_condition(images[:1]), timeout=photo_precheck_timeout)
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

        await _update_task(cache, task_id, progress=50, stage="calling_ai")
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
            task_id, title[:50], len(images), len(ai_similar_for_comparison),
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
                        await _update_task(cache, task_id, progress=pct, stage="calling_ai")

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
                    )
                finally:
                    pump_task.cancel()

            result = await asyncio.wait_for(_run_parallel_with_progress(), timeout=analysis_timeout)
        except (httpx.ReadTimeout, httpx.ConnectTimeout) as exc:
            logger.warning(
                "AI task %s: httpx %s — converting to TimeoutError",
                task_id, type(exc).__name__,
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

        await _update_task(cache, task_id, progress=85, stage="building_response")
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
            best_pick_reason=best_pick_reason,
            summary=result.get("summary", ""),
            disclaimer=DISCLAIMER,
        )

        # Brief intermediate progress so the bar doesn't stall at 85→100
        await _update_task(cache, task_id, progress=95, stage="building_response")

        # Cache result (use cache passed from endpoint)
        cache_key = f"ai_analysis:v4:{payload.ad_id}:{payload.query}:cat={payload.category}"
        serialized = response.model_dump(by_alias=True)
        await cache.set_json(cache_key, serialized, ttl=_task_ttl())

        await _update_task(
            cache,
            task_id,
            status="done",
            progress=100,
            stage="done",
            result=serialized,
            error=None,
        )
        logger.info("AI task %s stage=done", task_id)

    except TimeoutError:
        logger.warning(
            "AI async task %s timed out for ad_id=%d — attempting deterministic fallback",
            task_id,
            payload.ad_id,
        )
        if await _deliver_fallback_result("timeout"):
            return
        await _update_task(
            cache,
            task_id,
            status="error",
            stage="timeout",
            error="AI анализ занял слишком долго. Попробуйте ещё раз.",
        )
    except _AI_ANALYSIS_ERRORS as exc:
        logger.error("AI async task %s failed: [%s] %s", task_id, type(exc).__name__, exc)
        # When the AI provider itself fails (5xx, parse error, empty content,
        # etc.) we still have all the market data needed to synthesise a full
        # deterministic analysis. Prefer giving the user a complete (if
        # conservative) report over a blunt "AI unavailable" error.
        if await _deliver_fallback_result(f"ai_error:{type(exc).__name__}"):
            return
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
        await _update_task(cache, task_id, status="error", stage="error", error=err_msg)


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
    cache_key = f"ai_analysis:v4:{payload.ad_id}:{payload.query}:cat={payload.category}"
    cached = await cache.get_json(cache_key)
    if cached:
        return {"task_id": None, "cached": True, "result": cached}

    await _check_rate_limit(request, _user.user_id)

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
        "_created_ts": datetime.now(UTC).timestamp(),
        "_updated_ts": datetime.now(UTC).timestamp(),
    }
    await _set_task(cache, task_id, task)

    # Use _spawn_bg_task instead of bare asyncio.create_task — without a
    # strong reference Python's GC can drop the task before completion.
    _spawn_bg_task(
        _run_analysis(task_id, payload, settings, cache, kufar_client),
        name=f"ai-analysis-{task_id[:8]}",
    )

    return {"task_id": task_id}


# ── XSS sanitization for export HTML ──────────────────────────────────────
# Defence-in-depth: even with a strict CSP we strip dangerous tags and
# attributes server-side so that the export remains safe when the CSP is
# accidentally relaxed (e.g. opened outside the export route).
_XSS_PAIRED_TAGS = (
    "script", "iframe", "object", "embed", "applet",
    "form", "svg", "frame", "frameset", "title", "style",
)
# Tags that MUST NOT appear in the export at all — including self-closing.
_XSS_VOID_TAGS = (
    "script", "iframe", "object", "embed", "applet",
    "link", "meta", "base", "svg", "frame", "frameset",
)
_XSS_PAIRED_TAG_PATTERNS = [
    _re.compile(
        rf"<\s*{tag}\b[^>]*>.*?<\s*/\s*{tag}\s*>",
        flags=_re.DOTALL | _re.IGNORECASE,
    )
    for tag in _XSS_PAIRED_TAGS
]
_XSS_VOID_TAG_RE = _re.compile(
    r"<\s*(?:" + "|".join(_XSS_VOID_TAGS) + r")\b[^>]*/?\s*>",
    flags=_re.IGNORECASE,
)
_XSS_EVENT_HANDLER_RE = _re.compile(
    r"\son\w+\s*=\s*(?:\"[^\"]*\"|'[^']*'|[^\s>]+)",
    flags=_re.IGNORECASE,
)
# Match dangerous URL schemes inside href/src/action attributes.
# Allow https:, http:, mailto:, tel:, data: (images only), and relative URLs.
_XSS_DANGEROUS_URL_ATTR_RE = _re.compile(
    r"(\s(?:href|src|action|formaction|xlink:href|background|cite|poster|srcset)\s*=\s*)"
    r"(\"|')\s*(?:javascript|vbscript|data:(?!image/)|about|file)\s*:[^\"'>]*\2",
    flags=_re.IGNORECASE,
)


def _sanitize_export_html(html: str) -> str:
    """Strip XSS vectors from user-provided HTML before storing it."""
    sanitized = html
    for pattern in _XSS_PAIRED_TAG_PATTERNS:
        sanitized = pattern.sub("", sanitized)
    sanitized = _XSS_VOID_TAG_RE.sub("", sanitized)
    sanitized = _XSS_EVENT_HANDLER_RE.sub("", sanitized)
    sanitized = _XSS_DANGEROUS_URL_ATTR_RE.sub(r"\1\2#\2", sanitized)
    return sanitized


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
                "script-src 'none'; "
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
    kufar_client: KufarClient = Depends(get_kufar_client),
):
    """Quick condition assessment from listing photos."""
    ai = _check_ai_available()
    await _check_rate_limit(request, _user.user_id)

    settings = getattr(request.app.state, "settings", None)
    if settings is None:
        settings = get_settings()

    dataset = await load_query_dataset(
        query=payload.query,
        currency="BYN",
        strict_search=False,
        settings=settings,
        client=kufar_client,
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

    quick_condition_timeout = getattr(settings, "ai_quick_condition_timeout", 45)
    try:
        result = await asyncio.wait_for(ai.quick_condition(images), timeout=quick_condition_timeout)
    except (TimeoutError, httpx.HTTPError, RuntimeError, ValueError) as exc:
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
