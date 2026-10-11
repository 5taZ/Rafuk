from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from sqlalchemy.exc import SQLAlchemyError

from api.config import get_settings
from api.services.tracker_service import resume_paused_trackers
from api.services.users import ensure_user
from bot.database import get_bot_engine, get_bot_session_factory
from bot.keyboards import mini_app_keyboard

if TYPE_CHECKING:
    from aiogram.types import User

logger = logging.getLogger(__name__)

router = Router(name="start")


async def register_and_resume(user: User | None) -> int:
    """Register the user and resume trackers paused while they had blocked the bot.

    Returns how many trackers were resumed. Database errors are logged and reported as
    zero, so /start always answers.
    """
    if user is None:
        return 0
    session_factory = get_bot_session_factory(get_bot_engine())
    try:
        async with session_factory() as session:
            await ensure_user(
                session,
                user.id,
                first_name=getattr(user, "first_name", None),
                username=getattr(user, "username", None),
            )
            resumed = await resume_paused_trackers(session, user_id=user.id)
            await session.commit()
    except SQLAlchemyError:
        logger.warning("Failed to register user %s on /start", user.id, exc_info=True)
        return 0
    if resumed:
        logger.info("User %s is back: resumed %d paused trackers", user.id, len(resumed))
    return len(resumed)


@router.message(Command("start"))
async def cmd_start(message: Message) -> None:
    settings = get_settings()
    resumed = await register_and_resume(message.from_user)
    keyboard = mini_app_keyboard(settings.mini_app_url)
    text = (
        "Rafuks helps you inspect market prices, listings, and tracker alerts.\n"
        "Use /app to open the mini app or /help to see commands."
    )
    if resumed:
        noun = "tracker" if resumed == 1 else "trackers"
        text += (
            f"\nWelcome back! Resumed {resumed} {noun} paused while the bot was blocked. "
            "See /tracks."
        )
    if keyboard is None:
        text += "\nMini app button is disabled locally because Telegram WebApp requires HTTPS."
    await message.answer(text, reply_markup=keyboard)


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(
        "/app - open the mini app\n"
        "/price <query> - get price summary\n"
        "/top <query> - get top listings\n"
        "/track <query> - enable tracker\n"
        "/tracks - list active trackers\n"
        "/untrack <id> - disable tracker"
    )


@router.message(Command("app"))
async def cmd_app(message: Message) -> None:
    settings = get_settings()
    keyboard = mini_app_keyboard(settings.mini_app_url)
    if keyboard is None:
        await message.answer(
            "Mini app is disabled locally. Telegram WebApp buttons require HTTPS."
        )
        return
    await message.answer(
        "Open Rafuks mini app.",
        reply_markup=keyboard,
    )
