from __future__ import annotations

from functools import lru_cache

from pydantic import Field
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
    kufar_request_delay: float = 0.5
    kufar_parallel_semaphore: int = 3
    kufar_timeout: float = 15.0
    alert_check_interval: int = 30
    cache_ttl_seconds: int = 300
    # Maximum age of Telegram initData (auth_date) in seconds; 0 disables the age check.
    init_data_max_age_seconds: int = 86400
    # Serve /docs, /redoc and /openapi.json; compose turns them off for the containerized API.
    api_docs_enabled: bool = True
    # Requests per minute one client (Telegram user or address) may send to /api/ in total,
    # across all routes. 0 derives it from KUFAR_REQUEST_DELAY (see api/rate_limit.py).
    api_rate_limit_per_minute: int = Field(default=0, ge=0)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


class _SettingsProxy:
    def __getattr__(self, name: str) -> object:
        return getattr(get_settings(), name)


settings = _SettingsProxy()
