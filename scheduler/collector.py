from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError, TelegramRetryAfter
from aiogram.types import InlineKeyboardMarkup
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import delete, select, text, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from api.config import Settings, get_settings
from api.database import get_engine, get_session_factory
from api.models import QueryListingState, QuerySnapshot, Tracker, TrackerEvent
from api.services.aggregator import (
    PriceStats,
    apply_search_mode,
    build_query_key,
    compute_price_stats,
    extract_prices,
    normalize_price_byn,
    normalize_search_text,
)
from api.services.history_service import (
    QuerySyncResult,
    listing_ad_id,
    load_known_ad_ids,
    snapshot_bucket,
    sync_query_listing_states,
    upsert_query_snapshot,
)
from api.services.kufar_client import KufarClient
from api.services.market_signals import duplicate_counts
from api.services.reseller_tools import matches_tracker_filters
from bot.keyboards import tracker_alert_keyboard

logger = logging.getLogger(__name__)

# Kufar paging for one tracker group: follow the cursor until a page brings no
# unknown listings or this many pages were fetched.
PAGE_SIZE = 100
MAX_PAGES_PER_GROUP = 3

# Telegram allows about 30 messages per second per bot; stay well below it.
SEND_MIN_INTERVAL_SECONDS = 1 / 20
# A longer flood-control wait is not worth blocking the whole run for.
MAX_RETRY_AFTER_SECONDS = 60

SNAPSHOT_RETENTION_DAYS = 120
UNTRACKED_LISTING_STATE_RETENTION_DAYS = 90
TRACKED_LISTING_STATE_RETENTION_DAYS = 365

DB_HEALTH_CHECK_INTERVAL_SECONDS = 300
DB_STARTUP_MAX_BACKOFF_SECONDS = 60.0


@dataclass(slots=True)
class TrackerGroup:
    """Active trackers whose queries share one normalized query key."""

    key: str
    query: str
    strict_mode: bool
    tracker_ids: list[int] = field(default_factory=list)


@dataclass(slots=True)
class FetchedSample:
    ads: list[dict[str, Any]]
    total_results: int
    pages: int


@dataclass(slots=True)
class OutgoingAlert:
    tracker_id: int
    user_id: int
    text: str
    reply_markup: InlineKeyboardMarkup | None = None


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


def _matches_tracker(
    tracker: Tracker,
    ad: dict[str, Any] | None,
    market_stats: PriceStats,
    duplicate_index: dict[int, int],
) -> bool:
    if ad is None:
        return False
    return matches_tracker_filters(
        ad,
        market_stats=market_stats,
        duplicate_count=duplicate_index.get(listing_ad_id(ad), 0),
        min_discount_percent=tracker.min_discount_percent,
        max_price_byn=tracker.max_price_byn,
        seller_type=tracker.seller_type,
        condition=tracker.condition,
        region_name=tracker.region_name,
        config_keyword=tracker.config_keyword,
        exclude_duplicates=tracker.exclude_duplicates,
    )


def _filter_sync_result_for_tracker(
    tracker: Tracker,
    sync_result: QuerySyncResult,
    ads_by_id: dict[int, dict[str, Any]],
    duplicate_index: dict[int, int],
    market_stats: PriceStats | None = None,
) -> QuerySyncResult:
    if market_stats is None:
        market_stats = compute_price_stats(extract_prices(list(ads_by_id.values())))
    new_listings = [
        state
        for state in sync_result.new_listings
        if _matches_tracker(tracker, ads_by_id.get(state.ad_id), market_stats, duplicate_index)
    ]
    price_drops = [
        (state, delta)
        for state, delta in sync_result.price_drops
        if _matches_tracker(tracker, ads_by_id.get(state.ad_id), market_stats, duplicate_index)
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
    action_title: str | None = None,
) -> str | None:
    """Render an alert; ``action_title`` names the lot the action buttons refer to."""
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
    total_lots = len(sync_result.new_listings) + len(sync_result.price_drops)
    if action_title and total_lots > 1:
        lines.append("")
        lines.append(f"Кнопки ниже относятся к лоту «{action_title}»")
    return "\n".join(lines)


def _primary_event(events: list[TrackerEvent]) -> TrackerEvent | None:
    """Pick the event the alert buttons act on: the first one with a usable link."""
    for event in events:
        if event.link and event.link.startswith(("https://", "http://")):
            return event
    return None


async def _load_tracker_groups(
    session_factory: async_sessionmaker[AsyncSession],
) -> list[TrackerGroup]:
    async with session_factory() as session:
        rows = (
            await session.execute(
                select(Tracker.id, Tracker.query, Tracker.strict_mode)
                .where(Tracker.active.is_(True))
                .order_by(Tracker.id)
            )
        ).all()

    groups: dict[str, TrackerGroup] = {}
    for tracker_id, query, strict_mode in rows:
        if not normalize_search_text(query or "").strip():
            logger.warning("Tracker %s has an empty query; skipping it", tracker_id)
            continue
        key = build_query_key(query, bool(strict_mode))
        group = groups.get(key)
        if group is None:
            # The oldest tracker's raw query represents the whole group.
            group = TrackerGroup(key=key, query=query, strict_mode=bool(strict_mode))
            groups[key] = group
        group.tracker_ids.append(tracker_id)
    return list(groups.values())


async def _fetch_group_sample(
    client: KufarClient,
    group: TrackerGroup,
    known_ad_ids: set[int],
) -> FetchedSample:
    """Fetch recent listings of a group, following the cursor for a few pages.

    Paging stops early once a page contains only listings that were already seen
    for this query (results are sorted by date, so older pages are known too).
    """
    ads: list[dict[str, Any]] = []
    seen_ids: set[int] = set()
    kufar_total: int | None = None
    cursor: str | None = None
    pages = 0

    for _ in range(MAX_PAGES_PER_GROUP):
        payload = await client.search(
            query=group.query,
            currency="BYN",
            size=PAGE_SIZE,
            cursor=cursor,
        )
        pages += 1
        if kufar_total is None:
            kufar_total = KufarClient.extract_total(payload)
        page_ads = apply_search_mode(
            list(payload.get("ads") or []),
            group.query,
            group.strict_mode,
        )
        has_unknown = False
        for ad in page_ads:
            ad_id = listing_ad_id(ad)
            if ad_id <= 0 or ad_id in seen_ids:
                continue
            seen_ids.add(ad_id)
            ads.append(ad)
            if ad_id not in known_ad_ids:
                has_unknown = True
        if not has_unknown:
            break
        cursor = KufarClient.extract_next_cursor(payload)
        if not cursor:
            break

    # In strict mode Kufar's total counts unfiltered results.
    if group.strict_mode or kufar_total is None:
        total_results = len(ads)
    else:
        total_results = max(kufar_total, len(ads))
    return FetchedSample(ads=ads, total_results=total_results, pages=pages)


async def _process_group(
    group: TrackerGroup,
    *,
    client: KufarClient,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> list[OutgoingAlert]:
    """Fetch, sync and persist one group in its own transaction; return alerts to send.

    The transaction is committed before anything is sent, so a failure later in the
    run never rolls back state for which users were already notified.
    """
    async with session_factory() as session:
        known_ad_ids = await load_known_ad_ids(session, query=group.key)
        # Do not keep a transaction open while talking to Kufar.
        await session.rollback()

    sample = await _fetch_group_sample(client, group, known_ad_ids)
    observed_at = datetime.now(UTC)

    async with session_factory() as session:
        trackers = list(
            (
                await session.execute(
                    select(Tracker)
                    .where(Tracker.id.in_(group.tracker_ids), Tracker.active.is_(True))
                    .order_by(Tracker.id)
                )
            ).scalars()
        )
        if not trackers:
            return []

        # The scheduler's sample covers only a few recent pages, so it never
        # replaces a snapshot written from a full API scan.
        await upsert_query_snapshot(
            session,
            query=group.key,
            ads=sample.ads,
            total_results=sample.total_results,
            bucket_at=snapshot_bucket(observed_at),
            only_if_missing=True,
        )
        sync_result = await sync_query_listing_states(
            session,
            query=group.key,
            ads=sample.ads,
            observed_at=observed_at,
            total_results=sample.total_results,
        )
        ads_by_id = {listing_ad_id(ad): ad for ad in sample.ads}
        duplicate_index = duplicate_counts(sample.ads)
        market_stats = compute_price_stats(extract_prices(sample.ads))

        newest = sample.ads[0] if sample.ads else None
        newest_id = listing_ad_id(newest) if newest is not None else None
        newest_price_byn = (
            normalize_price_byn(newest.get("price_byn")) if newest is not None else None
        )

        drafts: list[tuple[Tracker, QuerySyncResult, list[TrackerEvent]]] = []
        for tracker in trackers:
            if tracker.last_checked_at is not None:
                tracker_sync_result = _filter_sync_result_for_tracker(
                    tracker,
                    sync_result,
                    ads_by_id,
                    duplicate_index,
                    market_stats,
                )
                if tracker_sync_result.new_listings or tracker_sync_result.price_drops:
                    events = persist_tracker_events(session, tracker, tracker_sync_result)
                    drafts.append((tracker, tracker_sync_result, events))
            # The first check of a tracker only records a baseline.
            tracker.last_seen_ad_id = newest_id
            tracker.last_seen_price_byn = newest_price_byn
            tracker.last_checked_at = observed_at

        await session.flush()

        alerts: list[OutgoingAlert] = []
        for tracker, tracker_sync_result, events in drafts:
            primary_event = _primary_event(events)
            message = _build_tracker_message(
                tracker.query,
                tracker.strict_mode,
                tracker_sync_result,
                action_title=primary_event.title if primary_event is not None else None,
            )
            if not message:
                continue
            keyboard = (
                tracker_alert_keyboard(
                    settings.mini_app_url,
                    query=primary_event.query,
                    listing_url=primary_event.link,
                    event_id=primary_event.id,
                    strict_mode=primary_event.strict_mode,
                )
                if primary_event is not None
                else None
            )
            alerts.append(
                OutgoingAlert(
                    tracker_id=tracker.id,
                    user_id=tracker.user_id,
                    text=message,
                    reply_markup=keyboard,
                )
            )

        await session.commit()

    logger.info(
        "Checked %s: %d trackers, %d listings from %d pages, %d new, %d price drops",
        group.key,
        len(trackers),
        len(sample.ads),
        sample.pages,
        len(sync_result.new_listings),
        len(sync_result.price_drops),
    )
    return alerts


async def deactivate_user_trackers(
    session_factory: async_sessionmaker[AsyncSession],
    user_id: int,
) -> int:
    """Deactivate every active tracker of a user who blocked the bot."""
    async with session_factory() as session:
        result = await session.execute(
            update(Tracker)
            .where(Tracker.user_id == user_id, Tracker.active.is_(True))
            .values(active=False)
        )
        await session.commit()
    return result.rowcount or 0


class AlertSender:
    """Sends alerts with throttling and per-message Telegram error handling."""

    def __init__(
        self,
        bot: Bot,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        min_interval: float | None = None,
    ) -> None:
        self._bot = bot
        self._session_factory = session_factory
        self._min_interval = SEND_MIN_INTERVAL_SECONDS if min_interval is None else min_interval
        self._last_sent_at: float | None = None
        self._blocked_users: set[int] = set()

    async def _throttle(self) -> None:
        loop = asyncio.get_running_loop()
        if self._last_sent_at is not None:
            wait = self._min_interval - (loop.time() - self._last_sent_at)
            if wait > 0:
                await asyncio.sleep(wait)
        self._last_sent_at = loop.time()

    async def send(self, alert: OutgoingAlert) -> bool:
        if alert.user_id in self._blocked_users:
            return False
        for attempt in range(2):
            await self._throttle()
            try:
                await self._bot.send_message(
                    alert.user_id,
                    alert.text,
                    reply_markup=alert.reply_markup,
                )
                return True
            except TelegramRetryAfter as exc:
                if attempt == 0 and exc.retry_after <= MAX_RETRY_AFTER_SECONDS:
                    logger.warning(
                        "Telegram flood control for user %s: retrying in %s s",
                        alert.user_id,
                        exc.retry_after,
                    )
                    await asyncio.sleep(exc.retry_after)
                    continue
                logger.warning(
                    "Dropped alert of tracker %s for user %s: flood control (retry after %s s)",
                    alert.tracker_id,
                    alert.user_id,
                    exc.retry_after,
                )
                return False
            except TelegramForbiddenError:
                self._blocked_users.add(alert.user_id)
                await self._deactivate(alert)
                return False
            except TelegramAPIError as exc:
                logger.warning(
                    "Failed to send alert of tracker %s to user %s: %s",
                    alert.tracker_id,
                    alert.user_id,
                    exc,
                )
                return False
            except Exception:
                logger.exception(
                    "Unexpected error while sending alert of tracker %s to user %s",
                    alert.tracker_id,
                    alert.user_id,
                )
                return False
        return False

    async def _deactivate(self, alert: OutgoingAlert) -> None:
        try:
            count = await deactivate_user_trackers(self._session_factory, alert.user_id)
        except Exception:
            logger.exception(
                "User %s blocked the bot, but deactivating their trackers failed",
                alert.user_id,
            )
            return
        logger.warning(
            "User %s blocked the bot (tracker %s): deactivated %d active trackers",
            alert.user_id,
            alert.tracker_id,
            count,
        )


async def check_trackers(
    bot: Bot,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    """Check all active trackers, one Kufar search and one transaction per query key.

    A failing group (Kufar, database or Telegram error) is logged and skipped; the
    other groups are still processed and committed.
    """
    groups = await _load_tracker_groups(session_factory)
    if not groups:
        return

    client = KufarClient(settings)
    sender = AlertSender(bot, session_factory)
    failed = 0
    try:
        for group in groups:
            try:
                alerts = await _process_group(
                    group,
                    client=client,
                    session_factory=session_factory,
                    settings=settings,
                )
            except Exception:
                failed += 1
                logger.exception("Tracker check failed for %s; skipping it this run", group.key)
                continue
            for alert in alerts:
                await sender.send(alert)
    finally:
        await client.aclose()

    if failed:
        logger.warning("Tracker check finished: %d of %d query groups failed", failed, len(groups))


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
        # Run the first check right after start instead of one interval later.
        next_run_time=datetime.now(UTC),
    )
    # Daily cleanup of old events, snapshots and listing states
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
    async with session_factory() as session:
        try:
            await cleanup_old_events(session, days=30)
            await cleanup_old_snapshots(session, days=SNAPSHOT_RETENTION_DAYS)
            await cleanup_stale_listing_states(session)
            await session.commit()
            logger.info("Daily cleanup completed successfully")
        except Exception:
            await session.rollback()
            logger.exception("Daily cleanup failed")


async def cleanup_old_events(session: AsyncSession, days: int = 30) -> int:
    """Delete tracker events older than specified days."""
    cutoff = datetime.now(UTC) - timedelta(days=days)
    result = await session.execute(
        delete(TrackerEvent).where(TrackerEvent.created_at < cutoff)
    )
    deleted_count = result.rowcount
    if deleted_count > 0:
        logger.info("Cleaned up %d old tracker events (older than %d days)", deleted_count, days)
    return deleted_count


async def cleanup_old_snapshots(
    session: AsyncSession,
    days: int = SNAPSHOT_RETENTION_DAYS,
) -> int:
    """Delete hourly query snapshots older than specified days."""
    cutoff = datetime.now(UTC) - timedelta(days=days)
    result = await session.execute(
        delete(QuerySnapshot).where(QuerySnapshot.snapshot_at < cutoff)
    )
    deleted_count = result.rowcount
    if deleted_count > 0:
        logger.info("Cleaned up %d query snapshots (older than %d days)", deleted_count, days)
    return deleted_count


async def cleanup_stale_listing_states(
    session: AsyncSession,
    *,
    untracked_days: int = UNTRACKED_LISTING_STATE_RETENTION_DAYS,
    tracked_days: int = TRACKED_LISTING_STATE_RETENTION_DAYS,
) -> int:
    """Delete listing states that were not seen for a long time.

    States of query keys without active trackers go after ``untracked_days``
    (active or not: nothing syncs them any more). States of tracked keys are kept
    much longer, because forgetting an ad_id turns it into a "new" listing again.
    """
    rows = (
        await session.execute(
            select(Tracker.query, Tracker.strict_mode).where(Tracker.active.is_(True))
        )
    ).all()
    tracked_keys = sorted(
        {build_query_key(query, bool(strict_mode)) for query, strict_mode in rows}
    )

    now = datetime.now(UTC)
    untracked = await session.execute(
        delete(QueryListingState).where(
            QueryListingState.query.not_in(tracked_keys),
            QueryListingState.last_seen_at < now - timedelta(days=untracked_days),
        )
    )
    deleted_count = untracked.rowcount or 0
    if tracked_keys:
        tracked = await session.execute(
            delete(QueryListingState).where(
                QueryListingState.query.in_(tracked_keys),
                QueryListingState.last_seen_at < now - timedelta(days=tracked_days),
            )
        )
        deleted_count += tracked.rowcount or 0
    if deleted_count > 0:
        logger.info("Cleaned up %d stale listing states", deleted_count)
    return deleted_count


async def check_db_health(engine: AsyncEngine) -> bool:
    """Check database connection health; never raises."""
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
            return True
    except Exception as exc:
        logger.error("Database health check failed: %s", exc)
        return False


async def wait_for_database(
    engine: AsyncEngine,
    *,
    initial_delay: float = 1.0,
    max_delay: float = DB_STARTUP_MAX_BACKOFF_SECONDS,
) -> None:
    """Block until the database answers, retrying with exponential backoff."""
    delay = initial_delay
    while not await check_db_health(engine):
        logger.warning("Database is not available yet; retrying in %.0f s", delay)
        await asyncio.sleep(delay)
        delay = min(delay * 2, max_delay)


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    settings = get_settings()
    engine = get_engine()
    session_factory = get_session_factory(engine)
    bot = Bot(settings.bot_token)
    scheduler = create_scheduler(bot, session_factory, settings)

    try:
        await wait_for_database(engine)
        scheduler.start()
        logger.info("Scheduler started")
        while True:
            await asyncio.sleep(DB_HEALTH_CHECK_INTERVAL_SECONDS)
            # Jobs survive a temporary outage: pool_pre_ping replaces dead
            # connections, so a failed check is only reported.
            if not await check_db_health(engine):
                logger.warning("Database is unavailable; jobs will fail until it recovers")
    finally:
        if scheduler.running:
            scheduler.shutdown(wait=False)
        await engine.dispose()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
