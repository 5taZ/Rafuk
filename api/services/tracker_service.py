"""Tracker creation, deactivation and reactivation shared by the API and the bot."""

from __future__ import annotations

import math
import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import select

from api.config import get_settings
from api.models import Tracker
from api.services.aggregator import build_query_key, normalize_search_text
from api.services.users import MAX_ACTIVE_TRACKERS_PER_USER, ensure_user, lock_user
from api.validators import MAX_QUERY_LENGTH

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

# Integer primary keys are int4 on Postgres; larger path/command ids can never match.
MAX_DB_ID = 2_147_483_647
# Interval stored when the caller does not ask for one (matches the column default).
DEFAULT_TRACKER_INTERVAL_MIN = 15
# Mirrors scheduler.collector.DUE_SLACK_MINUTES (the API never imports the scheduler).
DUE_SLACK_MINUTES = 1
_DB_ID_PATTERN = re.compile(r"#?([0-9]{1,10})")


class TrackerServiceError(Exception):
    """Base class for tracker errors; ``message`` is shown to the user (Russian)."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class TrackerValidationError(TrackerServiceError):
    """The tracker query or configuration is invalid."""


class DuplicateTrackerError(TrackerServiceError):
    """An active tracker with the same query, mode and filters already exists."""

    def __init__(self, tracker: Tracker) -> None:
        super().__init__(f"Такой трекер уже есть: #{tracker.id} «{tracker.query}».")
        self.tracker = tracker


class TrackerQuotaExceededError(TrackerServiceError):
    """The user already has the maximum number of active trackers."""

    def __init__(self, limit: int) -> None:
        super().__init__(
            f"Достигнут лимит активных трекеров ({limit}). "
            "Удалите ненужные трекеры, чтобы добавить новый."
        )
        self.limit = limit


def parse_db_id(raw: str | None) -> int | None:
    """Parse a user-supplied id (``12`` or ``#12``): ASCII digits within the int4 range."""
    match = _DB_ID_PATTERN.fullmatch((raw or "").strip())
    if match is None:
        return None
    value = int(match.group(1))
    if not 1 <= value <= MAX_DB_ID:
        return None
    return value


def normalize_tracker_query(raw_query: str | None) -> str:
    """Strip the query and check that it is non-empty and fits ``Tracker.query``."""
    query = (raw_query or "").strip()
    if not query or not normalize_search_text(query):
        raise TrackerValidationError("Запрос не должен быть пустым.")
    if len(query) > MAX_QUERY_LENGTH:
        raise TrackerValidationError(
            f"Запрос слишком длинный: максимум {MAX_QUERY_LENGTH} символов."
        )
    return query


def _clean_text(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


def _normalized_keyword(value: str | None) -> str | None:
    if not value:
        return None
    return normalize_search_text(value) or None


def _float_key(value: float | None) -> float | None:
    return None if value is None else round(float(value), 4)


def _tracker_identity(
    *,
    query: str,
    strict_mode: bool,
    min_discount_percent: float | None,
    max_price_byn: float | None,
    seller_type: str | None,
    condition: str | None,
    region_name: str | None,
    config_keyword: str | None,
    exclude_duplicates: bool,
) -> tuple[object, ...]:
    return (
        build_query_key(query, strict_mode),
        bool(strict_mode),
        _float_key(min_discount_percent),
        _float_key(max_price_byn),
        _clean_text(seller_type),
        _clean_text(condition),
        _clean_text(region_name),
        _normalized_keyword(config_keyword),
        bool(exclude_duplicates),
    )


def tracker_identity(tracker: Tracker) -> tuple[object, ...]:
    """Key that identifies trackers producing the same alerts for one user."""
    return _tracker_identity(
        query=tracker.query,
        strict_mode=tracker.strict_mode,
        min_discount_percent=tracker.min_discount_percent,
        max_price_byn=tracker.max_price_byn,
        seller_type=tracker.seller_type,
        condition=tracker.condition,
        region_name=tracker.region_name,
        config_keyword=tracker.config_keyword,
        exclude_duplicates=tracker.exclude_duplicates,
    )


def effective_interval_min(requested: int | None) -> int:
    """Interval a tracker is really checked at.

    The scheduler runs every ``ALERT_CHECK_INTERVAL`` minutes, so a shorter interval can
    never be honoured; storing the real cadence keeps the Mini App and /tracks truthful.
    """
    value = DEFAULT_TRACKER_INTERVAL_MIN if requested is None else int(requested)
    return max(value, get_settings().alert_check_interval)


def real_check_interval_min(interval_min: int | None) -> int:
    """How often the scheduler really checks a tracker with this stored interval.

    The scheduler runs every ``ALERT_CHECK_INTERVAL`` minutes and checks a tracker once its
    interval minus ``DUE_SLACK_MINUTES`` has passed (see ``scheduler.collector.tracker_is_due``),
    so the real cadence is the first multiple of the global interval that reaches it: with
    a 30-minute check, 45 becomes 60. This is what the Mini App shows.
    """
    check_interval = max(1, int(get_settings().alert_check_interval))
    if interval_min is None or interval_min <= check_interval:
        return check_interval
    runs = max(1, math.ceil((interval_min - DUE_SLACK_MINUTES) / check_interval))
    return runs * check_interval


async def list_active_trackers(session: AsyncSession, user_id: int) -> list[Tracker]:
    result = await session.execute(
        select(Tracker)
        .where(Tracker.user_id == user_id, Tracker.active.is_(True))
        .order_by(Tracker.created_at.desc(), Tracker.id.desc())
    )
    return list(result.scalars())


async def create_tracker(
    session: AsyncSession,
    *,
    user_id: int,
    query: str | None,
    strict_mode: bool = False,
    interval_min: int | None = None,
    min_discount_percent: float | None = None,
    max_price_byn: float | None = None,
    seller_type: str | None = None,
    condition: str | None = None,
    region_name: str | None = None,
    config_keyword: str | None = None,
    exclude_duplicates: bool = False,
    first_name: str | None = None,
    username: str | None = None,
    max_active: int | None = None,
) -> Tracker:
    """Validate and insert a tracker (flushed, not committed).

    ``config_keyword`` stays ``None`` unless given: the query itself is never used as a
    keyword filter. Raises ``TrackerValidationError``, ``DuplicateTrackerError`` or
    ``TrackerQuotaExceededError``.
    """
    normalized_query = normalize_tracker_query(query)
    values = {
        "query": normalized_query,
        "strict_mode": bool(strict_mode),
        "min_discount_percent": min_discount_percent,
        "max_price_byn": max_price_byn,
        "seller_type": _clean_text(seller_type),
        "condition": _clean_text(condition),
        "region_name": _clean_text(region_name),
        "config_keyword": _clean_text(config_keyword),
        "exclude_duplicates": bool(exclude_duplicates),
    }

    # Upsert and lock the user's row before the checks, so concurrent creations of one user
    # (a double tap) run one after another and the second one sees the first tracker.
    await ensure_user(session, user_id, first_name=first_name, username=username)
    await lock_user(session, user_id)

    active_trackers = await list_active_trackers(session, user_id)
    identity = _tracker_identity(**values)
    for existing in active_trackers:
        if tracker_identity(existing) == identity:
            raise DuplicateTrackerError(existing)
    limit = MAX_ACTIVE_TRACKERS_PER_USER if max_active is None else max_active
    if len(active_trackers) >= limit:
        raise TrackerQuotaExceededError(limit)

    tracker = Tracker(
        user_id=user_id,
        interval_min=effective_interval_min(interval_min),
        **values,
    )
    session.add(tracker)
    await session.flush()
    return tracker


async def deactivate_tracker(
    session: AsyncSession,
    *,
    user_id: int,
    tracker_id: int,
) -> Tracker | None:
    """Soft-delete the user's active tracker (no commit). Returns ``None`` if not found."""
    if not 1 <= tracker_id <= MAX_DB_ID:
        return None
    tracker = await session.scalar(
        select(Tracker).where(
            Tracker.id == tracker_id,
            Tracker.user_id == user_id,
            Tracker.active.is_(True),
        )
    )
    if tracker is None:
        return None
    tracker.active = False
    tracker.deleted_at = datetime.now(UTC)
    return tracker


async def resume_paused_trackers(
    session: AsyncSession,
    *,
    user_id: int,
    max_active: int | None = None,
) -> list[Tracker]:
    """Reactivate the trackers paused because the user had blocked the bot (no commit).

    The scheduler pauses trackers by clearing ``active`` only; explicit deletions (API,
    /untrack) also set ``deleted_at``, so those are never resumed. Paused trackers that
    duplicate an active one, or would exceed the quota, stay paused. A resumed tracker
    starts with a fresh baseline, so it does not alert on everything missed meanwhile.
    """
    paused = list(
        (
            await session.execute(
                select(Tracker)
                .where(
                    Tracker.user_id == user_id,
                    Tracker.active.is_(False),
                    Tracker.deleted_at.is_(None),
                )
                .order_by(Tracker.id)
            )
        ).scalars()
    )
    if not paused:
        return []

    active_trackers = await list_active_trackers(session, user_id)
    identities = {tracker_identity(tracker) for tracker in active_trackers}
    limit = MAX_ACTIVE_TRACKERS_PER_USER if max_active is None else max_active
    available = limit - len(active_trackers)

    resumed: list[Tracker] = []
    for tracker in paused:
        if len(resumed) >= available:
            break
        identity = tracker_identity(tracker)
        if identity in identities:
            continue
        identities.add(identity)
        tracker.active = True
        tracker.last_checked_at = None
        tracker.interval_min = effective_interval_min(tracker.interval_min)
        resumed.append(tracker)
    if resumed:
        await session.flush()
    return resumed
