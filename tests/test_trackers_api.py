from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from api.middleware.telegram_auth import TelegramInitData
from api.models import TrackerEvent


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
            json={
                "query": "iphone 15",
                "strict_mode": True,
                "interval_min": 30,
                "min_discount_percent": 12,
                "max_price_byn": 2200,
                "seller_type": "Частное лицо",
                "exclude_duplicates": True,
            },
        )
        assert create_response.status_code == 201
        created = create_response.json()
        assert created["query"] == "iphone 15"
        assert created["strict_mode"] is True
        assert created["interval_min"] == 30
        assert created["min_discount_percent"] == 12
        assert created["max_price_byn"] == 2200
        assert created["seller_type"] == "Частное лицо"
        assert created["exclude_duplicates"] is True
        assert created["config_keyword"] is not None

        list_response = client.get("/api/v1/trackers")
        assert list_response.status_code == 200
        trackers = list_response.json()
        assert len(trackers) == 1
        assert trackers[0]["id"] == created["id"]
        assert trackers[0]["strict_mode"] is True
        assert trackers[0]["min_discount_percent"] == 12

        delete_response = client.delete(f"/api/v1/trackers/{created['id']}")
        assert delete_response.status_code == 204

        list_response_after_delete = client.get("/api/v1/trackers")
        assert list_response_after_delete.status_code == 200
        assert list_response_after_delete.json() == []


async def seed_tracker_event(session_factory) -> None:
    async with session_factory() as session:
        # Create user with telegram_user_id matching the fake auth
        from api.services.workflow_store import ensure_user

        user_id = await ensure_user(
            session, telegram_user_id=123456, first_name="Test"
        )
        await session.commit()

        session.add(
            TrackerEvent(
                tracker_id=1,
                user_id=user_id,
                ad_id=1,
                query="iphone 15",
                strict_mode=True,
                event_type="new_listing",
                title="iPhone 15 256GB",
                link="https://www.kufar.by/item/1",
                price_byn=2400.0,
                created_at=datetime.now(UTC),
            )
        )
        await session.commit()


def test_tracker_events_endpoint() -> None:
    from api.dependencies import get_telegram_user
    from api.main import create_app

    app = create_app()
    app.dependency_overrides[get_telegram_user] = fake_telegram_user

    with TestClient(app) as client:
        asyncio.run(seed_tracker_event(app.state.session_factory))
        response = client.get("/api/v1/tracker-events")

    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 1
    assert payload[0]["event_type"] == "new_listing"
    assert payload[0]["strict_mode"] is True
    assert payload[0]["ad_id"] == 1
