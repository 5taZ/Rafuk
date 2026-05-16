"""Tests for scheduler/collector.py"""
from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

import scheduler.collector as collector
from api.database import get_engine, get_session_factory
from api.models import (
    Base,
    LeadItem,
    LeadReminder,
    QueryListingState,
    TelegramNotificationDLQ,
    Tracker,
    TrackerEvent,
)
from api.services.aggregator import build_query_key
from api.services.history_service import QuerySyncResult, TrendReversal
from scheduler.collector import (
    _DLQ_MAX_RETRIES,
    _build_new_listing_message,
    _build_price_drop_message,
    _build_threshold_message,
    _build_tracker_message,
    _dispatch_tracker_notifications,
    _format_price_byn,
    _recent_event_keys,
    _recent_events_by_tracker,
    _recent_trend_event_tracker_ids,
    _TrackerNotifyJob,
    check_reminders,
    check_trackers,
    cleanup_ai_audit_log,
    cleanup_inactive_listing_states,
    cleanup_old_events,
    cleanup_old_snapshots,
    cleanup_stale_missing_watchlist,
    cleanup_telegram_notification_dlq,
    notify_user,
    persist_tracker_events,
    retry_telegram_notification_dlq,
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
    assert _format_price_byn(150) == "150 BYN"


def test_format_price_byn_large():
    assert _format_price_byn(2500) == "2500 BYN"


def test_format_price_byn_large_rounding():
    assert _format_price_byn(1000) == "1000 BYN"


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
    assert "2000 BYN" in msg


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
    assert "1800 BYN" in msg


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
# SCH-HIGH / LOGIC-HIGH (issues §5.1, §11.3): discount alert sign
# ---------------------------------------------------------------------------

def test_detect_threshold_alerts_ignores_overpriced_listings():
    """Overpriced listings (delta>0) must NOT trigger a discount alert.

    The previous impl used ``abs(compute_price_vs_reference(...))`` which
    folded "30% cheaper than median" and "30% dearer than median" into
    the same value. After the fix only negative deltas (true discounts)
    drive the alert.
    """
    from api.services.aggregator import PriceStats

    tracker = MagicMock(spec=Tracker)
    tracker.id = 1
    tracker.user_id = 1
    tracker.query = "test"
    tracker.strict_mode = False
    tracker.alert_price_threshold = None
    tracker.alert_discount_percent = 30.0
    tracker.seller_type = None
    tracker.condition = None
    tracker.region_name = None
    tracker.config_keyword = None
    tracker.category_id = None

    market_stats = PriceStats(
        mean=1000.0, median=1000.0, q1=900.0, q3=1100.0,
        min=800.0, max=1300.0, count=20,
    )
    # Ad priced 30% ABOVE median → must NOT fire a discount alert.
    # NB: Kufar's price_byn field is in kopecks; PriceStats fields are
    # in BYN. 130000 kopecks → 1300 BYN, so the ad is +30% vs 1000 BYN.
    overpriced_ad = {
        "ad_id": 100,
        "subject": "Overpriced phone",
        "ad_link": "https://kufar.by/100",
        "price_byn": 130000,  # +30% vs median=1000 BYN (kopecks!)
        "category": 17000,
    }
    events = collector._detect_threshold_alerts(
        ads_by_id={100: overpriced_ad},
        tracker=tracker,
        market_stats=market_stats,
        category_price_stats=None,
        seen=set(),
    )
    assert events == [], (
        "Overpriced listing must not trip discount_alert (was a bug "
        "where abs() folded over- and under-pricing together)"
    )

    # Ad priced 30% BELOW median → must fire a discount alert.
    underpriced_ad = {
        "ad_id": 200,
        "subject": "Discount phone",
        "ad_link": "https://kufar.by/200",
        "price_byn": 70000,  # 70000 kopecks → 700 BYN = −30% vs median=1000
        "category": 17000,
    }
    events = collector._detect_threshold_alerts(
        ads_by_id={200: underpriced_ad},
        tracker=tracker,
        market_stats=market_stats,
        category_price_stats=None,
        seen=set(),
    )
    assert len(events) == 1
    assert events[0].event_type == "discount_alert"
    # delta_byn carries the positive percentage.
    assert events[0].delta_byn is not None and events[0].delta_byn > 0


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
    from sqlalchemy import select

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
    queued = (
        await session.execute(select(TelegramNotificationDLQ))
    ).scalar_one()
    assert queued.user_id == user.id
    assert queued.telegram_user_id == 123456
    assert queued.source == "notify_user"
    assert queued.message == "Hello"


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
        session,
        tracker,
        sync,
        ads_by_id={},
        seen=set(),
        trend_signal=trend,
        trend_already_sent=False,
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
        session,
        tracker,
        sync,
        ads_by_id={},
        seen=set(),
        trend_signal=trend,
        trend_already_sent=True,
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
async def test_cleanup_ai_audit_log_ensures_partitions_on_postgres():
    class _Dialect:
        name = "postgresql"

    class _Bind:
        dialect = _Dialect()

    class _Result:
        rowcount = 0

    class _Session:
        def __init__(self):
            self.statements = []

        def get_bind(self):
            return _Bind()

        async def execute(self, statement):
            self.statements.append(str(statement))
            return _Result()

    session = _Session()

    deleted = await cleanup_ai_audit_log(session, days=365)  # type: ignore[arg-type]

    assert deleted == 0
    assert "ensure_ai_audit_log_monthly_partitions" in session.statements[0]
    assert "DELETE FROM ai_audit_log" in session.statements[1]


@pytest.mark.asyncio
async def test_cleanup_telegram_notification_dlq(populated_session):
    session, user, _tracker = populated_session
    old = datetime.now(UTC) - timedelta(days=45)
    recent = datetime.now(UTC) - timedelta(days=5)

    session.add_all([
        TelegramNotificationDLQ(
            user_id=user.id,
            telegram_user_id=user.telegram_user_id,
            source="test",
            message="old",
            error_kind="retryable",
            created_at=old,
        ),
        TelegramNotificationDLQ(
            user_id=user.id,
            telegram_user_id=user.telegram_user_id,
            source="test",
            message="recent",
            error_kind="retryable",
            created_at=recent,
        ),
    ])
    await session.flush()

    deleted = await cleanup_telegram_notification_dlq(session, days=30)
    assert deleted == 1


@pytest.mark.asyncio
async def test_cleanup_telegram_notification_dlq_drops_retry_exhausted(populated_session):
    """OPUS-2: cleanup also wipes rows that exhausted ``_DLQ_MAX_RETRIES``
    so the pump query stays cheap."""
    session, user, _tracker = populated_session

    session.add_all([
        TelegramNotificationDLQ(
            user_id=user.id,
            telegram_user_id=user.telegram_user_id,
            source="test",
            message="exhausted",
            error_kind="retryable",
            retry_count=_DLQ_MAX_RETRIES,
            created_at=datetime.now(UTC) - timedelta(hours=1),
        ),
        TelegramNotificationDLQ(
            user_id=user.id,
            telegram_user_id=user.telegram_user_id,
            source="test",
            message="still trying",
            error_kind="retryable",
            retry_count=2,
            created_at=datetime.now(UTC) - timedelta(hours=1),
        ),
    ])
    await session.flush()

    deleted = await cleanup_telegram_notification_dlq(session, days=30)
    assert deleted == 1


# ---------------------------------------------------------------------------
# Test retry_telegram_notification_dlq (OPUS-2)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retry_dlq_sent_deletes_row(monkeypatch, mock_bot, populated_session):
    """OPUS-2: a successful resend wipes the DLQ row."""
    session, user, _tracker = populated_session

    session.add(TelegramNotificationDLQ(
        user_id=user.id,
        telegram_user_id=user.telegram_user_id,
        source="tracker",
        message="hello",
        error_kind="retryable",
    ))
    await session.commit()

    async def _stub_classify(bot, tg_id, message, **kwargs):
        return "sent"

    monkeypatch.setattr(collector, "_send_message_classified", _stub_classify)

    sent = await retry_telegram_notification_dlq(
        mock_bot,
        get_session_factory(get_engine()),
    )
    assert sent == 1

    rows = (await session.execute(
        TelegramNotificationDLQ.__table__.select()
    )).all()
    assert rows == []


@pytest.mark.asyncio
async def test_retry_dlq_retry_bumps_count_and_next_retry(
    monkeypatch, mock_bot, populated_session,
):
    """OPUS-2: retry outcome bumps retry_count and schedules
    next_retry_at via exponential backoff."""
    session, user, _tracker = populated_session

    session.add(TelegramNotificationDLQ(
        user_id=user.id,
        telegram_user_id=user.telegram_user_id,
        source="tracker",
        message="hello",
        error_kind="retryable",
        retry_count=1,
    ))
    await session.commit()

    async def _stub_classify(bot, tg_id, message, **kwargs):
        return "retry"

    monkeypatch.setattr(collector, "_send_message_classified", _stub_classify)

    sent = await retry_telegram_notification_dlq(
        mock_bot,
        get_session_factory(get_engine()),
    )
    assert sent == 0

    row = (await session.execute(
        TelegramNotificationDLQ.__table__.select()
    )).one()
    assert row.retry_count == 2
    assert row.next_retry_at is not None
    # SQLite drops timezone on roundtrip; normalise both sides so the
    # backoff arithmetic check stays correct on PG and SQLite alike.
    next_retry = row.next_retry_at
    if next_retry.tzinfo is None:
        next_retry = next_retry.replace(tzinfo=UTC)
    # Next retry must be in the future, < 1 day out — confirms the
    # backoff arithmetic ran without overflow.
    assert next_retry > datetime.now(UTC)
    assert next_retry < datetime.now(UTC) + timedelta(days=1)


@pytest.mark.asyncio
async def test_retry_dlq_blocked_drops_user_rows_and_trackers(
    monkeypatch, mock_bot, populated_session,
):
    """OPUS-2: blocked outcome wipes every DLQ row for that user and
    deactivates their trackers."""
    session, user, tracker = populated_session

    session.add_all([
        TelegramNotificationDLQ(
            user_id=user.id,
            telegram_user_id=user.telegram_user_id,
            source="tracker",
            message="first",
            error_kind="retryable",
        ),
        TelegramNotificationDLQ(
            user_id=user.id,
            telegram_user_id=user.telegram_user_id,
            source="tracker",
            message="second",
            error_kind="retryable",
        ),
    ])
    await session.commit()

    async def _stub_classify(bot, tg_id, message, **kwargs):
        return "blocked"

    monkeypatch.setattr(collector, "_send_message_classified", _stub_classify)

    sent = await retry_telegram_notification_dlq(
        mock_bot,
        get_session_factory(get_engine()),
    )
    assert sent == 0

    rows = (await session.execute(
        TelegramNotificationDLQ.__table__.select()
    )).all()
    assert rows == []

    await session.refresh(tracker)
    assert tracker.active is False


@pytest.mark.asyncio
async def test_retry_dlq_skips_due_in_future(monkeypatch, mock_bot, populated_session):
    """OPUS-2: rows with next_retry_at in the future are NOT pumped."""
    session, user, _tracker = populated_session

    future = datetime.now(UTC) + timedelta(hours=1)
    session.add(TelegramNotificationDLQ(
        user_id=user.id,
        telegram_user_id=user.telegram_user_id,
        source="tracker",
        message="future",
        error_kind="retryable",
        retry_count=2,
        next_retry_at=future,
    ))
    await session.commit()

    sent = await retry_telegram_notification_dlq(
        mock_bot,
        get_session_factory(get_engine()),
    )
    assert sent == 0

    row = (await session.execute(
        TelegramNotificationDLQ.__table__.select()
    )).one()
    assert row.retry_count == 2  # untouched
    # SQLite drops timezone on roundtrip; normalise both sides.
    seen = row.next_retry_at
    if seen.tzinfo is None:
        seen = seen.replace(tzinfo=UTC)
    # next_retry_at preserved (within 1s for timezone-roundtrip safety).
    assert abs((seen - future).total_seconds()) < 1


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


@pytest.mark.asyncio
async def test_cleanup_lead_item_price_snapshots(populated_session):
    """DB-HIGH (issues §4.3): the global retention task must delete
    LeadItemPriceSnapshot rows older than the configured window."""
    from api.models import LeadItemPriceSnapshot
    from scheduler.collector import cleanup_lead_item_price_snapshots

    session, user, tracker = populated_session

    lead = LeadItem(
        user_id=user.id,
        query="ipad",
        ad_id=99001,
        title="iPad",
        link="https://www.kufar.by/item/99001",
        price_byn=1500.0,
        source="manual",
        status="watching",
    )
    session.add(lead)
    await session.flush()

    old_snap = LeadItemPriceSnapshot(
        lead_item_id=lead.id,
        snapped_at=datetime.now(UTC) - timedelta(days=120),
        price_byn=1400.0,
    )
    fresh_snap = LeadItemPriceSnapshot(
        lead_item_id=lead.id,
        snapped_at=datetime.now(UTC) - timedelta(days=10),
        price_byn=1500.0,
    )
    session.add_all([old_snap, fresh_snap])
    await session.flush()

    deleted = await cleanup_lead_item_price_snapshots(session, days=90)
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


@pytest.mark.asyncio
async def test_check_trackers_groups_same_query_by_category(monkeypatch, mock_bot: AsyncMock):
    from sqlalchemy import select

    from api.models import QuerySnapshot

    calls: list[dict] = []

    class CategoryAwareClient:
        def __init__(self, settings):
            del settings

        async def search(self, **kwargs):
            calls.append(kwargs)
            category = kwargs.get("category")
            ad_id = 7000 + (category or 0)
            return {
                "ads": [
                    {
                        "ad_id": ad_id,
                        "subject": "iPhone 15",
                        "price_byn": 2000,
                        "ad_link": f"https://www.kufar.by/item/{ad_id}",
                    }
                ],
                "total": 1,
            }

        async def aclose(self):
            return None

    monkeypatch.setattr(collector, "KufarClient", CategoryAwareClient)

    engine = get_engine()
    sf = get_session_factory(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with sf() as session:
        user = make_user(telegram_user_id=404, first_name="Category")
        session.add(user)
        await session.flush()
        session.add_all(
            [
                Tracker(user_id=user.id, query="iphone 15", strict_mode=False),
                Tracker(
                    user_id=user.id,
                    query="iphone 15",
                    strict_mode=False,
                    category_id=17010,
                    category_label="Мобильные телефоны",
                ),
            ]
        )
        await session.commit()

    settings = MagicMock()
    settings.mini_app_url = "https://example.com/app"
    await check_trackers(mock_bot, sf, settings)

    assert len(calls) == 2
    assert {call.get("category") for call in calls} == {None, 17010}

    async with sf() as session:
        rows = await session.execute(select(QuerySnapshot.query))
        keys = {row.query for row in rows}
        assert build_query_key("iphone 15", False) in keys
        assert build_query_key("iphone 15", False, 17010) in keys

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.mark.asyncio
async def test_check_trackers_fetches_kufar_before_savepoint(monkeypatch, mock_bot: AsyncMock):
    import contextvars

    inside_savepoint = contextvars.ContextVar("inside_savepoint", default=False)
    search_inside_savepoint: list[bool] = []
    original_begin_nested = AsyncSession.begin_nested

    class TrackedSavepoint:
        def __init__(self, cm):
            self.cm = cm
            self.token = None

        async def __aenter__(self):
            self.token = inside_savepoint.set(True)
            return await self.cm.__aenter__()

        async def __aexit__(self, exc_type, exc, tb):
            try:
                return await self.cm.__aexit__(exc_type, exc, tb)
            finally:
                inside_savepoint.reset(self.token)

    def begin_nested_with_flag(self):
        return TrackedSavepoint(original_begin_nested(self))

    class SavepointAwareClient:
        def __init__(self, settings):
            del settings

        async def search(self, **kwargs):
            del kwargs
            search_inside_savepoint.append(inside_savepoint.get())
            return {
                "ads": [
                    {
                        "ad_id": 901,
                        "subject": "iPhone 15",
                        "price_byn": 2000,
                        "ad_link": "https://www.kufar.by/item/901",
                    }
                ],
                "total": 1,
            }

        async def aclose(self):
            return None

    monkeypatch.setattr(AsyncSession, "begin_nested", begin_nested_with_flag)
    monkeypatch.setattr(collector, "KufarClient", SavepointAwareClient)

    engine = get_engine()
    sf = get_session_factory(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with sf() as session:
        user = make_user(telegram_user_id=405, first_name="Tx")
        session.add(user)
        await session.flush()
        session.add(Tracker(user_id=user.id, query="iphone 15", strict_mode=False))
        await session.commit()

    settings = MagicMock()
    settings.mini_app_url = "https://example.com/app"
    await check_trackers(mock_bot, sf, settings)

    assert search_inside_savepoint == [False]

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


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


# ---------------------------------------------------------------------------
# PERF-M3: _dispatch_tracker_notifications — tracker-loop parallel fan-out
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_dispatch_tracker_notifications_empty_is_noop(mock_bot: AsyncMock):
    """Empty job list → return 0 without touching the session factory."""

    def _sf():  # pragma: no cover — must NOT be called
        raise AssertionError("session_factory must not run for empty jobs")

    total = await _dispatch_tracker_notifications(mock_bot, [], _sf)
    assert total == 0
    mock_bot.send_message.assert_not_called()


@pytest.mark.asyncio
async def test_dispatch_tracker_notifications_sends_all(mock_bot: AsyncMock):
    """Happy path: every ``sent`` outcome is counted, no deactivation."""
    engine = get_engine()
    sf = get_session_factory(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with sf() as session:
        u1 = make_user(telegram_user_id=501, first_name="A")
        u2 = make_user(telegram_user_id=502, first_name="B")
        session.add_all([u1, u2])
        await session.flush()
        session.add(Tracker(user_id=u1.id, query="q1", strict_mode=False))
        session.add(Tracker(user_id=u2.id, query="q2", strict_mode=False))
        await session.commit()
        u1_id, u2_id = u1.id, u2.id

    jobs = [
        _TrackerNotifyJob(501, u1_id, "msg-1a", None),
        _TrackerNotifyJob(501, u1_id, "msg-1b", None),
        _TrackerNotifyJob(502, u2_id, "msg-2a", None),
    ]
    total = await _dispatch_tracker_notifications(mock_bot, jobs, sf)

    assert total == 3
    assert mock_bot.send_message.await_count == 3

    async with sf() as session:
        from sqlalchemy import select
        actives = (
            await session.execute(select(Tracker.active))
        ).scalars().all()
        assert all(actives)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.mark.asyncio
async def test_dispatch_tracker_notifications_blocked_skips_user_rest(
    mock_bot: AsyncMock,
):
    """Blocked user → remaining jobs for that user dropped, other
    users unaffected, trackers for blocked user deactivated in ONE
    bulk UPDATE."""
    from aiogram.exceptions import TelegramForbiddenError

    engine = get_engine()
    sf = get_session_factory(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with sf() as session:
        blocked_user = make_user(telegram_user_id=601, first_name="Blocked")
        other_user = make_user(telegram_user_id=602, first_name="Other")
        session.add_all([blocked_user, other_user])
        await session.flush()
        session.add(Tracker(user_id=blocked_user.id, query="q1", strict_mode=False))
        session.add(Tracker(user_id=blocked_user.id, query="q2", strict_mode=False))
        session.add(Tracker(user_id=other_user.id, query="q3", strict_mode=False))
        await session.commit()
        b_id, o_id = blocked_user.id, other_user.id

    call_log: list[int] = []

    async def _fake_send(tg_id, msg, reply_markup=None):
        call_log.append(tg_id)
        if tg_id == 601:
            raise _make_tg_error(TelegramForbiddenError, "Forbidden")
        return MagicMock()

    mock_bot.send_message.side_effect = _fake_send

    jobs = [
        _TrackerNotifyJob(601, b_id, "b1", None),
        _TrackerNotifyJob(601, b_id, "b2", None),
        _TrackerNotifyJob(601, b_id, "b3", None),
        _TrackerNotifyJob(602, o_id, "o1", None),
    ]
    total = await _dispatch_tracker_notifications(mock_bot, jobs, sf)

    # Only user 602's single job counts as sent. User 601 gets 1
    # attempt, discovers the block, short-circuits → 2 send_message
    # calls total (1 blocked + 1 other).
    assert total == 1
    assert call_log.count(601) == 1
    assert call_log.count(602) == 1

    async with sf() as session:
        from sqlalchemy import select
        b_trackers = (
            await session.execute(
                select(Tracker.active).where(Tracker.user_id == b_id)
            )
        ).scalars().all()
        o_trackers = (
            await session.execute(
                select(Tracker.active).where(Tracker.user_id == o_id)
            )
        ).scalars().all()
        assert b_trackers == [False, False]
        assert o_trackers == [True]

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.mark.asyncio
async def test_dispatch_tracker_notifications_retry_does_not_count(
    mock_bot: AsyncMock,
):
    """Transient ``TelegramRetryAfter`` → outcome is ``retry``, which
    is NOT counted in total and does NOT deactivate trackers; the job
    loop continues with the user's next job."""
    from aiogram.exceptions import TelegramRetryAfter

    engine = get_engine()
    sf = get_session_factory(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with sf() as session:
        user = make_user(telegram_user_id=701, first_name="R")
        session.add(user)
        await session.flush()
        session.add(Tracker(user_id=user.id, query="q", strict_mode=False))
        await session.commit()
        uid = user.id

    retry_exc = TelegramRetryAfter(method=MagicMock(), message="rl", retry_after=10)
    mock_bot.send_message.side_effect = [retry_exc, MagicMock()]

    jobs = [
        _TrackerNotifyJob(701, uid, "a", None),
        _TrackerNotifyJob(701, uid, "b", None),
    ]
    total = await _dispatch_tracker_notifications(mock_bot, jobs, sf)

    assert total == 1
    assert mock_bot.send_message.await_count == 2
    async with sf() as session:
        from sqlalchemy import select
        active = (
            await session.execute(
                select(Tracker.active).where(Tracker.user_id == uid)
            )
        ).scalar_one()
        assert active is True
        queued = (
            await session.execute(select(TelegramNotificationDLQ))
        ).scalar_one()
        assert queued.user_id == uid
        assert queued.telegram_user_id == 701
        assert queued.source == "tracker"
        assert queued.message == "a"

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


def test_scheduler_does_not_import_aiogram_types() -> None:
    """ARC-P1: scheduler must not reach into aiogram.types to construct
    keyboards. Keyboard factories live in bot/keyboards.py; the scheduler
    calls them as opaque helpers."""
    source = Path("scheduler/collector.py").read_text(encoding="utf-8")
    assert "from aiogram.types import" not in source
    assert "InlineKeyboardButton(" not in source
    assert "WebAppInfo(" not in source
    assert "InlineKeyboardMarkup(" not in source
    assert "lead_reminder_keyboard" in source
