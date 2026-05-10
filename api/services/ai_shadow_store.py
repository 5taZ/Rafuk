"""
BE-C5: in-process shadow store for AI tasks/exports.

Originally lived inline at the top of ``api/routers/ai_analysis.py``.
Pulling it into a service module keeps the router focused on
HTTP-shaped concerns and gives the privacy/cleanup helpers a stable
import surface (``api.services.ai_shadow_store``) instead of
reaching into a router for state.

The shadow store is the in-memory fallback when Redis is unavailable
or returns ``None`` for a key the request expects. Two dicts hold:

* ``_tasks``  — AI analysis task records keyed by task_id.
* ``_exports`` — generated AI export reports keyed by random token.

Access to both dicts is serialised through a single
``asyncio.Lock``; without it concurrent pruners and consent-deletion
paths iterate the dicts while task-status updates and export-creation
mutate them, which can raise ``"dictionary changed size during
iteration"`` or silently drop records. One lock is enough — both
dicts are small, modified infrequently, and often walked together
(account deletion in particular touches both).

The module also exports ``periodic_prune_shadow_stores`` — the
background task ``api/main.py`` schedules at startup so the dicts
don't grow without bound on long-running processes.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from api.config import get_settings

_tasks: dict[str, dict] = {}
_exports: dict[str, dict] = {}

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


def _export_ttl() -> int:
    return int(getattr(get_settings(), "ai_export_ttl", 900) or 900)


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


def _prune_old_exports_unlocked() -> None:
    """Drop expired/over-quota export entries. Caller must hold the lock."""
    if not _exports:
        return
    now = datetime.now(UTC).timestamp()
    ttl = _export_ttl()
    expired = [token for token, item in _exports.items() if now - item.get("_created_ts", 0) > ttl]
    for token in expired:
        _exports.pop(token, None)
    while len(_exports) > _MAX_SHADOW_ENTRIES:
        oldest_key = min(_exports, key=lambda k: _exports[k].get("_created_ts", 0))
        _exports.pop(oldest_key, None)


async def _prune_old_exports() -> None:
    async with _get_shadow_lock():
        _prune_old_exports_unlocked()


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
            await _prune_old_exports()
    except asyncio.CancelledError:
        pass
