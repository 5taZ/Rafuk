from __future__ import annotations

from functools import lru_cache

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    bot_token: SecretStr
    database_url: str
    redis_url: str
    api_base_url: str = Field(..., description="HTTPS URL for the API base")
    mini_app_url: str = Field(..., description="HTTPS URL for the Telegram Mini App")
    kufar_request_delay: float = 1.0
    kufar_parallel_semaphore: int = 2
    kufar_timeout: float = 15.0
    kufar_max_ads_per_query: int = 5000
    alert_check_interval: int = 30
    cache_ttl_seconds: int = 300
    auto_remove_missing_days: int = 7
    db_pool_size: int = 10
    db_max_overflow: int = 20

    @field_validator("api_base_url", "mini_app_url")
    @classmethod
    def validate_https_url(cls, v: str) -> str:
        v = v.rstrip("/")
        # Allow http:// for localhost/127.0.0.1 (local development)
        if v.startswith("http://") and not (
            v.startswith("http://127.0.0.1")
            or v.startswith("http://localhost")
            or v.startswith("http://10.0.2.2")  # Docker host on Android emulator
        ):
            raise ValueError("URLs must use HTTPS unless using localhost/127.0.0.1")
        if not (v.startswith("https://") or v.startswith("http://")):
            raise ValueError("URLs must start with http:// or https://")
        if v.strip() == "*":
            raise ValueError("Wildcard origins are not allowed")
        return v


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
