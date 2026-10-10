"""Shared slowapi rate limiter.

Requests are keyed by the Telegram user id when the request carries a validly signed
``X-Telegram-Init-Data`` header (the age of ``auth_date`` does not matter for keying), and by
the client address otherwise. Behind nginx/cloudflared the client address is only meaningful
when uvicorn trusts the proxy's forwarded headers (``--proxy-headers --forwarded-allow-ips``).
"""

from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from api.config import get_settings
from api.middleware.telegram_auth import parse_init_data_user_id, verify_telegram_signature

DEFAULT_RATE_LIMIT = "120/minute"
INIT_DATA_HEADER = "X-Telegram-Init-Data"


def rate_limit_key(request: Request) -> str:
    init_data = request.headers.get(INIT_DATA_HEADER)
    if init_data:
        try:
            parsed = verify_telegram_signature(init_data, get_settings().bot_token)
            return f"tg:{parse_init_data_user_id(parsed)}"
        except ValueError:
            pass
    return get_remote_address(request)


limiter = Limiter(key_func=rate_limit_key, default_limits=[DEFAULT_RATE_LIMIT])


def rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    # Synchronous on purpose: SlowAPIMiddleware falls back to slowapi's English default
    # handler when the registered handler is a coroutine function.
    return JSONResponse(
        status_code=429,
        content={
            "detail": "Слишком много запросов. Подождите немного и попробуйте снова.",
            "limit": str(exc.detail),
        },
    )
