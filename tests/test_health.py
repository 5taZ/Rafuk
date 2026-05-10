"""Health endpoint tests."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from api.main import create_app


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
