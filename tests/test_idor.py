from __future__ import annotations

from fastapi.testclient import TestClient

from api.middleware.telegram_auth import TelegramInitData

USER_A = TelegramInitData(user_id=111111, first_name="Alice", raw={})
USER_B = TelegramInitData(user_id=222222, first_name="Bob", raw={})


def _make_app_with_user(user: TelegramInitData):
    from api.dependencies import get_telegram_user
    from api.main import create_app

    app = create_app()
    app.dependency_overrides[get_telegram_user] = lambda: user
    return app


def _create_lead_as_user_a() -> dict:
    app = _make_app_with_user(USER_A)
    with TestClient(app) as client:
        resp = client.post(
            "/api/v1/leads",
            json={
                "query": "iphone 15",
                "ad_id": 9001,
                "title": "iPhone 15 128GB",
                "link": "https://www.kufar.by/item/9001",
                "price_byn": 2000,
                "source": "manual",
            },
        )
        assert resp.status_code == 201
        return resp.json()


def test_user_b_cannot_get_user_a_lead() -> None:
    lead = _create_lead_as_user_a()

    app = _make_app_with_user(USER_B)
    with TestClient(app) as client:
        resp = client.get("/api/v1/leads")
        assert resp.status_code == 200
        assert all(item["id"] != lead["id"] for item in resp.json())


def test_user_b_cannot_patch_user_a_lead() -> None:
    lead = _create_lead_as_user_a()

    app = _make_app_with_user(USER_B)
    with TestClient(app) as client:
        resp = client.patch(
            f"/api/v1/leads/{lead['id']}",
            json={"status": "in_progress"},
        )
        assert resp.status_code == 404


def test_user_b_cannot_delete_user_a_lead() -> None:
    lead = _create_lead_as_user_a()

    app = _make_app_with_user(USER_B)
    with TestClient(app) as client:
        resp = client.delete(f"/api/v1/leads/{lead['id']}")
        assert resp.status_code == 404


def test_user_a_lead_still_exists_after_user_b_attempts() -> None:
    lead = _create_lead_as_user_a()

    app_b = _make_app_with_user(USER_B)
    with TestClient(app_b) as client:
        client.patch(f"/api/v1/leads/{lead['id']}", json={"status": "sold"})
        client.delete(f"/api/v1/leads/{lead['id']}")

    app_a = _make_app_with_user(USER_A)
    with TestClient(app_a) as client:
        resp = client.get("/api/v1/leads")
        leads = resp.json()
        assert any(item["id"] == lead["id"] for item in leads)
        assert next(item for item in leads if item["id"] == lead["id"])["status"] == "new"


def test_user_b_cannot_delete_user_a_tracker() -> None:
    app_a = _make_app_with_user(USER_A)
    with TestClient(app_a) as client:
        create_resp = client.post(
            "/api/v1/trackers",
            json={"query": "ps5 console"},
        )
        assert create_resp.status_code == 201
        tracker_id = create_resp.json()["id"]

    app_b = _make_app_with_user(USER_B)
    with TestClient(app_b) as client:
        resp = client.delete(f"/api/v1/trackers/{tracker_id}")
        assert resp.status_code == 404


def test_user_b_cannot_patch_user_a_tracker() -> None:
    app_a = _make_app_with_user(USER_A)
    with TestClient(app_a) as client:
        create_resp = client.post(
            "/api/v1/trackers",
            json={"query": "xbox series x"},
        )
        assert create_resp.status_code == 201
        tracker_id = create_resp.json()["id"]

    app_b = _make_app_with_user(USER_B)
    with TestClient(app_b) as client:
        resp = client.patch(
            f"/api/v1/trackers/{tracker_id}",
            json={"query": "stolen query"},
        )
        assert resp.status_code == 404


def test_user_b_cannot_access_user_a_expense() -> None:
    app_a = _make_app_with_user(USER_A)
    with TestClient(app_a) as client:
        lead_resp = client.post(
            "/api/v1/leads",
            json={
                "query": "macbook air m3",
                "ad_id": 9002,
                "title": "MacBook Air M3",
                "link": "https://www.kufar.by/item/9002",
                "price_byn": 2500,
                "source": "manual",
            },
        )
        assert lead_resp.status_code == 201
        lead_id = lead_resp.json()["id"]

        expense_resp = client.post(
            f"/api/v1/leads/{lead_id}/expenses",
            json={
                "expense_type": "delivery",
                "amount_byn": 50.0,
                "notes": "курьер",
            },
        )
        assert expense_resp.status_code == 201
        expense_id = expense_resp.json()["id"]

    app_b = _make_app_with_user(USER_B)
    with TestClient(app_b) as client:
        list_resp = client.get(f"/api/v1/leads/{lead_id}/expenses")
        assert list_resp.status_code == 404

        patch_resp = client.patch(
            f"/api/v1/leads/{lead_id}/expenses/{expense_id}",
            json={"amount_byn": 9999},
        )
        assert patch_resp.status_code == 404

        delete_resp = client.delete(f"/api/v1/leads/{lead_id}/expenses/{expense_id}")
        assert delete_resp.status_code == 404


def test_user_a_expense_unchanged_after_user_b_attempts() -> None:
    app_a = _make_app_with_user(USER_A)
    with TestClient(app_a) as client:
        lead_resp = client.post(
            "/api/v1/leads",
            json={
                "query": "airpods pro",
                "ad_id": 9003,
                "title": "AirPods Pro 2",
                "link": "https://www.kufar.by/item/9003",
                "price_byn": 400,
                "source": "manual",
            },
        )
        assert lead_resp.status_code == 201
        lead_id = lead_resp.json()["id"]

        expense_resp = client.post(
            f"/api/v1/leads/{lead_id}/expenses",
            json={
                "expense_type": "repair",
                "amount_byn": 30.0,
                "notes": "замена амбушюр",
            },
        )
        assert expense_resp.status_code == 201
        expense_id = expense_resp.json()["id"]

    app_b = _make_app_with_user(USER_B)
    with TestClient(app_b) as client:
        client.patch(
            f"/api/v1/leads/{lead_id}/expenses/{expense_id}",
            json={"amount_byn": 0},
        )
        client.delete(f"/api/v1/leads/{lead_id}/expenses/{expense_id}")

    app_a = _make_app_with_user(USER_A)
    with TestClient(app_a) as client:
        resp = client.get(f"/api/v1/leads/{lead_id}/expenses")
        assert resp.status_code == 200
        expenses = resp.json()
        assert len(expenses) == 1
        assert expenses[0]["id"] == expense_id
        assert expenses[0]["amount_byn"] == 30.0


def test_user_b_cannot_delete_user_a_watchlist_item() -> None:
    app_a = _make_app_with_user(USER_A)
    with TestClient(app_a) as client:
        wl_resp = client.post(
            "/api/v1/watchlist",
            json={
                "query": "samsung galaxy s25",
                "ad_id": 9004,
                "title": "Samsung Galaxy S25",
                "link": "https://www.kufar.by/item/9004",
                "price_byn": 1800,
            },
        )
        assert wl_resp.status_code == 201
        wl_id = wl_resp.json()["id"]

    app_b = _make_app_with_user(USER_B)
    with TestClient(app_b) as client:
        resp = client.delete(f"/api/v1/watchlist/{wl_id}")
        assert resp.status_code == 204

    app_a = _make_app_with_user(USER_A)
    with TestClient(app_a) as client:
        resp = client.get("/api/v1/watchlist")
        items = resp.json()
        assert any(w["id"] == wl_id for w in items)


def test_user_b_cannot_patch_user_a_watchlist_item() -> None:
    app_a = _make_app_with_user(USER_A)
    with TestClient(app_a) as client:
        wl_resp = client.post(
            "/api/v1/watchlist",
            json={
                "query": "pixel 9 pro",
                "ad_id": 9005,
                "title": "Google Pixel 9 Pro",
                "link": "https://www.kufar.by/item/9005",
                "price_byn": 1500,
            },
        )
        assert wl_resp.status_code == 201
        wl_id = wl_resp.json()["id"]

    app_b = _make_app_with_user(USER_B)
    with TestClient(app_b) as client:
        resp = client.patch(
            f"/api/v1/watchlist/{wl_id}",
            json={"notes": "hijacked"},
        )
        assert resp.status_code == 404

    app_a = _make_app_with_user(USER_A)
    with TestClient(app_a) as client:
        resp = client.get("/api/v1/watchlist")
        items = resp.json()
        item = next(w for w in items if w["id"] == wl_id)
        assert item["notes"] is None


def test_user_b_cannot_see_user_a_watchlist() -> None:
    app_a = _make_app_with_user(USER_A)
    with TestClient(app_a) as client:
        client.post(
            "/api/v1/watchlist",
            json={
                "query": "nokia 3310",
                "ad_id": 9006,
                "title": "Nokia 3310",
                "link": "https://www.kufar.by/item/9006",
                "price_byn": 100,
            },
        )

    app_b = _make_app_with_user(USER_B)
    with TestClient(app_b) as client:
        resp = client.get("/api/v1/watchlist")
        assert resp.status_code == 200
        assert resp.json() == []


def test_user_b_cannot_see_user_a_tracker_events() -> None:
    import asyncio

    from api.database import get_engine, get_session_factory
    from api.models import Base, Tracker, TrackerEvent, User

    app_a = _make_app_with_user(USER_A)
    engine = get_engine()

    async def _seed():
        session_factory = get_session_factory(engine)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with session_factory() as session:
            user = User(telegram_user_id=111111, first_name="Alice")
            session.add(user)
            await session.flush()
            tracker = Tracker(user_id=user.id, query="iphone se", strict_mode=False)
            session.add(tracker)
            await session.flush()
            from datetime import UTC, datetime

            session.add(
                TrackerEvent(
                    tracker_id=tracker.id,
                    user_id=user.id,
                    ad_id=777,
                    query="iphone se",
                    strict_mode=False,
                    event_type="new_listing",
                    title="iPhone SE",
                    link="https://www.kufar.by/item/777",
                    price_byn=500.0,
                    created_at=datetime.now(UTC),
                )
            )
            await session.commit()
            return tracker.id

    tracker_id = asyncio.run(_seed())

    with TestClient(app_a) as client:
        resp_a = client.get("/api/v1/tracker-events", params={"tracker_id": tracker_id})
        assert resp_a.status_code == 200
        assert len(resp_a.json()) >= 1

    app_b = _make_app_with_user(USER_B)
    with TestClient(app_b) as client:
        resp_b = client.get("/api/v1/tracker-events", params={"tracker_id": tracker_id})
        assert resp_b.status_code == 200
        assert resp_b.json() == []

    asyncio.run(engine.dispose())
