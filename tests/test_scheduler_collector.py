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
    TRACKER_PAUSE_REASON_BOT_BLOCKED,
    Base,
    LeadItem,
    LeadReminder,
    QueryListingState,
    TelegramNotificationDLQ,
    Tracker,
    TrackerEvent,
)
from api.services.aggregator import build_query_key
from api.services.history_service import QuerySyncResult, TrendReversal, as_utc
from scheduler.collector import (
    _DLQ_MAX_RETRIES,
    _build_new_listing_message,
    _build_price_drop_message,
    _build_threshold_message,
    _build_tracker_message,
    _deserialize_reply_markup,
    _enqueue_tracker_notifications,
    _format_price_byn,
    _recent_event_keys,
    _recent_events_by_tracker,
    _recent_trend_event_tracker_ids,
    _serialize_reply_markup,
    _TrackerNotifyJob,
    check_reminders,
    check_trackers,
    cleanup_ai_audit_log,
    cleanup_inactive_listing_states,
    cleanup_old_events,
    cleanup_old_snapshots,
    cleanup_stale_missing_watchlist,
    cleanup_telegram_notification_dlq,
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

    RV-SCH-05: the 24 h ``seen`` set argument became ``alerted`` (lowest
    price per already-alerted lot); empty here, as ``seen`` was.
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
        alerted={},
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
        alerted={},
    )
    assert len(events) == 1
    assert events[0].event_type == "discount_alert"
    # delta_byn carries the positive percentage.
    assert events[0].delta_byn is not None and events[0].delta_byn > 0


# ---------------------------------------------------------------------------
# Test _send_message_classified
# ---------------------------------------------------------------------------
# SCH-17 / RV2-DS-16: ``notify_user`` (dead in production) is gone; its
# tests now pin the classifier every sender shares.

@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (None, ("sent", None)),
        (("TelegramForbiddenError", "Forbidden"), ("blocked", None)),
        (("TelegramBadRequest", "message is too long"), ("permanent_error", None)),
        (("TelegramAPIError", "Server error"), ("retry", None)),
    ],
)
async def test_send_message_classified_outcomes(
    monkeypatch, mock_bot: AsyncMock, error, expected,
):
    import aiogram.exceptions

    monkeypatch.setattr(collector, "_telegram_paused_until", None)
    if error is not None:
        exc_cls = getattr(aiogram.exceptions, error[0])
        mock_bot.send_message.side_effect = _make_tg_error(exc_cls, error[1])

    outcome = await collector._send_message_classified(mock_bot, 123456, "Hello")

    assert outcome == expected
    mock_bot.send_message.assert_awaited_once_with(123456, "Hello", reply_markup=None)
    assert not collector._telegram_delivery_paused()


@pytest.mark.asyncio
@pytest.mark.parametrize("error_name", ["TelegramNotFound", "TelegramUnauthorizedError"])
async def test_send_message_classified_rejected_token_pauses_delivery(
    monkeypatch, mock_bot: AsyncMock, error_name: str,
):
    """RV-SCH-03 / RV2-DS-03: 401/404 mean the bot token itself is rejected,
    so every chat fails the same way — never a per-user block, and delivery
    pauses instead of spending each row's retries."""
    import aiogram.exceptions

    monkeypatch.setattr(collector, "_telegram_paused_until", None)
    exc_cls = getattr(aiogram.exceptions, error_name)
    mock_bot.send_message.side_effect = _make_tg_error(exc_cls, "token rejected")

    outcome = await collector._send_message_classified(mock_bot, 123456, "Hello")

    assert outcome == ("unauthorized", None)
    assert collector._telegram_delivery_paused()


@pytest.mark.asyncio
async def test_send_message_classified_reports_retry_after(mock_bot: AsyncMock):
    from aiogram.exceptions import TelegramRetryAfter

    mock_bot.send_message.side_effect = TelegramRetryAfter(
        method=MagicMock(), message="Rate limited", retry_after=30,
    )

    outcome = await collector._send_message_classified(mock_bot, 123456, "Hello")

    assert outcome == ("rate_limited", 30)


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
        return "sent", None

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
        return "retry", None

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
async def test_retry_dlq_blocked_drops_user_rows_and_pauses_trackers(
    monkeypatch, mock_bot, populated_session,
):
    """OPUS-2: blocked outcome wipes every DLQ row for that user and
    pauses their trackers (RV-SCH-03: no longer soft-deletes them)."""
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
        return "blocked", None

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
    assert tracker.active is True
    assert tracker.paused is True
    assert tracker.pause_reason == TRACKER_PAUSE_REASON_BOT_BLOCKED


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
async def test_check_reminders_blocked_user_pauses_trackers(mock_bot: AsyncMock):
    """PERF-M3: when a user has blocked the bot, the matching
    TelegramForbiddenError must translate into the user's active
    trackers being paused (RV-SCH-03: not soft-deleted) in one bulk
    update.

    RV-SCH-04: the reminder is no longer left pending "for a future
    cycle" — that re-sent it to the blocked chat every 15 minutes
    forever. It ends ``failed`` (``last_error='blocked'``), still
    ``sent=False``."""
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
        assert tracker_after.active is True
        assert tracker_after.paused is True
        assert tracker_after.pause_reason == TRACKER_PAUSE_REASON_BOT_BLOCKED
        from sqlalchemy import select
        reminder_after = (
            await session.execute(select(LeadReminder).limit(1))
        ).scalars().first()
        assert reminder_after is not None
        assert reminder_after.sent is False
        assert reminder_after.status == "failed"
        assert reminder_after.last_error == "blocked"

    # Terminal: the next tick does not knock on the blocked chat again.
    mock_bot.send_message.reset_mock()
    await check_reminders(mock_bot, sf, settings)
    mock_bot.send_message.assert_not_called()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.mark.asyncio
async def test_check_reminders_retry_error_leaves_reminder_pending(mock_bot: AsyncMock):
    """PERF-M3: transient Telegram errors (rate limit, generic API
    errors) must NOT mark the reminder as sent — a later tick needs
    to pick it up again.

    RV-SCH-04: the retry lives on the reminder row (``attempts`` +
    ``next_attempt_at`` back-off) and nothing is queued to the DLQ —
    both used to fire, delivering the reminder twice."""
    from aiogram.exceptions import TelegramAPIError

    engine = get_engine()
    sf = get_session_factory(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with sf() as session:
        await _seed_due_reminder(session, telegram_user_id=301, ad_id=9005)
        await session.commit()

    # RV2-DS-03: a generic API error — flood control no longer counts.
    mock_bot.send_message.side_effect = _make_tg_error(TelegramAPIError, "Server error")

    settings = MagicMock()
    settings.mini_app_url = "https://example.com/app"
    await check_reminders(mock_bot, sf, settings)

    async with sf() as session:
        from sqlalchemy import select
        reminder = (
            await session.execute(select(LeadReminder).limit(1))
        ).scalars().first()
        assert reminder is not None
        assert reminder.sent is False  # NOT sent — let a later tick retry
        assert reminder.status == "pending"
        assert reminder.attempts == 1
        next_attempt = reminder.next_attempt_at
        if next_attempt.tzinfo is None:
            next_attempt = next_attempt.replace(tzinfo=UTC)
        assert next_attempt > datetime.now(UTC)
        dlq_rows = (await session.execute(select(TelegramNotificationDLQ))).scalars().all()
        assert dlq_rows == []

    # Backing off: the very next tick leaves it alone.
    mock_bot.send_message.reset_mock()
    await check_reminders(mock_bot, sf, settings)
    mock_bot.send_message.assert_not_called()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


# ---------------------------------------------------------------------------
# RV-SCH-02: tracker alerts go through the outbox (enqueue in the group's
# transaction, deliver with the DLQ pump). These replace the PERF-M3
# ``_dispatch_tracker_notifications`` tests: same fan-out guarantees, now
# asserted on the enqueue + pump path that replaced the in-memory queue.
# ---------------------------------------------------------------------------

async def _enqueue_and_pump(mock_bot, sf, jobs, **enqueue_kwargs) -> tuple[int, int]:
    """Write ``jobs`` to the outbox in one transaction, then run one pump
    batch. Returns (rows written, messages sent)."""
    async with sf() as session:
        written = _enqueue_tracker_notifications(session, jobs, **enqueue_kwargs)
        await session.commit()
    sent = await retry_telegram_notification_dlq(mock_bot, sf)
    return written, sent


def test_enqueue_tracker_notifications_empty_is_noop() -> None:
    """Empty job list → no outbox rows."""
    session = MagicMock()
    assert _enqueue_tracker_notifications(session, []) == 0
    session.add.assert_not_called()


@pytest.mark.asyncio
async def test_outbox_tracker_notifications_sends_all(mock_bot: AsyncMock):
    """Happy path: every ``sent`` outcome is counted and its outbox row
    removed, no deactivation."""
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
    written, total = await _enqueue_and_pump(mock_bot, sf, jobs)

    assert written == 3
    assert total == 3
    assert mock_bot.send_message.await_count == 3

    async with sf() as session:
        from sqlalchemy import select
        actives = (
            await session.execute(select(Tracker.active))
        ).scalars().all()
        assert all(actives)
        assert (await session.execute(select(TelegramNotificationDLQ))).first() is None

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.mark.asyncio
async def test_outbox_tracker_notifications_blocked_skips_user_rest(
    mock_bot: AsyncMock,
):
    """Blocked user → remaining jobs for that user dropped, other
    users unaffected, trackers for blocked user paused in ONE
    bulk UPDATE (RV-SCH-03: no longer soft-deleted)."""
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
    _written, total = await _enqueue_and_pump(mock_bot, sf, jobs)

    # Only user 602's single job counts as sent. User 601 gets 1
    # attempt, discovers the block, short-circuits → 2 send_message
    # calls total (1 blocked + 1 other).
    assert total == 1
    assert call_log.count(601) == 1
    assert call_log.count(602) == 1

    async with sf() as session:
        from sqlalchemy import select
        state = (Tracker.active, Tracker.paused, Tracker.pause_reason)
        b_trackers = (
            await session.execute(select(*state).where(Tracker.user_id == b_id))
        ).all()
        o_trackers = (
            await session.execute(select(*state).where(Tracker.user_id == o_id))
        ).all()
        assert [tuple(row) for row in b_trackers] == [
            (True, True, TRACKER_PAUSE_REASON_BOT_BLOCKED),
        ] * 2
        assert [tuple(row) for row in o_trackers] == [(True, False, None)]
        # The blocked user's queued rows are gone, not retried.
        assert (await session.execute(select(TelegramNotificationDLQ))).first() is None

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.mark.asyncio
async def test_outbox_tracker_notifications_retry_does_not_count(
    mock_bot: AsyncMock,
):
    """Transient Telegram error → outcome is ``retry``, which
    is NOT counted in total and does NOT deactivate trackers; the job
    loop continues with the user's next job and the failed row stays
    queued with a back-off."""
    from aiogram.exceptions import TelegramAPIError

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

    retry_exc = _make_tg_error(TelegramAPIError, "Server error")
    mock_bot.send_message.side_effect = [retry_exc, MagicMock()]

    jobs = [
        _TrackerNotifyJob(701, uid, "a", None),
        _TrackerNotifyJob(701, uid, "b", None),
    ]
    _written, total = await _enqueue_and_pump(mock_bot, sf, jobs)

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
        assert queued.retry_count == 1
        assert queued.next_retry_at is not None

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



@pytest.mark.asyncio
async def test_persist_tracker_events_extracts_thumbnail_from_images(
    populated_session,
):
    """Regression for the bot-thumbnail bug: scheduler used to call
    ``ad.get('thumbnail')`` but raw Kufar payloads have no top-level
    ``thumbnail`` key — images live under ``images[].path``. The fix
    routes through ``first_image_url(ad)`` so new TrackerEvent rows
    get a proper URL the bot can forward into LeadItem/WatchlistItem."""
    session, user, tracker = populated_session
    now = datetime.now(UTC)
    state = QueryListingState(
        ad_id=4242,
        query="iphone 15",
        title="iPhone 15 256GB",
        last_price_byn=2000.0,
        link="https://www.kufar.by/item/4242",
        active=True,
        first_seen_at=now,
        last_seen_at=now,
    )
    sync = QuerySyncResult(
        stats_count=1, total_results=1, new_listings=[state], price_drops=[]
    )
    # Raw Kufar shape: no 'thumbnail' key, image lives in images[0].path.
    ads_by_id = {
        4242: {
            "ad_id": 4242,
            "images": [{"path": "adim1/photo.jpg"}],
            "seller_type": "private",
        },
    }

    events = await persist_tracker_events(
        session, tracker, sync, ads_by_id=ads_by_id, seen=set()
    )
    await session.flush()

    assert len(events) == 1
    assert events[0].thumbnail == "https://rms.kufar.by/v1/gallery/adim1/photo.jpg"
    assert events[0].seller_type == "private"


@pytest.mark.asyncio
async def test_persist_tracker_events_thumbnail_none_when_no_images(
    populated_session,
):
    """When the Kufar payload has no images at all, thumbnail must be
    None — not a crash, not an empty string."""
    session, user, tracker = populated_session
    now = datetime.now(UTC)
    state = QueryListingState(
        ad_id=4243,
        query="iphone",
        title="iPhone",
        last_price_byn=1500.0,
        link="https://www.kufar.by/item/4243",
        active=True,
        first_seen_at=now,
        last_seen_at=now,
    )
    sync = QuerySyncResult(
        stats_count=1, total_results=1, new_listings=[state], price_drops=[]
    )
    ads_by_id = {4243: {"ad_id": 4243, "images": []}}

    events = await persist_tracker_events(
        session, tracker, sync, ads_by_id=ads_by_id, seen=set()
    )
    await session.flush()

    assert events[0].thumbnail is None


# ---------------------------------------------------------------------------
# AUDIT-LOW: cross-tracker dedup of pending Telegram notifications
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_outbox_tracker_notifications_dedups_same_user_same_ad(
    mock_bot: AsyncMock,
):
    """If a user owns two trackers for the same query (e.g. different
    price ceilings) and a single ad passes both filter sets, the user
    should get ONE Telegram message — not two near-identical pings.

    Dedup keys jobs sharing ``(telegram_user_id, dedup_key)``; the
    first-enqueued job wins so the message tied to the earlier-iterated
    tracker is the one delivered. RV-SCH-02: the collapse now happens
    when the jobs are written to the outbox."""
    engine = get_engine()
    sf = get_session_factory(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with sf() as session:
        user = make_user(telegram_user_id=701, first_name="Dup")
        session.add(user)
        await session.flush()
        session.add(Tracker(user_id=user.id, query="iphone", strict_mode=False))
        session.add(Tracker(user_id=user.id, query="iphone", strict_mode=True))
        await session.commit()
        u_id = user.id

    # Two jobs for the same (user, ad, event) — should collapse to 1.
    jobs = [
        _TrackerNotifyJob(701, u_id, "msg-strict", None, dedup_key="new_listing:42"),
        _TrackerNotifyJob(701, u_id, "msg-loose", None, dedup_key="new_listing:42"),
        # Different ad → goes through.
        _TrackerNotifyJob(701, u_id, "msg-other", None, dedup_key="new_listing:99"),
        # Threshold-style job (dedup_key=None) is NOT collapsed.
        _TrackerNotifyJob(701, u_id, "msg-threshold-A", None, dedup_key=None),
        _TrackerNotifyJob(701, u_id, "msg-threshold-B", None, dedup_key=None),
    ]
    written, total = await _enqueue_and_pump(mock_bot, sf, jobs)

    # 1 deduped + 1 unique listing + 2 threshold = 4 sends; first
    # listing job (msg-strict) wins.
    assert written == 4
    assert total == 4
    sent_messages = [
        call.kwargs.get("text") or call.args[1] if len(call.args) > 1 else call.kwargs.get("text")
        for call in mock_bot.send_message.await_args_list
    ]
    assert "msg-strict" in sent_messages
    assert "msg-loose" not in sent_messages  # deduped
    assert "msg-other" in sent_messages
    assert "msg-threshold-A" in sent_messages
    assert "msg-threshold-B" in sent_messages

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.mark.asyncio
async def test_outbox_tracker_notifications_dedup_is_per_user(
    mock_bot: AsyncMock,
):
    """Two different users can both receive the same dedup_key — the
    dedup is scoped per-user, not global."""
    engine = get_engine()
    sf = get_session_factory(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with sf() as session:
        u1 = make_user(telegram_user_id=801, first_name="A")
        u2 = make_user(telegram_user_id=802, first_name="B")
        session.add_all([u1, u2])
        await session.flush()
        session.add(Tracker(user_id=u1.id, query="x", strict_mode=False))
        session.add(Tracker(user_id=u2.id, query="x", strict_mode=False))
        await session.commit()
        u1_id, u2_id = u1.id, u2.id

    jobs = [
        _TrackerNotifyJob(801, u1_id, "user-1", None, dedup_key="new_listing:7"),
        _TrackerNotifyJob(802, u2_id, "user-2", None, dedup_key="new_listing:7"),
    ]
    written, total = await _enqueue_and_pump(mock_bot, sf, jobs)

    assert written == 2
    assert total == 2
    assert mock_bot.send_message.await_count == 2

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


# ---------------------------------------------------------------------------
# AUDIT-LOW: DLQ keyboard preservation across retries
# ---------------------------------------------------------------------------

def test_serialize_reply_markup_roundtrip() -> None:
    """``_serialize_reply_markup`` + ``_deserialize_reply_markup`` must
    produce a markup with the same button text/callback_data as the
    original — this is what makes a DLQ retry land with usable
    "📌 В покупки" / "⭐ В Избранное" / "🔗 Открыть" buttons."""
    from bot.keyboards import enhanced_alert_keyboard

    original = enhanced_alert_keyboard(
        ad_id=4242, listing_url="https://www.kufar.by/item/4242",
    )
    payload = _serialize_reply_markup(original)
    assert payload is not None
    assert "В покупки" in payload
    assert "add_lead:4242" in payload

    restored = _deserialize_reply_markup(payload)
    assert restored is not None
    # Same shape as the original — first row has both action buttons.
    assert restored.inline_keyboard[0][0].text == "📌 В покупки"
    assert restored.inline_keyboard[0][0].callback_data == "add_lead:4242"
    assert restored.inline_keyboard[0][1].text == "⭐ В Избранное"
    # Second row has the URL button.
    assert restored.inline_keyboard[1][0].text == "🔗 Открыть"
    assert restored.inline_keyboard[1][0].url == "https://www.kufar.by/item/4242"


def test_serialize_reply_markup_handles_none_and_garbage() -> None:
    """Robustness: serializer/deserializer must never raise on bad input
    — losing a keyboard on retry is cosmetic, but raising here would
    drop the entire DLQ row or block the pump."""
    assert _serialize_reply_markup(None) is None
    # Plain object with no model_dump_json — falls through to None.
    assert _serialize_reply_markup({"not": "a markup"}) is None
    assert _deserialize_reply_markup(None) is None
    assert _deserialize_reply_markup("") is None
    assert _deserialize_reply_markup("{not valid json") is None


@pytest.mark.asyncio
async def test_dlq_retry_pump_preserves_inline_keyboard(mock_bot: AsyncMock):
    """End-to-end: a DLQ row written with a serialized keyboard must
    have that keyboard rebuilt and passed back to ``send_message`` on
    retry. Pre-fix this dropped the markup and the retried message
    arrived as plain text with no action buttons."""
    from bot.keyboards import enhanced_alert_keyboard

    engine = get_engine()
    sf = get_session_factory(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    keyboard = enhanced_alert_keyboard(
        ad_id=999, listing_url="https://www.kufar.by/item/999",
    )

    async with sf() as session:
        user = make_user(telegram_user_id=901, first_name="DLQ")
        session.add(user)
        await session.flush()
        # Hand-craft the DLQ row with a serialized keyboard.
        session.add(
            TelegramNotificationDLQ(
                user_id=user.id,
                telegram_user_id=901,
                source="tracker",
                message="iPhone 15 Pro 1100 BYN",
                error_kind="retry",
                reply_markup_json=_serialize_reply_markup(keyboard),
            )
        )
        await session.commit()

    sent = await retry_telegram_notification_dlq(mock_bot, sf)
    assert sent == 1

    # The pump rebuilt the keyboard from JSON and passed it through to
    # send_message. Verify by inspecting the call.
    assert mock_bot.send_message.await_count == 1
    call = mock_bot.send_message.await_args
    reply_markup = call.kwargs.get("reply_markup")
    assert reply_markup is not None
    assert reply_markup.inline_keyboard[0][0].callback_data == "add_lead:999"

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.mark.asyncio
async def test_dlq_retry_pump_falls_back_to_plain_text_when_no_markup(
    mock_bot: AsyncMock,
):
    """Backward-compat: rows queued before the schema change have
    NULL ``reply_markup_json`` and must still send (just without
    buttons) — the retry pump shouldn't refuse to dispatch them."""
    engine = get_engine()
    sf = get_session_factory(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with sf() as session:
        user = make_user(telegram_user_id=902, first_name="Legacy")
        session.add(user)
        await session.flush()
        session.add(
            TelegramNotificationDLQ(
                user_id=user.id,
                telegram_user_id=902,
                source="tracker",
                message="legacy row, no keyboard",
                error_kind="retry",
                reply_markup_json=None,
            )
        )
        await session.commit()

    sent = await retry_telegram_notification_dlq(mock_bot, sf)
    assert sent == 1
    call = mock_bot.send_message.await_args
    assert call.kwargs.get("reply_markup") is None

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


# ---------------------------------------------------------------------------
# AUDIT-LOW: _MAX_EVENTS_PER_TYPE cap is logged when it bites
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_persist_tracker_events_logs_when_max_events_cap_drops_events(
    populated_session,
    caplog,
):
    """When ``sync_result.new_listings`` exceeds the per-type cap, the
    overflow is silently sliced off — log a single info line so the
    drop is observable."""
    import logging

    session, user, tracker = populated_session
    now = datetime.now(UTC)
    cap = collector._MAX_EVENTS_PER_TYPE
    overflow = 3
    states = [
        QueryListingState(
            ad_id=10_000 + i,
            query=tracker.query,
            title=f"Listing {i}",
            last_price_byn=1000.0 + i,
            link=f"https://www.kufar.by/item/{10_000 + i}",
            active=True,
            first_seen_at=now,
            last_seen_at=now,
        )
        for i in range(cap + overflow)
    ]
    sync = QuerySyncResult(
        stats_count=cap + overflow,
        total_results=cap + overflow,
        new_listings=states,
        price_drops=[],
    )

    with caplog.at_level(logging.INFO, logger="scheduler.collector"):
        events = await persist_tracker_events(
            session, tracker, sync, ads_by_id={}, seen=set(),
        )
        await session.flush()

    # Only `cap` events persisted — overflow dropped.
    assert len(events) == cap
    # And we logged about it.
    assert any(
        "_MAX_EVENTS_PER_TYPE cap dropped" in record.getMessage()
        and "new_listing" in record.getMessage()
        for record in caplog.records
    )


# ---------------------------------------------------------------------------
# AUDIT-MEDIUM: discount_pct sign — only show "% от медианы" when below market
# ---------------------------------------------------------------------------

def test_new_listing_message_omits_discount_when_above_market() -> None:
    """A listing priced ABOVE the median is a premium, not a discount;
    the "% от медианы" line implies a discount via its leading "-"
    prefix in the template, so we MUST NOT emit it for positive
    deltas. The fix in ``check_trackers`` passes ``discount_pct=None``
    when ``compute_price_vs_reference >= 0``; this test pins that the
    template-side rendering matches."""
    state = QueryListingState(
        ad_id=1,
        query="iphone",
        title="iPhone 15 Pro",
        last_price_byn=2500.0,
        link="https://www.kufar.by/item/1",
        active=True,
        first_seen_at=datetime.now(UTC),
        last_seen_at=datetime.now(UTC),
    )
    # Pre-fix: collector passed ``abs(+20%)=20`` here even for premiums,
    # producing a "-20% от медианы" line. Post-fix the collector passes
    # None for premium-priced listings, so the template never emits it.
    msg_premium = _build_new_listing_message(
        state, median_byn=2000.0, discount_pct=None,
    )
    assert "% от медианы" not in msg_premium
    # Genuine discount still renders as before.
    msg_discount = _build_new_listing_message(
        state, median_byn=2000.0, discount_pct=20.0,
    )
    assert "-20% от медианы" in msg_discount


@pytest.mark.asyncio
async def test_check_trackers_does_not_auto_pause_while_kufar_breaker_is_open(
    monkeypatch, mock_bot: AsyncMock,
):
    """RV-BE-05: an open circuit breaker (KufarUnavailableError) is a
    global outage. It must skip the key without feeding the C-04 counter —
    otherwise a ~25 min outage would auto-pause every tracker."""
    from sqlalchemy import select

    from api.services.kufar_client import KufarUnavailableError

    class BreakerOpenClient:
        def __init__(self, settings):
            del settings

        async def search(self, **kwargs):
            del kwargs
            raise KufarUnavailableError("Kufar circuit breaker open")

        async def aclose(self):
            return None

    monkeypatch.setattr(collector, "KufarClient", BreakerOpenClient)
    collector._tracker_failure_counts.clear()

    engine = get_engine()
    sf = get_session_factory(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with sf() as session:
        user = make_user(telegram_user_id=910, first_name="Outage")
        session.add(user)
        await session.flush()
        session.add(Tracker(user_id=user.id, query="breaker query", strict_mode=False))
        await session.commit()

    settings = MagicMock()
    settings.mini_app_url = "https://example.com/app"

    for _ in range(collector._TRACKER_AUTO_PAUSE_THRESHOLD + 2):
        await check_trackers(mock_bot, sf, settings)

    async with sf() as session:
        tracker = (await session.execute(select(Tracker))).scalar_one()
        assert tracker.paused is False
        assert tracker.pause_reason is None
        # Never synced, so the key stays due for the next tick.
        assert tracker.last_checked_at is None
    assert collector._tracker_failure_counts == {}


@pytest.mark.asyncio
async def test_check_trackers_auto_pauses_after_persistent_kufar_errors(
    monkeypatch, mock_bot: AsyncMock,
):
    """C-04: a tracker whose query group raises ``KufarAPIError`` on
    every tick gets paused after ``_TRACKER_AUTO_PAUSE_THRESHOLD``
    consecutive failures, with ``pause_reason='persistent_kufar_error'``.
    """
    from sqlalchemy import select

    from api.services.kufar_client import KufarAPIError

    class AlwaysFailingClient:
        def __init__(self, settings):
            del settings

        async def search(self, **kwargs):
            del kwargs
            raise KufarAPIError("Kufar 503")

        async def aclose(self):
            return None

    monkeypatch.setattr(collector, "KufarClient", AlwaysFailingClient)
    # Reset module-level counter — earlier tests in the same process
    # may have populated it.
    collector._tracker_failure_counts.clear()

    engine = get_engine()
    sf = get_session_factory(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with sf() as session:
        user = make_user(telegram_user_id=909, first_name="Persistent")
        session.add(user)
        await session.flush()
        session.add(
            Tracker(user_id=user.id, query="phantom query", strict_mode=False),
        )
        await session.commit()

    settings = MagicMock()
    settings.mini_app_url = "https://example.com/app"

    # _TRACKER_AUTO_PAUSE_THRESHOLD - 1 ticks must NOT pause yet.
    for _ in range(collector._TRACKER_AUTO_PAUSE_THRESHOLD - 1):
        await check_trackers(mock_bot, sf, settings)

    async with sf() as session:
        tracker = (await session.execute(select(Tracker))).scalar_one()
        assert tracker.paused is False
        assert tracker.pause_reason is None

    # The threshold-th failure must trigger the pause.
    await check_trackers(mock_bot, sf, settings)

    async with sf() as session:
        tracker = (await session.execute(select(Tracker))).scalar_one()
        assert tracker.paused is True
        assert tracker.pause_reason == "persistent_kufar_error"
        assert tracker.paused_at is not None

    # Counter must clear after auto-pause so a manually-resumed
    # tracker starts fresh.
    assert collector._tracker_failure_counts.get(tracker.id) is None

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.mark.asyncio
async def test_check_trackers_failure_counter_resets_on_success(
    monkeypatch, mock_bot: AsyncMock,
):
    """C-04: a transient outage that recovers must NOT lead to
    auto-pause. The failure counter is cleared as soon as a tick
    succeeds.
    """
    from sqlalchemy import select

    from api.services.kufar_client import KufarAPIError

    fail_count = {"value": 2}  # fail twice, then succeed forever

    class FlakyClient:
        def __init__(self, settings):
            del settings

        async def search(self, **kwargs):
            if fail_count["value"] > 0:
                fail_count["value"] -= 1
                raise KufarAPIError("transient")
            return {"ads": [], "total": 0}

        async def aclose(self):
            return None

    monkeypatch.setattr(collector, "KufarClient", FlakyClient)
    collector._tracker_failure_counts.clear()

    engine = get_engine()
    sf = get_session_factory(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with sf() as session:
        user = make_user(telegram_user_id=910, first_name="Flaky")
        session.add(user)
        await session.flush()
        session.add(Tracker(user_id=user.id, query="fluctuates", strict_mode=False))
        await session.commit()

    settings = MagicMock()
    settings.mini_app_url = "https://example.com/app"

    # Two failed ticks → counter at 2, well below threshold.
    await check_trackers(mock_bot, sf, settings)
    await check_trackers(mock_bot, sf, settings)
    # One successful tick → counter cleared.
    await check_trackers(mock_bot, sf, settings)
    # Many more successful ticks must keep the tracker active even
    # if we passed _TRACKER_AUTO_PAUSE_THRESHOLD ticks total.
    for _ in range(collector._TRACKER_AUTO_PAUSE_THRESHOLD + 2):
        await check_trackers(mock_bot, sf, settings)

    async with sf() as session:
        tracker = (await session.execute(select(Tracker))).scalar_one()
        assert tracker.paused is False, "Transient outage must not auto-pause"
        assert tracker.pause_reason is None

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


# ---------------------------------------------------------------------------
# LOGIC-NEW-5: DLQ inline purge at max retries
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_dlq_pump_deletes_exhausted_rows_inline(mock_bot: AsyncMock):
    """LOGIC-NEW-5: rows that hit _DLQ_MAX_RETRIES are deleted inline."""
    engine = get_engine()
    sf = get_session_factory(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with sf() as session:
        user = make_user(telegram_user_id=950, first_name="Exhaust")
        session.add(user)
        await session.flush()
        # Row at max_retries - 1 so next bump hits the limit
        session.add(
            TelegramNotificationDLQ(
                user_id=user.id,
                telegram_user_id=950,
                source="tracker",
                message="will exhaust",
                error_kind="retry",
                retry_count=_DLQ_MAX_RETRIES - 1,
                next_retry_at=datetime.now(UTC) - timedelta(minutes=5),
            )
        )
        await session.commit()

    # Make send fail with retry outcome
    mock_bot.send_message = AsyncMock(side_effect=Exception("timeout"))
    import scheduler.collector as _col

    original = _col._send_message_classified

    async def _always_retry(*args, **kwargs):
        return "retry", None

    _col._send_message_classified = _always_retry
    try:
        await retry_telegram_notification_dlq(mock_bot, sf)
    finally:
        _col._send_message_classified = original

    # Row should be deleted inline
    from sqlalchemy import select as sa_select
    async with sf() as session:
        remaining = (await session.execute(
            sa_select(TelegramNotificationDLQ)
        )).scalars().all()
        assert len(remaining) == 0, "Exhausted DLQ row should be deleted inline"

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


# ---------------------------------------------------------------------------
# LOGIC-NEW-6: cleanup_stale_missing_watchlist purges child rows
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cleanup_stale_missing_watchlist_purges_child_rows(populated_session):
    """LOGIC-NEW-6: snapshots and expenses are deleted before parent lead_items."""
    session, user, tracker = populated_session
    from api.models import DealExpense, LeadItemPriceSnapshot

    old = datetime.now(UTC) - timedelta(days=10)
    item = LeadItem(
        user_id=user.id,
        ad_id=9001,
        query="iphone 15",
        title="Stale with children",
        link="https://www.kufar.by/item/9001",
        status="watching",
        market_status="missing",
        missing_since_at=old,
    )
    session.add(item)
    await session.flush()

    # Add child snapshot and expense
    session.add(LeadItemPriceSnapshot(
        lead_item_id=item.id,
        price_byn=100.0,
    ))
    session.add(DealExpense(
        lead_id=item.id,
        user_id=user.id,
        expense_type="delivery",
        amount_byn=50.0,
    ))
    await session.flush()

    deleted = await cleanup_stale_missing_watchlist(session, days=7)
    assert deleted == 1

    # Verify child rows are gone
    from sqlalchemy import select as sa_select
    snaps = (await session.execute(
        sa_select(LeadItemPriceSnapshot).where(LeadItemPriceSnapshot.lead_item_id == item.id)
    )).scalars().all()
    assert len(snaps) == 0

    expenses = (await session.execute(
        sa_select(DealExpense).where(DealExpense.lead_id == item.id)
    )).scalars().all()
    assert len(expenses) == 0


# ---------------------------------------------------------------------------
# RV-SCH-01 / RV-SCH-02 / RV-SCH-05 / RV-SEC-05: the tracker tick end to end
# ---------------------------------------------------------------------------

_TICK_SETTINGS = MagicMock(mini_app_url="https://example.com/app")


def _ad(ad_id: int, price_kopecks: int, subject: str = "iPhone 13 128GB") -> dict:
    return {
        "ad_id": ad_id,
        "subject": subject,
        "price_byn": price_kopecks,
        "ad_link": f"https://www.kufar.by/item/{ad_id}",
    }


def _kufar_stub(ads_for, calls: list[dict]):
    """KufarClient stand-in: ``ads_for(query)`` gives the page (or an
    exception to raise); every search is recorded in ``calls``."""

    class _Stub:
        def __init__(self, settings):
            del settings

        async def search(self, **kwargs):
            calls.append(kwargs)
            page = ads_for(kwargs["query"])
            if isinstance(page, BaseException):
                raise page
            return {"ads": page, "total": len(page)}

        async def aclose(self):
            return None

    return _Stub


@pytest.fixture
async def tick_db():
    engine = get_engine()
    sf = get_session_factory(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    collector._tracker_failure_counts.clear()
    yield sf
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


async def _event_keys(sf) -> set[tuple[int, int | None, str]]:
    from sqlalchemy import select

    async with sf() as session:
        rows = await session.execute(
            select(TrackerEvent.tracker_id, TrackerEvent.ad_id, TrackerEvent.event_type)
        )
        return {(row.tracker_id, row.ad_id, row.event_type) for row in rows}


def _sent_chats(mock_bot: AsyncMock) -> list[int]:
    return [call.args[0] for call in mock_bot.send_message.await_args_list]


@pytest.mark.asyncio
async def test_check_trackers_each_tracker_gets_changes_since_its_own_check(
    monkeypatch, mock_bot: AsyncMock, tick_db,
):
    """RV-SCH-01: two trackers on one search key, checked on different
    schedules. The 15-minute tracker syncs the key first; the hourly one
    must still get the new listing and the price drop that appeared after
    ITS previous check — the old key-level diff had already "eaten" both."""
    from sqlalchemy import update

    sf = tick_db
    calls: list[dict] = []
    # ad 1 was 1100 BYN before both checks, now 1000; ad 2 is brand new.
    page = [_ad(1, 100_000), _ad(2, 150_000)]
    monkeypatch.setattr(collector, "KufarClient", _kufar_stub(lambda q: page, calls))
    now = datetime.now(UTC)
    async with sf() as session:
        fast_user = make_user(telegram_user_id=1101, first_name="Fast")
        slow_user = make_user(telegram_user_id=1102, first_name="Slow")
        session.add_all([fast_user, slow_user])
        await session.flush()
        fast = Tracker(
            user_id=fast_user.id, query="iphone 13", strict_mode=False,
            interval_min=15, last_checked_at=now - timedelta(minutes=20),
        )
        slow = Tracker(
            user_id=slow_user.id, query="iphone 13", strict_mode=False,
            interval_min=60, last_checked_at=now - timedelta(minutes=20),
        )
        session.add_all([fast, slow])
        session.add(QueryListingState(
            query=build_query_key("iphone 13", False),
            ad_id=1,
            title="iPhone 13 128GB",
            link="https://www.kufar.by/item/1",
            last_price_byn=1100,
            price_type="fixed",
            active=True,
            first_seen_at=now - timedelta(hours=3),
            last_seen_at=now - timedelta(minutes=20),
        ))
        await session.commit()
        fast_id, slow_id = fast.id, slow.id

    # Tick 1: only the 15-minute tracker is due.
    await check_trackers(mock_bot, sf, _TICK_SETTINGS)
    assert len(calls) == 1
    assert await _event_keys(sf) == {(fast_id, 2, "new_listing"), (fast_id, 1, "price_drop")}

    # Tick 2: the hourly tracker comes due; its last check predates tick 1.
    async with sf() as session:
        await session.execute(
            update(Tracker)
            .where(Tracker.id == slow_id)
            .values(last_checked_at=datetime.now(UTC) - timedelta(minutes=70))
        )
        await session.commit()
    await check_trackers(mock_bot, sf, _TICK_SETTINGS)
    assert len(calls) == 2
    assert await _event_keys(sf) == {
        (fast_id, 2, "new_listing"), (fast_id, 1, "price_drop"),
        (slow_id, 2, "new_listing"), (slow_id, 1, "price_drop"),
    }
    assert sorted(_sent_chats(mock_bot)) == [1101, 1101, 1102, 1102]


@pytest.mark.asyncio
async def test_check_trackers_queries_kufar_once_per_search_key(
    monkeypatch, mock_bot: AsyncMock, tick_db,
):
    """RV-SCH-01: "iPhone 13" and "iphone 13" share one search key (and one
    set of listing states). They used to be two groups: Kufar was asked
    twice and the second group saw nothing new."""
    sf = tick_db
    calls: list[dict] = []
    monkeypatch.setattr(
        collector, "KufarClient", _kufar_stub(lambda q: [_ad(7, 120_000)], calls),
    )
    checked = datetime.now(UTC) - timedelta(minutes=20)
    async with sf() as session:
        first = make_user(telegram_user_id=1201, first_name="Caps")
        second = make_user(telegram_user_id=1202, first_name="Lower")
        session.add_all([first, second])
        await session.flush()
        a = Tracker(
            user_id=first.id, query="iPhone 13", strict_mode=False, last_checked_at=checked,
        )
        session.add(a)
        await session.flush()
        b = Tracker(
            user_id=second.id, query="iphone 13", strict_mode=False, last_checked_at=checked,
        )
        session.add(b)
        await session.commit()
        a_id, b_id = a.id, b.id

    await check_trackers(mock_bot, sf, _TICK_SETTINGS)

    assert len(calls) == 1
    # Asked in the wording of the key's oldest tracker.
    assert calls[0]["query"] == "iPhone 13"
    assert await _event_keys(sf) == {(a_id, 7, "new_listing"), (b_id, 7, "new_listing")}
    assert sorted(_sent_chats(mock_bot)) == [1201, 1202]


@pytest.mark.asyncio
async def test_check_trackers_delivers_through_outbox_with_keyboard(
    monkeypatch, mock_bot: AsyncMock, tick_db,
):
    """RV-SCH-02: the alert is an outbox row committed with the key's state
    and sent by the drain at the end of the tick — keyboard included, row
    gone afterwards."""
    from sqlalchemy import select

    sf = tick_db
    monkeypatch.setattr(collector, "KufarClient", _kufar_stub(lambda q: [_ad(31, 99_000)], []))
    async with sf() as session:
        user = make_user(telegram_user_id=1301, first_name="Outbox")
        session.add(user)
        await session.flush()
        session.add(Tracker(
            user_id=user.id, query="iphone 13", strict_mode=False,
            last_checked_at=datetime.now(UTC) - timedelta(minutes=20),
        ))
        await session.commit()

    await check_trackers(mock_bot, sf, _TICK_SETTINGS)

    assert mock_bot.send_message.await_count == 1
    call = mock_bot.send_message.await_args
    assert call.args[0] == 1301
    assert "НОВЫЙ ЛОТ" in call.args[1]
    assert call.kwargs["reply_markup"].inline_keyboard[0][0].callback_data == "add_lead:31"
    async with sf() as session:
        assert (await session.execute(select(TelegramNotificationDLQ))).first() is None


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["fetch", "process"])
async def test_check_trackers_unexpected_error_is_isolated_to_its_key(
    monkeypatch, mock_bot: AsyncMock, tick_db, stage: str,
):
    """RV-SCH-02 / RV2-DS-01: an unexpected exception on one key used to
    abort the whole tick, and the failing key — oldest cursor — led the
    next tick and aborted it again: silence for every user. Now the key is
    skipped with a strike and its cursor moved on (a tracker without a
    baseline keeps NULL), and later keys still deliver."""
    from sqlalchemy import select

    sf = tick_db

    def _pages(query):
        if stage == "fetch" and query == "macbook air":
            return RuntimeError("unexpected")
        return [_ad(41, 99_000)]

    if stage == "process":
        original_jobs = collector._listing_notification_jobs

        def _jobs(tracker, *args, **kwargs):
            if tracker.query == "macbook air":
                raise ValueError("unexpected")
            return original_jobs(tracker, *args, **kwargs)

        monkeypatch.setattr(collector, "_listing_notification_jobs", _jobs)
    monkeypatch.setattr(collector, "KufarClient", _kufar_stub(_pages, []))
    now = datetime.now(UTC)
    async with sf() as session:
        user = make_user(telegram_user_id=1401, first_name="Crash")
        session.add(user)
        await session.flush()
        fresh = Tracker(user_id=user.id, query="macbook air", strict_mode=False)
        poisoned = Tracker(user_id=user.id, query="macbook air", strict_mode=False,
                           last_checked_at=now - timedelta(minutes=40))
        healthy = Tracker(user_id=user.id, query="iphone 13", strict_mode=False,
                          last_checked_at=now - timedelta(minutes=20))
        session.add_all([fresh, poisoned, healthy])
        await session.commit()
        fresh_id, poisoned_id, healthy_id = fresh.id, poisoned.id, healthy.id

    await check_trackers(mock_bot, sf, _TICK_SETTINGS)

    assert _sent_chats(mock_bot) == [1401]
    assert "НОВЫЙ ЛОТ" in mock_bot.send_message.await_args.args[1]
    assert await _event_keys(sf) == {(healthy_id, 41, "new_listing")}
    async with sf() as session:
        cursors = dict((await session.execute(select(Tracker.id, Tracker.last_checked_at))).all())
    assert cursors[fresh_id] is None
    assert as_utc(cursors[poisoned_id]) > now
    assert collector._tracker_failure_counts == {fresh_id: 1, poisoned_id: 1}


@pytest.mark.asyncio
async def test_check_trackers_budget_defers_new_keys_but_finishes_current(
    monkeypatch, mock_bot: AsyncMock, tick_db,
):
    """RV-SCH-02: once the time budget is spent no new key is started, but
    the key in flight completes (cursor, events, alert). The old 600 s
    ``wait_for`` cancelled it — and every queued alert of the tick."""
    from sqlalchemy import select

    sf = tick_db
    clock = {"now": 0.0}
    calls: list[dict] = []

    def _pages(query):
        clock["now"] += collector._CYCLE_BUDGET_SECONDS + 1  # a very slow Kufar
        return [_ad(51, 99_000)]

    monkeypatch.setattr(collector, "_monotonic", lambda: clock["now"])
    monkeypatch.setattr(collector, "KufarClient", _kufar_stub(_pages, calls))
    now = datetime.now(UTC)
    async with sf() as session:
        user = make_user(telegram_user_id=1501, first_name="Slow")
        session.add(user)
        await session.flush()
        first = Tracker(user_id=user.id, query="iphone 13", strict_mode=False,
                        last_checked_at=now - timedelta(minutes=40))
        deferred = Tracker(user_id=user.id, query="macbook air", strict_mode=False,
                           last_checked_at=now - timedelta(minutes=20))
        session.add_all([first, deferred])
        await session.commit()
        first_id, deferred_id = first.id, deferred.id

    await check_trackers(mock_bot, sf, _TICK_SETTINGS)

    assert [call["query"] for call in calls] == ["iphone 13"]
    assert await _event_keys(sf) == {(first_id, 51, "new_listing")}
    assert _sent_chats(mock_bot) == [1501]
    async with sf() as session:
        rows = dict((await session.execute(select(Tracker.id, Tracker.last_checked_at))).all())
    assert as_utc(rows[first_id]) > now
    # Not touched — still due, it leads the next tick.
    assert as_utc(rows[deferred_id]) < now


@pytest.mark.asyncio
async def test_check_trackers_failed_key_rolls_back_events_cursor_and_outbox(
    monkeypatch, mock_bot: AsyncMock, tick_db,
):
    """RV-SCH-02: a key that fails mid-way leaves nothing behind. The error
    handler used to sit inside the savepoint, which released it with the
    group half-written instead of rolling it back."""
    from sqlalchemy import select
    from sqlalchemy.exc import SQLAlchemyError

    from api.models import QuerySnapshot

    sf = tick_db
    monkeypatch.setattr(collector, "KufarClient", _kufar_stub(lambda q: [_ad(61, 99_000)], []))
    original = collector._enqueue_tracker_notifications

    def _enqueue_then_fail(*args, **kwargs):
        original(*args, **kwargs)
        raise SQLAlchemyError("boom")

    monkeypatch.setattr(collector, "_enqueue_tracker_notifications", _enqueue_then_fail)
    checked = datetime.now(UTC) - timedelta(minutes=20)
    async with sf() as session:
        user = make_user(telegram_user_id=1601, first_name="Rollback")
        session.add(user)
        await session.flush()
        tracker = Tracker(user_id=user.id, query="iphone 13", strict_mode=False,
                          last_checked_at=checked)
        session.add(tracker)
        await session.commit()
        tracker_id = tracker.id

    await check_trackers(mock_bot, sf, _TICK_SETTINGS)

    mock_bot.send_message.assert_not_called()
    assert await _event_keys(sf) == set()
    async with sf() as session:
        assert (await session.execute(select(TelegramNotificationDLQ))).first() is None
        assert (await session.execute(select(QuerySnapshot))).first() is None
        assert (await session.execute(select(QueryListingState))).first() is None
        stored = (await session.get(Tracker, tracker_id)).last_checked_at
    assert as_utc(stored) == checked


@pytest.mark.asyncio
async def test_concurrent_dlq_pumps_send_each_row_once(mock_bot: AsyncMock, tick_db):
    """RV-SCH-02: the tick's outbox drain and the per-minute pump job run
    in the same loop; without the pump lock both picked the same due rows
    and every message went out twice."""
    import asyncio

    sf = tick_db
    async with sf() as session:
        user = make_user(telegram_user_id=1701, first_name="Twice")
        session.add(user)
        await session.flush()
        _enqueue_tracker_notifications(session, [
            _TrackerNotifyJob(1701, user.id, f"msg-{i}", None) for i in range(3)
        ])
        await session.commit()

    async def _slow_send(tg_id, message, reply_markup=None):
        await asyncio.sleep(0.05)
        return MagicMock()

    mock_bot.send_message.side_effect = _slow_send
    sent = await asyncio.gather(
        retry_telegram_notification_dlq(mock_bot, sf),
        retry_telegram_notification_dlq(mock_bot, sf),
    )

    assert sum(sent) == 3
    assert mock_bot.send_message.await_count == 3


@pytest.mark.asyncio
async def test_dlq_pump_drops_permanently_rejected_rows(
    monkeypatch, mock_bot: AsyncMock, populated_session,
):
    """RV-SCH-02: a message Telegram rejects (BadRequest) is dropped at once
    instead of being re-sent until the retry budget runs out."""
    session, user, _tracker = populated_session
    session.add(TelegramNotificationDLQ(
        user_id=user.id,
        telegram_user_id=user.telegram_user_id,
        source="tracker",
        message="bad payload",
        error_kind="pending",
    ))
    await session.commit()

    async def _stub_classify(bot, tg_id, message, **kwargs):
        return "permanent_error", None

    monkeypatch.setattr(collector, "_send_message_classified", _stub_classify)

    sent = await retry_telegram_notification_dlq(mock_bot, get_session_factory(get_engine()))

    assert sent == 0
    rows = (await session.execute(TelegramNotificationDLQ.__table__.select())).all()
    assert rows == []


@pytest.mark.asyncio
async def test_enqueue_skips_alerts_already_waiting_in_outbox(tick_db):
    """RV-SCH-02 / AUDIT-LOW: cross-tracker dedup holds against rows already
    queued (an earlier key of the tick, or a copy still backing off) — not
    just within one batch."""
    from sqlalchemy import select

    sf = tick_db
    async with sf() as session:
        user = make_user(telegram_user_id=1801, first_name="Dedup")
        session.add(user)
        await session.flush()
        uid = user.id
        _enqueue_tracker_notifications(session, [
            _TrackerNotifyJob(1801, uid, "first", None, dedup_key="new_listing:5"),
        ])
        await session.commit()

    async with sf() as session:
        pending = await collector._pending_outbox_keys(session, {uid})
        assert pending == {(1801, "new_listing:5")}
        written = _enqueue_tracker_notifications(session, [
            _TrackerNotifyJob(1801, uid, "dup", None, dedup_key="new_listing:5"),
            _TrackerNotifyJob(1801, uid, "fresh", None, dedup_key="new_listing:6"),
        ], skip_keys=pending)
        await session.commit()
        messages = (await session.execute(select(TelegramNotificationDLQ.message))).scalars().all()

    assert written == 1
    assert sorted(messages) == ["first", "fresh"]


# ── RV-SEC-05: withdrawn pd_processing consent ──────────────────────────


async def _seed_consents(session, user_id: int, *states: str) -> None:
    """``states``: 'revoked' / 'active' pd_processing rows to add."""
    from api.models import UserConsent

    for state in states:
        session.add(UserConsent(
            user_id=user_id,
            consent_type="pd_processing",
            revoked_at=datetime.now(UTC) - timedelta(days=1) if state == "revoked" else None,
        ))


@pytest.mark.asyncio
async def test_dlq_pump_drops_messages_of_users_who_withdrew_consent(
    mock_bot: AsyncMock, tick_db,
):
    """RV-SEC-05: after a pd_processing revoke the DLQ/outbox kept
    delivering alerts. Rows of a user who revoked (and did not consent
    again) are deleted unsent; users without any consent rows — accounts
    older than the consent flow — keep getting theirs."""
    from sqlalchemy import select

    from api.models import UserConsent

    sf = tick_db
    async with sf() as session:
        revoked = make_user(telegram_user_id=1901, first_name="Revoked")
        legacy = make_user(telegram_user_id=1902, first_name="NoRecords")
        regranted = make_user(telegram_user_id=1903, first_name="Regranted")
        other_scope = make_user(telegram_user_id=1904, first_name="AiOnly")
        session.add_all([revoked, legacy, regranted, other_scope])
        await session.flush()
        await _seed_consents(session, revoked.id, "revoked")
        await _seed_consents(session, regranted.id, "revoked", "active")
        session.add(UserConsent(
            user_id=other_scope.id, consent_type="ai_analysis",
            revoked_at=datetime.now(UTC),
        ))
        _enqueue_tracker_notifications(session, [
            _TrackerNotifyJob(user.telegram_user_id, user.id, f"to-{user.first_name}", None)
            for user in (revoked, legacy, regranted, other_scope)
        ])
        await session.commit()

    sent = await retry_telegram_notification_dlq(mock_bot, sf)

    assert sent == 3
    assert sorted(_sent_chats(mock_bot)) == [1902, 1903, 1904]
    async with sf() as session:
        assert (await session.execute(select(TelegramNotificationDLQ))).first() is None


@pytest.mark.asyncio
async def test_check_trackers_skips_users_who_withdrew_consent(
    monkeypatch, mock_bot: AsyncMock, tick_db,
):
    """RV-SEC-05: a tracker left running for a user who withdrew
    pd_processing (e.g. resumed without consenting again) is not
    processed; a user with no consent records at all still is."""
    sf = tick_db
    calls: list[dict] = []
    monkeypatch.setattr(collector, "KufarClient", _kufar_stub(lambda q: [_ad(71, 99_000)], calls))
    async with sf() as session:
        revoked = make_user(telegram_user_id=2001, first_name="Revoked")
        legacy = make_user(telegram_user_id=2002, first_name="NoRecords")
        session.add_all([revoked, legacy])
        await session.flush()
        await _seed_consents(session, revoked.id, "revoked")
        session.add_all([
            Tracker(user_id=revoked.id, query="revoked query", strict_mode=False),
            Tracker(user_id=legacy.id, query="legacy query", strict_mode=False),
        ])
        await session.commit()

    await check_trackers(mock_bot, sf, _TICK_SETTINGS)

    assert [call["query"] for call in calls] == ["legacy query"]


# ── RV-SCH-05: threshold alerts ─────────────────────────────────────────


def _threshold_tracker(**overrides) -> MagicMock:
    tracker = MagicMock(spec=Tracker)
    tracker.id = 1
    tracker.user_id = 1
    tracker.query = "диван"
    tracker.strict_mode = False
    tracker.alert_price_threshold = 500
    tracker.alert_discount_percent = None
    tracker.seller_type = None
    tracker.condition = None
    tracker.region_name = None
    tracker.config_keyword = None
    tracker.category_id = None
    for key, value in overrides.items():
        setattr(tracker, key, value)
    return tracker


def _reliable_stats():
    from api.services.aggregator import PriceStats

    return PriceStats(mean=1000.0, median=1000.0, q1=900.0, q3=1100.0,
                      min=400.0, max=1300.0, count=20)


def test_threshold_alert_repeats_only_below_previously_alerted_price():
    """RV-SCH-05: a lot already alerted at 450 BYN is not alerted again at
    450 (it used to be, every 24 h); a real new drop re-alerts."""
    from decimal import Decimal

    tracker = _threshold_tracker()
    alerted = {(10, "price_threshold_alert"): Decimal("450.00")}

    same = collector._detect_threshold_alerts(
        {10: _ad(10, 45_000, "Диван")}, tracker,
        market_stats=_reliable_stats(), alerted=alerted,
    )
    assert same == []

    cheaper = collector._detect_threshold_alerts(
        {10: _ad(10, 40_000, "Диван")}, tracker,
        market_stats=_reliable_stats(), alerted=alerted,
    )
    assert [(e.ad_id, e.event_type, e.price_byn) for e in cheaper] == [
        (10, "price_threshold_alert", 400.0),
    ]


def test_threshold_and_discount_alerts_are_independent():
    """RV-SCH-05: a lot already alerted for the price threshold used to
    ``continue`` past the discount check and never got its discount alert."""
    from decimal import Decimal

    tracker = _threshold_tracker(alert_discount_percent=30)
    events = collector._detect_threshold_alerts(
        {10: _ad(10, 45_000, "Диван")}, tracker,
        market_stats=_reliable_stats(),
        alerted={(10, "price_threshold_alert"): Decimal("450.00")},
    )
    assert [(e.ad_id, e.event_type) for e in events] == [(10, "discount_alert")]


def test_free_lot_alerts_threshold_once_and_never_as_discount():
    """RV-SCH-05: a giveaway (price 0) is not a "-100%" discount alert, and
    its threshold alert is not repeated — 0 cannot drop any further."""
    from decimal import Decimal

    tracker = _threshold_tracker(alert_discount_percent=30)
    free_ad = {20: _ad(20, 0, "Отдам даром диван")}

    first = collector._detect_threshold_alerts(
        free_ad, tracker, market_stats=_reliable_stats(),
    )
    assert [(e.ad_id, e.event_type, e.price_type) for e in first] == [
        (20, "price_threshold_alert", "free"),
    ]
    again = collector._detect_threshold_alerts(
        free_ad, tracker, market_stats=_reliable_stats(),
        alerted={(20, "price_threshold_alert"): Decimal("0.00")},
    )
    assert again == []


def test_threshold_message_labels_free_lots():
    """RV-SCH-05: a giveaway reads "бесплатно", not "0 BYN"."""
    tracker = _threshold_tracker()
    event = TrackerEvent(
        event_type="price_threshold_alert", price_byn=0, price_type="free",
        title="Отдам даром диван", link="https://www.kufar.by/item/20",
        parameters={"threshold": 500.0},
    )
    msg = _build_threshold_message(tracker, [event])
    assert "💰 бесплатно (порог: 500 BYN)" in msg


@pytest.mark.asyncio
async def test_check_trackers_does_not_repeat_threshold_alert_after_24h(
    monkeypatch, mock_bot: AsyncMock, tick_db,
):
    """RV-SCH-05: the dedup now covers every retained event, not the last
    24 h — a lot alerted two days ago at the same price stays quiet; once
    its price drops further it is alerted again."""
    from sqlalchemy import select, update

    sf = tick_db
    price = {"lot": 45_000}

    def _pages(query):
        return [
            _ad(10, price["lot"], "Диван угловой"),
            _ad(11, 90_000, "Диван кожаный"),
            _ad(12, 100_000, "Диван раскладной"),
        ]

    monkeypatch.setattr(collector, "KufarClient", _kufar_stub(_pages, []))
    async with sf() as session:
        user = make_user(telegram_user_id=2101, first_name="Threshold")
        session.add(user)
        await session.flush()
        tracker = Tracker(
            user_id=user.id, query="диван", strict_mode=False,
            alert_price_threshold=500,
            last_checked_at=datetime.now(UTC) - timedelta(minutes=20),
        )
        session.add(tracker)
        await session.flush()
        session.add(TrackerEvent(
            tracker_id=tracker.id, user_id=user.id, ad_id=10, query="диван",
            strict_mode=False, event_type="price_threshold_alert",
            title="Диван угловой", link="https://www.kufar.by/item/10",
            price_byn=450, price_type="fixed", parameters={"threshold": 500.0},
            created_at=datetime.now(UTC) - timedelta(days=2),
        ))
        await session.commit()
        tracker_id = tracker.id

    async def _threshold_prices():
        async with sf() as session:
            rows = await session.execute(
                select(TrackerEvent.price_byn)
                .where(TrackerEvent.event_type == "price_threshold_alert")
                .order_by(TrackerEvent.id)
            )
            return [float(value) for value in rows.scalars()]

    await check_trackers(mock_bot, sf, _TICK_SETTINGS)
    assert await _threshold_prices() == [450.0]
    assert not any("ПОРОГ ЦЕНЫ" in call.args[1] for call in mock_bot.send_message.await_args_list)

    price["lot"] = 40_000
    async with sf() as session:
        await session.execute(
            update(Tracker)
            .where(Tracker.id == tracker_id)
            .values(last_checked_at=datetime.now(UTC) - timedelta(minutes=20))
        )
        await session.commit()
    await check_trackers(mock_bot, sf, _TICK_SETTINGS)
    assert await _threshold_prices() == [450.0, 400.0]
    assert any("ПОРОГ ЦЕНЫ" in call.args[1] for call in mock_bot.send_message.await_args_list)


# ── RV-SCH-04: reminder lifecycle ───────────────────────────────────────


async def _reminder_rows(sf) -> list[LeadReminder]:
    from sqlalchemy import select

    async with sf() as session:
        return list((await session.execute(
            select(LeadReminder).order_by(LeadReminder.id)
        )).scalars())


@pytest.mark.asyncio
async def test_check_reminders_permanent_error_is_terminal(mock_bot: AsyncMock, tick_db):
    """RV-SCH-04: a message Telegram rejects (BadRequest, e.g. a malformed
    chat) is not retried every 15 minutes forever."""
    from aiogram.exceptions import TelegramBadRequest

    sf = tick_db
    async with sf() as session:
        await _seed_due_reminder(session, telegram_user_id=2201, ad_id=9101)
        await session.commit()
    mock_bot.send_message.side_effect = _make_tg_error(TelegramBadRequest, "chat not found")

    await check_reminders(mock_bot, sf, _TICK_SETTINGS)
    [reminder] = await _reminder_rows(sf)
    assert (reminder.status, reminder.last_error, reminder.sent) == (
        "failed", "permanent_error", False,
    )

    mock_bot.send_message.reset_mock()
    await check_reminders(mock_bot, sf, _TICK_SETTINGS)
    mock_bot.send_message.assert_not_called()


@pytest.mark.asyncio
async def test_check_reminders_gives_up_after_max_attempts(mock_bot: AsyncMock, tick_db):
    """RV-SCH-04: transient failures back off, but not forever."""
    from aiogram.exceptions import TelegramAPIError

    sf = tick_db
    async with sf() as session:
        _user, _lead, reminder = await _seed_due_reminder(
            session, telegram_user_id=2301, ad_id=9102,
        )
        reminder.attempts = collector._REMINDER_MAX_ATTEMPTS - 1
        await session.commit()
    mock_bot.send_message.side_effect = _make_tg_error(TelegramAPIError, "Server error")

    await check_reminders(mock_bot, sf, _TICK_SETTINGS)

    [reminder] = await _reminder_rows(sf)
    assert (reminder.status, reminder.last_error) == ("failed", "retry_exhausted")
    assert reminder.attempts == collector._REMINDER_MAX_ATTEMPTS


@pytest.mark.asyncio
async def test_check_reminders_flood_control_waits_without_spending_attempts(
    mock_bot: AsyncMock, tick_db,
):
    """RV2-DS-03: a 429 is Telegram's pacing, not the reminder's failure.
    It used to cost an attempt each time, so a long flood wait could turn
    a deliverable reminder into ``failed``; now it waits ``retry_after``."""
    from aiogram.exceptions import TelegramRetryAfter

    sf = tick_db
    async with sf() as session:
        _user, _lead, reminder = await _seed_due_reminder(
            session, telegram_user_id=2311, ad_id=9111,
        )
        reminder.attempts = collector._REMINDER_MAX_ATTEMPTS - 1
        await session.commit()
    mock_bot.send_message.side_effect = TelegramRetryAfter(
        method=MagicMock(), message="rl", retry_after=600,
    )

    before = datetime.now(UTC)
    await check_reminders(mock_bot, sf, _TICK_SETTINGS)

    [reminder] = await _reminder_rows(sf)
    assert (reminder.status, reminder.last_error) == ("pending", "rate_limited")
    assert reminder.attempts == collector._REMINDER_MAX_ATTEMPTS - 1
    wait = as_utc(reminder.next_attempt_at) - before
    assert timedelta(seconds=590) < wait < timedelta(seconds=660)


@pytest.mark.asyncio
async def test_check_reminders_skips_reminders_already_marked_sent(
    mock_bot: AsyncMock, tick_db,
):
    """RV2-DS-11: during the 0026 rollout the old scheduler marks a
    delivered reminder ``sent`` without touching ``status`` (default
    ``pending``); the new due query must not deliver it again."""
    sf = tick_db
    async with sf() as session:
        _user, _lead, reminder = await _seed_due_reminder(
            session, telegram_user_id=2321, ad_id=9121,
        )
        reminder.sent = True
        await session.commit()

    await check_reminders(mock_bot, sf, _TICK_SETTINGS)

    mock_bot.send_message.assert_not_called()


@pytest.mark.asyncio
async def test_check_reminders_takes_oldest_due_first_and_respects_backoff(
    monkeypatch, mock_bot: AsyncMock, tick_db,
):
    """RV-SCH-04: ``LIMIT`` without ``ORDER BY`` and without a back-off
    filter let stuck rows hold the batch; the oldest due reminders that
    are not backing off go first."""
    sf = tick_db
    monkeypatch.setattr(collector, "_REMINDER_BATCH_LIMIT", 2)
    now = datetime.now(UTC)
    async with sf() as session:
        user, lead, newest = await _seed_due_reminder(session, telegram_user_id=2401, ad_id=9103)
        newest.remind_at = now - timedelta(minutes=10)
        newest.message = "newest"
        session.add_all([
            LeadReminder(lead_id=lead.id, user_id=user.id, message="backing off",
                         remind_at=now - timedelta(hours=4),
                         next_attempt_at=now + timedelta(minutes=30), attempts=1),
            LeadReminder(lead_id=lead.id, user_id=user.id, message="middle",
                         remind_at=now - timedelta(hours=1)),
            LeadReminder(lead_id=lead.id, user_id=user.id, message="oldest",
                         remind_at=now - timedelta(hours=3)),
        ])
        await session.commit()

    await check_reminders(mock_bot, sf, _TICK_SETTINGS)

    sent_texts = [call.args[1] for call in mock_bot.send_message.await_args_list]
    assert len(sent_texts) == 2
    assert {text.splitlines()[0] for text in sent_texts} == {
        "⏰ Напоминание: oldest", "⏰ Напоминание: middle",
    }
    statuses = {r.message: r.status for r in await _reminder_rows(sf)}
    assert statuses == {
        "oldest": "sent", "middle": "sent", "backing off": "pending", "newest": "pending",
    }


@pytest.mark.asyncio
async def test_check_reminders_cancels_reminders_on_closed_deals(mock_bot: AsyncMock, tick_db):
    """RV-SCH-04: a reminder on a deal the user has closed (sold, closed,
    abandoned, skipped) is cancelled instead of sent."""
    sf = tick_db
    async with sf() as session:
        _user, lead, _reminder = await _seed_due_reminder(
            session, telegram_user_id=2501, ad_id=9104,
        )
        lead.status = "sold"
        await session.commit()

    await check_reminders(mock_bot, sf, _TICK_SETTINGS)

    mock_bot.send_message.assert_not_called()
    [reminder] = await _reminder_rows(sf)
    assert (reminder.status, reminder.last_error) == ("cancelled", "lead_closed")


@pytest.mark.asyncio
async def test_check_reminders_respects_withdrawn_pd_consent(mock_bot: AsyncMock, tick_db):
    """RV-SEC-05: reminders kept coming after a pd_processing revoke. Only an
    explicit revoke (without a later grant) stops them — users with no
    consent rows still get theirs."""
    sf = tick_db
    async with sf() as session:
        revoked, _lead, _r = await _seed_due_reminder(session, telegram_user_id=2601, ad_id=9105)
        await _seed_due_reminder(session, telegram_user_id=2602, ad_id=9106)
        regranted, _lead, _r = await _seed_due_reminder(
            session, telegram_user_id=2603, ad_id=9107,
        )
        await _seed_consents(session, revoked.id, "revoked")
        await _seed_consents(session, regranted.id, "revoked", "active")
        await session.commit()
        revoked_id = revoked.id

    await check_reminders(mock_bot, sf, _TICK_SETTINGS)

    assert sorted(_sent_chats(mock_bot)) == [2602, 2603]
    by_user = {r.user_id: (r.status, r.last_error) for r in await _reminder_rows(sf)}
    assert by_user[revoked_id] == ("cancelled", "consent_revoked")


# ── RV2: scheduler review fixes ─────────────────────────────────────────


async def _outbox_rows(sf) -> list[TelegramNotificationDLQ]:
    from sqlalchemy import select

    async with sf() as session:
        return list(
            (await session.execute(
                select(TelegramNotificationDLQ).order_by(TelegramNotificationDLQ.id)
            )).scalars()
        )


async def _seed_outbox(sf, telegram_user_id: int, messages: list[str]) -> int:
    async with sf() as session:
        user = make_user(telegram_user_id=telegram_user_id, first_name="Outbox")
        session.add(user)
        await session.flush()
        _enqueue_tracker_notifications(session, [
            _TrackerNotifyJob(telegram_user_id, user.id, message, None) for message in messages
        ])
        await session.commit()
        return user.id


@pytest.mark.asyncio
async def test_dlq_pump_cancelled_mid_batch_does_not_resend_delivered_rows(
    mock_bot: AsyncMock, tick_db,
):
    """RV2-DS-02: the pump deleted delivered rows in one commit after the
    whole batch, so a pump cancelled mid-batch (SIGTERM on deploy) left
    them queued and they went out again after the restart."""
    import asyncio

    sf = tick_db
    await _seed_outbox(sf, 2401, ["first", "second", "third"])
    mock_bot.send_message.side_effect = [MagicMock(), asyncio.CancelledError()]

    with pytest.raises(asyncio.CancelledError):
        await retry_telegram_notification_dlq(mock_bot, sf)

    assert [row.message for row in await _outbox_rows(sf)] == ["second", "third"]


@pytest.mark.asyncio
async def test_dlq_pump_flood_control_defers_without_spending_retries(
    monkeypatch, mock_bot: AsyncMock, tick_db,
):
    """RV2-DS-03 / SCH-09: a 429 parks the chat's rows for ``retry_after``
    and records it; it used to count an attempt (and keep sending to the
    same chat), so a long flood wait exhausted the rows and deleted them."""
    from aiogram.exceptions import TelegramRetryAfter

    monkeypatch.setattr(collector, "_telegram_paused_until", None)
    sf = tick_db
    await _seed_outbox(sf, 2501, ["a", "b"])
    mock_bot.send_message.side_effect = TelegramRetryAfter(
        method=MagicMock(), message="rl", retry_after=120,
    )

    before = datetime.now(UTC)
    assert await retry_telegram_notification_dlq(mock_bot, sf) == 0

    assert mock_bot.send_message.await_count == 1
    rows = await _outbox_rows(sf)
    assert [(r.message, r.retry_count, r.retry_after_seconds, r.error_kind) for r in rows] == [
        ("a", 0, 120, "rate_limited"), ("b", 0, 120, "rate_limited"),
    ]
    for row in rows:
        wait = as_utc(row.next_retry_at) - before
        assert timedelta(seconds=115) < wait < timedelta(seconds=180)


@pytest.mark.asyncio
async def test_dlq_pump_rejected_bot_token_pauses_delivery_without_losing_rows(
    monkeypatch, mock_bot: AsyncMock, tick_db,
):
    """RV2-DS-03: a rejected bot token (401/404) fails every chat. Five
    pump passes used to exhaust and delete every queued alert within ~30
    minutes; now no attempt is counted and the pump stands down."""
    from aiogram.exceptions import TelegramUnauthorizedError

    monkeypatch.setattr(collector, "_telegram_paused_until", None)
    sf = tick_db
    await _seed_outbox(sf, 2601, ["a"])
    await _seed_outbox(sf, 2602, ["b"])
    mock_bot.send_message.side_effect = _make_tg_error(TelegramUnauthorizedError, "Unauthorized")

    assert await retry_telegram_notification_dlq(mock_bot, sf) == 0
    assert mock_bot.send_message.await_count == 2
    rows = await _outbox_rows(sf)
    assert [(r.retry_count, r.error_kind) for r in rows] == [(0, "unauthorized")] * 2

    # Even once the rows come due again, nothing is sent while paused.
    async with sf() as session:
        await session.execute(
            TelegramNotificationDLQ.__table__.update().values(next_retry_at=None)
        )
        await session.commit()
    assert await retry_telegram_notification_dlq(mock_bot, sf) == 0
    assert mock_bot.send_message.await_count == 2
    assert len(await _outbox_rows(sf)) == 2


@pytest.mark.asyncio
async def test_check_trackers_reuses_one_kufar_client_across_ticks(
    monkeypatch, mock_bot: AsyncMock, tick_db,
):
    """RV2-DS-05: a fresh client per tick reset the circuit breaker every
    tick, so each tick of an outage paid full retries (and strikes) on its
    first keys. The breaker now lives as long as the process."""
    sf = tick_db
    created: list[object] = []
    stub = _kufar_stub(lambda q: [_ad(71, 99_000)], [])

    class _CountingStub(stub):
        def __init__(self, settings):
            super().__init__(settings)
            created.append(self)

    monkeypatch.setattr(collector, "KufarClient", _CountingStub)
    async with sf() as session:
        user = make_user(telegram_user_id=2701, first_name="Reuse")
        session.add(user)
        await session.flush()
        session.add(Tracker(user_id=user.id, query="iphone 13", strict_mode=False))
        await session.commit()

    await check_trackers(mock_bot, sf, _TICK_SETTINGS)
    async with sf() as session:
        await session.execute(Tracker.__table__.update().values(last_checked_at=None))
        await session.commit()
    await check_trackers(mock_bot, sf, _TICK_SETTINGS)

    assert len(created) == 1


@pytest.mark.asyncio
async def test_check_trackers_kufar_back_off_is_not_a_strike(
    monkeypatch, mock_bot: AsyncMock, tick_db,
):
    """RV2-DS-05: a ``KufarAPIError`` carrying ``retry_after`` means the
    breaker opened during this request's retries or Kufar asked us to back
    off — an outage, not the key's fault. It used to strike the first two
    keys of every tick until their trackers were auto-paused."""
    from sqlalchemy import select

    from api.services.kufar_client import KufarAPIError

    sf = tick_db
    monkeypatch.setattr(
        collector,
        "KufarClient",
        _kufar_stub(lambda q: KufarAPIError("breaker opened", retry_after=30.0), []),
    )
    async with sf() as session:
        user = make_user(telegram_user_id=2801, first_name="Outage")
        session.add(user)
        await session.flush()
        session.add(Tracker(user_id=user.id, query="iphone 13", strict_mode=False))
        await session.commit()

    for _ in range(collector._TRACKER_AUTO_PAUSE_THRESHOLD + 1):
        await check_trackers(mock_bot, sf, _TICK_SETTINGS)

    async with sf() as session:
        tracker = (await session.execute(select(Tracker))).scalar_one()
    assert (tracker.paused, tracker.pause_reason) == (False, None)
    assert collector._tracker_failure_counts == {}


@pytest.mark.asyncio
async def test_check_trackers_auto_pause_commits_before_next_kufar_request(
    monkeypatch, mock_bot: AsyncMock, tick_db,
):
    """RV2-DS-12: the auto-pause UPDATE stayed uncommitted while the next
    key was fetched (up to ~50 s with retries), holding row locks that
    PATCH /trackers waits on."""
    from sqlalchemy import select

    from api.services.kufar_client import KufarAPIError

    sf = tick_db
    seen_paused: list[bool] = []

    def _pages(query):
        return KufarAPIError("HTTP 400")

    calls: list[dict] = []
    stub = _kufar_stub(_pages, calls)

    class _ObservingStub(stub):
        async def search(self, **kwargs):
            if kwargs["query"] == "macbook air":
                async with sf() as other:
                    seen_paused.append(
                        (await other.execute(
                            select(Tracker.paused).where(Tracker.query == "iphone 13")
                        )).scalar_one()
                    )
                return {"ads": [], "total": 0}
            return await super().search(**kwargs)

    monkeypatch.setattr(collector, "KufarClient", _ObservingStub)
    now = datetime.now(UTC)
    async with sf() as session:
        user = make_user(telegram_user_id=2901, first_name="Locks")
        session.add(user)
        await session.flush()
        failing = Tracker(user_id=user.id, query="iphone 13", strict_mode=False,
                          last_checked_at=now - timedelta(minutes=40))
        session.add_all([failing, Tracker(
            user_id=user.id, query="macbook air", strict_mode=False,
            last_checked_at=now - timedelta(minutes=20),
        )])
        await session.commit()
        collector._tracker_failure_counts[failing.id] = (
            collector._TRACKER_AUTO_PAUSE_THRESHOLD - 1
        )

    await check_trackers(mock_bot, sf, _TICK_SETTINGS)

    assert seen_paused == [True]


@pytest.mark.asyncio
async def test_check_trackers_empty_page_keeps_the_hour_snapshot(
    monkeypatch, mock_bot: AsyncMock, tick_db,
):
    """RV2-DS-06: an empty Kufar page overwrote the hour's snapshot with
    zeros before the outage guard ran — a permanent "0 BYN" point in the
    price history."""
    from sqlalchemy import select

    from api.models import QuerySnapshot

    sf = tick_db
    monkeypatch.setattr(collector, "KufarClient", _kufar_stub(lambda q: [], []))
    now = datetime.now(UTC)
    key = build_query_key("iphone 13", False)
    async with sf() as session:
        user = make_user(telegram_user_id=3001, first_name="Empty")
        session.add(user)
        await session.flush()
        session.add_all([
            Tracker(user_id=user.id, query="iphone 13", strict_mode=False,
                    last_checked_at=now - timedelta(minutes=20)),
            QuerySnapshot(
                query=key, snapshot_at=collector.snapshot_bucket(now),
                total_results=5, analyzed_count=5, mean_byn=1000, median_byn=1000,
                min_byn=900, max_byn=1100,
            ),
        ])
        await session.commit()

    await check_trackers(mock_bot, sf, _TICK_SETTINGS)

    async with sf() as session:
        snapshot = (await session.execute(select(QuerySnapshot))).scalar_one()
    assert (snapshot.total_results, float(snapshot.median_byn)) == (5, 1000.0)


@pytest.mark.asyncio
async def test_enqueue_dedups_long_keys_against_pending_rows(tick_db):
    """RV2-DS-10: keys longer than the column were cut on write but
    compared in full, so ``trend_reversal:<long search key>`` was never
    deduped against a pending row, and two long keys sharing a prefix
    collapsed into one."""
    sf = tick_db
    long_key = "trend_reversal:" + "x" * 200
    other_key = long_key + "y"
    stored = collector._outbox_dedup_key(long_key)
    assert len(stored) <= 128
    assert collector._outbox_dedup_key(other_key) != stored
    assert collector._outbox_dedup_key("new_listing:5") == "new_listing:5"

    async with sf() as session:
        user = make_user(telegram_user_id=3101, first_name="Long")
        session.add(user)
        await session.flush()
        uid = user.id
        _enqueue_tracker_notifications(session, [
            _TrackerNotifyJob(3101, uid, "first", None, dedup_key=long_key),
        ])
        await session.commit()

    async with sf() as session:
        pending = await collector._pending_outbox_keys(session, {uid})
        written = _enqueue_tracker_notifications(session, [
            _TrackerNotifyJob(3101, uid, "dup", None, dedup_key=long_key),
            _TrackerNotifyJob(3101, uid, "other", None, dedup_key=other_key),
        ], skip_keys=pending)
        await session.commit()

    assert written == 1
    assert [row.message for row in await _outbox_rows(sf)] == ["first", "other"]


@pytest.mark.asyncio
async def test_check_trackers_limit_applies_to_due_trackers_only(
    monkeypatch, mock_bot: AsyncMock, tick_db,
):
    """RV2-DS-04: the LIMIT ran before the exact interval check, so hourly
    trackers that were not due yet — but had older cursors — filled it and
    the due 15-minute trackers waited. Intervals under the 15-minute floor
    are still checked at the floor."""
    sf = tick_db
    calls: list[dict] = []
    monkeypatch.setattr(collector, "_DUE_TRACKER_LIMIT", 1)
    monkeypatch.setattr(collector, "KufarClient", _kufar_stub(lambda q: [], calls))
    now = datetime.now(UTC)
    async with sf() as session:
        user = make_user(telegram_user_id=3201, first_name="Starved")
        session.add(user)
        await session.flush()
        session.add_all([
            Tracker(user_id=user.id, query="macbook air", strict_mode=False,
                    interval_min=60, last_checked_at=now - timedelta(minutes=30)),
            Tracker(user_id=user.id, query="ipad mini", strict_mode=False,
                    interval_min=5, last_checked_at=now - timedelta(minutes=10)),
            Tracker(user_id=user.id, query="iphone 13", strict_mode=False,
                    interval_min=15, last_checked_at=now - timedelta(minutes=20)),
        ])
        await session.commit()

    await check_trackers(mock_bot, sf, _TICK_SETTINGS)

    assert [call["query"] for call in calls] == ["iphone 13"]
