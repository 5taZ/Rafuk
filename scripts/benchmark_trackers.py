"""Synthetic tracker benchmark: isolated SQLite, fake Kufar, no Telegram sends.

Run from the repository root: uv run python -m scripts.benchmark_trackers --users 10000
"""
from __future__ import annotations

import argparse
import asyncio
import json
import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import event, func, insert, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api.models import Base, TelegramNotificationDLQ, Tracker, TrackerEvent, User
from api.services.history_service import sync_query_listing_states
from scheduler import collector


def ads(size: int) -> list[dict]:
    return [dict(
        ad_id=i + 1, subject=f"Synthetic item {i + 1}", price_byn=100_000,
        ad_link=f"https://www.kufar.by/item/{i + 1}",
    ) for i in range(size)]


async def benchmark(users: int, queries: int, new_ads: int, latency: float) -> dict:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    sf = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        now = datetime.now(UTC)
        async with sf() as session:
            await session.execute(insert(User), [dict(
                id=i + 1, telegram_user_id=90_000_000 + i, first_name="Synthetic",
            ) for i in range(users)])
            await session.execute(insert(Tracker), [dict(
                user_id=i + 1, query=f"synthetic {i % queries}",
                last_checked_at=now - timedelta(minutes=20),
            ) for i in range(users)])
            await session.commit()
        sql_calls = 0

        def record_sql(*args):
            nonlocal sql_calls
            sql_calls += 1

        event.listen(engine.sync_engine, "before_cursor_execute", record_sql)
        writes = []
        for size in (50, 200, 1500):
            sql_calls = 0
            started = time.perf_counter()
            async with sf() as session:
                result = await sync_query_listing_states(
                    session, query=f"write-benchmark-{size}", ads=ads(size),
                    observed_at=now, total_results=size,
                )
                await session.commit()
                assert len(result.new_listings) == size
            writes.append(dict(
                ads=size, sql=sql_calls, seconds=round(time.perf_counter() - started, 3),
            ))
        searches = active = peak = 0

        async def search(**kwargs):
            nonlocal searches, active, peak
            searches += 1
            active += 1
            peak = max(peak, active)
            try:
                await asyncio.sleep(latency)
                return {"ads": ads(new_ads)}
            finally:
                active -= 1

        sql_calls = 0
        started = time.perf_counter()
        with patch.object(
            collector, "_get_kufar_client", return_value=SimpleNamespace(search=search),
        ):
            await collector._check_trackers_inner(
                sf, SimpleNamespace(mini_app_url="https://example.com"),
                deadline=time.monotonic() + collector._CYCLE_BUDGET_SECONDS,
            )
        elapsed = time.perf_counter() - started
        tick_sql = sql_calls
        async with sf() as session:
            checked = await session.scalar(select(func.count(Tracker.id)).where(
                Tracker.last_checked_at >= now,
            ))
            events = await session.scalar(select(func.count(TrackerEvent.id)))
            queued = await session.scalar(select(func.count(TelegramNotificationDLQ.id)))
        assert checked == users, f"Only {checked} of {users} subscribers processed within budget"
        assert events == users * min(new_ads, collector._MAX_EVENTS_PER_TYPE)
        return dict(
            storage="isolated SQLite in memory", synthetic_users=users, unique_queries=queries,
            fake_http_latency_seconds=latency, write_batches=writes,
            tick=dict(seconds=round(elapsed, 3), sql=tick_sql, searches=searches,
                      peak_http=peak, checked=checked, events=events, queued=queued),
        )
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--users", type=int, default=1000)
    parser.add_argument("--queries", type=int, default=1)
    parser.add_argument("--new-ads", type=int, default=1)
    parser.add_argument("--latency", type=float, default=0.01)
    args = parser.parse_args()
    if not (1 <= args.queries <= args.users <= 50_000 and 1 <= args.new_ads <= 50
            and 0 <= args.latency <= 10):
        parser.error("Require queries <= users <= 50000, 1 <= new-ads <= 50, 0 <= latency <= 10")
    print(json.dumps(asyncio.run(benchmark(
        args.users, args.queries, args.new_ads, args.latency,
    )), indent=2))


if __name__ == "__main__":
    main()
