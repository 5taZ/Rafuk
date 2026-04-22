from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import logging

from slowapi.errors import RateLimitExceeded

logger = logging.getLogger(__name__)

from api.config import get_settings
from api.database import get_engine, get_session_factory
from api.limiter import limiter
from api.routers import (
    ai_analysis,
    compare,
    contacts,
    currency,
    expenses,
    export,
    geography,
    health,
    listing_detail,
    listings,
    price_history,
    price_stats,
    risks,
    saved_searches,
    segments,
    trackers,
    workflow,
)
from api.services.cache import MemoryCache, RedisCache
from api.services.currency_service import CurrencyService


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

    try:
        yield
    finally:
        # Close AI service httpx client
        from api.services.ai_service import get_ai_service
        ai = get_ai_service()
        if ai and hasattr(ai, "_httpx_client") and ai._httpx_client:
            await ai._httpx_client.aclose()
        # Close Redis connection pool
        if isinstance(cache, RedisCache):
            await cache.aclose()
        await currency_service.aclose()
        await engine.dispose()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="Rafuks API", lifespan=lifespan)

    # Add CORS middleware
    origins = [settings.mini_app_url, settings.api_base_url]
    if settings.debug:
        origins.extend([
            "http://localhost:8081",
            "http://127.0.0.1:8081",
            "http://localhost:8010",
            "http://127.0.0.1:8010",
        ])
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["X-Telegram-Init-Data", "Content-Type", "Accept"],
    )

    # Global exception handler — ensures JSON responses for ALL errors
    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception):
        logger.error(
            "Unhandled exception on %s %s: [%s] %s",
            request.method, request.url.path,
            type(exc).__name__, exc,
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
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
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
    app.include_router(currency.router, prefix="/api/v1")
    app.include_router(compare.router, prefix="/api/v1")
    app.include_router(listing_detail.router, prefix="/api/v1")
    app.include_router(trackers.router, prefix="/api/v1")
    app.include_router(saved_searches.router, prefix="/api/v1")
    app.include_router(workflow.router, prefix="/api/v1")
    app.include_router(contacts.router, prefix="/api/v1")
    app.include_router(expenses.router, prefix="/api/v1")
    app.include_router(health.router, prefix="/api/v1")
    app.include_router(risks.router, prefix="/api/v1")
    app.include_router(export.router, prefix="/api/v1")
    app.include_router(ai_analysis.router, prefix="/api/v1")
    return app


app = create_app()
