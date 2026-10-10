from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from fastapi.testclient import TestClient
from sqlalchemy import select

from api.middleware.telegram_auth import TelegramInitData
from api.models import Tracker, TrackerEvent, User


def fake_telegram_user() -> TelegramInitData:
    return TelegramInitData(
        user_id=123456,
        first_name="Test",
        raw={"user": '{"id": 123456, "first_name": "Test", "username": "tester"}'},
    )


def build_client() -> tuple[TestClient, object]:
    from api.dependencies import get_telegram_user
    from api.main import create_app

    app = create_app()
    app.dependency_overrides[get_telegram_user] = fake_telegram_user
    return TestClient(app), app


async def fetch_all(session_factory, statement) -> list:
    async with session_factory() as session:
        return list((await session.execute(statement)).scalars())


def test_trackers_crud() -> None:
    client, app = build_client()
    with client:
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
        # The query is not turned into a keyword filter (H2).
        assert created["config_keyword"] is None

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

        stored = asyncio.run(fetch_all(app.state.session_factory, select(Tracker)))
        assert len(stored) == 1
        assert stored[0].active is False
        assert stored[0].deleted_at is not None

        users = asyncio.run(fetch_all(app.state.session_factory, select(User)))
        assert [(user.telegram_user_id, user.first_name, user.username) for user in users] == [
            (123456, "Test", "tester")
        ]


def test_tracker_keeps_explicit_config_keyword() -> None:
    client, _ = build_client()
    with client:
        response = client.post(
            "/api/v1/trackers",
            json={"query": "iphone 15", "config_keyword": "  256  "},
        )
    assert response.status_code == 201
    assert response.json()["config_keyword"] == "256"


def test_duplicate_tracker_is_rejected() -> None:
    client, _ = build_client()
    with client:
        first = client.post("/api/v1/trackers", json={"query": "iphone 15", "strict_mode": True})
        assert first.status_code == 201

        # Same query after normalization, same mode and filters.
        duplicate = client.post(
            "/api/v1/trackers",
            json={"query": "  iPhone   15 ", "strict_mode": True},
        )
        assert duplicate.status_code == 409
        assert f"#{first.json()['id']}" in duplicate.json()["detail"]
        assert "уже есть" in duplicate.json()["detail"]

        other_mode = client.post("/api/v1/trackers", json={"query": "iphone 15"})
        assert other_mode.status_code == 201

        other_filters = client.post(
            "/api/v1/trackers",
            json={"query": "iphone 15", "strict_mode": True, "max_price_byn": 1500},
        )
        assert other_filters.status_code == 201

        assert len(client.get("/api/v1/trackers").json()) == 3

        # A deleted tracker no longer blocks the same configuration.
        assert client.delete(f"/api/v1/trackers/{first.json()['id']}").status_code == 204
        recreated = client.post(
            "/api/v1/trackers",
            json={"query": "iphone 15", "strict_mode": True},
        )
        assert recreated.status_code == 201


def test_tracker_quota(monkeypatch) -> None:
    from api.services import tracker_service

    monkeypatch.setattr(tracker_service, "MAX_ACTIVE_TRACKERS_PER_USER", 2)
    client, _ = build_client()
    with client:
        assert client.post("/api/v1/trackers", json={"query": "iphone 13"}).status_code == 201
        assert client.post("/api/v1/trackers", json={"query": "iphone 14"}).status_code == 201
        over_limit = client.post("/api/v1/trackers", json={"query": "iphone 15"})
        assert over_limit.status_code == 409
        assert "лимит" in over_limit.json()["detail"]

        tracker_id = client.get("/api/v1/trackers").json()[0]["id"]
        assert client.delete(f"/api/v1/trackers/{tracker_id}").status_code == 204
        assert client.post("/api/v1/trackers", json={"query": "iphone 15"}).status_code == 201


def test_tracker_query_validation() -> None:
    client, _ = build_client()
    with client:
        blank = client.post("/api/v1/trackers", json={"query": "   "})
        assert blank.status_code == 422

        punctuation_only = client.post("/api/v1/trackers", json={"query": "!!!"})
        assert punctuation_only.status_code == 422

        too_long = client.post("/api/v1/trackers", json={"query": "a" * 256})
        assert too_long.status_code == 422

        assert client.get("/api/v1/trackers").json() == []


def test_delete_tracker_id_bounds() -> None:
    client, _ = build_client()
    with client:
        assert client.delete("/api/v1/trackers/0").status_code == 422
        assert client.delete("/api/v1/trackers/99999999999").status_code == 422
        missing = client.delete("/api/v1/trackers/2147483647")
        assert missing.status_code == 404
        assert missing.json()["detail"] == "Трекер не найден"


async def seed_tracker_event(session_factory) -> None:
    async with session_factory() as session:
        session.add(
            TrackerEvent(
                tracker_id=1,
                user_id=123456,
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
    client, app = build_client()
    with client:
        asyncio.run(seed_tracker_event(app.state.session_factory))
        response = client.get("/api/v1/tracker-events")

    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 1
    assert payload[0]["event_type"] == "new_listing"
    assert payload[0]["strict_mode"] is True
    assert payload[0]["ad_id"] == 1


async def test_create_tracker_service_upserts_user_once() -> None:
    from api.database import get_engine, get_session_factory
    from api.services import tracker_service

    engine = get_engine()
    session_factory = get_session_factory(engine)
    async with session_factory() as session:
        await tracker_service.create_tracker(
            session, user_id=5_123_456_789, query="iphone 15", first_name="Anna"
        )
        await tracker_service.create_tracker(session, user_id=5_123_456_789, query="iphone 16")
        await session.commit()

    async with session_factory() as session:
        users = list((await session.execute(select(User))).scalars())
    assert [(user.telegram_user_id, user.first_name) for user in users] == [
        (5_123_456_789, "Anna")
    ]
    await engine.dispose()


def test_parse_db_id() -> None:
    from api.services.tracker_service import parse_db_id

    assert parse_db_id("12") == 12
    assert parse_db_id(" #12 ") == 12
    assert parse_db_id("2147483647") == 2_147_483_647
    for raw in (None, "", "abc", "#", "0", "-1", "1.5", "²", "١٢", "2147483648", "9" * 30):
        assert parse_db_id(raw) is None, raw
