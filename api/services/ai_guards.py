"""
BE-C5: pre-flight guards for AI router endpoints.

Three thin checks every AI handler runs before doing real work:

* ``_check_ai_available`` — make sure the upstream AI service is
  configured. Fails loud (503) if not, so the client doesn't see a
  generic 500 from a missing API key.
* ``_check_rate_limit`` — per-user hourly + shared daily caps,
  backed by ``cache.incr``. Centralised so every endpoint
  (analyze, listing-assistant, ai-tools) gets the same behaviour
  without re-implementing the math.
* ``_check_ai_consent`` — verify the user granted ``ai_analysis``,
  ``cross_border`` and ``pd_processing`` consent. Skipped in DEBUG
  mode for local dev (we don't want to force a consent grant just to
  test the pipeline).
* ``_coerce_string_list`` — input sanitiser used by the listing
  assistant + ai_tools to normalise free-form lists from prompts.
  Lives here because every guard-using router also imports it.

Lifted from ``api/routers/ai_analysis.py`` as part of the BE-C5
god-file split. The previous monolith had these helpers at module
scope right next to the FastAPI handlers, and three sibling routers
imported them from the router file (a code smell in itself —
routers shouldn't be importing from each other).
"""

from __future__ import annotations

import re as _re
from typing import Any

from fastapi import HTTPException, Request
from sqlalchemy import select

from api.config import get_settings
from api.models import UserConsent
from api.services.consent_policy import CURRENT_POLICY_VERSION
from api.services.workflow_store import resolve_user_id

_REQUIRED_AI_CONSENTS = ("ai_analysis", "cross_border", "pd_processing")


def _check_ai_available():
    """503 if the upstream AI service hasn't been configured.

    The lookup goes through ``api.routers.ai_analysis.get_ai_service``
    (re-exported from this package) rather than importing directly
    from ``api.services.ai_service`` so legacy tests that
    ``monkeypatch.setattr(ai_analysis, "get_ai_service", ...)`` —
    which is the patch hook those tests grew up with — still
    intercept the call. The router is already imported by the time
    any handler runs, so the deferred import is essentially free.
    """
    from api.routers import ai_analysis as _aa  # late, preserves test patch

    service = _aa.get_ai_service()
    if not service.available:
        raise HTTPException(status_code=503, detail="AI analysis is not configured")
    return service


async def _check_rate_limit(
    request: Request,
    user_id: int,
    *,
    endpoint: str = "default",
) -> None:
    """Per-user rate limit: hourly per-endpoint + shared daily cap.

    Hits two cache counters via ``incr``: ``ai_rate:{user_id}:{endpoint}``
    (1h TTL) and ``ai_daily:{user_id}`` (24h TTL). Both raise 429 when
    the limits are crossed. Limits come from ``settings`` so ops can
    tune them without redeploying the router.

    Like ``_check_ai_available``, ``get_cache`` is resolved via
    ``api.routers.ai_analysis`` so per-router test patches keep
    working.
    """
    from api.routers import ai_analysis as _aa  # late, preserves test patch

    cache = _aa.get_cache(request)
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
    """Verify the user has granted every consent needed for AI processing.

    Skipped in DEBUG so local dev doesn't have to grant consent for
    every test request — the consent UI is exercised by the consent
    test suite instead. In production every AI endpoint gates on
    this; failure raises HTTP 403 with a structured ``consent_type``
    payload the frontend uses to pop the right modal.
    """
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
                    UserConsent.consent_type.in_(_REQUIRED_AI_CONSENTS),
                    UserConsent.version == CURRENT_POLICY_VERSION,
                    UserConsent.revoked_at.is_(None),
                )
            )
        ).scalars().all()

        for consent_type in _REQUIRED_AI_CONSENTS:
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


def _coerce_string_list(raw: Any, *, limit: int, max_len: int) -> list[str]:
    """Deduplicating string-list coercer shared with sub-routers.

    Used by ai_listing_assistant and ai_tools for prompt-input
    sanitisation: collapses whitespace, trims to ``max_len``, drops
    empties, dedupes case-insensitively, caps at ``limit`` items.
    """
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
