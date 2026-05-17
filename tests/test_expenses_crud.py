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


def test_create_expense_accepts_full_contract_type_set() -> None:
    app = _make_app(USER_A)
    with TestClient(app) as client:
        lead = _create_lead(client)
        for expense_type in (
            "delivery",
            "repair",
            "customs",
            "packaging",
            "transport",
            "other",
        ):
            resp = client.post(
                f"/api/v1/leads/{lead['id']}/expenses",
                json={"expense_type": expense_type, "amount_byn": 10.0},
            )
            assert resp.status_code == 201, resp.text
            assert resp.json()["expense_type"] == expense_type


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


def test_update_expense_can_clear_notes_with_null() -> None:
    # A-2: PATCH with {"notes": null} must clear the notes field.
    # The previous ``if payload.notes is not None`` guard silently
    # ignored the null and left the old note in place.
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
            json={"notes": None},
        )
        assert resp.status_code == 200
        assert resp.json()["notes"] is None


def test_update_expense_omitted_field_is_unchanged() -> None:
    # Regression: omitting a field must NOT clear it. Only an
    # explicit ``null`` should clear (and only for nullable fields).
    app = _make_app(USER_A)
    with TestClient(app) as client:
        lead = _create_lead(client)
        create_resp = client.post(
            f"/api/v1/leads/{lead['id']}/expenses",
            json={"expense_type": "other", "amount_byn": 10.0, "notes": "kept"},
        )
        expense_id = create_resp.json()["id"]
        resp = client.patch(
            f"/api/v1/leads/{lead['id']}/expenses/{expense_id}",
            json={"amount_byn": 12.0},  # notes intentionally absent
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["amount_byn"] == 12.0
        assert body["notes"] == "kept"
