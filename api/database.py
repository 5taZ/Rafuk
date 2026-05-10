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
    settings = get_settings()
    kwargs: dict[str, object] = {"echo": False}
    if not database_url.startswith("sqlite"):
        kwargs.update(
            pool_size=settings.db_pool_size,
            max_overflow=settings.db_max_overflow,
            pool_pre_ping=True,
            pool_recycle=1800,
            pool_timeout=30,
            # DB-M1: LIFO check-out so a small hot set of connections
            # gets reused and the rest stay idle long enough for the
            # Postgres server-side recycler and pool_recycle to drop
            # them. FIFO (the SQLAlchemy default) round-robins across
            # the pool, which keeps every connection "warm" and
            # consequently none of them ever exceed pool_recycle's
            # 1800s — leading to long-lived connections that hang on
            # to server-side caches and memory. LIFO is a
            # drop-in win for any pool where peak concurrency is
            # normally well below ``pool_size`` (ours is: 5 connections
            # per worker × 4 workers = 20 steady-state, but most
            # requests finish in <100ms so only 1-2 connections are
            # actively in flight at any given moment).
            pool_use_lifo=True,
        )
    return create_async_engine(database_url, **kwargs)


def get_session_factory(engine: AsyncEngine | None = None) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(
        bind=engine or get_engine(),
        class_=AsyncSession,
        expire_on_commit=False,
    )
