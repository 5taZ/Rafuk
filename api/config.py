from __future__ import annotations

import os
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
    # Lowered from 5000 → 1500 because every analytics endpoint
    # (price-stats / listings / segments / geography / price-history)
    # paginates Kufar up to this cap before responding. At size=200
    # per page that's 25 calls per query × 5-6 parallel endpoints =
    # 100+ Kufar round-trips on first search. Median quality from
    # 1500 prices is statistically indistinguishable from 5000, but
    # the wall-clock saving is roughly 3× on broad queries.
    kufar_max_ads_per_query: int = 1500
    alert_check_interval: int = 30
    cache_ttl_seconds: int = 300
    auto_remove_missing_days: int = 7
    max_trackers_per_user: int = 50
    debug: bool = False
    # Telegram initData max age in seconds. Telegram generates initData once
    # when the Mini App opens and never refreshes it — so a short window
    # (e.g. 300s/5min) causes auth failures after the user spends a few
    # minutes in the app.  The HMAC signature already prevents tampering;
    # the max-age only limits replay-window exposure.  7200s (2 hours) is
    # a reasonable balance between security and usability.
    telegram_init_data_max_age: int = 7200

    @field_validator("debug")
    @classmethod
    def _force_debug_off_in_production(cls, v: bool, info) -> bool:
        """Prevent debug=True when connected to a non-localhost database."""
        if not v:
            return False
        if os.getenv("ENV") == "production":
            raise ValueError(
                "debug=True is not allowed when ENV=production. "
                "All requests would share user_id=0."
            )
        db_url = info.data.get("database_url", "")
        if db_url and not any(
            h in db_url
            for h in ("localhost", "127.0.0.1", "10.0.2.2", "::1", "sqlite")
        ):
            raise ValueError(
                "debug=True is not allowed with a non-localhost DATABASE_URL. "
                "All requests would share user_id=0."
            )
        return v
    db_pool_size: int = 10
    db_max_overflow: int = 20

    # AI Analysis (Together API — OpenAI-compatible)
    ai_api_key: SecretStr | None = None
    ai_base_url: str = "https://api.together.xyz/v1"
    ai_model: str = "gemini-3-flash"
    ai_max_images: int = 3
    ai_cache_hours: int = 1
    ai_hourly_limit: int = 10
    ai_daily_limit: int = 50
    ai_proxy_url: str | None = None
    ai_analysis_timeout: int = 150
    ai_quick_condition_timeout: int = 45
    ai_photo_precheck_timeout: int = 30
    ai_task_ttl: int = 3600
    ai_export_ttl: int = 900
    ai_fallback_cache_ttl: int = 1800

    # Result size limits
    max_deal_ads_per_query: int = 20
    max_opportunity_items: int = 12
    max_opportunity_signals: int = 4
    max_saved_searches_for_board: int = 8
    max_recent_tracker_events: int = 6

    # Image proxy (WebP/AVIF transcode of Kufar JPEGs).
    image_proxy_enabled: bool = True
    image_proxy_max_width: int = 1024
    image_proxy_quality: int = 80
    image_proxy_fetch_timeout: float = 5.0
    image_proxy_max_bytes: int = 5 * 1024 * 1024

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
