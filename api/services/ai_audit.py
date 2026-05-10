"""
BE-C5: Belarus Law No. 91-Z audit-log writer for AI calls.

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

import logging
from typing import Any

from api.models import AIAuditLog
from api.services.workflow_store import resolve_user_id

logger = logging.getLogger(__name__)


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
    """Write an AI audit log entry (Belarus Law No. 91-Z requirement).

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
