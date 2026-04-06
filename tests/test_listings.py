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
    async def search_all_ads(self, **kwargs) -> dict:
        return {
            "total": 7,
            "ads": [
                {
                    "ad_id": 1,
                    "subject": "iPhone 15 256GB",
                    "price_byn": 200000,
                    "ad_link": "https://www.kufar.by/item/1",
                    "list_time": "2026-04-01T10:00:00",
                    "region_id": 6,
                    "ad_parameters": [{"p": "condition", "v": "Новый"}],
                },
                {
                    "ad_id": 2,
                    "subject": "iPhone 15 Pro 256GB",
                    "price_byn": 260000,
                    "ad_link": "https://www.kufar.by/item/2",
                    "list_time": "2026-04-01T11:00:00",
                    "region_id": 6,
                    "ad_parameters": [{"p": "condition", "v": "Новый"}],
                },
                {
                    "ad_id": 3,
                    "subject": "iPhone 15 mini 128GB",
                    "price_byn": 150000,
                    "ad_link": "https://www.kufar.by/item/3",
                    "list_time": "2026-04-01T09:00:00",
                    "region_id": 6,
                    "ad_parameters": [{"p": "condition", "v": "Б/у"}],
                },
            ]
        }

    async def aclose(self) -> None:
        return None


def _make_app_with_fake_client(fake_client=None):
    from api.dependencies import get_cache, get_currency_service, get_kufar_client
    from api.main import create_app

    client = fake_client or FakeKufarClient()
    app = create_app()
    app.dependency_overrides[get_cache] = lambda: MemoryCache()
    app.dependency_overrides[get_currency_service] = lambda: FakeCurrencyService()
    app.dependency_overrides[get_telegram_user] = lambda: _fake_telegram_user
    app.dependency_overrides[get_kufar_client] = lambda: client
    return app


def test_listings_endpoint_returns_items(monkeypatch) -> None:
    app = _make_app_with_fake_client()
    with TestClient(app) as client:
        response = client.get("/api/v1/listings", params={"query": "iphone", "currency": "USD"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 3
    assert payload["returned"] == 3
    assert payload["listings"][0]["title"] == "iPhone 15 Pro 256GB"
    assert payload["normalized_query"] == "iphone"
    assert payload["listings"][0]["deal_verdict"] is not None
    assert isinstance(payload["listings"][0]["deal_reasons"], list)
    assert payload["listings"][0]["liquidity"] is not None
    assert payload["listings"][0]["flip_estimates"]


def test_listings_endpoint_supports_cheap_sort(monkeypatch) -> None:
    app = _make_app_with_fake_client()
    with TestClient(app) as client:
        response = client.get(
            "/api/v1/listings",
            params={
                "query": "iphone",
                "currency": "BYN",
                "sort": "cheap",
                "discount_from_percent": 10,
                "discount_to_percent": 30,
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["sort"] == "cheap"
    assert payload["discount_from_percent"] == 10
    assert payload["discount_to_percent"] == 30
    assert [item["ad_id"] for item in payload["listings"]] == [3]


def test_listings_endpoint_supports_strict_search(monkeypatch) -> None:
    app = _make_app_with_fake_client()
    with TestClient(app) as client:
        response = client.get(
            "/api/v1/listings",
            params={"query": "iphone 15 256", "currency": "BYN", "strict_search": True},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    assert [item["ad_id"] for item in payload["listings"]] == [1]


def test_listings_endpoint_normalizes_alias_queries(monkeypatch) -> None:
    app = _make_app_with_fake_client()
    with TestClient(app) as client:
        response = client.get(
            "/api/v1/listings",
            params={"query": "айфон 15 256гб", "currency": "BYN", "strict_search": True},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    assert payload["normalized_query"] == "iphone 15 256"


def test_listings_endpoint_returns_market_signals(monkeypatch) -> None:
    class SignalsClient:
        async def search_all_ads(self, **kwargs) -> dict:
            del kwargs
            return {
                "total": 5,
                "ads": [
                    {
                        "ad_id": 1,
                        "subject": "iPhone 15 256GB",
                        "price_byn": 200000,
                        "ad_link": "https://www.kufar.by/item/1",
                        "list_time": "2026-04-01T10:00:00",
                        "region_id": 6,
                        "ad_parameters": [{"p": "seller_type", "v": "Частное лицо"}],
                    },
                    {
                        "ad_id": 2,
                        "subject": "iPhone 15 256GB",
                        "price_byn": 202000,
                        "ad_link": "https://www.kufar.by/item/2",
                        "list_time": "2026-04-01T11:00:00",
                        "region_id": 6,
                        "ad_parameters": [{"p": "seller_type", "v": "Частное лицо"}],
                    },
                    {
                        "ad_id": 3,
                        "subject": "iPhone 15 256GB",
                        "price_byn": 198000,
                        "ad_link": "https://www.kufar.by/item/3",
                        "list_time": "2026-04-01T12:00:00",
                        "region_id": 6,
                        "ad_parameters": [{"p": "seller_type", "v": "Частное лицо"}],
                    },
                    {
                        "ad_id": 4,
                        "subject": "iPhone 15 Pro 256GB",
                        "price_byn": 240000,
                        "ad_link": "https://www.kufar.by/item/4",
                        "list_time": "2026-04-01T09:00:00",
                        "region_id": 6,
                        "ad_parameters": [{"p": "seller_type", "v": "Магазин"}],
                    },
                    {
                        "ad_id": 5,
                        "subject": "iPhone 15 Ultra 1TB",
                        "price_byn": 420000,
                        "ad_link": "https://www.kufar.by/item/5",
                        "list_time": "2026-04-01T08:00:00",
                        "region_id": 6,
                        "ad_parameters": [{"p": "seller_type", "v": "Магазин"}],
                    },
                ],
            }

        async def aclose(self) -> None:
            return None

    app = _make_app_with_fake_client(SignalsClient())
    with TestClient(app) as client:
        response = client.get("/api/v1/listings", params={"query": "iphone", "currency": "BYN"})

    assert response.status_code == 200
    payload = response.json()
    duplicate_item = next(item for item in payload["listings"] if item["ad_id"] == 2)
    anomaly_item = next(item for item in payload["listings"] if item["ad_id"] == 5)
    assert duplicate_item["is_duplicate"] is True
    assert duplicate_item["duplicate_count"] >= 1
    assert duplicate_item["fair_price_label"] is not None
    assert duplicate_item["deal_score"] >= 0
    assert duplicate_item["deal_verdict"] in {"Забирать", "Смотреть", "Норм", "Мимо"}
    assert duplicate_item["liquidity"] is not None
    assert anomaly_item["anomaly_flags"] == ["too_expensive"]
    assert anomaly_item["region_name"] == "Регион 6"
