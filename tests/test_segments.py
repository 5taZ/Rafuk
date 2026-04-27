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

    # Segments now derive from the singleflighted dataset (no extra
    # Kufar fan-out). Stub load_query_dataset to return a mixed
    # batch of new/used + private/shop ads and verify the router
    # splits them into all 4 baskets correctly.
    async def fake_load_query_dataset(**kwargs):
        del kwargs
        from api.services.query_pipeline import QueryDataset
        new = [{"p": "condition", "v": "Новый"}]
        used = [{"p": "condition", "v": "Б/у"}]
        ads = [
            {"price_byn": 2000, "company_ad": False, "ad_parameters": new},
            {"price_byn": 2500, "company_ad": True, "ad_parameters": new},
            {"price_byn": 1800, "company_ad": False, "ad_parameters": used},
            {"price_byn": 2200, "company_ad": True, "ad_parameters": used},
        ]
        return QueryDataset(
            query="iphone", currency="USD", strict_search=False,
            response={"ads": ads, "total": 4}, ads=ads,
        )

    monkeypatch.setattr(segments, "KufarClient", FakeKufarClient)
    monkeypatch.setattr(segments, "load_query_dataset", fake_load_query_dataset)
    app = create_app()
    app.dependency_overrides[get_cache] = lambda: MemoryCache()
    app.dependency_overrides[get_currency_service] = lambda: FakeCurrencyService()
    with TestClient(app) as client:
        response = client.get("/api/v1/segments", params={"query": "iphone", "currency": "USD"})
    assert response.status_code == 200
    payload = response.json()
    # All 4 segments exist (new/used × private/shop) and each has 1 ad.
    assert payload["new_private"]["count"] == 1
    assert payload["new_shop"]["count"] == 1
    assert payload["used_private"]["count"] == 1
    assert payload["used_shop"]["count"] == 1
