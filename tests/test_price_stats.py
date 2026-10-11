from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from api.services.cache import MemoryCache


class FakeCurrencyService:
    async def get_rates(self) -> dict[str, object]:
        return {
            "base": "BYN",
            "rates": {"USD": 3.2, "EUR": 3.5},
            "source": "test",
            "fetched_at": datetime.now(UTC).isoformat(),
        }

    def convert_from_byn(self, amount_byn: float, currency: str, rates: dict[str, float]) -> float:
        if currency == "BYN":
            return round(amount_byn, 2)
        return round(amount_byn / rates[currency], 2)


class FakeKufarClient:
    def __init__(self, settings) -> None:
        del settings

    async def search_all_ads(self, **kwargs) -> dict:
        del kwargs
        return {
            "ads": [{"price_byn": 200000}, {"price_byn": 220000}, {"price_byn": 180000}],
            "total": 10,
        }

    async def aclose(self) -> None:
        return None


def test_price_stats_endpoint_returns_payload(monkeypatch) -> None:
    from api.dependencies import get_cache, get_currency_service
    from api.main import create_app
    from api.routers import price_stats

    monkeypatch.setattr(price_stats, "KufarClient", FakeKufarClient)
    app = create_app()
    app.dependency_overrides[get_cache] = lambda: MemoryCache()
    app.dependency_overrides[get_currency_service] = lambda: FakeCurrencyService()
    with TestClient(app) as client:
        response = client.get("/api/v1/price-stats", params={"query": "iphone", "currency": "USD"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["query"] == "iphone"
    assert payload["count"] == 3
    assert payload["total_results"] == 3
    assert payload["analyzed_count"] == 3
    assert payload["currency"] == "USD"


class CountingKufarClient(FakeKufarClient):
    calls: list[dict] = []

    async def search_all_ads(self, **kwargs) -> dict:
        CountingKufarClient.calls.append(kwargs)
        return await super().search_all_ads(**kwargs)


def make_app(monkeypatch, client_cls=FakeKufarClient):
    from api.dependencies import get_cache, get_currency_service
    from api.main import create_app
    from api.routers import price_stats

    monkeypatch.setattr(price_stats, "KufarClient", client_cls)
    app = create_app()
    cache = MemoryCache()
    app.dependency_overrides[get_cache] = lambda: cache
    app.dependency_overrides[get_currency_service] = lambda: FakeCurrencyService()
    return app


def test_price_stats_validates_query_and_currency(monkeypatch) -> None:
    app = make_app(monkeypatch)
    with TestClient(app) as client:
        blank = client.get("/api/v1/price-stats", params={"query": "  "})
        euro = client.get("/api/v1/price-stats", params={"query": "iphone", "currency": "EUR"})
        default = client.get("/api/v1/price-stats", params={"query": "  iphone  "})

    assert blank.status_code == 422
    assert euro.status_code == 422
    assert default.status_code == 200
    assert default.json()["currency"] == "BYN"
    assert default.json()["query"] == "iphone"
    assert default.json()["median"] == 2000.0


def test_price_stats_cache_key_ignores_case_and_spacing(monkeypatch) -> None:
    CountingKufarClient.calls = []
    app = make_app(monkeypatch, CountingKufarClient)
    with TestClient(app) as client:
        first = client.get("/api/v1/price-stats", params={"query": "iPhone 15"})
        second = client.get("/api/v1/price-stats", params={"query": "iphone   15"})
        usd = client.get("/api/v1/price-stats", params={"query": "iphone 15", "currency": "USD"})

    assert first.status_code == second.status_code == usd.status_code == 200
    assert second.json()["query"] == "iphone   15"
    assert usd.json()["median"] == 625.0
    # The USD response is a separate response-cache entry, but the dataset cache serves it
    # without another Kufar pagination.
    assert [call["currency"] for call in CountingKufarClient.calls] == ["BYN"]


def test_price_stats_survives_snapshot_write_failure(monkeypatch) -> None:
    from api.routers import price_stats

    async def broken_upsert(*args, **kwargs):
        del args, kwargs
        raise OperationalError("INSERT", {}, Exception("database is down"))

    monkeypatch.setattr(price_stats, "upsert_query_snapshot", broken_upsert)
    app = make_app(monkeypatch)
    with TestClient(app) as client:
        response = client.get("/api/v1/price-stats", params={"query": "iphone"})

    assert response.status_code == 200
    assert response.json()["count"] == 3
