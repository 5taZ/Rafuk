"""AI task cache and background task management."""

from __future__ import annotations

import asyncio
import hashlib
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


async def _delete_task(cache: CacheBackend, task_id: str, *, user_id: int | None = None) -> None:
    await cache.delete(_task_cache_key(task_id, user_id=user_id))


# ── RV-AI-07: per-(user, analysis) singleflight ────────────────────
#
# The frontend gives up polling after ~90 s while the pipeline may run
# up to _BG_TASK_TIMEOUT, so "Повторить" used to start a duplicate task
# and charge the quota again. The first request claims
# ``ai_inflight:u{uid}:{digest}`` with its task_id; repeats return that
# task_id until the task ends. The claim lives in the shared cache
# (Redis SET NX across workers) and expires on its own if a worker dies
# mid-task: no live task can outlast the watchdog.
_INFLIGHT_TTL_SECONDS = int(_BG_TASK_TIMEOUT) + 60
_RUNNING_TASK_STATUSES = frozenset({"pending", "processing"})

# MemoryCache has no SET NX; it also only exists inside one process, so
# an in-process lock around get+set gives the same guarantee there.
_inflight_lock: asyncio.Lock | None = None
_inflight_lock_loop: asyncio.AbstractEventLoop | None = None


def _get_inflight_lock() -> asyncio.Lock:
    global _inflight_lock, _inflight_lock_loop
    loop = asyncio.get_running_loop()
    if _inflight_lock is None or _inflight_lock_loop is not loop:
        _inflight_lock = asyncio.Lock()
        _inflight_lock_loop = loop
    return _inflight_lock


def _inflight_key(user_id: int, analysis_key: str) -> str:
    digest = hashlib.sha256(analysis_key.encode("utf-8")).hexdigest()[:32]
    return f"ai_inflight:u{int(user_id)}:{digest}"


async def _task_is_running(cache: CacheBackend, task_id: str, *, user_id: int) -> bool:
    task = await _get_task(cache, task_id, user_id=user_id)
    return bool(task) and task.get("status") in _RUNNING_TASK_STATUSES


async def _running_inflight_task(cache: CacheBackend, key: str, *, user_id: int) -> str | None:
    """task_id of the live task holding ``key``, if there is one."""
    owner = await cache.get(key)
    if owner and await _task_is_running(cache, owner, user_id=user_id):
        return owner
    return None


async def _set_if_absent(cache: CacheBackend, key: str, value: str) -> bool | None:
    acquire = getattr(cache, "try_acquire_lock", None)
    if callable(acquire):
        return await acquire(key, value, ttl_seconds=_INFLIGHT_TTL_SECONDS)
    async with _get_inflight_lock():
        if await cache.get(key) is not None:
            return False
        await cache.set(key, value, ttl=_INFLIGHT_TTL_SECONDS)
        return True


async def _claim_inflight(
    cache: CacheBackend, key: str, task_id: str, *, user_id: int,
) -> str | None:
    """Make ``task_id`` the owner of ``key``.

    Returns None when the caller now owns the claim (or the backend
    can't tell, in which case the request simply runs unshared), else
    the task_id of the live task that already owns it. A claim whose
    task is gone or finished is stale and taken over.
    """
    for _ in range(2):
        acquired = await _set_if_absent(cache, key, task_id)
        if acquired is not False:
            return None
        owner = await cache.get(key)
        if owner and await _task_is_running(cache, owner, user_id=user_id):
            return owner
        if owner:
            await _release_inflight(cache, key, owner)
    return None


async def _release_inflight(cache: CacheBackend, key: str, task_id: str) -> None:
    """Drop the claim, but only while ``task_id`` still owns it."""
    release = getattr(cache, "release_lock", None)
    if callable(release):
        await release(key, task_id)
        return
    async with _get_inflight_lock():
        if await cache.get(key) == task_id:
            await cache.delete(key)


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
            # PR-12: skip locks that are currently held by another
            # ``_update_task`` invocation. The previous shape evicted
            # the oldest half by insertion order alone — if a long-
            # running task happened to be in that half, a parallel
            # update for the SAME task_id would build a fresh lock
            # and the per-task serialisation that AI-05 set up was
            # broken for the duration of that race.
            target = _MAX_TASK_LOCKS // 2
            evicted = 0
            for stale_key in list(_task_locks.keys()):
                candidate = _task_locks.get(stale_key)
                if candidate is None:
                    continue
                if candidate.locked():
                    continue
                _task_locks.pop(stale_key, None)
                evicted += 1
                if evicted >= target:
                    break
            # Emergency release valve: if every lock is held (rare —
            # would mean 1024 tasks updating concurrently on one
            # worker), drop the oldest anyway so the dict can't grow
            # past the cap forever. The next update on those tasks
            # picks up a fresh lock; the worst case is the same race
            # the FIFO sweep used to have, but only when there's no
            # better option.
            if evicted == 0:
                for stale_key in list(_task_locks.keys())[:target]:
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
            # RV2-AI2-07: a task recreated after the user's data was erased
            # must land under the user-scoped key the erasure sweeps, not
            # the unscoped one.
            "_telegram_user_id": user_id,
            "_created_ts": datetime.now(UTC).timestamp(),
            "_updated_ts": datetime.now(UTC).timestamp(),
        }
        task.update(updates)
        task["_updated_ts"] = datetime.now(UTC).timestamp()
        return await _set_task(cache, task_id, task)
