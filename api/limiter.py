"""Shared rate limiter instance for the application.

Keys by Telegram user_id when available (authenticated endpoints),
falls back to client IP for public endpoints.
"""

from __future__ import annotations

import logging

from fastapi import Request
from slowapi import Limiter
from slowapi.util import get_remote_address

logger = logging.getLogger(__name__)


def _rate_limit_key(request: Request) -> str:
    """Use telegram user_id when available, otherwise fall back to IP."""
    init_data = getattr(request.state, "telegram_user", None)
    if init_data is not None:
        return f"tg:{init_data.user_id}"

    return get_remote_address(request)


def _create_limiter() -> Limiter:
    """Create the limiter, preferring Redis storage when available."""
    try:
        from api.config import get_settings

        settings = get_settings()
        import redis

        r = redis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_connect_timeout=0.5,
        )
        # Verify connectivity
        r.ping()
        logger.info("Rate limiter using Redis storage")
        return Limiter(key_func=_rate_limit_key, storage_uri=settings.redis_url)
    except Exception:
        logger.info("Rate limiter using in-memory storage (Redis unavailable)")
        return Limiter(key_func=_rate_limit_key)


limiter = _create_limiter()
