from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.types import BotCommand

from api.config import get_settings
from bot.api_client import close_http_client
from bot.database import close_bot_engine, init_bot_engine
from bot.handlers.analytics import router as analytics_router
from bot.handlers.callbacks import router as callbacks_router
from bot.handlers.start import router as start_router

logging.basicConfig(level=logging.INFO)


def build_dispatcher() -> Dispatcher:
    dispatcher = Dispatcher()
    dispatcher.include_router(start_router)
    dispatcher.include_router(analytics_router)
    dispatcher.include_router(callbacks_router)
    return dispatcher


async def main() -> None:
    settings = get_settings()
    await init_bot_engine()
    bot = Bot(settings.bot_token.get_secret_value())
    await bot.set_my_commands(
        [
            BotCommand(command="app", description="Открыть мини-апп"),
            BotCommand(command="start", description="Приветствие"),
            BotCommand(command="deals", description="Активные сделки"),
            BotCommand(command="profit", description="Прибыль за месяц"),
            BotCommand(command="stats", description="Статистика по запросу"),
        ]
    )
    dispatcher = build_dispatcher()
    try:
        await dispatcher.start_polling(bot)
    finally:
        # Order matters slightly: drain the API client pool BEFORE closing
        # the DB engine, since some shutdown paths could still try to make
        # outbound calls. Both are best-effort idempotent.
        await close_http_client()
        await close_bot_engine()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
