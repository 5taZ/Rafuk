from __future__ import annotations

import os
from typing import Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from api.config import get_settings

# Opt-in switch for FOREIGN KEY enforcement on SQLite ("1" or "true"). It stays off by
# default: local SQLite databases built by Base.metadata.create_all on older models declare
# user_id REFERENCES users(id), which migrations cannot rebuild, so enforcing it there would
# reject every user-scoped write. The test suite turns it on (tests/conftest.py).
SQLITE_FOREIGN_KEYS_ENV = "SQLITE_FOREIGN_KEYS"


def sqlite_foreign_keys_enabled() -> bool:
    return os.environ.get(SQLITE_FOREIGN_KEYS_ENV, "").strip().lower() in {"1", "true"}


def _enable_sqlite_foreign_keys(dbapi_connection: Any, _connection_record: Any) -> None:
    # SQLite ignores FOREIGN KEY clauses unless this pragma is set on every new connection.
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
    finally:
        cursor.close()


def get_engine(url: str | None = None) -> AsyncEngine:
    database_url = url or get_settings().database_url
    kwargs: dict[str, object] = {"echo": False}
    is_sqlite = database_url.startswith("sqlite")
    if not is_sqlite:
        kwargs.update(
            pool_size=5,
            max_overflow=10,
            pool_pre_ping=True,  # Verify connection health before use
            pool_recycle=1800,  # Recycle connections every 30 minutes
            pool_timeout=30,  # Wait up to 30s for a connection
        )
    engine = create_async_engine(database_url, **kwargs)
    if is_sqlite and sqlite_foreign_keys_enabled():
        # Enforce foreign keys like Postgres does, so the tests catch FK bugs.
        event.listen(engine.sync_engine, "connect", _enable_sqlite_foreign_keys)
    return engine


def get_session_factory(engine: AsyncEngine | None = None) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(
        bind=engine or get_engine(),
        class_=AsyncSession,
        expire_on_commit=False,
    )
