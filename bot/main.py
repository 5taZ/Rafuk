from __future__ import annotations

import asyncio
import logging
import os

from aiogram import Bot, Dispatcher
from aiogram.types import BotCommand
from sqlalchemy import text

from api.config import get_settings
from api.healthcheck import start_health_server, stop_health_server
from api.logging_config import configure_logging
from bot.api_client import close_http_client
from bot.commands import BOT_COMMANDS
from bot.database import close_bot_engine, get_bot_engine, init_bot_engine
from bot.handlers.analytics import router as analytics_router
from bot.handlers.callbacks import router as callbacks_router
from bot.handlers.start import router as start_router

# INF-H9: structured JSON logging (or LOG_FORMAT=text for local). Runs
# before the bot spins up routers so their @router.message handlers
# already use the configured formatter.
configure_logging(service="bot")


def build_dispatcher() -> Dispatcher:
    dispatcher = Dispatcher()
    dispatcher.include_router(start_router)
    dispatcher.include_router(analytics_router)
    dispatcher.include_router(callbacks_router)
    return dispatcher


async def _bot_readiness() -> bool:
    """INF-H6: readiness check used by /health/ready for the bot.

    A simple "SELECT 1" against the bot's dedicated engine is enough
    to prove the DB is reachable and the connection pool isn't
    exhausted — that's the only hard dependency the bot holds apart
    from Telegram's own API (which polling itself exercises).
    """
    try:
        engine = get_bot_engine()
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception as exc:
        logger.warning("bot readiness probe failed: %s", exc)
        return False


logger = logging.getLogger(__name__)


async def main() -> None:
    settings = get_settings()
    await init_bot_engine()
    bot = Bot(settings.bot_token.get_secret_value())
    await bot.set_my_commands(
        [
            BotCommand(command=command, description=description)
            for command, description in BOT_COMMANDS
        ]
    )
    # INF-H6: run a tiny HTTP health server on a side port so Docker
    # can probe liveness/readiness directly instead of relying on
    # `pgrep python`, which only proves the interpreter exists — not
    # that the event loop is turning or the DB is reachable.
    health_port = int(os.environ.get("BOT_HEALTH_PORT", "8001"))
    health_runner = await start_health_server(
        port=health_port, readiness=_bot_readiness
    )
    dispatcher = build_dispatcher()
    try:
        await dispatcher.start_polling(bot)
    finally:
        # Order matters slightly: drain the API client pool BEFORE closing
        # the DB engine, since some shutdown paths could still try to make
        # outbound calls. Both are best-effort idempotent.
        await stop_health_server(health_runner)
        await close_http_client()
        await close_bot_engine()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
