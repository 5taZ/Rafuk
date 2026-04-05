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

    async def search_all_ads(self, **kwargs) -> dict:
        del kwargs
        return {
            "total": 7,
            "ads": [
                {
                    "ad_id": 1,
                    "subject": "iPhone 15",
                    "price_byn": 200000,
                    "ad_link": "https://www.kufar.by/item/1",
                    "list_time": "2026-04-01T10:00:00",
                    "region_id": 6,
                    "ad_parameters": [{"p": "condition", "v": "Новый"}],
                }
            ]
        }

    async def aclose(self) -> None:
        return None


def test_listings_endpoint_returns_items(monkeypatch) -> None:
    from api.dependencies import get_cache, get_currency_service
    from api.main import create_app
    from api.routers import listings

    monkeypatch.setattr(listings, "KufarClient", FakeKufarClient)
    app = create_app()
    app.dependency_overrides[get_cache] = lambda: MemoryCache()
    app.dependency_overrides[get_currency_service] = lambda: FakeCurrencyService()
    with TestClient(app) as client:
        response = client.get("/api/v1/listings", params={"query": "iphone", "currency": "USD"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 7
    assert payload["returned"] == 1
    assert payload["listings"][0]["title"] == "iPhone 15"
