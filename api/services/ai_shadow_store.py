"""
BE-C5: in-process shadow store for AI tasks.

Originally lived inline at the top of ``api/routers/ai_analysis.py``.
Pulling it into a service module keeps the router focused on
HTTP-shaped concerns and gives the privacy/cleanup helpers a stable
import surface (``api.services.ai_shadow_store``) instead of
reaching into a router for state.

The shadow store is the in-memory fallback when Redis is unavailable
or returns ``None`` for a key the request expects:

* ``_tasks``  — AI analysis task records keyed by task_id.

Access to the dict is serialised through a single
``asyncio.Lock``; without it concurrent pruners and consent-deletion
paths iterate the dict while task-status updates mutate it, which can
raise ``"dictionary changed size during iteration"`` or silently drop
records.

The module also exports ``periodic_prune_shadow_stores`` — the
background task ``api/main.py`` schedules at startup so the dict
don't grow without bound on long-running processes.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

_tasks: dict[str, dict] = {}

_MAX_SHADOW_ENTRIES = 50

# Lazy-init so the lock binds to the currently-running loop rather
# than whatever loop happened to be active at import time (important
# for tests that spin up multiple loops).
_shadow_lock: asyncio.Lock | None = None


def _get_shadow_lock() -> asyncio.Lock:
    """Return (and lazily create) the singleton shadow-store lock."""
    global _shadow_lock
    if _shadow_lock is None:
        _shadow_lock = asyncio.Lock()
    return _shadow_lock


def _prune_old_tasks_shadow_unlocked() -> None:
    """Drop expired/over-quota task entries. Caller must hold the lock."""
    now = datetime.now(UTC).timestamp()
    # Deferred import — ai_task_store imports cache helpers that
    # eventually pull settings, which we already imported. Doing it
    # here keeps the module-import graph shallow.
    from api.services.ai_task_store import _task_ttl

    ttl = _task_ttl()
    expired = [k for k, v in _tasks.items() if now - v.get("_updated_ts", 0) > ttl]
    for k in expired:
        _tasks.pop(k, None)
    while len(_tasks) > _MAX_SHADOW_ENTRIES:
        oldest_key = min(
            list(_tasks.keys()),
            key=lambda k: _tasks[k].get("_updated_ts", 0),
            default=None,
        )
        if oldest_key is None:
            break
        _tasks.pop(oldest_key, None)


async def _prune_old_tasks_shadow() -> None:
    async with _get_shadow_lock():
        _prune_old_tasks_shadow_unlocked()


async def periodic_prune_shadow_stores() -> None:
    """Background task: periodically prune in-memory shadow stores.

    Scheduled from ``api/main.py`` lifespan. Sleeps 5 minutes between
    sweeps; cancellation is the normal way to stop it (FastAPI's
    shutdown sequence calls ``task.cancel()``).
    """
    try:
        while True:
            await asyncio.sleep(300)
            await _prune_old_tasks_shadow()
    except asyncio.CancelledError:
        pass
