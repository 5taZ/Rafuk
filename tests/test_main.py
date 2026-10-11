from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient


class FakeCurrencyService:
    async def get_rates(self) -> dict[str, object]:
        return {
            "base": "BYN",
            "rates": {"USD": 3.2, "EUR": 3.5},
            "source": "test",
            "fetched_at": datetime.now(UTC).isoformat(),
        }


def test_create_app_has_expected_routes() -> None:
    from api.main import create_app

    app = create_app()
    paths = {route.path for route in app.router.routes}
    assert "/api/v1/price-stats" in paths
    assert "/api/v1/listings" in paths
    assert "/api/v1/segments" in paths
    assert "/api/v1/geography" in paths
    assert "/api/v1/currency-rates" in paths
    assert "/api/v1/compare" in paths
    assert "/api/v1/saved-searches" in paths
    assert "/api/v1/opportunity-board" in paths
    assert "/api/v1/leads" in paths
    assert "/api/v1/watchlist" in paths


def test_currency_endpoint_smoke() -> None:
    from api.dependencies import get_currency_service
    from api.main import create_app

    app = create_app()
    app.dependency_overrides[get_currency_service] = lambda: FakeCurrencyService()
    with TestClient(app) as client:
        response = client.get("/api/v1/currency-rates")
    assert response.status_code == 200
    assert response.json()["base"] == "BYN"


def test_kufar_api_error_maps_to_502_with_russian_detail() -> None:
    from api.main import create_app
    from api.services.kufar_client import KufarAPIError

    app = create_app()

    @app.get("/kufar-down")
    async def kufar_down() -> None:
        raise KufarAPIError("Kufar API request failed after 3 attempts")

    response = TestClient(app).get("/kufar-down")
    assert response.status_code == 502
    assert response.json() == {"detail": "Kufar временно недоступен, попробуйте позже."}


def test_cors_origins_are_normalized_without_path() -> None:
    from api.main import build_cors_origins, normalize_origin

    assert normalize_origin("https://Example.com/app?x=1") == "https://example.com"
    assert normalize_origin("http://localhost:8080/app/") == "http://localhost:8080"
    assert normalize_origin("not a url") is None
    assert normalize_origin("") is None
    assert build_cors_origins(
        "https://kufar-analytics.example.com/app",
        "https://kufar-analytics.example.com",
        None,
    ) == ["https://kufar-analytics.example.com"]


def test_cors_preflight_accepts_mini_app_origin() -> None:
    from api.main import create_app

    app = create_app()
    response = TestClient(app).options(
        "/api/v1/listings",
        headers={
            "Origin": "https://kufar-analytics.example.com",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "https://kufar-analytics.example.com"


class ClosableCache:
    def __init__(self) -> None:
        self.closed = 0

    async def ping(self) -> bool:
        return True

    async def get_json(self, key: str) -> None:
        del key
        return None

    async def set_json(self, key: str, value: object, ttl: int | None = None) -> None:
        del key, value, ttl

    async def aclose(self) -> None:
        self.closed += 1


def test_lifespan_closes_cache_backend(monkeypatch) -> None:
    from api import main

    cache = ClosableCache()
    monkeypatch.setattr(main.RedisCache, "from_url", classmethod(lambda cls, url: cache))
    app = main.create_app()
    with TestClient(app):
        assert app.state.cache is cache
        assert cache.closed == 0
    assert cache.closed == 1


def test_lifespan_closes_unreachable_redis_and_falls_back(monkeypatch) -> None:
    from api import main

    class DeadCache(ClosableCache):
        async def ping(self) -> bool:
            return False

    dead = DeadCache()
    monkeypatch.setattr(main.RedisCache, "from_url", classmethod(lambda cls, url: dead))
    app = main.create_app()
    with TestClient(app):
        assert isinstance(app.state.cache, main.MemoryCache)
        assert dead.closed == 1
    assert dead.closed == 1


def test_lifespan_tolerates_cache_without_aclose(monkeypatch) -> None:
    from api import main

    class LegacyCache:
        async def ping(self) -> bool:
            return True

    monkeypatch.setattr(main.RedisCache, "from_url", classmethod(lambda cls, url: LegacyCache()))
    app = main.create_app()
    with TestClient(app):
        pass


def test_database_has_no_dead_session_helper() -> None:
    import api.database

    assert not hasattr(api.database, "get_db_session")
