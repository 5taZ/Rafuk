from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from api.dependencies import get_telegram_user
from api.services.cache import MemoryCache
from tests.conftest import FAKE_TELEGRAM_USER


class FakeCurrencyService:
    async def get_rates(self) -> dict[str, object]:
        return {
            "base": "BYN",
            "rates": {"USD": 3.0},
            "source": "test",
            "fetched_at": datetime.now(UTC).isoformat(),
        }

    def convert_from_byn(self, amount_byn: float, currency: str, rates: dict[str, float]) -> float:
        if currency == "BYN":
            return round(amount_byn, 2)
        return round(amount_byn / rates[currency], 2)


class FakeKufarClient:
    async def search_all_ads(self, **kwargs) -> dict:
        del kwargs
        return {
            "total": 4,
            "ads": [
                {
                    "ad_id": 1,
                    "subject": "iPhone 15",
                    "price_byn": 200000,
                    "ad_link": "https://www.kufar.by/item/1",
                    "region_id": 6,
                },
                {
                    "ad_id": 2,
                    "subject": "iPhone 15 Pro",
                    "price_byn": 220000,
                    "ad_link": "https://www.kufar.by/item/2",
                    "region_id": 6,
                },
                {
                    "ad_id": 3,
                    "subject": "iPhone 15 Mini",
                    "price_byn": 180000,
                    "ad_link": "https://www.kufar.by/item/3",
                    "region_id": 7,
                },
                {
                    "ad_id": 4,
                    "subject": "iPhone 15 Max",
                    "price_byn": 0,
                    "ad_link": "https://www.kufar.by/item/4",
                    "region_id": 7,
                },
            ],
        }

    async def aclose(self) -> None:
        return None


def test_geography_endpoint_returns_region_stats(monkeypatch) -> None:
    from api.dependencies import get_cache, get_currency_service, get_kufar_client
    from api.main import create_app

    app = create_app()
    app.dependency_overrides[get_cache] = lambda: MemoryCache()
    app.dependency_overrides[get_currency_service] = lambda: FakeCurrencyService()
    app.dependency_overrides[get_telegram_user] = lambda: FAKE_TELEGRAM_USER
    app.dependency_overrides[get_kufar_client] = lambda: FakeKufarClient()

    with TestClient(app) as client:
        response = client.get("/api/v1/geography", params={"query": "iphone", "currency": "BYN"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["total_analyzed"] == 3
    assert len(payload["regions"]) == 2
    assert payload["regions"][0]["region_name"] == "Регион 6"
    assert payload["regions"][0]["count"] == 2
