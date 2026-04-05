from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from api.services.cache import MemoryCache


class FakeCurrencyService:
    async def get_rates(self) -> dict[str, object]:
        return {
            "base": "BYN",
            "rates": {"USD": 3.2},
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
            "total": 1,
            "ads": [
                {
                    "ad_id": 1,
                    "subject": "iPhone 15",
                    "price_byn": 200000,
                    "ad_link": "https://www.kufar.by/item/1",
                    "list_time": "2026-04-01T10:00:00",
                    "region_id": 6,
                    "body": "Полное описание объявления",
                    "company_ad": True,
                    "phone_hidden": False,
                    "images": [{"path": "adim1/test.jpg"}],
                    "ad_parameters": [
                        {"p": "category", "pl": "Подкатегория", "vl": "Мобильные телефоны"},
                        {"p": "condition", "pl": "Состояние", "v": "Новый", "vl": "Новое"},
                        {"p": "seller_type", "pl": "Продавец", "v": "Частное лицо"},
                        {"p": "phones_memory", "pl": "Память", "vl": "256 Гб"},
                    ],
                    "account_parameters": [
                        {"p": "name", "pl": "Имя", "v": "Иван"},
                        {"p": "shop_address", "pl": "Город", "v": "Минск"},
                    ],
                }
            ],
        }

    async def aclose(self) -> None:
        return None


def test_listing_detail_endpoint_returns_full_card(monkeypatch) -> None:
    from api.dependencies import get_cache, get_currency_service
    from api.main import create_app
    from api.routers import listing_detail

    monkeypatch.setattr(listing_detail, "KufarClient", FakeKufarClient)
    app = create_app()
    app.dependency_overrides[get_cache] = lambda: MemoryCache()
    app.dependency_overrides[get_currency_service] = lambda: FakeCurrencyService()
    with TestClient(app) as client:
        response = client.get(
            "/api/v1/listing-detail",
            params={"query": "iphone", "ad_id": 1, "currency": "BYN"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["title"] == "iPhone 15"
    assert payload["description"] == "Полное описание объявления"
    assert payload["images"][0].endswith("/adim1/test.jpg")
    assert payload["parameters"][0]["label"] == "Подкатегория"
    assert payload["seller_fields"][0]["value"] == "Иван"


def test_listing_detail_not_found(monkeypatch) -> None:
    from api.dependencies import get_cache, get_currency_service
    from api.main import create_app
    from api.routers import listing_detail

    monkeypatch.setattr(listing_detail, "KufarClient", FakeKufarClient)
    app = create_app()
    app.dependency_overrides[get_cache] = lambda: MemoryCache()
    app.dependency_overrides[get_currency_service] = lambda: FakeCurrencyService()
    with TestClient(app) as client:
        response = client.get(
            "/api/v1/listing-detail",
            params={"query": "iphone", "ad_id": 999, "currency": "BYN"},
        )

    assert response.status_code == 404
