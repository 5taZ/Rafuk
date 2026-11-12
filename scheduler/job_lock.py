"""Cluster-wide single-run guard for scheduler jobs.

PRJ-REVIEW-5: nothing stopped two scheduler processes on one database (the
local stack started next to the VPS, or a second replica) from both running
every job — duplicate alerts, reminders and twice the Kufar traffic. The bot
is protected by Telegram itself (a second getUpdates poller gets 409); the
scheduler was not. Each job now runs under a Postgres session advisory lock
held for its duration: one process runs the tick, the other skips it.
SQLite (tests, host-mode dev) has no advisory locks and runs the job as is.
"""

from __future__ import annotations

import functools
import hashlib
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

logger = logging.getLogger(__name__)


def job_lock_key(name: str) -> int:
    digest = hashlib.sha256(f"rafuk-scheduler:{name}".encode()).digest()
    return int.from_bytes(digest[:8], "big", signed=True)


def single_run(
    name: str, job: Callable[..., Awaitable[Any]],
) -> Callable[..., Awaitable[Any]]:
    """Wrap ``job`` (called with a ``session_factory`` kwarg) in the lock."""
    key = job_lock_key(name)

    @functools.wraps(job)
    async def run(*args: Any, **kwargs: Any) -> Any:
        factory: async_sessionmaker[AsyncSession] = kwargs["session_factory"]
        engine = factory.kw["bind"]
        if engine.dialect.name != "postgresql":
            return await job(*args, **kwargs)
        async with engine.connect() as conn:
            acquired = (await conn.execute(
                text("SELECT pg_try_advisory_lock(:key)"), {"key": key},
            )).scalar_one()
            # Session-level lock: end the transaction so the connection does
            # not sit "idle in transaction" for the whole job.
            await conn.commit()
            if not acquired:
                logger.warning(
                    "Scheduler job %s skipped: another scheduler process holds its lock", name,
                )
                return None
            try:
                return await job(*args, **kwargs)
            finally:
                try:
                    await conn.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": key})
                    await conn.commit()
                except Exception:
                    # A pooled connection must not keep the lock: drop it so
                    # Postgres releases the lock with the backend session.
                    logger.warning("Scheduler job %s: unlock failed, dropping connection", name)
                    await conn.invalidate()

    return run
