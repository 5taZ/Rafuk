from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api.config import Settings
from api.middleware.telegram_auth import TelegramInitData
from api.models import AccountStatus, User
from api.schemas import (
    ProfileLimitsRead,
    ProfilePermissionsRead,
    ProfileRead,
    ProfileStatusRead,
    ProfileUserRead,
    QuotaBucketRead,
)
from api.services.cache import CacheBackend

BARE_SEARCH_STATUS_CODE = "bare_search"
QUOTA_BUCKET_AI = "ai"
QUOTA_BUCKET_ASSISTANT = "assistant"
QUOTA_BUCKETS = (QUOTA_BUCKET_AI, QUOTA_BUCKET_ASSISTANT)
_MINSK_TZ = ZoneInfo("Europe/Minsk")
_QUOTA_TTL_MARGIN = timedelta(hours=48)


@dataclass(frozen=True, slots=True)
class QuotaWindow:
    key_date: str
    resets_at: datetime
    ttl_seconds: int


def admin_telegram_user_ids(settings: Settings) -> frozenset[int]:
    raw = str(getattr(settings, "admin_telegram_user_ids", "") or "")
    ids: set[int] = set()
    for chunk in raw.replace(";", ",").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            value = int(chunk)
        except ValueError:
            continue
        if value > 0:
            ids.add(value)
    return frozenset(ids)


def is_admin_user(telegram_user_id: int, settings: Settings) -> bool:
    return int(telegram_user_id) in admin_telegram_user_ids(settings)


def quota_window(now: datetime | None = None) -> QuotaWindow:
    current = now or datetime.now(_MINSK_TZ)
    if current.tzinfo is None or current.utcoffset() is None:
        current = current.replace(tzinfo=_MINSK_TZ)
    current = current.astimezone(_MINSK_TZ)
    next_midnight = (current + timedelta(days=1)).replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )
    ttl = max(1, int((next_midnight + _QUOTA_TTL_MARGIN - current).total_seconds()))
    return QuotaWindow(
        key_date=current.date().isoformat(),
        resets_at=next_midnight,
        ttl_seconds=ttl,
    )


def quota_key(bucket: str, telegram_user_id: int, *, now: datetime | None = None) -> str:
    _validate_quota_bucket(bucket)
    window = quota_window(now)
    return f"quota:{bucket}:{int(telegram_user_id)}:{window.key_date}"


async def quota_snapshot(
    cache: CacheBackend,
    *,
    bucket: str,
    telegram_user_id: int,
    limit: int,
    now: datetime | None = None,
) -> QuotaBucketRead:
    _validate_quota_bucket(bucket)
    window = quota_window(now)
    raw = await cache.get(f"quota:{bucket}:{int(telegram_user_id)}:{window.key_date}")
    try:
        raw_used = max(0, int(raw or 0))
    except (TypeError, ValueError):
        raw_used = 0
    normalized_limit = max(0, int(limit))
    return QuotaBucketRead(
        used=min(raw_used, normalized_limit),
        limit=normalized_limit,
        remaining=max(0, normalized_limit - raw_used),
        resets_at=window.resets_at,
    )


async def consume_quota(
    cache: CacheBackend,
    *,
    bucket: str,
    telegram_user_id: int,
    limit: int,
    now: datetime | None = None,
) -> QuotaBucketRead:
    _validate_quota_bucket(bucket)
    normalized_limit = max(0, int(limit))
    if normalized_limit <= 0:
        snapshot = await quota_snapshot(
            cache,
            bucket=bucket,
            telegram_user_id=telegram_user_id,
            limit=normalized_limit,
            now=now,
        )
        raise HTTPException(
            status_code=403,
            detail={
                "error": "premium_required",
                "bucket": bucket,
                "used": snapshot.used,
                "limit": snapshot.limit,
                "resets_at": snapshot.resets_at.isoformat(),
                "message": _quota_error_message(bucket, premium_required=True),
            },
        )

    window = quota_window(now)
    key = f"quota:{bucket}:{int(telegram_user_id)}:{window.key_date}"
    raw_used = max(0, int(await cache.incr(key, ttl=window.ttl_seconds)))
    snapshot = QuotaBucketRead(
        used=min(raw_used, normalized_limit),
        limit=normalized_limit,
        remaining=max(0, normalized_limit - raw_used),
        resets_at=window.resets_at,
    )
    if raw_used > normalized_limit:
        raise HTTPException(
            status_code=429,
            detail={
                "error": "quota_exceeded",
                "bucket": bucket,
                "used": snapshot.used,
                "limit": snapshot.limit,
                "resets_at": snapshot.resets_at.isoformat(),
                "message": _quota_error_message(bucket, premium_required=False),
            },
        )
    return snapshot


async def get_status_by_code(
    session: AsyncSession,
    code: str | None,
) -> AccountStatus:
    status = None
    if code:
        status = await session.get(AccountStatus, code)
    if status is None and code != BARE_SEARCH_STATUS_CODE:
        status = await session.get(AccountStatus, BARE_SEARCH_STATUS_CODE)
    if status is None:
        raise RuntimeError("account_statuses seed is missing bare_search")
    return status


async def get_effective_status(
    session: AsyncSession,
    user: User,
    now: datetime | None = None,
) -> AccountStatus:
    current = now or datetime.now(UTC)
    expires_at = user.status_expires_at
    if expires_at is not None:
        if expires_at.tzinfo is None or expires_at.utcoffset() is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        if expires_at <= current:
            return await get_status_by_code(session, BARE_SEARCH_STATUS_CODE)
    return await get_status_by_code(session, user.account_status_code)


async def get_or_create_profile_user(
    session: AsyncSession,
    telegram_user: TelegramInitData,
) -> User:
    now = datetime.now(UTC)
    first_name, username = _extract_telegram_names(telegram_user)
    user = (
        await session.execute(select(User).where(User.telegram_user_id == telegram_user.user_id))
    ).scalar_one_or_none()
    if user is None:
        user = User(
            telegram_user_id=telegram_user.user_id,
            first_name=first_name[:128],
            username=username[:128] if username else None,
            last_seen_at=now,
        )
        session.add(user)
        try:
            await session.flush()
        except IntegrityError:
            await session.rollback()
            user = (
                await session.execute(
                    select(User).where(User.telegram_user_id == telegram_user.user_id)
                )
            ).scalar_one()
    changed = False
    if first_name and user.first_name != first_name[:128]:
        user.first_name = first_name[:128]
        changed = True
    if username and user.username != username[:128]:
        user.username = username[:128]
        changed = True
    if user.last_seen_at != now:
        user.last_seen_at = now
        changed = True
    if changed:
        await session.flush()
    await session.refresh(user)
    return user


async def build_profile(
    *,
    session: AsyncSession,
    cache: CacheBackend,
    telegram_user: TelegramInitData,
    settings: Settings,
    now: datetime | None = None,
) -> ProfileRead:
    user = await get_or_create_profile_user(session, telegram_user)
    status = await get_effective_status(session, user, now)
    granted_at, expires_at = effective_status_metadata(user, status)
    limits = ProfileLimitsRead(
        ai=await quota_snapshot(
            cache,
            bucket=QUOTA_BUCKET_AI,
            telegram_user_id=user.telegram_user_id,
            limit=status.ai_daily_limit,
            now=now,
        ),
        assistant=await quota_snapshot(
            cache,
            bucket=QUOTA_BUCKET_ASSISTANT,
            telegram_user_id=user.telegram_user_id,
            limit=status.assistant_daily_limit,
            now=now,
        ),
    )
    return ProfileRead(
        user=ProfileUserRead.model_validate(user),
        status=ProfileStatusRead(
            code=status.code,
            display_name=status.display_name,
            tagline=status.tagline,
            accent=status.accent,
            granted_at=granted_at,
            expires_at=expires_at,
        ),
        limits=limits,
        permissions=ProfilePermissionsRead(
            can_use_ai=status.ai_daily_limit > 0,
            can_use_assistant=status.assistant_daily_limit > 0,
            is_admin=is_admin_user(user.telegram_user_id, settings),
        ),
    )


def effective_status_metadata(
    user: User,
    status: AccountStatus,
) -> tuple[datetime | None, datetime | None]:
    if status.code != user.account_status_code:
        return None, None
    return user.status_granted_at, user.status_expires_at


def _extract_telegram_names(telegram_user: TelegramInitData) -> tuple[str, str | None]:
    first_name = telegram_user.first_name or ""
    username = None
    raw_user = telegram_user.raw.get("user") if isinstance(telegram_user.raw, dict) else None
    if raw_user:
        try:
            payload = json.loads(raw_user)
        except json.JSONDecodeError:
            payload = {}
        if isinstance(payload, dict):
            first_name = str(payload.get("first_name") or first_name)
            raw_username = payload.get("username")
            username = str(raw_username) if raw_username else None
    return first_name, username


def _validate_quota_bucket(bucket: str) -> None:
    if bucket not in QUOTA_BUCKETS:
        raise ValueError(f"unknown quota bucket: {bucket}")


def _quota_error_message(bucket: str, *, premium_required: bool) -> str:
    if premium_required:
        return (
            "AI-помощник продавца доступен со статуса Скаут Барахолки"
            if bucket == QUOTA_BUCKET_ASSISTANT
            else "AI доступен со статуса Скаут Барахолки"
        )
    return (
        "Лимит AI-помощника продавца на сегодня закончился"
        if bucket == QUOTA_BUCKET_ASSISTANT
        else "Лимит AI-запросов на сегодня закончился"
    )
