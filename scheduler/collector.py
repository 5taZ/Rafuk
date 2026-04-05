from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.config import Settings, get_settings
from api.database import get_engine, get_session_factory
from api.models import Base, Tracker
from api.services.kufar_client import KufarClient


async def notify_user(bot: Bot, user_id: int, message: str, session: AsyncSession) -> None:
    try:
        await bot.send_message(user_id, message)
    except TelegramForbiddenError:
        await session.execute(
            update(Tracker).where(Tracker.user_id == user_id).values(active=False)
        )
        await session.commit()


async def check_trackers(
    bot: Bot,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    async with session_factory() as session:
        result = await session.execute(select(Tracker).where(Tracker.active.is_(True)))
        trackers = list(result.scalars())
        client = KufarClient(settings)
        try:
            for tracker in trackers:
                payload = await client.search(query=tracker.query, currency="USD", size=10)
                ads = payload.get("ads", [])
                if not ads:
                    continue
                newest_id = int(ads[0].get("ad_id", 0))
                if tracker.last_seen_ad_id is None:
                    tracker.last_seen_ad_id = newest_id
                    continue
                if newest_id != tracker.last_seen_ad_id:
                    tracker.last_seen_ad_id = newest_id
                    await notify_user(
                        bot,
                        tracker.user_id,
                        (
                            f"New listing found for '{tracker.query}' at "
                            f"{datetime.now(UTC).isoformat()}"
                        ),
                        session,
                    )
            await session.commit()
        finally:
            await client.aclose()


def create_scheduler(
    bot: Bot,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone="Europe/Minsk")
    scheduler.add_job(
        check_trackers,
        trigger="interval",
        minutes=settings.alert_check_interval,
        kwargs={"bot": bot, "session_factory": session_factory, "settings": settings},
        id="tracker-check",
        replace_existing=True,
    )
    return scheduler


async def main() -> None:
    settings = get_settings()
    engine = get_engine()
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = get_session_factory(engine)
    bot = Bot(settings.bot_token)
    scheduler = create_scheduler(bot, session_factory, settings)
    scheduler.start()
    try:
        while True:
            await asyncio.sleep(3600)
    finally:
        scheduler.shutdown(wait=False)
        await engine.dispose()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
