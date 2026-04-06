from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from api.dependencies import get_telegram_user
from api.services.cache import MemoryCache
from tests.conftest import FAKE_TELEGRAM_USER, init_test_tables


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
    async def search_all_ads(self, **kwargs) -> dict:
        del kwargs
        return {
            "ads": [{"price_byn": 200000}, {"price_byn": 220000}, {"price_byn": 180000}],
            "total": 10,
        }

    async def aclose(self) -> None:
        return None


def test_price_stats_endpoint_returns_payload(monkeypatch) -> None:
    from api.dependencies import get_cache, get_currency_service, get_kufar_client
    from api.main import create_app

    app = create_app()
    app.dependency_overrides[get_cache] = lambda: MemoryCache()
    app.dependency_overrides[get_currency_service] = lambda: FakeCurrencyService()
    app.dependency_overrides[get_telegram_user] = lambda: FAKE_TELEGRAM_USER
    app.dependency_overrides[get_kufar_client] = lambda: FakeKufarClient()
    with TestClient(app) as client:
        asyncio.get_event_loop().run_until_complete(init_test_tables(app))
        response = client.get("/api/v1/price-stats", params={"query": "iphone", "currency": "USD"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["query"] == "iphone"
    assert payload["count"] == 3
    assert payload["total_results"] == 3
    assert payload["analyzed_count"] == 3
    assert payload["currency"] == "USD"
