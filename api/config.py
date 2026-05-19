from __future__ import annotations

import ipaddress
from functools import lru_cache
from urllib.parse import urlparse

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _is_local_database_url(db_url: str) -> bool:
    """BE-M15: return True if ``db_url`` points at the local host.

    The previous implementation relied on substring matching against
    ``("localhost", "127.0.0.1", "10.0.2.2", "::1", "sqlite")`` which
    has two problems: it falsely accepts hostile hosts that *contain*
    one of those tokens (e.g. ``db.localhost.attacker.com``,
    ``user:pass@127.0.0.1.evil.example`` or a DSN whose password
    happens to spell ``sqlitebackup``) and it falsely rejects valid
    private-network DSNs (``192.168.x.x``, ``10.x.x.x`` outside the
    Android-emulator gateway, IPv6 ULA). Parse the URL and inspect the
    real host instead.

    Accepts:
    * SQLite URLs (no host component)
    * Hostname ``localhost``
    * IPv4/IPv6 loopback (``127.0.0.0/8`` and ``::1``)
    * ``10.0.2.2`` — the Android-emulator host gateway, kept as a
      named exception for the existing local-dev workflow.
    """
    if not db_url:
        return False
    if db_url.startswith("sqlite"):
        return True
    try:
        parsed = urlparse(db_url)
    except ValueError:
        return False
    host = (parsed.hostname or "").lower()
    if not host:
        return False
    if host in ("localhost", "10.0.2.2"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _is_local_url(value: str) -> bool:
    try:
        parsed = urlparse(value)
    except ValueError:
        return False
    host = (parsed.hostname or "").lower()
    if not host or host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    env: str = "development"
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
    # `debug` controls non-security knobs only (extra dev origins in CORS,
    # verbose logging, etc.). Auth bypass is a SEPARATE flag — historically
    # `debug=True` would also let any LAN host hit the API as user_id=0,
    # which silently turned every "dev convenience" toggle into an auth-
    # bypass switch on misconfigured boxes.
    debug: bool = False
    # auth_bypass=True allows requests without a valid Telegram initData
    # header to be treated as a synthetic Debug user (user_id=0). Use this
    # ONLY for local manual testing. The validators below refuse to load
    # this in production / against a remote DB.
    auth_bypass: bool = False
    # Telegram initData max age in seconds. Telegram generates initData once
    # when the Mini App opens and never refreshes it — so a short window
    # (e.g. 300s/5min) causes auth failures after the user spends a few
    # minutes in the app.  The HMAC signature already prevents tampering;
    # the max-age only limits replay-window exposure.  7200s (2 hours) is
    # a reasonable balance between security and usability.
    telegram_init_data_max_age: int = 7200

    @field_validator("env", mode="before")
    @classmethod
    def _normalize_env(cls, v: object) -> str:
        return str(v or "development").strip().lower() or "development"

    @field_validator("debug")
    @classmethod
    def _force_debug_off_in_production(cls, v: bool, info) -> bool:
        """Prevent debug=True when connected to a non-localhost database."""
        if not v:
            return False
        if info.data.get("env") == "production":
            raise ValueError(
                "debug=True is not allowed when ENV=production."
            )
        db_url = info.data.get("database_url", "")
        if db_url and not _is_local_database_url(db_url):
            raise ValueError(
                "debug=True is not allowed with a non-localhost DATABASE_URL."
            )
        return v

    @field_validator("auth_bypass")
    @classmethod
    def _force_auth_bypass_off_in_production(cls, v: bool, info) -> bool:
        """Refuse auth_bypass=True outside local development.

        With auth_bypass on, every unauthenticated request becomes user_id=0,
        which means *all such requests share the same fake user* — anyone on
        the same network can read/write the Debug user's state. This must
        never reach production or a remote DB.
        """
        if not v:
            return False
        if info.data.get("env") == "production":
            raise ValueError(
                "auth_bypass=True is not allowed when ENV=production."
            )
        db_url = info.data.get("database_url", "")
        if db_url and not _is_local_database_url(db_url):
            raise ValueError(
                "auth_bypass=True is not allowed with a non-localhost "
                "DATABASE_URL — every request would share user_id=0."
            )
        return v
    # Production policy gates (G-03/G-04/G-05). Defaults are
    # backwards-compatible (lax); production deployments should set the
    # first three to True and the last to False via env.
    security_cache_required: bool = Field(default=False)
    internal_service_token_required: bool = Field(default=False)
    ai_audit_required: bool = Field(default=False)
    allow_legacy_bot_initdata: bool = Field(default=True)

    # Connection-pool sizing.
    #
    # The API runs multiple uvicorn workers (PERF-H1, see Dockerfile —
    # WORKERS=4 by default). Postgres ships with `max_connections=100`
    # so we must size the pool such that
    #   workers × (pool_size + max_overflow) <= ~80 (leaves headroom
    #   for psql / migrations / backups).
    # 4 workers × (5 + 10) = 60 — comfortably under the limit while
    # giving each worker enough sockets for the request fan-out.
    # If you bump WORKERS above 4 OR raise Postgres max_connections,
    # adjust these accordingly (or move to PgBouncer for the next
    # tier — DB-MED-2).
    db_pool_size: int = 5
    db_max_overflow: int = 10

    # AI Analysis (OpenAI-compatible chat completions).
    # OPUS-5: defaults point at Google's Gemini OpenAI-compat endpoint
    # because the default ``ai_model`` is ``gemini-2.5-flash`` —
    # Together AI does not serve that model, so the previous
    # together.xyz default would 404 the moment a fresh deployment
    # tried to call it. Switch the default base URL to match the
    # default model. Together remains a one-line override in
    # ``.env`` (set both AI_BASE_URL and AI_MODEL together).
    ai_api_key: SecretStr | None = None
    ai_base_url: str = "https://generativelanguage.googleapis.com/v1beta/openai"
    ai_model: str = "gemini-2.5-flash"
    ai_provider_name: str | None = None
    ai_provider_region: str | None = None
    ai_max_images: int = 3
    ai_cache_hours: int = 1
    ai_hourly_limit: int = 10
    ai_daily_limit: int = 50
    ai_proxy_url: str | None = None
    ai_analysis_timeout: int = 150
    ai_quick_condition_timeout: int = 45
    ai_photo_precheck_timeout: int = 30
    ai_photo_precheck_enabled: bool = False
    ai_chat_min_interval_seconds: float = 12.0
    ai_task_ttl: int = 3600
    ai_fallback_cache_ttl: int = 1800

    # Result size limits
    max_deal_ads_per_query: int = 20
    max_opportunity_items: int = 12
    max_opportunity_signals: int = 4
    max_saved_searches_for_board: int = 8
    max_recent_tracker_events: int = 6

    # OPUS-12: internal service-to-service authentication. Lets the
    # bot call the API for arbitrary telegram_user_ids without
    # forging initData with the bot token. When unset, the bot
    # falls back to the legacy initData-forge path with a
    # deprecation warning so existing deployments keep working.
    internal_service_token: SecretStr | None = None
    # SEC-NEW-8: HMAC key for AI audit log hashes. Falls back to
    # BOT_TOKEN-derived key when unset.
    audit_hash_secret: SecretStr | None = Field(default=None, alias="AUDIT_HASH_SECRET")
    admin_telegram_user_ids: str = ""

    # Image proxy (WebP/AVIF transcode of Kufar JPEGs).
    image_proxy_enabled: bool = True
    image_proxy_max_width: int = 1024
    image_proxy_quality: int = 80
    image_proxy_fetch_timeout: float = 5.0
    image_proxy_max_bytes: int = 5 * 1024 * 1024
    metrics_bearer_token: SecretStr | None = None
    api_max_body_bytes: int = 8 * 1024 * 1024

    @field_validator("redis_url")
    @classmethod
    def validate_production_redis_auth(cls, v: str, info) -> str:
        if info.data.get("env") != "production" or _is_local_url(v):
            return v
        parsed = urlparse(v)
        if parsed.scheme in {"redis", "rediss"} and parsed.password is None:
            raise ValueError("Remote Redis requires AUTH password when ENV=production")
        return v

    @field_validator("ai_base_url")
    @classmethod
    def validate_ai_transport_url(cls, v: str) -> str:
        if not v:
            return v
        parsed = urlparse(v)
        if parsed.scheme == "http" and not _is_local_url(v):
            raise ValueError("AI service URLs must use HTTPS unless using localhost")
        if parsed.scheme not in {"http", "https"}:
            raise ValueError("AI service URLs must start with http:// or https://")
        return v.rstrip("/")

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


def is_production_like_deployment(settings: Settings) -> bool:
    if settings.env == "production":
        return True
    return not _is_local_database_url(settings.database_url)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
