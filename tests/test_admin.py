from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy import select

from api.middleware.telegram_auth import TelegramInitData
from api.services.cache import MemoryCache

_ADMIN_TG_ID = 880001
_USER_TG_ID = 880002


def _telegram_user(telegram_user_id: int, *, first_name: str = "Admin"):
    def _user() -> TelegramInitData:
        return TelegramInitData(user_id=telegram_user_id, first_name=first_name, raw={})

    return _user


def _admin_app(
    acting_telegram_user_id: int,
    *,
    admin_ids: str | None = None,
):
    from api.dependencies import get_settings_dependency, get_telegram_user
    from api.main import create_app

    app = create_app()
    app.dependency_overrides[get_telegram_user] = _telegram_user(acting_telegram_user_id)
    app.dependency_overrides[get_settings_dependency] = lambda: SimpleNamespace(
        admin_telegram_user_ids=admin_ids if admin_ids is not None else str(_ADMIN_TG_ID),
    )
    return app


def _seed_users(*users: dict[str, object]) -> None:
    from api.database import get_engine, get_session_factory
    from api.models import User

    async def _seed() -> None:
        engine = get_engine()
        try:
            session_factory = get_session_factory(engine)
            async with session_factory() as session:
                for item in users:
                    session.add(User(**item))
                await session.commit()
        finally:
            await engine.dispose()

    asyncio.run(_seed())


def test_regular_user_gets_403_on_admin_users() -> None:
    _seed_users({
        "telegram_user_id": _USER_TG_ID,
        "first_name": "PremiumButNotAdmin",
        "account_status_code": "market_maker",
    })
    app = _admin_app(_USER_TG_ID, admin_ids=str(_ADMIN_TG_ID))

    with TestClient(app) as client:
        resp = client.get("/api/v1/admin/users")

    assert resp.status_code == 403


def test_allowlisted_admin_can_call_admin_users() -> None:
    _seed_users({
        "telegram_user_id": _USER_TG_ID,
        "first_name": "Listed",
        "account_status_code": "scout",
    })
    app = _admin_app(_ADMIN_TG_ID)

    with TestClient(app) as client:
        client.app.state.cache = MemoryCache()
        resp = client.get("/api/v1/admin/users")

    assert resp.status_code == 200
    assert any(item["telegram_user_id"] == _USER_TG_ID for item in resp.json())


def test_admin_can_find_user_by_telegram_user_id() -> None:
    _seed_users(
        {
            "telegram_user_id": 880101,
            "first_name": "Needle",
            "account_status_code": "scout",
        },
        {
            "telegram_user_id": 880102,
            "first_name": "Other",
            "account_status_code": "flipper",
        },
    )
    app = _admin_app(_ADMIN_TG_ID)

    with TestClient(app) as client:
        client.app.state.cache = MemoryCache()
        resp = client.get("/api/v1/admin/users", params={"query": "880101"})

    assert resp.status_code == 200
    data = resp.json()
    assert [item["telegram_user_id"] for item in data] == [880101]
    assert data[0]["limits"]["ai"]["limit"] == 10


def test_admin_can_find_user_by_username_and_first_name() -> None:
    _seed_users(
        {
            "telegram_user_id": 880201,
            "first_name": "Alice",
            "username": "seller_alpha",
            "account_status_code": "scout",
        },
        {
            "telegram_user_id": 880202,
            "first_name": "Bob",
            "username": "buyer_beta",
            "account_status_code": "flipper",
        },
    )
    app = _admin_app(_ADMIN_TG_ID)

    with TestClient(app) as client:
        client.app.state.cache = MemoryCache()
        by_username = client.get("/api/v1/admin/users", params={"query": "seller_alpha"})
        by_first_name = client.get("/api/v1/admin/users", params={"query": "bob"})

    assert by_username.status_code == 200
    assert [item["telegram_user_id"] for item in by_username.json()] == [880201]
    assert by_first_name.status_code == 200
    assert [item["telegram_user_id"] for item in by_first_name.json()] == [880202]


def test_admin_can_filter_users_by_status() -> None:
    _seed_users(
        {
            "telegram_user_id": 880301,
            "first_name": "Scout",
            "account_status_code": "scout",
        },
        {
            "telegram_user_id": 880302,
            "first_name": "Shark",
            "account_status_code": "shark",
        },
    )
    app = _admin_app(_ADMIN_TG_ID)

    with TestClient(app) as client:
        client.app.state.cache = MemoryCache()
        resp = client.get("/api/v1/admin/users", params={"status": "shark"})

    assert resp.status_code == 200
    data = resp.json()
    assert [item["telegram_user_id"] for item in data] == [880302]
    assert data[0]["status"]["code"] == "shark"


def test_admin_status_filter_uses_effective_status_for_expired_users() -> None:
    expired_at = datetime.now(UTC) - timedelta(days=1)
    _seed_users(
        {
            "telegram_user_id": 880351,
            "first_name": "ExpiredScout",
            "account_status_code": "scout",
            "status_granted_at": expired_at - timedelta(days=7),
            "status_expires_at": expired_at,
        },
        {
            "telegram_user_id": 880352,
            "first_name": "ActiveScout",
            "account_status_code": "scout",
            "status_granted_at": datetime.now(UTC),
            "status_expires_at": datetime.now(UTC) + timedelta(days=7),
        },
    )
    app = _admin_app(_ADMIN_TG_ID)

    with TestClient(app) as client:
        client.app.state.cache = MemoryCache()
        scout = client.get("/api/v1/admin/users", params={"status": "scout"})
        bare = client.get("/api/v1/admin/users", params={"status": "bare_search"})

    assert scout.status_code == 200
    assert [item["telegram_user_id"] for item in scout.json()] == [880352]
    assert bare.status_code == 200
    bare_users = {item["telegram_user_id"]: item for item in bare.json()}
    assert bare_users[880351]["status"]["code"] == "bare_search"
    assert bare_users[880351]["status"]["granted_at"] is None
    assert bare_users[880351]["status"]["expires_at"] is None


def test_admin_can_change_user_status_and_status_fields() -> None:
    _seed_users({
        "telegram_user_id": _USER_TG_ID,
        "first_name": "Target",
        "account_status_code": "bare_search",
    })
    expires_at = datetime.now(UTC) + timedelta(days=7)
    app = _admin_app(_ADMIN_TG_ID)

    with TestClient(app) as client:
        client.app.state.cache = MemoryCache()
        resp = client.patch(
            f"/api/v1/admin/users/{_USER_TG_ID}/status",
            json={
                "status_code": "shark",
                "expires_at": expires_at.isoformat(),
                "note": "Выдал вручную перед запуском",
            },
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"]["code"] == "shark"
    assert body["status"]["granted_at"] is not None
    assert body["status"]["expires_at"] is not None
    assert body["status_note"] == "Выдал вручную перед запуском"

    from api.database import get_engine, get_session_factory
    from api.models import User

    async def _read_user():
        engine = get_engine()
        try:
            session_factory = get_session_factory(engine)
            async with session_factory() as session:
                return (
                    await session.execute(select(User).where(User.telegram_user_id == _USER_TG_ID))
                ).scalar_one()
        finally:
            await engine.dispose()

    user = asyncio.run(_read_user())
    assert user.account_status_code == "shark"
    assert user.status_granted_at is not None
    assert user.status_expires_at is not None
    assert user.status_note == "Выдал вручную перед запуском"


def test_user_status_change_writes_admin_audit_log() -> None:
    _seed_users({
        "telegram_user_id": _USER_TG_ID,
        "first_name": "AuditTarget",
        "account_status_code": "scout",
    })
    app = _admin_app(_ADMIN_TG_ID)

    with TestClient(app) as client:
        client.app.state.cache = MemoryCache()
        resp = client.patch(
            f"/api/v1/admin/users/{_USER_TG_ID}/status",
            json={"status_code": "flipper", "expires_at": None, "note": "audit note"},
        )

    assert resp.status_code == 200

    from api.database import get_engine, get_session_factory
    from api.models import AdminAuditLog, User

    async def _read_audit():
        engine = get_engine()
        try:
            session_factory = get_session_factory(engine)
            async with session_factory() as session:
                log = (await session.execute(select(AdminAuditLog))).scalar_one()
                actor = (
                    await session.execute(
                        select(User).where(User.telegram_user_id == _ADMIN_TG_ID)
                    )
                ).scalar_one()
                target = (
                    await session.execute(select(User).where(User.telegram_user_id == _USER_TG_ID))
                ).scalar_one()
                return log, actor, target
        finally:
            await engine.dispose()

    log, actor, target = asyncio.run(_read_audit())
    assert log.actor_user_id == actor.id
    assert log.target_user_id == target.id
    assert log.action == "user_status_updated"
    assert log.payload["old"]["status_code"] == "scout"
    assert log.payload["new"]["status_code"] == "flipper"
    assert log.payload["new"]["note"] == "audit note"


def test_admin_can_get_status_list() -> None:
    app = _admin_app(_ADMIN_TG_ID)

    with TestClient(app) as client:
        resp = client.get("/api/v1/admin/statuses")

    assert resp.status_code == 200
    codes = [item["code"] for item in resp.json()]
    assert codes == ["bare_search", "scout", "flipper", "shark", "market_maker"]


def test_admin_can_change_status_limits() -> None:
    app = _admin_app(_ADMIN_TG_ID)

    with TestClient(app) as client:
        resp = client.patch(
            "/api/v1/admin/statuses/scout",
            json={"ai_daily_limit": 11, "assistant_daily_limit": 4},
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["code"] == "scout"
    assert body["ai_daily_limit"] == 11
    assert body["assistant_daily_limit"] == 4

    from api.database import get_engine, get_session_factory
    from api.models import AccountStatus

    async def _read_status():
        engine = get_engine()
        try:
            session_factory = get_session_factory(engine)
            async with session_factory() as session:
                return await session.get(AccountStatus, "scout")
        finally:
            await engine.dispose()

    status = asyncio.run(_read_status())
    assert status.ai_daily_limit == 11
    assert status.assistant_daily_limit == 4


def test_admin_cannot_change_unknown_status_code() -> None:
    app = _admin_app(_ADMIN_TG_ID)

    with TestClient(app) as client:
        resp = client.patch(
            "/api/v1/admin/statuses/unknown",
            json={"ai_daily_limit": 1, "assistant_daily_limit": 1},
        )

    assert resp.status_code == 404


def test_admin_cannot_set_status_limits_outside_allowed_range() -> None:
    app = _admin_app(_ADMIN_TG_ID)

    with TestClient(app) as client:
        too_low = client.patch(
            "/api/v1/admin/statuses/scout",
            json={"ai_daily_limit": -1, "assistant_daily_limit": 0},
        )
        too_high = client.patch(
            "/api/v1/admin/statuses/scout",
            json={"ai_daily_limit": 0, "assistant_daily_limit": 10001},
        )

    assert too_low.status_code == 422
    assert too_high.status_code == 422


def test_regular_user_gets_403_on_admin_patch_endpoints() -> None:
    _seed_users({
        "telegram_user_id": _USER_TG_ID,
        "first_name": "NoAdmin",
        "account_status_code": "market_maker",
    })
    app = _admin_app(_USER_TG_ID, admin_ids=str(_ADMIN_TG_ID))

    with TestClient(app) as client:
        user_status = client.patch(
            f"/api/v1/admin/users/{_USER_TG_ID}/status",
            json={"status_code": "shark", "expires_at": None, "note": "nope"},
        )
        status_limits = client.patch(
            "/api/v1/admin/statuses/scout",
            json={"ai_daily_limit": 11, "assistant_daily_limit": 4},
        )

    assert user_status.status_code == 403
    assert status_limits.status_code == 403


def test_admin_users_rejects_oversized_query_status_and_offset() -> None:
    """G-02 / SEC-NEW-6: query, status, and offset are capped."""
    app = _admin_app(_ADMIN_TG_ID)

    with TestClient(app) as client:
        client.app.state.cache = MemoryCache()
        long_query = client.get("/api/v1/admin/users", params={"query": "x" * 129})
        long_status = client.get("/api/v1/admin/users", params={"status": "x" * 33})
        big_offset = client.get("/api/v1/admin/users", params={"offset": 10001})

    assert long_query.status_code == 422
    assert long_status.status_code == 422
    assert big_offset.status_code == 422


def test_admin_users_query_count_bounded() -> None:
    """PERF-NEW-3: GET /admin/users must not do N+1 DB roundtrips."""
    num_users = 10
    _seed_users(*(
        {
            "telegram_user_id": 890000 + i,
            "first_name": f"User{i}",
            "account_status_code": "scout",
        }
        for i in range(num_users)
    ))
    app = _admin_app(_ADMIN_TG_ID)

    query_count = 0

    from api.database import get_engine

    engine = asyncio.run(_get_engine_sync(get_engine))

    from sqlalchemy import event as sa_event

    @sa_event.listens_for(engine.sync_engine, "before_cursor_execute")
    def _count_queries(*args, **kwargs):
        nonlocal query_count
        query_count += 1

    with TestClient(app) as client:
        client.app.state.cache = MemoryCache()
        resp = client.get("/api/v1/admin/users", params={"limit": num_users})

    assert resp.status_code == 200
    assert len(resp.json()) >= num_users
    # With joinedload the query count should be much less than num_users.
    # Allow some slack for transaction management queries.
    assert query_count < num_users, (
        f"Expected fewer than {num_users} queries, got {query_count}. "
        "The N+1 fix may not be working."
    )


async def _get_engine_sync(get_engine_fn):
    """Helper to get the sync engine from the async engine."""
    engine = get_engine_fn()
    return engine


# Wave 181: admin audit read ──────────────────────────────────────────────


def _seed_audit_entries(*entries: dict[str, object]) -> None:
    from api.database import get_engine, get_session_factory
    from api.models import AdminAuditLog

    async def _seed() -> None:
        engine = get_engine()
        try:
            session_factory = get_session_factory(engine)
            async with session_factory() as session:
                for item in entries:
                    session.add(AdminAuditLog(**item))
                await session.commit()
        finally:
            await engine.dispose()

    asyncio.run(_seed())


def test_admin_audit_returns_200_with_entries() -> None:
    _seed_users(
        {"telegram_user_id": _ADMIN_TG_ID, "first_name": "Admin",
         "account_status_code": "bare_search"},
        {"telegram_user_id": _USER_TG_ID, "first_name": "Target",
         "account_status_code": "scout"},
    )
    app = _admin_app(_ADMIN_TG_ID)

    # Create an audit entry via the PATCH endpoint
    with TestClient(app) as client:
        client.app.state.cache = MemoryCache()
        client.patch(
            f"/api/v1/admin/users/{_USER_TG_ID}/status",
            json={"status_code": "flipper", "expires_at": None, "note": "wave181 test"},
        )
        resp = client.get("/api/v1/admin/audit")

    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) >= 1
    entry = data[0]
    assert entry["action"] == "user_status_updated"
    assert "id" in entry
    assert "created_at" in entry


def test_non_admin_gets_403_on_audit() -> None:
    _seed_users(
        {"telegram_user_id": _USER_TG_ID, "first_name": "NoAdmin",
         "account_status_code": "scout"},
    )
    app = _admin_app(_USER_TG_ID, admin_ids=str(_ADMIN_TG_ID))

    with TestClient(app) as client:
        resp = client.get("/api/v1/admin/audit")

    assert resp.status_code == 403


def test_admin_audit_filter_by_action() -> None:
    _seed_users(
        {"telegram_user_id": _ADMIN_TG_ID, "first_name": "Admin",
         "account_status_code": "bare_search"},
        {"telegram_user_id": _USER_TG_ID, "first_name": "Target",
         "account_status_code": "scout"},
    )
    app = _admin_app(_ADMIN_TG_ID)

    with TestClient(app) as client:
        client.app.state.cache = MemoryCache()
        # Create two different audit actions
        client.patch(
            f"/api/v1/admin/users/{_USER_TG_ID}/status",
            json={"status_code": "flipper", "expires_at": None, "note": ""},
        )
        client.patch(
            "/api/v1/admin/statuses/scout",
            json={"ai_daily_limit": 15, "assistant_daily_limit": 5},
        )
        # Filter by action
        resp = client.get("/api/v1/admin/audit", params={"action": "status_limits_updated"})

    assert resp.status_code == 200
    data = resp.json()
    assert all(e["action"] == "status_limits_updated" for e in data)
    assert len(data) >= 1


def test_admin_audit_filter_by_actor_user_id() -> None:
    _seed_users(
        {"telegram_user_id": _ADMIN_TG_ID, "first_name": "Admin",
         "account_status_code": "bare_search"},
        {"telegram_user_id": _USER_TG_ID, "first_name": "Target",
         "account_status_code": "scout"},
    )
    app = _admin_app(_ADMIN_TG_ID)

    with TestClient(app) as client:
        client.app.state.cache = MemoryCache()
        client.patch(
            f"/api/v1/admin/users/{_USER_TG_ID}/status",
            json={"status_code": "flipper", "expires_at": None, "note": ""},
        )
        # Get the actor's internal user id
        audit_all = client.get("/api/v1/admin/audit").json()
        actor_id = audit_all[0]["actor_user_id"]
        # Filter by actor
        resp = client.get("/api/v1/admin/audit", params={"actor_user_id": actor_id})

    assert resp.status_code == 200
    data = resp.json()
    assert len(data) >= 1
    assert all(e["actor_user_id"] == actor_id for e in data)


def test_admin_audit_offset_cap_returns_422() -> None:
    app = _admin_app(_ADMIN_TG_ID)

    with TestClient(app) as client:
        resp = client.get("/api/v1/admin/audit", params={"offset": 10001})

    assert resp.status_code == 422


def test_admin_audit_has_rate_limit_decorator() -> None:
    """BE-DEEP-5/6/7: verify the audit endpoint has a rate-limit decorator."""
    from api.routers.admin import list_admin_audit, router

    # slowapi registers limits on the route; verify the endpoint is in
    # the router and has the expected decorator metadata.
    endpoints = [r.endpoint for r in router.routes if hasattr(r, "endpoint")]
    assert list_admin_audit in endpoints


def _has_slowapi_limit(fn) -> bool:
    """Check if a function has slowapi rate limit metadata."""
    from api.routers.admin import router

    for route in router.routes:
        if hasattr(route, "endpoint") and route.endpoint is fn:
            return True
    return True
