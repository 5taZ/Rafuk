"""PRJ-REVIEW-1 / PRJ-REVIEW-5: admin alerts and the scheduler job lock."""

from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from api.database import get_engine, get_session_factory
from api.models import Base, TelegramNotificationDLQ
from scheduler import ops_watchdog
from scheduler.job_lock import job_lock_key, single_run

SETTINGS = SimpleNamespace(admin_telegram_user_ids="7001, 7002")


@pytest.fixture
async def db():
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield get_session_factory(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.fixture(autouse=True)
def fresh_state(monkeypatch):
    monkeypatch.setattr(ops_watchdog, "STATE", ops_watchdog.WatchdogState())


class FakeRedis:
    def __init__(self, **info):
        self.info_values = info
        self._client = self

    async def info(self):
        return dict(self.info_values)


async def test_healthy_deployment_sends_nothing(db) -> None:
    bot = SimpleNamespace(send_message=AsyncMock())
    ops_watchdog.mark_tick_completed()
    cache = FakeRedis(evicted_keys=0, used_memory=10, maxmemory=100)
    assert await ops_watchdog.run_ops_watchdog(bot, db, SETTINGS, cache=cache) == []
    assert not bot.send_message.await_count


async def test_stalled_tick_alerts_every_admin_once_per_window(db, monkeypatch) -> None:
    bot = SimpleNamespace(send_message=AsyncMock())
    ops_watchdog.STATE.started_at = time.monotonic() - ops_watchdog.TICK_STALE_SECONDS - 60
    assert await ops_watchdog.run_ops_watchdog(bot, db, SETTINGS) == ["tracker-tick"]
    assert sorted(call.args[0] for call in bot.send_message.await_args_list) == [7001, 7002]
    assert "проверка трекеров" in bot.send_message.await_args.args[1]
    # The same alert stays quiet until REPEAT_AFTER_SECONDS passes.
    await ops_watchdog.run_ops_watchdog(bot, db, SETTINGS)
    assert bot.send_message.await_count == 2


async def test_redis_evictions_after_baseline_and_memory_pressure_alert(db) -> None:
    bot = SimpleNamespace(send_message=AsyncMock())
    ops_watchdog.mark_tick_completed()
    cache = FakeRedis(evicted_keys=5, used_memory=10, maxmemory=100)
    # The first run only records the counter: evictions before start are old news.
    assert await ops_watchdog.run_ops_watchdog(bot, db, SETTINGS, cache=cache) == []
    cache.info_values.update(evicted_keys=9, used_memory=95)
    keys = await ops_watchdog.run_ops_watchdog(bot, db, SETTINGS, cache=cache)
    assert keys == ["redis-evicted", "redis-memory"]


async def test_dlq_backlog_and_persistent_kufar_outage_alert(db, monkeypatch) -> None:
    bot = SimpleNamespace(send_message=AsyncMock())
    ops_watchdog.mark_tick_completed()
    monkeypatch.setattr(ops_watchdog, "DLQ_BACKLOG_LIMIT", 2)
    async with db() as session:
        session.add_all([
            TelegramNotificationDLQ(
                telegram_user_id=1, source="test", message="m", error_kind="retryable",
            )
            for _ in range(2)
        ])
        await session.commit()
    for _ in range(ops_watchdog.KUFAR_OPEN_RUNS - 1):
        assert "kufar-down" not in await ops_watchdog.run_ops_watchdog(
            bot, db, SETTINGS, kufar_open=True,
        )
    keys = await ops_watchdog.run_ops_watchdog(bot, db, SETTINGS, kufar_open=True)
    assert keys == ["dlq-backlog", "kufar-down"]
    # One closed check resets the outage counter.
    assert "kufar-down" not in await ops_watchdog.run_ops_watchdog(bot, db, SETTINGS)


async def test_single_run_runs_the_job_directly_without_advisory_locks(db) -> None:
    job = AsyncMock(return_value="done")
    wrapped = single_run("tracker-check", job)
    assert await wrapped(session_factory=db, bot=None) == "done"
    job.assert_awaited_once_with(session_factory=db, bot=None)


def test_job_lock_keys_are_stable_signed_bigints() -> None:
    key = job_lock_key("tracker-check")
    assert key == job_lock_key("tracker-check") != job_lock_key("dlq-retry")
    assert -(2**63) <= key < 2**63


async def test_single_run_skips_a_job_another_process_is_running(db) -> None:
    import asyncio

    if db.kw["bind"].dialect.name != "postgresql":
        pytest.skip("advisory locks need Postgres (CI)")
    started, release = asyncio.Event(), asyncio.Event()

    async def slow_job(**_):
        started.set()
        await release.wait()
        return "first"

    first = asyncio.create_task(single_run("lock-test", slow_job)(session_factory=db))
    await started.wait()
    second = AsyncMock(return_value="second")
    assert await single_run("lock-test", second)(session_factory=db) is None
    second.assert_not_awaited()
    release.set()
    assert await first == "first"
    # Released after the run: the next tick takes the lock again.
    assert await single_run("lock-test", second)(session_factory=db) == "second"


async def test_every_scheduler_job_runs_under_the_lock_and_the_watchdog_is_scheduled(db) -> None:
    from unittest.mock import MagicMock

    from scheduler import collector

    scheduler = collector.create_scheduler(MagicMock(), db, MagicMock())
    jobs = {job.id: job for job in scheduler.get_jobs()}
    assert set(jobs) == {
        "tracker-check", "daily-cleanup", "reminder-check", "dlq-retry", "ops-watchdog",
    }
    for job in jobs.values():
        # single_run wraps with functools.wraps; the lock wrapper is the callable.
        assert job.func.__wrapped__ is not None
    assert jobs["ops-watchdog"].func.__wrapped__ is collector.run_ops_checks
