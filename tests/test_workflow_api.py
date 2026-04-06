from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import FAKE_TELEGRAM_USER


class FakeKufarClient:
    def __init__(self, settings) -> None:
        del settings

    async def search_all_ads(self, **kwargs) -> dict:
        del kwargs
        return {
            "total": 2,
            "ads": [
                {
                    "ad_id": 101,
                    "subject": "iPhone 15 128GB",
                    "price_byn": 180000,
                    "ad_link": "https://www.kufar.by/item/101",
                    "list_time": "2026-04-06T10:00:00",
                    "region_id": 6,
                    "ad_parameters": [{"p": "seller_type", "v": "Частное лицо"}],
                },
                {
                    "ad_id": 102,
                    "subject": "iPhone 15 128GB",
                    "price_byn": 195000,
                    "ad_link": "https://www.kufar.by/item/102",
                    "list_time": "2026-04-05T10:00:00",
                    "region_id": 6,
                    "ad_parameters": [{"p": "seller_type", "v": "Магазин"}],
                },
            ],
        }

    async def aclose(self) -> None:
        return None


def test_leads_and_watchlist_workflow(monkeypatch) -> None:
    from api.dependencies import get_telegram_user
    from api.main import create_app
    from api.routers import workflow

    monkeypatch.setattr(workflow, "KufarClient", FakeKufarClient)
    app = create_app()
    app.dependency_overrides[get_telegram_user] = lambda: FAKE_TELEGRAM_USER

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
            json={"status": "in_progress", "notes": "созвониться"},
        )
        assert update_lead_response.status_code == 200
        assert update_lead_response.json()["status"] == "in_progress"

        update_lead_meta_response = client.patch(
            f"/api/v1/leads/{lead['id']}",
            json={"target_resale_byn": None, "notes": "созвониться сегодня"},
        )
        assert update_lead_meta_response.status_code == 200
        assert update_lead_meta_response.json()["target_resale_byn"] is None
        assert update_lead_meta_response.json()["notes"] == "созвониться сегодня"

        list_leads_response = client.get("/api/v1/leads")
        assert list_leads_response.status_code == 200
        assert len(list_leads_response.json()) == 1

        watchlist_response = client.post(
            "/api/v1/watchlist",
            json={
                "query": "iphone 15 128",
                "ad_id": 101,
                "title": "iPhone 15 128GB",
                "link": "https://www.kufar.by/item/101",
                "price_byn": 2000,
            },
        )
        assert watchlist_response.status_code == 201
        watchlist_item = watchlist_response.json()
        assert watchlist_item["initial_price_byn"] == 2000
        assert watchlist_item["workflow_status"] == "watching"

        update_watchlist_response = client.patch(
            f"/api/v1/watchlist/{watchlist_item['id']}",
            json={"workflow_status": "reviewing", "notes": "сравнить вечером"},
        )
        assert update_watchlist_response.status_code == 200
        assert update_watchlist_response.json()["workflow_status"] == "reviewing"
        assert update_watchlist_response.json()["notes"] == "сравнить вечером"

        refresh_response = client.post("/api/v1/watchlist/refresh", json={})
        assert refresh_response.status_code == 200
        assert refresh_response.json() == {"updated": 1, "missing": 0, "price_drops": 1}

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
