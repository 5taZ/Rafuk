"""Shared slowapi rate limiter and the application-wide request budget.

Requests are keyed by the Telegram user id when the request carries ``X-Telegram-Init-Data``
that passes the same checks as authentication (HMAC signature and ``auth_date`` age), and by
the client address otherwise, so leaked expired initData cannot spend its owner's budget.
Behind nginx/cloudflared the client address is only meaningful when uvicorn trusts the
proxy's forwarded headers (``--proxy-headers --forwarded-allow-ips``).

Two layers apply to every request under ``/api/``:

* ``ApplicationRateLimitMiddleware``: one budget per key shared by all routes, sized to the
  process-wide Kufar request pacer (see ``application_limit_per_minute``). It also covers
  routes with their own ``@limiter.limit``, which slowapi's middleware skips.
* slowapi: ``DEFAULT_RATE_LIMIT`` per route and key, or the stricter per-route limit of a
  ``@limiter.limit`` decorator.
"""

from __future__ import annotations

import logging
from functools import lru_cache

from fastapi import Request
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from slowapi.wrappers import Limit, LimitGroup
from starlette.types import ASGIApp, Receive, Scope, Send

from api.config import Settings, get_settings
from api.middleware.telegram_auth import verify_telegram_init_data

logger = logging.getLogger(__name__)

DEFAULT_RATE_LIMIT = "120/minute"
INIT_DATA_HEADER = "X-Telegram-Init-Data"

APPLICATION_LIMIT_SCOPE = "application"
APPLICATION_LIMIT_PATH_PREFIX = "/api/"
MIN_APPLICATION_LIMIT_PER_MINUTE = 30
MAX_APPLICATION_LIMIT_PER_MINUTE = 120


def rate_limit_key(request: Request) -> str:
    init_data = request.headers.get(INIT_DATA_HEADER)
    if init_data:
        settings = get_settings()
        try:
            user = verify_telegram_init_data(
                init_data,
                settings.bot_token,
                max_age_seconds=settings.init_data_max_age_seconds,
            )
        except ValueError:
            pass
        else:
            return f"tg:{user.user_id}"
    return get_remote_address(request)


limiter = Limiter(key_func=rate_limit_key, default_limits=[DEFAULT_RATE_LIMIT])


def application_limit_per_minute(settings: Settings | None = None) -> int:
    """Requests per minute one key may send to /api/ across all routes.

    ``API_RATE_LIMIT_PER_MINUTE`` wins when set. Otherwise the budget follows the shared Kufar
    pacer: it sends at most ``60 / KUFAR_REQUEST_DELAY`` pages a minute for all users together,
    and one key gets half of that in requests (60 at the default 0.5 s delay), clamped to
    [MIN_APPLICATION_LIMIT_PER_MINUTE, MAX_APPLICATION_LIMIT_PER_MINUTE]. A Mini App search
    sends about five requests, so a user still gets about a dozen searches a minute.
    """
    settings = settings or get_settings()
    if settings.api_rate_limit_per_minute > 0:
        return settings.api_rate_limit_per_minute
    delay = settings.kufar_request_delay
    if delay <= 0:
        return MAX_APPLICATION_LIMIT_PER_MINUTE
    pacer_pages_per_minute = 60.0 / delay
    return max(
        MIN_APPLICATION_LIMIT_PER_MINUTE,
        min(MAX_APPLICATION_LIMIT_PER_MINUTE, int(pacer_pages_per_minute // 2)),
    )


@lru_cache(maxsize=8)
def _application_limit(per_minute: int) -> Limit:
    group = LimitGroup(
        f"{per_minute}/minute",
        rate_limit_key,
        APPLICATION_LIMIT_SCOPE,
        False,
        None,
        None,
        None,
        1,
        False,
    )
    return next(iter(group))


class ApplicationRateLimitMiddleware:
    """Charge every /api/ request against one per-key budget shared by all routes.

    Uses the storage of the shared ``limiter``, so ``limiter.reset()`` clears it as well.
    A storage failure lets the request through: the per-route limits still apply.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] != "http"
            or not scope["path"].startswith(APPLICATION_LIMIT_PATH_PREFIX)
            or not limiter.enabled
        ):
            await self.app(scope, receive, send)
            return

        request = Request(scope)
        limit = _application_limit(application_limit_per_minute())
        try:
            allowed = limiter.limiter.hit(
                limit.limit, rate_limit_key(request), APPLICATION_LIMIT_SCOPE
            )
        except Exception:
            logger.warning("Application rate limit check failed", exc_info=True)
            allowed = True
        if not allowed:
            logger.info("Application rate limit %s exceeded on %s", limit.limit, scope["path"])
            response = rate_limit_exceeded_handler(request, RateLimitExceeded(limit))
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)


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
