from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
)
from aiogram.methods import SendMessage
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

import scheduler.collector as collector
from api.config import get_settings
from api.database import get_engine, get_session_factory
from api.models import QueryListingState, QuerySnapshot, Tracker, TrackerEvent
from api.services.aggregator import build_query_key, normalize_search_text
from api.services.history_service import BASELINE_MARKER_AD_ID, snapshot_bucket
from api.services.kufar_client import KufarAPIError, KufarClient
from api.services.users import ensure_user

# Listings are old unless a test says otherwise, so paging decisions do not depend on
# the wall clock.
OLD_LIST_TIME = "2020-01-01T10:00:00Z"


def make_ad(
    ad_id: int,
    price_byn: float,
    subject: str = "iPhone 15 128GB",
    list_time: str = OLD_LIST_TIME,
) -> dict[str, Any]:
    return {
        "ad_id": ad_id,
        "subject": subject,
        "price_byn": int(price_byn * 100),
        "ad_link": f"https://www.kufar.by/item/{ad_id}",
        "list_time": list_time,
    }


def fresh_list_time() -> str:
    return datetime.now(UTC).isoformat()


class FakeKufarClient(KufarClient):
    """Serves listings from ``catalog`` (keyed by normalized query), newest first."""

    catalog: dict[str, list[dict[str, Any]]] = {}
    failing: set[str] = set()
    failing_cursors: set[str] = set()
    calls: list[tuple[str, str | None]] = []

    def __init__(self, settings: Any, http_client: Any = None) -> None:
        pass

    async def aclose(self) -> None:
        pass

    async def search(
        self,
        query: str,
        size: int = 200,
        currency: str = "USD",
        sort: str = "lst.d",
        cursor: str | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        type(self).calls.append((query, cursor))
        key = normalize_search_text(query)
        if key in self.failing or (cursor is not None and cursor in self.failing_cursors):
            raise KufarAPIError("Kufar is down")
        ads = self.catalog.get(key, [])
        start = int(cursor or 0)
        page = ads[start : start + size]
        payload: dict[str, Any] = {"ads": [dict(ad) for ad in page], "total": len(ads)}
        if start + size < len(ads):
            payload["pagination"] = {"pages": [{"label": "next", "token": str(start + size)}]}
        return payload


class FakeBot:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str, Any]] = []
        self.errors: dict[int, list[Exception]] = {}
        self.attempts: list[int] = []

    async def send_message(self, chat_id: int, text: str, reply_markup: Any = None) -> None:
        self.attempts.append(chat_id)
        pending = self.errors.get(chat_id)
        if pending:
            raise pending.pop(0)
        self.sent.append((chat_id, text, reply_markup))

    def recipients(self) -> list[int]:
        return sorted(chat_id for chat_id, _, _ in self.sent)


def telegram_method(chat_id: int = 1) -> SendMessage:
    return SendMessage(chat_id=chat_id, text="x")


@pytest.fixture
def kufar(monkeypatch: pytest.MonkeyPatch) -> type[FakeKufarClient]:
    monkeypatch.setattr(FakeKufarClient, "catalog", {})
    monkeypatch.setattr(FakeKufarClient, "failing", set())
    monkeypatch.setattr(FakeKufarClient, "failing_cursors", set())
    monkeypatch.setattr(FakeKufarClient, "calls", [])
    monkeypatch.setattr(collector, "KufarClient", FakeKufarClient)
    monkeypatch.setattr(collector, "SEND_MIN_INTERVAL_SECONDS", 0)
    return FakeKufarClient


@pytest.fixture
def sleeps(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Record the send backoff instead of sleeping."""
    delays: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        delays.append(seconds)

    monkeypatch.setattr(collector, "_sleep", fake_sleep)
    return delays


@pytest.fixture
async def session_factory():
    engine = get_engine()
    yield get_session_factory(engine)
    await engine.dispose()


async def add_trackers(
    session_factory, *specs: tuple[int, str], strict_mode: bool = False
) -> list[int]:
    async with session_factory() as session:
        for user_id in sorted({user_id for user_id, _ in specs}):
            await ensure_user(session, user_id)
        trackers = [
            Tracker(user_id=user_id, query=query, strict_mode=strict_mode)
            for user_id, query in specs
        ]
        session.add_all(trackers)
        await session.commit()
        return [tracker.id for tracker in trackers]


async def run_check(bot: FakeBot, session_factory) -> None:
    await collector.check_trackers(bot, session_factory, get_settings())  # type: ignore[arg-type]


async def load_events(session_factory) -> list[TrackerEvent]:
    async with session_factory() as session:
        return list(
            (await session.execute(select(TrackerEvent).order_by(TrackerEvent.id))).scalars()
        )


async def test_first_run_records_baseline_then_alerts_new_listing(kufar, session_factory) -> None:
    kufar.catalog["iphone 15 128"] = [make_ad(2, 2000), make_ad(1, 1900)]
    await add_trackers(session_factory, (111, "iphone 15 128"))
    bot = FakeBot()

    await run_check(bot, session_factory)
    assert bot.sent == []
    async with session_factory() as session:
        tracker = await session.scalar(select(Tracker))
        assert tracker is not None
        assert tracker.last_checked_at is not None
        assert tracker.last_seen_ad_id == 2
        states = list((await session.execute(select(QueryListingState))).scalars())
    assert {state.query for state in states} == {"broad::iphone 15 128"}
    assert len(states) == 2

    kufar.catalog["iphone 15 128"].insert(0, make_ad(3, 1500, "iPhone 15 128GB Blue"))
    await run_check(bot, session_factory)
    assert bot.recipients() == [111]
    _, text, keyboard = bot.sent[0]
    assert "Новые объявления: 1" in text
    assert "iPhone 15 128GB Blue" in text
    # A single lot needs no hint about which lot the buttons belong to.
    assert "Кнопки ниже" not in text
    events = await load_events(session_factory)
    assert [(event.ad_id, event.event_type) for event in events] == [(3, "new_listing")]
    assert keyboard.inline_keyboard[1][0].callback_data == f"lead:{events[0].id}"

    # Nothing changed: no repeated alert.
    await run_check(bot, session_factory)
    assert len(bot.sent) == 1


async def test_price_drop_respects_threshold(kufar, session_factory) -> None:
    kufar.catalog["iphone 15 128"] = [make_ad(1, 2000)]
    await add_trackers(session_factory, (111, "iphone 15 128"))
    bot = FakeBot()
    await run_check(bot, session_factory)

    # 0.25% looks like exchange-rate noise and is ignored.
    kufar.catalog["iphone 15 128"] = [make_ad(1, 1995)]
    await run_check(bot, session_factory)
    assert bot.sent == []

    kufar.catalog["iphone 15 128"] = [make_ad(1, 1800)]
    await run_check(bot, session_factory)
    assert bot.recipients() == [111]
    assert "Снижение цены: 1" in bot.sent[0][1]
    assert "(-195 р.)" in bot.sent[0][1]
    events = await load_events(session_factory)
    assert [(event.event_type, round(event.delta_byn or 0)) for event in events] == [
        ("price_drop", 195)
    ]


async def test_equivalent_queries_share_one_fetch_and_all_get_alerts(
    kufar, session_factory
) -> None:
    kufar.catalog["iphone 15 128"] = [make_ad(1, 2000)]
    await add_trackers(
        session_factory,
        (111, "iphone 15 128"),
        (222, "iPhone 15 128GB"),
        (333, "айфон 15 128"),
    )
    bot = FakeBot()
    await run_check(bot, session_factory)
    assert len(kufar.calls) == 1
    assert kufar.calls[0][0] == "iphone 15 128"

    kufar.catalog["iphone 15 128"] = [make_ad(2, 1700), make_ad(1, 1500)]
    kufar.calls.clear()
    await run_check(bot, session_factory)
    assert len(kufar.calls) == 1
    assert bot.recipients() == [111, 222, 333]
    for _, text, _ in bot.sent:
        assert "Новые объявления: 1" in text
        assert "Снижение цены: 1" in text
        assert "Кнопки ниже относятся к лоту «iPhone 15 128GB»" in text
    events = await load_events(session_factory)
    assert len(events) == 6
    assert {event.query for event in events} == {
        "iphone 15 128",
        "iPhone 15 128GB",
        "айфон 15 128",
    }


async def test_failing_group_does_not_roll_back_other_groups(kufar, session_factory) -> None:
    kufar.catalog["aaa"] = [make_ad(1, 100, "aaa phone")]
    kufar.catalog["zzz"] = [make_ad(11, 100, "zzz phone")]
    kufar.catalog["bbb"] = [make_ad(21, 100, "bbb phone")]
    await add_trackers(session_factory, (111, "aaa"), (222, "zzz"), (333, "bbb"))
    bot = FakeBot()
    await run_check(bot, session_factory)

    kufar.catalog["aaa"].insert(0, make_ad(2, 90, "aaa phone new"))
    kufar.catalog["zzz"].insert(0, make_ad(12, 90, "zzz phone new"))
    kufar.catalog["bbb"].insert(0, make_ad(22, 90, "bbb phone new"))
    kufar.failing.add("zzz")
    await run_check(bot, session_factory)
    assert bot.recipients() == [111, 333]
    assert {event.ad_id for event in await load_events(session_factory)} == {2, 22}

    # The next run neither repeats delivered alerts nor loses the failed group.
    kufar.failing.clear()
    await run_check(bot, session_factory)
    assert bot.recipients() == [111, 222, 333]
    assert {event.ad_id for event in await load_events(session_factory)} == {2, 12, 22}


async def test_database_error_in_one_group_is_isolated(
    kufar, session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    kufar.catalog["aaa"] = [make_ad(1, 100, "aaa phone")]
    kufar.catalog["zzz"] = [make_ad(11, 100, "zzz phone")]
    await add_trackers(session_factory, (111, "zzz"), (333, "aaa"))
    bot = FakeBot()
    await run_check(bot, session_factory)

    original_sync = collector.sync_query_listing_states

    async def flaky_sync(session: Any, *, query: str, **kwargs: Any) -> Any:
        if query == "broad::zzz":
            raise SQLAlchemyError("value too long for type character varying(255)")
        return await original_sync(session, query=query, **kwargs)

    monkeypatch.setattr(collector, "sync_query_listing_states", flaky_sync)
    kufar.catalog["aaa"].insert(0, make_ad(2, 90, "aaa phone new"))
    kufar.catalog["zzz"].insert(0, make_ad(12, 90, "zzz phone new"))
    await run_check(bot, session_factory)
    assert bot.recipients() == [333]
    async with session_factory() as session:
        trackers = list((await session.execute(select(Tracker).order_by(Tracker.id))).scalars())
    # The failed group keeps its previous check time and is retried next run.
    assert trackers[0].last_checked_at < trackers[1].last_checked_at


async def test_telegram_error_for_one_user_does_not_stop_others(kufar, session_factory) -> None:
    kufar.catalog["aaa"] = [make_ad(1, 100, "aaa phone")]
    kufar.catalog["bbb"] = [make_ad(21, 100, "bbb phone")]
    await add_trackers(session_factory, (111, "aaa"), (333, "bbb"))
    bot = FakeBot()
    await run_check(bot, session_factory)

    kufar.catalog["aaa"].insert(0, make_ad(2, 90, "aaa phone new"))
    kufar.catalog["bbb"].insert(0, make_ad(22, 90, "bbb phone new"))
    bot.errors[111] = [TelegramBadRequest(telegram_method(111), "Bad Request: BUTTON_URL_INVALID")]
    await run_check(bot, session_factory)
    assert bot.recipients() == [333]
    assert len(await load_events(session_factory)) == 2

    await run_check(bot, session_factory)
    assert bot.recipients() == [333]


async def test_forbidden_deactivates_all_user_trackers(
    kufar, session_factory, caplog: pytest.LogCaptureFixture
) -> None:
    kufar.catalog["aaa"] = [make_ad(1, 100, "aaa phone")]
    kufar.catalog["bbb"] = [make_ad(21, 100, "bbb phone")]
    await add_trackers(session_factory, (111, "aaa"), (111, "bbb"), (333, "aaa"))
    bot = FakeBot()
    await run_check(bot, session_factory)

    kufar.catalog["aaa"].insert(0, make_ad(2, 90, "aaa phone new"))
    kufar.catalog["bbb"].insert(0, make_ad(22, 90, "bbb phone new"))
    bot.errors[111] = [
        TelegramForbiddenError(telegram_method(111), "Forbidden: bot was blocked by the user")
    ]
    with caplog.at_level(logging.WARNING, logger="scheduler.collector"):
        await run_check(bot, session_factory)

    assert bot.recipients() == [333]
    # The second alert of the blocked user is not even attempted.
    assert bot.attempts.count(111) == 1
    async with session_factory() as session:
        trackers = list((await session.execute(select(Tracker).order_by(Tracker.id))).scalars())
    assert [(tracker.user_id, tracker.active) for tracker in trackers] == [
        (111, False),
        (111, False),
        (333, True),
    ]
    assert any("blocked the bot" in record.getMessage() for record in caplog.records)


async def test_retry_after_is_retried_once(kufar, session_factory) -> None:
    kufar.catalog["aaa"] = [make_ad(1, 100, "aaa phone")]
    await add_trackers(session_factory, (111, "aaa"))
    bot = FakeBot()
    await run_check(bot, session_factory)

    kufar.catalog["aaa"].insert(0, make_ad(2, 90, "aaa phone new"))
    bot.errors[111] = [TelegramRetryAfter(telegram_method(111), "Flood control", retry_after=0)]
    await run_check(bot, session_factory)
    assert bot.attempts == [111, 111]
    assert bot.recipients() == [111]


async def test_repeated_retry_after_gives_up_without_failing_run(kufar, session_factory) -> None:
    kufar.catalog["aaa"] = [make_ad(1, 100, "aaa phone")]
    kufar.catalog["bbb"] = [make_ad(21, 100, "bbb phone")]
    await add_trackers(session_factory, (111, "aaa"), (333, "bbb"))
    bot = FakeBot()
    await run_check(bot, session_factory)

    kufar.catalog["aaa"].insert(0, make_ad(2, 90, "aaa phone new"))
    kufar.catalog["bbb"].insert(0, make_ad(22, 90, "bbb phone new"))
    bot.errors[111] = [
        TelegramRetryAfter(telegram_method(111), "Flood control", retry_after=0)
        for _ in range(collector.MAX_SEND_ATTEMPTS)
    ]
    await run_check(bot, session_factory)
    assert bot.attempts.count(111) == collector.MAX_SEND_ATTEMPTS
    assert bot.recipients() == [333]


async def test_paging_follows_cursor_until_known_listings(
    kufar, session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(collector, "PAGE_SIZE", 2)
    kufar.catalog["aaa"] = [make_ad(ad_id, 100 + ad_id, "aaa phone") for ad_id in range(10, 0, -1)]
    await add_trackers(session_factory, (111, "aaa"))
    bot = FakeBot()

    # Baseline: nothing is known yet, so the maximum number of pages is fetched.
    await run_check(bot, session_factory)
    assert [cursor for _, cursor in kufar.calls] == [None, "2", "4"]
    async with session_factory() as session:
        states = list((await session.execute(select(QueryListingState))).scalars())
    assert len(states) == 2 * collector.MAX_PAGES_PER_GROUP

    # Two new listings fill the first page; the second one is fully known, so paging stops.
    kufar.catalog["aaa"][:0] = [make_ad(ad_id, 50, "aaa phone") for ad_id in (12, 11)]
    kufar.calls.clear()
    await run_check(bot, session_factory)
    assert [cursor for _, cursor in kufar.calls] == [None, "2"]
    assert "Новые объявления: 2" in bot.sent[0][1]

    kufar.calls.clear()
    await run_check(bot, session_factory)
    assert [cursor for _, cursor in kufar.calls] == [None]
    assert len(bot.sent) == 1


async def test_scheduler_does_not_overwrite_api_snapshot(kufar, session_factory) -> None:
    key = build_query_key("aaa", False)
    bucket = snapshot_bucket(datetime.now(UTC))
    async with session_factory() as session:
        session.add(
            QuerySnapshot(
                query=key,
                snapshot_at=bucket,
                total_results=800,
                analyzed_count=800,
                mean_byn=500,
                median_byn=500,
                min_byn=100,
                max_byn=900,
            )
        )
        await session.commit()

    kufar.catalog["aaa"] = [make_ad(1, 100, "aaa phone")]
    await add_trackers(session_factory, (111, "aaa"))
    await run_check(FakeBot(), session_factory)

    async with session_factory() as session:
        snapshots = list((await session.execute(select(QuerySnapshot))).scalars())
    assert len(snapshots) == 1
    assert snapshots[0].analyzed_count == 800
    assert snapshots[0].median_byn == 500


async def test_scheduler_writes_no_snapshots(kufar, session_factory) -> None:
    # Its sample of recent pages is not comparable with the full scans of /price-stats,
    # so price history comes only from the API (strict and broad alike).
    kufar.catalog["aaa"] = [make_ad(1, 100, "aaa phone"), make_ad(2, 300, "aaa phone")]
    await add_trackers(session_factory, (111, "aaa"))
    await add_trackers(session_factory, (222, "aaa"), strict_mode=True)
    bot = FakeBot()
    await run_check(bot, session_factory)
    kufar.catalog["aaa"].insert(0, make_ad(3, 90, "aaa phone new"))
    await run_check(bot, session_factory)
    assert bot.recipients() == [111, 222]

    async with session_factory() as session:
        snapshots = list((await session.execute(select(QuerySnapshot))).scalars())
    assert snapshots == []


async def test_run_cleanup_removes_old_snapshots_and_untracked_states(session_factory) -> None:
    now = datetime.now(UTC)
    old = now - timedelta(days=200)
    await add_trackers(session_factory, (111, "tracked"))
    async with session_factory() as session:
        session.add_all(
            [
                QuerySnapshot(query="broad::x", snapshot_at=snapshot_at, analyzed_count=1)
                for snapshot_at in (old, now)
            ]
        )
        for ad_id, query, seen_at, active in (
            (1, "broad::tracked", old, False),  # tracked key: kept for a year
            (2, "broad::untracked", old, True),  # untracked, stale even though active
            (3, "broad::untracked", now, True),  # untracked but recent
        ):
            session.add(
                QueryListingState(
                    query=query,
                    ad_id=ad_id,
                    title="t",
                    link="",
                    active=active,
                    first_seen_at=seen_at,
                    last_seen_at=seen_at,
                )
            )
        await session.commit()

    await collector.run_cleanup(session_factory)

    async with session_factory() as session:
        snapshots = list((await session.execute(select(QuerySnapshot))).scalars())
        states = list((await session.execute(select(QueryListingState))).scalars())
    assert len(snapshots) == 1
    assert sorted(state.ad_id for state in states) == [1, 3]


async def test_check_db_health_never_raises() -> None:
    class BrokenEngine:
        def connect(self) -> Any:
            raise ConnectionRefusedError("connection refused")

    assert await collector.check_db_health(BrokenEngine()) is False  # type: ignore[arg-type]


async def test_wait_for_database_retries_with_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    results = iter([False, False, True])
    delays: list[float] = []

    async def fake_health(engine: Any) -> bool:
        return next(results)

    async def fake_sleep(delay: float) -> None:
        delays.append(delay)

    monkeypatch.setattr(collector, "check_db_health", fake_health)
    monkeypatch.setattr(collector.asyncio, "sleep", fake_sleep)
    await collector.wait_for_database(object(), initial_delay=1, max_delay=60)  # type: ignore[arg-type]
    assert delays == [1, 2]


async def test_create_scheduler_runs_first_check_immediately(session_factory) -> None:
    before = datetime.now(UTC)
    scheduler = collector.create_scheduler(
        FakeBot(),  # type: ignore[arg-type]
        session_factory,
        get_settings(),
    )
    scheduler.start(paused=True)
    try:
        job = scheduler.get_job("tracker-check")
        assert job is not None
        assert job.next_run_time <= before + timedelta(seconds=5)
    finally:
        scheduler.shutdown(wait=False)


async def test_late_first_check_still_runs(
    session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The first check is scheduled before a slow database wait; APScheduler's default
    # misfire grace of one second used to drop it until a whole interval later.
    ran = asyncio.Event()

    async def fake_check_trackers(**_: Any) -> None:
        ran.set()

    monkeypatch.setattr(collector, "check_trackers", fake_check_trackers)
    scheduler = collector.create_scheduler(
        FakeBot(),  # type: ignore[arg-type]
        session_factory,
        get_settings(),
        first_run_at=datetime.now(UTC) - timedelta(seconds=30),
    )
    scheduler.start()
    try:
        await asyncio.wait_for(ran.wait(), timeout=5)
    finally:
        scheduler.shutdown(wait=False)


# --- baseline of a key without remembered listings ---------------------------------------------


async def test_key_without_states_records_baseline_without_alerts(kufar, session_factory) -> None:
    # A tracker checked before a deploy that changed the query normalization: its states
    # live under the old key, the new key has none, so every listing would look new.
    kufar.catalog["aaa"] = [make_ad(ad_id, 100 + ad_id, "aaa phone") for ad_id in (3, 2, 1)]
    (tracker_id,) = await add_trackers(session_factory, (111, "aaa"))
    previous_check = datetime.now(UTC) - timedelta(hours=1)
    async with session_factory() as session:
        tracker = await session.get(Tracker, tracker_id)
        assert tracker is not None
        tracker.last_checked_at = previous_check
        session.add(
            QueryListingState(
                query="broad::aaa (old key)",
                ad_id=1,
                title="aaa phone",
                link="",
                first_seen_at=previous_check,
                last_seen_at=previous_check,
            )
        )
        await session.commit()

    bot = FakeBot()
    await run_check(bot, session_factory)
    assert bot.sent == []
    assert await load_events(session_factory) == []
    async with session_factory() as session:
        states = list(
            (
                await session.execute(
                    select(QueryListingState).where(QueryListingState.query == "broad::aaa")
                )
            ).scalars()
        )
        tracker = await session.get(Tracker, tracker_id)
    assert sorted(state.ad_id for state in states) == [1, 2, 3]
    assert tracker is not None
    assert tracker.last_checked_at.replace(tzinfo=UTC) > previous_check

    kufar.catalog["aaa"].insert(0, make_ad(4, 90, "aaa phone new"))
    await run_check(bot, session_factory)
    assert bot.recipients() == [111]
    assert "Новые объявления: 1" in bot.sent[0][1]


async def test_empty_baseline_still_alerts_the_first_listing(kufar, session_factory) -> None:
    # A rare query without results: the baseline is remembered by a marker state, so the
    # first listing that appears later is reported.
    await add_trackers(session_factory, (111, "rare thing"))
    bot = FakeBot()
    await run_check(bot, session_factory)
    async with session_factory() as session:
        states = list((await session.execute(select(QueryListingState))).scalars())
    assert [(state.ad_id, state.active) for state in states] == [(BASELINE_MARKER_AD_ID, False)]

    await run_check(bot, session_factory)
    kufar.catalog["rare thing"] = [make_ad(5, 100, "rare thing")]
    await run_check(bot, session_factory)
    assert bot.recipients() == [111]
    assert "Новые объявления: 1" in bot.sent[0][1]
    events = await load_events(session_factory)
    assert [event.ad_id for event in events] == [5]


# --- paging ----------------------------------------------------------------------------------


async def test_strict_paging_continues_past_fresh_page_without_strict_matches(
    kufar, session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(collector, "PAGE_SIZE", 2)
    kufar.catalog["iphone 15"] = [make_ad(2, 2000), make_ad(1, 1900)]
    await add_trackers(session_factory, (111, "iphone 15"), strict_mode=True)
    bot = FakeBot()
    await run_check(bot, session_factory)

    # The first page holds only fresh "Pro" and "Max" listings: nothing passes the strict
    # filter, but the page is newer than the previous check, so the next one is fetched.
    fresh = fresh_list_time()
    kufar.catalog["iphone 15"][:0] = [
        make_ad(10, 3000, "iPhone 15 Pro 256GB", list_time=fresh),
        make_ad(11, 3500, "iPhone 15 Pro Max", list_time=fresh),
        make_ad(12, 1800, "iPhone 15 128GB Black", list_time=fresh),
    ]
    kufar.calls.clear()
    await run_check(bot, session_factory)
    assert [cursor for _, cursor in kufar.calls] == [None, "2", "4"]
    assert bot.recipients() == [111]
    assert "Новые объявления: 1" in bot.sent[0][1]
    assert "iPhone 15 128GB Black" in bot.sent[0][1]


async def test_paging_stops_on_known_page_older_than_previous_check(
    kufar, session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(collector, "PAGE_SIZE", 2)
    kufar.catalog["aaa"] = [make_ad(ad_id, 100, "aaa phone") for ad_id in range(6, 0, -1)]
    await add_trackers(session_factory, (111, "aaa"), strict_mode=True)
    bot = FakeBot()
    await run_check(bot, session_factory)

    kufar.calls.clear()
    await run_check(bot, session_factory)
    assert [cursor for _, cursor in kufar.calls] == [None]


# --- deep scan -------------------------------------------------------------------------------


@pytest.fixture
def deep_scan(monkeypatch: pytest.MonkeyPatch) -> dict[str, bool]:
    """Let the test decide when a run is a deep scan."""
    state = {"due": False}
    monkeypatch.setattr(collector, "deep_scan_is_due", lambda *_args, **_kw: state["due"])
    monkeypatch.setattr(collector, "PAGE_SIZE", 2)
    monkeypatch.setattr(collector, "DEEP_SCAN_MAX_PAGES", 5)
    return state


async def test_deep_scan_reports_price_drop_of_older_listing(
    kufar, session_factory, deep_scan: dict[str, bool]
) -> None:
    kufar.catalog["aaa"] = [make_ad(ad_id, 1000, "aaa phone") for ad_id in range(10, 0, -1)]
    await add_trackers(session_factory, (111, "aaa"))
    bot = FakeBot()
    await run_check(bot, session_factory)
    assert [cursor for _, cursor in kufar.calls] == [None, "2", "4"]

    # The first deep scan pages further and records the older listings silently.
    deep_scan["due"] = True
    kufar.calls.clear()
    await run_check(bot, session_factory)
    assert [cursor for _, cursor in kufar.calls] == [None, "2", "4", "6", "8"]
    assert bot.sent == []
    async with session_factory() as session:
        states = list((await session.execute(select(QueryListingState))).scalars())
    assert sorted(state.ad_id for state in states) == list(range(1, 11))

    # A price drop on page 5 is invisible to the regular runs...
    kufar.catalog["aaa"][8] = make_ad(2, 800, "aaa phone")
    deep_scan["due"] = False
    kufar.calls.clear()
    await run_check(bot, session_factory)
    assert [cursor for _, cursor in kufar.calls] == [None]
    assert bot.sent == []

    # ...and reported by the next deep scan.
    deep_scan["due"] = True
    await run_check(bot, session_factory)
    assert bot.recipients() == [111]
    assert "Снижение цены: 1" in bot.sent[0][1]
    assert "Новые объявления" not in bot.sent[0][1]
    events = await load_events(session_factory)
    assert [(event.ad_id, event.event_type) for event in events] == [(2, "price_drop")]


async def test_deep_scan_failure_beyond_window_keeps_group_result(
    kufar, session_factory, deep_scan: dict[str, bool]
) -> None:
    kufar.catalog["aaa"] = [make_ad(ad_id, 1000, "aaa phone") for ad_id in range(10, 0, -1)]
    await add_trackers(session_factory, (111, "aaa"))
    bot = FakeBot()
    await run_check(bot, session_factory)

    deep_scan["due"] = True
    kufar.failing_cursors.add("8")
    kufar.catalog["aaa"].insert(0, make_ad(11, 900, "aaa phone new"))
    kufar.calls.clear()
    await run_check(bot, session_factory)
    assert [cursor for _, cursor in kufar.calls] == [None, "2", "4", "6", "8"]
    assert bot.recipients() == [111]
    assert "Новые объявления: 1" in bot.sent[0][1]


def test_deep_scan_is_due_once_per_window_per_key() -> None:
    start = datetime(2026, 10, 11, 0, 0, tzinfo=UTC)
    interval = timedelta(hours=6)
    assert not collector.deep_scan_is_due("broad::aaa", None, start, interval=interval)

    for key in ("broad::aaa", "strict::iphone 15", "broad::galaxy s24"):
        runs = [start + timedelta(minutes=30 * step) for step in range(1, 49)]  # one day
        due = [
            collector.deep_scan_is_due(key, previous, now, interval=interval)
            for previous, now in zip([start, *runs[:-1]], runs, strict=True)
        ]
        assert sum(due) == 4
        # A group that was not checked for longer than the window scans deeply.
        assert collector.deep_scan_is_due(
            key, start, start + timedelta(hours=7), interval=interval
        )

    # SQLite returns naive datetimes; they are UTC.
    assert collector.deep_scan_is_due(
        "broad::aaa", start.replace(tzinfo=None), start + timedelta(days=1)
    )


# --- AlertSender -----------------------------------------------------------------------------


def make_alert(user_id: int) -> collector.OutgoingAlert:
    return collector.OutgoingAlert(tracker_id=user_id, user_id=user_id, text="alert")


def network_error(chat_id: int) -> TelegramNetworkError:
    return TelegramNetworkError(telegram_method(chat_id), "HTTP Client says - timeout")


def server_error(chat_id: int) -> TelegramServerError:
    return TelegramServerError(telegram_method(chat_id), "Bad Gateway")


async def test_alert_sender_retries_transient_errors_with_backoff(
    session_factory, sleeps: list[float]
) -> None:
    bot = FakeBot()
    bot.errors[1] = [network_error(1), server_error(1)]
    sender = collector.AlertSender(bot, session_factory, min_interval=0)  # type: ignore[arg-type]
    assert await sender.send(make_alert(1)) is True
    assert bot.attempts == [1, 1, 1]
    assert sleeps == [
        collector.SEND_BACKOFF_BASE_SECONDS,
        collector.SEND_BACKOFF_BASE_SECONDS * 2,
    ]


async def test_alert_sender_gives_up_after_max_attempts(
    session_factory, sleeps: list[float]
) -> None:
    bot = FakeBot()
    bot.errors[1] = [server_error(1) for _ in range(collector.MAX_SEND_ATTEMPTS)]
    sender = collector.AlertSender(bot, session_factory, min_interval=0)  # type: ignore[arg-type]
    assert await sender.send(make_alert(1)) is False
    assert bot.attempts == [1] * collector.MAX_SEND_ATTEMPTS
    assert len(sleeps) == collector.MAX_SEND_ATTEMPTS - 1


async def test_alert_sender_stops_retrying_while_telegram_is_down(
    session_factory, sleeps: list[float]
) -> None:
    bot = FakeBot()
    attempts = collector.MAX_SEND_ATTEMPTS
    failing_users = list(range(1, collector.MAX_CONSECUTIVE_TRANSIENT_FAILURES + 1))
    for user_id in failing_users:
        bot.errors[user_id] = [network_error(user_id) for _ in range(attempts)]
    sender = collector.AlertSender(bot, session_factory, min_interval=0)  # type: ignore[arg-type]
    for user_id in failing_users:
        assert await sender.send(make_alert(user_id)) is False

    # Telegram looks down: one attempt, no backoff.
    bot.errors[50] = [network_error(50)]
    sleeps.clear()
    assert await sender.send(make_alert(50)) is False
    assert bot.attempts.count(50) == 1
    assert sleeps == []

    # A delivered alert restores the retries.
    assert await sender.send(make_alert(60)) is True
    bot.errors[70] = [network_error(70)]
    assert await sender.send(make_alert(70)) is True
    assert bot.attempts.count(70) == 2


@pytest.mark.parametrize(
    "error",
    [
        TelegramRetryAfter(telegram_method(1), "Flood control", retry_after=3600),
        TelegramBadRequest(telegram_method(1), "Bad Request: chat not found"),
    ],
    ids=["long-retry-after", "bad-request"],
)
async def test_alert_sender_does_not_retry_permanent_errors(
    session_factory, sleeps: list[float], error: Exception
) -> None:
    bot = FakeBot()
    bot.errors[1] = [error]
    sender = collector.AlertSender(bot, session_factory, min_interval=0)  # type: ignore[arg-type]
    assert await sender.send(make_alert(1)) is False
    assert bot.attempts == [1]
    assert sleeps == []


async def test_alert_sender_retry_after_waits_the_requested_time(
    session_factory, sleeps: list[float]
) -> None:
    bot = FakeBot()
    bot.errors[1] = [TelegramRetryAfter(telegram_method(1), "Flood control", retry_after=7)]
    sender = collector.AlertSender(bot, session_factory, min_interval=0)  # type: ignore[arg-type]
    assert await sender.send(make_alert(1)) is True
    assert sleeps == [7]


async def test_alert_sender_forbidden_blocks_user_without_retry(
    session_factory, sleeps: list[float]
) -> None:
    await add_trackers(session_factory, (1, "aaa"), (1, "bbb"))
    bot = FakeBot()
    bot.errors[1] = [TelegramForbiddenError(telegram_method(1), "Forbidden: bot was blocked")]
    sender = collector.AlertSender(bot, session_factory, min_interval=0)  # type: ignore[arg-type]
    assert await sender.send(make_alert(1)) is False
    assert await sender.send(make_alert(1)) is False
    assert bot.attempts == [1]
    assert sleeps == []
    async with session_factory() as session:
        trackers = list((await session.execute(select(Tracker))).scalars())
    assert [tracker.active for tracker in trackers] == [False, False]
