from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import UTC, datetime

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.config import Settings, get_settings
from api.database import get_engine, get_session_factory
from api.models import Base, Tracker
from api.services.aggregator import apply_search_mode, build_query_key, normalize_price_byn
from api.services.history_service import (
    QuerySyncResult,
    snapshot_bucket,
    sync_query_listing_states,
    upsert_query_snapshot,
)
from api.services.kufar_client import KufarClient


async def notify_user(bot: Bot, user_id: int, message: str, session: AsyncSession) -> None:
    try:
        await bot.send_message(user_id, message)
    except TelegramForbiddenError:
        await session.execute(
            update(Tracker).where(Tracker.user_id == user_id).values(active=False)
        )
        await session.commit()


def _format_price_byn(value: float | None) -> str:
    if value is None:
        return "без цены"
    if value >= 1000:
        compact = f"{value / 1000:.2f}".rstrip("0").rstrip(".")
        return f"{compact} тыс. р."
    return f"{round(value)} р."


def _build_tracker_message(
    query: str,
    strict_mode: bool,
    sync_result: QuerySyncResult,
) -> str | None:
    lines: list[str] = []
    label = f'{query} [строгий]' if strict_mode else query

    if sync_result.new_listings:
        lines.append(f'Запрос "{label}"')
        lines.append(f"Новые объявления: {len(sync_result.new_listings)}")
        for state in sync_result.new_listings[:3]:
            lines.append(f"• {state.title} - {_format_price_byn(state.last_price_byn)}")
            if state.link:
                lines.append(state.link)
        if len(sync_result.new_listings) > 3:
            lines.append(f"• и ещё {len(sync_result.new_listings) - 3}")

    if sync_result.price_drops:
        if lines:
            lines.append("")
        if not sync_result.new_listings:
            lines.append(f'Запрос "{label}"')
        lines.append(f"Снижение цены: {len(sync_result.price_drops)}")
        for state, delta in sync_result.price_drops[:3]:
            lines.append(
                f"• {state.title} - {_format_price_byn(state.last_price_byn)} "
                f"(-{round(delta)} р.)"
            )
            if state.link:
                lines.append(state.link)
        if len(sync_result.price_drops) > 3:
            lines.append(f"• и ещё {len(sync_result.price_drops) - 3}")

    if not lines:
        return None
    return "\n".join(lines)


async def check_trackers(
    bot: Bot,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    async with session_factory() as session:
        result = await session.execute(select(Tracker).where(Tracker.active.is_(True)))
        trackers = list(result.scalars())
        trackers_by_query: dict[tuple[str, bool], list[Tracker]] = defaultdict(list)
        for tracker in trackers:
            trackers_by_query[(tracker.query, tracker.strict_mode)].append(tracker)

        client = KufarClient(settings)
        try:
            observed_at = datetime.now(UTC)
            bucket_at = snapshot_bucket(observed_at)

            for (query, strict_mode), query_trackers in trackers_by_query.items():
                payload = await client.search(query=query, currency="BYN", size=50)
                ads = apply_search_mode(payload.get("ads", []), query, strict_mode)
                search_key = build_query_key(query, strict_mode)
                await upsert_query_snapshot(
                    session,
                    query=search_key,
                    ads=ads,
                    total_results=len(ads),
                    bucket_at=bucket_at,
                )
                sync_result = await sync_query_listing_states(
                    session,
                    query=search_key,
                    ads=ads,
                    observed_at=observed_at,
                    total_results=len(ads),
                )

                newest_id = int(ads[0].get("ad_id", 0)) if ads else None
                newest_price_byn = normalize_price_byn(ads[0].get("price_byn")) if ads else None

                for tracker in query_trackers:
                    if tracker.last_checked_at is None:
                        tracker.last_seen_ad_id = newest_id
                        tracker.last_seen_price_byn = newest_price_byn
                        tracker.last_checked_at = observed_at
                        continue

                    message = _build_tracker_message(query, strict_mode, sync_result)
                    if message:
                        await notify_user(bot, tracker.user_id, message, session)

                    tracker.last_seen_ad_id = newest_id
                    tracker.last_seen_price_byn = newest_price_byn
                    tracker.last_checked_at = observed_at

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
