"""AI task cache and background task management."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

from api.config import get_settings
from api.services.cache import CacheBackend

logger = logging.getLogger(__name__)

# ── Background task bookkeeping ────────────────────────────────────

# Strong references to background asyncio tasks — without this the GC may
# drop a task before it finishes (documented behaviour in Python 3.12+ for
# fire-and-forget asyncio.create_task patterns). Tasks self-clean from
# this set in their done callback.
_bg_tasks: set[asyncio.Task[Any]] = set()

# INF-02: Hard cap on in-flight AI background tasks.
#
# Each AI analysis can take 60-150s talking to Together AI / Gemini.
# Without an upper bound the same user (or anyone with a valid Mini-App
# initData token) can spam ``POST /ai/analyze`` and put 50+ live tasks
# in flight, each holding an httpx connection, a Kufar dataset, and the
# response payload. Memory grows linearly, the AI provider starts
# rate-limiting at the workspace level, and uvicorn's worker eventually
# OOM-kills.
#
# 16 is sized for the api service's 1 GB memory ceiling (docker-compose)
# minus the FastAPI app + cache pools; it leaves ample headroom for the
# burst of normal HTTP traffic while preventing a single bad actor from
# saturating the AI budget. Lower it in production if you see memory
# pressure; raise it if AI rps stays well below the provider rate limit
# AND you have monitoring in place to catch leaks.
#
# Previous value (50) was effectively no limit — it just protected
# against runaway code rather than abusive request rate.
_BG_TASK_LIMIT = 16


# AI-06: hard upper bound on how long a background AI task is allowed
# to run before we force-cancel it. The AI analysis pipeline already
# has its own per-stage timeouts and the httpx client has a 45 s read
# timeout, but a zombie task with a hung downstream connection (or
# stuck inside a non-cooperative await) was previously kept alive
# forever in _bg_tasks — every zombie burnt a slot until 50 zombies
# DoS'd the bg-task queue.
#
# 240 seconds covers the realistic worst case (search + scoring +
# parallel AI calls + photo download + retries) with margin; tasks
# that exceed it are demonstrably stuck and need to die.
_BG_TASK_TIMEOUT = 240.0


async def _bg_task_watchdog(coro: Any) -> Any:
    """Wrap ``coro`` so it can't outlive _BG_TASK_TIMEOUT seconds."""
    try:
        return await asyncio.wait_for(coro, timeout=_BG_TASK_TIMEOUT)
    except TimeoutError:
        # asyncio.wait_for already requested cancellation on the inner
        # coroutine. Logged here so on-call has a single grep pattern.
        logger.warning(
            "Background task exceeded %.0fs watchdog timeout — cancelled",
            _BG_TASK_TIMEOUT,
        )
        raise


def _spawn_bg_task(coro: Any, *, name: str | None = None) -> asyncio.Task[Any]:
    """Spawn a background task that survives GC until completion.

    Raises ``RuntimeError`` when the in-flight cap is reached so callers
    can map the failure to a 503 response (the alternative — queueing
    silently — leaves the user staring at a "pending" task that may
    never start).

    AI-06: the spawned coroutine is wrapped with an ``asyncio.wait_for``
    watchdog so it can't camp on a bg-task slot indefinitely.
    """
    _bg_tasks.difference_update(t for t in list(_bg_tasks) if t.done())
    if len(_bg_tasks) >= _BG_TASK_LIMIT:
        logger.warning(
            "_bg_tasks at capacity (%d/%d), refusing new task",
            len(_bg_tasks), _BG_TASK_LIMIT,
        )
        raise RuntimeError("Too many background tasks")
    task = asyncio.create_task(_bg_task_watchdog(coro), name=name)
    _bg_tasks.add(task)
    task.add_done_callback(_bg_tasks.discard)
    return task


# ── Task cache helpers ─────────────────────────────────────────────

def _task_ttl() -> int:
    return int(getattr(get_settings(), "ai_task_ttl", 3600) or 3600)


def _task_cache_key(task_id: str, *, user_id: int | None = None) -> str:
    # Namespace by user_id to prevent cross-user data access via shared Redis
    uid_part = f":u{user_id}" if user_id is not None else ""
    return f"ai_task{uid_part}:{task_id}"


def _task_version(task: dict[str, Any] | None) -> float:
    if not task:
        return 0.0
    try:
        return float(task.get("_updated_ts") or task.get("_created_ts") or 0.0)
    except (TypeError, ValueError):
        return 0.0


async def _get_task(
    cache: CacheBackend, task_id: str, *, user_id: int | None = None,
) -> dict[str, Any] | None:
    if user_id is not None:
        cached_task = await cache.get_json(_task_cache_key(task_id, user_id=user_id))
        if cached_task:
            return cached_task
    cached_task = await cache.get_json(_task_cache_key(task_id))
    if cached_task:
        return cached_task
    return None


async def _set_task(cache: CacheBackend, task_id: str, task: dict[str, Any]) -> dict[str, Any]:
    if "_updated_ts" not in task:
        task["_updated_ts"] = datetime.now(UTC).timestamp()
    user_id = task.get("_telegram_user_id")
    await cache.set_json(_task_cache_key(task_id, user_id=user_id), task, ttl=_task_ttl())
    return task


# AI-05: per-task asyncio.Lock so concurrent _update_task calls inside
# a single worker can't read-modify-write race against each other. The
# common case (sequential stages of one bg task) doesn't even contend
# for the lock; the lock matters for the rare path where the endpoint
# initialiser and the spawned bg task both write within the same tick.
#
# Locks are weakly-keyed by task_id and cleaned out when the task
# eventually expires from Redis — we cap the dict size as a hedge
# against unbounded growth if a worker handles many short-lived tasks.
_task_locks: dict[str, asyncio.Lock] = {}
_MAX_TASK_LOCKS = 1024


def _task_lock(task_id: str) -> asyncio.Lock:
    lock = _task_locks.get(task_id)
    if lock is None:
        if len(_task_locks) >= _MAX_TASK_LOCKS:
            # Drop the oldest entries — Python dicts preserve insertion
            # order, so popping from the front evicts the stalest keys.
            for stale_key in list(_task_locks.keys())[: _MAX_TASK_LOCKS // 2]:
                _task_locks.pop(stale_key, None)
        lock = asyncio.Lock()
        _task_locks[task_id] = lock
    return lock


async def _update_task(
    cache: CacheBackend, task_id: str, *, user_id: int | None = None, **updates: Any,
) -> dict[str, Any]:
    # AI-05: serialise the read-modify-write so a parallel update can't
    # overwrite a freshly-merged task with a stale snapshot.
    async with _task_lock(task_id):
        task = await _get_task(cache, task_id, user_id=user_id) or {
            "status": "pending",
            "progress": 0,
            "result": None,
            "error": None,
            "_created_ts": datetime.now(UTC).timestamp(),
            "_updated_ts": datetime.now(UTC).timestamp(),
        }
        task.update(updates)
        task["_updated_ts"] = datetime.now(UTC).timestamp()
        return await _set_task(cache, task_id, task)
