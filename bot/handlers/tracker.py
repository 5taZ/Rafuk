from __future__ import annotations

import logging
from contextlib import suppress

from aiogram import Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, Message, User
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from api.models import TrackerEvent
from api.services import tracker_service
from api.services.tracker_service import (
    DuplicateTrackerError,
    TrackerServiceError,
    parse_db_id,
)
from api.services.users import ensure_user
from api.services.workflow_store import upsert_lead
from bot.database import get_bot_engine, get_bot_session_factory

logger = logging.getLogger(__name__)

router = Router(name="tracker")

TRACK_USAGE = "Использование: /track <запрос>"
UNTRACK_USAGE = "Использование: /untrack <id>. Номера трекеров показывает /tracks."
DB_ERROR_TEXT = "Не удалось выполнить команду. Попробуйте позже."
# Telegram rejects messages longer than 4096 characters.
MESSAGE_CHUNK_LIMIT = 4000
CALLBACK_STATUSES = {"lead": "in_progress", "later": "deferred"}


def _user_names(user: User) -> dict[str, str | None]:
    return {
        "first_name": getattr(user, "first_name", None),
        "username": getattr(user, "username", None),
    }


def _chunk_lines(lines: list[str], limit: int = MESSAGE_CHUNK_LIMIT) -> list[str]:
    chunks: list[str] = []
    current: list[str] = []
    size = 0
    for line in lines:
        line = line[:limit]
        if current and size + len(line) + 1 > limit:
            chunks.append("\n".join(current))
            current, size = [], 0
        current.append(line)
        size += len(line) + 1
    if current:
        chunks.append("\n".join(current))
    return chunks


@router.message(Command("track"))
async def cmd_track(message: Message, command: CommandObject) -> None:
    raw_query = (command.args or "").strip()
    if not raw_query or message.from_user is None:
        await message.answer(TRACK_USAGE)
        return

    session_factory = get_bot_session_factory(get_bot_engine())
    try:
        async with session_factory() as session:
            tracker = await tracker_service.create_tracker(
                session,
                user_id=message.from_user.id,
                query=raw_query,
                **_user_names(message.from_user),
            )
            await session.commit()
            tracker_id, query = tracker.id, tracker.query
    except DuplicateTrackerError as exc:
        await message.answer(f"Трекер #{exc.tracker.id} для «{exc.tracker.query}» уже существует.")
        return
    except TrackerServiceError as exc:
        await message.answer(exc.message)
        return
    except SQLAlchemyError:
        logger.exception("Failed to create tracker for user %s", message.from_user.id)
        await message.answer(DB_ERROR_TEXT)
        return
    await message.answer(f"Трекер #{tracker_id} создан для «{query}».")


@router.message(Command("tracks"))
async def cmd_tracks(message: Message) -> None:
    if message.from_user is None:
        await message.answer("Не удалось определить пользователя.")
        return

    session_factory = get_bot_session_factory(get_bot_engine())
    try:
        async with session_factory() as session:
            trackers = await tracker_service.list_active_trackers(session, message.from_user.id)
    except SQLAlchemyError:
        logger.exception("Failed to list trackers for user %s", message.from_user.id)
        await message.answer(DB_ERROR_TEXT)
        return
    if not trackers:
        await message.answer("Активных трекеров нет.")
        return
    lines = ["Активные трекеры:"]
    for tracker in trackers:
        mode = ", строгий режим" if tracker.strict_mode else ""
        lines.append(f"#{tracker.id} {tracker.query}{mode}")
    for chunk in _chunk_lines(lines):
        await message.answer(chunk)


@router.message(Command("untrack"))
async def cmd_untrack(message: Message, command: CommandObject) -> None:
    tracker_id = parse_db_id(command.args)
    if tracker_id is None or message.from_user is None:
        await message.answer(UNTRACK_USAGE)
        return

    session_factory = get_bot_session_factory(get_bot_engine())
    try:
        async with session_factory() as session:
            tracker = await tracker_service.deactivate_tracker(
                session,
                user_id=message.from_user.id,
                tracker_id=tracker_id,
            )
            if tracker is not None:
                await session.commit()
    except SQLAlchemyError:
        logger.exception("Failed to disable tracker %s", tracker_id)
        await message.answer(DB_ERROR_TEXT)
        return
    if tracker is None:
        await message.answer("Трекер не найден.")
        return
    await message.answer(f"Трекер #{tracker_id} отключён.")


async def _save_alert_lead(callback: CallbackQuery, action: str, event_id: int) -> str:
    """Create or update the lead for an alert event; returns the text for the callback."""
    session_factory = get_bot_session_factory(get_bot_engine())
    async with session_factory() as session:
        event = await session.scalar(
            select(TrackerEvent).where(
                TrackerEvent.id == event_id,
                TrackerEvent.user_id == callback.from_user.id,
            )
        )
        if event is None:
            return "Сигнал уже недоступен."

        await ensure_user(session, callback.from_user.id, **_user_names(callback.from_user))
        # upsert_lead never downgrades a lead that is already in work, bought or sold.
        await upsert_lead(
            session,
            user_id=callback.from_user.id,
            ad_id=int(event.ad_id or event.id),
            query=event.query,
            title=event.title,
            link=event.link,
            price_byn=event.price_byn,
            status=CALLBACK_STATUSES[action],
            source="telegram_alert",
        )
        await session.commit()
    return "Сохранено."


@router.callback_query(lambda callback: (callback.data or "").startswith(("lead:", "later:")))
async def handle_tracker_action(callback: CallbackQuery) -> None:
    answer_text = "Не удалось сохранить. Попробуйте позже."
    show_alert = True
    try:
        if callback.from_user is None or not callback.data:
            answer_text = "Не удалось определить пользователя."
            return
        action, _, raw_event_id = callback.data.partition(":")
        event_id = parse_db_id(raw_event_id) if not raw_event_id.startswith("#") else None
        if action not in CALLBACK_STATUSES or event_id is None:
            answer_text = "Некорректное действие."
            return
        answer_text = await _save_alert_lead(callback, action, event_id)
        show_alert = answer_text != "Сохранено."
    except SQLAlchemyError:
        logger.exception("Failed to save lead from callback %r", callback.data)
    finally:
        # Always answer, otherwise the button keeps spinning in the client.
        with suppress(TelegramAPIError):
            await callback.answer(answer_text, show_alert=show_alert)
