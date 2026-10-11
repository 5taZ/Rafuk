from __future__ import annotations

import pytest
from pydantic import ValidationError

from api.config import Settings, get_settings

REQUIRED_ENV = ("BOT_TOKEN", "DATABASE_URL", "REDIS_URL", "API_BASE_URL", "MINI_APP_URL")
OPTIONAL_ENV = (
    "KUFAR_REQUEST_DELAY",
    "KUFAR_PARALLEL_SEMAPHORE",
    "KUFAR_TIMEOUT",
    "ALERT_CHECK_INTERVAL",
    "CACHE_TTL_SECONDS",
    "INIT_DATA_MAX_AGE_SECONDS",
    "API_RATE_LIMIT_PER_MINUTE",
)


def test_settings_loads_from_env_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BOT_TOKEN", "7123456789:AAFtesttoken")
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://user:pass@db:5432/kufar")
    monkeypatch.setenv("REDIS_URL", "redis://redis:6379/0")
    monkeypatch.setenv("API_BASE_URL", "https://kufar-analytics.example.com")
    monkeypatch.setenv("MINI_APP_URL", "https://kufar-analytics.example.com/app")
    monkeypatch.setenv("INIT_DATA_MAX_AGE_SECONDS", "3600")

    s = Settings(_env_file=None)

    assert s.bot_token == "7123456789:AAFtesttoken"
    assert s.database_url == "postgresql+asyncpg://user:pass@db:5432/kufar"
    assert s.redis_url == "redis://redis:6379/0"
    assert s.api_base_url == "https://kufar-analytics.example.com"
    assert s.mini_app_url == "https://kufar-analytics.example.com/app"
    assert s.init_data_max_age_seconds == 3600


def test_settings_applies_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in OPTIONAL_ENV:
        monkeypatch.delenv(key, raising=False)

    s = Settings(_env_file=None)

    assert s.kufar_request_delay == 0.5
    assert s.kufar_parallel_semaphore == 3
    assert s.alert_check_interval == 30
    assert s.init_data_max_age_seconds == 86400
    # 0: the application-wide request budget follows KUFAR_REQUEST_DELAY.
    assert s.api_rate_limit_per_minute == 0
    assert not hasattr(s, "scheduler_snapshot_hour")


def test_api_rate_limit_setting_is_read_and_validated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("API_RATE_LIMIT_PER_MINUTE", "45")
    assert Settings(_env_file=None).api_rate_limit_per_minute == 45

    monkeypatch.setenv("API_RATE_LIMIT_PER_MINUTE", "-1")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_settings_missing_required_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in REQUIRED_ENV:
        monkeypatch.delenv(key, raising=False)

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_get_settings_is_cached_until_cleared(monkeypatch: pytest.MonkeyPatch) -> None:
    first = get_settings()
    assert get_settings() is first

    monkeypatch.setenv("CACHE_TTL_SECONDS", "42")
    get_settings.cache_clear()
    try:
        assert get_settings().cache_ttl_seconds == 42
    finally:
        get_settings.cache_clear()
