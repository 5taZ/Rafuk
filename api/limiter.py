"""Shared rate limiter instance for the application.

Keys by Telegram user_id when available (authenticated endpoints),
falls back to client IP for public endpoints.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from urllib.parse import urlparse

import redis
from fastapi import Request
from redis.exceptions import RedisError
from slowapi import Limiter

from api.services.client_ip import get_client_ip

logger = logging.getLogger(__name__)

rate_limiter_degraded: bool = False


def _rate_limit_key(request: Request) -> str:
    """Use telegram user_id when available, otherwise fall back to IP."""
    init_data = getattr(request.state, "telegram_user", None)
    if init_data is not None:
        return f"tg:{init_data.user_id}"

    return get_client_ip(request) or "unknown"


def _redis_reachable(url: str) -> bool:
    """Quick command probe to decide whether to use Redis or fall back to memory.

    slowapi's Limiter doesn't ping at construction — it only fails on the
    first INCR. If Redis is down, every request would 500 with
    ``ConnectionError: 111 Connection refused``. So we probe up-front.

    Uses a very short timeout (0.1s) to minimize blocking at import time.
    Cached via lru_cache so the command probe runs once on first call, not
    at every module import.
    """
    try:
        parsed = urlparse(url)
    except (ValueError, TypeError):
        return False
    if parsed.scheme not in {"redis", "rediss", "unix"}:
        return False
    try:
        # P1-SEC-INF-01: TCP-open is not enough; ping through redis-py so
        # wrong passwords, ACL failures, DB selection errors, and TLS
        # transport issues are caught before SlowAPI accepts the storage URI.
        client = redis.Redis.from_url(
            url,
            socket_connect_timeout=0.1,
            socket_timeout=0.2,
            retry_on_timeout=False,
        )
        try:
            return bool(client.ping())
        finally:
            client.close()
    except (OSError, RedisError):
        return False


_redis_reachable = lru_cache(maxsize=1)(_redis_reachable)


def _mask_password(url: str) -> str:
    """Mask password in Redis URL for safe logging."""
    try:
        parsed = urlparse(url)
        if parsed.password:
            return url.replace(f":{parsed.password}@", ":****@")
    except Exception:
        pass
    return url


def _create_limiter() -> Limiter:
    """Create the limiter, preferring Redis storage when available.

    Uses the REDIS_URL env var if set. Probes Redis with a short TCP
    connect; if unreachable, falls back to in-memory storage so that
    requests continue to succeed (rate limits become per-process).
    """
    try:
        from api.config import get_settings

        settings = get_settings()
        storage_uri = settings.redis_url or ""
    except Exception:
        logger.info("Rate limiter using in-memory storage (Settings unavailable)")
        return Limiter(key_func=_rate_limit_key, storage_uri="memory://")

    if storage_uri.startswith(("redis://", "rediss://", "unix://")) and _redis_reachable(
        storage_uri
    ):
        logger.info("Rate limiter configured with Redis storage URI")
        return Limiter(key_func=_rate_limit_key, storage_uri=storage_uri)

    global rate_limiter_degraded
    logger.error(
        "Rate limiter using in-memory fallback — "
        "limits not shared across workers. Redis at %s unreachable",
        _mask_password(storage_uri) or "<unset>",
    )
    rate_limiter_degraded = True
    return Limiter(key_func=_rate_limit_key, storage_uri="memory://")


limiter = _create_limiter()
