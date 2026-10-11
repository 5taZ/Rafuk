"""GET /healthz and the api_docs_enabled setting (L3)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from api.config import Settings, get_settings
from api.rate_limit import DEFAULT_RATE_LIMIT, limiter


@pytest.fixture(autouse=True)
def reset_limiter():
    limiter.reset()
    yield
    limiter.reset()


def test_healthz_needs_no_lifespan_or_database() -> None:
    from api.main import create_app

    # No "with": the lifespan never runs, so there is no engine, cache or currency service.
    response = TestClient(create_app()).get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_healthz_is_outside_api_prefix_and_schema() -> None:
    from api.main import create_app

    app = create_app()
    paths = {route.path for route in app.router.routes}
    assert "/healthz" in paths
    assert "/api/v1/healthz" not in paths
    assert "/healthz" not in app.openapi()["paths"]


def test_healthz_is_exempt_from_rate_limit() -> None:
    from api.main import create_app

    client = TestClient(create_app())
    limit = int(DEFAULT_RATE_LIMIT.split("/", 1)[0])
    statuses = {client.get("/healthz").status_code for _ in range(limit + 5)}
    assert statuses == {200}


def test_docs_enabled_by_default() -> None:
    from api.main import create_app

    assert Settings(_env_file=None).api_docs_enabled is True
    client = TestClient(create_app())
    assert client.get("/docs").status_code == 200
    assert client.get("/redoc").status_code == 200
    assert client.get("/openapi.json").status_code == 200


@pytest.mark.parametrize("value", ["false", "0", "False"])
def test_docs_can_be_disabled(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    from api.main import create_app

    monkeypatch.setenv("API_DOCS_ENABLED", value)
    get_settings.cache_clear()
    assert get_settings().api_docs_enabled is False

    client = TestClient(create_app())
    for path in ("/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"):
        assert client.get(path).status_code == 404, path
    assert client.get("/healthz").json() == {"status": "ok"}
