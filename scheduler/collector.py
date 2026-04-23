from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from datetime import UTC, datetime, timedelta

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError
from aiogram.types import InlineKeyboardMarkup
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import httpx
from sqlalchemy import delete, select, text, update
from sqlalchemy.exc import OperationalError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import joinedload

from api.config import Settings, get_settings
from api.database import get_engine, get_session_factory
from api.models import Tracker, TrackerEvent
from api.services.aggregator import (
    apply_search_mode,
    build_query_key,
    compute_category_price_stats,
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
from api.services.market_signals import region_label
from api.services.reseller_tools import matches_tracker_filters
from bot.keyboards import tracker_alert_keyboard

# Configure logging for the scheduler process (after imports, before usage)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

logger = logging.getLogger(__name__)

_TRACKER_QUERY_ERRORS = (
    OperationalError,
    SQLAlchemyError,
    httpx.HTTPError,
    httpx.TimeoutException,
    KufarAPIError,
    TypeError,
    ValueError,
)


async def notify_user(
    bot: Bot,
    telegram_user_id: int,
    message: str,
    session: AsyncSession,
    *,
    internal_user_id: int | None = None,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> bool:
    """Send message to user via telegram_user_id.

    Returns False if tracker should be deactivated.
    """
    try:
        await bot.send_message(telegram_user_id, message, reply_markup=reply_markup)
        return True
    except TelegramForbiddenError:
        if internal_user_id is not None:
            # Mark tracker as inactive - caller will commit
            await session.execute(
                update(Tracker)
                .where(
                    Tracker.user_id == internal_user_id,
                    Tracker.active.is_(True),
                )
                .values(active=False)
            )
        return False


async def _recent_event_keys(
    session: AsyncSession,
    tracker_id: int,
    hours: int = 24,
) -> set[tuple[int, str]]:
    """Return (ad_id, event_type) pairs that already have events within *hours*."""
    cutoff = datetime.now(UTC) - timedelta(hours=hours)
    result = await session.execute(
        select(TrackerEvent.ad_id, TrackerEvent.event_type).where(
            TrackerEvent.tracker_id == tracker_id,
            TrackerEvent.created_at >= cutoff,
        )
    )
    return {(row.ad_id, row.event_type) for row in result if row.ad_id is not None}


async def persist_tracker_events(
    session: AsyncSession,
    tracker: Tracker,
    sync_result: QuerySyncResult,
    ads_by_id: dict[int, dict[str, object]] | None = None,
) -> list[TrackerEvent]:
    # Skip events that were already recorded recently for this tracker + ad
    seen = await _recent_event_keys(session, tracker.id)

    created: list[TrackerEvent] = []
    for state in sync_result.new_listings[:10]:
        if (state.ad_id, "new_listing") in seen:
            continue
        # Get enriched data from ad if available
        ad = ads_by_id.get(state.ad_id) if ads_by_id else None
        thumbnail = ad.get("thumbnail") if ad else None
        seller_type = ad.get("seller_type") if ad else None
        region_name = region_label(ad) if ad else None

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
            thumbnail=thumbnail,
            seller_type=seller_type,
            region_name=region_name,
        )
        session.add(event)
        created.append(event)

    for state, delta in sync_result.price_drops[:10]:
        if (state.ad_id, "price_drop") in seen:
            continue
        # Get enriched data from ad if available
        ad = ads_by_id.get(state.ad_id) if ads_by_id else None
        thumbnail = ad.get("thumbnail") if ad else None
        seller_type = ad.get("seller_type") if ad else None
        region_name = region_label(ad) if ad else None

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
            thumbnail=thumbnail,
            seller_type=seller_type,
            region_name=region_name,
        )
        session.add(event)
        created.append(event)
    return created


def _filter_sync_result_for_tracker(
    tracker: Tracker,
    sync_result: QuerySyncResult,
    ads_by_id: dict[int, dict[str, object]],
) -> QuerySyncResult:
    market_stats = compute_price_stats(extract_prices(list(ads_by_id.values())))
    category_price_stats = compute_category_price_stats(list(ads_by_id.values()))
    new_listings = [
        state
        for state in sync_result.new_listings
        if (ad := ads_by_id.get(state.ad_id))
        and matches_tracker_filters(
            ad,
            market_stats=market_stats,
            category_price_stats=category_price_stats,
            min_discount_percent=tracker.min_discount_percent,
            max_price_byn=tracker.max_price_byn,
            seller_type=tracker.seller_type,
            condition=tracker.condition,
            region_name=tracker.region_name,
            config_keyword=tracker.config_keyword,
        )
    ]
    price_drops = [
        (state, delta)
        for state, delta in sync_result.price_drops
        if (ad := ads_by_id.get(state.ad_id))
        and matches_tracker_filters(
            ad,
            market_stats=market_stats,
            category_price_stats=category_price_stats,
            min_discount_percent=tracker.min_discount_percent,
            max_price_byn=tracker.max_price_byn,
            seller_type=tracker.seller_type,
            condition=tracker.condition,
            region_name=tracker.region_name,
            config_keyword=tracker.config_keyword,
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
    label = f"{query} [строгий]" if strict_mode else query

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
            delta_str = f"(-{round(delta)} р.)" if round(delta) > 0 else ""
            lines.append(
                f"• {state.title} - {_format_price_byn(state.last_price_byn)} {delta_str}".rstrip()
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
        # Only check active AND non-paused trackers, eager-load user for telegram_user_id
        result = await session.execute(
            select(Tracker)
            .where(Tracker.active.is_(True), Tracker.paused.is_(False))
            .options(joinedload(Tracker.user))
        )
        all_trackers = list(result.scalars())
        if not all_trackers:
            logger.debug("No active trackers to check")
            return

        # Filter out trackers whose interval_min hasn't elapsed yet
        now = datetime.now(UTC)
        trackers = [
            t
            for t in all_trackers
            if t.last_checked_at is None
            or (now - t.last_checked_at).total_seconds() >= t.interval_min * 60
        ]
        if not trackers:
            logger.debug(
                "All %d tracker(s) skipped — interval not elapsed yet",
                len(all_trackers),
            )
            return

        trackers_by_query: dict[tuple[str, bool], list[Tracker]] = defaultdict(list)
        for tracker in trackers:
            trackers_by_query[(tracker.query, tracker.strict_mode)].append(tracker)

        logger.info(
            "Tracker check: %d due (of %d total), %d unique query group(s)",
            len(trackers),
            len(all_trackers),
            len(trackers_by_query),
        )

        client = KufarClient(settings)
        try:
            observed_at = datetime.now(UTC)
            bucket_at = snapshot_bucket(observed_at)
            total_notified = 0
            total_errors = 0

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
                        int(ad.get("ad_id", 0)): ad for ad in ads if int(ad.get("ad_id", 0)) > 0
                    }

                    newest_id = int(ads[0].get("ad_id", 0)) if ads else None
                    newest_price_byn = (
                        normalize_price_byn(ads[0].get("price_byn")) if ads else None
                    )

                    safe_query = query.replace("\n", " ")[:80]
                    logger.info(
                        "Query %r [strict=%s]: %d ads, %d new, %d price drops",
                        safe_query,
                        strict_mode,
                        len(ads),
                        len(sync_result.new_listings),
                        len(sync_result.price_drops),
                    )

                    for tracker in query_trackers:
                        if tracker.last_checked_at is None:
                            logger.info(
                                "Tracker %d (user %d): first check, initializing baseline",
                                tracker.id,
                                tracker.user_id,
                            )
                            tracker.last_seen_ad_id = newest_id
                            tracker.last_seen_price_byn = newest_price_byn
                            tracker.last_checked_at = observed_at
                            continue

                        tracker_sync_result = _filter_sync_result_for_tracker(
                            tracker,
                            sync_result,
                            ads_by_id,
                        )
                        message = _build_tracker_message(query, strict_mode, tracker_sync_result)
                        if message:
                            created_events = await persist_tracker_events(
                                session, tracker, tracker_sync_result, ads_by_id
                            )
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
                            can_notify = await notify_user(
                                bot,
                                tracker.user.telegram_user_id,
                                message,
                                session,
                                internal_user_id=tracker.user_id,
                                reply_markup=keyboard,
                            )
                            if can_notify:
                                total_notified += 1
                                logger.info(
                                    "Tracker %d (user %d): notified (%d events)",
                                    tracker.id,
                                    tracker.user_id,
                                    len(created_events),
                                )
                            else:
                                logger.info(
                                    "Deactivated tracker %d for user %d (Telegram forbidden)",
                                    tracker.id,
                                    tracker.user_id,
                                )

                        tracker.last_seen_ad_id = newest_id
                        tracker.last_seen_price_byn = newest_price_byn
                        tracker.last_checked_at = observed_at

                    # Flush after each query group so a later failure
                    # doesn't discard this group's snapshot/state/events.
                    await session.flush()
                except _TRACKER_QUERY_ERRORS:
                    total_errors += 1
                    logger.exception(
                        "Error processing query %r [strict=%s], skipping",
                        query,
                        strict_mode,
                    )

            # Commit whatever succeeded — errors are logged but don't block
            await session.commit()
            logger.info(
                "Tracker check complete: notified %d, errors %d",
                total_notified,
                total_errors,
            )
        finally:
            await client.aclose()


def create_scheduler(
    bot: Bot,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone="Europe/Minsk")
    # Base tick interval: run frequently enough to respect the shortest
    # per-tracker interval_min.  Each tick skips trackers whose interval
    # hasn't elapsed yet, so a 30-min tracker is only checked every 30 min.
    tick_minutes = min(settings.alert_check_interval, 5)
    scheduler.add_job(
        check_trackers,
        trigger="interval",
        minutes=tick_minutes,
        kwargs={"bot": bot, "session_factory": session_factory, "settings": settings},
        id="tracker-check",
        replace_existing=True,
    )
    # Daily cleanup of old events and inactive listings
    scheduler.add_job(
        run_cleanup,
        trigger="cron",
        hour=3,  # Run at 3 AM
        minute=0,
        kwargs={"session_factory": session_factory},
        id="daily-cleanup",
        replace_existing=True,
    )
    return scheduler


async def run_cleanup(session_factory: async_sessionmaker[AsyncSession]) -> None:
    """Run daily cleanup of old data."""
    settings = get_settings()
    async with session_factory() as session:
        try:
            await cleanup_old_events(session, days=30)
            await cleanup_inactive_listing_states(session, days=90)
            await cleanup_stale_missing_watchlist(session, days=settings.auto_remove_missing_days)
            await session.commit()
            logger.info("Daily cleanup completed successfully")
        except (OperationalError, SQLAlchemyError):
            await session.rollback()
            logger.exception("Daily cleanup failed")


async def cleanup_old_events(session: AsyncSession, days: int = 30) -> int:
    """Delete tracker events older than specified days."""
    cutoff = datetime.now(UTC) - timedelta(days=days)
    result = await session.execute(delete(TrackerEvent).where(TrackerEvent.created_at < cutoff))
    deleted_count = result.rowcount
    if deleted_count > 0:
        logger.info("Cleaned up %d old tracker events (older than %d days)", deleted_count, days)
    return deleted_count


async def cleanup_inactive_listing_states(session: AsyncSession, days: int = 90) -> int:
    """Delete inactive listing states older than specified days."""
    from api.models import QueryListingState

    cutoff = datetime.now(UTC) - timedelta(days=days)
    result = await session.execute(
        delete(QueryListingState).where(
            QueryListingState.active.is_(False),
            QueryListingState.last_seen_at < cutoff,
        )
    )
    deleted_count = result.rowcount
    if deleted_count > 0:
        logger.info(
            "Cleaned up %d inactive listing states (older than %d days)",
            deleted_count,
            days,
        )
    return deleted_count


async def cleanup_stale_missing_watchlist(session: AsyncSession, days: int = 7) -> int:
    """Auto-remove watchlist items that have been missing for longer than the threshold."""
    from api.models import WatchlistItem

    cutoff = datetime.now(UTC) - timedelta(days=days)
    result = await session.execute(
        delete(WatchlistItem).where(
            WatchlistItem.market_status == "missing",
            WatchlistItem.missing_since_at.isnot(None),
            WatchlistItem.missing_since_at < cutoff,
        )
    )
    deleted_count = result.rowcount
    if deleted_count > 0:
        logger.info(
            "Auto-removed %d watchlist items missing for more than %d days",
            deleted_count,
            days,
        )
    return deleted_count


async def check_db_health(engine) -> bool:
    """Check database connection health."""
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
            return True
    except OperationalError as e:
        logger.error("Database health check failed: %s", e)
        return False


async def main() -> None:
    settings = get_settings()
    engine = get_engine()
    session_factory = get_session_factory(engine)
    bot = Bot(settings.bot_token.get_secret_value())
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
