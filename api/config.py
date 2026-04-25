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
    max_trackers_per_user: int = 50
    debug: bool = False
    db_pool_size: int = 10
    db_max_overflow: int = 20

    # AI Analysis (Together API — OpenAI-compatible)
    ai_api_key: SecretStr | None = None
    ai_base_url: str = "https://api.together.xyz/v1"
    ai_model: str = "google/gemma-4-31B-it"
    ai_max_images: int = 3
    ai_cache_hours: int = 1
    ai_hourly_limit: int = 10
    ai_proxy_url: str | None = None
    ai_analysis_timeout: int = 150
    ai_quick_condition_timeout: int = 45
    ai_photo_precheck_timeout: int = 30
    ai_task_ttl: int = 3600
    ai_export_ttl: int = 900
    ai_fallback_cache_ttl: int = 1800

    # Result size limits
    max_compare_queries: int = 2
    max_deal_ads_per_query: int = 20
    max_opportunity_items: int = 12
    max_opportunity_signals: int = 4
    max_saved_searches_for_board: int = 8
    max_recent_tracker_events: int = 6

    @field_validator("api_base_url", "mini_app_url")
    @classmethod
    def validate_https_url(cls, v: str) -> str:
        v = v.rstrip("/")
        if v.startswith("http://") and not (
            v.startswith("http://127.0.0.1")
            or v.startswith("http://localhost")
            or v.startswith("http://10.0.2.2")
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
