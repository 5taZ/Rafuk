from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import SecretStr


def test_session_factory_dependency_returns_lifespan_state() -> None:
    from api.dependencies import get_session_factory_dependency

    factory = object()
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(session_factory=factory)))

    assert get_session_factory_dependency(request) is factory


def test_session_factory_dependency_fails_without_lifespan_state() -> None:
    from api.dependencies import get_session_factory_dependency

    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))

    with pytest.raises(RuntimeError, match="session_factory.*lifespan"):
        get_session_factory_dependency(request)


# ── OPUS-12: internal service-token auth ───────────────────────────────────


def _service_request(headers: dict | None = None):
    """Minimal request shape for get_telegram_user."""
    return SimpleNamespace(
        state=SimpleNamespace(),
        app=SimpleNamespace(state=SimpleNamespace(cache=None)),
        client=None,
        headers={**(headers or {})},
    )


@pytest.mark.asyncio
async def test_service_token_path_authenticates_acting_user(monkeypatch) -> None:
    """OPUS-12: a valid service token + acting user id resolves the
    request without forging initData. Returned user has the acting
    id and an empty first_name (the bot doesn't have a real name)."""
    from api import dependencies

    class _Stub:
        bot_token = SecretStr("dummy")
        internal_service_token = SecretStr("super-secret")
        auth_bypass = False

    monkeypatch.setattr(dependencies, "get_settings", lambda: _Stub())

    user = await dependencies.get_telegram_user(
        _service_request(),
        x_telegram_init_data=None,
        x_internal_service_token="super-secret",
        x_acting_telegram_user_id="42",
    )
    assert user.user_id == 42
    assert user.first_name == ""


@pytest.mark.asyncio
async def test_service_token_path_rejects_wrong_token(monkeypatch) -> None:
    from api import dependencies

    class _Stub:
        bot_token = SecretStr("dummy")
        internal_service_token = SecretStr("super-secret")
        auth_bypass = False

    monkeypatch.setattr(dependencies, "get_settings", lambda: _Stub())

    with pytest.raises(HTTPException) as exc:
        await dependencies.get_telegram_user(
            _service_request(),
            x_telegram_init_data=None,
            x_internal_service_token="wrong-token",
            x_acting_telegram_user_id="42",
        )
    assert exc.value.status_code == 401
    assert "service token" in exc.value.detail.lower()


@pytest.mark.asyncio
async def test_service_token_path_requires_positive_acting_id(monkeypatch) -> None:
    from api import dependencies

    class _Stub:
        bot_token = SecretStr("dummy")
        internal_service_token = SecretStr("super-secret")
        auth_bypass = False

    monkeypatch.setattr(dependencies, "get_settings", lambda: _Stub())

    for bad in ("0", "-7", "not-an-int", ""):
        with pytest.raises(HTTPException) as exc:
            await dependencies.get_telegram_user(
                _service_request(),
                x_telegram_init_data=None,
                x_internal_service_token="super-secret",
                x_acting_telegram_user_id=bad or None,
            )
        assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_service_token_falls_back_to_initdata_when_unset(monkeypatch) -> None:
    """OPUS-12: when ``internal_service_token`` is None, the
    service-token branch is bypassed entirely and the request
    falls through to the legacy initData path."""
    from api import dependencies

    class _Stub:
        bot_token = SecretStr("dummy")
        internal_service_token = None
        auth_bypass = False

    monkeypatch.setattr(dependencies, "get_settings", lambda: _Stub())

    # No initData and no service-token config — should hit the
    # missing-initData path with a 401.
    with pytest.raises(HTTPException) as exc:
        await dependencies.get_telegram_user(
            _service_request(),
            x_telegram_init_data=None,
            x_internal_service_token="ignored",
            x_acting_telegram_user_id="42",
        )
    assert exc.value.status_code == 401
    assert "initData" in exc.value.detail


# ── PR-17: ensure_user_exists works on both Postgres and SQLite ─────────


@pytest.mark.asyncio
async def test_ensure_user_exists_works_on_sqlite_dialect() -> None:
    """PR-17: ``ensure_user_exists`` must use the dialect-appropriate
    ``insert(...)`` constructor. The previous shape called
    ``pg_insert`` unconditionally; on aiosqlite this would render
    Postgres-only SQL and aiosqlite would raise OperationalError on
    the ``ON CONFLICT`` clause once a non-debug code path actually
    reached it. Test exercises the function against an in-memory
    aiosqlite engine to lock the contract.
    """
    from sqlalchemy import select

    from api.database import get_engine, get_session_factory
    from api.dependencies import ensure_user_exists
    from api.models import Base, User

    engine = get_engine("sqlite+aiosqlite:///:memory:")
    factory = get_session_factory(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # First call inserts a fresh row.
    await ensure_user_exists(factory, telegram_user_id=42, first_name="Alice")
    async with factory() as session:
        rows = (await session.execute(select(User))).scalars().all()
    assert len(rows) == 1
    assert rows[0].telegram_user_id == 42

    # Second call for the same user MUST be idempotent — ON CONFLICT
    # DO NOTHING means no error, no duplicate row.
    await ensure_user_exists(factory, telegram_user_id=42, first_name="Different name")
    async with factory() as session:
        rows = (await session.execute(select(User))).scalars().all()
    assert len(rows) == 1, "ON CONFLICT DO NOTHING must not create a duplicate row"
    # Original first_name preserved (DO NOTHING semantics, not REPLACE).
    assert rows[0].first_name == "Alice"

    # Debug user (id=0) is short-circuited entirely.
    await ensure_user_exists(factory, telegram_user_id=0)
    async with factory() as session:
        rows = (await session.execute(select(User))).scalars().all()
    assert len(rows) == 1

    await engine.dispose()
