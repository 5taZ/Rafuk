from __future__ import annotations

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
            pool_pre_ping=True,
            pool_recycle=1800,
            pool_timeout=30,
        )
    return create_async_engine(database_url, **kwargs)


def get_session_factory(engine: AsyncEngine | None = None) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(
        bind=engine or get_engine(),
        class_=AsyncSession,
        expire_on_commit=False,
    )
