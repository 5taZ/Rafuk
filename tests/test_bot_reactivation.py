"""/start registers the user and resumes trackers paused after a bot block (L25)."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.exc import OperationalError

import scheduler.collector as collector
from api.database import get_session_factory
from api.models import Tracker, User
from api.services import tracker_service
from api.services.users import ensure_user
from bot.handlers import start as start_handlers
from bot.handlers.start import cmd_start

USER_ID = 424242
OTHER_USER_ID = 515151


@pytest.fixture
async def session_factory(monkeypatch):
    """A fresh bot engine per test: the module-level singleton would leak between tests."""
    import bot.database as bot_database

    monkeypatch.setattr(bot_database, "_engine", None)
    engine = bot_database.get_bot_engine()
    yield get_session_factory(engine)
    await engine.dispose()


def make_message(user_id: int = USER_ID) -> SimpleNamespace:
    return SimpleNamespace(
        from_user=SimpleNamespace(id=user_id, first_name="Test", username="tester"),
        answer=AsyncMock(),
    )


async def add_tracker(session_factory, user_id: int, query: str, **values) -> int:
    async with session_factory() as session:
        await ensure_user(session, user_id, first_name="Test")
        tracker = Tracker(user_id=user_id, query=query, **values)
        session.add(tracker)
        await session.commit()
        return tracker.id


async def trackers_by_id(session_factory) -> dict[int, Tracker]:
    async with session_factory() as session:
        trackers = (await session.execute(select(Tracker))).scalars()
        return {tracker.id: tracker for tracker in trackers}


async def test_start_registers_new_user_without_trackers(session_factory) -> None:
    message = make_message()
    await cmd_start(message)  # type: ignore[arg-type]

    text = message.answer.await_args.args[0]
    assert "Resumed" not in text
    async with session_factory() as session:
        user = await session.scalar(select(User).where(User.telegram_user_id == USER_ID))
    assert user is not None
    assert user.username == "tester"


async def test_start_resumes_trackers_paused_by_a_block(session_factory) -> None:
    paused = await add_tracker(
        session_factory, USER_ID, "iphone 15", last_checked_at=datetime.now(UTC)
    )
    paused_too = await add_tracker(session_factory, USER_ID, "galaxy s24", interval_min=5)
    deleted = await add_tracker(session_factory, USER_ID, "pixel 8")
    active = await add_tracker(session_factory, USER_ID, "xiaomi 14")
    foreign = await add_tracker(session_factory, OTHER_USER_ID, "iphone 15")

    # The scheduler pauses every active tracker of a user who blocked the bot ...
    assert await collector.deactivate_user_trackers(session_factory, OTHER_USER_ID) == 1
    async with session_factory() as session:
        for tracker_id in (paused, paused_too):
            tracker = await session.get(Tracker, tracker_id)
            assert tracker is not None
            tracker.active = False
        # ... while an explicit deletion also sets deleted_at.
        removed = await tracker_service.deactivate_tracker(
            session, user_id=USER_ID, tracker_id=deleted
        )
        assert removed is not None
        await session.commit()

    message = make_message()
    await cmd_start(message)  # type: ignore[arg-type]

    text = message.answer.await_args.args[0]
    assert "Resumed 2 trackers paused while the bot was blocked" in text
    trackers = await trackers_by_id(session_factory)
    assert trackers[paused].active and trackers[paused_too].active
    # A resumed tracker starts from a fresh baseline and shows its real interval.
    assert trackers[paused].last_checked_at is None
    assert trackers[paused_too].interval_min == tracker_service.effective_interval_min(5)
    assert not trackers[deleted].active and trackers[deleted].deleted_at is not None
    assert trackers[active].active
    assert not trackers[foreign].active

    # Nothing left to resume.
    message = make_message()
    await cmd_start(message)  # type: ignore[arg-type]
    assert "Resumed" not in message.answer.await_args.args[0]


async def test_start_reports_a_single_tracker(session_factory) -> None:
    await add_tracker(session_factory, USER_ID, "iphone 15", active=False)

    message = make_message()
    await cmd_start(message)  # type: ignore[arg-type]

    assert "Resumed 1 tracker paused" in message.answer.await_args.args[0]


async def test_resume_skips_duplicates_and_respects_quota(session_factory) -> None:
    active = await add_tracker(session_factory, USER_ID, "iphone 15")
    duplicate = await add_tracker(session_factory, USER_ID, "iPhone 15", active=False)
    first = await add_tracker(session_factory, USER_ID, "galaxy s24", active=False)
    second = await add_tracker(session_factory, USER_ID, "pixel 8", active=False)

    async with session_factory() as session:
        resumed = await tracker_service.resume_paused_trackers(
            session, user_id=USER_ID, max_active=2
        )
        await session.commit()
    assert [tracker.id for tracker in resumed] == [first]

    trackers = await trackers_by_id(session_factory)
    assert trackers[active].active and trackers[first].active
    assert not trackers[duplicate].active
    assert not trackers[second].active


async def test_start_answers_when_database_fails(session_factory, monkeypatch) -> None:
    async def broken(*_args, **_kwargs):
        raise OperationalError("SELECT 1", {}, Exception("database is down"))

    monkeypatch.setattr(start_handlers, "resume_paused_trackers", broken)
    message = make_message()
    await cmd_start(message)  # type: ignore[arg-type]

    message.answer.assert_awaited_once()
    assert "Rafuks helps you" in message.answer.await_args.args[0]
