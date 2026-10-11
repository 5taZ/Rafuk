from __future__ import annotations

from pathlib import Path

import pytest


def _clear_settings_cache() -> None:
    from api.config import get_settings

    get_settings.cache_clear()


def _reset_throttling_middlewares() -> None:
    """Forget per-user throttling windows (and loop-bound semaphores) of the bot routers."""
    from bot.handlers import price, start, tracker
    from bot.middlewares import ThrottlingMiddleware

    for router in (price.router, start.router, tracker.router):
        for middleware in router.message.middleware:
            if isinstance(middleware, ThrottlingMiddleware):
                middleware._last_call.clear()
                middleware._warned.clear()
                middleware._semaphore = None


def _reset_process_state() -> None:
    """Reset every process-wide singleton the application keeps between requests."""
    from api.rate_limit import limiter
    from api.services import kufar_client
    from api.services.query_pipeline import configure_dataset_cache

    limiter.reset()
    configure_dataset_cache(None)
    kufar_client._PACERS.clear()
    _reset_throttling_middlewares()


@pytest.fixture(autouse=True)
def configure_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("BOT_TOKEN", "7123456789:AAFtesttoken")
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("API_BASE_URL", "https://kufar-analytics.example.com")
    monkeypatch.setenv("MINI_APP_URL", "https://kufar-analytics.example.com/app")
    _clear_settings_cache()
    _reset_process_state()
    yield
    _reset_process_state()
    # The cached settings point at this test's tmp_path; never let the next test see them.
    _clear_settings_cache()


@pytest.fixture(autouse=True)
async def create_test_tables(configure_env):
    """Create the schema in the per-test SQLite file (foreign keys enforced) and drop it after.

    Apps built inside a test share this database file, so they need no table setup of their
    own. The bot engine singleton is reset on both sides, so it always follows the per-test
    DATABASE_URL and never keeps a connection to a previous test's file.
    """
    from api.database import get_engine
    from api.models import Base
    from bot.database import reset_bot_engine

    await reset_bot_engine()
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield
    finally:
        await reset_bot_engine()
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await engine.dispose()


@pytest.fixture
def sample_ads() -> list[dict[str, object]]:
    return [
        {
            "ad_id": 1,
            "subject": "iPhone 15",
            "price_byn": 200000,
            "currency": "BYN",
            "ad_link": "https://www.kufar.by/item/1",
            "list_time": "2026-04-01T10:00:00",
            "region_id": 6,
            "ad_parameters": [
                {"p": "condition", "v": "Новый"},
                {"p": "seller_type", "v": "Частное лицо"},
            ],
        },
        {
            "ad_id": 2,
            "subject": "iPhone 15 Pro",
            "price_byn": 220000,
            "currency": "BYN",
            "ad_link": "https://www.kufar.by/item/2",
            "list_time": "2026-04-01T12:00:00",
            "region_id": 6,
            "ad_parameters": [
                {"p": "condition", "v": "Б/у"},
                {"p": "seller_type", "v": "Магазин"},
            ],
        },
        {
            "ad_id": 3,
            "subject": "iPhone 15 Pro Max",
            "price_byn": 250000,
            "currency": "BYN",
            "ad_link": "https://www.kufar.by/item/3",
            "list_time": "2026-04-01T09:00:00",
            "region_id": 6,
            "ad_parameters": [
                {"p": "condition", "v": "Новый"},
                {"p": "seller_type", "v": "Магазин"},
            ],
        },
        {
            "ad_id": 4,
            "subject": "iPhone 15 Used",
            "price_byn": 180000,
            "currency": "BYN",
            "ad_link": "https://www.kufar.by/item/4",
            "list_time": "2026-03-31T18:00:00",
            "region_id": 6,
            "ad_parameters": [
                {"p": "condition", "v": "Б/у"},
                {"p": "seller_type", "v": "Частное лицо"},
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
