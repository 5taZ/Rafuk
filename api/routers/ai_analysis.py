"""
AI Analysis router — listing analysis HTTP endpoints.

BE-C5 split: this used to be a 1300+ line god-file that mixed
HTTP handlers, in-memory shadow stores, privacy cleanup, rate
limiting, consent checks, audit logging, and helper utilities.
The supporting concerns now live in dedicated services:

* ``api.services.ai_shadow_store``  — _tasks + pruner
* ``api.services.ai_privacy``       — clear_user_ai_data
* ``api.services.ai_guards``        — _check_ai_*, _coerce_string_list
* ``api.services.ai_audit``         — _log_ai_audit

The names are re-exported from this module for backward compat
with three sibling routers that import them
(``api.routers.ai_listing_assistant`` and ``ai_tools``) and the
older test files that grew up against the monolith. Treat the
re-exports as deprecated aliases — new code should reach the
service modules directly.
"""

from __future__ import annotations

import logging
import secrets
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request

from api.config import get_settings
from api.dependencies import get_cache, get_kufar_client, get_telegram_user
from api.schemas import AIAnalysisRequest

# ── Re-exports for back-compat with sibling routers and tests ────────────
#
# Do NOT remove without updating every importer:
#   tests/test_consent.py            → clear_user_ai_data
#   api/routers/consent.py           → clear_user_ai_data (deferred)
#   api/main.py                      → periodic_prune_shadow_stores
#   api/routers/ai_listing_assistant → _AI_ANALYSIS_ERRORS, _check_ai_*,
#                                       _coerce_string_list, _log_ai_audit
#   api/routers/ai_tools             → same set as ai_listing_assistant
from api.services.ai_analysis_pipeline import (  # noqa: F401 — re-exports
    _AC,
    _AI_ANALYSIS_ERRORS,
    DISCLAIMER,
    _AnalysisComplete,
    _build_fallback_response,
    _build_resale_potential,
    _deliver_fallback_result,
    _init_analysis_state,
    _parse_list_age_days,
    _run_analysis,
)
from api.services.ai_audit import _log_ai_audit  # noqa: F401 — re-export
from api.services.ai_guards import (  # noqa: F401 — re-exports
    _check_ai_available,
    _check_ai_consent,
    _check_rate_limit,
    _coerce_string_list,
)
from api.services.ai_privacy import clear_user_ai_data  # noqa: F401 — re-export

# ``get_ai_service`` is re-exported here so ai_guards' late-bound
# lookup (and several monkeypatch sites in tests) can stay anchored
# at api.routers.ai_analysis.get_ai_service. Treat this as a public
# API surface for the AI router family.
from api.services.ai_service import get_ai_service  # noqa: F401 — re-export
from api.services.ai_shadow_store import (  # noqa: F401 — re-exports
    _MAX_SHADOW_ENTRIES,
    _get_shadow_lock,
    _prune_old_tasks_shadow,
    _tasks,
    periodic_prune_shadow_stores,
)
from api.services.ai_task_store import (
    _get_task,
    _set_task,
    _spawn_bg_task,
    _update_task,
)
from api.services.kufar_client import KufarClient
from api.services.query_pipeline import load_query_dataset  # noqa: F401 — re-export

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ai", tags=["ai"])


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
        if isinstance(cached, dict) and cached.get("_ai_warning"):
            await cache.delete(cache_key)
        else:
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
            _run_analysis(
                task_id, payload, settings, cache, kufar_client, user_id=_user.user_id,
            ),
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
