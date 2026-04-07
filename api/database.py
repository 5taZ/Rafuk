from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from api.config import get_settings


def get_engine(url: str | None = None) -> AsyncEngine:
    database_url = url or get_settings().database_url
    kwargs: dict[str, object] = {"echo": False}
    if not database_url.startswith("sqlite"):
        kwargs.update(
            pool_size=5,
            max_overflow=10,
            pool_pre_ping=True,      # Verify connection health before use
            pool_recycle=1800,       # Recycle connections every 30 minutes
            pool_timeout=30,         # Wait up to 30s for a connection
        )
    return create_async_engine(database_url, **kwargs)


def get_session_factory(engine: AsyncEngine | None = None) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(
        bind=engine or get_engine(),
        class_=AsyncSession,
        expire_on_commit=False,
    )


async def get_db_session() -> AsyncIterator[AsyncSession]:
    session_factory = get_session_factory()
    async with session_factory() as session:
        yield session
