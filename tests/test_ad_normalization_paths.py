"""Malformed Kufar ads in the scheduler and in /top (L12)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

import scheduler.collector as collector
from api.config import get_settings
from api.database import get_engine, get_session_factory
from api.models import QueryListingState, Tracker, TrackerEvent
from api.services.users import ensure_user
from bot.handlers import price


def good_ad(ad_id: int, price_byn: float, subject: str = "iPhone 15 128GB") -> dict[str, Any]:
    return {
        "ad_id": ad_id,
        "subject": subject,
        "price_byn": int(price_byn * 100),
        "ad_link": f"https://www.kufar.by/item/{ad_id}",
        "list_time": "2026-10-11T10:00:00",
        "region_id": 6,
        "images": [{"path": "a.jpg"}],
        "ad_parameters": [{"p": "condition", "v": "Б/у"}],
    }


def malformed_ads() -> list[Any]:
    """Ads that used to raise somewhere in the sync, the filters or the alert."""
    return [
        "not an ad",
        None,
        {**good_ad(0, 1000), "ad_id": None},
        {**good_ad(0, 1000), "ad_id": "abc"},
        {**good_ad(0, 1000), "ad_id": {"nested": 1}},
        {**good_ad(901, 1000, "iPhone 15 no time"), "list_time": None},
        {**good_ad(902, 1000, "iPhone 15 no images"), "images": None},
        {**good_ad(903, 1000, "iPhone 15 bad params"), "ad_parameters": [None, "x", 3]},
        {**good_ad(904, 1000, "iPhone 15 bad region"), "region_id": "abc"},
        {**good_ad(905, 1000, "iPhone 15 bad price"), "price_byn": "n/a"},
    ]


class FakeKufarClient:
    pages: list[Any] = []
    closed = 0

    def __init__(self, settings: Any, http_client: Any = None) -> None:
        pass

    async def aclose(self) -> None:
        type(self).closed += 1

    async def search(self, **_: Any) -> Any:
        return self.pages.pop(0) if self.pages else {"ads": []}

    @staticmethod
    def extract_total(payload: Any) -> int:
        return len(payload.get("ads") or []) if isinstance(payload, dict) else 0

    @staticmethod
    def extract_next_cursor(payload: Any) -> str | None:
        return None


class FakeBot:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []

    async def send_message(self, chat_id: int, text: str, reply_markup: Any = None) -> None:
        self.sent.append((chat_id, text))


@pytest.fixture
def kufar(monkeypatch: pytest.MonkeyPatch) -> type[FakeKufarClient]:
    monkeypatch.setattr(FakeKufarClient, "pages", [])
    monkeypatch.setattr(FakeKufarClient, "closed", 0)
    monkeypatch.setattr(collector, "KufarClient", FakeKufarClient)
    monkeypatch.setattr(collector, "SEND_MIN_INTERVAL_SECONDS", 0)
    monkeypatch.setattr(price, "KufarClient", FakeKufarClient)
    return FakeKufarClient


@pytest.fixture
async def session_factory():
    engine = get_engine()
    yield get_session_factory(engine)
    await engine.dispose()


async def test_scheduler_survives_malformed_ads(kufar, session_factory) -> None:
    async with session_factory() as session:
        await ensure_user(session, 111, first_name="Test")
        session.add(Tracker(user_id=111, query="iphone 15"))
        await session.commit()
    bot = FakeBot()

    kufar.pages = [{"ads": [good_ad(1, 2000), good_ad(2, 2100)]}]
    await collector.check_trackers(bot, session_factory, get_settings())  # type: ignore[arg-type]
    assert bot.sent == []

    kufar.pages = [
        {"ads": [good_ad(3, 1500, "iPhone 15 Blue"), *malformed_ads(), good_ad(1, 2000)]}
    ]
    await collector.check_trackers(bot, session_factory, get_settings())  # type: ignore[arg-type]

    async with session_factory() as session:
        tracker = await session.scalar(select(Tracker))
        events = list((await session.execute(select(TrackerEvent))).scalars())
        state_ids = set((await session.execute(select(QueryListingState.ad_id))).scalars())
    assert tracker is not None and tracker.active
    # Ads without a usable id are dropped; the others were normalized and synced.
    assert state_ids == {1, 2, 3, 901, 902, 903, 904, 905}
    assert 3 in {event.ad_id for event in events}
    assert [chat_id for chat_id, _ in bot.sent] == [111]
    assert "Новые объявления: 6" in bot.sent[0][1]


async def test_scheduler_survives_non_list_ads(kufar, session_factory) -> None:
    async with session_factory() as session:
        await ensure_user(session, 111, first_name="Test")
        session.add(Tracker(user_id=111, query="iphone 15"))
        await session.commit()

    kufar.pages = [{"ads": {"unexpected": "shape"}}]
    await collector.check_trackers(FakeBot(), session_factory, get_settings())  # type: ignore[arg-type]

    async with session_factory() as session:
        tracker = await session.scalar(select(Tracker))
    assert tracker is not None and tracker.last_checked_at is not None


async def test_matcher_errors_skip_only_the_listing(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken_filters(ad: dict[str, Any], **_: Any) -> bool:
        raise ValueError("unexpected field shape")

    monkeypatch.setattr(collector, "matches_tracker_filters", broken_filters)
    tracker = Tracker(user_id=111, query="iphone 15")
    stats = collector.compute_price_stats([100.0])
    assert collector._matches_tracker(tracker, {"ad_id": 1}, stats, {}) is False


async def test_top_skips_malformed_ads(kufar) -> None:
    kufar.pages = [{"ads": [*malformed_ads(), good_ad(7, 900, "iPhone 15 Good")]}]

    listings = await price.fetch_listings("iphone 15", limit=20)

    titles = [item["title"] for item in listings]
    assert "iPhone 15 Good" in titles
    assert "iPhone 15 no time" in titles
    assert len(listings) == len(malformed_ads()) - 2 + 1  # the two non-dict entries go
    by_title = {item["title"]: item for item in listings}
    assert by_title["iPhone 15 bad price"]["price_byn"] is None
    assert by_title["iPhone 15 Good"]["price_byn"] == 900.0
    assert kufar.closed == 1


async def test_top_command_answers_with_non_list_ads(kufar) -> None:
    kufar.pages = [{"ads": "garbage"}]
    message = SimpleNamespace(answer=AsyncMock())

    await price.cmd_top(message, SimpleNamespace(args="iphone"))  # type: ignore[arg-type]

    message.answer.assert_awaited_once_with("No listings found for iphone.")
