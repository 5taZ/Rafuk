from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from api.database import get_engine, get_session_factory

# Process-wide engine for the bot. Creation is synchronous, so asyncio needs no lock:
# no other task can run between the None check and the assignment.
_engine: AsyncEngine | None = None


def get_bot_engine() -> AsyncEngine:
    """Get or create the singleton engine for the bot process."""
    global _engine
    if _engine is None:
        _engine = get_engine()
    return _engine


def get_bot_session_factory(engine: AsyncEngine | None = None) -> async_sessionmaker[AsyncSession]:
    """Get a session factory bound to the bot engine (or to ``engine`` when given)."""
    return get_session_factory(engine or get_bot_engine())


async def init_bot_engine() -> None:
    """Create the bot engine eagerly at startup."""
    get_bot_engine()


async def close_bot_engine() -> None:
    """Dispose the bot engine singleton; the next get_bot_engine() creates a fresh one."""
    global _engine
    engine, _engine = _engine, None
    if engine is not None:
        await engine.dispose()


async def reset_bot_engine() -> None:
    """Test helper: drop the cached engine so the next test uses its own DATABASE_URL."""
    await close_bot_engine()
