from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, AsyncSession

from api.database import get_engine

_engine: AsyncEngine | None = None


def get_bot_engine() -> AsyncEngine:
    """Get or create singleton engine for the bot process."""
    global _engine
    if _engine is None:
        _engine = get_engine()
    return _engine


def get_bot_session_factory(engine: AsyncEngine | None = None) -> async_sessionmaker[AsyncSession]:
    """Get session factory for the bot process."""
    from api.database import get_session_factory
    return get_session_factory(engine or get_bot_engine())


async def init_bot_engine() -> None:
    """Initialize the bot engine singleton."""
    global _engine
    if _engine is not None:
        return
    _engine = get_engine()


async def close_bot_engine() -> None:
    """Dispose the bot engine singleton."""
    global _engine
    if _engine is not None:
        await _engine.dispose()
        _engine = None
