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

    # load_segment_datasets does 2 parallel searches (condition=new + condition=used)
    async def fake_parallel_search(client, tasks, settings):
        del client, tasks, settings
        # Return exactly 2 responses matching the 2 _API_CONDITION_TASKS
        return [
            {"ads": [{"price_byn": 200000}, {"price_byn": 250000}]},
            {"ads": [{"price_byn": 180000}, {"price_byn": 220000}]},
        ]

    monkeypatch.setattr(segments, "KufarClient", FakeKufarClient)
    monkeypatch.setattr(segments, "parallel_search_all", fake_parallel_search)
    app = create_app()
    app.dependency_overrides[get_cache] = lambda: MemoryCache()
    app.dependency_overrides[get_currency_service] = lambda: FakeCurrencyService()
    with TestClient(app) as client:
        response = client.get("/api/v1/segments", params={"query": "iphone", "currency": "USD"})
    assert response.status_code == 200
    payload = response.json()
    # All 4 segments exist; private/shop split is client-side via company_ad flag
    assert "new_private" in payload
    assert "used_shop" in payload
    # Since no ad has company_ad=True, all go to *_private segments
    assert payload["new_private"]["count"] >= 1
    assert payload["used_private"]["count"] >= 1
