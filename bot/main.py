from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.types import BotCommand

from api.config import get_settings
from bot.database import close_bot_engine, init_bot_engine
from bot.handlers.price import router as price_router
from bot.handlers.start import router as start_router
from bot.handlers.tracker import router as tracker_router

logging.basicConfig(level=logging.INFO)


def build_dispatcher() -> Dispatcher:
    dispatcher = Dispatcher()
    dispatcher.include_router(start_router)
    dispatcher.include_router(price_router)
    dispatcher.include_router(tracker_router)
    return dispatcher


async def main() -> None:
    settings = get_settings()
    await init_bot_engine()
    bot = Bot(settings.bot_token.get_secret_value())
    await bot.set_my_commands(
        [
            BotCommand(command="app", description="Open mini app"),
            BotCommand(command="price", description="Price statistics"),
            BotCommand(command="top", description="Recent listings"),
            BotCommand(command="track", description="Create tracker"),
            BotCommand(command="tracks", description="List trackers"),
            BotCommand(command="untrack", description="Disable tracker"),
        ]
    )
    dispatcher = build_dispatcher()
    try:
        await dispatcher.start_polling(bot)
    finally:
        await close_bot_engine()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
