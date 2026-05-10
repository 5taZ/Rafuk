"""AI Analysis router — listing analysis and listing assistant."""

from __future__ import annotations

import asyncio
import logging
import re as _re
import secrets
from datetime import UTC, datetime
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select

from api.config import get_settings
from api.dependencies import get_cache, get_kufar_client, get_telegram_user
from api.models import AIAuditLog, UserConsent
from api.schemas import AIAnalysisRequest, AIAnalysisResponse
from api.services.ai_analysis_pipeline import (
    DISCLAIMER,
    _AI_ANALYSIS_ERRORS,
    _AnalysisComplete,
    _AC,
    _build_fallback_response,
    _deliver_fallback_result,
    _init_analysis_state,
    _parse_list_age_days,
    _build_resale_potential,
    _run_analysis,
)
from api.services.ai_export import (  # noqa: F401 — re-exported for ai_listing_assistant / tests
    AIExportReportRequest,
    create_export_report,
    get_export_report,
    _sanitize_export_html,
)
from api.services.ai_service import get_ai_service
from api.services.query_pipeline import load_query_dataset  # noqa: F401 — re-exported for tests
from api.services.ai_task_store import (
    _export_delete,
    _export_get,
    _export_set,
    _get_task,
    _set_task,
    _spawn_bg_task,
    _task_cache_key,
    _task_version,
    _update_task,
    _bg_tasks,
)
from api.services.cache import CacheBackend
from api.services.kufar_client import KufarClient
from api.services.workflow_store import resolve_user_id

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ai", tags=["ai"])

# Include the export sub-router
from api.services.ai_export import export_router

router.include_router(export_router)


# ── Shadow stores (in-process fallback when Redis is unavailable) ────────
#
# Access to _tasks / _exports is serialised through _shadow_lock. Without
# the lock, concurrent pruners and consent-deletion paths iterate the
# dicts while other coroutines mutate them (task status updates, export
# creation), which can raise "dictionary changed size during iteration"
# or silently drop records. One lock covers both dicts — they are small,
# modified infrequently, and often walked together (e.g. during account
# deletion).

_tasks: dict[str, dict] = {}
_exports: dict[str, dict] = {}

_MAX_SHADOW_ENTRIES = 50

# Lazy-init so the lock binds to the currently-running loop rather than
# whatever loop happened to be active at import time (important for
# tests that spin up multiple loops).
_shadow_lock: asyncio.Lock | None = None


def _get_shadow_lock() -> asyncio.Lock:
    global _shadow_lock
    if _shadow_lock is None:
        _shadow_lock = asyncio.Lock()
    return _shadow_lock


def _export_ttl() -> int:
    return int(getattr(get_settings(), "ai_export_ttl", 900) or 900)


def _prune_old_tasks_shadow_unlocked() -> None:
    """Caller must hold _get_shadow_lock()."""
    now = datetime.now(UTC).timestamp()
    from api.services.ai_task_store import _task_ttl

    ttl = _task_ttl()
    expired = [k for k, v in _tasks.items() if now - v.get("_updated_ts", 0) > ttl]
    for k in expired:
        _tasks.pop(k, None)
    while len(_tasks) > _MAX_SHADOW_ENTRIES:
        oldest_key = min(
            list(_tasks.keys()),
            key=lambda k: _tasks[k].get("_updated_ts", 0),
            default=None,
        )
        if oldest_key is None:
            break
        _tasks.pop(oldest_key, None)


async def _prune_old_tasks_shadow() -> None:
    async with _get_shadow_lock():
        _prune_old_tasks_shadow_unlocked()


def _prune_old_exports_unlocked() -> None:
    """Caller must hold _get_shadow_lock()."""
    if not _exports:
        return
    now = datetime.now(UTC).timestamp()
    ttl = _export_ttl()
    expired = [token for token, item in _exports.items() if now - item.get("_created_ts", 0) > ttl]
    for token in expired:
        _exports.pop(token, None)
    while len(_exports) > _MAX_SHADOW_ENTRIES:
        oldest_key = min(_exports, key=lambda k: _exports[k].get("_created_ts", 0))
        _exports.pop(oldest_key, None)


async def _prune_old_exports() -> None:
    async with _get_shadow_lock():
        _prune_old_exports_unlocked()


# ── AI data cleanup (called from consent.py during account deletion) ─────

async def clear_user_ai_data(telegram_user_id: int) -> None:
    """Remove all AI analysis data for a user from Redis cache."""
    from api.services.cache import RedisCache, MemoryCache

    settings = get_settings()
    try:
        cache = RedisCache.from_url(settings.redis_url)
        if not await cache.ping():
            cache = MemoryCache()
    except Exception:
        cache = MemoryCache()

    try:
        redis_client = getattr(cache, "_client", None)
        if redis_client is not None:
            # NOTE: ai_analysis:v5:* is intentionally NOT cleaned here.
            # It's a shared deterministic cache keyed by (ad_id, query, category)
            # with no user_id — entries are reused across all users and expire
            # via their own TTL. Wiping the entire cache on a single user's
            # account deletion would punish unrelated users with cache misses.
            user_scoped_patterns = [
                f"ai_task:u{telegram_user_id}:*",
                f"ai_rate:{telegram_user_id}:*",
                f"ai_daily:{telegram_user_id}:*",
            ]
            for pattern in user_scoped_patterns:
                cursor = 0
                while True:
                    cursor, keys = await redis_client.scan(cursor, match=pattern, count=100)
                    if keys:
                        await redis_client.delete(*keys)
                    if cursor == 0:
                        break

            # ai_export:* tokens are random; filter by stored user_id in the value.
            cursor = 0
            while True:
                cursor, keys = await redis_client.scan(cursor, match="ai_export:*", count=100)
                for key in keys:
                    item = await cache.get_json(key.decode() if isinstance(key, bytes) else key)
                    if item and item.get("_telegram_user_id") == telegram_user_id:
                        await redis_client.delete(key)
                if cursor == 0:
                    break
        # Also clean shadow stores. The lock guards us from a
        # concurrent pruner walking the same keys.
        async with _get_shadow_lock():
            for task_id in list(_tasks.keys()):
                if _tasks[task_id].get("_telegram_user_id") == telegram_user_id:
                    _tasks.pop(task_id, None)
            for export_id in list(_exports.keys()):
                if _exports[export_id].get("_telegram_user_id") == telegram_user_id:
                    _exports.pop(export_id, None)
    except Exception:
        logger.warning("Failed to clear AI data for user %d", telegram_user_id, exc_info=True)
    finally:
        if isinstance(cache, RedisCache):
            await cache.aclose()


async def periodic_prune_shadow_stores() -> None:
    """Background task: periodically prune in-memory shadow stores."""
    try:
        while True:
            await asyncio.sleep(300)
            await _prune_old_tasks_shadow()
            await _prune_old_exports()
    except asyncio.CancelledError:
        pass


# ── Helpers ──────────────────────────────────────────────────────────────


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
    """Verify the user has granted AI analysis and cross-border consent."""
    if get_settings().debug:
        return

    session_factory = request.app.state.session_factory
    async with session_factory() as session:
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
    cached: bool = False,
) -> None:
    """Write an AI audit log entry (Belarus Law No. 91-Z requirement)."""
    try:
        async with session_factory() as session:
            uid = await resolve_user_id(session, telegram_user_id)
            if uid is None:
                return
            entry = AIAuditLog(
                user_id=uid,
                endpoint=endpoint,
                ad_id=ad_id,
                query=(query or "")[:256] if query else None,
                result_summary=(
                    (result_summary or "cached")[:512]
                    if (result_summary or cached)
                    else None
                ),
                model=model,
                latency_ms=latency_ms,
            )
            session.add(entry)
            await session.commit()
    except Exception:
        logger.warning("Failed to write AI audit log", exc_info=True)


# ── Endpoints ────────────────────────────────────────────────────────────


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


@router.post("/analyze")
async def analyze_listing(
    payload: AIAnalysisRequest,
    request: Request,
    _user=Depends(get_telegram_user),
    kufar_client: KufarClient = Depends(get_kufar_client),
):
    """Start async AI analysis. Returns task_id immediately for polling."""
    _check_ai_available()
    await _check_ai_consent(request, _user.user_id)

    cache = get_cache(request)
    cache_key = f"ai_analysis:v5:{payload.ad_id}:{payload.query}:cat={payload.category}"
    cached = await cache.get_json(cache_key)
    if cached:
        await _log_ai_audit(
            request.app.state.session_factory,
            telegram_user_id=_user.user_id,
            endpoint="analyze",
            ad_id=str(payload.ad_id),
            query=payload.query,
            model=get_settings().ai_model,
            cached=True,
        )
        return {"task_id": None, "cached": True, "result": cached}

    await _check_rate_limit(request, _user.user_id, endpoint="analyze")

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

    task_id = secrets.token_urlsafe(16)
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

    try:
        _spawn_bg_task(
            _run_analysis(task_id, payload, settings, cache, kufar_client, user_id=_user.user_id),
            name=f"ai-analysis-{task_id[:8]}",
        )
    except RuntimeError:
        await _update_task(
            cache, task_id,
            user_id=_user.user_id,
            status="error",
            error="Сервер перегружен, попробуйте позже",
        )
        raise HTTPException(
            status_code=503,
            detail="Сервер перегружен, попробуйте позже",
        ) from None

    return {"task_id": task_id}


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
