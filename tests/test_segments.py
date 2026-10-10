from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient

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

    async def aclose(self) -> None:
        return None


def test_segments_endpoint_returns_all_groups(monkeypatch) -> None:
    from api.dependencies import get_cache, get_currency_service
    from api.main import create_app
    from api.routers import segments

    segment_prices = {
        ("new", "search_owner"): 200000,
        ("new", "search_business"): 250000,
        ("used", "search_owner"): 180000,
        ("used", "search_business"): 220000,
    }

    async def fake_parallel_search_all(client, tasks, settings):
        # Like the real helper: one response per task (the dataset cache loads segments
        # one task at a time).
        del client, settings
        return [
            {"ads": [{"price_byn": segment_prices[(task["condition"], task["seller_type"])]}]}
            for task in tasks
        ]

    monkeypatch.setattr(segments, "KufarClient", FakeKufarClient)
    monkeypatch.setattr(segments, "parallel_search_all", fake_parallel_search_all)
    app = create_app()
    app.dependency_overrides[get_cache] = lambda: MemoryCache()
    app.dependency_overrides[get_currency_service] = lambda: FakeCurrencyService()
    with TestClient(app) as client:
        response = client.get("/api/v1/segments", params={"query": "iphone", "currency": "USD"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["new_private"]["count"] == 1
    assert payload["used_shop"]["count"] == 1


def test_segments_validates_input_and_queries_kufar_in_byn(monkeypatch) -> None:
    from api.dependencies import get_cache, get_currency_service
    from api.main import create_app
    from api.routers import segments

    seen_tasks: list[dict] = []

    async def fake_parallel_search_all(client, tasks, settings):
        del client, settings
        seen_tasks.extend(tasks)
        return [{"ads": [{"price_byn": 200000}]} for _ in tasks]

    monkeypatch.setattr(segments, "KufarClient", FakeKufarClient)
    monkeypatch.setattr(segments, "parallel_search_all", fake_parallel_search_all)
    app = create_app()
    cache = MemoryCache()
    app.dependency_overrides[get_cache] = lambda: cache
    app.dependency_overrides[get_currency_service] = lambda: FakeCurrencyService()
    with TestClient(app) as client:
        default = client.get("/api/v1/segments", params={"query": " iPhone "})
        cached = client.get("/api/v1/segments", params={"query": "iphone"})
        blank = client.get("/api/v1/segments", params={"query": "  "})
        euro = client.get("/api/v1/segments", params={"query": "iphone", "currency": "EUR"})

    assert default.status_code == 200
    assert default.json()["currency"] == "BYN"
    assert default.json()["new_private"]["median"] == 2000.0
    assert cached.json()["query"] == "iphone"
    assert len(seen_tasks) == 4
    assert {task["currency"] for task in seen_tasks} == {"BYN"}
    assert {task["query"] for task in seen_tasks} == {"iPhone"}
    assert blank.status_code == 422
    assert euro.status_code == 422
