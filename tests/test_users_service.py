from __future__ import annotations

from types import SimpleNamespace

from sqlalchemy import select
from sqlalchemy.dialects import postgresql

from api.models import User
from api.services.users import ensure_user, lock_user


class RecordingSession:
    """Stands in for an AsyncSession bound to Postgres and records executed statements."""

    def __init__(self) -> None:
        self.statements: list[object] = []
        self._bind = SimpleNamespace(dialect=postgresql.dialect())

    def get_bind(self) -> SimpleNamespace:
        return self._bind

    async def execute(self, statement: object) -> None:
        self.statements.append(statement)


def compile_postgres(statement) -> str:
    return str(statement.compile(dialect=postgresql.dialect()))


async def test_ensure_user_compiles_to_postgres_upsert() -> None:
    session = RecordingSession()
    await ensure_user(session, 5_123_456_789, first_name="  Anna  ", username="anna")

    (statement,) = session.statements
    sql = compile_postgres(statement)
    assert sql.startswith("INSERT INTO users")
    assert "ON CONFLICT (telegram_user_id) DO UPDATE SET" in sql
    update_clause = sql.split("DO UPDATE SET", 1)[1]
    for column in ("last_seen_at", "first_name", "username"):
        assert f"{column} = %(param_" in update_clause
    params = statement.compile(dialect=postgresql.dialect()).params
    assert params["telegram_user_id"] == 5_123_456_789
    assert params["first_name"] == "Anna"
    assert "Anna" in params.values() and "anna" in params.values()


async def test_ensure_user_without_names_never_clears_stored_names() -> None:
    session = RecordingSession()
    await ensure_user(session, 42)

    (statement,) = session.statements
    sql = compile_postgres(statement)
    update_clause = sql.split("DO UPDATE SET", 1)[1]
    assert "last_seen_at = %(param_" in update_clause
    assert "first_name" not in update_clause
    assert "username" not in update_clause


async def test_lock_user_selects_the_row_for_update_on_postgres() -> None:
    session = RecordingSession()
    await lock_user(session, 42)

    (statement,) = session.statements
    sql = compile_postgres(statement)
    assert "FROM users" in sql
    assert "WHERE users.telegram_user_id = %(telegram_user_id_1)s" in sql
    assert sql.rstrip().endswith("FOR UPDATE")


async def test_lock_user_runs_on_sqlite() -> None:
    from api.database import get_engine, get_session_factory

    engine = get_engine()
    session_factory = get_session_factory(engine)
    async with session_factory() as session:
        await ensure_user(session, 42, first_name="Bob")
        await lock_user(session, 42)
        await session.commit()
        stored = await session.scalar(select(User.first_name).where(User.telegram_user_id == 42))
    assert stored == "Bob"
    await engine.dispose()
