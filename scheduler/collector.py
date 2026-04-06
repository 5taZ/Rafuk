from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from datetime import UTC, datetime

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError
from aiogram.types import InlineKeyboardMarkup
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select, text, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.config import Settings, get_settings
from api.database import get_engine, get_session_factory
from api.models import Tracker, TrackerEvent
from api.services.aggregator import (
    apply_search_mode,
    build_query_key,
    compute_price_stats,
    extract_prices,
    normalize_price_byn,
)
from api.services.history_service import (
    QuerySyncResult,
    snapshot_bucket,
    sync_query_listing_states,
    upsert_query_snapshot,
)
from api.services.kufar_client import KufarAPIError, KufarClient
from api.services.market_signals import duplicate_counts
from api.services.reseller_tools import matches_tracker_filters
from bot.keyboards import tracker_alert_keyboard

logger = logging.getLogger(__name__)


async def notify_user(
    bot: Bot,
    user_id: int,
    message: str,
    session: AsyncSession,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
    try:
        await bot.send_message(user_id, message, reply_markup=reply_markup)
    except TelegramForbiddenError:
        await session.execute(
            update(Tracker).where(Tracker.user_id == user_id).values(active=False)
        )
        await session.commit()


def persist_tracker_events(
    session: AsyncSession,
    tracker: Tracker,
    sync_result: QuerySyncResult,
) -> list[TrackerEvent]:
    created: list[TrackerEvent] = []
    for state in sync_result.new_listings[:10]:
        event = TrackerEvent(
            tracker_id=tracker.id,
            user_id=tracker.user_id,
            ad_id=state.ad_id,
            query=tracker.query,
            strict_mode=tracker.strict_mode,
            event_type="new_listing",
            title=state.title,
            link=state.link,
            price_byn=state.last_price_byn,
        )
        session.add(event)
        created.append(event)

    for state, delta in sync_result.price_drops[:10]:
        event = TrackerEvent(
            tracker_id=tracker.id,
            user_id=tracker.user_id,
            ad_id=state.ad_id,
            query=tracker.query,
            strict_mode=tracker.strict_mode,
            event_type="price_drop",
            title=state.title,
            link=state.link,
            price_byn=state.last_price_byn,
            delta_byn=delta,
        )
        session.add(event)
        created.append(event)
    return created


def _filter_sync_result_for_tracker(
    tracker: Tracker,
    sync_result: QuerySyncResult,
    ads_by_id: dict[int, dict[str, object]],
    duplicate_index: dict[int, int],
    market_stats: object,
) -> QuerySyncResult:
    new_listings = [
        state
        for state in sync_result.new_listings
        if (
            ad := ads_by_id.get(state.ad_id)
        ) and matches_tracker_filters(
            ad,
            market_stats=market_stats,
            duplicate_count=duplicate_index.get(state.ad_id, 0),
            min_discount_percent=tracker.min_discount_percent,
            max_price_byn=tracker.max_price_byn,
            seller_type=tracker.seller_type,
            condition=tracker.condition,
            region_name=tracker.region_name,
            config_keyword=tracker.config_keyword,
            exclude_duplicates=tracker.exclude_duplicates,
        )
    ]
    price_drops = [
        (state, delta)
        for state, delta in sync_result.price_drops
        if (
            ad := ads_by_id.get(state.ad_id)
        ) and matches_tracker_filters(
            ad,
            market_stats=market_stats,
            duplicate_count=duplicate_index.get(state.ad_id, 0),
            min_discount_percent=tracker.min_discount_percent,
            max_price_byn=tracker.max_price_byn,
            seller_type=tracker.seller_type,
            condition=tracker.condition,
            region_name=tracker.region_name,
            config_keyword=tracker.config_keyword,
            exclude_duplicates=tracker.exclude_duplicates,
        )
    ]
    return QuerySyncResult(
        stats_count=sync_result.stats_count,
        total_results=sync_result.total_results,
        new_listings=new_listings,
        price_drops=price_drops,
    )


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
                try:
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
                    ads_by_id = {
                        int(ad.get("ad_id", 0)): ad
                        for ad in ads
                        if int(ad.get("ad_id", 0)) > 0
                    }
                    duplicate_index = duplicate_counts(ads)
                    market_stats = compute_price_stats(extract_prices(list(ads_by_id.values())))

                    newest_id = int(ads[0].get("ad_id", 0)) if ads else None
                    newest_price_byn = normalize_price_byn(ads[0].get("price_byn")) if ads else None

                    for tracker in query_trackers:
                        if tracker.last_checked_at is None:
                            tracker.last_seen_ad_id = newest_id
                            tracker.last_seen_price_byn = newest_price_byn
                            tracker.last_checked_at = observed_at
                            continue

                        tracker_sync_result = _filter_sync_result_for_tracker(
                            tracker,
                            sync_result,
                            ads_by_id,
                            duplicate_index,
                            market_stats,
                        )
                        message = _build_tracker_message(query, strict_mode, tracker_sync_result)
                        if message:
                            created_events = persist_tracker_events(session, tracker, tracker_sync_result)
                            await session.flush()
                            primary_event = created_events[0] if created_events else None
                            keyboard = (
                                tracker_alert_keyboard(
                                    settings.mini_app_url,
                                    query=primary_event.query,
                                    listing_url=primary_event.link,
                                    event_id=primary_event.id,
                                )
                                if primary_event is not None
                                else None
                            )
                            await notify_user(
                                bot,
                                tracker.user_id,
                                message,
                                session,
                                reply_markup=keyboard,
                            )

                        tracker.last_seen_ad_id = newest_id
                        tracker.last_seen_price_byn = newest_price_byn
                        tracker.last_checked_at = observed_at
                except KufarAPIError as exc:
                    logger.error("Kufar API error for query %r (strict=%s): %s", query, strict_mode, exc)
                except Exception as exc:
                    logger.exception("Unexpected error for query %r (strict=%s): %s", query, strict_mode, exc)

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


async def check_db_health(engine) -> bool:
    """Check database connection health."""
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
            return True
    except OperationalError as e:
        logger.error(f"Database health check failed: {e}")
        return False


async def main() -> None:
    settings = get_settings()
    engine = get_engine()
    session_factory = get_session_factory(engine)
    bot = Bot(settings.bot_token)
    scheduler = create_scheduler(bot, session_factory, settings)

    # Initial health check
    if not await check_db_health(engine):
        logger.error("Database connection failed at startup. Exiting.")
        await engine.dispose()
        await bot.session.close()
        return

    scheduler.start()
    try:
        while True:
            # Periodic health check every 5 minutes
            if not await check_db_health(engine):
                logger.warning("Database connection lost. Attempting reconnect...")
                await engine.dispose()
                engine = get_engine()
                session_factory = get_session_factory(engine)
                if not await check_db_health(engine):
                    logger.error("Reconnection failed. Exiting.")
                    break
                logger.info("Database reconnected successfully.")
            await asyncio.sleep(300)
    finally:
        scheduler.shutdown(wait=False)
        await engine.dispose()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
