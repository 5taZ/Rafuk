from __future__ import annotations

from fastapi.testclient import TestClient

from api.middleware.telegram_auth import TelegramInitData


def fake_telegram_user() -> TelegramInitData:
    return TelegramInitData(user_id=123456, first_name="Test", raw={})


def test_trackers_crud() -> None:
    from api.dependencies import get_telegram_user
    from api.main import create_app

    app = create_app()
    app.dependency_overrides[get_telegram_user] = fake_telegram_user

    with TestClient(app) as client:
        create_response = client.post(
            "/api/v1/trackers",
            json={"query": "iphone 15", "interval_min": 30},
        )
        assert create_response.status_code == 201
        created = create_response.json()
        assert created["query"] == "iphone 15"
        assert created["interval_min"] == 30

        list_response = client.get("/api/v1/trackers")
        assert list_response.status_code == 200
        trackers = list_response.json()
        assert len(trackers) == 1
        assert trackers[0]["id"] == created["id"]

        delete_response = client.delete(f"/api/v1/trackers/{created['id']}")
        assert delete_response.status_code == 204

        list_response_after_delete = client.get("/api/v1/trackers")
        assert list_response_after_delete.status_code == 200
        assert list_response_after_delete.json() == []
