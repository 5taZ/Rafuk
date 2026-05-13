"""Dedicated unit tests for AI service helpers (Wave 27 — TEST-02).

The existing AI test suite exercises the full HTTP pipeline through
the FastAPI router; this file targets the four modules the audit
called out as having NO dedicated coverage:

* ``api.services.ai_audit``       — _log_ai_audit
* ``api.services.ai_privacy``     — clear_user_ai_data
* ``api.services.ai_task_store``  — _spawn_bg_task / _update_task / _bg_task_watchdog
* ``api.services.ai_shadow_store`` — _tasks / _prune_old_*
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import select

from api.database import get_engine, get_session_factory
from api.metrics import _reset_metrics_for_tests, render_prometheus_metrics
from api.models import AIAuditLog, Base, User
from api.services import ai_shadow_store
from api.services.ai_audit import _log_ai_audit
from api.services.ai_privacy import clear_user_ai_data
from api.services.ai_task_store import (
    _BG_TASK_LIMIT,
    _bg_tasks,
    _spawn_bg_task,
    _task_cache_key,
    _update_task,
)
from api.services.cache import MemoryCache

# ──────────────────────────────────────────────────────────────────────
# Test helpers — fresh in-memory schema per test (so the rows we insert
# can't leak across tests in this file).
# ──────────────────────────────────────────────────────────────────────


async def _fresh_session_factory():
    engine = get_engine("sqlite+aiosqlite:///:memory:")
    factory = get_session_factory(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return factory


# ──────────────────────────────────────────────────────────────────────
# ai_audit._log_ai_audit
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_log_ai_audit_creates_row_for_known_user() -> None:
    factory = await _fresh_session_factory()
    async with factory() as session:
        session.add(User(telegram_user_id=111111, first_name="A"))
        await session.commit()

    await _log_ai_audit(
        factory,
        telegram_user_id=111111,
        endpoint="analyze",
        ad_id="123",
        query="iphone",
        model="gemini-2.5-flash",
        latency_ms=42,
        ip_address="203.0.113.5",
    )

    async with factory() as session:
        rows = (await session.execute(select(AIAuditLog))).scalars().all()
    assert len(rows) == 1
    entry = rows[0]
    assert entry.endpoint == "analyze"
    assert entry.ad_id == "123"
    assert entry.model == "gemini-2.5-flash"
    assert entry.latency_ms == 42
    # OPUS-17: IP captured at decision time matches what we passed.
    assert entry.ip_address == "203.0.113.5"


@pytest.mark.asyncio
async def test_log_ai_audit_truncates_long_query() -> None:
    factory = await _fresh_session_factory()
    async with factory() as session:
        session.add(User(telegram_user_id=222222, first_name="B"))
        await session.commit()

    long_query = "iphone " * 100  # ~700 chars
    await _log_ai_audit(
        factory,
        telegram_user_id=222222,
        endpoint="analyze",
        query=long_query,
        model="m",
    )

    async with factory() as session:
        row = (await session.execute(select(AIAuditLog))).scalar_one()
    # Field is clamped to 256 chars to keep the row size predictable.
    assert row.query is not None
    assert len(row.query) <= 256


@pytest.mark.asyncio
async def test_log_ai_audit_silent_no_op_for_unknown_user() -> None:
    """resolve_user_id returns None for a telegram_user_id we've never
    seen — the writer should skip the row rather than crash."""
    factory = await _fresh_session_factory()
    await _log_ai_audit(
        factory,
        telegram_user_id=999_999_999,
        endpoint="analyze",
        model="m",
    )
    async with factory() as session:
        rows = (await session.execute(select(AIAuditLog))).scalars().all()
    assert rows == []


@pytest.mark.asyncio
async def test_log_ai_audit_swallows_session_errors(caplog) -> None:
    """The audit row is best-effort — a broken session factory must
    NOT propagate into the caller (the AI response was already sent
    by the time we get here)."""
    import logging as _logging

    _reset_metrics_for_tests()
    caplog.set_level(_logging.WARNING)

    def broken_factory():
        raise RuntimeError("db is down")

    # Should not raise.
    await _log_ai_audit(
        broken_factory,
        telegram_user_id=1,
        endpoint="analyze",
        model="m",
    )
    assert any(
        "Failed to write AI audit log" in record.message
        for record in caplog.records
    )
    assert 'kufar_ai_audit_failures_total{endpoint="analyze"} 1' in render_prometheus_metrics()


# ──────────────────────────────────────────────────────────────────────
# ai_privacy.clear_user_ai_data
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_clear_user_ai_data_uses_passed_cache(monkeypatch) -> None:
    """OPUS-20: when the caller passes ``cache``, clear_user_ai_data
    must NOT open a new RedisCache. The already-warm pool from
    ``app.state.cache`` is enough.
    """
    from api.services import ai_privacy
    from api.services.cache import MemoryCache, RedisCache

    open_calls = 0

    def _from_url(*args, **kwargs):
        nonlocal open_calls
        open_calls += 1
        return RedisCache(redis_url="redis://stub:6379/0")

    monkeypatch.setattr(RedisCache, "from_url", staticmethod(_from_url))

    passed_cache = MemoryCache()
    await ai_privacy.clear_user_ai_data(123, cache=passed_cache)

    assert open_calls == 0, "Passed cache must short-circuit the Redis pool init"


@pytest.mark.asyncio
async def test_clear_user_ai_data_purges_shadow_stores(monkeypatch) -> None:
    """The shadow-store cleanup branch is the one path that DOESN'T
    depend on Redis — exercise it in isolation to make sure the
    per-user filter works (and doesn't drop OTHER users' entries)."""
    user_a = 11111
    user_b = 22222

    # Force the function down the MemoryCache fallback by making
    # ``RedisCache.from_url`` raise — the try/except inside
    # clear_user_ai_data swaps to MemoryCache, and the subsequent
    # ``redis_client = getattr(cache, "_client", None)`` returns None
    # so we skip the Redis SCAN loop entirely.
    from api.services import cache as cache_module

    def _boom(*args, **kwargs):
        raise RuntimeError("redis unreachable in test")

    monkeypatch.setattr(cache_module.RedisCache, "from_url", _boom)

    # Seed two users into the shadow store.
    ai_shadow_store._tasks.clear()
    ai_shadow_store._tasks["task-a-1"] = {"_telegram_user_id": user_a, "x": 1}
    ai_shadow_store._tasks["task-a-2"] = {"_telegram_user_id": user_a, "x": 2}
    ai_shadow_store._tasks["task-b-1"] = {"_telegram_user_id": user_b, "x": 3}

    await clear_user_ai_data(user_a)

    # User A's entries are gone; User B's are untouched.
    remaining_task_users = {
        v.get("_telegram_user_id") for v in ai_shadow_store._tasks.values()
    }
    assert user_a not in remaining_task_users
    assert user_b in remaining_task_users


# ──────────────────────────────────────────────────────────────────────
# ai_task_store._spawn_bg_task
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_spawn_bg_task_tracks_until_completion() -> None:
    _bg_tasks.clear()

    async def noop() -> int:
        await asyncio.sleep(0)
        return 7

    task = _spawn_bg_task(noop(), name="noop")
    assert task in _bg_tasks
    result = await task
    assert result == 7
    # done callback evicts the task from the set.
    assert task not in _bg_tasks


@pytest.mark.asyncio
async def test_spawn_bg_task_rejects_when_over_limit() -> None:
    _bg_tasks.clear()

    # Fill up the slot table with placeholder tasks that never complete
    # until we await them at the end.
    placeholders: list[asyncio.Task] = []
    blocker_event = asyncio.Event()

    async def blocker():
        await blocker_event.wait()

    try:
        for _ in range(_BG_TASK_LIMIT):
            placeholders.append(_spawn_bg_task(blocker(), name="block"))
        # Build the overflow coroutine separately so we can close it
        # ourselves after the expected RuntimeError — otherwise pytest
        # warns about an un-awaited coroutine.
        overflow_coro = blocker()
        try:
            with pytest.raises(RuntimeError, match="Too many background tasks"):
                _spawn_bg_task(overflow_coro, name="overflow")
        finally:
            overflow_coro.close()
    finally:
        # Release the blockers so the event loop can shut down cleanly.
        blocker_event.set()
        await asyncio.gather(*placeholders, return_exceptions=True)
        _bg_tasks.clear()


# ──────────────────────────────────────────────────────────────────────
# _update_task version stamp + namespacing
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_update_task_stamps_updated_ts_on_every_write() -> None:
    cache = MemoryCache()
    first = await _update_task(cache, "tid", progress=10)
    second = await _update_task(cache, "tid", progress=20)
    assert second["_updated_ts"] >= first["_updated_ts"]
    assert second["progress"] == 20


@pytest.mark.asyncio
async def test_update_task_namespaces_by_user_id() -> None:
    """User A's update must not be visible at User B's namespaced key.

    Namespace comes from the task's ``_telegram_user_id`` field — that's
    the value _set_task reads when computing the cache key. ai_analysis
    seeds it via the initial ``_set_task`` call; once it's there every
    subsequent _update_task lands in the right namespace.
    """
    from api.services.ai_task_store import _set_task

    cache = MemoryCache()
    await _set_task(cache, "tid", {"_telegram_user_id": 1, "status": "pending"})
    await _update_task(cache, "tid", user_id=1, status="done")
    key_a = _task_cache_key("tid", user_id=1)
    key_b = _task_cache_key("tid", user_id=2)
    assert key_a != key_b
    assert await cache.get_json(key_a) is not None
    assert await cache.get_json(key_b) is None


# ──────────────────────────────────────────────────────────────────────
# ai_shadow_store pruning
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_prune_old_tasks_evicts_stale_entries() -> None:
    """Stale entries past the TTL window should be dropped while
    recently-touched ones survive. The pruner keys off _updated_ts
    (not _created_ts) so the "fresh" entry has to set both."""
    import time

    ai_shadow_store._tasks.clear()
    now = time.time()
    # 2 days old — well past the 1-hour TTL default.
    ai_shadow_store._tasks["old"] = {
        "_created_ts": now - 86400 * 2,
        "_updated_ts": now - 86400 * 2,
    }
    ai_shadow_store._tasks["fresh"] = {
        "_created_ts": now,
        "_updated_ts": now,
    }

    await ai_shadow_store._prune_old_tasks_shadow()
    assert "old" not in ai_shadow_store._tasks
    assert "fresh" in ai_shadow_store._tasks
