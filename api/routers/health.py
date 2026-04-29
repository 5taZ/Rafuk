"""Health check endpoint for infrastructure monitoring."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.dependencies import get_session_factory_dependency
from api.limiter import rate_limiter_degraded

router = APIRouter()


@router.get("/health", status_code=status.HTTP_200_OK)
async def health_check(
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory_dependency),
) -> dict:
    """Check database connectivity and return service status."""
    checks: dict[str, str] = {"status": "healthy"}

    # Database health check
    try:
        async with session_factory() as session:
            await session.execute(text("SELECT 1"))
            checks["database"] = "ok"
    except Exception:
        checks["database"] = "unavailable"
        checks["status"] = "degraded"

    # Rate limiter status
    if rate_limiter_degraded:
        checks["rate_limiter"] = "degraded"
        checks["status"] = "degraded"
    else:
        checks["rate_limiter"] = "ok"

    return checks


@router.get("/health/ready")
async def readiness_check(
    session_factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory_dependency),
) -> Response:
    """Readiness probe — returns 200 only if DB is reachable."""
    try:
        async with session_factory() as session:
            await session.execute(text("SELECT 1"))
        return Response(status_code=status.HTTP_200_OK)
    except Exception:
        return Response(status_code=status.HTTP_503_SERVICE_UNAVAILABLE)
