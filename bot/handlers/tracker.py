from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select

from api.models import Tracker, TrackerEvent
from api.services.workflow_store import upsert_lead
from bot.database import get_bot_engine, get_bot_session_factory

router = Router(name="tracker")


@router.message(Command("track"))
async def cmd_track(message: Message, command: CommandObject) -> None:
    query = (command.args or "").strip()
    if not query or message.from_user is None:
        await message.answer("Usage: /track <query>")
        return

    engine = get_bot_engine()
    session_factory = get_bot_session_factory(engine)
    async with session_factory() as session:
        tracker = Tracker(user_id=message.from_user.id, query=query)
        session.add(tracker)
        await session.commit()
        await session.refresh(tracker)
    await message.answer(f"Tracker #{tracker.id} created for '{query}'.")


@router.message(Command("tracks"))
async def cmd_tracks(message: Message) -> None:
    if message.from_user is None:
        await message.answer("Unknown user.")
        return

    engine = get_bot_engine()
    session_factory = get_bot_session_factory(engine)
    async with session_factory() as session:
        result = await session.execute(
            select(Tracker).where(
                Tracker.user_id == message.from_user.id,
                Tracker.active.is_(True),
            )
        )
        trackers = list(result.scalars())
    if not trackers:
        await message.answer("No active trackers.")
        return
    lines = ["Active trackers:"]
    for tracker in trackers:
        lines.append(f"#{tracker.id} {tracker.query} every {tracker.interval_min} min")
    await message.answer("\n".join(lines))


@router.message(Command("untrack"))
async def cmd_untrack(message: Message, command: CommandObject) -> None:
    tracker_id_raw = (command.args or "").strip()
    if not tracker_id_raw or message.from_user is None:
        await message.answer("Usage: /untrack <id>")
        return

    if not tracker_id_raw.isdigit():
        await message.answer("Usage: /untrack <id>")
        return

    engine = get_bot_engine()
    session_factory = get_bot_session_factory(engine)
    async with session_factory() as session:
        result = await session.execute(
            select(Tracker).where(
                Tracker.id == int(tracker_id_raw),
                Tracker.user_id == message.from_user.id,
                Tracker.active.is_(True),
            )
        )
        tracker = result.scalar_one_or_none()
        if tracker is None:
            await message.answer("Tracker not found.")
            return
        tracker.active = False
        await session.commit()
    await message.answer(f"Tracker #{tracker_id_raw} disabled.")


@router.callback_query(lambda callback: (callback.data or "").startswith(("lead:", "later:")))
async def handle_tracker_action(callback: CallbackQuery) -> None:
    if callback.from_user is None or not callback.data:
        await callback.answer("Unknown user.", show_alert=True)
        return

    action, _, raw_event_id = callback.data.partition(":")
    if not raw_event_id.isdigit():
        await callback.answer("Bad action.", show_alert=True)
        return

    engine = get_bot_engine()
    session_factory = get_bot_session_factory(engine)
    async with session_factory() as session:
        event = await session.scalar(
            select(TrackerEvent).where(
                TrackerEvent.id == int(raw_event_id),
                TrackerEvent.user_id == callback.from_user.id,
            )
        )
        if event is None:
            await callback.answer("Сигнал уже недоступен.", show_alert=True)
            return

        status = "in_progress" if action == "lead" else "deferred"
        await upsert_lead(
            session,
            user_id=callback.from_user.id,
            ad_id=int(event.ad_id or event.id),
            query=event.query,
            title=event.title,
            link=event.link,
            price_byn=event.price_byn,
            status=status,
            source="telegram_alert",
        )
        await session.commit()
    await callback.answer("Сохранено.")
