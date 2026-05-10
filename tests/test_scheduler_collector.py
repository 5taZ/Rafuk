"""Tests for scheduler/collector.py"""
from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from api.database import get_engine, get_session_factory
from api.models import Base, LeadItem, LeadReminder, QueryListingState, Tracker, TrackerEvent
from api.services.history_service import QuerySyncResult, TrendReversal
from scheduler.collector import (
    _build_new_listing_message,
    _build_price_drop_message,
    _build_threshold_message,
    _build_tracker_message,
    _format_price_byn,
    _recent_event_keys,
    _recent_events_by_tracker,
    _recent_trend_event_tracker_ids,
    check_reminders,
    cleanup_ai_audit_log,
    cleanup_inactive_listing_states,
    cleanup_old_events,
    cleanup_old_snapshots,
    cleanup_stale_missing_watchlist,
    notify_user,
    persist_tracker_events,
)

sys.path.insert(0, str(Path(__file__).parent))
from conftest import make_user


def _make_tg_error(exc_cls, message="error"):
    """Create a Telegram error with required method arg."""
    mock_method = MagicMock()
    return exc_cls(method=mock_method, message=message)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_bot() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def mock_session() -> AsyncMock:
    session = AsyncMock(spec=AsyncSession)
    return session


@pytest.fixture
async def populated_session():
    """Create a real in-memory DB session with a user and tracker for tests."""
    engine = get_engine()
    session_factory = get_session_factory(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with session_factory() as session:
        user = make_user(telegram_user_id=123456, first_name="Test")
        session.add(user)
        await session.flush()

        tracker = Tracker(user_id=user.id, query="iphone 15", strict_mode=False)
        session.add(tracker)
        await session.flush()

        yield session, user, tracker

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


# ---------------------------------------------------------------------------
# Test _format_price_byn
# ---------------------------------------------------------------------------

def test_format_price_byn_none():
    # None price with no price_type -> negotiable (default for empty)
    assert _format_price_byn(None) == "договорная"


def test_format_price_byn_none_negotiable():
    assert _format_price_byn(None, price_type="negotiable") == "договорная"


def test_format_price_byn_none_free():
    assert _format_price_byn(None, price_type="free") == "бесплатно"


def test_format_price_byn_zero():
    # value=0 with no price_type -> "бесплатно" (zero is treated as free by default)
    assert _format_price_byn(0) == "бесплатно"


def test_format_price_byn_zero_negotiable():
    assert _format_price_byn(0, price_type="negotiable") == "договорная"


def test_format_price_byn_zero_free():
    assert _format_price_byn(0, price_type="free") == "бесплатно"


def test_format_price_byn_small():
    assert _format_price_byn(150) == "150 р."


def test_format_price_byn_large():
    result = _format_price_byn(2500)
    assert "тыс. р." in result


def test_format_price_byn_large_rounding():
    # 1000 -> 1.0 тыс. р.
    result = _format_price_byn(1000)
    assert "тыс. р." in result


# ---------------------------------------------------------------------------
# Test _build_tracker_message
# ---------------------------------------------------------------------------

def test_build_tracker_message_no_events_returns_none():
    sync = QuerySyncResult(stats_count=0, total_results=0)
    assert _build_tracker_message("iphone 15", False, sync) is None


def test_build_tracker_message_new_listings():
    now = datetime.now(UTC)
    state = QueryListingState(
        ad_id=1,
        query="iphone 15",
        title="iPhone 15 256GB",
        last_price_byn=2000.0,
        link="https://www.kufar.by/item/1",
        active=True,
        first_seen_at=now,
        last_seen_at=now,
    )
    sync = QuerySyncResult(
        stats_count=1, total_results=1, new_listings=[state], price_drops=[]
    )
    msg = _build_tracker_message("iphone 15", False, sync)
    assert msg is not None
    assert "Новые объявления: 1" in msg
    assert "iPhone 15 256GB" in msg


def test_build_tracker_message_price_drops():
    now = datetime.now(UTC)
    state = QueryListingState(
        ad_id=2,
        query="iphone 15",
        title="iPhone 15 Pro",
        last_price_byn=1800.0,
        link="https://www.kufar.by/item/2",
        active=True,
        first_seen_at=now,
        last_seen_at=now,
    )
    sync = QuerySyncResult(
        stats_count=1, total_results=1, new_listings=[], price_drops=[(state, 200.0)]
    )
    msg = _build_tracker_message("iphone 15", False, sync)
    assert msg is not None
    assert "Снижение цены: 1" in msg


def test_build_tracker_message_strict_mode():
    sync = QuerySyncResult(stats_count=0, total_results=0)
    msg = _build_tracker_message("iphone 15", True, sync)
    assert msg is None


def test_build_tracker_message_strict_mode_with_events():
    now = datetime.now(UTC)
    state = QueryListingState(
        ad_id=1,
        query="iphone 15",
        title="iPhone 15",
        last_price_byn=2000.0,
        link="https://www.kufar.by/item/1",
        active=True,
        first_seen_at=now,
        last_seen_at=now,
    )
    sync = QuerySyncResult(
        stats_count=1, total_results=1, new_listings=[state], price_drops=[]
    )
    msg = _build_tracker_message("iphone 15", True, sync)
    assert msg is not None
    assert "строгий" in msg


# ---------------------------------------------------------------------------
# Test _build_new_listing_message
# ---------------------------------------------------------------------------

def test_build_new_listing_message_basic():
    now = datetime.now(UTC)
    state = QueryListingState(
        ad_id=1,
        query="iphone 15",
        title="iPhone 15 256GB",
        last_price_byn=2000.0,
        link="https://www.kufar.by/item/1",
        active=True,
        first_seen_at=now,
        last_seen_at=now,
    )
    msg = _build_new_listing_message(state)
    assert "НОВЫЙ ЛОТ" in msg
    assert "2 тыс. р." in msg


def test_build_new_listing_message_with_median():
    now = datetime.now(UTC)
    state = QueryListingState(
        ad_id=1,
        query="iphone 15",
        title="iPhone 15 256GB",
        last_price_byn=2000.0,
        link="https://www.kufar.by/item/1",
        active=True,
        first_seen_at=now,
        last_seen_at=now,
    )
    msg = _build_new_listing_message(state, median_byn=2200.0, discount_pct=10.0, liquidity="High")
    assert "НОВЫЙ ЛОТ" in msg
    assert "медиана" in msg
    assert "-10%" in msg
    assert "High" in msg


# ---------------------------------------------------------------------------
# Test _build_price_drop_message
# ---------------------------------------------------------------------------

def test_build_price_drop_message_basic():
    now = datetime.now(UTC)
    state = QueryListingState(
        ad_id=2,
        query="iphone 15",
        title="iPhone 15 Pro",
        last_price_byn=1800.0,
        link="https://www.kufar.by/item/2",
        active=True,
        first_seen_at=now,
        last_seen_at=now,
    )
    msg = _build_price_drop_message(state, 200.0)
    assert "СНИЖЕНИЕ ЦЕНЫ" in msg
    assert "1.8 тыс. р." in msg


def test_build_price_drop_message_with_median():
    now = datetime.now(UTC)
    state = QueryListingState(
        ad_id=2,
        query="iphone 15",
        title="iPhone 15 Pro",
        last_price_byn=1800.0,
        link="https://www.kufar.by/item/2",
        active=True,
        first_seen_at=now,
        last_seen_at=now,
    )
    msg = _build_price_drop_message(state, 200.0, median_byn=2200.0, discount_pct=10.0)
    assert "СНИЖЕНИЕ ЦЕНЫ" in msg
    assert "медиана" in msg
    assert "-10%" in msg


# ---------------------------------------------------------------------------
# Test _build_threshold_message
# ---------------------------------------------------------------------------

def test_build_threshold_message_empty_returns_none():
    assert _build_threshold_message(MagicMock(), []) is None


def test_build_threshold_message_price_alerts():

    tracker = MagicMock()
    tracker.query = "iphone 15"
    tracker.strict_mode = False

    event = MagicMock()
    event.event_type = "price_threshold_alert"
    event.price_byn = 1500.0
    event.title = "iPhone 15"
    event.link = "https://www.kufar.by/item/1"
    event.parameters = {"threshold": 1500.0}

    msg = _build_threshold_message(tracker, [event])
    assert msg is not None
    assert "ПОРОГ ЦЕНЫ" in msg


def test_build_threshold_message_discount_alerts():
    tracker = MagicMock()
    tracker.query = "iphone 15"
    tracker.strict_mode = False

    event = MagicMock()
    event.event_type = "discount_alert"
    event.price_byn = 1800.0
    event.title = "iPhone 15"
    event.link = "https://www.kufar.by/item/1"
    event.delta_byn = 15.0
    event.parameters = {"discount_percent": 15.0, "median_byn": 2200.0}

    msg = _build_threshold_message(tracker, [event])
    assert msg is not None
    assert "СКИДКА ОТ МЕДИАНЫ" in msg


# ---------------------------------------------------------------------------
# Test notify_user
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_notify_user_success(mock_bot: AsyncMock, populated_session):
    session, user, tracker = populated_session
    result = await notify_user(
        mock_bot,
        telegram_user_id=123456,
        message="Hello",
        session=session,
        internal_user_id=user.id,
    )
    assert result is True
    mock_bot.send_message.assert_called_once_with(123456, "Hello", reply_markup=None)


@pytest.mark.asyncio
async def test_notify_user_forbidden_deactivates_tracker(mock_bot: AsyncMock, populated_session):
    from aiogram.exceptions import TelegramForbiddenError

    session, user, tracker = populated_session
    mock_bot.send_message.side_effect = _make_tg_error(TelegramForbiddenError, "Forbidden")

    result = await notify_user(
        mock_bot,
        telegram_user_id=123456,
        message="Hello",
        session=session,
        internal_user_id=user.id,
    )
    assert result is False


@pytest.mark.asyncio
async def test_notify_user_not_found_deactivates_tracker(mock_bot: AsyncMock, populated_session):
    from aiogram.exceptions import TelegramNotFound

    session, user, tracker = populated_session
    mock_bot.send_message.side_effect = _make_tg_error(TelegramNotFound, "Not found")

    result = await notify_user(
        mock_bot,
        telegram_user_id=123456,
        message="Hello",
        session=session,
        internal_user_id=user.id,
    )
    assert result is False


@pytest.mark.asyncio
async def test_notify_user_unauthorized_deactivates_tracker(
    mock_bot: AsyncMock, populated_session
):
    from aiogram.exceptions import TelegramUnauthorizedError

    session, user, tracker = populated_session
    mock_bot.send_message.side_effect = _make_tg_error(TelegramUnauthorizedError, "Unauthorized")

    result = await notify_user(
        mock_bot,
        telegram_user_id=123456,
        message="Hello",
        session=session,
        internal_user_id=user.id,
    )
    assert result is False


@pytest.mark.asyncio
async def test_notify_user_retry_after_keeps_active(mock_bot: AsyncMock, populated_session):
    from aiogram.exceptions import TelegramRetryAfter

    session, user, tracker = populated_session
    exc = TelegramRetryAfter(method=MagicMock(), message="Rate limited", retry_after=30)
    mock_bot.send_message.side_effect = exc

    result = await notify_user(
        mock_bot,
        telegram_user_id=123456,
        message="Hello",
        session=session,
        internal_user_id=user.id,
    )
    assert result is True


@pytest.mark.asyncio
async def test_notify_user_generic_api_error_keeps_active(
    mock_bot: AsyncMock, populated_session
):
    from aiogram.exceptions import TelegramAPIError

    session, user, tracker = populated_session
    mock_bot.send_message.side_effect = _make_tg_error(TelegramAPIError, "Server error")

    result = await notify_user(
        mock_bot,
        telegram_user_id=123456,
        message="Hello",
        session=session,
        internal_user_id=user.id,
    )
    assert result is True


@pytest.mark.asyncio
async def test_notify_user_no_internal_user_id_skips_deactivation(
    mock_bot: AsyncMock, populated_session
):
    from aiogram.exceptions import TelegramForbiddenError

    session, user, tracker = populated_session
    mock_bot.send_message.side_effect = _make_tg_error(TelegramForbiddenError, "Forbidden")

    result = await notify_user(
        mock_bot,
        telegram_user_id=123456,
        message="Hello",
        session=session,
        internal_user_id=None,
    )
    assert result is False


# ---------------------------------------------------------------------------
# Test _recent_event_keys
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_recent_event_keys_empty(populated_session):
    session, user, tracker = populated_session
    keys = await _recent_event_keys(session, tracker.id)
    assert keys == set()


@pytest.mark.asyncio
async def test_recent_event_keys_returns_recent(populated_session):
    session, user, tracker = populated_session
    now = datetime.now(UTC)
    session.add(
        TrackerEvent(
            tracker_id=tracker.id,
            user_id=user.id,
            ad_id=1,
            query="iphone 15",
            strict_mode=False,
            event_type="new_listing",
            title="iPhone 15",
            link="https://www.kufar.by/item/1",
            created_at=now,
        )
    )
    await session.flush()

    keys = await _recent_event_keys(session, tracker.id, hours=24)
    assert (1, "new_listing") in keys


@pytest.mark.asyncio
async def test_recent_event_keys_excludes_old(populated_session):
    session, user, tracker = populated_session
    old = datetime.now(UTC) - timedelta(hours=48)
    session.add(
        TrackerEvent(
            tracker_id=tracker.id,
            user_id=user.id,
            ad_id=1,
            query="iphone 15",
            strict_mode=False,
            event_type="new_listing",
            title="iPhone 15",
            link="https://www.kufar.by/item/1",
            created_at=old,
        )
    )
    await session.flush()

    keys = await _recent_event_keys(session, tracker.id, hours=24)
    assert keys == set()


# ---------------------------------------------------------------------------
# Test _recent_trend_event_tracker_ids
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_recent_trend_event_tracker_ids_empty(populated_session):
    session, user, tracker = populated_session
    ids = await _recent_trend_event_tracker_ids(session, [])
    assert ids == set()


@pytest.mark.asyncio
async def test_recent_trend_event_tracker_ids_within_window(populated_session):
    session, user, tracker = populated_session
    now = datetime.now(UTC)
    session.add(
        TrackerEvent(
            tracker_id=tracker.id,
            user_id=user.id,
            ad_id=None,
            query="iphone 15",
            strict_mode=False,
            event_type="trend_reversal",
            title="Цена ↑",
            link="",
            created_at=now,
        )
    )
    await session.flush()

    ids = await _recent_trend_event_tracker_ids(session, [tracker.id], hours=24)
    assert tracker.id in ids


@pytest.mark.asyncio
async def test_recent_trend_event_tracker_ids_outside_window(populated_session):
    session, user, tracker = populated_session
    old = datetime.now(UTC) - timedelta(hours=48)
    session.add(
        TrackerEvent(
            tracker_id=tracker.id,
            user_id=user.id,
            ad_id=None,
            query="iphone 15",
            strict_mode=False,
            event_type="trend_reversal",
            title="Цена ↑",
            link="",
            created_at=old,
        )
    )
    await session.flush()

    ids = await _recent_trend_event_tracker_ids(session, [tracker.id], hours=24)
    assert ids == set()


# ---------------------------------------------------------------------------
# Test persist_tracker_events
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_persist_tracker_events_new_listing(populated_session):
    session, user, tracker = populated_session
    now = datetime.now(UTC)
    state = QueryListingState(
        ad_id=100,
        query="iphone 15",
        title="iPhone 15 256GB",
        last_price_byn=2000.0,
        link="https://www.kufar.by/item/100",
        active=True,
        first_seen_at=now,
        last_seen_at=now,
    )
    sync = QuerySyncResult(stats_count=1, total_results=1, new_listings=[state], price_drops=[])

    events = await persist_tracker_events(
        session, tracker, sync, ads_by_id={}, seen=set()
    )
    await session.flush()

    assert len(events) == 1
    assert events[0].event_type == "new_listing"
    assert events[0].ad_id == 100


@pytest.mark.asyncio
async def test_persist_tracker_events_price_drop(populated_session):
    session, user, tracker = populated_session
    now = datetime.now(UTC)
    state = QueryListingState(
        ad_id=101,
        query="iphone 15",
        title="iPhone 15 Pro",
        last_price_byn=1800.0,
        link="https://www.kufar.by/item/101",
        active=True,
        first_seen_at=now,
        last_seen_at=now,
    )
    sync = QuerySyncResult(
        stats_count=1, total_results=1, new_listings=[], price_drops=[(state, 200.0)]
    )

    events = await persist_tracker_events(
        session, tracker, sync, ads_by_id={}, seen=set()
    )
    await session.flush()

    assert len(events) == 1
    assert events[0].event_type == "price_drop"
    assert events[0].delta_byn == 200.0


@pytest.mark.asyncio
async def test_persist_tracker_events_skips_seen(populated_session):
    session, user, tracker = populated_session
    now = datetime.now(UTC)
    state = QueryListingState(
        ad_id=102,
        query="iphone 15",
        title="iPhone 15",
        last_price_byn=2000.0,
        link="https://www.kufar.by/item/102",
        active=True,
        first_seen_at=now,
        last_seen_at=now,
    )
    sync = QuerySyncResult(stats_count=1, total_results=1, new_listings=[state], price_drops=[])

    events = await persist_tracker_events(
        session, tracker, sync, ads_by_id={}, seen={(102, "new_listing")}
    )
    await session.flush()

    assert len(events) == 0


@pytest.mark.asyncio
async def test_persist_tracker_events_trend_reversal(populated_session):
    session, user, tracker = populated_session
    sync = QuerySyncResult(stats_count=0, total_results=0, new_listings=[], price_drops=[])

    trend = TrendReversal(
        low_byn=700.0,
        today_byn=770.0,
        pre_high_byn=1000.0,
        decline_pct=30.0,
        rebound_pct=10.0,
        low_at=datetime(2026, 4, 5, tzinfo=UTC).date(),
    )

    events = await persist_tracker_events(
        session, tracker, sync, ads_by_id={}, seen=set(), trend_signal=trend, trend_already_sent=False
    )
    await session.flush()

    assert len(events) == 1
    assert events[0].event_type == "trend_reversal"
    assert events[0].ad_id is None


@pytest.mark.asyncio
async def test_persist_tracker_events_trend_already_sent(populated_session):
    session, user, tracker = populated_session
    sync = QuerySyncResult(stats_count=0, total_results=0, new_listings=[], price_drops=[])

    trend = TrendReversal(
        low_byn=700.0,
        today_byn=770.0,
        pre_high_byn=1000.0,
        decline_pct=30.0,
        rebound_pct=10.0,
        low_at=datetime(2026, 4, 5, tzinfo=UTC).date(),
    )

    events = await persist_tracker_events(
        session, tracker, sync, ads_by_id={}, seen=set(), trend_signal=trend, trend_already_sent=True
    )
    await session.flush()

    assert len(events) == 0


# ---------------------------------------------------------------------------
# Test cleanup functions
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cleanup_old_events(populated_session):
    session, user, tracker = populated_session

    old = datetime.now(UTC) - timedelta(days=60)
    recent = datetime.now(UTC) - timedelta(days=10)

    session.add(
        TrackerEvent(
            tracker_id=tracker.id,
            user_id=user.id,
            ad_id=1,
            query="iphone 15",
            strict_mode=False,
            event_type="new_listing",
            title="Old",
            link="",
            created_at=old,
        )
    )
    session.add(
        TrackerEvent(
            tracker_id=tracker.id,
            user_id=user.id,
            ad_id=2,
            query="iphone 15",
            strict_mode=False,
            event_type="new_listing",
            title="Recent",
            link="",
            created_at=recent,
        )
    )
    await session.flush()

    deleted = await cleanup_old_events(session, days=30)
    assert deleted == 1


@pytest.mark.asyncio
async def test_cleanup_ai_audit_log(populated_session):
    """DB-H3: ensure the new cleanup_ai_audit_log helper deletes
    rows older than the retention window and leaves recent rows
    alone. Mirrors the test_cleanup_old_events shape so both
    cleanups stay covered by the same nightly job."""
    session, user, _tracker = populated_session
    from api.models import AIAuditLog

    old = datetime.now(UTC) - timedelta(days=400)
    recent = datetime.now(UTC) - timedelta(days=10)

    session.add(
        AIAuditLog(
            user_id=user.id,
            endpoint="analyse_listing",
            ad_id="111",
            query="iphone 15",
            result_summary="old summary",
            model="gemini-flash",
            latency_ms=120,
            created_at=old,
        )
    )
    session.add(
        AIAuditLog(
            user_id=user.id,
            endpoint="analyse_listing",
            ad_id="222",
            query="iphone 15",
            result_summary="recent summary",
            model="gemini-flash",
            latency_ms=130,
            created_at=recent,
        )
    )
    await session.flush()

    deleted = await cleanup_ai_audit_log(session, days=365)
    assert deleted == 1


@pytest.mark.asyncio
async def test_cleanup_inactive_listing_states(populated_session):
    session, user, tracker = populated_session
    from api.models import QueryListingState

    old = datetime.now(UTC) - timedelta(days=100)
    recent = datetime.now(UTC) - timedelta(days=10)

    session.add(
        QueryListingState(
            query="iphone 15",
            ad_id=1,
            title="Old inactive",
            link="",
            active=False,
            first_seen_at=old,
            last_seen_at=old,
        )
    )
    session.add(
        QueryListingState(
            query="iphone 15",
            ad_id=2,
            title="Recent inactive",
            link="",
            active=False,
            first_seen_at=recent,
            last_seen_at=recent,
        )
    )
    await session.flush()

    deleted = await cleanup_inactive_listing_states(session, days=90)
    assert deleted == 1


@pytest.mark.asyncio
async def test_cleanup_stale_missing_watchlist(populated_session):
    session, user, tracker = populated_session
    from api.models import LeadItem

    old = datetime.now(UTC) - timedelta(days=10)
    recent = datetime.now(UTC) - timedelta(days=2)

    session.add(
        LeadItem(
            user_id=user.id,
            ad_id=1,
            query="iphone 15",
            title="Old missing",
            link="https://www.kufar.by/item/1",
            status="watching",
            market_status="missing",
            missing_since_at=old,
        )
    )
    session.add(
        LeadItem(
            user_id=user.id,
            ad_id=2,
            query="iphone 15",
            title="Recent missing",
            link="https://www.kufar.by/item/2",
            status="watching",
            market_status="missing",
            missing_since_at=recent,
        )
    )
    await session.flush()

    deleted = await cleanup_stale_missing_watchlist(session, days=7)
    assert deleted == 1


@pytest.mark.asyncio
async def test_cleanup_old_snapshots(populated_session):
    session, user, tracker = populated_session
    from api.models import QuerySnapshot

    old = datetime.now(UTC) - timedelta(days=100)
    recent = datetime.now(UTC) - timedelta(days=10)

    session.add(
        QuerySnapshot(
            query="iphone 15",
            snapshot_at=old,
            total_results=10,
            analyzed_count=10,
            mean_byn=2000.0,
            median_byn=2000.0,
            min_byn=1500.0,
            max_byn=2500.0,
        )
    )
    session.add(
        QuerySnapshot(
            query="iphone 15",
            snapshot_at=recent,
            total_results=10,
            analyzed_count=10,
            mean_byn=2000.0,
            median_byn=2000.0,
            min_byn=1500.0,
            max_byn=2500.0,
        )
    )
    await session.flush()

    deleted = await cleanup_old_snapshots(session, days=90)
    assert deleted == 1


# ---------------------------------------------------------------------------
# Test _recent_events_by_tracker
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_recent_events_by_tracker_empty(populated_session):
    session, user, tracker = populated_session
    result = await _recent_events_by_tracker(session, [])
    assert result == {}


@pytest.mark.asyncio
async def test_recent_events_by_tracker_groups_by_id(populated_session):
    session, user, tracker = populated_session
    now = datetime.now(UTC)
    session.add(
        TrackerEvent(
            tracker_id=tracker.id,
            user_id=user.id,
            ad_id=1,
            query="iphone 15",
            strict_mode=False,
            event_type="new_listing",
            title="A",
            link="",
            created_at=now,
        )
    )
    await session.flush()

    result = await _recent_events_by_tracker(session, [tracker.id])
    assert tracker.id in result
    assert (1, "new_listing") in result[tracker.id]


# ---------------------------------------------------------------------------
# Test persist_tracker_events with enriched ad data
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_persist_tracker_events_with_ads(populated_session):
    session, user, tracker = populated_session
    now = datetime.now(UTC)
    state = QueryListingState(
        ad_id=200,
        query="iphone 15",
        title="iPhone 15 256GB",
        last_price_byn=2000.0,
        link="https://www.kufar.by/item/200",
        active=True,
        first_seen_at=now,
        last_seen_at=now,
    )
    sync = QuerySyncResult(stats_count=1, total_results=1, new_listings=[state], price_drops=[])

    ads_by_id = {
        200: {
            "ad_id": 200,
            "subject": "iPhone 15 256GB",
            "price_byn": 2000,
            "ad_link": "https://www.kufar.by/item/200",
            "seller_type": "Частное лицо",
            "region_id": 6,
        }
    }

    events = await persist_tracker_events(
        session, tracker, sync, ads_by_id=ads_by_id, seen=set()
    )
    await session.flush()

    assert len(events) == 1
    assert events[0].ad_id == 200
    assert events[0].seller_type == "Частное лицо"


# ---------------------------------------------------------------------------
# Wave 23 / PERF-M3: parallel check_reminders pipeline
# ---------------------------------------------------------------------------


async def _seed_due_reminder(session, *, telegram_user_id, ad_id=9001):
    """Helper: create a user + lead + due reminder in ``session``."""
    user = make_user(telegram_user_id=telegram_user_id, first_name=f"U{telegram_user_id}")
    session.add(user)
    await session.flush()
    lead = LeadItem(
        user_id=user.id,
        ad_id=ad_id,
        query="iphone",
        title="iPhone 15",
        link="https://www.kufar.by/item/9001",
        price_byn=2000,
        source="manual",
    )
    session.add(lead)
    await session.flush()
    reminder = LeadReminder(
        lead_id=lead.id,
        user_id=user.id,
        remind_at=datetime.now(UTC) - timedelta(minutes=1),
        message="Проверь сделку",
        sent=False,
    )
    session.add(reminder)
    await session.flush()
    return user, lead, reminder


@pytest.mark.asyncio
async def test_check_reminders_happy_path_marks_all_sent(mock_bot: AsyncMock):
    """PERF-M3: three due reminders for three different users should
    all come out with ``sent=True`` after a single ``check_reminders``
    call, and the bot.send_message mock should have been awaited three
    times — once per reminder."""
    engine = get_engine()
    sf = get_session_factory(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with sf() as session:
        await _seed_due_reminder(session, telegram_user_id=101, ad_id=9001)
        await _seed_due_reminder(session, telegram_user_id=102, ad_id=9002)
        await _seed_due_reminder(session, telegram_user_id=103, ad_id=9003)
        await session.commit()

    settings = MagicMock()
    settings.mini_app_url = "https://example.com/app"
    await check_reminders(mock_bot, sf, settings)

    assert mock_bot.send_message.await_count == 3

    async with sf() as session:
        from sqlalchemy import select
        remaining = await session.execute(
            select(LeadReminder).where(LeadReminder.sent.is_(False))
        )
        assert remaining.scalars().first() is None

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.mark.asyncio
async def test_check_reminders_blocked_user_deactivates_trackers(mock_bot: AsyncMock):
    """PERF-M3: when a user has blocked the bot, the matching
    TelegramForbiddenError must translate into the user's active
    trackers being flipped to ``active=False`` in one bulk update.
    The reminder itself stays ``sent=False`` so a future cycle can
    retry if the user later re-enables the bot."""
    from aiogram.exceptions import TelegramForbiddenError

    engine = get_engine()
    sf = get_session_factory(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with sf() as session:
        user, _lead, _reminder = await _seed_due_reminder(
            session, telegram_user_id=201, ad_id=9004,
        )
        # Give the user an active tracker we can watch flip to inactive.
        tracker = Tracker(user_id=user.id, query="macbook", strict_mode=False)
        session.add(tracker)
        await session.flush()
        tracker_id = tracker.id
        await session.commit()

    mock_bot.send_message.side_effect = _make_tg_error(TelegramForbiddenError, "Forbidden")

    settings = MagicMock()
    settings.mini_app_url = "https://example.com/app"
    await check_reminders(mock_bot, sf, settings)

    async with sf() as session:
        tracker_after = await session.get(Tracker, tracker_id)
        assert tracker_after is not None
        assert tracker_after.active is False
        # Reminder kept at sent=False so a later cycle can retry.
        from sqlalchemy import select
        reminder_after = (
            await session.execute(select(LeadReminder).limit(1))
        ).scalars().first()
        assert reminder_after is not None
        assert reminder_after.sent is False

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.mark.asyncio
async def test_check_reminders_retry_error_leaves_reminder_pending(mock_bot: AsyncMock):
    """PERF-M3: transient Telegram errors (rate limit, generic API
    errors) must NOT mark the reminder as sent — the next tick needs
    to pick it up again."""
    from aiogram.exceptions import TelegramRetryAfter

    engine = get_engine()
    sf = get_session_factory(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with sf() as session:
        await _seed_due_reminder(session, telegram_user_id=301, ad_id=9005)
        await session.commit()

    exc = TelegramRetryAfter(method=MagicMock(), message="Rate limited", retry_after=30)
    mock_bot.send_message.side_effect = exc

    settings = MagicMock()
    settings.mini_app_url = "https://example.com/app"
    await check_reminders(mock_bot, sf, settings)

    async with sf() as session:
        from sqlalchemy import select
        reminder = (
            await session.execute(select(LeadReminder).limit(1))
        ).scalars().first()
        assert reminder is not None
        assert reminder.sent is False  # NOT sent — let the next tick retry

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()
