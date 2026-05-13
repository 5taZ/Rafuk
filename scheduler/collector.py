from __future__ import annotations

import asyncio
import logging
import signal
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx
from aiogram import Bot
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramForbiddenError,
    TelegramNotFound,
    TelegramRetryAfter,
    TelegramUnauthorizedError,
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import delete, select, text, update
from sqlalchemy.exc import OperationalError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import joinedload

from api.config import Settings, get_settings
from api.database import get_engine, get_session_factory
from api.models import (
    AIAuditLog,
    LeadItem,
    LeadReminder,
    QueryListingState,
    QuerySnapshot,
    TelegramNotificationDLQ,
    Tracker,
    TrackerEvent,
)
from api.services.aggregator import (
    PriceStats,
    apply_search_mode,
    build_query_key,
    compute_category_price_stats,
    compute_price_stats,
    compute_price_vs_reference,
    extract_prices,
    normalize_price_byn,
)
from api.services.history_service import (
    QuerySyncResult,
    TrendReversal,
    detect_trend_reversal_up,
    load_query_snapshots,
    snapshot_bucket,
    sync_query_listing_states,
    upsert_query_snapshot,
)
from api.services.kufar_client import KufarAPIError, KufarClient
from api.services.market_signals import region_label
from api.services.reseller_tools import compute_deal_score, matches_tracker_filters
from bot.keyboards import enhanced_alert_keyboard, lead_reminder_keyboard, tracker_alert_keyboard
from scheduler.messages import (
    _build_new_listing_message,
    _build_price_drop_message,
    _build_threshold_message,
    _build_tracker_message,
    _format_price_byn,  # noqa: F401 — back-compat import for tests/external callers
)

# Maximum time (seconds) for a single tracker check cycle.
# Prevents the cycle from running indefinitely when Kufar is slow.
_CYCLE_TIMEOUT_SECONDS = 600

# Maximum events per tracker per check cycle (new listings + price drops).
_MAX_EVENTS_PER_TYPE = 10

# INF-H9: structured JSON logging (or LOG_FORMAT=text for local dev).
# Switched from the previous bare basicConfig() so scheduler emits the
# same line shape as API and bot, with a stable "service":"scheduler"
# field for log aggregators to filter on.
from api.logging_config import configure_logging as _configure_logging  # noqa: E402

_configure_logging(service="scheduler")

logger = logging.getLogger(__name__)

_TRACKER_QUERY_ERRORS: tuple[type[Exception], ...] = (
    OperationalError,
    SQLAlchemyError,
    httpx.HTTPError,
    httpx.TimeoutException,
    KufarAPIError,
    asyncio.TimeoutError,
)


async def _send_message_classified(
    bot: Bot,
    telegram_user_id: int,
    message: str,
    *,
    reply_markup: object | None = None,
) -> str:
    """PERF-M3 helper: send one Telegram message and return a string
    classifying the outcome — pure I/O, no SQLAlchemy session touched.

    The classification is the same one ``notify_user`` derives, just
    decoupled from the deactivation side-effect so this function is
    safe to call from inside ``asyncio.gather`` (the AsyncSession
    isn't safe under concurrent access).

    Returns:
      ``"sent"``    — Telegram accepted the message.
      ``"blocked"`` — user blocked / deleted the bot, the chat is no
                      longer reachable; caller should disable the
                      user's trackers.
      ``"retry"``   — transient Telegram error (rate limit, server
                      error). Caller leaves the row's ``sent`` /
                      tracker-active flags alone so the next tick
                      retries.
    """
    try:
        await bot.send_message(telegram_user_id, message, reply_markup=reply_markup)
        return "sent"
    except (TelegramForbiddenError, TelegramUnauthorizedError, TelegramNotFound):
        return "blocked"
    except TelegramRetryAfter as exc:
        logger.warning(
            "Telegram rate limit hit for user %d, retry_after=%ss",
            telegram_user_id,
            getattr(exc, "retry_after", "?"),
        )
        return "retry"
    except TelegramAPIError as exc:
        logger.warning(
            "Telegram API error sending to user %d: [%s] %s",
            telegram_user_id,
            type(exc).__name__,
            exc,
        )
        return "retry"


def _queue_notification_dlq(
    session: AsyncSession,
    *,
    telegram_user_id: int,
    message: str,
    source: str,
    internal_user_id: int | None = None,
    error_kind: str = "retry",
    error_message: str | None = None,
    retry_after_seconds: int | None = None,
) -> None:
    session.add(
        TelegramNotificationDLQ(
            user_id=internal_user_id,
            telegram_user_id=telegram_user_id,
            source=source,
            message=message[:4096],
            error_kind=error_kind[:64],
            error_message=error_message[:512] if error_message else None,
            retry_after_seconds=retry_after_seconds,
        )
    )


async def notify_user(
    bot: Bot,
    telegram_user_id: int,
    message: str,
    session: AsyncSession,
    *,
    internal_user_id: int | None = None,
    reply_markup: object | None = None,
) -> bool:
    """Send message to user via telegram_user_id.

    Returns False if tracker should be deactivated (user blocked/deleted bot
    or chat is no longer reachable). Other transient telegram errors are
    logged but the tracker stays active so the next tick can retry.

    Internally delegates the actual send to
    ``_send_message_classified`` (Wave 23 / PERF-M3) so the
    error-classification logic stays single-source-of-truth across
    the sequential per-tracker callers and the new bounded-parallel
    reminder loop.
    """
    outcome = await _send_message_classified(
        bot, telegram_user_id, message, reply_markup=reply_markup,
    )
    if outcome == "sent":
        return True
    if outcome == "blocked":
        # User blocked the bot or deleted the chat — disable all their
        # active trackers so we stop spamming the failing chat_id.
        if internal_user_id is not None:
            await session.execute(
                update(Tracker)
                .where(
                    Tracker.user_id == internal_user_id,
                    Tracker.active.is_(True),
                )
                .values(active=False)
            )
            await session.flush()
        return False
    # outcome == "retry" — keep state untouched
    _queue_notification_dlq(
        session,
        telegram_user_id=telegram_user_id,
        message=message,
        source="notify_user",
        internal_user_id=internal_user_id,
    )
    return True


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


async def _recent_trend_event_tracker_ids(
    session: AsyncSession,
    tracker_ids: list[int],
    hours: int = 24,
) -> set[int]:
    """Return tracker_ids that already received a trend_reversal event in the last *hours*.

    trend_reversal events are query-level (ad_id is null) so they can't
    reuse the ``_recent_events_by_tracker`` lookup, which keys on
    (ad_id, event_type) and skips null ad_ids.
    """
    if not tracker_ids:
        return set()
    cutoff = datetime.now(UTC) - timedelta(hours=hours)
    result_ids: set[int] = set()
    # Batch IN-clause to avoid hitting PostgreSQL's 65535 bind-parameter limit
    batch_size = 500
    for i in range(0, len(tracker_ids), batch_size):
        batch = tracker_ids[i : i + batch_size]
        result = await session.execute(
            select(TrackerEvent.tracker_id).where(
                TrackerEvent.tracker_id.in_(batch),
                TrackerEvent.event_type == "trend_reversal",
                TrackerEvent.created_at >= cutoff,
            )
        )
        result_ids.update(row.tracker_id for row in result)
    return result_ids


async def _recent_events_by_tracker(
    session: AsyncSession,
    tracker_ids: list[int],
    hours: int = 24,
) -> dict[int, set[tuple[int, str]]]:
    """Bulk-load recently-seen (ad_id, event_type) pairs grouped by tracker_id.

    Replaces the per-tracker N+1 SELECT loop in ``check_trackers`` — loading
    everything for the tick in one query keeps DB round-trips bounded by the
    number of query groups instead of by the total tracker count.
    """
    if not tracker_ids:
        return {}
    cutoff = datetime.now(UTC) - timedelta(hours=hours)
    out: dict[int, set[tuple[int, str]]] = defaultdict(set)
    # Batch IN-clause to avoid hitting PostgreSQL's 65535 bind-parameter limit
    batch_size = 500
    for i in range(0, len(tracker_ids), batch_size):
        batch = tracker_ids[i : i + batch_size]
        result = await session.execute(
            select(
                TrackerEvent.tracker_id, TrackerEvent.ad_id, TrackerEvent.event_type
            ).where(
                TrackerEvent.tracker_id.in_(batch),
                TrackerEvent.created_at >= cutoff,
            )
        )
        for row in result:
            if row.ad_id is not None:
                out[row.tracker_id].add((row.ad_id, row.event_type))
    return out


async def persist_tracker_events(
    session: AsyncSession,
    tracker: Tracker,
    sync_result: QuerySyncResult,
    ads_by_id: dict[int, dict[str, object]] | None = None,
    seen: set[tuple[int, str]] | None = None,
    trend_signal: TrendReversal | None = None,
    trend_already_sent: bool = False,
) -> list[TrackerEvent]:
    # Skip events that were already recorded recently for this tracker + ad.
    # Callers can pass a pre-computed ``seen`` (from ``_recent_events_by_tracker``)
    # to avoid an extra SELECT per tracker; falling back keeps the per-tracker
    # behaviour for any external/test callers.
    if seen is None:
        seen = await _recent_event_keys(session, tracker.id)

    created: list[TrackerEvent] = []
    for state in sync_result.new_listings[:_MAX_EVENTS_PER_TYPE]:
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
            price_type=state.price_type,
            thumbnail=thumbnail,
            seller_type=seller_type,
            region_name=region_name,
        )
        session.add(event)
        created.append(event)

    for state, delta in sync_result.price_drops[:_MAX_EVENTS_PER_TYPE]:
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
            price_type=state.price_type,
            delta_byn=delta,
            thumbnail=thumbnail,
            seller_type=seller_type,
            region_name=region_name,
        )
        session.add(event)
        created.append(event)

    if trend_signal is not None and not trend_already_sent:
        title = (
            f"Цена ↑ {trend_signal.rebound_pct:.1f}% после "
            f"{trend_signal.decline_pct:.1f}% падения"
        )[:255]
        event = TrackerEvent(
            tracker_id=tracker.id,
            user_id=tracker.user_id,
            ad_id=None,
            query=tracker.query,
            strict_mode=tracker.strict_mode,
            event_type="trend_reversal",
            title=title,
            link="",
            price_byn=trend_signal.today_byn,
            delta_byn=trend_signal.rebound_pct,
            parameters={
                "low_byn": trend_signal.low_byn,
                "today_byn": trend_signal.today_byn,
                "pre_high_byn": trend_signal.pre_high_byn,
                "decline_pct": trend_signal.decline_pct,
                "rebound_pct": trend_signal.rebound_pct,
                "low_at": trend_signal.low_at.isoformat(),
            },
        )
        session.add(event)
        created.append(event)
    return created


def _filter_sync_result_for_tracker(
    tracker: Tracker,
    sync_result: QuerySyncResult,
    ads_by_id: dict[int, dict[str, object]],
    *,
    market_stats=None,
    category_price_stats=None,
) -> QuerySyncResult:
    if market_stats is None:
        market_stats = compute_price_stats(extract_prices(list(ads_by_id.values())))
    if category_price_stats is None:
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


def _detect_threshold_alerts(
    ads_by_id: dict[int, dict[str, object]],
    tracker: Tracker,
    *,
    market_stats: PriceStats,
    category_price_stats: dict[int, PriceStats] | None = None,
    seen: set[tuple[int, str]],
) -> list[TrackerEvent]:
    """Check ads against tracker's alert thresholds and return TrackerEvent rows.

    Two independent threshold checks:
    - ``alert_price_threshold``: fire when an ad's price <= threshold
    - ``alert_discount_percent``: fire when discount from median >= threshold

    Both checks only consider ads that pass the tracker's base filters
    (seller, condition, region, config_keyword). Unlike the soft
    ``min_discount_percent`` filter which *excludes* non-matching ads,
    threshold alerts *escalate* matching ads into dedicated event types.
    """
    if not tracker.alert_price_threshold and not tracker.alert_discount_percent:
        return []

    events: list[TrackerEvent] = []
    for ad_id, ad in ads_by_id.items():
        # Must pass base tracker filters (seller, condition, region, etc.)
        if not matches_tracker_filters(
            ad,
            market_stats=market_stats,
            category_price_stats=category_price_stats,
            max_price_byn=None,  # Don't filter by max_price for alerts
            seller_type=tracker.seller_type,
            condition=tracker.condition,
            region_name=tracker.region_name,
            config_keyword=tracker.config_keyword,
        ):
            continue

        # Pass the raw ad so detect_price_type can return 0.0 for genuine
        # free listings instead of None — otherwise free items would be
        # silently dropped from "below threshold" alerts (they trivially
        # satisfy any threshold) and from "discount" alerts (they're 100%
        # off, the strongest possible signal).
        price_byn = normalize_price_byn(ad.get("price_byn"), ad)
        if price_byn is None:
            # negotiable — unknown price, can't evaluate either alert
            continue
        price_type = "free" if price_byn == 0.0 else "fixed"

        # Price threshold alert
        if tracker.alert_price_threshold and price_byn <= float(tracker.alert_price_threshold):
            if (ad_id, "price_threshold_alert") in seen:
                continue
            title = str(ad.get("subject", ""))[:255]
            link = str(ad.get("ad_link", ""))
            thumbnail = ad.get("thumbnail")
            seller_type = ad.get("seller_type")
            region_name_val = region_label(ad) if ad else None

            events.append(TrackerEvent(
                tracker_id=tracker.id,
                user_id=tracker.user_id,
                ad_id=ad_id,
                query=tracker.query,
                strict_mode=tracker.strict_mode,
                event_type="price_threshold_alert",
                title=title,
                link=link,
                price_byn=price_byn,
                price_type=price_type,
                parameters={
                    "threshold": float(tracker.alert_price_threshold),
                },
                thumbnail=thumbnail,
                seller_type=seller_type,
                region_name=region_name_val,
            ))

        # Discount alert
        if tracker.alert_discount_percent:
            discount_pct = abs(
                compute_price_vs_reference(ad, market_stats, category_price_stats)
            )
            if discount_pct >= float(tracker.alert_discount_percent):
                if (ad_id, "discount_alert") in seen:
                    continue
                title = str(ad.get("subject", ""))[:255]
                link = str(ad.get("ad_link", ""))
                thumbnail = ad.get("thumbnail")
                seller_type = ad.get("seller_type")
                region_name_val = region_label(ad) if ad else None

                events.append(TrackerEvent(
                    tracker_id=tracker.id,
                    user_id=tracker.user_id,
                    ad_id=ad_id,
                    query=tracker.query,
                    strict_mode=tracker.strict_mode,
                    event_type="discount_alert",
                    title=title,
                    link=link,
                    price_byn=price_byn,
                    price_type=price_type,
                    delta_byn=discount_pct,
                    parameters={
                        "discount_percent": round(discount_pct, 1),
                        "threshold_percent": float(tracker.alert_discount_percent),
                        "median_byn": market_stats.median,
                    },
                    thumbnail=thumbnail,
                    seller_type=seller_type,
                    region_name=region_name_val,
                ))

    # Cap threshold events to prevent huge notifications
    _max_threshold_events = 10
    return events[:_max_threshold_events]


async def check_trackers(
    bot: Bot,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    """Check all due trackers for new listings and price drops.

    Wrapped in an overall timeout to prevent the cycle from running
    indefinitely when Kufar is slow or there are many query groups.
    """
    try:
        await asyncio.wait_for(
            _check_trackers_inner(bot, session_factory, settings),
            timeout=_CYCLE_TIMEOUT_SECONDS,
        )
    except TimeoutError:
        logger.error(
            "Tracker check cycle exceeded %ds timeout — aborting",
            _CYCLE_TIMEOUT_SECONDS,
        )


async def _check_trackers_inner(
    bot: Bot,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    async with session_factory() as session:
        # BE-03 / INF-05: previously this loaded every active+unpaused
        # tracker into memory and filtered on `interval_min` in Python.
        # With 10k+ trackers that's tens of MB and a lot of GC pressure
        # for one cycle of work. Now we shape a dialect-agnostic SQL
        # filter so only DUE trackers leave the database, plus a hard
        # safety LIMIT so a pathological dataset can't OOM a worker.
        now = datetime.now(UTC)
        cutoff_max = now  # never-checked rows are always due
        # An "interval has elapsed" predicate built without dialect-
        # specific date math: we compare `last_checked_at` against
        # ``now - interval_min minutes``. SQLAlchemy renders this as
        # ``last_checked_at <= :now - INTERVAL '1 minute' * interval_min``
        # on Postgres and as ``last_checked_at <= datetime(:now, '-' ||
        # interval_min || ' minutes')`` on SQLite. The portable shape:
        # use ``func.... `` would tie us to a dialect — instead, do the
        # interval math via Python (one row per distinct interval_min)
        # which Postgres' planner handles fine through the
        # idx_trackers_last_checked index.
        #
        # Realistically intervals come from a small enum (15, 30, 60,
        # 180, 720) so the resulting OR-list is short.
        from sqlalchemy import or_
        stmt = (
            select(Tracker)
            .where(Tracker.active.is_(True), Tracker.paused.is_(False))
            .options(joinedload(Tracker.user))
            .order_by(Tracker.last_checked_at.asc().nulls_first())
            .limit(2000)  # safety cap; tracked-user counts well below this
        )
        # We can't compute the "due" predicate in a single SQL
        # expression without dialect-specific date arithmetic, but we
        # can prune most never-due rows on the SQL side: a tracker is
        # only DUE if last_checked_at is NULL or older than 'cutoff_max -
        # smallest_interval'. The smallest configured interval is
        # 15 minutes per the Tracker.__init__ default — anything more
        # recent than 15 minutes ago is definitely not due regardless
        # of interval_min.
        smallest_interval_min = 15
        coarse_cutoff = cutoff_max - timedelta(minutes=smallest_interval_min)
        stmt = stmt.where(
            or_(
                Tracker.last_checked_at.is_(None),
                Tracker.last_checked_at <= coarse_cutoff,
            )
        )
        result = await session.execute(stmt)
        all_trackers = list(result.scalars().unique())
        if not all_trackers:
            logger.debug("No active trackers due for check")
            return

        # Final per-row check now that we have only candidates, not
        # the full table.
        trackers = [
            t
            for t in all_trackers
            if t.last_checked_at is None
            or (now - t.last_checked_at).total_seconds() >= t.interval_min * 60
        ]
        if not trackers:
            logger.debug(
                "All %d candidate tracker(s) skipped — interval not elapsed yet",
                len(all_trackers),
            )
            return

        trackers_by_query: dict[tuple[str, bool, int | None], list[Tracker]] = defaultdict(list)
        for tracker in trackers:
            key = (tracker.query, tracker.strict_mode, tracker.category_id)
            trackers_by_query[key].append(tracker)

        # Bulk-load recently-seen events for all due trackers in a single
        # query — avoids N+1 SELECTs inside persist_tracker_events.
        seen_by_tracker = await _recent_events_by_tracker(
            session, [t.id for t in trackers]
        )
        # Trend events live at the query level (ad_id is null) so they
        # need their own debounce lookup. Bulk-load once per tick.
        trend_sent_recently = await _recent_trend_event_tracker_ids(
            session, [t.id for t in trackers]
        )

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
            total_errors = 0
            # PERF-M3: collect all per-listing / trend / threshold
            # notifications during the DB pass and dispatch them AFTER
            # the outer commit so a slow Telegram doesn't block DB
            # writes. See ``_dispatch_tracker_notifications``.
            pending_notifications: list[_TrackerNotifyJob] = []

            for (query, strict_mode, category_id), query_trackers in trackers_by_query.items():
                try:
                    payload = await client.search(
                        query=query,
                        currency="BYN",
                        size=50,
                        category=category_id,
                    )
                    ads = apply_search_mode(payload.get("ads", []), query, strict_mode)
                except _TRACKER_QUERY_ERRORS:
                    total_errors += 1
                    logger.exception(
                        "Error fetching query %r [strict=%s category=%s], skipping",
                        query,
                        strict_mode,
                        category_id,
                    )
                    continue

                search_key = build_query_key(query, strict_mode, category_id)
                # Use a savepoint per query group so that a rollback only
                # discards THIS group's changes — previous groups' flushed
                # data stays intact in the outer transaction.
                async with session.begin_nested():
                    try:
                        await upsert_query_snapshot(
                            session,
                            query=search_key,
                            ads=ads,
                            total_results=len(ads),
                            bucket_at=bucket_at,
                        )
                        # Flush so the just-upserted snapshot is visible to
                        # the trend detector below.
                        await session.flush()
                        snapshots = await load_query_snapshots(
                            session, query=search_key, days=10
                        )
                        trend_signal = detect_trend_reversal_up(snapshots)
                        sync_result = await sync_query_listing_states(
                            session,
                            query=search_key,
                            ads=ads,
                            observed_at=observed_at,
                            total_results=len(ads),
                        )
                        ads_by_id = {
                            int(ad["ad_id"]): ad
                            for ad in ads
                            if int(ad.get("ad_id", 0)) > 0
                        }
                        # Hoist these out of the per-tracker loop — they
                        # depend only on the query result, not the tracker.
                        # Was being recomputed inside _filter_sync_result_for_tracker
                        # once per tracker sharing the same query.
                        ads_list = list(ads_by_id.values())
                        market_stats = compute_price_stats(extract_prices(ads_list))
                        category_price_stats = compute_category_price_stats(ads_list)

                        newest_id = int(ads[0].get("ad_id", 0)) if ads else None
                        newest_price_byn = (
                            normalize_price_byn(ads[0].get("price_byn")) if ads else None
                        )

                        safe_query = query.replace("\n", " ")[:80]
                        logger.info(
                            "Query %r [strict=%s category=%s]: %d ads, %d new, %d price drops",
                            safe_query,
                            strict_mode,
                            category_id,
                            len(ads),
                            len(sync_result.new_listings),
                            len(sync_result.price_drops),
                        )

                        for tracker in query_trackers:
                            is_first_check = tracker.last_checked_at is None
                            if is_first_check:
                                logger.info(
                                    "Tracker %d (user %d): first check, initializing baseline",
                                    tracker.id,
                                    tracker.user_id,
                                )
                                tracker.last_seen_ad_id = newest_id
                                tracker.last_seen_price_byn = newest_price_byn
                                tracker.last_checked_at = observed_at
                                # Mark all current listings as seen for this tracker
                                # so they don't generate events on the next cycle.
                                # sync_query_listing_states already recorded them as new
                                # for the query, but this tracker should skip them.
                                seen_by_tracker[tracker.id] = {
                                    (state.ad_id, "new_listing")
                                    for state in sync_result.new_listings
                                }
                                continue

                            tracker_sync_result = _filter_sync_result_for_tracker(
                                tracker,
                                sync_result,
                                ads_by_id,
                                market_stats=market_stats,
                                category_price_stats=category_price_stats,
                            )
                            trend_already_sent = tracker.id in trend_sent_recently
                            seen_for_tracker = seen_by_tracker.get(tracker.id, set())

                            # Persist all events first so we have their data
                            await persist_tracker_events(
                                session,
                                tracker,
                                tracker_sync_result,
                                ads_by_id,
                                seen=seen_for_tracker,
                                trend_signal=trend_signal,
                                trend_already_sent=trend_already_sent,
                            )
                            await session.flush()
                            if trend_signal is not None and not trend_already_sent:
                                trend_sent_recently.add(tracker.id)

                            # Send per-listing enhanced notifications for new listings
                            for state in tracker_sync_result.new_listings[:3]:
                                ad = ads_by_id.get(state.ad_id)
                                median_byn = (
                                    market_stats.median
                                    if market_stats.median > 0 else None
                                )
                                discount_pct = None
                                liquidity_label = None
                                if ad is not None:
                                    discount_pct = abs(
                                        compute_price_vs_reference(
                                            ad, market_stats, category_price_stats
                                        )
                                    )
                                    try:
                                        deal = compute_deal_score(
                                            ad, query=query, market_stats=market_stats
                                        )
                                        if deal.score >= 70:
                                            liquidity_label = "Высокая ликвидность"
                                        elif deal.score >= 40:
                                            liquidity_label = "Средняя ликвидность"
                                    except Exception:  # noqa: BLE001
                                        pass
                                listing_msg = _build_new_listing_message(
                                    state,
                                    median_byn=median_byn,
                                    discount_pct=discount_pct,
                                    liquidity=liquidity_label,
                                )
                                keyboard = enhanced_alert_keyboard(
                                    ad_id=state.ad_id,
                                    listing_url=state.link,
                                )
                                # PERF-M3: queue instead of sending now.
                                # Dispatch after the outer commit so
                                # Telegram latency doesn't hold the DB
                                # transaction open.
                                pending_notifications.append(_TrackerNotifyJob(
                                    telegram_user_id=tracker.user.telegram_user_id,
                                    internal_user_id=tracker.user_id,
                                    message=listing_msg,
                                    reply_markup=keyboard,
                                ))

                            # Send per-listing enhanced notifications for price drops
                            for state, delta in tracker_sync_result.price_drops[:3]:
                                ad = ads_by_id.get(state.ad_id)
                                median_byn = (
                                    market_stats.median
                                    if market_stats.median > 0 else None
                                )
                                discount_pct = None
                                if ad is not None:
                                    discount_pct = abs(
                                        compute_price_vs_reference(
                                            ad, market_stats, category_price_stats
                                        )
                                    )
                                drop_msg = _build_price_drop_message(
                                    state,
                                    delta,
                                    median_byn=median_byn,
                                    discount_pct=discount_pct,
                                )
                                keyboard = enhanced_alert_keyboard(
                                    ad_id=state.ad_id,
                                    listing_url=state.link,
                                )
                                # PERF-M3: queue; see new_listings block above.
                                pending_notifications.append(_TrackerNotifyJob(
                                    telegram_user_id=tracker.user.telegram_user_id,
                                    internal_user_id=tracker.user_id,
                                    message=drop_msg,
                                    reply_markup=keyboard,
                                ))

                            # Send trend reversal as a separate message (uses old keyboard)
                            if trend_signal is not None and not trend_already_sent:
                                trend_msg = _build_tracker_message(
                                    query,
                                    strict_mode,
                                    QuerySyncResult(
                                        stats_count=tracker_sync_result.stats_count,
                                        total_results=tracker_sync_result.total_results,
                                        new_listings=[],
                                        price_drops=[],
                                    ),
                                    trend_signal=trend_signal,
                                    trend_already_sent=False,
                                )
                                if trend_msg:
                                    keyboard = tracker_alert_keyboard(
                                        settings.mini_app_url,
                                        query=query,
                                        listing_url=None,
                                    )
                                    # PERF-M3: queue; see new_listings block.
                                    pending_notifications.append(_TrackerNotifyJob(
                                        telegram_user_id=tracker.user.telegram_user_id,
                                        internal_user_id=tracker.user_id,
                                        message=trend_msg,
                                        reply_markup=keyboard,
                                    ))

                            tracker.last_seen_ad_id = newest_id
                            tracker.last_seen_price_byn = newest_price_byn
                            tracker.last_checked_at = observed_at

                            # ── Threshold alerts (price ≤ X, discount ≥ Y%) ──
                            threshold_events = _detect_threshold_alerts(
                                ads_by_id,
                                tracker,
                                market_stats=market_stats,
                                category_price_stats=category_price_stats,
                                seen=seen_for_tracker,
                            )
                            if threshold_events:
                                for evt in threshold_events:
                                    session.add(evt)
                                await session.flush()
                                threshold_msg = _build_threshold_message(
                                    tracker, threshold_events
                                )
                                if threshold_msg:
                                    primary = threshold_events[0]
                                    keyboard = tracker_alert_keyboard(
                                        settings.mini_app_url,
                                        query=primary.query,
                                        listing_url=primary.link,
                                    )
                                    # PERF-M3: queue; see new_listings block.
                                    pending_notifications.append(_TrackerNotifyJob(
                                        telegram_user_id=tracker.user.telegram_user_id,
                                        internal_user_id=tracker.user_id,
                                        message=threshold_msg,
                                        reply_markup=keyboard,
                                    ))
                                    logger.info(
                                        "Tracker %d (user %d): threshold alert queued "
                                        "(%d events)",
                                        tracker.id,
                                        tracker.user_id,
                                        len(threshold_events),
                                    )

                        # Savepoint auto-commits on clean exit, preserving
                        # this group's data even if a later group fails.
                    except _TRACKER_QUERY_ERRORS:
                        total_errors += 1
                        # Rolling back the savepoint only discards this
                        # group's changes — previous groups are safe.
                        logger.exception(
                            "Error processing query %r [strict=%s category=%s], skipping",
                            query,
                            strict_mode,
                            category_id,
                        )

            # Commit whatever succeeded — errors are logged but don't block
            await session.commit()

            # PERF-M3: dispatch queued notifications AFTER the commit.
            # If Telegram is slow or flaky, the DB state is already safe
            # (snapshots upserted, tracker.last_seen_* advanced, events
            # persisted) and the worst case is a notification gets lost
            # for one tick — same failure mode as before, just without
            # holding an open transaction for the duration of the sends.
            total_notified = await _dispatch_tracker_notifications(
                bot, pending_notifications, session_factory,
            )
            logger.info(
                "Tracker check complete: notified %d (of %d queued), errors %d",
                total_notified,
                len(pending_notifications),
                total_errors,
            )
        finally:
            await client.aclose()


# PERF-M3: cap on parallel ``check_reminders`` Telegram sends. Set
# conservatively below Telegram's documented ~30 msg/sec global ceiling
# so a tick with 100+ due reminders doesn't trip rate limits while
# still cutting wall-clock by ~5×. Each in-flight send is to a
# DIFFERENT user (one reminder per (lead, user)) so per-user rate
# limits aren't a concern.
_REMINDER_SEND_CONCURRENCY = 5


# PERF-M3: tracker-loop concurrency cap. Lower than the reminder cap
# because each unit of work is "all jobs for one user" (possibly up to
# 8 sends per tracker × N trackers per user), not a single message.
# 5 concurrent users × up to ~8 in-flight per-user serial sends is still
# well under Telegram's 30 msg/sec global limit. Per-user serialization
# is intentional — Telegram rate-limits individual chats to ~1 msg/sec,
# so we must never parallelize within a single telegram_user_id.
_TRACKER_USER_CONCURRENCY = 5


@dataclass(slots=True)
class _TrackerNotifyJob:
    """PERF-M3: a single pending Telegram notification collected during
    the ``check_trackers`` DB pass and dispatched afterwards.

    Decoupling the "what should be sent" from the "send it now" lets us
    fan out across users with bounded concurrency instead of blocking
    the tick on ``300 trackers × 3 notif × RTT`` of sequential Telegram
    latency. The fields are a strict subset of the ``notify_user``
    argument list — internal_user_id is kept so a blocked-user outcome
    can trigger the same per-user tracker deactivation as before,
    batched across the whole tick.
    """
    telegram_user_id: int
    internal_user_id: int
    message: str
    reply_markup: object | None


async def _dispatch_tracker_notifications(
    bot: Bot,
    jobs: list[_TrackerNotifyJob],
    session_factory: async_sessionmaker[AsyncSession],
) -> int:
    """PERF-M3: fan out collected tracker notifications after the DB
    transaction commits.

    Group jobs by ``telegram_user_id`` and send each user's batch
    **serially** (Telegram rate-limits individual chats to ~1 msg/sec)
    but schedule up to ``_TRACKER_USER_CONCURRENCY`` user-batches
    **in parallel** across distinct chats. On the first ``blocked``
    outcome for a user, drop the rest of that user's jobs and mark
    their ``internal_user_id`` for bulk tracker deactivation.

    Returns the number of successfully sent messages (i.e. only
    ``"sent"`` outcomes; ``"retry"`` is not counted, matching the
    ``check_reminders`` accounting established in Wave 23).

    After the fan-out, if any users turned out to be blocked, a single
    ``UPDATE Tracker SET active=False WHERE user_id IN (...)`` is
    issued in a fresh session. Same semantic as the per-call
    deactivation ``notify_user`` performed before, just batched so a
    tick with K blocked users does 1 UPDATE instead of up to
    K × jobs-per-user.
    """
    if not jobs:
        return 0

    by_user: dict[int, list[_TrackerNotifyJob]] = defaultdict(list)
    for job in jobs:
        by_user[job.telegram_user_id].append(job)

    sem = asyncio.Semaphore(_TRACKER_USER_CONCURRENCY)

    async def _send_for_user(
        user_jobs: list[_TrackerNotifyJob],
    ) -> tuple[int, int | None, list[_TrackerNotifyJob]]:
        """Serial send loop for one user."""
        async with sem:
            sent = 0
            retries: list[_TrackerNotifyJob] = []
            for job in user_jobs:
                outcome = await _send_message_classified(
                    bot,
                    job.telegram_user_id,
                    job.message,
                    reply_markup=job.reply_markup,
                )
                if outcome == "sent":
                    sent += 1
                elif outcome == "blocked":
                    # Skip the rest of this user's queue — same effect
                    # as the old inline ``break`` but now applied across
                    # ALL of that user's trackers, not just one.
                    return sent, job.internal_user_id, retries
                # outcome == "retry": keep trying; a transient error on
                # one job doesn't imply the next will fail too.
                elif outcome == "retry":
                    retries.append(job)
            return sent, None, retries

    results = await asyncio.gather(
        *[_send_for_user(user_jobs) for user_jobs in by_user.values()]
    )
    total_sent = sum(s for s, _, _ in results)
    blocked_user_ids = {uid for _, uid, _ in results if uid is not None}
    retry_jobs = [job for _, _, retries in results for job in retries]

    if blocked_user_ids or retry_jobs:
        async with session_factory() as session:
            if blocked_user_ids:
                await session.execute(
                    update(Tracker)
                    .where(
                        Tracker.user_id.in_(blocked_user_ids),
                        Tracker.active.is_(True),
                    )
                    .values(active=False)
                )
            for job in retry_jobs:
                _queue_notification_dlq(
                    session,
                    telegram_user_id=job.telegram_user_id,
                    message=job.message,
                    source="tracker",
                    internal_user_id=job.internal_user_id,
                )
            await session.commit()
            if blocked_user_ids:
                logger.info(
                    "Tracker notifications: deactivated trackers for %d blocked user(s)",
                    len(blocked_user_ids),
                )

    return total_sent


async def check_reminders(
    bot: Bot,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    """Send due reminders via bot and mark them as sent.

    PERF-M3: refactored from a sequential ``for reminder: await
    notify_user`` loop into a 3-phase pipeline so a tick with many
    due reminders doesn't block for ``count × telegram_rtt``.

      1. Build phase — single DB read + per-row job materialisation.
         No outbound calls; reminders that can't be sent (no lead,
         no user) are marked ``sent=True`` immediately because the
         retry would never succeed.
      2. Send phase — ``asyncio.gather`` the prepared messages
         through ``_send_message_classified`` with a bounded
         semaphore. Each send is pure I/O against ``bot`` and does
         not touch the SQLAlchemy session — that's what makes the
         parallelism safe (AsyncSession is not concurrency-safe).
      3. Apply phase — single sequential pass over the outcomes
         that updates the session: mark ``sent=True`` for the
         "sent" cases, accumulate distinct ``user_id`` values whose
         users blocked the bot, then issue one bulk
         ``UPDATE Tracker SET active=False WHERE user_id IN (...)``
         per blocked user. Single ``session.commit()`` at the end
         so a partial failure doesn't half-commit the tick.

    Wall-clock for 50 reminders × ~50 ms RTT each: 2.5 s sequential
    → ~500 ms with concurrency=5.
    """
    async with session_factory() as session:
        now = datetime.now(UTC)
        result = await session.execute(
            select(LeadReminder)
            .where(LeadReminder.remind_at <= now, LeadReminder.sent.is_(False))
            .options(joinedload(LeadReminder.lead).joinedload(LeadItem.user))
        )
        due_reminders = list(result.scalars())
        if not due_reminders:
            logger.debug("No due reminders to send")
            return

        logger.info("Processing %d due reminder(s)", len(due_reminders))

        # ── Phase 1: build job list ──────────────────────────────────
        jobs: list[tuple[LeadReminder, int, str, object | None]] = []
        for reminder in due_reminders:
            lead = reminder.lead
            if lead is None:
                # Stale reminder for a deleted lead — no point retrying.
                reminder.sent = True
                continue

            telegram_user_id = lead.user.telegram_user_id if lead.user else None
            if telegram_user_id is None:
                reminder.sent = True
                continue

            lead_title = (lead.title or "Без названия").replace("\n", " ")[:120]
            msg_text = reminder.message or "Проверьте сделку"
            lines = [
                f"⏰ Напоминание: {msg_text}",
                f"📌 {lead_title}",
            ]
            if lead.link:
                lines.append(lead.link)

            keyboard = lead_reminder_keyboard(settings.mini_app_url)
            jobs.append((reminder, telegram_user_id, "\n".join(lines), keyboard))

        # ── Phase 2: bounded-concurrency parallel send ───────────────
        sem = asyncio.Semaphore(_REMINDER_SEND_CONCURRENCY)

        async def _bounded_send(
            tg_user_id: int,
            text: str,
            kb: object | None,
        ) -> str:
            async with sem:
                return await _send_message_classified(
                    bot, tg_user_id, text, reply_markup=kb,
                )

        outcomes: list[str] = await asyncio.gather(
            *[_bounded_send(uid, text, kb) for _, uid, text, kb in jobs],
            return_exceptions=False,
        )

        # ── Phase 3: apply outcomes back to the session ──────────────
        sent_count = 0
        blocked_user_ids: set[int] = set()
        for (reminder, _uid, _text, _kb), outcome in zip(jobs, outcomes, strict=True):
            if outcome == "sent":
                reminder.sent = True
                sent_count += 1
            elif outcome == "blocked":
                # Don't mark ``sent`` so a future cycle (after the
                # user re-enables the bot) can retry naturally.
                if reminder.user_id is not None:
                    blocked_user_ids.add(reminder.user_id)
            elif outcome == "retry":
                _queue_notification_dlq(
                    session,
                    telegram_user_id=_uid,
                    message=_text,
                    source="reminder",
                    internal_user_id=reminder.user_id,
                )

        if blocked_user_ids:
            # Single bulk update per tick instead of one UPDATE per
            # blocked user — same semantic as the per-call deactivation
            # in ``notify_user``, just batched.
            await session.execute(
                update(Tracker)
                .where(
                    Tracker.user_id.in_(blocked_user_ids),
                    Tracker.active.is_(True),
                )
                .values(active=False)
            )

        await session.commit()
        logger.info(
            "Reminders sent: %d / %d (blocked users: %d)",
            sent_count, len(due_reminders), len(blocked_user_ids),
        )


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
    # Check due reminders every 15 minutes
    scheduler.add_job(
        check_reminders,
        trigger="interval",
        minutes=15,
        kwargs={"bot": bot, "session_factory": session_factory, "settings": settings},
        id="reminder-check",
        replace_existing=True,
    )
    # OPUS-2: pump the Telegram DLQ every minute so transient
    # failures (Telegram API blip, single user retry-after) get
    # a real second chance instead of waiting for the daily
    # cleanup to wipe them.
    scheduler.add_job(
        retry_telegram_notification_dlq,
        trigger="interval",
        minutes=_DLQ_RETRY_TICK_MINUTES,
        kwargs={"bot": bot, "session_factory": session_factory},
        id="dlq-retry",
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
            await cleanup_old_snapshots(session, days=90)
            # DB-H3: previously the cleanup function existed in
            # 20260510_0004 as a SQL function but nothing actually
            # called it, so ai_audit_log kept growing forever. We
            # now run the same DELETE here every night so retention
            # actually happens. 365 days matches the
            # Law-99-З review window we picked when adding the
            # function.
            await cleanup_ai_audit_log(session, days=365)
            await cleanup_telegram_notification_dlq(session, days=30)
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
    """Auto-remove watchlist items (lead_items with status='watching')
    that have been missing for longer than the threshold."""
    cutoff = datetime.now(UTC) - timedelta(days=days)
    result = await session.execute(
        delete(LeadItem).where(
            LeadItem.status == "watching",
            LeadItem.market_status == "missing",
            LeadItem.missing_since_at.isnot(None),
            LeadItem.missing_since_at < cutoff,
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


async def cleanup_old_snapshots(session: AsyncSession, days: int = 90) -> int:
    """Delete query snapshots older than specified days to prevent unbounded growth."""
    cutoff = datetime.now(UTC) - timedelta(days=days)
    result = await session.execute(
        delete(QuerySnapshot).where(QuerySnapshot.snapshot_at < cutoff)
    )
    deleted_count = result.rowcount
    if deleted_count > 0:
        logger.info(
            "Cleaned up %d old query snapshots (older than %d days)", deleted_count, days
        )
    return deleted_count


async def cleanup_ai_audit_log(session: AsyncSession, days: int = 365) -> int:
    """Delete AI audit-log rows older than ``days`` (default 365).

    Mirrors the SQL ``clean_ai_audit_log`` function added in
    20260510_0004 so we can keep ORM-level visibility (rowcount log,
    transaction integration) without depending on pg_cron or an out-
    of-band scheduler. Runs nightly from ``run_cleanup``.
    """
    cutoff = datetime.now(UTC) - timedelta(days=days)
    result = await session.execute(
        delete(AIAuditLog).where(AIAuditLog.created_at < cutoff)
    )
    deleted_count = result.rowcount
    if deleted_count > 0:
        logger.info(
            "Cleaned up %d AI audit log rows (older than %d days)",
            deleted_count,
            days,
        )
    return deleted_count


async def cleanup_telegram_notification_dlq(session: AsyncSession, days: int = 30) -> int:
    """Delete old Telegram notification failure payloads.

    OPUS-2: also drops rows that already exhausted ``_DLQ_MAX_RETRIES``
    so a permanently-failing recipient doesn't keep them eligible
    for the pump query forever.
    """
    cutoff = datetime.now(UTC) - timedelta(days=days)
    result = await session.execute(
        delete(TelegramNotificationDLQ).where(
            (TelegramNotificationDLQ.created_at < cutoff)
            | (TelegramNotificationDLQ.retry_count >= _DLQ_MAX_RETRIES)
        )
    )
    deleted_count = result.rowcount
    if deleted_count > 0:
        logger.info(
            "Cleaned up %d Telegram notification DLQ rows "
            "(older than %d days or retry-exhausted)",
            deleted_count,
            days,
        )
    return deleted_count


# OPUS-2: how aggressive the retry pump is. 5 attempts × exponential
# backoff (2, 4, 8, 16, 32 minutes after the failures) covers a ~1h
# Telegram outage without spamming the API on every minute. Tunable
# via constants only — no env knob until ops actually need it.
_DLQ_RETRY_TICK_MINUTES = 1
_DLQ_PUMP_BATCH_LIMIT = 50
_DLQ_MAX_RETRIES = 5
_DLQ_BACKOFF_BASE_MINUTES = 2


def _dlq_next_retry_after(retry_count: int) -> datetime:
    """Exponential backoff anchored on the *next* attempt count."""
    minutes = _DLQ_BACKOFF_BASE_MINUTES * (2 ** max(0, retry_count - 1))
    return datetime.now(UTC) + timedelta(minutes=minutes)


async def retry_telegram_notification_dlq(
    bot: Bot,
    session_factory: async_sessionmaker[AsyncSession],
) -> int:
    """OPUS-2: pump pending DLQ rows back through Telegram.

    The DLQ used to be a write-only graveyard: rows landed there
    when ``_send_message_classified`` reported ``retry``, then sat
    until the daily cleanup wiped them 30 days later. Telegram
    blips of even ~10 minutes lost the affected notifications.

    This pump reads up to ``_DLQ_PUMP_BATCH_LIMIT`` rows whose
    ``next_retry_at`` is due (or NULL — fresh failures), groups by
    ``telegram_user_id`` so one user's burst doesn't preempt
    everyone else, sends each batch serially through the same
    classifier, and applies the outcome:

    * ``sent``    → DELETE row.
    * ``blocked`` → DELETE every DLQ row for that user_id +
                    deactivate their trackers in bulk.
    * ``retry``   → ``retry_count += 1``,
                    ``next_retry_at = now + 2^(n-1) * 2 min``.

    Returns the number of rows that successfully sent.
    """
    async with session_factory() as session:
        now = datetime.now(UTC)
        result = await session.execute(
            select(TelegramNotificationDLQ)
            .where(
                TelegramNotificationDLQ.retry_count < _DLQ_MAX_RETRIES,
                (TelegramNotificationDLQ.next_retry_at.is_(None))
                | (TelegramNotificationDLQ.next_retry_at <= now),
            )
            .order_by(TelegramNotificationDLQ.created_at.asc())
            .limit(_DLQ_PUMP_BATCH_LIMIT)
        )
        rows = list(result.scalars())
        if not rows:
            return 0
        # Snapshot the bytes we need before we leave the session — the
        # async send loop runs without a session open so we don't block
        # connections on outbound I/O.
        snapshots: list[tuple[int, int, int | None, str, int]] = [
            (row.id, row.telegram_user_id, row.user_id, row.message, row.retry_count)
            for row in rows
        ]

    by_user: dict[int, list[tuple[int, int | None, str, int]]] = defaultdict(list)
    for row_id, tg_id, user_id, message, retry_count in snapshots:
        by_user[tg_id].append((row_id, user_id, message, retry_count))

    sem = asyncio.Semaphore(_TRACKER_USER_CONCURRENCY)

    async def _drain(
        tg_user_id: int,
        items: list[tuple[int, int | None, str, int]],
    ) -> tuple[int, list[int], list[tuple[int, int]], int | None]:
        """Send every queued message for one user, serially.

        Returns a tuple of:
        * count of successful sends,
        * row ids to delete (sent OR blocked),
        * (row_id, new_retry_count) tuples to bump for retry,
        * blocked user_id (if encountered) for bulk deactivation.
        """
        sent_count = 0
        delete_ids: list[int] = []
        bump_ids: list[tuple[int, int]] = []
        blocked_internal_id: int | None = None
        async with sem:
            for row_id, user_id, message, retry_count in items:
                outcome = await _send_message_classified(
                    bot, tg_user_id, message,
                )
                if outcome == "sent":
                    sent_count += 1
                    delete_ids.append(row_id)
                elif outcome == "blocked":
                    # User no longer reachable — pointless to keep
                    # any of their queued rows. Mark THIS one and
                    # let the caller wipe siblings via tg_user_id.
                    blocked_internal_id = user_id
                    delete_ids.append(row_id)
                    return sent_count, delete_ids, bump_ids, blocked_internal_id
                else:
                    bump_ids.append((row_id, retry_count + 1))
        return sent_count, delete_ids, bump_ids, blocked_internal_id

    results = await asyncio.gather(
        *[_drain(tg_id, items) for tg_id, items in by_user.items()]
    )

    total_sent = sum(r[0] for r in results)
    delete_ids: list[int] = []
    bump_ids: list[tuple[int, int]] = []
    blocked_user_ids: set[int] = set()
    blocked_internal_ids: set[int] = set()
    for tg_id, (_sent_count, drained_delete, drained_bump, blocked_uid) in zip(
        by_user.keys(), results, strict=True,
    ):
        delete_ids.extend(drained_delete)
        bump_ids.extend(drained_bump)
        if blocked_uid is not None:
            blocked_user_ids.add(tg_id)
            blocked_internal_ids.add(blocked_uid)

    if not (delete_ids or bump_ids or blocked_user_ids):
        return total_sent

    async with session_factory() as session:
        # Wipe every DLQ row for blocked users; covers rows we
        # didn't pull this tick too.
        if blocked_user_ids:
            await session.execute(
                delete(TelegramNotificationDLQ).where(
                    TelegramNotificationDLQ.telegram_user_id.in_(blocked_user_ids),
                )
            )
            await session.execute(
                update(Tracker)
                .where(
                    Tracker.user_id.in_(blocked_internal_ids),
                    Tracker.active.is_(True),
                )
                .values(active=False)
            )
            logger.info(
                "DLQ pump: dropped trackers for %d blocked user(s)",
                len(blocked_internal_ids),
            )
        if delete_ids:
            await session.execute(
                delete(TelegramNotificationDLQ).where(
                    TelegramNotificationDLQ.id.in_(delete_ids)
                )
            )
        for row_id, new_retry_count in bump_ids:
            await session.execute(
                update(TelegramNotificationDLQ)
                .where(TelegramNotificationDLQ.id == row_id)
                .values(
                    retry_count=new_retry_count,
                    next_retry_at=_dlq_next_retry_after(new_retry_count),
                )
            )
        await session.commit()
    if total_sent or bump_ids:
        logger.info(
            "DLQ pump: sent=%d retried=%d blocked_users=%d",
            total_sent, len(bump_ids), len(blocked_user_ids),
        )
    return total_sent


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
    import os

    from api.healthcheck import start_health_server, stop_health_server

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

    # INF-H6: HTTP health server so Docker can hit /health/ready
    # directly. The closure captures `engine` by name so reconnects
    # below (which reassign `engine`) automatically update the probe
    # target without us re-registering handlers.
    async def _scheduler_readiness() -> bool:
        try:
            return await check_db_health(engine)
        except Exception as exc:
            logger.warning("scheduler readiness probe failed: %s", exc)
            return False

    health_port = int(os.environ.get("SCHEDULER_HEALTH_PORT", "8002"))
    health_runner = await start_health_server(
        port=health_port, readiness=_scheduler_readiness
    )

    # PERF-M4: graceful shutdown on SIGTERM/SIGINT. Python's default
    # SIGTERM disposition kills the process immediately, which used to
    # interrupt mid-cycle tracker checks — the affected
    # ``session.commit()`` would be torn down, leaving half of a
    # tick's ``tracker_events`` rows in the DB and the other half
    # silently dropped. Now SIGTERM sets ``shutdown_event``, the main
    # loop exits cleanly, and the ``finally`` block calls
    # ``scheduler.shutdown(wait=True)`` which lets the in-flight job
    # finish its transaction. Docker's default 10s grace before
    # SIGKILL is enough for a typical tracker tick to commit; if it
    # isn't, the SIGKILL is still the safety net.
    shutdown_event = asyncio.Event()

    def _request_shutdown(signum: int) -> None:
        signal_name = signal.Signals(signum).name if signum else "unknown"
        logger.info(
            "Received %s, beginning graceful shutdown", signal_name,
        )
        shutdown_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        # add_signal_handler isn't supported on Windows and may fail
        # if we're not the main thread (some test harnesses). Fall
        # back silently to Python's default disposition in that case.
        try:
            loop.add_signal_handler(sig, _request_shutdown, sig)
        except (NotImplementedError, RuntimeError, ValueError):
            logger.debug("Could not install handler for %s", sig.name)

    scheduler.start()
    try:
        while not shutdown_event.is_set():
            # Periodic health check every 5 minutes. The
            # ``wait_for(shutdown_event.wait(), timeout=300)`` idiom
            # interrupts the sleep as soon as a signal arrives so we
            # don't sit in an unkillable wait state for up to 5
            # minutes after SIGTERM.
            if not await check_db_health(engine):
                logger.warning("Database connection lost. Attempting reconnect...")
                scheduler.shutdown(wait=False)
                await engine.dispose()
                engine = get_engine()
                session_factory = get_session_factory(engine)
                if not await check_db_health(engine):
                    logger.error("Reconnection failed. Exiting.")
                    break
                logger.info("Database reconnected successfully. Restarting scheduler...")
                scheduler = create_scheduler(bot, session_factory, settings)
                scheduler.start()
            try:
                await asyncio.wait_for(shutdown_event.wait(), timeout=300)
            except TimeoutError:
                # 5 minutes elapsed without a shutdown signal — fall
                # through to the next health-check iteration.
                continue
    finally:
        # PERF-M4: wait=True lets the currently-running tracker tick
        # commit its transaction before the engine is disposed.
        # Without this, the engine would be torn down mid-flight and
        # the session's connection would raise on commit.
        scheduler.shutdown(wait=True)
        await stop_health_server(health_runner)
        await engine.dispose()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
