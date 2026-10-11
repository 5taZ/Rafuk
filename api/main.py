from __future__ import annotations

import logging
import math
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from api.config import get_settings
from api.database import get_engine, get_session_factory
from api.rate_limit import (
    ApplicationRateLimitMiddleware,
    limiter,
    rate_limit_exceeded_handler,
)
from api.routers import (
    compare,
    currency,
    geography,
    listing_detail,
    listings,
    price_history,
    price_stats,
    saved_searches,
    segments,
    trackers,
    workflow,
)
from api.services.cache import MemoryCache, RedisCache
from api.services.currency_service import CurrencyService
from api.services.kufar_client import KufarAPIError, KufarBusyError
from api.services.query_pipeline import DatasetCache, configure_dataset_cache
from api.validators import reject_nul_in_request

logger = logging.getLogger(__name__)

KUFAR_UNAVAILABLE_DETAIL = "Kufar временно недоступен, попробуйте позже."
KUFAR_BUSY_DETAIL = "Сервис сейчас перегружен запросами к Kufar. Попробуйте через минуту."
KUFAR_BUSY_RETRY_AFTER_SECONDS = 30
# Datasets can hold thousands of ads, so the API keeps only a handful in memory.
API_DATASET_CACHE_ENTRIES = 16


@limiter.exempt
async def healthz() -> dict[str, str]:
    """Liveness probe for compose and the local scripts: no database, cache or Kufar access."""
    return {"status": "ok"}


async def kufar_api_error_handler(request: Request, exc: KufarAPIError) -> JSONResponse:
    logger.warning("Kufar API error on %s: %s", request.url.path, exc)
    return JSONResponse(status_code=502, content={"detail": KUFAR_UNAVAILABLE_DETAIL})


async def kufar_busy_error_handler(request: Request, exc: KufarBusyError) -> JSONResponse:
    # Our own queue to Kufar is full, Kufar itself is fine: 503 with a hint when to retry.
    logger.warning("Kufar request queue full on %s: %s", request.url.path, exc)
    return JSONResponse(
        status_code=503,
        content={"detail": KUFAR_BUSY_DETAIL},
        headers={"Retry-After": str(KUFAR_BUSY_RETRY_AFTER_SECONDS)},
    )


def _json_safe(value: Any) -> Any:
    """Stringify NaN/Infinity echoed back as rejected input so the 422 body stays valid JSON."""
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    return value


async def request_validation_error_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    del request
    return JSONResponse(
        status_code=422,
        content={"detail": _json_safe(jsonable_encoder(exc.errors()))},
    )


DEFAULT_PORTS = {"http": 80, "https": 443}


def normalize_origin(url: str | None) -> str | None:
    """Reduce a configured URL to the Origin a browser sends: scheme://host[:port], no path.

    Like browsers, it drops the scheme's default port and keeps IPv6 hosts in brackets.
    """
    if not url:
        return None
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower()
    if scheme not in DEFAULT_PORTS or not parts.hostname:
        return None
    try:
        port = parts.port
    except ValueError:
        return None
    host = parts.hostname
    if ":" in host:
        host = f"[{host}]"
    origin = f"{scheme}://{host}"
    if port is not None and port != DEFAULT_PORTS[scheme]:
        origin += f":{port}"
    return origin


def build_cors_origins(*urls: str | None) -> list[str]:
    origins: list[str] = []
    for url in urls:
        origin = normalize_origin(url)
        if origin and origin not in origins:
            origins.append(origin)
    return origins


async def _close_cache(cache: object) -> None:
    # Cache backends gain aclose() in a separate change; tolerate backends without it.
    aclose = getattr(cache, "aclose", None)
    if aclose is None:
        return
    try:
        await aclose()
    except Exception:
        logger.warning("Failed to close cache backend", exc_info=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    engine = get_engine(settings.database_url)
    session_factory = get_session_factory(engine)
    cache = RedisCache.from_url(settings.redis_url)
    if not await cache.ping():
        await _close_cache(cache)
        cache = MemoryCache()
    currency_service = CurrencyService(cache)

    app.state.engine = engine
    app.state.session_factory = session_factory
    app.state.cache = cache
    app.state.currency_service = currency_service
    # One Kufar pagination per query serves every market endpoint of a search.
    configure_dataset_cache(DatasetCache(max_entries=API_DATASET_CACHE_ENTRIES))

    try:
        yield
    finally:
        configure_dataset_cache(None)
        await currency_service.aclose()
        await _close_cache(cache)
        await engine.dispose()


def create_app() -> FastAPI:
    settings = get_settings()
    docs_enabled = settings.api_docs_enabled
    app = FastAPI(
        title="Rafuks API",
        lifespan=lifespan,
        docs_url="/docs" if docs_enabled else None,
        redoc_url="/redoc" if docs_enabled else None,
        openapi_url="/openapi.json" if docs_enabled else None,
        # NUL in a query or path parameter is a 422, not a Postgres error (bodies: schemas).
        dependencies=[Depends(reject_nul_in_request)],
    )

    # One shared limiter: default limits for every route via the middleware,
    # stricter per-route limits via @limiter.limit (see api/rate_limit.py).
    app.state.limiter = limiter
    app.add_middleware(SlowAPIMiddleware)
    # Outside SlowAPIMiddleware: one per-key budget for all /api/ routes, decorated ones too.
    app.add_middleware(ApplicationRateLimitMiddleware)
    app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)
    app.add_exception_handler(KufarAPIError, kufar_api_error_handler)
    # Starlette picks the handler by the exception's MRO, so the subclass wins.
    app.add_exception_handler(KufarBusyError, kufar_busy_error_handler)
    app.add_exception_handler(RequestValidationError, request_validation_error_handler)

    # Added last so it is the outermost middleware and 429 responses carry CORS headers too.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=build_cors_origins(settings.mini_app_url, settings.api_base_url),
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["*"],
    )

    # Outside /api/v1: nginx does not proxy it, it is for probes next to the process.
    app.add_api_route("/healthz", healthz, methods=["GET"], include_in_schema=False)

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
    return app


app = create_app()
