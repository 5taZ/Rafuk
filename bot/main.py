from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.types import BotCommand, ErrorEvent

from api.config import get_settings
from bot.database import close_bot_engine, init_bot_engine
from bot.handlers.price import router as price_router
from bot.handlers.start import router as start_router
from bot.handlers.tracker import router as tracker_router

logger = logging.getLogger(__name__)

ERROR_TEXT = "An unexpected error occurred. Please try again later."


async def on_error(event: ErrorEvent) -> bool:
    """Log any unhandled handler exception and make sure the user gets a response."""
    update = event.update
    logger.error(
        "Unhandled error while processing update %s",
        getattr(update, "update_id", None),
        exc_info=(type(event.exception), event.exception, event.exception.__traceback__),
    )
    try:
        callback = getattr(update, "callback_query", None)
        message = getattr(update, "message", None)
        if callback is not None:
            await callback.answer(ERROR_TEXT, show_alert=True)
        elif message is not None:
            await message.answer(ERROR_TEXT)
    except Exception:
        logger.exception("Failed to notify the user about an unhandled error")
    return True


def build_dispatcher() -> Dispatcher:
    dispatcher = Dispatcher()
    dispatcher.errors.register(on_error)
    dispatcher.include_router(start_router)
    dispatcher.include_router(price_router)
    dispatcher.include_router(tracker_router)
    return dispatcher


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    settings = get_settings()
    await init_bot_engine()
    bot = Bot(settings.bot_token)
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
