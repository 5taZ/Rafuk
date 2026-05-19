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
        "DEBUG": "false",
        "AUTH_BYPASS": "false",
        "REDIS_URL": "redis://redis:6379/0",
        "API_BASE_URL": "https://kufar-analytics.example.com",
        "MINI_APP_URL": "https://kufar-analytics.example.com/app",
    }
    with patch.dict(os.environ, env, clear=False):
        from api import config

        config.get_settings.cache_clear()
        importlib.reload(config)
        # _env_file=None — don't inherit from a developer's .env (which may
        # legitimately set AUTH_BYPASS=true for local dev). The test must
        # exercise pure-env behaviour.
        s = config.Settings(_env_file=None)
        assert s.bot_token.get_secret_value() == "7123456789:AAFtesttoken"
        assert s.database_url == "postgresql+asyncpg://user:pass@db:5432/kufar"
        assert s.redis_url == "redis://redis:6379/0"
        assert s.api_base_url == "https://kufar-analytics.example.com"
        assert s.mini_app_url == "https://kufar-analytics.example.com/app"


def test_settings_applies_defaults() -> None:
    # Save original env vars
    import os

    _config_keys = [
        "KUFAR_REQUEST_DELAY",
        "KUFAR_PARALLEL_SEMAPHORE",
        "SCHEDULER_SNAPSHOT_HOUR",
        "ALERT_CHECK_INTERVAL",
    ]
    original_env = {}
    for key in _config_keys:
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


def test_auth_bypass_rejected_with_remote_database() -> None:
    """Refuse auth_bypass=True if DATABASE_URL points at a non-local host."""
    env = {
        "BOT_TOKEN": "test",
        "DATABASE_URL": "postgresql+asyncpg://user:pass@db.production.example.com:5432/kufar",
        "REDIS_URL": "redis://redis:6379/0",
        "API_BASE_URL": "https://example.com",
        "MINI_APP_URL": "https://example.com/app",
        "AUTH_BYPASS": "true",
    }
    with patch.dict(os.environ, env, clear=True):
        from api import config

        config.get_settings.cache_clear()
        importlib.reload(config)
        with pytest.raises(ValidationError, match="auth_bypass=True is not allowed"):
            config.Settings(_env_file=None)


def test_auth_bypass_allowed_with_localhost_database() -> None:
    """auth_bypass=True is fine for local dev."""
    env = {
        "BOT_TOKEN": "test",
        "DATABASE_URL": "postgresql+asyncpg://user:pass@localhost:5432/kufar",
        "REDIS_URL": "redis://localhost:6379/0",
        "API_BASE_URL": "https://example.com",
        "MINI_APP_URL": "https://example.com/app",
        "AUTH_BYPASS": "true",
    }
    with patch.dict(os.environ, env, clear=True):
        from api import config

        config.get_settings.cache_clear()
        importlib.reload(config)
        s = config.Settings(_env_file=None)
        assert s.auth_bypass is True


def test_auth_bypass_rejected_with_env_production() -> None:
    """Refuse auth_bypass=True if ENV=production, regardless of DB."""
    env = {
        "BOT_TOKEN": "test",
        "DATABASE_URL": "sqlite+aiosqlite:///test.db",
        "REDIS_URL": "redis://localhost:6379/0",
        "API_BASE_URL": "https://example.com",
        "MINI_APP_URL": "https://example.com/app",
        "AUTH_BYPASS": "true",
        "ENV": " Production ",
    }
    with patch.dict(os.environ, env, clear=True):
        from api import config

        config.get_settings.cache_clear()
        importlib.reload(config)
        with pytest.raises(ValidationError, match="auth_bypass=True is not allowed"):
            config.Settings(_env_file=None)


def test_debug_independent_of_auth_bypass() -> None:
    """debug=True alone must NOT bypass auth — that was the original bug."""
    env = {
        "BOT_TOKEN": "test",
        "DATABASE_URL": "sqlite+aiosqlite:///test.db",
        "REDIS_URL": "redis://localhost:6379/0",
        "API_BASE_URL": "https://example.com",
        "MINI_APP_URL": "https://example.com/app",
        "DEBUG": "true",
        "AUTH_BYPASS": "false",
    }
    with patch.dict(os.environ, env, clear=True):
        from api import config

        config.get_settings.cache_clear()
        importlib.reload(config)
        s = config.Settings(_env_file=None)
        assert s.debug is True
        assert s.auth_bypass is False


def test_remote_redis_requires_auth_in_production() -> None:
    env = {
        "BOT_TOKEN": "test",
        "DATABASE_URL": "postgresql+asyncpg://user:pass@db.production.example.com:5432/kufar",
        "REDIS_URL": "redis://redis.example.com:6379/0",
        "API_BASE_URL": "https://example.com",
        "MINI_APP_URL": "https://example.com/app",
        "ENV": "production",
    }
    with patch.dict(os.environ, env, clear=True):
        from api import config

        config.get_settings.cache_clear()
        importlib.reload(config)
        with pytest.raises(ValidationError, match="Remote Redis requires AUTH"):
            config.Settings(_env_file=None)


def test_local_redis_without_auth_allowed_in_production() -> None:
    env = {
        "BOT_TOKEN": "test",
        "DATABASE_URL": "sqlite+aiosqlite:///test.db",
        "REDIS_URL": "redis://localhost:6379/0",
        "API_BASE_URL": "https://example.com",
        "MINI_APP_URL": "https://example.com/app",
        "ENV": "production",
    }
    with patch.dict(os.environ, env, clear=True):
        from api import config

        config.get_settings.cache_clear()
        importlib.reload(config)
        s = config.Settings(_env_file=None)
        assert s.redis_url == "redis://localhost:6379/0"


def test_remote_ai_base_url_requires_https() -> None:
    env = {
        "BOT_TOKEN": "test",
        "DATABASE_URL": "sqlite+aiosqlite:///test.db",
        "REDIS_URL": "redis://localhost:6379/0",
        "API_BASE_URL": "https://example.com",
        "MINI_APP_URL": "https://example.com/app",
        "AI_BASE_URL": "http://ai.example.com/v1",
    }
    with patch.dict(os.environ, env, clear=True):
        from api import config

        config.get_settings.cache_clear()
        importlib.reload(config)
        with pytest.raises(ValidationError, match="AI service URLs must use HTTPS"):
            config.Settings(_env_file=None)


def test_ai_defaults_match_their_provider() -> None:
    """OPUS-5: the default ai_model (gemini-2.5-flash) is served by
    Google's OpenAI-compat endpoint, not Together AI. The default
    ai_base_url must point at the same provider that serves the
    default model — otherwise a fresh deploy 404s on first call.
    """
    env = {
        "BOT_TOKEN": "test",
        "DATABASE_URL": "sqlite+aiosqlite:///test.db",
        "REDIS_URL": "redis://localhost:6379/0",
        "API_BASE_URL": "https://example.com",
        "MINI_APP_URL": "https://example.com/app",
    }
    with patch.dict(os.environ, env, clear=True):
        from api import config

        config.get_settings.cache_clear()
        importlib.reload(config)
        s = config.Settings(_env_file=None)
        assert s.ai_model == "gemini-2.5-flash"
        assert s.ai_base_url == (
            "https://generativelanguage.googleapis.com/v1beta/openai"
        )


def test_production_policy_gate_defaults() -> None:
    """Wave 180: all four policy-gate fields exist with documented defaults."""
    env = {
        "BOT_TOKEN": "test",
        "DATABASE_URL": "sqlite+aiosqlite:///test.db",
        "REDIS_URL": "redis://localhost:6379/0",
        "API_BASE_URL": "https://example.com",
        "MINI_APP_URL": "https://example.com/app",
    }
    with patch.dict(os.environ, env, clear=True):
        from api import config

        config.get_settings.cache_clear()
        importlib.reload(config)
        s = config.Settings(_env_file=None)
        assert s.security_cache_required is False
        assert s.internal_service_token_required is False
        assert s.ai_audit_required is False
        assert s.allow_legacy_bot_initdata is True
