from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from api.services import aggregator
from api.services.cache import MemoryCache

FROZEN_NOW = datetime(2026, 4, 1, 12, tzinfo=UTC)


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

    monkeypatch.setattr(aggregator, "utc_now", lambda: FROZEN_NOW)
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
    assert payload["fair_price_label"] is not None
    assert payload["region_name"] == "Регион 6"
    assert payload["normalized_query"] == "iphone"
    # 55 base + 8 private seller + 12 fresh (2 h old) + 6 model match.
    assert payload["deal_score"] == 81.0
    assert payload["deal_verdict"] == "Забирать"
    assert payload["deal_reasons"] == ["частник", "свежий лот"]
    assert payload["seller_type"] == "Частное лицо"
    assert payload["condition"] == "Новый"
    assert payload["price_byn"] == 2000
    assert payload["liquidity"] is not None
    assert payload["flip_estimates"]


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
    assert response.json()["detail"] == "Объявление не найдено в выдаче по этому запросу"


def test_listing_detail_handles_unpriced_and_malformed_ads(monkeypatch) -> None:
    from api.dependencies import get_cache, get_currency_service
    from api.main import create_app
    from api.routers import listing_detail

    class OddClient(FakeKufarClient):
        async def search_all_ads(self, **kwargs) -> dict:
            del kwargs
            return {
                "ads": [
                    {"ad_id": None, "subject": "broken"},
                    {
                        "ad_id": "7",
                        "subject": "iPhone 15",
                        "price_byn": None,
                        "list_time": None,
                        "region_id": "abc",
                        "images": [None, "oops", {"path": "adim1/7.jpg"}],
                        "ad_parameters": None,
                        "account_parameters": ["oops"],
                        "company_ad": True,
                    },
                    {"ad_id": 8, "subject": "iPhone 15", "price_byn": 200000},
                ],
            }

    monkeypatch.setattr(listing_detail, "KufarClient", OddClient)
    app = create_app()
    app.dependency_overrides[get_cache] = lambda: MemoryCache()
    app.dependency_overrides[get_currency_service] = lambda: FakeCurrencyService()
    with TestClient(app) as client:
        response = client.get("/api/v1/listing-detail", params={"query": "iphone", "ad_id": 7})
        blank = client.get("/api/v1/listing-detail", params={"query": " ", "ad_id": 7})

    assert response.status_code == 200
    payload = response.json()
    assert payload["currency"] == "BYN"
    assert payload["price_byn"] is None
    assert payload["price_vs_median"] is None
    assert payload["fair_price_band"] is None
    assert payload["deal_verdict"] == "Нет цены"
    assert payload["flip_estimates"] == []
    assert payload["region_id"] is None
    assert payload["images"] == ["https://rms.kufar.by/v1/gallery/adim1/7.jpg"]
    assert payload["parameters"] == []
    assert payload["seller_fields"] == []
    assert payload["seller_type"] == "Магазин"
    assert blank.status_code == 422
