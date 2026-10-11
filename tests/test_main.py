from __future__ import annotations

from datetime import UTC, datetime

import pytest
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


@pytest.mark.parametrize(
    ("url", "origin"),
    [
        ("https://app.example.com:443/", "https://app.example.com"),
        ("http://app.example.com:80/app", "http://app.example.com"),
        ("https://app.example.com:8443/app", "https://app.example.com:8443"),
        ("http://app.example.com:443/", "http://app.example.com:443"),
        ("HTTPS://App.Example.com/", "https://app.example.com"),
        ("http://[::1]:8081/app", "http://[::1]:8081"),
        ("https://[2001:DB8::1]/", "https://[2001:db8::1]"),
        ("https://[2001:db8::1]:443/", "https://[2001:db8::1]"),
        ("https://user:secret@app.example.com/", "https://app.example.com"),
        ("https://app.example.com:99999/", None),
        ("ftp://app.example.com/", None),
    ],
)
def test_normalize_origin_matches_browser_origin(url: str, origin: str | None) -> None:
    # Browsers omit the scheme's default port and keep IPv6 hosts in brackets (r3-core-5).
    from api.main import normalize_origin

    assert normalize_origin(url) == origin


def test_cors_allows_origin_configured_with_default_port(monkeypatch: pytest.MonkeyPatch) -> None:
    from api.config import get_settings
    from api.main import create_app

    monkeypatch.setenv("MINI_APP_URL", "https://app.example.com:443/")
    get_settings.cache_clear()
    response = TestClient(create_app()).options(
        "/api/v1/listings",
        headers={"Origin": "https://app.example.com", "Access-Control-Request-Method": "GET"},
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "https://app.example.com"


@pytest.mark.parametrize(
    "query_string",
    [
        "query=iphone%00",
        "query=iphone&sort=newest%00",
        "query=iphone&bad%00name=1",
    ],
)
def test_nul_in_query_parameters_is_rejected_with_422(query_string: str) -> None:
    # Any query string, not only the validated search query, is rejected (r3-core-3).
    from api.main import create_app

    response = TestClient(create_app()).get(f"/api/v1/listings?{query_string}")
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail[0]["loc"][0] == "query"
    assert "NUL" in detail[0]["msg"]


def test_nul_in_compare_queries_is_rejected_with_422() -> None:
    from api.main import create_app

    response = TestClient(create_app()).get(
        "/api/v1/compare?base_query=iphone&compare_query=pixel%00"
    )
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["query", "compare_query"]


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
