"""Shared rate limiter instance for the application.

Keys by Telegram user_id when available (authenticated endpoints),
falls back to client IP for public endpoints.
"""

from __future__ import annotations

import logging
from urllib.parse import urlparse

from fastapi import Request
from slowapi import Limiter
from slowapi.util import get_remote_address

logger = logging.getLogger(__name__)

rate_limiter_degraded: bool = False


def _rate_limit_key(request: Request) -> str:
    """Use telegram user_id when available, otherwise fall back to IP."""
    init_data = getattr(request.state, "telegram_user", None)
    if init_data is not None:
        return f"tg:{init_data.user_id}"

    return get_remote_address(request)


def _redis_reachable(url: str) -> bool:
    """Quick TCP probe to decide whether to use Redis or fall back to memory.

    slowapi's Limiter doesn't ping at construction — it only fails on the
    first INCR. If Redis is down, every request would 500 with
    ``ConnectionError: 111 Connection refused``. So we probe up-front.

    Uses a very short timeout (0.1s) to minimize blocking at import time.
    This is called once at module load; the cost is acceptable.
    """
    import socket

    try:
        parsed = urlparse(url)
    except (ValueError, TypeError):
        return False
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 6379
    try:
        with socket.create_connection((host, port), timeout=0.1):
            return True
    except OSError:
        return False


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
