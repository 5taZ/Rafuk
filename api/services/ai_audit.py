"""
BE-C5: Belarus Law No. 99-З audit-log writer for AI calls.

Every AI endpoint records who asked for what model, against which
ad/query, and how long it took. The function used to live inline at
the bottom of ``api/routers/ai_analysis.py`` next to the FastAPI
handlers and was imported across three sibling routers — moving it
into ``api/services/`` keeps router→router imports out of the
codebase.

Cleanup of these rows is owned by ``cleanup_ai_audit_log`` in the
scheduler (``scheduler/collector.py``); this module only writes.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from typing import Any

from api.metrics import observe_ai_audit_failure
from api.models import AIAuditLog
from api.services.ai_sanitize import sanitize_user_text
from api.services.workflow_store import resolve_user_id

logger = logging.getLogger(__name__)

# SEC-NEW-8: module-level cache for the derived HMAC secret so we
# don't re-hash on every audit call.
# L1: In multi-worker deployments each worker process derives its own
# HMAC secret at first call. This means audit hashes from different
# workers are not comparable, but the audit log is write-heavy and
# rarely read cross-worker, so the impact is negligible.
_audit_secret_cache: bytes | None = None


def _get_audit_secret() -> bytes:
    """Return the HMAC key for audit hashes.

    Uses AUDIT_HASH_SECRET if configured, otherwise derives a key from
    BOT_TOKEN via SHA-256. Cached module-level after first call.
    """
    global _audit_secret_cache
    if _audit_secret_cache is not None:
        return _audit_secret_cache
    from api.config import get_settings
    settings = get_settings()
    if settings.audit_hash_secret:
        _audit_secret_cache = settings.audit_hash_secret.get_secret_value().encode("utf-8")
    else:
        _audit_secret_cache = hashlib.sha256(
            settings.bot_token.get_secret_value().encode("utf-8"),
        ).digest()
    return _audit_secret_cache


def audit_text_hash(value: str | None, *, secret: bytes) -> str | None:
    """HMAC-SHA256 hash of ``value`` keyed by ``secret``.

    SEC-NEW-8: HMAC keyed by AUDIT_HASH_SECRET (or BOT_TOKEN-derived
    fallback) so leaked audit rows can't be rainbow-tabled back to
    user queries.

    Migration policy: old rows keep their unsalted SHA-256 hashes.
    New writes go through this function. The DB column shape is
    unchanged (TEXT). Read-side code that needs to verify old rows
    should use ``audit_text_sha256`` for those.
    """
    if value is None:
        return None
    text = str(value)
    if not text:
        return None
    return hmac.new(secret, text.encode("utf-8"), hashlib.sha256).hexdigest()


# SEC-NEW-8: kept for read-side compatibility with rows hashed before
# the HMAC migration. New writes go through ``audit_text_hash``.
def audit_text_sha256(value: str | None) -> str | None:
    if value is None:
        return None
    text = str(value)
    if not text:
        return None
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sanitize_audit_text(
    value: str | None,
    *,
    max_length: int,
    context: str,
) -> str | None:
    return sanitize_user_text(value, max_length=max_length, context=f"ai_audit.{context}")


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
    ip_address: str | None = None,
) -> None:
    """Write an AI audit log entry (Belarus Law No. 99-З requirement).

    OPUS-17: ``ip_address`` captures the trusted client IP at the
    moment of the AI call so audit rows match the IP that
    ``grant_consent`` already records. The router resolves IP via
    the shared ``client_ip`` helper before calling us.

    Best-effort: a failure to write the audit row is logged at WARN
    but never raises into the caller. The actual AI response was
    already produced by the time we're called, so dropping the audit
    row degrades observability rather than the user's request.
    """
    try:
        async with session_factory() as session:
            uid = await resolve_user_id(session, telegram_user_id)
            if uid is None:
                return
            query_preview = sanitize_audit_text(query, max_length=256, context="query")
            if result_summary or cached:
                result_preview = sanitize_audit_text(
                    result_summary or "cached",
                    max_length=512,
                    context="result_summary",
                )
            else:
                result_preview = None
            entry = AIAuditLog(
                user_id=uid,
                endpoint=endpoint,
                ad_id=ad_id,
                # P1-PRIV-01: keep a sanitized preview plus a keyed
                # digest for traceability; never persist raw user text.
                query=query_preview,
                # SEC-NEW-8: HMAC keyed by AUDIT_HASH_SECRET (or BOT_TOKEN-derived fallback)
                # so leaked audit rows can't be rainbow-tabled back to user queries.
                query_hash=audit_text_hash(query, secret=_get_audit_secret()),
                result_summary=result_preview,
                result_summary_hash=audit_text_hash(result_summary, secret=_get_audit_secret()),
                model=model,
                latency_ms=latency_ms,
                ip_address=ip_address,
            )
            session.add(entry)
            await session.commit()
    except Exception:
        # G-05: strict mode (AI_AUDIT_REQUIRED=true) propagates audit-write failures
        # so the AI endpoint returns 5xx instead of degrading silently.
        from api.config import get_settings
        if get_settings().ai_audit_required:
            raise
        observe_ai_audit_failure(endpoint=endpoint)
        logger.warning("Failed to write AI audit log", exc_info=True)
