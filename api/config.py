from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    bot_token: str
    database_url: str
    redis_url: str
    api_base_url: str
    mini_app_url: str
    kufar_request_delay: float = 1.0
    kufar_parallel_semaphore: int = 3
    kufar_timeout: float = 15.0
    scheduler_snapshot_hour: int = 9
    alert_check_interval: int = 30
    cache_ttl_seconds: int = 300
    auto_remove_missing_days: int = 7


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


class _SettingsProxy:
    def __getattr__(self, name: str) -> object:
        return getattr(get_settings(), name)


settings = _SettingsProxy()
