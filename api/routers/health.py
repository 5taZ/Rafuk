"""Health check endpoint for infrastructure monitoring."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api import limiter as limiter_mod
from api.dependencies import get_session_factory_dependency

router = APIRouter()
_DB_TIMEOUT_SECONDS = 2.0


@router.get("/health", status_code=status.HTTP_200_OK)
async def health_check(
    request: Request,
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory_dependency),
) -> dict:
    """Check database connectivity and return service status."""
    if (
        not hasattr(request.app.state, "session_factory")
        or request.app.state.session_factory is None
    ):
        return {"status": "unhealthy", "database": "not initialized"}

    checks: dict[str, str] = {"status": "healthy"}

    # Database health check
    try:
        async with session_factory() as session:
            await asyncio.wait_for(
                session.execute(text("SELECT 1")),
                timeout=_DB_TIMEOUT_SECONDS,
            )
            checks["database"] = "ok"
    except Exception:
        checks["database"] = "unavailable"
        checks["status"] = "degraded"

    # Rate limiter status
    if limiter_mod.rate_limiter_degraded:
        checks["rate_limiter"] = "degraded"
        checks["status"] = "degraded"
    else:
        checks["rate_limiter"] = "ok"

    return checks


@router.get("/health/ready")
async def readiness_check(
    request: Request,
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory_dependency),
) -> Response:
    """Readiness probe — returns 200 only if DB and Redis are reachable."""
    try:
        async with session_factory() as session:
            await asyncio.wait_for(
                session.execute(text("SELECT 1")),
                timeout=_DB_TIMEOUT_SECONDS,
            )
    except Exception:
        return Response(status_code=status.HTTP_503_SERVICE_UNAVAILABLE)

    cache = getattr(request.app.state, "cache", None)
    if cache is not None:
        try:
            if not await cache.ping():
                return Response(status_code=status.HTTP_503_SERVICE_UNAVAILABLE)
        except Exception:
            return Response(status_code=status.HTTP_503_SERVICE_UNAVAILABLE)

    return Response(status_code=status.HTTP_200_OK)
