from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi.errors import RateLimitExceeded

from api.config import get_settings
from api.database import get_engine, get_session_factory
from api.dependencies import ensure_user_exists
from api.limiter import limiter
from api.logging_config import configure_logging, request_id_ctxvar
from api.routers import (
    ai_analysis,
    ai_listing_assistant,
    ai_tools,
    analytics,
    consent,
    expenses,
    export,
    geography,
    health,
    image_proxy,
    listing_detail,
    listings,
    price_history,
    price_stats,
    reminders,
    segments,
    trackers,
    workflow,
)
from api.routers.ai_analysis import periodic_prune_shadow_stores
from api.services.ai_service import get_ai_service
from api.services.cache import MemoryCache, RedisCache
from api.services.currency_service import CurrencyService
from api.services.kufar_client import KufarClient

logger = logging.getLogger(__name__)


async def rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded):
    """Handle rate limit exceeded."""
    return JSONResponse(
        status_code=429,
        content={
            "detail": "Rate limit exceeded. Please try again later.",
            "limit": str(exc.detail),
        },
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    engine = get_engine(settings.database_url)
    session_factory = get_session_factory(engine)
    cache = RedisCache.from_url(settings.redis_url)
    if not await cache.ping():
        cache = MemoryCache()
    currency_service = CurrencyService(cache)

    app.state.engine = engine
    app.state.session_factory = session_factory
    app.state.cache = cache
    app.state.currency_service = currency_service
    app.state.settings = settings
    app.state.kufar_client = KufarClient(settings)

    # Start background prune task for AI shadow stores. Wrap a
    # done-callback that surfaces any uncaught exception — without it
    # `asyncio.create_task` swallows errors silently and the next
    # event-loop iteration's GC may never get round to flagging the
    # task; we'd then run with the prune task quietly dead and shadow
    # stores growing forever (PERF-H3).
    def _log_bg_task_exception(task: asyncio.Task[None]) -> None:
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.error(
                "background task %s raised: %r",
                task.get_name() or "<unnamed>",
                exc,
                exc_info=exc,
            )

    prune_task = asyncio.create_task(
        periodic_prune_shadow_stores(), name="periodic_prune_shadow_stores",
    )
    prune_task.add_done_callback(_log_bg_task_exception)

    # Loud warning at startup if auth is bypassed — this used to be tied
    # to `debug` and could silently turn into a production foot-gun.
    if settings.auth_bypass:
        logger.warning(
            "⚠ auth_bypass=True is set — unauthenticated requests are "
            "treated as user_id=0. This MUST be disabled in any "
            "non-local-development environment.",
        )
    if settings.debug:
        logger.warning("Debug mode is active (extra dev origins, verbose logs)")

    # Rate limiter sanity check: if Redis was unreachable at import time
    # the limiter silently fell back to in-memory storage, which makes
    # per-user limits effectively useless with multiple workers. Refuse
    # to start in production (fail-closed) and warn loudly in dev.
    from api.limiter import rate_limiter_degraded  # noqa: PLC0415 — read latest value

    if rate_limiter_degraded:
        if os.environ.get("ENV") == "production":
            raise RuntimeError(
                "Rate limiter degraded (Redis unreachable) and ENV=production. "
                "Refusing to start — running with in-memory limits across N "
                "workers means abuse protection is effectively off. Fix "
                "Redis connectivity and retry."
            )
        logger.warning(
            "⚠ Rate limiter is in DEGRADED mode (in-memory fallback). "
            "Limits are per-process, not shared across workers. "
            "This is only acceptable for local development.",
        )

    try:
        yield
    finally:
        # Cancel background prune task
        prune_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await prune_task
        # Close AI service httpx client
        ai = get_ai_service()
        if ai is not None:
            await ai.close()
        # Close shared KufarClient
        await app.state.kufar_client.aclose()
        # Drain the image-proxy keep-alive pool — long-lived since
        # 200-card list views fan out hundreds of thumbnail requests
        # per session, so we cache the connection across requests.
        await image_proxy.aclose_http_client()
        # Close Redis connection pool
        if isinstance(cache, RedisCache):
            await cache.aclose()
        await currency_service.aclose()
        await engine.dispose()


def create_app() -> FastAPI:
    settings = get_settings()
    # INF-H9: configure structured logging once per process, as
    # early as possible so any import-time warnings we log land in
    # the right formatter.
    configure_logging(service="api")
    app = FastAPI(title="Rafuk API", lifespan=lifespan)

    # Add CORS middleware
    #
    # NOTE: "null" origin is NOT allowed. A "null" Origin header is what
    # browsers send from sandboxed iframes, file:// URLs, and data: URIs.
    # Allowing it lets a malicious sandbox embed our API and replay any
    # exfiltrated initData. Telegram Mini Apps run inside web.telegram.org
    # (and webk.telegram.org) — their Origin is non-null. If a future
    # client legitimately needs cross-origin POSTs, add its real origin
    # explicitly rather than reopening "null".
    origins = [
        settings.mini_app_url,
        settings.api_base_url,
        "https://web.telegram.org",
        "https://webk.telegram.org",
    ]
    if settings.debug:
        origins.extend(
            [
                "http://localhost:8081",
                "http://127.0.0.1:8081",
                "http://localhost:8010",
                "http://127.0.0.1:8010",
            ]
        )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=[
            "X-Telegram-Init-Data",
            "Content-Type",
            "Accept",
            # FE-H7: X-Requested-With is a custom header → triggers a
            # preflight. Whitelisting it means legitimate browsers
            # pass the CORS check while forged cross-origin requests
            # still fail at the Origin guard below.
            "X-Requested-With",
        ],
    )

    # Global exception handler — ensures JSON responses for ALL errors
    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception):
        logger.error(
            "Unhandled exception on %s %s: [%s] %s",
            request.method,
            request.url.path,
            type(exc).__name__,
            exc,
            exc_info=True,
        )
        return JSONResponse(
            status_code=500,
            content={"detail": "Внутренняя ошибка сервера. Попробуйте позже."},
        )

    # INF-H9: request-ID middleware. Generate (or accept) a short ID
    # for each request and stash it on a contextvar so every
    # subsequent log line — in this middleware, inside dependencies,
    # or deep in a service layer — automatically carries the same
    # ``request_id`` field. The ID is also echoed back in the
    # ``X-Request-ID`` response header so clients can correlate a
    # bug report with server logs.
    import secrets as _secrets

    @app.middleware("http")
    async def add_request_id(request: Request, call_next):
        incoming = request.headers.get("x-request-id")
        # Only honour IDs that look safe (hex/base36, up to 64 chars).
        # Otherwise we'd let a client poison the logs with arbitrary
        # text including newlines.
        if incoming and len(incoming) <= 64 and incoming.replace("-", "").isalnum():
            req_id = incoming
        else:
            req_id = _secrets.token_hex(8)
        token = request_id_ctxvar.set(req_id)
        try:
            response = await call_next(request)
        finally:
            request_id_ctxvar.reset(token)
        response.headers["X-Request-ID"] = req_id
        return response

    # Security headers middleware
    @app.middleware("http")
    async def add_security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        # X-Frame-Options: DENY for API responses (frontend uses SAMEORIGIN via nginx)
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        # FE-H6 / SEC-LOW: API responses are JSON, no <script>/<style>
        # contexts to lock down — but the modern cross-origin trio
        # still pays off:
        #   * Permissions-Policy clamps device APIs even if a future
        #     route accidentally serves HTML.
        #   * COOP cuts a window opener off from this origin, blocking
        #     a class of XS-leak attacks via window.opener.
        #   * CORP says "this resource isn't shareable cross-origin",
        #     stopping a malicious page from `<img src=...>`-loading
        #     our JSON to probe for side-channels.
        response.headers["Permissions-Policy"] = (
            "geolocation=(), microphone=(), camera=(), payment=(), usb=()"
        )
        response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        response.headers["Cross-Origin-Resource-Policy"] = "same-site"
        return response

    # Cache-Control middleware
    @app.middleware("http")
    async def add_cache_control(request: Request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/api/v1/health"):
            pass
        elif request.method in ("POST", "PATCH", "DELETE"):
            response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        elif request.url.path.startswith(("/api/v1/price-stats", "/api/v1/listings")):
            response.headers["Cache-Control"] = "max-age=300"
        return response

    # CSRF protection: validate Origin header on state-changing requests.
    # Prevents cross-origin POST/PATCH/DELETE from arbitrary websites.
    # Same "null" origin restriction as CORS — see comment above.
    _csrf_allowed = frozenset(origins) | frozenset(
        [
            "https://web.telegram.org",
            "https://webk.telegram.org",
        ]
    )
    if settings.debug:
        _csrf_allowed = _csrf_allowed | frozenset(
            [
                "http://localhost:8081",
                "http://127.0.0.1:8081",
                "http://localhost:8010",
                "http://127.0.0.1:8010",
            ]
        )
    if settings.mini_app_url:
        _csrf_allowed = _csrf_allowed | {settings.mini_app_url}
    if settings.api_base_url:
        _csrf_allowed = _csrf_allowed | {settings.api_base_url}

    @app.middleware("http")
    async def csrf_origin_check(request: Request, call_next):
        if request.method in ("POST", "PATCH", "DELETE", "PUT"):
            origin = request.headers.get("origin")
            if not origin:
                return JSONResponse(
                    status_code=403,
                    content={"detail": "CSRF: origin required"},
                )
            if origin not in _csrf_allowed:
                return JSONResponse(
                    status_code=403,
                    content={"detail": "CSRF: origin not allowed"},
                )
            # FE-H7 (defence in depth): also require the browser-only
            # header X-Requested-With. HTML forms and image/link tags
            # cannot set custom headers, so this means any CSRF payload
            # has to pass a CORS preflight as well. The mini-app sets
            # this header in api_core.js for every mutating request;
            # the Telegram bot goes through its own HTTP client and
            # never hits this middleware from a browser context.
            requested_with = request.headers.get("x-requested-with", "").lower()
            if requested_with != "xmlhttprequest":
                return JSONResponse(
                    status_code=403,
                    content={"detail": "CSRF: X-Requested-With required"},
                )
        return await call_next(request)

    # Auto-provision User row on first authenticated request.
    #
    # PERF-H6: previously the body of this middleware did
    #     response = await call_next(request)
    #     ...do DB work synchronously...
    #     return response
    # That meant every request waited on a DB round-trip AFTER the
    # response was already produced — keeping the connection open and
    # blocking the worker for ~50ms per first-request just to make sure
    # the User row exists. Move the upsert to a fire-and-forget task so
    # the response returns immediately; the upsert itself is
    # idempotent (INSERT … ON CONFLICT DO NOTHING) so it's safe to run
    # without awaiting.
    _provision_tasks: set[asyncio.Task[None]] = set()

    async def _provision_user_async(
        sf: Any, user_id: int, first_name: str,
    ) -> None:
        try:
            await ensure_user_exists(sf, user_id, first_name)
        except Exception:  # noqa: BLE001 — background task, must never raise
            logger.warning(
                "Auto-provision failed for telegram_user_id=%s",
                user_id, exc_info=True,
            )

    @app.middleware("http")
    async def auto_provision_user(request: Request, call_next):
        response = await call_next(request)
        init_data = getattr(request.state, "telegram_user", None)
        if init_data is not None and init_data.user_id != 0:
            sf = getattr(request.app.state, "session_factory", None)
            if sf is not None:
                # Strong-ref the task so the GC doesn't drop it before
                # it runs; self-clean via done callback.
                task = asyncio.create_task(
                    _provision_user_async(sf, init_data.user_id, init_data.first_name),
                    name=f"provision_user_{init_data.user_id}",
                )
                _provision_tasks.add(task)
                task.add_done_callback(_provision_tasks.discard)
        return response

    # Add rate limiter to app state
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)

    # Include routers
    app.include_router(price_stats.router, prefix="/api/v1")
    app.include_router(price_history.router, prefix="/api/v1")
    app.include_router(listings.router, prefix="/api/v1")
    app.include_router(segments.router, prefix="/api/v1")
    app.include_router(geography.router, prefix="/api/v1")
    app.include_router(listing_detail.router, prefix="/api/v1")
    app.include_router(trackers.router, prefix="/api/v1")
    app.include_router(workflow.router, prefix="/api/v1")
    app.include_router(expenses.router, prefix="/api/v1")
    app.include_router(reminders.router, prefix="/api/v1")
    app.include_router(health.router, prefix="/api/v1")
    app.include_router(export.router, prefix="/api/v1")
    app.include_router(ai_analysis.router, prefix="/api/v1")
    app.include_router(ai_listing_assistant.router, prefix="/api/v1")
    app.include_router(ai_tools.router, prefix="/api/v1")
    app.include_router(image_proxy.router, prefix="/api/v1")
    app.include_router(analytics.router, prefix="/api/v1")
    app.include_router(consent.router, prefix="/api/v1")
    return app


app = create_app()
