from __future__ import annotations

import asyncio
import logging
import zlib
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from aiogram import Bot
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramEntityTooLarge,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
)
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
    parse_list_time,
)
from api.services.history_service import (
    QuerySyncResult,
    has_listing_states,
    listing_ad_id,
    load_known_ad_ids,
    mark_query_baseline,
    sync_query_listing_states,
)
from api.services.kufar_client import KufarClient
from api.services.market_signals import duplicate_counts
from api.services.query_pipeline import normalize_ads
from api.services.reseller_tools import matches_tracker_filters
from api.services.tracker_service import rounded_interval_min
from bot.keyboards import tracker_alert_keyboard

logger = logging.getLogger(__name__)

# Kufar paging for one tracker group: follow the cursor while a page brings unknown
# listings or listings newer than the group's previous check, up to this many pages.
PAGE_SIZE = 100
MAX_PAGES_PER_GROUP = 3
# Kufar's list_time and our clock may disagree a little; a page whose oldest listing
# is this close to the previous check still counts as possibly new.
LIST_TIME_SLACK_MINUTES = 5

# Once per window of this length every group pages deeper than its recent listings, so
# price drops of older active listings are noticed too. Listings first seen beyond the
# regular window are recorded silently: they are old, not new.
DEEP_SCAN_INTERVAL_HOURS = 6
DEEP_SCAN_MAX_PAGES = 10

# Telegram allows about 30 messages per second per bot; stay well below it.
SEND_MIN_INTERVAL_SECONDS = 1 / 20
# A longer flood-control wait is not worth blocking the whole run for.
MAX_RETRY_AFTER_SECONDS = 60
# Attempts per alert for flood control and transient network or server errors.
MAX_SEND_ATTEMPTS = 3
SEND_BACKOFF_BASE_SECONDS = 2.0
# After this many alerts in a row were lost to transient errors Telegram is probably
# down: the remaining alerts of the run get one attempt each instead of a backoff.
MAX_CONSECUTIVE_TRANSIENT_FAILURES = 3

SNAPSHOT_RETENTION_DAYS = 120
UNTRACKED_LISTING_STATE_RETENTION_DAYS = 90
TRACKED_LISTING_STATE_RETENTION_DAYS = 365

DB_HEALTH_CHECK_INTERVAL_SECONDS = 300
DB_STARTUP_MAX_BACKOFF_SECONDS = 60.0


async def _sleep(seconds: float) -> None:
    # Separate hook so tests can skip send backoff without patching asyncio.sleep globally.
    await asyncio.sleep(seconds)


def _as_utc(value: datetime) -> datetime:
    # SQLite drops the timezone; the columns always store UTC.
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def tracker_is_due(
    interval_min: int | None,
    last_checked_at: datetime | None,
    *,
    check_interval_min: int,
    now: datetime,
) -> bool:
    """Whether a tracker wants a check in the run that starts at ``now``.

    Trackers whose interval is not longer than the global check interval run every time;
    others run when they were never checked or their rounded interval (see
    ``rounded_interval_min``) has passed since the last check. ``last_checked_at`` holds
    the start of the run that checked the tracker, and half a check interval of slack
    absorbs job jitter, so a late run never shifts the cadence by a whole cycle.
    """
    if interval_min is None or interval_min <= check_interval_min or last_checked_at is None:
        return True
    cadence = rounded_interval_min(interval_min, check_interval_min)
    elapsed = now - _as_utc(last_checked_at)
    return elapsed >= timedelta(minutes=cadence - check_interval_min / 2)


def deep_scan_is_due(
    key: str,
    previous_check: datetime | None,
    now: datetime,
    *,
    interval: timedelta | None = None,
) -> bool:
    """Whether this run of a group should page deeper than its recent listings.

    Time is cut into windows of ``interval`` shifted per key by a stable hash, so the deep
    scans of different groups spread over the window instead of piling up in one run. A
    group scans deeply on its first run in each window. The decision depends only on the
    previous check time, so it survives restarts. A group that was never checked records
    its baseline with the regular paging first.
    """
    if previous_check is None:
        return False
    if interval is None:
        interval = timedelta(hours=DEEP_SCAN_INTERVAL_HOURS)
    seconds = max(int(interval.total_seconds()), 1)
    offset = zlib.crc32(key.encode("utf-8")) % seconds

    def window(moment: datetime) -> int:
        return (int(_as_utc(moment).timestamp()) + offset) // seconds

    return window(now) != window(previous_check)


@dataclass(slots=True)
class TrackerGroup:
    """Active trackers whose queries share one normalized query key."""

    key: str
    query: str
    strict_mode: bool
    tracker_ids: list[int] = field(default_factory=list)
    # (interval_min, last_checked_at) of every tracker in the group.
    schedules: list[tuple[int | None, datetime | None]] = field(default_factory=list)

    def is_due(self, *, check_interval_min: int, now: datetime) -> bool:
        """A group runs when at least one of its trackers is due.

        A running group always processes all of its trackers: they share one listing
        state, so a tracker skipped now would never see the listings synced in this run.
        """
        if not self.schedules:
            return True
        return any(
            tracker_is_due(
                interval_min,
                last_checked_at,
                check_interval_min=check_interval_min,
                now=now,
            )
            for interval_min, last_checked_at in self.schedules
        )

    @property
    def last_checked_at(self) -> datetime | None:
        """Start of the group's previous run: the latest check of its trackers."""
        checks = [_as_utc(checked) for _, checked in self.schedules if checked is not None]
        return max(checks) if checks else None


@dataclass(slots=True)
class FetchedSample:
    ads: list[dict[str, Any]]
    total_results: int
    pages: int
    # Listings first fetched beyond the regular window during a deep scan: recorded
    # without a "new listing" alert, because they are old.
    silent_ad_ids: set[int] = field(default_factory=set)


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
    try:
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
    except Exception:
        # One odd listing must not cost the whole group its alerts.
        logger.warning(
            "Tracker %s: skipping listing %s that the filters could not evaluate",
            tracker.id,
            ad.get("ad_id"),
            exc_info=True,
        )
        return False


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
            lines.append(
                f"• {state.title} - {_format_price_byn(state.last_price_byn)} (-{round(delta)} р.)"
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
                select(
                    Tracker.id,
                    Tracker.query,
                    Tracker.strict_mode,
                    Tracker.interval_min,
                    Tracker.last_checked_at,
                )
                .where(Tracker.active.is_(True))
                .order_by(Tracker.id)
            )
        ).all()

    groups: dict[str, TrackerGroup] = {}
    for tracker_id, query, strict_mode, interval_min, last_checked_at in rows:
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
        group.schedules.append((interval_min, last_checked_at))
    return list(groups.values())


KnownIdsLookup = Callable[[Iterable[int]], Awaitable[set[int]]]


def _page_reaches_since(raw_ads: list[dict[str, Any]], since: datetime | None) -> bool:
    """Whether even the oldest listing of a raw page is newer than the previous check.

    Results are sorted by date, so the next page may then hold listings that appeared
    after the previous check too, even if this page had nothing unknown (for example a
    strict query whose fresh listings are all "Pro" and "Max").
    """
    if since is None:
        return False
    times = [parse_list_time(ad.get("list_time")) for ad in raw_ads]
    parsed = [moment for moment in times if moment is not None]
    if not parsed:
        return False
    return min(parsed) >= since - timedelta(minutes=LIST_TIME_SLACK_MINUTES)


async def _fetch_group_sample(
    client: KufarClient,
    group: TrackerGroup,
    lookup_known: KnownIdsLookup,
    *,
    since: datetime | None = None,
    deep: bool = False,
) -> FetchedSample:
    """Fetch recent listings of a group, following the cursor for a few pages.

    The regular window ends after a page whose listings are all known for this query
    and whose raw listings (before the strict filter) are all older than ``since``, the
    group's previous check, or after ``MAX_PAGES_PER_GROUP`` pages. ``lookup_known``
    answers which of the given ad_ids were seen before, one page at a time.

    A ``deep`` scan keeps paging up to ``DEEP_SCAN_MAX_PAGES`` to refresh the prices of
    older listings. Listings first seen beyond the regular window go to
    ``silent_ad_ids``, and a failing page beyond it ends the scan instead of the group.
    """
    ads: list[dict[str, Any]] = []
    seen_ids: set[int] = set()
    silent_ids: set[int] = set()
    kufar_total: int | None = None
    cursor: str | None = None
    pages = 0
    in_window = True
    max_pages = max(DEEP_SCAN_MAX_PAGES, MAX_PAGES_PER_GROUP) if deep else MAX_PAGES_PER_GROUP

    while pages < max_pages:
        try:
            payload = await client.search(
                query=group.query,
                currency="BYN",
                size=PAGE_SIZE,
                cursor=cursor,
            )
        except Exception:
            if in_window:
                raise
            logger.warning(
                "Deep scan of %s stopped after %d pages", group.key, pages, exc_info=True
            )
            break
        pages += 1
        if kufar_total is None:
            kufar_total = KufarClient.extract_total(payload)
        # Normalized ads have an integer ad_id and well-typed fields, so one malformed
        # listing cannot break the sync, the filters or the alert of the whole group.
        raw_ads = normalize_ads(payload.get("ads") if isinstance(payload, dict) else None)
        page_ads = apply_search_mode(raw_ads, group.query, group.strict_mode)
        page_ids: list[int] = []
        for ad in page_ads:
            ad_id = listing_ad_id(ad)
            if ad_id <= 0 or ad_id in seen_ids:
                continue
            seen_ids.add(ad_id)
            ads.append(ad)
            page_ids.append(ad_id)
        if in_window:
            known = await lookup_known(page_ids) if page_ids else set()
            has_unknown = any(ad_id not in known for ad_id in page_ids)
            in_window = pages < MAX_PAGES_PER_GROUP and (
                has_unknown or _page_reaches_since(raw_ads, since)
            )
        else:
            silent_ids.update(page_ids)
        if not in_window and not deep:
            break
        cursor = KufarClient.extract_next_cursor(payload)
        if not cursor:
            break

    # In strict mode Kufar's total counts unfiltered results.
    if group.strict_mode or kufar_total is None:
        total_results = len(ads)
    else:
        total_results = max(kufar_total, len(ads))
    return FetchedSample(
        ads=ads,
        total_results=total_results,
        pages=pages,
        silent_ad_ids=silent_ids,
    )


async def _process_group(
    group: TrackerGroup,
    *,
    client: KufarClient,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    checked_at: datetime | None = None,
) -> list[OutgoingAlert]:
    """Fetch, sync and persist one group in its own transaction; return alerts to send.

    The transaction is committed before anything is sent, so a failure later in the
    run never rolls back state for which users were already notified.

    A key without any remembered listing (a new query, or a key renamed by a change of
    the query normalization) only records a baseline: every listing would look new.
    ``checked_at`` is the start of the run and becomes the trackers' ``last_checked_at``.
    The scheduler never writes query snapshots: its sample of recent pages is not
    comparable with the full scans behind /price-stats.
    """
    if checked_at is None:
        checked_at = datetime.now(UTC)
    since = group.last_checked_at

    # Short sessions: no transaction stays open while talking to Kufar.
    async with session_factory() as session:
        baseline = not await has_listing_states(session, query=group.key)

    async def lookup_known(ad_ids: Iterable[int]) -> set[int]:
        if baseline:
            return set()
        async with session_factory() as session:
            return await load_known_ad_ids(session, query=group.key, ad_ids=ad_ids)

    deep = not baseline and deep_scan_is_due(group.key, since, checked_at)
    sample = await _fetch_group_sample(client, group, lookup_known, since=since, deep=deep)
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

        sync_result = await sync_query_listing_states(
            session,
            query=group.key,
            ads=sample.ads,
            observed_at=observed_at,
            total_results=sample.total_results,
        )
        if baseline and not sample.ads:
            # Remember that the key was initialized, so the first listing of a query
            # that has no results yet still produces an alert.
            await mark_query_baseline(session, query=group.key, observed_at=observed_at)
        if sample.silent_ad_ids:
            sync_result.new_listings = [
                state
                for state in sync_result.new_listings
                if state.ad_id not in sample.silent_ad_ids
            ]
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
            # The first check of a tracker, or of a key, only records a baseline.
            if not baseline and tracker.last_checked_at is not None:
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
            tracker.last_seen_ad_id = newest_id
            tracker.last_seen_price_byn = newest_price_byn
            tracker.last_checked_at = checked_at

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
        "Checked %s%s: %d trackers, %d listings from %d pages, %d new, %d price drops",
        group.key,
        " (baseline)" if baseline else " (deep scan)" if deep else "",
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
        # Alerts dropped in a row because of transient errors.
        self._transient_failures = 0

    async def _throttle(self) -> None:
        loop = asyncio.get_running_loop()
        if self._last_sent_at is not None:
            wait = self._min_interval - (loop.time() - self._last_sent_at)
            if wait > 0:
                await asyncio.sleep(wait)
        self._last_sent_at = loop.time()

    async def send(self, alert: OutgoingAlert) -> bool:
        """Send one alert; return whether it was delivered.

        Flood control (up to ``MAX_RETRY_AFTER_SECONDS``) and transient network or server
        errors are retried up to ``MAX_SEND_ATTEMPTS`` times with exponential backoff:
        the listing states are already committed, so a dropped alert is not repeated by
        the next run (its events stay visible in the Mini App). While Telegram looks
        down, alerts get a single attempt each so the run is not stalled by backoffs.
        """
        if alert.user_id in self._blocked_users:
            return False
        max_attempts = (
            1
            if self._transient_failures >= MAX_CONSECUTIVE_TRANSIENT_FAILURES
            else max(MAX_SEND_ATTEMPTS, 1)
        )
        for attempt in range(1, max_attempts + 1):
            await self._throttle()
            try:
                await self._bot.send_message(
                    alert.user_id,
                    alert.text,
                    reply_markup=alert.reply_markup,
                )
                self._transient_failures = 0
                return True
            except TelegramRetryAfter as exc:
                if attempt < max_attempts and exc.retry_after <= MAX_RETRY_AFTER_SECONDS:
                    logger.warning(
                        "Telegram flood control for user %s: retrying in %s s",
                        alert.user_id,
                        exc.retry_after,
                    )
                    await _sleep(exc.retry_after)
                    continue
                self._transient_failures += 1
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
            except TelegramEntityTooLarge as exc:
                # A network-error subclass, but resending the same message cannot help.
                logger.warning(
                    "Failed to send alert of tracker %s to user %s: %s",
                    alert.tracker_id,
                    alert.user_id,
                    exc,
                )
                return False
            except (TelegramNetworkError, TelegramServerError) as exc:
                if attempt < max_attempts:
                    delay = SEND_BACKOFF_BASE_SECONDS * 2 ** (attempt - 1)
                    logger.warning(
                        "Transient Telegram error for user %s (attempt %d of %d), "
                        "retrying in %.0f s: %s",
                        alert.user_id,
                        attempt,
                        max_attempts,
                        delay,
                        exc,
                    )
                    await _sleep(delay)
                    continue
                self._transient_failures += 1
                logger.warning(
                    "Dropped alert of tracker %s for user %s after %d attempts: %s",
                    alert.tracker_id,
                    alert.user_id,
                    attempt,
                    exc,
                )
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
    """Check all due trackers, one Kufar search and one transaction per query key.

    A group is skipped when none of its trackers is due (see ``TrackerGroup.is_due``).
    A failing group (Kufar, database or Telegram error) is logged and skipped; the
    other groups are still processed and committed.
    """
    groups = await _load_tracker_groups(session_factory)
    if not groups:
        return

    # Every due check is measured from the start of the run and recorded as the trackers'
    # last_checked_at, so neither a long run nor slow Kufar fetches shift the cadence of
    # the groups processed late in it.
    run_started_at = datetime.now(UTC)
    client = KufarClient(settings)
    sender = AlertSender(bot, session_factory)
    failed = 0
    skipped = 0
    try:
        for group in groups:
            if not group.is_due(
                check_interval_min=settings.alert_check_interval,
                now=run_started_at,
            ):
                skipped += 1
                continue
            try:
                alerts = await _process_group(
                    group,
                    client=client,
                    session_factory=session_factory,
                    settings=settings,
                    checked_at=run_started_at,
                )
            except Exception:
                failed += 1
                logger.exception("Tracker check failed for %s; skipping it this run", group.key)
                continue
            for alert in alerts:
                await sender.send(alert)
    finally:
        await client.aclose()

    if skipped:
        logger.info("Tracker check skipped %d query groups that are not due yet", skipped)
    if failed:
        logger.warning("Tracker check finished: %d of %d query groups failed", failed, len(groups))


def create_scheduler(
    bot: Bot,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    *,
    first_run_at: datetime | None = None,
) -> AsyncIOScheduler:
    """Build the scheduler; the first tracker check runs at ``first_run_at`` (default: now).

    A run that starts late (the scheduler was started after a slow database wait, or the
    event loop was busy) still happens instead of being dropped as misfired, and missed
    runs are coalesced into one.
    """
    scheduler = AsyncIOScheduler(timezone="Europe/Minsk")
    scheduler.add_job(
        check_trackers,
        trigger="interval",
        minutes=settings.alert_check_interval,
        kwargs={"bot": bot, "session_factory": session_factory, "settings": settings},
        id="tracker-check",
        replace_existing=True,
        # Run the first check right after start instead of one interval later.
        next_run_time=first_run_at or datetime.now(UTC),
        misfire_grace_time=None,
        coalesce=True,
        max_instances=1,
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
        misfire_grace_time=None,
        coalesce=True,
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
    result = await session.execute(delete(TrackerEvent).where(TrackerEvent.created_at < cutoff))
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
    result = await session.execute(delete(QuerySnapshot).where(QuerySnapshot.snapshot_at < cutoff))
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
    scheduler: AsyncIOScheduler | None = None

    try:
        await wait_for_database(engine)
        # Created after the wait, so the first check is scheduled for now, not for the
        # moment the process started.
        scheduler = create_scheduler(bot, session_factory, settings)
        scheduler.start()
        logger.info("Scheduler started")
        while True:
            await asyncio.sleep(DB_HEALTH_CHECK_INTERVAL_SECONDS)
            # Jobs survive a temporary outage: pool_pre_ping replaces dead
            # connections, so a failed check is only reported.
            if not await check_db_health(engine):
                logger.warning("Database is unavailable; jobs will fail until it recovers")
    finally:
        if scheduler is not None and scheduler.running:
            scheduler.shutdown(wait=False)
        await engine.dispose()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
