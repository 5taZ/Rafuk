from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from api.dependencies import get_telegram_user
from api.middleware.telegram_auth import TelegramInitData
from api.services.cache import MemoryCache

_fake_telegram_user = TelegramInitData(user_id=123456, first_name="Test", raw={})


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
    async def aclose(self) -> None:
        return None


def test_segments_endpoint_returns_all_groups(monkeypatch) -> None:
    from api.dependencies import get_cache, get_currency_service, get_kufar_client
    from api.main import create_app
    from api.routers import segments

    async def fake_parallel_search_all(client, tasks, settings):
        del client, tasks, settings
        return [
            {"ads": [{"price_byn": 200000}]},
            {"ads": [{"price_byn": 250000}]},
            {"ads": [{"price_byn": 180000}]},
            {"ads": [{"price_byn": 220000}]},
        ]

    monkeypatch.setattr(segments, "parallel_search_all", fake_parallel_search_all)
    app = create_app()
    app.dependency_overrides[get_cache] = lambda: MemoryCache()
    app.dependency_overrides[get_currency_service] = lambda: FakeCurrencyService()
    app.dependency_overrides[get_telegram_user] = lambda: _fake_telegram_user
    app.dependency_overrides[get_kufar_client] = lambda: FakeKufarClient()
    with TestClient(app) as client:
        response = client.get("/api/v1/segments", params={"query": "iphone", "currency": "USD"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["new_private"]["count"] == 1
    assert payload["used_shop"]["count"] == 1
