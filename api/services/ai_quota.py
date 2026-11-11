from __future__ import annotations

import logging
import math
from datetime import UTC, datetime

from fastapi import HTTPException

from api.schemas import AIQuotaRead, QuotaBucketRead
from api.services.cache import CacheBackend, QuotaStoreUnavailableError

logger = logging.getLogger(__name__)


def ai_quota_key(telegram_user_id: int) -> str:
    return f"quota:ai:{int(telegram_user_id)}:rolling-v2"


def _now(value: datetime | None) -> datetime:
    current = value or datetime.now(UTC)
    return current.replace(tzinfo=UTC) if current.tzinfo is None else current


def _read(
    usage: tuple[int, int, float, float, bool], short_limit: int, weekly_limit: int,
    *, available: bool = True,
) -> AIQuotaRead:
    def bucket(used: int, limit: int, reset: float) -> QuotaBucketRead:
        return QuotaBucketRead(
            used=used, limit=limit, remaining=max(0, limit - used),
            resets_at=datetime.fromtimestamp(reset, UTC) if reset else None,
            available=available,
        )

    return AIQuotaRead(
        five_hour=bucket(usage[0], short_limit, usage[2]),
        weekly=bucket(usage[1], weekly_limit, usage[3]),
    )


async def ai_quota_snapshot(
    cache: CacheBackend, *, telegram_user_id: int, short_limit: int, weekly_limit: int,
    now: datetime | None = None,
) -> AIQuotaRead:
    if short_limit <= 0 or weekly_limit <= 0:
        return _read((0, 0, 0, 0, True), short_limit, weekly_limit)
    try:
        usage = await cache.rolling_quota(
            ai_quota_key(telegram_user_id), now=_now(now).timestamp(),
            short_limit=short_limit, weekly_limit=weekly_limit,
        )
    except QuotaStoreUnavailableError:
        return _read((0, 0, 0, 0, False), short_limit, weekly_limit, available=False)
    return _read(usage, short_limit, weekly_limit)


async def consume_ai_quota(
    cache: CacheBackend, *, telegram_user_id: int, short_limit: int, weekly_limit: int,
    request_id: str, now: datetime | None = None,
) -> AIQuotaRead:
    if short_limit <= 0 or weekly_limit <= 0:
        raise HTTPException(status_code=403, detail={
            "error": "premium_required", "bucket": "ai",
            "message": "AI доступен на тарифах Pro, Max и Ultra.",
        })
    current = _now(now)
    try:
        usage = await cache.rolling_quota(
            ai_quota_key(telegram_user_id), now=current.timestamp(),
            short_limit=short_limit, weekly_limit=weekly_limit, request_id=request_id,
        )
    except QuotaStoreUnavailableError as exc:
        raise HTTPException(
            status_code=503, detail="Не удалось проверить AI-лимиты. Попробуйте позже.",
        ) from exc
    snapshot = _read(usage, short_limit, weekly_limit)
    if not usage[4]:
        windows = (("5h", snapshot.five_hour), ("weekly", snapshot.weekly))
        # AI-REVIEW-3.12: a refusal with no full window (a concurrent refund
        # between the store's check and this read) used to max() an empty
        # list and answer 500; it is reported against the 5-hour window.
        blocked = [(w, q) for w, q in windows if q.used >= q.limit] or [windows[0]]
        window, quota = max(blocked, key=lambda item: item[1].resets_at or current)
        retry = max(1, math.ceil((quota.resets_at - current).total_seconds())) if (
            quota.resets_at
        ) else 60
        period = "за 5 часов" if window == "5h" else "за 7 дней"
        raise HTTPException(status_code=429, headers={"Retry-After": str(retry)}, detail={
            "error": "quota_exceeded", "bucket": "ai", "window": window,
            "used": quota.used, "limit": quota.limit,
            "resets_at": quota.resets_at.isoformat() if quota.resets_at else None,
            "message": f"Лимит AI-запросов {period} исчерпан. Дождитесь восстановления лимита.",
        })
    return snapshot


async def refund_ai_quota(
    cache: CacheBackend, *, telegram_user_id: int, request_id: str,
    now: datetime | None = None,
) -> None:
    try:
        await cache.rolling_quota(
            ai_quota_key(telegram_user_id), now=_now(now).timestamp(),
            short_limit=0, weekly_limit=0, refund_id=request_id,
        )
    except QuotaStoreUnavailableError:
        logger.warning("AI quota refund failed: quota store unavailable")
