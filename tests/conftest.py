from __future__ import annotations

import asyncio
import hashlib
import hmac
import time
from pathlib import Path
from urllib.parse import urlencode

import pytest

from api.middleware.telegram_auth import TelegramInitData

FAKE_TELEGRAM_USER = TelegramInitData(user_id=123456, first_name="Test", raw={})


def make_telegram_init_data(user_id: int = 123456) -> str:
    """Create a valid Telegram initData string for testing."""
    bot_token = "7123456789:AAFtesttoken"
    data_dict = {
        "user": f'{{"id":{user_id},"first_name":"Test"}}',
        "auth_date": str(int(time.time())),
        "query_id": "AAHtest",
    }
    data_check_string = "\n".join(f"{key}={value}" for key, value in sorted(data_dict.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    signature = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    data_dict["hash"] = signature
    return urlencode(data_dict)


@pytest.fixture
def telegram_headers() -> dict[str, str]:
    return {"X-Telegram-Init-Data": make_telegram_init_data()}


@pytest.fixture(autouse=True)
def configure_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("BOT_TOKEN", "7123456789:AAFtesttoken")
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("API_BASE_URL", "https://kufar-analytics.example.com")
    monkeypatch.setenv("MINI_APP_URL", "https://kufar-analytics.example.com/app")

    try:
        from api.config import get_settings

        get_settings.cache_clear()
    except Exception:
        pass


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
