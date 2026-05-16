"""
BE-C5 + BE-C6: per-user data erasure (Belarus Law No. 99-З).

Lives in api/services/ so consent.py and the AI router can both
call into the same helper without one router importing another.

The function is the single source of truth for the cleanup that
runs when a Telegram user deletes their account. See the docstring
on ``clear_user_ai_data`` for the exact list of namespaces — keep
that list in sync with whatever new per-user keys we introduce.
"""

from __future__ import annotations

import logging
from typing import Any

from api.config import get_settings
from api.services.ai_shadow_store import _get_shadow_lock, _tasks

logger = logging.getLogger(__name__)


async def clear_user_ai_data(
    telegram_user_id: int,
    *,
    cache: Any | None = None,
    session_factory: Any | None = None,
) -> None:
    """Remove all per-user data for a Telegram user (Law-99-З erasure).

    BE-C6: this used to only touch the AI shadow stores plus a few
    Redis namespaces; the matching call from delete_account also
    issued ``cache.delete("ai_rate:{tg}")`` (without the trailing
    ``:*``) which is a no-op against the actual key shape
    ``ai_rate:{tg}:{endpoint}``. The fix is to put all the per-user
    Redis cleanup behind a single function and have it cover every
    namespace that stores a user_id, including the auth-side data
    that was previously left behind:

    * ``ai_task:u{tg}:*`` — AI task results (api/services/ai_task_store)
    * ``ai_listing:u{tg}:*`` — Listing Assistant response cache
    * ``ai_rate:{tg}:*`` — per-endpoint hourly limiter buckets
    * ``ai_daily:{tg}`` — shared daily limiter counter
    * ``auth:blacklist:{tg}`` — session blacklist entry (if any)
    * ``auth:replay_warn:{tg}`` — replay-warning counter (OPUS-8)
    * ``auth:initdata:*`` — IP-tracking entries (filter by user_id in value)
    * in-memory ``_tasks`` shadow store

    AI-HIGH (issues §3.2): the shared analysis cache
    (``ai_analysis:{version}:{ad_id}:{query}:cat=*``) has no user_id
    in the key, but a freshly-revoked user still has its previously-
    cached results live. When ``session_factory`` is provided we walk
    that user's ``AIAuditLog`` entries (which DO carry user_id) and
    SCAN+DELETE every analysis cache key matching ``{ad_id, query}``.
    Other users continue to share whatever cohort cache the eviction
    didn't touch — a partial cold cache is the right trade-off
    against silently serving consented results to a revoked user.

    OPUS-20: ``cache`` lets callers reuse ``app.state.cache`` instead
    of opening a fresh Redis pool just for the cleanup. With the
    pool already warm, both ``delete_account`` and ``revoke_consent``
    avoid the connect/close cost. When ``cache`` isn't provided we
    fall back to the original "open + close" behaviour so legacy
    callers (and the privacy module's own tests) keep working.
    """
    from api.services.cache import MemoryCache, RedisCache

    owns_cache = False
    if cache is None:
        owns_cache = True
        settings = get_settings()
        try:
            cache = RedisCache.from_url(settings.redis_url)
            if not await cache.ping():
                # PR-10: log the in-memory fallback so operators can
                # see the privacy / account-deletion path lost its
                # cross-worker view of per-user keys. Without the log
                # the Redis blip showed up as "consent revoke didn't
                # actually evict the AI cache" tickets days later.
                logger.warning(
                    "Redis ping failed in clear_user_ai_data — falling back to "
                    "MemoryCache; per-worker eviction only.",
                )
                cache = MemoryCache()
        except Exception:  # noqa: BLE001 — fall back to in-memory on any cache init issue
            logger.warning(
                "RedisCache.from_url raised in clear_user_ai_data — falling back "
                "to MemoryCache; per-worker eviction only.",
                exc_info=True,
            )
            cache = MemoryCache()

    try:
        redis_client = getattr(cache, "_client", None)
        if redis_client is not None:
            # Patterns where the user id lives in the key. SCAN+DELETE
            # rather than KEYS so we don't block Redis on big DBs.
            user_scoped_patterns = [
                f"ai_task:u{telegram_user_id}:*",
                f"ai_listing:u{telegram_user_id}:*",
                f"ai_rate:{telegram_user_id}:*",
                f"ai_daily:{telegram_user_id}",
                # Session blacklist key is a fixed shape, but reuse
                # the same scan loop for symmetry — it's a 1-key scan
                # in practice.
                f"auth:blacklist:{telegram_user_id}",
                # OPUS-8: rolling replay counter must die with the
                # user — otherwise a previously-flagged session leaks
                # its history into a fresh account.
                f"auth:replay_warn:{telegram_user_id}",
            ]
            for pattern in user_scoped_patterns:
                cursor = 0
                while True:
                    cursor, keys = await redis_client.scan(cursor, match=pattern, count=100)
                    if keys:
                        await redis_client.delete(*keys)
                    if cursor == 0:
                        break

            # Patterns where the user_id is inside the value, not the
            # key.
            value_keyed_patterns = ("auth:initdata:*",)
            for pattern in value_keyed_patterns:
                cursor = 0
                while True:
                    cursor, keys = await redis_client.scan(cursor, match=pattern, count=100)
                    for key in keys:
                        decoded = key.decode() if isinstance(key, bytes) else key
                        item = await cache.get_json(decoded)
                        if not item:
                            continue
                        owner = item.get("user_id")
                        if owner == telegram_user_id:
                            await redis_client.delete(key)
                    if cursor == 0:
                        break

            # AI-HIGH (issues §3.2): also evict ``ai_analysis:*`` entries
            # the user touched. Cache keys have shape
            # ``ai_analysis:{version}:{ad_id}:{query}:cat={category}``,
            # so a glob ``ai_analysis:*:{ad_id}:{query}:*`` is unique to
            # this user's audit-logged tuples. We resolve the user's
            # internal id and walk the audit log within the cache TTL
            # window (1h is the default) to bound the work.
            if session_factory is not None:
                try:
                    from datetime import UTC, datetime, timedelta

                    from sqlalchemy import select

                    from api.models import AIAuditLog, User

                    cutoff = datetime.now(UTC) - timedelta(hours=2)
                    async with session_factory() as session:
                        internal_id = await session.scalar(
                            select(User.id).where(
                                User.telegram_user_id == telegram_user_id
                            )
                        )
                        if internal_id is not None:
                            rows = await session.execute(
                                select(AIAuditLog.ad_id, AIAuditLog.query)
                                .where(
                                    AIAuditLog.user_id == internal_id,
                                    AIAuditLog.created_at >= cutoff,
                                    AIAuditLog.endpoint == "analyze",
                                    AIAuditLog.ad_id.is_not(None),
                                )
                                .distinct()
                            )
                            for ad_id, query in rows:
                                if not ad_id or not query:
                                    continue
                                # Redis glob-escape only ``[`` and ``]``;
                                # query strings rarely contain them, but
                                # if they do the SCAN simply returns no
                                # match — we skip rather than crash.
                                pattern = f"ai_analysis:*:{ad_id}:{query}:*"
                                cursor = 0
                                while True:
                                    cursor, keys = await redis_client.scan(
                                        cursor, match=pattern, count=50
                                    )
                                    if keys:
                                        await redis_client.delete(*keys)
                                    if cursor == 0:
                                        break
                except Exception:  # noqa: BLE001 — eviction is best-effort
                    logger.warning(
                        "ai_analysis cache eviction failed for user %d",
                        telegram_user_id,
                        exc_info=True,
                    )
        # Also clean shadow stores. The lock guards us from a
        # concurrent pruner walking the same keys.
        async with _get_shadow_lock():
            for task_id in list(_tasks.keys()):
                if _tasks[task_id].get("_telegram_user_id") == telegram_user_id:
                    _tasks.pop(task_id, None)
    except Exception:
        logger.warning("Failed to clear AI data for user %d", telegram_user_id, exc_info=True)
    finally:
        # Only close the cache we created ourselves; passed-in caches
        # belong to the caller's lifecycle.
        if owns_cache and isinstance(cache, RedisCache):
            await cache.aclose()
