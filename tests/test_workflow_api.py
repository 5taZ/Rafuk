from __future__ import annotations

from fastapi.testclient import TestClient

from api.middleware.telegram_auth import TelegramInitData


def fake_telegram_user() -> TelegramInitData:
    return TelegramInitData(user_id=123456, first_name="Test", raw={})


class FakeKufarClient:
    def __init__(self, settings) -> None:
        del settings

    async def search_all_ads(self, **kwargs) -> dict:
        del kwargs
        return {
            "total": 3,
            "ads": [
                {
                    "ad_id": 101,
                    "subject": "iPhone 15 128GB",
                    "price_byn": 1800,
                    "ad_link": "https://www.kufar.by/item/101",
                    "list_time": "2026-04-06T10:00:00",
                    "region_id": 6,
                    "ad_parameters": [{"p": "seller_type", "v": "Частное лицо"}],
                },
                {
                    "ad_id": 102,
                    "subject": "iPhone 15 128GB",
                    "price_byn": 1950,
                    "ad_link": "https://www.kufar.by/item/102",
                    "list_time": "2026-04-05T10:00:00",
                    "region_id": 6,
                    "ad_parameters": [{"p": "seller_type", "v": "Магазин"}],
                },
                {
                    "ad_id": 201,
                    "subject": "iPhone 15 128GB",
                    "price_byn": 1800,
                    "ad_link": "https://www.kufar.by/item/201",
                    "list_time": "2026-04-06T10:00:00",
                    "region_id": 6,
                    "ad_parameters": [{"p": "seller_type", "v": "Частное лицо"}],
                },
            ],
        }

    async def aclose(self) -> None:
        return None


def test_leads_and_watchlist_workflow(monkeypatch) -> None:
    from api.dependencies import get_kufar_client, get_telegram_user
    from api.main import create_app
    from api.routers import workflow

    monkeypatch.setattr(workflow, "KufarClient", FakeKufarClient)
    app = create_app()
    app.dependency_overrides[get_kufar_client] = lambda: FakeKufarClient(None)
    app.dependency_overrides[get_telegram_user] = fake_telegram_user

    with TestClient(app) as client:
        lead_response = client.post(
            "/api/v1/leads",
            json={
                "query": "iphone 15 128",
                "ad_id": 101,
                "title": "iPhone 15 128GB",
                "link": "https://www.kufar.by/item/101",
                "price_byn": 2000,
                "target_resale_byn": 2250,
                "source": "manual",
            },
        )
        assert lead_response.status_code == 201
        lead = lead_response.json()
        assert lead["status"] == "new"
        assert lead["target_resale_byn"] == 2250

        update_lead_response = client.patch(
            f"/api/v1/leads/{lead['id']}",
            json={"status": "in_progress"},
        )
        assert update_lead_response.status_code == 200
        assert update_lead_response.json()["status"] == "in_progress"

        update_lead_meta_response = client.patch(
            f"/api/v1/leads/{lead['id']}",
            json={"target_resale_byn": None},
        )
        assert update_lead_meta_response.status_code == 200
        assert update_lead_meta_response.json()["target_resale_byn"] is None

        sell_lead_response = client.patch(
            f"/api/v1/leads/{lead['id']}",
            json={"status": "sold", "buy_price_byn": 1700, "sold_price_byn": 2100},
        )
        assert sell_lead_response.status_code == 200
        assert sell_lead_response.json()["status"] == "sold"

        close_lead_response = client.patch(
            f"/api/v1/leads/{lead['id']}",
            json={"status": "closed"},
        )
        assert close_lead_response.status_code == 200
        assert close_lead_response.json()["status"] == "closed"

        list_leads_response = client.get("/api/v1/leads")
        assert list_leads_response.status_code == 200
        assert len(list_leads_response.json()) == 1

        sold_lead_response = client.post(
            "/api/v1/leads",
            json={
                "query": "iphone 14 pro max",
                "ad_id": 103,
                "title": "iPhone 14 Pro Max",
                "link": "https://www.kufar.by/item/103",
                "price_byn": 1900,
                "source": "manual",
            },
        )
        assert sold_lead_response.status_code == 201
        sold_lead = sold_lead_response.json()
        assert (
            client.patch(
                f"/api/v1/leads/{sold_lead['id']}",
                json={"status": "sold", "buy_price_byn": 1800, "sold_price_byn": 2200},
            ).status_code
            == 200
        )

        delete_active_leads_response = client.delete("/api/v1/leads/all")
        assert delete_active_leads_response.status_code == 204
        remaining_leads = client.get("/api/v1/leads").json()
        assert len(remaining_leads) == 1
        assert remaining_leads[0]["status"] == "closed"

        # Use a fresh ad_id (201) for watchlist — the leads/watchlist tables
        # were merged in 20260427_0001 so (user_id, ad_id) is unique. Trying
        # to add ad_id=101 to watchlist would collide with the closed lead
        # we kept above.
        watchlist_response = client.post(
            "/api/v1/watchlist",
            json={
                "query": "iphone 15 128",
                "ad_id": 201,
                "title": "iPhone 15 128GB",
                "link": "https://www.kufar.by/item/201",
                "price_byn": 2000,
            },
        )
        assert watchlist_response.status_code == 201
        watchlist_item = watchlist_response.json()
        assert watchlist_item["initial_price_byn"] == 2000
        # workflow_status is now a no-op constant after watchlist→leads merge.
        assert watchlist_item["workflow_status"] == "default"

        update_watchlist_response = client.patch(
            f"/api/v1/watchlist/{watchlist_item['id']}",
            json={"workflow_status": "reviewing", "notes": "сравнить вечером"},
        )
        assert update_watchlist_response.status_code == 200
        # workflow_status is intentionally fixed at "default"; only notes mutates.
        assert update_watchlist_response.json()["workflow_status"] == "default"
        assert update_watchlist_response.json()["notes"] == "сравнить вечером"

        refresh_response = client.post("/api/v1/watchlist/refresh", json={})
        assert refresh_response.status_code == 200
        refresh_data = refresh_response.json()
        assert refresh_data["updated"] >= 1

        list_watchlist_response = client.get("/api/v1/watchlist")
        assert list_watchlist_response.status_code == 200
        items = list_watchlist_response.json()
        assert len(items) == 1
        assert items[0]["current_price_byn"] == 1800
        assert items[0]["price_delta_byn"] == -200
        assert items[0]["market_status"] == "price_drop"

        delete_response = client.delete(f"/api/v1/watchlist/{watchlist_item['id']}")
        assert delete_response.status_code == 204
        assert client.get("/api/v1/watchlist").json() == []
