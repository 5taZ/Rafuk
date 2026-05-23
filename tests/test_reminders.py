from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from api.middleware.telegram_auth import TelegramInitData

USER_A = TelegramInitData(user_id=111111, first_name="Alice", raw={})
USER_B = TelegramInitData(user_id=222222, first_name="Bob", raw={})


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
            "query": "iphone 15",
            "ad_id": 8001,
            "title": "iPhone 15 128GB",
            "link": "https://www.kufar.by/item/8001",
            "price_byn": 2000,
            "source": "manual",
        },
    )
    assert resp.status_code == 201
    return resp.json()


def test_create_reminder_returns_201() -> None:
    app = _make_app(USER_A)
    with TestClient(app) as client:
        lead = _create_lead(client)
        remind_at = (datetime.now(UTC) + timedelta(hours=2)).isoformat()
        resp = client.post(
            f"/api/v1/leads/{lead['id']}/reminders",
            json={"remind_at": remind_at, "message": "Check listing"},
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["lead_id"] == lead["id"]
        assert body["message"] == "Check listing"
        assert body["sent"] is False


def test_create_reminder_rejects_past_remind_at() -> None:
    app = _make_app(USER_A)
    with TestClient(app) as client:
        lead = _create_lead(client)
        remind_at = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
        resp = client.post(
            f"/api/v1/leads/{lead['id']}/reminders",
            json={"remind_at": remind_at, "message": "Too late"},
        )
        assert resp.status_code == 422


def test_create_reminder_rejects_naive_remind_at() -> None:
    app = _make_app(USER_A)
    with TestClient(app) as client:
        lead = _create_lead(client)
        remind_at = (datetime.now(UTC) + timedelta(hours=1)).replace(tzinfo=None).isoformat()
        resp = client.post(
            f"/api/v1/leads/{lead['id']}/reminders",
            json={"remind_at": remind_at},
        )
        assert resp.status_code == 422


def test_create_reminder_rejects_extreme_future_remind_at() -> None:
    app = _make_app(USER_A)
    with TestClient(app) as client:
        lead = _create_lead(client)
        remind_at = (datetime.now(UTC) + timedelta(days=367)).isoformat()
        resp = client.post(
            f"/api/v1/leads/{lead['id']}/reminders",
            json={"remind_at": remind_at},
        )
        assert resp.status_code == 422


def test_get_reminders_returns_list() -> None:
    app = _make_app(USER_A)
    with TestClient(app) as client:
        lead = _create_lead(client)
        remind_at = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
        client.post(
            f"/api/v1/leads/{lead['id']}/reminders",
            json={"remind_at": remind_at},
        )
        resp = client.get(f"/api/v1/leads/{lead['id']}/reminders")
        assert resp.status_code == 200
        assert len(resp.json()) >= 1


def test_get_reminders_paginates() -> None:
    """PR-09: GET /reminders is paginated. Default ``limit=100`` caps
    the response so a power user with thousands of reminders on one
    lead can't 10-MB-flood every list call. ``offset`` walks the
    rest of the list deterministically (sorted by remind_at asc,
    id asc).
    """
    app = _make_app(USER_A)
    with TestClient(app) as client:
        lead = _create_lead(client)
        base = datetime.now(UTC) + timedelta(hours=1)
        for i in range(7):
            client.post(
                f"/api/v1/leads/{lead['id']}/reminders",
                json={
                    "remind_at": (base + timedelta(minutes=i)).isoformat(),
                    "message": f"reminder-{i}",
                },
            )
        # First page caps at the requested limit.
        first = client.get(
            f"/api/v1/leads/{lead['id']}/reminders?limit=3&offset=0"
        )
        assert first.status_code == 200
        first_body = first.json()
        assert len(first_body) == 3
        # Second page picks up where the first stopped — no overlap.
        second = client.get(
            f"/api/v1/leads/{lead['id']}/reminders?limit=3&offset=3"
        )
        assert second.status_code == 200
        second_body = second.json()
        assert len(second_body) == 3
        first_ids = {r["id"] for r in first_body}
        second_ids = {r["id"] for r in second_body}
        assert first_ids.isdisjoint(second_ids)


def test_get_reminders_rejects_invalid_pagination() -> None:
    """PR-09: bounds enforced by FastAPI's Query validation."""
    app = _make_app(USER_A)
    with TestClient(app) as client:
        lead = _create_lead(client)
        # limit > 500 → 422
        too_big = client.get(
            f"/api/v1/leads/{lead['id']}/reminders?limit=501"
        )
        assert too_big.status_code == 422
        # negative offset → 422
        negative = client.get(
            f"/api/v1/leads/{lead['id']}/reminders?offset=-1"
        )
        assert negative.status_code == 422


def test_delete_reminder_returns_204() -> None:
    app = _make_app(USER_A)
    with TestClient(app) as client:
        lead = _create_lead(client)
        remind_at = (datetime.now(UTC) + timedelta(hours=3)).isoformat()
        create_resp = client.post(
            f"/api/v1/leads/{lead['id']}/reminders",
            json={"remind_at": remind_at, "message": "Delete me"},
        )
        reminder_id = create_resp.json()["id"]
        resp = client.delete(f"/api/v1/leads/{lead['id']}/reminders/{reminder_id}")
        assert resp.status_code == 204


def test_create_reminder_nonexistent_lead_returns_404() -> None:
    app = _make_app(USER_A)
    with TestClient(app) as client:
        remind_at = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
        resp = client.post(
            "/api/v1/leads/999999/reminders",
            json={"remind_at": remind_at},
        )
        assert resp.status_code == 404


def test_idor_user_b_cannot_see_user_a_reminders() -> None:
    app_a = _make_app(USER_A)
    reminder_id: int | None = None
    lead_id: int | None = None
    with TestClient(app_a) as client:
        lead = _create_lead(client)
        lead_id = lead["id"]
        remind_at = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
        create_resp = client.post(
            f"/api/v1/leads/{lead_id}/reminders",
            json={"remind_at": remind_at, "message": "Private reminder"},
        )
        assert create_resp.status_code == 201
        reminder_id = create_resp.json()["id"]

    app_b = _make_app(USER_B)
    with TestClient(app_b) as client:
        list_resp = client.get(f"/api/v1/leads/{lead_id}/reminders")
        assert list_resp.status_code == 404

        delete_resp = client.delete(f"/api/v1/leads/{lead_id}/reminders/{reminder_id}")
        assert delete_resp.status_code == 404


def test_reminders_offset_cap_rejects_over_10000() -> None:
    """G-02: offset > 10_000 returns 422."""
    app = _make_app(USER_A)
    with TestClient(app) as client:
        lead = _create_lead(client)
        resp = client.get(f"/api/v1/leads/{lead['id']}/reminders", params={"offset": 10001})
    assert resp.status_code == 422
