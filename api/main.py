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

    # Start background prune task for AI shadow stores
    prune_task = asyncio.create_task(periodic_prune_shadow_stores())

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
        allow_headers=["X-Telegram-Init-Data", "Content-Type", "Accept"],
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

    # Security headers middleware
    @app.middleware("http")
    async def add_security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        # X-Frame-Options: DENY for API responses (frontend uses SAMEORIGIN via nginx)
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
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
        if request.method in ("POST", "PATCH", "DELETE"):
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
        return await call_next(request)

    # Auto-provision User row on first authenticated request
    @app.middleware("http")
    async def auto_provision_user(request: Request, call_next):
        response = await call_next(request)
        # After the request completes (so we don't delay the response),
        # ensure the Telegram user exists in the DB to prevent FK violations.
        init_data = getattr(request.state, "telegram_user", None)
        if init_data is not None and init_data.user_id != 0:
            sf = getattr(request.app.state, "session_factory", None)
            if sf is not None:
                try:
                    await ensure_user_exists(sf, init_data.user_id, init_data.first_name)
                except Exception as e:
                    logger.warning("Auto-provision failed for telegram_user_id=%s: %s", init_data.user_id, e, exc_info=True)
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
