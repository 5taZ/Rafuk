from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from api.middleware.telegram_auth import TelegramInitData


def fake_telegram_user() -> TelegramInitData:
    return TelegramInitData(user_id=123456, first_name="Test", raw={})


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
            "total": 3,
            "ads": [
                {
                    "ad_id": 1,
                    "subject": "iPhone 15 256GB",
                    "price_byn": 180000,
                    "ad_link": "https://www.kufar.by/item/1",
                    "list_time": "2026-04-06T10:00:00",
                    "region_id": 6,
                    "ad_parameters": [{"p": "seller_type", "v": "Частное лицо"}],
                },
                {
                    "ad_id": 2,
                    "subject": "iPhone 15 256GB",
                    "price_byn": 198000,
                    "ad_link": "https://www.kufar.by/item/2",
                    "list_time": "2026-04-06T09:00:00",
                    "region_id": 6,
                    "ad_parameters": [{"p": "seller_type", "v": "Частное лицо"}],
                },
                {
                    "ad_id": 3,
                    "subject": "iPhone 15 256GB",
                    "price_byn": 225000,
                    "ad_link": "https://www.kufar.by/item/3",
                    "list_time": "2026-04-05T09:00:00",
                    "region_id": 6,
                    "ad_parameters": [{"p": "seller_type", "v": "Магазин"}],
                },
            ],
        }

    async def aclose(self) -> None:
        return None


def test_saved_searches_crud_and_board(monkeypatch) -> None:
    from api.dependencies import get_currency_service, get_telegram_user
    from api.main import create_app
    from api.routers import saved_searches

    monkeypatch.setattr(saved_searches, "KufarClient", FakeKufarClient)
    app = create_app()
    app.dependency_overrides[get_telegram_user] = fake_telegram_user
    app.dependency_overrides[get_currency_service] = lambda: FakeCurrencyService()

    with TestClient(app) as client:
        create_response = client.post(
            "/api/v1/saved-searches",
            json={
                "query": "iphone 15 256",
                "group_name": "iPhone набор",
                "strict_mode": True,
                "target_discount_percent": 8,
                "seller_type": "Частное лицо",
            },
        )
        assert create_response.status_code == 201
        created = create_response.json()
        assert created["normalized_query"] == "iphone 15 256"
        assert created["group_name"] == "iPhone набор"
        assert created["strict_mode"] is True
        assert created["config_keyword"] is not None

        list_response = client.get("/api/v1/saved-searches")
        assert list_response.status_code == 200
        saved_searches_payload = list_response.json()
        assert len(saved_searches_payload) == 1
        assert saved_searches_payload[0]["id"] == created["id"]

        board_response = client.get("/api/v1/opportunity-board", params={"currency": "BYN"})
        assert board_response.status_code == 200
        board = board_response.json()
        assert board["items"]
        assert board["items"][0]["saved_search_id"] == created["id"]
        assert board["items"][0]["listing"]["deal_verdict"] in {"Забирать", "Смотреть", "Норм", "Мимо"}
        assert "top_price_drops" in board
        assert "market_signals" in board

        delete_response = client.delete(f"/api/v1/saved-searches/{created['id']}")
        assert delete_response.status_code == 204

        empty_board_response = client.get("/api/v1/opportunity-board", params={"currency": "BYN"})
        assert empty_board_response.status_code == 200
        assert empty_board_response.json()["items"] == []
