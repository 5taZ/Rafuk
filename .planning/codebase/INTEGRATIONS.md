# External Integrations

**Analysis Date:** 2026-04-22

## APIs & External Services

**Marketplace Data (Kufar.by):**
- Service: Kufar Search API (`api.kufar.by`)
  - SDK/Client: Custom `KufarClient` class in `api/services/kufar_client.py`
  - Endpoint: `https://api.kufar.by/search-api/v2/search/rendered-paginated`
  - Auth: None (public API, uses User-Agent header)
  - Features: Paginated search with cursor, retry/backoff (3 attempts, exponential backoff), rate limiting via configurable delay (`KUFAR_REQUEST_DELAY`)
  - Params: `query`, `size`, `cur` (currency), `sort`, `rgn` (region), `cnd` (condition), `cat` (category)
  - Pagination: Up to 25 pages or `KUFAR_MAX_ADS_PER_QUERY` (default 5000) per query
  - Note: `otype` parameter removed (422 since 2026); seller type filtered client-side

**AI Analysis (Together AI):**
- Service: Together AI — OpenAI-compatible chat completions API
  - SDK/Client: Custom `AIService` class in `api/services/ai_service.py`
  - Endpoint: `https://api.together.xyz/v1/chat/completions` (configurable via `AI_BASE_URL`)
  - Auth: `AI_API_KEY` (SecretStr in `.env`)
  - Model: `google/gemma-4-31B-it` (configurable via `AI_MODEL`)
  - Features: Vision (image analysis), listing analysis with market context, quick condition assessment
  - Proxy: Optional SOCKS proxy via `AI_PROXY_URL` (uses `httpx-socks`)
  - Rate limit: Per-user hourly limit (`AI_HOURLY_LIMIT`, default 10), cached results bypass limit
  - Cache: Results cached for 1 hour (`AI_CACHE_HOURS`) in Redis/memory
  - Quirks: Gemma 4 uses internal reasoning tokens; `response_format` must NOT be used (causes empty `content`). Output may be in `reasoning` field instead of `content`. `max_tokens` must be >= 2800 for analysis prompts.
  - Image handling: Downloads images from Kufar CDN, compresses to max 768px via Pillow, sends as base64 vision content. Prefers 2nd/3rd photo over hero shot.

**Currency Rates (NBRB):**
- Service: National Bank of the Republic of Belarus
  - SDK/Client: `CurrencyService` in `api/services/currency_service.py`
  - Endpoint: `https://api.nbrb.by/exrates/rates?periodicity=0`
  - Auth: None (public API)
  - Features: BYN to USD conversion, fallback rate of 3.0 if API unavailable
  - Cache: Rates cached for 1 hour (TTL 3600s) in Redis/memory
  - Used by: All price endpoints for BYN/USD conversion; frontend uses `state.usdRateByn`

**Telegram Bot Platform:**
- Service: Telegram Bot API via aiogram 3
  - SDK/Client: aiogram 3 (`bot/main.py`)
  - Auth: `BOT_TOKEN` (SecretStr in `.env`)
  - Features: `/app` command (opens mini app), `/start` command, inline keyboard callbacks for tracker alerts
  - Notification: `scheduler/collector.py` sends messages via `bot.send_message()` for tracker events (new listings, price drops)
  - Bot commands configured via `bot.set_my_commands()`

**Telegram WebApp SDK:**
- Service: Telegram WebApp for Mini Apps
  - SDK/Client: `telegram-web-app.js` loaded from `https://telegram.org/js/telegram-web-app.js` in `frontend/index.html`
  - Auth: `window.Telegram.WebApp.initData` sent as `X-Telegram-Init-Data` header to API (`frontend/js/api_core.js`)
  - Features: Theme integration (`Telegram.WebApp.themeParams`), color scheme detection, launch params parsing

**Cloudflare Tunnel (dev only):**
- Service: Cloudflare Tunnel (`cloudflared`)
  - Used in: `start-local.sh --with-tunnel` for exposing localhost:8081 to public HTTPS
  - Purpose: Telegram requires public HTTPS URL for Mini App; tunnel provides `*.trycloudflare.com` URL
  - Not used in production (real domain/SSL expected)

## Data Storage

**Databases:**
- PostgreSQL 16 (Alpine)
  - Connection: `DATABASE_URL` env var (format: `postgresql+asyncpg://user:pass@db:5432/kufar`)
  - Local port: 5433 (mapped from container 5432 to avoid conflicts)
  - Client: SQLAlchemy async with asyncpg driver (`api/database.py`)
  - ORM: SQLAlchemy 2.0 declarative models (`api/models.py`)
  - Migrations: Alembic (`migrations/alembic.ini`, `migrations/versions/`)
  - Pool: `db_pool_size=10`, `db_max_overflow=20`, `pool_pre_ping=True`, `pool_recycle=1800`
  - Tables: `users`, `trackers`, `tracker_events`, `query_snapshots`, `query_listing_states`, `lead_items`, `watchlist_items`, `saved_searches`, `deal_expenses`, `contacts`

**File Storage:**
- Local filesystem only
  - Docker volume `pgdata` for PostgreSQL data persistence
  - Frontend static files served from `frontend/` directory
  - Assets (logo) in `assets/` directory

**Caching:**
- Redis 7 (Alpine)
  - Connection: `REDIS_URL` env var (format: `redis://redis:6379/0`)
  - Local port: 6380 (mapped from container 6379 to avoid conflicts)
  - Client: `redis[hiredis]` async via `RedisCache` class (`api/services/cache.py`)
  - Fallback: `MemoryCache` (OrderedDict with TTL + LRU, max 500 entries) used if Redis is unavailable
  - Cache backends implement `CacheBackend` protocol: `get`, `set`, `get_json`, `set_json`, `ping`
  - Cached data: Kufar API responses, currency rates, AI analysis results, rate limit counters

## Authentication & Identity

**Auth Provider:**
- Telegram WebApp initData verification (custom implementation)
  - Implementation: `api/middleware/telegram_auth.py` — HMAC-SHA256 verification
  - Flow: Frontend sends `X-Telegram-Init-Data` header with `Telegram.WebApp.initData`; backend verifies signature using `BOT_TOKEN` as secret key
  - Returns: `TelegramInitData` dataclass with `user_id`, `first_name`, `raw` parsed fields
  - Dependency: `get_telegram_user` in `api/dependencies.py` validates on every user-scoped endpoint
  - Debug mode: When `debug=true`, allows requests without initData (returns mock `user_id=0`)
  - Public endpoints (no auth required): price-stats, listings, currency-rates, health

## Monitoring & Observability

**Error Tracking:**
- None (no Sentry, Datadog, or similar)

**Logs:**
- Python `logging` module (stdlib) — `logging.basicConfig(level=logging.INFO)`
- Scheduler uses structured format: `%(asctime)s [%(levelname)s] %(name)s: %(message)s`
- Per-service log files in `.run/` directory when using `start-local.sh`

**Health Checks:**
- `GET /api/v1/health/ready` — API readiness (used in Docker healthcheck)
- Scheduler: periodic DB health check every 5 minutes (`check_db_health()` in `scheduler/collector.py`)
- Docker healthchecks for all services (api, frontend, db, redis)

## CI/CD & Deployment

**Hosting:**
- Docker Compose (all services in containers)

**CI Pipeline:**
- GitHub Actions (`.github/workflows/ci.yml`)
  - **Lint job**: ruff check + ruff format check
  - **Test job**: PostgreSQL service container, Alembic migrations, pytest with coverage, Codecov upload
  - **Docker build job**: Build image on push to main (no push to registry)
  - Triggers: push to `main`/`develop`, PRs to `main`

## Environment Configuration

**Required env vars:**
- `BOT_TOKEN` — Telegram bot token (also used for initData HMAC verification)
- `DATABASE_URL` — PostgreSQL asyncpg connection string
- `REDIS_URL` — Redis connection string
- `API_BASE_URL` — Public API URL (HTTPS required in production)
- `MINI_APP_URL` — Public frontend URL (HTTPS required in production)

**Optional env vars:**
- `AI_API_KEY` — Together AI API key (required for AI analysis feature)
- `AI_BASE_URL` — AI API base URL (default: `https://api.together.xyz/v1`)
- `AI_MODEL` — AI model ID (default: `google/gemma-4-31B-it`)
- `AI_PROXY_URL` — HTTP/SOCKS proxy for AI API calls
- `KUFAR_REQUEST_DELAY` — Delay between Kufar API calls (default: 1.0s)
- `KUFAR_PARALLEL_SEMAPHORE` — Max parallel Kufar requests (default: 2)
- `ALERT_CHECK_INTERVAL` — Scheduler tick interval in minutes (default: 30)
- `DEBUG` — Debug mode flag (bypasses Telegram auth)
- `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD` — PostgreSQL container config

**Secrets location:**
- `.env` file at project root (not committed, listed in `.gitignore`)
- `.env.example` template committed with placeholder values

## Webhooks & Callbacks

**Incoming:**
- None (Telegram bot uses long-polling via `dispatcher.start_polling(bot)`, not webhooks)

**Outgoing:**
- Telegram `bot.send_message()` — Tracker alert notifications with inline keyboards (`scheduler/collector.py`)
- Inline keyboard callbacks: "В работу" / "Позже" buttons on tracker alerts (`bot/handlers/tracker.py`)

---

*Integration audit: 2026-04-22*
