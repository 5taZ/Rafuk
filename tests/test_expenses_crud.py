from __future__ import annotations

from fastapi.testclient import TestClient

from api.middleware.telegram_auth import TelegramInitData

USER_A = TelegramInitData(user_id=555555, first_name="ExpTester", raw={})


def _make_app(user: TelegramInitData):
    from api.dependencies import get_telegram_user
    from api.main import create_app

    app = create_app()
    app.dependency_overrides[get_telegram_user] = lambda: user
    return app


def _create_lead(client: TestClient) -> dict:
    resp = client.post(
        "/api/v1/leads",
        json={
            "query": "macbook air m2",
            "ad_id": 7001,
            "title": "MacBook Air M2",
            "link": "https://www.kufar.by/item/7001",
            "price_byn": 2500,
            "source": "manual",
        },
    )
    assert resp.status_code == 201
    return resp.json()


def test_create_expense_happy_path() -> None:
    app = _make_app(USER_A)
    with TestClient(app) as client:
        lead = _create_lead(client)
        resp = client.post(
            f"/api/v1/leads/{lead['id']}/expenses",
            json={
                "expense_type": "delivery",
                "amount_byn": 25.0,
                "notes": "курьерская доставка",
            },
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["lead_id"] == lead["id"]
        assert body["expense_type"] == "delivery"
        assert body["amount_byn"] == 25.0
        assert body["notes"] == "курьерская доставка"


def test_list_expenses_happy_path() -> None:
    app = _make_app(USER_A)
    with TestClient(app) as client:
        lead = _create_lead(client)
        client.post(
            f"/api/v1/leads/{lead['id']}/expenses",
            json={"expense_type": "delivery", "amount_byn": 20.0},
        )
        client.post(
            f"/api/v1/leads/{lead['id']}/expenses",
            json={"expense_type": "repair", "amount_byn": 50.0},
        )
        resp = client.get(f"/api/v1/leads/{lead['id']}/expenses")
        assert resp.status_code == 200
        items = resp.json()
        assert len(items) == 2


def test_update_expense_happy_path() -> None:
    app = _make_app(USER_A)
    with TestClient(app) as client:
        lead = _create_lead(client)
        create_resp = client.post(
            f"/api/v1/leads/{lead['id']}/expenses",
            json={"expense_type": "other", "amount_byn": 10.0, "notes": "old note"},
        )
        expense_id = create_resp.json()["id"]
        resp = client.patch(
            f"/api/v1/leads/{lead['id']}/expenses/{expense_id}",
            json={"amount_byn": 15.0, "notes": "updated note"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["amount_byn"] == 15.0
        assert body["notes"] == "updated note"


def test_delete_expense_happy_path() -> None:
    app = _make_app(USER_A)
    with TestClient(app) as client:
        lead = _create_lead(client)
        create_resp = client.post(
            f"/api/v1/leads/{lead['id']}/expenses",
            json={"expense_type": "delivery", "amount_byn": 30.0},
        )
        expense_id = create_resp.json()["id"]
        resp = client.delete(f"/api/v1/leads/{lead['id']}/expenses/{expense_id}")
        assert resp.status_code == 204

        list_resp = client.get(f"/api/v1/leads/{lead['id']}/expenses")
        assert all(e["id"] != expense_id for e in list_resp.json())
