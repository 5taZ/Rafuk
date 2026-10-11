"""Per-tracker intervals (L27): stored cadence and due groups in the scheduler."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select, update

import scheduler.collector as collector
from api.config import get_settings
from api.database import get_engine, get_session_factory
from api.models import Tracker
from api.services import tracker_service
from api.services.aggregator import normalize_search_text
from api.services.users import ensure_user

NOW = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)


def make_ad(ad_id: int, price_byn: float, subject: str = "iPhone 15 128GB") -> dict[str, Any]:
    return {
        "ad_id": ad_id,
        "subject": subject,
        "price_byn": int(price_byn * 100),
        "ad_link": f"https://www.kufar.by/item/{ad_id}",
        "list_time": "2026-10-11T10:00:00",
    }


class FakeKufarClient:
    catalog: dict[str, list[dict[str, Any]]] = {}
    calls: list[str] = []

    def __init__(self, settings: Any, http_client: Any = None) -> None:
        pass

    async def aclose(self) -> None:
        pass

    async def search(self, query: str, cursor: str | None = None, **_: Any) -> dict[str, Any]:
        type(self).calls.append(query)
        ads = self.catalog.get(normalize_search_text(query), [])
        return {"ads": [dict(ad) for ad in ads], "total": len(ads)}

    @staticmethod
    def extract_total(payload: dict[str, Any]) -> int:
        return int(payload.get("total") or 0)

    @staticmethod
    def extract_next_cursor(payload: dict[str, Any]) -> str | None:
        return None


class FakeBot:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []

    async def send_message(self, chat_id: int, text: str, reply_markup: Any = None) -> None:
        self.sent.append((chat_id, text))


@pytest.fixture
def kufar(monkeypatch: pytest.MonkeyPatch) -> type[FakeKufarClient]:
    monkeypatch.setattr(FakeKufarClient, "catalog", {})
    monkeypatch.setattr(FakeKufarClient, "calls", [])
    monkeypatch.setattr(collector, "KufarClient", FakeKufarClient)
    monkeypatch.setattr(collector, "SEND_MIN_INTERVAL_SECONDS", 0)
    return FakeKufarClient


@pytest.fixture
async def session_factory():
    engine = get_engine()
    yield get_session_factory(engine)
    await engine.dispose()


async def add_tracker(session_factory, user_id: int, query: str, interval_min: int) -> int:
    async with session_factory() as session:
        await ensure_user(session, user_id, first_name="Test")
        tracker = Tracker(user_id=user_id, query=query, interval_min=interval_min)
        session.add(tracker)
        await session.commit()
        return tracker.id


async def set_last_checked(session_factory, tracker_id: int, value: datetime) -> None:
    async with session_factory() as session:
        await session.execute(
            update(Tracker).where(Tracker.id == tracker_id).values(last_checked_at=value)
        )
        await session.commit()


async def load_tracker(session_factory, tracker_id: int) -> Tracker:
    async with session_factory() as session:
        tracker = await session.scalar(select(Tracker).where(Tracker.id == tracker_id))
        assert tracker is not None
        return tracker


async def run_check(bot: FakeBot, session_factory) -> None:
    await collector.check_trackers(bot, session_factory, get_settings())  # type: ignore[arg-type]


# --- tracker_is_due / TrackerGroup.is_due ----------------------------------------------------


@pytest.mark.parametrize(
    ("interval_min", "last_checked_at", "expected"),
    [
        (15, NOW, True),  # not longer than the global interval: every run
        (30, NOW, True),
        (None, NOW, True),
        (60, None, True),  # never checked
        (60, NOW - timedelta(minutes=30), False),
        (60, NOW - timedelta(minutes=58, seconds=59), False),
        (60, NOW - timedelta(minutes=59), True),  # one minute of slack for job drift
        (60, NOW - timedelta(hours=3), True),
        # SQLite returns naive datetimes; they are UTC.
        (60, (NOW - timedelta(minutes=10)).replace(tzinfo=None), False),
        (60, (NOW - timedelta(minutes=90)).replace(tzinfo=None), True),
    ],
)
def test_tracker_is_due(
    interval_min: int | None, last_checked_at: datetime | None, expected: bool
) -> None:
    assert (
        collector.tracker_is_due(
            interval_min, last_checked_at, check_interval_min=30, now=NOW
        )
        is expected
    )


def test_group_is_due_when_any_tracker_is_due() -> None:
    group = collector.TrackerGroup(key="broad::x", query="x", strict_mode=False)
    assert group.is_due(check_interval_min=30, now=NOW)

    group.schedules = [(120, NOW - timedelta(minutes=10)), (60, NOW - timedelta(minutes=20))]
    assert not group.is_due(check_interval_min=30, now=NOW)

    group.schedules.append((120, NOW - timedelta(minutes=130)))
    assert group.is_due(check_interval_min=30, now=NOW)


# --- stored interval ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("requested", "check_interval", "expected"),
    [(None, 30, 30), (15, 30, 30), (30, 30, 30), (60, 30, 60), (None, 10, 15), (5, 10, 10)],
)
async def test_create_tracker_stores_real_interval(
    monkeypatch: pytest.MonkeyPatch,
    session_factory,
    requested: int | None,
    check_interval: int,
    expected: int,
) -> None:
    monkeypatch.setenv("ALERT_CHECK_INTERVAL", str(check_interval))
    get_settings.cache_clear()
    async with session_factory() as session:
        tracker = await tracker_service.create_tracker(
            session, user_id=111, query="iphone 15", interval_min=requested
        )
        await session.commit()
        assert tracker.interval_min == expected


def test_effective_interval_never_below_global_interval() -> None:
    check_interval = get_settings().alert_check_interval
    assert tracker_service.effective_interval_min(1) == check_interval
    assert tracker_service.effective_interval_min(check_interval + 5) == check_interval + 5


# --- scheduler runs -----------------------------------------------------------------------------


async def test_group_with_long_interval_is_skipped_until_due(kufar, session_factory) -> None:
    kufar.catalog["iphone 15"] = [make_ad(1, 2000)]
    tracker_id = await add_tracker(session_factory, 111, "iphone 15", interval_min=120)
    bot = FakeBot()

    # Never checked: runs and records the baseline.
    await run_check(bot, session_factory)
    assert kufar.calls == ["iphone 15"]
    first_check = (await load_tracker(session_factory, tracker_id)).last_checked_at
    assert first_check is not None

    # Checked a moment ago: the whole group is skipped, Kufar is not asked.
    kufar.catalog["iphone 15"].insert(0, make_ad(2, 1500, "iPhone 15 Blue"))
    await run_check(bot, session_factory)
    assert kufar.calls == ["iphone 15"]
    assert bot.sent == []
    assert (await load_tracker(session_factory, tracker_id)).last_checked_at == first_check

    # Interval elapsed: runs again and alerts on what appeared meanwhile.
    await set_last_checked(session_factory, tracker_id, datetime.now(UTC) - timedelta(hours=2))
    await run_check(bot, session_factory)
    assert kufar.calls == ["iphone 15", "iphone 15"]
    assert [chat_id for chat_id, _ in bot.sent] == [111]
    assert "iPhone 15 Blue" in bot.sent[0][1]


async def test_running_group_processes_all_its_trackers(kufar, session_factory) -> None:
    kufar.catalog["iphone 15"] = [make_ad(1, 2000)]
    every_run = await add_tracker(session_factory, 111, "iphone 15", interval_min=30)
    hourly = await add_tracker(session_factory, 222, "iPhone 15", interval_min=120)
    other_query = await add_tracker(session_factory, 333, "galaxy s24", interval_min=120)
    bot = FakeBot()
    await run_check(bot, session_factory)
    assert sorted(kufar.calls) == ["galaxy s24", "iphone 15"]

    kufar.calls.clear()
    kufar.catalog["iphone 15"].insert(0, make_ad(2, 1500, "iPhone 15 Blue"))
    await run_check(bot, session_factory)

    # The 30-minute tracker makes its group due; the 120-minute tracker of the same query
    # is processed too (it shares the listing state), the other query is skipped.
    assert kufar.calls == ["iphone 15"]
    assert sorted(chat_id for chat_id, _ in bot.sent) == [111, 222]
    hourly_tracker = await load_tracker(session_factory, hourly)
    every_run_tracker = await load_tracker(session_factory, every_run)
    assert hourly_tracker.last_checked_at == every_run_tracker.last_checked_at
    assert (await load_tracker(session_factory, other_query)).last_checked_at is not None
