from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException

from api.services.ai_quota import ai_quota_snapshot, consume_ai_quota, refund_ai_quota
from api.services.cache import MemoryCache, RedisCache

START = datetime(2026, 11, 8, 10, tzinfo=UTC)


async def _charge(cache, token: str, *, now=START, short=2, weekly=3):
    return await consume_ai_quota(
        cache, telegram_user_id=42, short_limit=short, weekly_limit=weekly,
        request_id=token, now=now,
    )


async def _snapshot(cache, *, now=START, short=2, weekly=3):
    return await ai_quota_snapshot(
        cache, telegram_user_id=42, short_limit=short, weekly_limit=weekly, now=now,
    )


async def test_five_hour_window_recovers_without_resetting_weekly_usage() -> None:
    cache = MemoryCache()
    await _charge(cache, "first")
    await _charge(cache, "second", now=START + timedelta(minutes=1))
    with pytest.raises(HTTPException) as exc:
        await _charge(cache, "blocked")
    assert exc.value.status_code == 429
    assert exc.value.detail["window"] == "5h"
    assert exc.value.headers["Retry-After"] == "18000"
    recovered = await _charge(cache, "third", now=START + timedelta(hours=5))
    assert recovered.five_hour.used == 2
    assert recovered.weekly.used == 3
    with pytest.raises(HTTPException) as exc:
        await _charge(cache, "blocked-week", now=START + timedelta(hours=6))
    assert exc.value.detail["window"] == "weekly"
    assert exc.value.detail["resets_at"] == (START + timedelta(days=7)).isoformat()
    cleared = await _snapshot(cache, now=START + timedelta(days=7, hours=6))
    assert cleared.five_hour.used == cleared.weekly.used == 0
    assert cleared.weekly.resets_at is None


async def test_parallel_requests_cannot_exceed_either_budget() -> None:
    cache = MemoryCache()
    results = await asyncio.gather(*(
        _charge(cache, f"parallel-{i}", short=3, weekly=10) for i in range(20)
    ), return_exceptions=True)
    assert sum(not isinstance(result, Exception) for result in results) == 3
    snapshot = await _snapshot(cache, short=3, weekly=10)
    assert snapshot.five_hour.used == snapshot.weekly.used == 3


async def test_retry_and_refund_are_idempotent_across_five_hour_boundary() -> None:
    cache = MemoryCache()
    await _charge(cache, "same")
    await _charge(cache, "same")
    assert (await _snapshot(cache)).weekly.used == 1
    later = START + timedelta(hours=6)
    await _charge(cache, "later", now=later)
    await refund_ai_quota(cache, telegram_user_id=42, request_id="same", now=later)
    await refund_ai_quota(cache, telegram_user_id=42, request_id="same", now=later)
    snapshot = await _snapshot(cache, now=later)
    assert snapshot.five_hour.used == snapshot.weekly.used == 1
    await refund_ai_quota(cache, telegram_user_id=42, request_id="later", now=later)
    assert (await _snapshot(cache, now=later)).weekly.used == 0


async def test_lowered_limits_report_when_a_slot_will_actually_be_available() -> None:
    cache = MemoryCache()
    for i in range(4):
        await _charge(cache, str(i), now=START + timedelta(minutes=i), short=5, weekly=10)
    snapshot = await _snapshot(cache, now=START + timedelta(minutes=4), short=2, weekly=10)
    assert snapshot.five_hour.used == 4
    assert snapshot.five_hour.remaining == 0
    assert snapshot.five_hour.resets_at == START + timedelta(hours=5, minutes=2)


async def test_free_never_creates_quota_usage() -> None:
    cache = MemoryCache()
    with pytest.raises(HTTPException) as exc:
        await _charge(cache, "free", short=0, weekly=0)
    assert exc.value.status_code == 403
    assert not cache._storage


async def test_consent_revoke_keeps_usage_but_account_erasure_removes_it() -> None:
    from api.services.ai_privacy import clear_user_ai_data

    cache = MemoryCache()
    await _charge(cache, "retained")
    await clear_user_ai_data(42, cache=cache, keep_account_state=True)
    assert (await _snapshot(cache)).weekly.used == 1
    await clear_user_ai_data(42, cache=cache)
    assert (await _snapshot(cache)).weekly.used == 0


async def test_unavailable_quota_store_denies_ai_and_marks_profile_usage_unknown() -> None:
    cache = RedisCache.from_url("redis://127.0.0.1:1/0")
    try:
        with pytest.raises(HTTPException) as exc:
            await _charge(cache, "outage")
        assert exc.value.status_code == 503
        snapshot = await _snapshot(cache)
        assert snapshot.five_hour.available is False
        assert snapshot.weekly.available is False
    finally:
        await cache.aclose()


async def test_redis_lua_checks_both_windows_and_refunds_atomically(redis_cache) -> None:
    await _charge(redis_cache, "first")
    await _charge(redis_cache, "second")
    with pytest.raises(HTTPException):
        await _charge(redis_cache, "blocked")
    await refund_ai_quota(redis_cache, telegram_user_id=42, request_id="first", now=START)
    await _charge(redis_cache, "replacement")
    snapshot = await _snapshot(redis_cache)
    assert snapshot.five_hour.used == snapshot.weekly.used == 2
