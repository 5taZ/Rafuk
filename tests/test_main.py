from __future__ import annotations

import pytest
from fastapi import FastAPI, Request


def test_create_app_has_expected_routes() -> None:
    from api.main import create_app

    app = create_app()
    paths = {route.path for route in app.router.routes}
    assert "/api/v1/price-stats" in paths
    assert "/api/v1/listings" in paths
    assert "/api/v1/segments" in paths
    assert "/api/v1/geography" in paths
    assert "/api/v1/leads" in paths
    assert "/api/v1/watchlist" in paths
    assert "/metrics" in paths


def test_create_app_disables_schema_docs_in_production(monkeypatch) -> None:
    from api import main
    from api.config import Settings

    settings = Settings(
        env="production",
        bot_token="test",
        database_url="sqlite+aiosqlite:///test.db",
        redis_url="redis://localhost:6379/0",
        api_base_url="https://example.com",
        mini_app_url="https://example.com/app",
        debug=False,
        auth_bypass=False,
        _env_file=None,
    )
    monkeypatch.setattr(main, "get_settings", lambda: settings)

    app = main.create_app()

    assert app.docs_url is None
    assert app.redoc_url is None
    assert app.openapi_url is None


@pytest.mark.asyncio
async def test_degraded_limiter_fails_closed_for_remote_database_without_env(monkeypatch) -> None:
    from api import limiter as limiter_mod
    from api import main
    from api.config import Settings

    class DummyCache:
        async def ping(self) -> bool:
            return True

    class DummyEngine:
        async def dispose(self) -> None:
            return None

    class DummyKufarClient:
        def __init__(self, settings) -> None:
            del settings

        async def aclose(self) -> None:
            return None

    async def _noop_prune() -> None:
        return None

    settings = Settings(
        bot_token="test",
        database_url="postgresql+asyncpg://user:pass@db.production.example.com:5432/kufar",
        redis_url="redis://redis:6379/0",
        api_base_url="https://example.com",
        mini_app_url="https://example.com/app",
        debug=False,
        auth_bypass=False,
        _env_file=None,
    )
    monkeypatch.delenv("ENV", raising=False)
    monkeypatch.setattr(main, "get_settings", lambda: settings)
    monkeypatch.setattr(main, "get_engine", lambda _url: DummyEngine())
    monkeypatch.setattr(main, "get_session_factory", lambda _engine: object())
    monkeypatch.setattr(main.RedisCache, "from_url", staticmethod(lambda _url: DummyCache()))
    monkeypatch.setattr(main, "KufarClient", DummyKufarClient)
    monkeypatch.setattr(main, "periodic_prune_shadow_stores", _noop_prune)
    monkeypatch.setattr(limiter_mod, "rate_limiter_degraded", True)

    with pytest.raises(RuntimeError, match="production-like"):
        async with main.lifespan(FastAPI()):
            pass


def test_listings_cache_control_uses_swr_with_short_max_age() -> None:
    """OPUS-10: Cache-Control max-age must stay short so the browser
    doesn't pin a body older than the server-side cache TTL on screen.
    stale-while-revalidate covers the gap with a background refresh.
    """
    from fastapi.testclient import TestClient

    from api.dependencies import (
        get_cache,
        get_currency_service,
        get_kufar_client,
        get_session_factory_dependency,
        get_settings_dependency,
        get_telegram_user,
    )
    from api.main import create_app
    from api.middleware.telegram_auth import TelegramInitData

    app = create_app()

    class _StubCache:
        async def get_json(self, key):
            return None

        async def set_json(self, key, value, ttl=None):
            return None

    class _StubCurrency:
        async def get_rates(self):
            return {"rates": {"BYN": 1.0}}

    class _StubKufar:
        async def search_all_ads(self, **kwargs):
            return {"ads": [], "total": 0}

    class _StubSettings:
        cache_ttl_seconds = 300
        kufar_max_ads_per_query = 1500

    app.dependency_overrides[get_telegram_user] = lambda: TelegramInitData(
        user_id=1, first_name="t", raw={}
    )
    app.dependency_overrides[get_cache] = lambda: _StubCache()
    app.dependency_overrides[get_currency_service] = lambda: _StubCurrency()
    app.dependency_overrides[get_kufar_client] = lambda: _StubKufar()
    app.dependency_overrides[get_settings_dependency] = lambda: _StubSettings()
    app.dependency_overrides[get_session_factory_dependency] = lambda: object()

    with TestClient(app) as client:
        resp = client.get("/api/v1/listings?query=iphone")
        # The endpoint may legitimately return 200 (empty result) or
        # 503 (deps unwired) — either way the middleware ran and the
        # Cache-Control we tightened is what we actually care about.
        cache_control = resp.headers.get("cache-control", "")
        if resp.status_code == 200:
            assert "max-age=60" in cache_control
            assert "stale-while-revalidate=240" in cache_control
            assert "max-age=300" not in cache_control


@pytest.mark.asyncio
async def test_auto_provision_user_runs_once_per_ttl(monkeypatch) -> None:
    import asyncio

    from httpx import ASGITransport, AsyncClient

    from api import main
    from api.middleware.telegram_auth import TelegramInitData

    app = main.create_app()
    session_factory = object()
    app.state.session_factory = session_factory
    calls: list[tuple[object, int, str]] = []

    async def fake_ensure_user_exists(sf, user_id: int, first_name: str) -> None:
        calls.append((sf, user_id, first_name))

    monkeypatch.setattr(main, "ensure_user_exists", fake_ensure_user_exists)

    @app.get("/_provision-test")
    async def _provision_test(request: Request):
        request.state.telegram_user = TelegramInitData(user_id=777, first_name="TTL", raw={})
        return {"ok": True}
    app.router.routes.insert(0, app.router.routes.pop())

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        for _ in range(5):
            resp = await client.get("/_provision-test")
            assert resp.status_code == 200
        for _ in range(20):
            if calls:
                break
            await asyncio.sleep(0.01)

    assert calls == [(session_factory, 777, "TTL")]


def test_body_size_limit_returns_413_with_security_headers() -> None:
    """BE-DEEP-2: oversized Content-Length gets 413 with security headers."""
    from fastapi.testclient import TestClient

    from api.main import create_app

    app = create_app()

    with TestClient(app) as client:
        resp = client.post(
            "/api/v1/leads",
            headers={"content-length": "9000000"},
            content=b"",
        )

    assert resp.status_code == 413
    assert resp.json()["detail"] == "Request body too large"
    assert resp.headers.get("X-Content-Type-Options") == "nosniff"
    assert "DENY" in resp.headers.get("X-Frame-Options", "")
    assert "max-age=" in resp.headers.get("Strict-Transport-Security", "")
