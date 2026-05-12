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

from api.config import get_settings
from api.services.ai_shadow_store import _get_shadow_lock, _tasks

logger = logging.getLogger(__name__)


async def clear_user_ai_data(telegram_user_id: int) -> None:
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
    * ``ai_rate:{tg}:*`` — per-endpoint hourly limiter buckets
    * ``ai_daily:{tg}`` — shared daily limiter counter
    * ``auth:blacklist:{tg}`` — session blacklist entry (if any)
    * ``auth:initdata:*`` — IP-tracking entries (filter by user_id in value)
    * in-memory ``_tasks`` shadow store

    The shared deterministic AI cache (``ai_analysis:v5:*``) is
    intentionally untouched — it has no user_id, evicting it would
    just hand cold caches to everyone else.
    """
    from api.services.cache import MemoryCache, RedisCache

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
            # Patterns where the user id lives in the key. SCAN+DELETE
            # rather than KEYS so we don't block Redis on big DBs.
            user_scoped_patterns = [
                f"ai_task:u{telegram_user_id}:*",
                f"ai_rate:{telegram_user_id}:*",
                f"ai_daily:{telegram_user_id}",
                # Session blacklist key is a fixed shape, but reuse
                # the same scan loop for symmetry — it's a 1-key scan
                # in practice.
                f"auth:blacklist:{telegram_user_id}",
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
        # Also clean shadow stores. The lock guards us from a
        # concurrent pruner walking the same keys.
        async with _get_shadow_lock():
            for task_id in list(_tasks.keys()):
                if _tasks[task_id].get("_telegram_user_id") == telegram_user_id:
                    _tasks.pop(task_id, None)
    except Exception:
        logger.warning("Failed to clear AI data for user %d", telegram_user_id, exc_info=True)
    finally:
        if isinstance(cache, RedisCache):
            await cache.aclose()
