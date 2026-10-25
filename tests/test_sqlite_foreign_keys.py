"""IN-05: local SQLite runs enforce foreign keys, like Postgres in CI."""
from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError


async def test_app_engine_enforces_sqlite_foreign_keys() -> None:
    from api.database import get_engine

    engine = get_engine()
    if engine.dialect.name != "sqlite":
        pytest.skip("SQLite-only check; Postgres always enforces foreign keys")
    async with engine.connect() as conn:
        assert (await conn.execute(text("PRAGMA foreign_keys"))).scalar() == 1
        await conn.execute(text("CREATE TEMP TABLE fk_parent (id INTEGER PRIMARY KEY)"))
        await conn.execute(text(
            "CREATE TEMP TABLE fk_child (parent_id INTEGER REFERENCES fk_parent (id))"
        ))
        with pytest.raises(IntegrityError):
            await conn.execute(text("INSERT INTO fk_child (parent_id) VALUES (1)"))
