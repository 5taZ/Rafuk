from __future__ import annotations

import asyncio

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from api.database import get_engine, get_session_factory

_engine: AsyncEngine | None = None
_init_lock: asyncio.Lock | None = None


def _get_init_lock() -> asyncio.Lock:
    """Get or create the initialization lock."""
    global _init_lock
    if _init_lock is None:
        _init_lock = asyncio.Lock()
    return _init_lock


def get_bot_engine() -> AsyncEngine:
    """Get or create singleton engine for the bot process."""
    global _engine
    if _engine is None:
        _engine = get_engine()
    return _engine


def get_bot_session_factory(engine: AsyncEngine | None = None) -> async_sessionmaker[AsyncSession]:
    """Get session factory for the bot process."""
    return get_session_factory(engine or get_bot_engine())


async def init_bot_engine() -> None:
    """Thread-safe initialization of the bot engine singleton."""
    global _engine
    lock = _get_init_lock()
    async with lock:
        if _engine is None:
            _engine = get_engine()


async def close_bot_engine() -> None:
    """Dispose the bot engine singleton."""
    global _engine, _init_lock
    lock = _get_init_lock()
    async with lock:
        if _engine is not None:
            await _engine.dispose()
            _engine = None
