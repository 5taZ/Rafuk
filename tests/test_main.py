from __future__ import annotations

import pytest
from fastapi import FastAPI


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
