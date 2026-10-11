from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramRetryAfter,
)
from aiogram.methods import SendMessage
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

import scheduler.collector as collector
from api.config import get_settings
from api.database import get_engine, get_session_factory
from api.models import QueryListingState, QuerySnapshot, Tracker, TrackerEvent
from api.services.aggregator import build_query_key, normalize_search_text
from api.services.history_service import snapshot_bucket
from api.services.kufar_client import KufarAPIError, KufarClient
from api.services.users import ensure_user


def make_ad(ad_id: int, price_byn: float, subject: str = "iPhone 15 128GB") -> dict[str, Any]:
    return {
        "ad_id": ad_id,
        "subject": subject,
        "price_byn": int(price_byn * 100),
        "ad_link": f"https://www.kufar.by/item/{ad_id}",
        "list_time": "2026-10-11T10:00:00",
    }


class FakeKufarClient(KufarClient):
    """Serves listings from ``catalog`` (keyed by normalized query), newest first."""

    catalog: dict[str, list[dict[str, Any]]] = {}
    failing: set[str] = set()
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
        if key in self.failing:
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
    monkeypatch.setattr(FakeKufarClient, "calls", [])
    monkeypatch.setattr(collector, "KufarClient", FakeKufarClient)
    monkeypatch.setattr(collector, "SEND_MIN_INTERVAL_SECONDS", 0)
    return FakeKufarClient


@pytest.fixture
async def session_factory():
    engine = get_engine()
    yield get_session_factory(engine)
    await engine.dispose()


async def add_trackers(session_factory, *specs: tuple[int, str]) -> list[int]:
    async with session_factory() as session:
        for user_id in sorted({user_id for user_id, _ in specs}):
            await ensure_user(session, user_id)
        trackers = [Tracker(user_id=user_id, query=query) for user_id, query in specs]
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
        TelegramRetryAfter(telegram_method(111), "Flood control", retry_after=0),
        TelegramRetryAfter(telegram_method(111), "Flood control", retry_after=0),
    ]
    await run_check(bot, session_factory)
    assert bot.attempts.count(111) == 2
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


async def test_scheduler_fills_empty_snapshot_bucket(kufar, session_factory) -> None:
    kufar.catalog["aaa"] = [make_ad(1, 100, "aaa phone"), make_ad(2, 300, "aaa phone")]
    await add_trackers(session_factory, (111, "aaa"))
    await run_check(FakeBot(), session_factory)

    async with session_factory() as session:
        snapshot = await session.scalar(select(QuerySnapshot))
    assert snapshot is not None
    assert snapshot.query == "broad::aaa"
    assert snapshot.analyzed_count == 2
    assert snapshot.median_byn == 200


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
