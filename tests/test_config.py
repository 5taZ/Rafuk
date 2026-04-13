from __future__ import annotations

import importlib
import os
from unittest.mock import patch

import pytest
from pydantic import ValidationError


def test_settings_loads_from_env_vars() -> None:
    env = {
        "BOT_TOKEN": "7123456789:AAFtesttoken",
        "DATABASE_URL": "postgresql+asyncpg://user:pass@db:5432/kufar",
        "REDIS_URL": "redis://redis:6379/0",
        "API_BASE_URL": "https://kufar-analytics.example.com",
        "MINI_APP_URL": "https://kufar-analytics.example.com/app",
    }
    with patch.dict(os.environ, env, clear=False):
        from api import config

        config.get_settings.cache_clear()
        importlib.reload(config)
        s = config.Settings()
        assert s.bot_token.get_secret_value() == "7123456789:AAFtesttoken"
        assert s.database_url == "postgresql+asyncpg://user:pass@db:5432/kufar"
        assert s.redis_url == "redis://redis:6379/0"
        assert s.api_base_url == "https://kufar-analytics.example.com"
        assert s.mini_app_url == "https://kufar-analytics.example.com/app"


def test_settings_applies_defaults() -> None:
    # Save original env vars
    import os

    original_env = {}
    for key in ["KUFAR_REQUEST_DELAY", "KUFAR_PARALLEL_SEMAPHORE", "SCHEDULER_SNAPSHOT_HOUR", "ALERT_CHECK_INTERVAL"]:
        if key in os.environ:
            original_env[key] = os.environ[key]
            del os.environ[key]

    try:
        env = {
            "BOT_TOKEN": "test",
            "DATABASE_URL": "sqlite+aiosqlite:///test.db",
            "REDIS_URL": "redis://localhost:6379/0",
            "API_BASE_URL": "https://example.com",
            "MINI_APP_URL": "https://example.com/app",
        }
        with patch.dict(os.environ, env, clear=False):
            # Clear any existing .env file influence
            from api import config

            config.get_settings.cache_clear()
            importlib.reload(config)
            s = config.Settings(_env_file=None)  # Ignore .env file
            assert s.kufar_request_delay == 1.0
            assert s.kufar_parallel_semaphore == 2
            assert s.alert_check_interval == 30
    finally:
        # Restore original env vars
        for key, value in original_env.items():
            os.environ[key] = value


def test_settings_missing_required_raises() -> None:
    with patch.dict(os.environ, {}, clear=True):
        from api import config

        config.get_settings.cache_clear()
        importlib.reload(config)
        with pytest.raises(ValidationError):
            config.Settings(_env_file=None)
