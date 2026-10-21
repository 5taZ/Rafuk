from __future__ import annotations

import hashlib
import hmac
import json
import time
from types import SimpleNamespace
from urllib.parse import urlencode

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


# ── RV-SEC-05: erasure tombstone on the auth path ───────────────────────


class _InitDataSettings:
    bot_token = SecretStr("dummy")
    internal_service_token = None
    auth_bypass = False
    telegram_init_data_max_age = 7200


def _signed_init_data(telegram_user_id: int, *, auth_date: int, bot_token: str = "dummy") -> str:
    params = {
        "auth_date": str(auth_date),
        "user": json.dumps(
            {"id": telegram_user_id, "first_name": "Erased"}, separators=(",", ":"),
        ),
    }
    data_check_string = "\n".join(f"{key}={value}" for key, value in sorted(params.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()
    params["hash"] = hmac.new(
        secret, data_check_string.encode("utf-8"), hashlib.sha256,
    ).hexdigest()
    return urlencode(params)


@pytest.mark.asyncio
async def test_init_data_minted_before_account_deletion_is_refused(monkeypatch) -> None:
    """RV-SEC-05: initData lives up to telegram_init_data_max_age, so after
    DELETE /account the Mini App kept authenticating with it and every
    request re-provisioned the erased user. It now gets 410 before anything
    is written: request.state.telegram_user stays unset (the auto-provision
    middleware has nothing to act on) and the replay tracker records
    nothing. A reopened Mini App — newer auth_date — is let through."""
    from api import dependencies
    from api.services.account_erasure import mark_account_deleted
    from api.services.cache import MemoryCache

    monkeypatch.setattr(dependencies, "get_settings", lambda: _InitDataSettings())
    cache = MemoryCache()
    now = int(time.time())
    await mark_account_deleted(cache, 4242, deleted_at=now, ttl_seconds=60)
    request = _service_request()
    request.app.state.cache = cache

    with pytest.raises(HTTPException) as exc:
        await dependencies.get_telegram_user(
            request,
            x_telegram_init_data=_signed_init_data(4242, auth_date=now - 60),
            x_internal_service_token=None,
            x_acting_telegram_user_id=None,
        )
    assert exc.value.status_code == 410
    assert exc.value.detail["error"] == "account_deleted"
    assert "Аккаунт удалён" in exc.value.detail["message"]
    assert not hasattr(request.state, "telegram_user")
    assert not [key for key in cache._storage if key.startswith("auth:initdata:")]

    other = await dependencies.get_telegram_user(
        request,
        x_telegram_init_data=_signed_init_data(4343, auth_date=now - 60),
        x_internal_service_token=None,
        x_acting_telegram_user_id=None,
    )
    assert other.user_id == 4343

    reopened = await dependencies.get_telegram_user(
        request,
        x_telegram_init_data=_signed_init_data(4242, auth_date=now + 1),
        x_internal_service_token=None,
        x_acting_telegram_user_id=None,
    )
    assert reopened.user_id == 4242
    assert request.state.telegram_user is reopened


@pytest.mark.asyncio
async def test_erasure_tombstone_lookup_fails_open(monkeypatch) -> None:
    """RV-SEC-05: an unreachable cache must not lock every user out — the
    same fail-open contract as the blacklist lookup."""
    from api import dependencies

    class _DownCache:
        async def get(self, key):
            raise ConnectionError("redis down")

        async def get_json(self, key):
            raise ConnectionError("redis down")

        async def set_json(self, key, value, ttl=None):
            raise ConnectionError("redis down")

    monkeypatch.setattr(dependencies, "get_settings", lambda: _InitDataSettings())
    request = _service_request()
    request.app.state.cache = _DownCache()

    user = await dependencies.get_telegram_user(
        request,
        x_telegram_init_data=_signed_init_data(4242, auth_date=int(time.time()) - 60),
        x_internal_service_token=None,
        x_acting_telegram_user_id=None,
    )
    assert user.user_id == 4242


@pytest.mark.asyncio
async def test_service_token_path_ignores_erasure_tombstone(monkeypatch) -> None:
    """RV-SEC-05: the bot acts on a fresh message or button press from the
    user, not on a stale Mini App session — the tombstone must not break
    the bot's service-token path."""
    from api import dependencies
    from api.services.account_erasure import mark_account_deleted
    from api.services.cache import MemoryCache

    class _Stub(_InitDataSettings):
        internal_service_token = SecretStr("super-secret")

    monkeypatch.setattr(dependencies, "get_settings", lambda: _Stub())
    cache = MemoryCache()
    await mark_account_deleted(cache, 42, deleted_at=time.time(), ttl_seconds=60)
    request = _service_request()
    request.app.state.cache = cache

    user = await dependencies.get_telegram_user(
        request,
        x_telegram_init_data=None,
        x_internal_service_token="super-secret",
        x_acting_telegram_user_id="42",
    )
    assert user.user_id == 42
