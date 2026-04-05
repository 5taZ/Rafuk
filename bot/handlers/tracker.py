from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message
from sqlalchemy import select

from api.database import get_engine, get_session_factory
from api.models import Base, Tracker

router = Router(name="tracker")


async def _get_session():
    engine = get_engine()
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = get_session_factory(engine)
    return engine, session_factory


@router.message(Command("track"))
async def cmd_track(message: Message, command: CommandObject) -> None:
    query = (command.args or "").strip()
    if not query or message.from_user is None:
        await message.answer("Usage: /track <query>")
        return

    engine, session_factory = await _get_session()
    try:
        async with session_factory() as session:
            tracker = Tracker(user_id=message.from_user.id, query=query)
            session.add(tracker)
            await session.commit()
            await session.refresh(tracker)
        await message.answer(f"Tracker #{tracker.id} created for '{query}'.")
    finally:
        await engine.dispose()


@router.message(Command("tracks"))
async def cmd_tracks(message: Message) -> None:
    if message.from_user is None:
        await message.answer("Unknown user.")
        return

    engine, session_factory = await _get_session()
    try:
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
    finally:
        await engine.dispose()


@router.message(Command("untrack"))
async def cmd_untrack(message: Message, command: CommandObject) -> None:
    tracker_id_raw = (command.args or "").strip()
    if not tracker_id_raw or message.from_user is None:
        await message.answer("Usage: /untrack <id>")
        return

    engine, session_factory = await _get_session()
    try:
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
    finally:
        await engine.dispose()
