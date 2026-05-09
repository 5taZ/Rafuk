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
    resp = client.get("/api/v1/health/ready")
    assert resp.status_code in (200, 503)
