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
* ``_check_ai_entitlement`` — same status gate as quota consumption,
  but without spending quota on cache hits.
* ``_check_ai_consent`` — verify the user granted ``ai_analysis``,
  ``cross_border`` and ``pd_processing`` consent. The skip path is
  tied to ``auth_bypass`` (NOT plain ``debug``); ``auth_bypass``
  rejects production / remote DB at config-validation time, so an
  accidentally-enabled ``DEBUG=true`` in production no longer turns
  the consent gate off. Tests use the same flag via conftest.
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
from sqlalchemy.exc import IntegrityError

from api.config import get_settings
from api.models import User, UserConsent
from api.services.account_status import (
    BARE_SEARCH_STATUS_CODE,
    QUOTA_BUCKET_AI,
    QUOTA_BUCKET_ASSISTANT,
    _quota_error_message,
    consume_quota,
    get_effective_status,
    get_status_by_code,
    quota_snapshot,
)
from api.services.consent_policy import CURRENT_POLICY_VERSION
from api.services.workflow_store import resolve_user_id

_REQUIRED_AI_CONSENTS = ("ai_analysis", "cross_border", "pd_processing")
_ASSISTANT_QUOTA_ENDPOINTS = {"listing", "listing_assistant"}


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
    """Consume one status-based AI quota unit for this endpoint.

    The public signature stays compatible with the old per-endpoint
    limiter so sibling routers and tests can keep monkeypatching it.
    ``listing``/``listing_assistant`` spend from the assistant bucket;
    buyer-facing AI endpoints spend from the shared AI bucket.
    """
    from api.routers import ai_analysis as _aa  # late, preserves test patch

    cache = _aa.get_cache(request)
    bucket = _quota_bucket_for_endpoint(endpoint)
    limit = await _status_quota_limit(request, user_id, bucket=bucket)
    await consume_quota(
        cache,
        bucket=bucket,
        telegram_user_id=user_id,
        limit=limit,
    )


async def _check_ai_entitlement(
    request: Request,
    user_id: int,
    *,
    endpoint: str = "default",
) -> None:
    from api.routers import ai_analysis as _aa  # late, preserves test patch

    bucket = _quota_bucket_for_endpoint(endpoint)
    limit = await _status_quota_limit(request, user_id, bucket=bucket)
    if limit > 0:
        return
    snapshot = await quota_snapshot(
        _aa.get_cache(request),
        bucket=bucket,
        telegram_user_id=user_id,
        limit=limit,
    )
    raise HTTPException(
        status_code=403,
        detail={
            "error": "premium_required",
            "bucket": bucket,
            "used": snapshot.used,
            "limit": snapshot.limit,
            "resets_at": snapshot.resets_at.isoformat(),
            "message": _quota_error_message(bucket, premium_required=True),
        },
    )


def _quota_bucket_for_endpoint(endpoint: str) -> str:
    normalized = (endpoint or "").strip().lower()
    return QUOTA_BUCKET_ASSISTANT if normalized in _ASSISTANT_QUOTA_ENDPOINTS else QUOTA_BUCKET_AI


async def _status_quota_limit(request: Request, user_id: int, *, bucket: str) -> int:
    session_factory = request.app.state.session_factory
    async with session_factory() as session:
        user = (
            await session.execute(select(User).where(User.telegram_user_id == int(user_id)))
        ).scalar_one_or_none()
        if user is None and int(user_id) > 0:
            user = User(telegram_user_id=int(user_id), first_name="")
            session.add(user)
            try:
                await session.flush()
            except IntegrityError:
                await session.rollback()
                user = (
                    await session.execute(
                        select(User).where(User.telegram_user_id == int(user_id))
                    )
                ).scalar_one()
            else:
                await session.commit()
        status = (
            await get_effective_status(session, user)
            if user is not None
            else await get_status_by_code(session, BARE_SEARCH_STATUS_CODE)
        )
    if bucket == QUOTA_BUCKET_ASSISTANT:
        return int(status.assistant_daily_limit)
    return int(status.ai_daily_limit)


async def _check_ai_consent(request: Request, user_id: int) -> None:
    """Verify the user has granted every consent needed for AI processing.

    AI-CRITICAL fix (issues §3.2): the previous gate skipped on
    ``settings.debug``. Because ``debug`` only triggers a soft warning
    in some misconfigurations and a stale env file can ship to prod,
    a single careless ``DEBUG=true`` would silently disable Belarus
    Law No. 99-З consent enforcement for every AI endpoint.

    The skip path is now tied to ``settings.auth_bypass`` instead.
    That flag has stricter validators in ``api/config.py`` —
    ``ENV=production`` and a non-local ``DATABASE_URL`` both raise at
    startup — so the bypass cannot reach a real deployment even with
    a hand-edited env file. Local dev / tests opt in by setting
    ``AUTH_BYPASS=true`` (already used to skip Telegram initData).
    """
    if get_settings().auth_bypass:
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
