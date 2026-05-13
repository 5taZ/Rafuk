from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.database import get_engine, get_session_factory
from api.middleware.telegram_auth import TelegramInitData
from api.models import Base, QueryListingState, Tracker, TrackerEvent
from api.services.history_service import QuerySyncResult, TrendReversal
from scheduler.collector import (
    _build_tracker_message,
    _recent_events_by_tracker,
    _recent_trend_event_tracker_ids,
    persist_tracker_events,
)

sys.path.insert(0, str(Path(__file__).parent))
from conftest import make_user


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
                "category_id": 17010,
                "category_label": "Мобильные телефоны",
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
        assert created["category_id"] == 17010
        assert created["category_label"] == "Мобильные телефоны"
        assert created["config_keyword"] is not None

        list_response = client.get("/api/v1/trackers")
        assert list_response.status_code == 200
        trackers = list_response.json()
        assert len(trackers) == 1
        assert trackers[0]["id"] == created["id"]
        assert trackers[0]["strict_mode"] is True
        assert trackers[0]["min_discount_percent"] == 12
        assert trackers[0]["category_id"] == 17010

        update_response = client.patch(
            f"/api/v1/trackers/{created['id']}",
            json={"category_id": None},
        )
        assert update_response.status_code == 200
        updated = update_response.json()
        assert updated["category_id"] is None
        assert updated["category_label"] is None

        # OPUS-18: changing to a real category_id without sending a
        # new label is rejected so we never end up with a stale label
        # alongside a different id.
        bad_update = client.patch(
            f"/api/v1/trackers/{created['id']}",
            json={"category_id": 2010},
        )
        assert bad_update.status_code == 422
        assert "category_label" in bad_update.json()["detail"]

        # Sending both is the supported atomic update.
        atomic_update = client.patch(
            f"/api/v1/trackers/{created['id']}",
            json={"category_id": 2010, "category_label": "Авто"},
        )
        assert atomic_update.status_code == 200
        atomic = atomic_update.json()
        assert atomic["category_id"] == 2010
        assert atomic["category_label"] == "Авто"

        delete_response = client.delete(f"/api/v1/trackers/{created['id']}")
        assert delete_response.status_code == 204

        list_response_after_delete = client.get("/api/v1/trackers")
        assert list_response_after_delete.status_code == 200
        assert list_response_after_delete.json() == []


async def seed_tracker_event(session_factory) -> None:
    async with session_factory() as session:
        # Create user with telegram_user_id matching the fake auth
        from api.services.workflow_store import ensure_user

        user_id = await ensure_user(session, telegram_user_id=123456, first_name="Test")
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


@pytest.mark.asyncio
async def test_tracker_events_endpoint() -> None:
    from api.dependencies import get_telegram_user
    from api.main import create_app

    app = create_app()
    app.dependency_overrides[get_telegram_user] = fake_telegram_user

    with TestClient(app) as client:
        await seed_tracker_event(app.state.session_factory)
        response = client.get("/api/v1/tracker-events")

    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 1
    assert payload[0]["event_type"] == "new_listing"
    assert payload[0]["strict_mode"] is True
    assert payload[0]["ad_id"] == 1


@pytest.mark.asyncio
async def test_recent_events_by_tracker_groups_by_id_and_skips_old() -> None:
    """One bulk SELECT for all due trackers — replaces the per-tracker N+1
    that used to live inside persist_tracker_events.

    Verifies grouping (each tracker only sees its own ad/event pairs) and
    the recency cutoff (events older than `hours` are filtered out)."""
    engine = get_engine()
    session_factory = get_session_factory(engine)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    async with session_factory() as session:
        user = make_user(telegram_user_id=999_999, first_name="Test")
        session.add(user)
        await session.flush()

        tracker_a = Tracker(user_id=user.id, query="iphone 15", strict_mode=False)
        tracker_b = Tracker(user_id=user.id, query="iphone 15", strict_mode=True)
        session.add_all([tracker_a, tracker_b])
        await session.flush()

        now = datetime.now(UTC)
        old = now - timedelta(hours=48)
        session.add_all(
            [
                TrackerEvent(
                    tracker_id=tracker_a.id,
                    user_id=user.id,
                    ad_id=1,
                    query="iphone 15",
                    strict_mode=False,
                    event_type="new_listing",
                    title="A",
                    link="https://www.kufar.by/item/1",
                    created_at=now,
                ),
                TrackerEvent(
                    tracker_id=tracker_a.id,
                    user_id=user.id,
                    ad_id=2,
                    query="iphone 15",
                    strict_mode=False,
                    event_type="price_drop",
                    title="A2",
                    link="https://www.kufar.by/item/2",
                    created_at=old,  # outside the 24h window
                ),
                TrackerEvent(
                    tracker_id=tracker_b.id,
                    user_id=user.id,
                    ad_id=3,
                    query="iphone 15",
                    strict_mode=True,
                    event_type="new_listing",
                    title="B",
                    link="https://www.kufar.by/item/3",
                    created_at=now,
                ),
            ]
        )
        await session.commit()

        seen = await _recent_events_by_tracker(session, [tracker_a.id, tracker_b.id])

    assert seen[tracker_a.id] == {(1, "new_listing")}, seen
    assert seen[tracker_b.id] == {(3, "new_listing")}, seen
    # Tracker A's old (48h ago) event must be filtered out by the cutoff.
    assert (2, "price_drop") not in seen[tracker_a.id]

    await engine.dispose()


def _trend_signal() -> TrendReversal:
    return TrendReversal(
        low_byn=700.0,
        today_byn=770.0,
        pre_high_byn=1000.0,
        decline_pct=30.0,
        rebound_pct=10.0,
        low_at=datetime(2026, 4, 5, tzinfo=UTC).date(),
    )


def test_build_tracker_message_includes_trend_block_only_when_not_sent_yet() -> None:
    sync = QuerySyncResult(stats_count=0, total_results=0)
    msg = _build_tracker_message(
        "iphone 13",
        False,
        sync,
        trend_signal=_trend_signal(),
        trend_already_sent=False,
    )
    assert msg is not None
    assert "Цена снова растёт" in msg
    assert "30.0" in msg
    assert "10.0" in msg
    # Already sent → trend block suppressed → message is None when there
    # are no other events to report.
    assert (
        _build_tracker_message(
            "iphone 13",
            False,
            sync,
            trend_signal=_trend_signal(),
            trend_already_sent=True,
        )
        is None
    )


@pytest.mark.asyncio
async def test_persist_tracker_events_creates_trend_reversal_with_query_metadata() -> None:
    engine = get_engine()
    session_factory = get_session_factory(engine)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    async with session_factory() as session:
        user = make_user(telegram_user_id=11_111, first_name="Trend")
        session.add(user)
        await session.flush()
        tracker = Tracker(user_id=user.id, query="iphone 13", strict_mode=False)
        session.add(tracker)
        await session.flush()

        sync = QuerySyncResult(stats_count=0, total_results=0)
        events = await persist_tracker_events(
            session,
            tracker,
            sync,
            ads_by_id={},
            seen=set(),
            trend_signal=_trend_signal(),
            trend_already_sent=False,
        )
        await session.commit()

        assert len(events) == 1
        evt = events[0]
        assert evt.event_type == "trend_reversal"
        assert evt.ad_id is None
        assert evt.parameters is not None
        assert evt.parameters["low_byn"] == 700.0
        assert evt.parameters["today_byn"] == 770.0
        assert evt.parameters["decline_pct"] == 30.0
        assert evt.parameters["rebound_pct"] == 10.0
        assert evt.parameters["low_at"] == "2026-04-05"
        # Title hard-truncates to 255 chars but should fit comfortably here.
        assert "10.0%" in evt.title
        assert "30.0%" in evt.title

        # 24h debounce: another call with trend_already_sent=True must not
        # add a duplicate event.
        more = await persist_tracker_events(
            session,
            tracker,
            sync,
            ads_by_id={},
            seen=set(),
            trend_signal=_trend_signal(),
            trend_already_sent=True,
        )
        assert more == []

    await engine.dispose()


@pytest.mark.asyncio
async def test_persist_tracker_events_accepts_long_kufar_link() -> None:
    engine = get_engine()
    session_factory = get_session_factory(engine)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    async with session_factory() as session:
        user = make_user(telegram_user_id=33_333, first_name="Longlink")
        session.add(user)
        await session.flush()
        tracker = Tracker(user_id=user.id, query="iphone 13", strict_mode=False)
        session.add(tracker)
        await session.flush()
        now = datetime.now(UTC)
        long_link = "https://www.kufar.by/item/long?" + ("utm_campaign=rafuk&" * 40)
        state = QueryListingState(
            query=tracker.query,
            ad_id=77_777,
            title="iPhone 13 long link",
            link=long_link,
            last_price_byn=1000,
            first_seen_at=now,
            last_seen_at=now,
        )

        events = await persist_tracker_events(
            session,
            tracker,
            QuerySyncResult(stats_count=1, total_results=1, new_listings=[state]),
            ads_by_id={},
            seen=set(),
        )
        await session.commit()

        assert len(long_link) > 512
        assert len(long_link) <= 2048
        assert len(events) == 1
        assert events[0].link == long_link

    await engine.dispose()


@pytest.mark.asyncio
async def test_recent_trend_event_tracker_ids_within_window() -> None:
    engine = get_engine()
    session_factory = get_session_factory(engine)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    async with session_factory() as session:
        user = make_user(telegram_user_id=22_222, first_name="Window")
        session.add(user)
        await session.flush()
        tracker_recent = Tracker(user_id=user.id, query="ps5", strict_mode=False)
        tracker_old = Tracker(user_id=user.id, query="macbook", strict_mode=False)
        tracker_other_event = Tracker(user_id=user.id, query="iphone", strict_mode=False)
        session.add_all([tracker_recent, tracker_old, tracker_other_event])
        await session.flush()

        now = datetime.now(UTC)
        session.add_all(
            [
                TrackerEvent(
                    tracker_id=tracker_recent.id,
                    user_id=user.id,
                    ad_id=None,
                    query="ps5",
                    strict_mode=False,
                    event_type="trend_reversal",
                    title="Цена ↑",
                    link="",
                    created_at=now,
                ),
                TrackerEvent(
                    tracker_id=tracker_old.id,
                    user_id=user.id,
                    ad_id=None,
                    query="macbook",
                    strict_mode=False,
                    event_type="trend_reversal",
                    title="Цена ↑",
                    link="",
                    created_at=now - timedelta(hours=48),
                ),
                TrackerEvent(
                    tracker_id=tracker_other_event.id,
                    user_id=user.id,
                    ad_id=42,
                    query="iphone",
                    strict_mode=False,
                    event_type="price_drop",
                    title="Drop",
                    link="https://www.kufar.by/item/42",
                    created_at=now,
                ),
            ]
        )
        await session.commit()

        ids = await _recent_trend_event_tracker_ids(
            session,
            [tracker_recent.id, tracker_old.id, tracker_other_event.id],
        )

    assert ids == {tracker_recent.id}
    await engine.dispose()
