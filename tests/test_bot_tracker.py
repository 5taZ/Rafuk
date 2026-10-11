from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from api.models import LeadItem, Tracker, TrackerEvent, User
from api.services.users import ensure_user
from bot.database import get_bot_session_factory
from bot.handlers import tracker as tracker_handlers
from bot.handlers.tracker import (
    _chunk_lines,
    cmd_track,
    cmd_tracks,
    cmd_untrack,
    handle_tracker_action,
)
from bot.keyboards import tracker_alert_keyboard

USER_ID = 123456


@pytest.fixture
def session_factory():
    """Sessions on the bot engine; conftest resets the singleton around every test."""
    return get_bot_session_factory()


def make_message(user_id: int = USER_ID) -> SimpleNamespace:
    return SimpleNamespace(
        from_user=SimpleNamespace(id=user_id, first_name="Test", username="tester"),
        answer=AsyncMock(),
    )


def make_command(args: str | None) -> SimpleNamespace:
    return SimpleNamespace(args=args)


def make_callback(data: str, user_id: int = USER_ID) -> SimpleNamespace:
    return SimpleNamespace(
        data=data,
        from_user=SimpleNamespace(id=user_id, first_name="Test", username="tester"),
        answer=AsyncMock(),
    )


def answers(mock: AsyncMock) -> list[str]:
    return [call.args[0] for call in mock.await_args_list]


async def fetch_all(session_factory, statement) -> list:
    async with session_factory() as session:
        return list((await session.execute(statement)).scalars())


async def seed_event(session_factory, *, ad_id: int = 101) -> int:
    """Insert the user, its tracker and one alert event (FKs are enforced)."""
    async with session_factory() as session:
        await ensure_user(session, USER_ID, first_name="Test", username="tester")
        tracker = Tracker(user_id=USER_ID, query="iphone 15 128")
        session.add(tracker)
        await session.flush()
        event = TrackerEvent(
            tracker_id=tracker.id,
            user_id=USER_ID,
            ad_id=ad_id,
            query="iphone 15 128",
            strict_mode=False,
            event_type="new_listing",
            title="iPhone 15 128GB",
            link=f"https://www.kufar.by/item/{ad_id}",
            price_byn=1800,
        )
        session.add(event)
        await session.commit()
        return event.id


def test_tracker_alert_keyboard_contains_actions() -> None:
    keyboard = tracker_alert_keyboard(
        "https://kufar-analytics.example.com/app",
        query="iphone 15 128",
        listing_url="https://www.kufar.by/item/101",
        event_id=42,
    )

    assert len(keyboard.inline_keyboard) == 2
    assert keyboard.inline_keyboard[0][0].text == "Открыть запрос"
    assert "query=iphone+15+128" in keyboard.inline_keyboard[0][0].web_app.url
    assert keyboard.inline_keyboard[1][0].callback_data == "lead:42"
    assert keyboard.inline_keyboard[1][1].callback_data == "later:42"


async def test_tracker_callback_creates_lead(session_factory) -> None:
    event_id = await seed_event(session_factory)

    callback = make_callback(f"lead:{event_id}")
    await handle_tracker_action(callback)  # type: ignore[arg-type]

    leads = await fetch_all(session_factory, select(LeadItem))
    assert [(lead.ad_id, lead.status, lead.source) for lead in leads] == [
        (101, "in_progress", "telegram_alert")
    ]
    users = await fetch_all(session_factory, select(User))
    assert [user.telegram_user_id for user in users] == [USER_ID]
    callback.answer.assert_awaited_once()
    assert answers(callback.answer) == ["Сохранено."]


async def test_tracker_callback_never_downgrades_lead(session_factory) -> None:
    event_id = await seed_event(session_factory)
    async with session_factory() as session:
        session.add(
            LeadItem(
                user_id=USER_ID,
                ad_id=101,
                query="iphone 15 128",
                title="iPhone 15 128GB",
                link="https://www.kufar.by/item/101",
                price_byn=1800,
                status="sold",
                source="manual",
            )
        )
        await session.commit()

    for data in (f"later:{event_id}", f"lead:{event_id}", f"later:{event_id}"):
        callback = make_callback(data)
        await handle_tracker_action(callback)  # type: ignore[arg-type]
        callback.answer.assert_awaited_once()

    leads = await fetch_all(session_factory, select(LeadItem))
    assert [(lead.status, lead.source) for lead in leads] == [("sold", "manual")]


async def test_tracker_callback_later_then_lead(session_factory) -> None:
    event_id = await seed_event(session_factory)
    await handle_tracker_action(make_callback(f"later:{event_id}"))  # type: ignore[arg-type]
    leads = await fetch_all(session_factory, select(LeadItem))
    assert leads[0].status == "deferred"

    # Double tap and a later "in work" press: no IntegrityError, the deferred lead moves on.
    await handle_tracker_action(make_callback(f"lead:{event_id}"))  # type: ignore[arg-type]
    await handle_tracker_action(make_callback(f"lead:{event_id}"))  # type: ignore[arg-type]
    leads = await fetch_all(session_factory, select(LeadItem))
    assert [lead.status for lead in leads] == ["in_progress"]


@pytest.mark.parametrize("data", ["lead:²", "lead:abc", "lead:", "lead:99999999999", "x:1"])
async def test_tracker_callback_rejects_bad_data(session_factory, data: str) -> None:
    callback = make_callback(data)
    await handle_tracker_action(callback)  # type: ignore[arg-type]
    callback.answer.assert_awaited_once()
    assert answers(callback.answer) == ["Некорректное действие."]
    assert await fetch_all(session_factory, select(LeadItem)) == []


async def test_tracker_callback_answers_on_database_error(session_factory, monkeypatch) -> None:
    async def failing_save(*args, **kwargs):
        raise OperationalError("SELECT 1", {}, Exception("db down"))

    monkeypatch.setattr(tracker_handlers, "_save_alert_lead", failing_save)
    callback = make_callback("lead:1")
    await handle_tracker_action(callback)  # type: ignore[arg-type]
    callback.answer.assert_awaited_once()
    assert callback.answer.await_args.kwargs["show_alert"] is True


async def test_tracker_callback_unknown_event(session_factory) -> None:
    callback = make_callback("lead:777")
    await handle_tracker_action(callback)  # type: ignore[arg-type]
    assert answers(callback.answer) == ["Сигнал уже недоступен."]


async def test_track_creates_tracker_and_user(session_factory) -> None:
    message = make_message()
    await cmd_track(message, make_command("  iphone 15  "))  # type: ignore[arg-type]

    trackers = await fetch_all(session_factory, select(Tracker))
    assert len(trackers) == 1
    assert trackers[0].query == "iphone 15"
    assert trackers[0].config_keyword is None
    assert trackers[0].user_id == USER_ID
    assert answers(message.answer) == [f"Трекер #{trackers[0].id} создан для «iphone 15»."]

    users = await fetch_all(session_factory, select(User))
    assert [(user.telegram_user_id, user.username) for user in users] == [(USER_ID, "tester")]


async def test_track_duplicate_replies_with_existing_id(session_factory) -> None:
    await cmd_track(make_message(), make_command("iphone 15"))  # type: ignore[arg-type]
    message = make_message()
    await cmd_track(message, make_command("iPhone 15"))  # type: ignore[arg-type]

    trackers = await fetch_all(session_factory, select(Tracker))
    assert len(trackers) == 1
    assert answers(message.answer) == [f"Трекер #{trackers[0].id} для «iphone 15» уже существует."]


async def test_track_validates_query(session_factory) -> None:
    empty = make_message()
    await cmd_track(empty, make_command("   "))  # type: ignore[arg-type]
    assert answers(empty.answer) == ["Использование: /track <запрос>"]

    too_long = make_message()
    await cmd_track(too_long, make_command("a" * 300))  # type: ignore[arg-type]
    assert "слишком длинный" in answers(too_long.answer)[0]
    assert await fetch_all(session_factory, select(Tracker)) == []


async def test_track_quota(session_factory, monkeypatch) -> None:
    from api.services import tracker_service

    monkeypatch.setattr(tracker_service, "MAX_ACTIVE_TRACKERS_PER_USER", 1)
    await cmd_track(make_message(), make_command("iphone 15"))  # type: ignore[arg-type]
    message = make_message()
    await cmd_track(message, make_command("iphone 16"))  # type: ignore[arg-type]
    assert "лимит" in answers(message.answer)[0]
    assert len(await fetch_all(session_factory, select(Tracker))) == 1


async def test_track_replies_on_database_error(session_factory, monkeypatch) -> None:
    from api.services import tracker_service

    async def failing_create(*args, **kwargs):
        raise OperationalError("INSERT", {}, Exception("db down"))

    monkeypatch.setattr(tracker_service, "create_tracker", failing_create)
    message = make_message()
    await cmd_track(message, make_command("iphone 15"))  # type: ignore[arg-type]
    assert answers(message.answer) == ["Не удалось выполнить команду. Попробуйте позже."]


@pytest.mark.parametrize("args", [None, "abc", "#", "1.5", "²", "-3", "99999999999999999999999"])
async def test_untrack_rejects_bad_ids(session_factory, args: str | None) -> None:
    message = make_message()
    await cmd_untrack(message, make_command(args))  # type: ignore[arg-type]
    assert len(answers(message.answer)) == 1
    assert answers(message.answer)[0].startswith("Использование: /untrack <id>")


async def test_untrack_soft_deletes_tracker(session_factory) -> None:
    await cmd_track(make_message(), make_command("iphone 15"))  # type: ignore[arg-type]
    tracker_id = (await fetch_all(session_factory, select(Tracker)))[0].id

    foreign = make_message(user_id=999)
    await cmd_untrack(foreign, make_command(str(tracker_id)))  # type: ignore[arg-type]
    assert answers(foreign.answer) == ["Трекер не найден."]

    message = make_message()
    await cmd_untrack(message, make_command(f"#{tracker_id}"))  # type: ignore[arg-type]
    assert answers(message.answer) == [f"Трекер #{tracker_id} отключён."]

    tracker = (await fetch_all(session_factory, select(Tracker)))[0]
    assert tracker.active is False
    assert tracker.deleted_at is not None


async def test_tracks_lists_active_trackers(session_factory) -> None:
    empty = make_message()
    await cmd_tracks(empty)  # type: ignore[arg-type]
    assert answers(empty.answer) == ["Активных трекеров нет."]

    await cmd_track(make_message(), make_command("iphone 15"))  # type: ignore[arg-type]
    message = make_message()
    await cmd_tracks(message)  # type: ignore[arg-type]
    text = answers(message.answer)[0]
    assert text.startswith("Активные трекеры:")
    assert "iphone 15" in text


def test_chunk_lines_respects_telegram_limit() -> None:
    lines = [f"#{index} " + "x" * 250 for index in range(50)]
    chunks = _chunk_lines(lines)
    assert len(chunks) > 1
    assert all(len(chunk) <= 4000 for chunk in chunks)
    assert "\n".join(chunks).count("#") == 50
