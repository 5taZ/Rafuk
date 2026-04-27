from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def configure_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import os

    monkeypatch.setenv("BOT_TOKEN", "7123456789:AAFtesttoken")

    # Use TEST_DATABASE_URL if set (CI uses PostgreSQL), otherwise SQLite for local dev
    db_url = os.environ.get("TEST_DATABASE_URL")
    if db_url is None:
        db_url = f"sqlite+aiosqlite:///{tmp_path / 'test.db'}"
    monkeypatch.setenv("DATABASE_URL", db_url)

    # Point tests at a separate Redis logical DB so they don't pollute the
    # dev cache when a real Redis is reachable. Falls back gracefully to
    # MemoryCache via ping() if Redis is unavailable.
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6380/15")
    monkeypatch.setenv("API_BASE_URL", "https://kufar-analytics.example.com")
    monkeypatch.setenv("MINI_APP_URL", "https://kufar-analytics.example.com/app")
    # Tests must never hit real telegram auth (they use dependency_overrides).
    # Debug mode also skips AI consent checks — appropriate for test env.
    monkeypatch.setenv("DEBUG", "true")

    try:
        from api.config import get_settings

        get_settings.cache_clear()
    except Exception:
        pass


@pytest.fixture(autouse=True)
async def _flush_test_redis() -> None:
    """Flush the test Redis DB before each test so tests are isolated.

    Uses redis-py directly (not the async cache) to avoid event-loop
    binding issues. Silent no-op when Redis isn't reachable — tests fall
    back to MemoryCache automatically.
    """
    try:
        import redis

        client = redis.Redis.from_url(
            "redis://localhost:6380/15",
            socket_connect_timeout=0.2,
            socket_timeout=0.2,
        )
        client.flushdb()
        client.close()
    except Exception:
        pass
    yield


@pytest.fixture(autouse=True)
async def create_test_tables():
    """Auto-create test database tables for all tests."""
    from api.database import get_engine
    from api.models import Base

    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


async def init_test_tables(app) -> None:
    """Create all tables for a test app instance."""
    from api.models import Base

    async with app.state.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


@pytest.fixture
def sample_ads() -> list[dict[str, object]]:
    return [
        {
            "ad_id": 1,
            "subject": "iPhone 15",
            "price_byn": 2000,
            "currency": "BYN",
            "ad_link": "https://www.kufar.by/item/1",
            "list_time": "2026-04-01T10:00:00",
            "region_id": 6,
            "company_ad": False,
            "ad_parameters": [
                {"p": "condition", "v": "Новый"},
            ],
        },
        {
            "ad_id": 2,
            "subject": "iPhone 15 Pro",
            "price_byn": 2200,
            "currency": "BYN",
            "ad_link": "https://www.kufar.by/item/2",
            "list_time": "2026-04-01T12:00:00",
            "region_id": 6,
            "company_ad": True,
            "ad_parameters": [
                {"p": "condition", "v": "Б/у"},
            ],
        },
        {
            "ad_id": 3,
            "subject": "iPhone 15 Pro Max",
            "price_byn": 2500,
            "currency": "BYN",
            "ad_link": "https://www.kufar.by/item/3",
            "list_time": "2026-04-01T09:00:00",
            "region_id": 6,
            "company_ad": True,
            "ad_parameters": [
                {"p": "condition", "v": "Новый"},
            ],
        },
        {
            "ad_id": 4,
            "subject": "iPhone 15 Used",
            "price_byn": 1800,
            "currency": "BYN",
            "ad_link": "https://www.kufar.by/item/4",
            "list_time": "2026-03-31T18:00:00",
            "region_id": 6,
            "company_ad": False,
            "ad_parameters": [
                {"p": "condition", "v": "Б/у"},
            ],
        },
        {
            "ad_id": 5,
            "subject": "Broken listing",
            "price_byn": 0,
            "currency": "BYN",
            "ad_link": "https://www.kufar.by/item/5",
            "list_time": "",
            "region_id": 6,
            "ad_parameters": [],
        },
        {
            "ad_id": 6,
            "subject": "Anomaly",
            "price_byn": 15000000,
            "currency": "BYN",
            "ad_link": "https://www.kufar.by/item/6",
            "list_time": "",
            "region_id": 6,
            "ad_parameters": [],
        },
    ]
