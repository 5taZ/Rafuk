"""Health endpoint tests."""

from __future__ import annotations

import asyncio
from time import monotonic

import pytest
from fastapi.testclient import TestClient

from api.dependencies import get_session_factory_dependency
from api.main import create_app
from api.routers import health as health_router


@pytest.fixture
def client():
    app = create_app()
    with TestClient(app) as c:
        yield c


def test_health_check_returns_ok(client) -> None:
    resp = client.get("/api/v1/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] in ("healthy", "degraded")
    assert "database" in data
    assert "rate_limiter" in data
    assert data["rate_limiter"] in ("ok", "degraded")


def test_health_check_reads_live_rate_limiter_degradation(monkeypatch) -> None:
    from api import limiter as limiter_mod

    monkeypatch.setattr(limiter_mod, "rate_limiter_degraded", True)
    app = create_app()
    with TestClient(app) as c:
        resp = c.get("/api/v1/health")

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "degraded"
    assert data["rate_limiter"] == "degraded"


def test_readiness_check_returns_200(client) -> None:
    # TEST-M2: previously asserted ``status_code in (200, 503)`` to
    # paper over CI not having Redis. The lifespan in api/main.py
    # already handles that — when ``RedisCache.from_url(...).ping()``
    # fails it falls back to ``MemoryCache`` whose ``ping()`` always
    # returns True. Combined with SQLite ``SELECT 1`` succeeding for
    # tests, readiness MUST be 200; a 503 would be a real regression
    # we want to catch, not silently accept.
    resp = client.get("/api/v1/health/ready")
    assert resp.status_code == 200


def test_health_db_probe_times_out(monkeypatch) -> None:
    class SlowSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def execute(self, _stmt):
            await asyncio.sleep(60)

    app = create_app()
    app.dependency_overrides[get_session_factory_dependency] = lambda: lambda: SlowSession()
    monkeypatch.setattr(health_router, "_DB_TIMEOUT_SECONDS", 0.01)

    with TestClient(app) as c:
        start = monotonic()
        resp = c.get("/api/v1/health")
        elapsed = monotonic() - start

    assert elapsed < 1
    assert resp.status_code == 200
    assert resp.json()["database"] == "unavailable"
    assert resp.json()["status"] == "degraded"


def test_readiness_db_probe_times_out(monkeypatch) -> None:
    class SlowSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def execute(self, _stmt):
            await asyncio.sleep(60)

    app = create_app()
    app.dependency_overrides[get_session_factory_dependency] = lambda: lambda: SlowSession()
    monkeypatch.setattr(health_router, "_DB_TIMEOUT_SECONDS", 0.01)

    with TestClient(app) as c:
        start = monotonic()
        resp = c.get("/api/v1/health/ready")
        elapsed = monotonic() - start

    assert elapsed < 1
    assert resp.status_code == 503
