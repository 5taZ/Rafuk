from __future__ import annotations

import asyncio
import logging
import math
import signal
from collections import defaultdict
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
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
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
from bot.keyboards import enhanced_alert_keyboard, tracker_alert_keyboard

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

    Returns False if tracker should be deactivated (user blocked/deleted bot
    or chat is no longer reachable). Other transient telegram errors are
    logged but the tracker stays active so the next tick can retry.
    """
    try:
        await bot.send_message(telegram_user_id, message, reply_markup=reply_markup)
        return True
    except (TelegramForbiddenError, TelegramUnauthorizedError, TelegramNotFound):
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
    except TelegramRetryAfter as exc:
        logger.warning(
            "Telegram rate limit hit for user %d, retry_after=%ss",
            telegram_user_id,
            getattr(exc, "retry_after", "?"),
        )
        return True  # Keep tracker active, next tick will retry
    except TelegramAPIError as exc:
        logger.warning(
            "Telegram API error sending to user %d: [%s] %s",
            telegram_user_id,
            type(exc).__name__,
            exc,
        )
        return True  # Keep tracker active for unknown / transient errors


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


def _format_price_byn(value: float | None, price_type: str | None = None) -> str:
    if price_type == "negotiable" or (value is None and price_type != "free"):
        return "договорная"
    if price_type == "free" or value == 0:
        return "бесплатно"
    fval = float(value)
    if fval >= 1000:
        compact = f"{fval / 1000:.2f}".rstrip("0").rstrip(".")
        return f"{compact} тыс. р."
    return f"{round(fval)} р."


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


def _build_threshold_message(
    tracker: Tracker,
    threshold_events: list[TrackerEvent],
) -> str | None:
    """Format a Telegram notification for threshold alert events."""
    if not threshold_events:
        return None

    lines: list[str] = []
    label = f"{tracker.query} [строгий]" if tracker.strict_mode else tracker.query

    price_alerts = [e for e in threshold_events if e.event_type == "price_threshold_alert"]
    discount_alerts = [e for e in threshold_events if e.event_type == "discount_alert"]

    if price_alerts:
        lines.append(f'🎯 ПОРОГ ЦЕНЫ: "{label}"')
        for event in price_alerts[:3]:
            threshold = event.parameters.get("threshold") if event.parameters else None
            threshold_str = f" (порог: {float(threshold):,.0f} BYN)" if threshold else ""
            lines.append(f"💰 {float(event.price_byn):,.0f} BYN{threshold_str}")
            lines.append(f"  {event.title}")
            if event.link:
                lines.append(event.link)
        if len(price_alerts) > 3:
            lines.append(f"  и ещё {len(price_alerts) - 3}")

    if discount_alerts:
        if lines:
            lines.append("")
        lines.append(f'📉 СКИДКА ОТ МЕДИАНЫ: "{label}"')
        for event in discount_alerts[:3]:
            discount_pct = event.delta_byn
            median_byn = event.parameters.get("median_byn") if event.parameters else None
            discount_str = f" (-{float(discount_pct):.0f}% от медианы" if discount_pct else ""
            if median_byn:
                discount_str += f" {float(median_byn):,.0f} BYN"
            discount_str += ")" if discount_str else ""
            lines.append(f"💰 {float(event.price_byn):,.0f} BYN{discount_str}")
            lines.append(f"  {event.title}")
            if event.link:
                lines.append(event.link)
        if len(discount_alerts) > 3:
            lines.append(f"  и ещё {len(discount_alerts) - 3}")

    if not lines:
        return None
    return "\n".join(lines)


def _build_tracker_message(
    query: str,
    strict_mode: bool,
    sync_result: QuerySyncResult,
    trend_signal: TrendReversal | None = None,
    trend_already_sent: bool = False,
) -> str | None:
    lines: list[str] = []
    label = f"{query} [строгий]" if strict_mode else query

    if sync_result.new_listings:
        lines.append(f'Запрос "{label}"')
        lines.append(f"Новые объявления: {len(sync_result.new_listings)}")
        for state in sync_result.new_listings[:3]:
            lines.append(f"• {state.title} - {_format_price_byn(state.last_price_byn, state.price_type)}")
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
            delta_str = f"(-{math.ceil(float(delta))} р.)" if float(delta) >= 0.5 else ""
            lines.append(
                f"• {state.title} - {_format_price_byn(state.last_price_byn, state.price_type)} {delta_str}".rstrip()
            )
            if state.link:
                lines.append(state.link)
        if len(sync_result.price_drops) > 3:
            lines.append(f"• и ещё {len(sync_result.price_drops) - 3}")

    if trend_signal is not None and not trend_already_sent:
        if lines:
            lines.append("")
        if not sync_result.new_listings and not sync_result.price_drops:
            lines.append(f'Запрос "{label}"')
        lines.append(
            f"📈 Цена снова растёт после падения на "
            f"{trend_signal.decline_pct:.1f}%"
        )
        lines.append(
            f"• минимум: {_format_price_byn(trend_signal.low_byn)} "
            f"→ сейчас: {_format_price_byn(trend_signal.today_byn)} "
            f"(+{trend_signal.rebound_pct:.1f}%)"
        )
        lines.append(
            "• выкупайте до повторного роста, если ловили это окно"
        )

    if not lines:
        return None
    return "\n".join(lines)


def _build_new_listing_message(
    state: QueryListingState,
    *,
    median_byn: float | None = None,
    discount_pct: float | None = None,
    liquidity: str | None = None,
) -> str:
    """Format a single new-listing notification with market context.

    Includes median price, discount from median, and a liquidity hint
    so the user can decide at a glance whether the deal is worth pursuing.
    """
    price = _format_price_byn(state.last_price_byn, state.price_type)
    title = (state.title or "Без названия").replace("\n", " ")[:120]

    lines = [f"🔔 НОВЫЙ ЛОТ: {title}"]
    if median_byn is not None and median_byn > 0:
        lines.append(f"💰 {price} (медиана: {_format_price_byn(median_byn)})")
    else:
        lines.append(f"💰 {price}")

    if discount_pct is not None and discount_pct > 0:
        lines.append(f"📉 -{discount_pct:.0f}% от медианы")

    if liquidity:
        lines.append(f"⚡ {liquidity}")

    return "\n".join(lines)


def _build_price_drop_message(
    state: QueryListingState,
    delta: float,
    *,
    median_byn: float | None = None,
    discount_pct: float | None = None,
) -> str:
    """Format a single price-drop notification with market context."""
    price = _format_price_byn(state.last_price_byn, state.price_type)
    title = (state.title or "Без названия").replace("\n", " ")[:120]
    delta_str = f"(-{round(float(delta))} р.)" if round(float(delta)) > 0 else ""

    lines = [f"📉 СНИЖЕНИЕ ЦЕНЫ: {title}"]
    if median_byn is not None and median_byn > 0:
        lines.append(f"💰 {price} {delta_str} (медиана: {_format_price_byn(median_byn)})")
    else:
        lines.append(f"💰 {price} {delta_str}".rstrip())

    if discount_pct is not None and discount_pct > 0:
        lines.append(f"📊 -{discount_pct:.0f}% от медианы")

    return "\n".join(lines)


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
            total_notified = 0
            total_errors = 0

            for (query, strict_mode), query_trackers in trackers_by_query.items():
                # Use a savepoint per query group so that a rollback only
                # discards THIS group's changes — previous groups' flushed
                # data stays intact in the outer transaction.
                async with session.begin_nested():
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
                            "Query %r [strict=%s]: %d ads, %d new, %d price drops",
                            safe_query,
                            strict_mode,
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
                                    (state.ad_id, state.event_type)
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
                                can_notify = await notify_user(
                                    bot,
                                    tracker.user.telegram_user_id,
                                    listing_msg,
                                    session,
                                    internal_user_id=tracker.user_id,
                                    reply_markup=keyboard,
                                )
                                if can_notify:
                                    total_notified += 1
                                else:
                                    break  # User blocked — stop sending

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
                                can_notify = await notify_user(
                                    bot,
                                    tracker.user.telegram_user_id,
                                    drop_msg,
                                    session,
                                    internal_user_id=tracker.user_id,
                                    reply_markup=keyboard,
                                )
                                if can_notify:
                                    total_notified += 1
                                else:
                                    break  # User blocked — stop sending

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
                                    await notify_user(
                                        bot,
                                        tracker.user.telegram_user_id,
                                        trend_msg,
                                        session,
                                        internal_user_id=tracker.user_id,
                                        reply_markup=keyboard,
                                    )

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
                                    can_notify = await notify_user(
                                        bot,
                                        tracker.user.telegram_user_id,
                                        threshold_msg,
                                        session,
                                        internal_user_id=tracker.user_id,
                                        reply_markup=keyboard,
                                    )
                                    if can_notify:
                                        total_notified += 1
                                        logger.info(
                                            "Tracker %d (user %d): threshold alert "
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


async def check_reminders(
    bot: Bot,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    """Send due reminders via bot and mark them as sent."""
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
        sent_count = 0
        for reminder in due_reminders:
            lead = reminder.lead
            if lead is None:
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

            # Build inline keyboard with "Открыть сделку" button
            keyboard_rows: list[list[InlineKeyboardButton]] = []
            if settings.mini_app_url and settings.mini_app_url.startswith("https://"):
                deal_url = f"{settings.mini_app_url}?view=deals"
                keyboard_rows.append([
                    InlineKeyboardButton(
                        text="📌 Открыть сделку",
                        web_app=WebAppInfo(url=deal_url),
                    )
                ])
            keyboard = (
                InlineKeyboardMarkup(inline_keyboard=keyboard_rows)
                if keyboard_rows
                else None
            )

            telegram_user_id = lead.user.telegram_user_id if lead.user else None
            if telegram_user_id is None:
                reminder.sent = True
                continue

            can_notify = await notify_user(
                bot,
                telegram_user_id,
                "\n".join(lines),
                session,
                internal_user_id=reminder.user_id,
                reply_markup=keyboard,
            )
            if can_notify:
                sent_count += 1
                reminder.sent = True
            # If notify_user returned False (user blocked bot), leave
            # reminder.sent = False so a future cycle can retry.

        await session.commit()
        logger.info("Reminders sent: %d / %d", sent_count, len(due_reminders))


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
            # Law-91-Z review window we picked when adding the
            # function.
            await cleanup_ai_audit_log(session, days=365)
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
            except asyncio.TimeoutError:
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
