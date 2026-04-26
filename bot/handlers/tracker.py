"""Tracker notification callback handlers.

Handles inline button callbacks from tracker alerts:
- "В работу" (lead:) — saves listing to leads
- "Позже" (later:) — saves listing with deferred status

Command-based tracker management (/track, /tracks, /untrack) has been removed
in favor of the mini app interface.
"""

from __future__ import annotations

from aiogram import Router
from aiogram.types import CallbackQuery
from sqlalchemy import select

from api.models import TrackerEvent
from api.services.workflow_store import ensure_user, upsert_lead
from bot.database import get_bot_engine, get_bot_session_factory

router = Router(name="tracker_callbacks")


@router.callback_query(lambda callback: (callback.data or "").startswith(("lead:", "later:")))
async def handle_tracker_action(callback: CallbackQuery) -> None:
    if callback.from_user is None or not callback.data:
        await callback.answer("Не удалось определить пользователя.", show_alert=True)
        return

    action, _, raw_event_id = callback.data.partition(":")
    if not raw_event_id.isdigit():
        await callback.answer("Некорректное действие.", show_alert=True)
        return

    engine = get_bot_engine()
    session_factory = get_bot_session_factory(engine)
    async with session_factory() as session:
        # Resolve internal user_id from telegram user_id
        user_id = await ensure_user(
            session,
            telegram_user_id=callback.from_user.id,
            first_name=callback.from_user.first_name or "",
            username=callback.from_user.username,
        )

        event = await session.scalar(
            select(TrackerEvent).where(
                TrackerEvent.id == int(raw_event_id),
                TrackerEvent.user_id == user_id,
            )
        )
        if event is None:
            await callback.answer("Сигнал уже недоступен.", show_alert=True)
            return

        status = "researching" if action == "lead" else "new"
        await upsert_lead(
            session,
            user_id=user_id,
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
