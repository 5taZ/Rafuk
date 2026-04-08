from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from api.config import get_settings
from api.database import get_engine, get_session_factory
from api.routers import (
    compare,
    contacts,
    currency,
    expenses,
    export,
    geography,
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

# Rate limiter setup
limiter = Limiter(key_func=get_remote_address)


async def rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded):
    """Handle rate limit exceeded."""
    from fastapi.responses import JSONResponse

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

    try:
        yield
    finally:
        await currency_service.aclose()
        await engine.dispose()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="Rafuks API", lifespan=lifespan)

    # Add CORS middleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.mini_app_url, settings.api_base_url],
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["*"],
    )

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
    app.include_router(risks.router, prefix="/api/v1")
    app.include_router(export.router, prefix="/api/v1")
    return app


app = create_app()
