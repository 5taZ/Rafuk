# Architecture

**Analysis Date:** 2026-04-22

## Pattern Overview

**Overall:** Service-oriented monolith with three independent long-running processes sharing a single PostgreSQL database. The frontend is a server-rendered vanilla JS single-page application.

**Key Characteristics:**
- Three autonomous services (API, Bot, Scheduler) with shared data layer but separate processes
- Stateless API layer: all persistence delegated to PostgreSQL, Redis used only for caching
- Module pattern frontend: factory functions (`createXxx`) with dependency injection via context objects
- Telegram-first authentication: HMAC-verified `initData` from Telegram WebApp SDK
- All prices stored and computed in BYN; currency conversion happens at the response boundary

## Layers

### Frontend Layer
- Purpose: Telegram Mini App UI — search, analytics, deal pipeline, tracker management
- Location: `frontend/js/`, `frontend/css/`, `frontend/index.html`
- Contains: 18 JS modules (factory functions), 1 CSS file with CSS custom properties for theming
- Depends on: API via Nginx reverse proxy (`/api/` -> `:8010`)
- Used by: End users through Telegram WebApp

### API Layer
- Purpose: REST API serving listing data, analytics, tracker CRUD, deal workflow, AI analysis
- Location: `api/`
- Contains: FastAPI app, routers, services, middleware, schemas, ORM models
- Depends on: PostgreSQL (asyncpg), Redis (cache), Kufar public API (httpx), Together AI API (httpx), NBRB API (httpx)
- Used by: Frontend (via Nginx), Bot (shared DB models), Scheduler (shared services and models)

### Bot Layer
- Purpose: Telegram bot commands (`/app`, `/start`) and tracker alert callback handling
- Location: `bot/`
- Contains: aiogram 3 dispatcher, command handlers, inline keyboard builders
- Depends on: PostgreSQL (shared with API), Telegram Bot API
- Used by: End users via Telegram

### Scheduler Layer
- Purpose: Periodic tracker checking, new listing detection, price drop notification, daily cleanup
- Location: `scheduler/collector.py`
- Contains: APScheduler jobs (`check_trackers`, `run_cleanup`), notification formatting, event persistence
- Depends on: PostgreSQL, Kufar public API, Telegram Bot API (for sending notifications)
- Used by: Internal (cron-like background process)

### Data Layer
- Purpose: Persistent storage for users, trackers, listings, events, leads, watchlist
- Location: `api/models.py` (SQLAlchemy ORM), `migrations/` (Alembic)
- Contains: 10 ORM models with mixins, 18 migration files
- Depends on: PostgreSQL 16
- Used by: All three services (API, Bot, Scheduler)

### Infrastructure Layer
- Purpose: Request routing, caching, currency rates, rate limiting
- Location: `nginx/default.conf`, `api/services/cache.py`, `api/services/currency_service.py`, `api/limiter.py`
- Contains: Nginx reverse proxy config, Redis cache with in-memory fallback, NBRB rate fetching, SlowAPI rate limiter
- Depends on: Nginx, Redis, NBRB public API
- Used by: API layer

## Data Flow

### Search & Analytics Flow
1. User enters query in Telegram WebApp frontend
2. Frontend sends `fetch /api/v1/listings?query=...` with `X-Telegram-Init-Data` header
3. Nginx proxies request to FastAPI backend on port 8010
4. `get_telegram_user` dependency validates Telegram HMAC signature
5. Router calls `load_query_dataset()` which creates a `KufarClient` and fetches from `api.kufar.by`
6. `aggregator.py` normalizes prices (kopecks -> BYN), removes outliers (IQR), applies strict/loose search mode
7. `listing_mapper.py` builds response schemas with deal scores, liquidity, flip estimates
8. Response cached in Redis (fallback: `MemoryCache`), returned as JSON
9. Frontend receives data, updates `state` object, calls `render*()` functions to update DOM

### Tracker Notification Flow
1. APScheduler triggers `check_trackers` every N minutes (configurable, default 5-min tick, per-tracker interval respected)
2. Scheduler loads active, non-paused trackers from DB, groups by `(query, strict_mode)`
3. For each group: `KufarClient.search()` fetches current listings
4. `sync_query_listing_states()` compares current vs known listings, detects new/removed/price-dropped
5. `matches_tracker_filters()` applies per-tracker filters (discount %, price cap, seller type, condition, region, config keyword)
6. Matching events persisted as `TrackerEvent` rows
7. Bot sends Telegram message with inline keyboard ("Open query" / "Open listing" / "To work" / "Later")
8. User callback handlers ("To work"/"Later") save to leads via `workflow_store.upsert_lead()`

### AI Analysis Flow
1. User clicks "AI analysis" button on listing detail modal
2. Frontend calls `POST /api/v1/ai/analyze` with `ad_id` and `query`
3. Router fetches listing detail from Kufar, builds market context (median, Q1/Q3, alternatives)
4. `AIService.analyze_listing()` builds category-aware system prompt with negotiation hints
5. Downloads up to 1 listing image (prefers 2nd/3rd photo for better condition assessment)
6. Sends to Together AI API (OpenAI-compatible) with `google/gemma-4-31B-it` model
7. Response parsed from `content` or `reasoning` field (Gemma 4 quirk: may put JSON in reasoning)
8. JSON extracted via balanced-brace algorithm, returned to frontend
9. Nginx `proxy_read_timeout: 300s` accommodates 30-60s AI response time

### State Management (Frontend)
- Single `state` object in `createAppCore()` (`app_core.js`) is the source of truth
- All DOM updates go through `render*()` functions in renderer modules
- Event handlers call API endpoints, update `state`, then call render functions
- Dirty-view tracking via `markDirty()`/`isDirty()` prevents unnecessary re-renders
- Cross-module communication: `context._hooks` object shared between renderer modules

## Key Abstractions

### QueryDataset
- Purpose: Encapsulates a search query result with lazy-computed price statistics
- Examples: `api/services/query_pipeline.py`
- Pattern: Dataclass with cached properties (`prices_byn`, `price_stats`, `total_results`)
- Created by `load_query_dataset()` and `load_segment_datasets()` — shared across all routers

### CacheBackend Protocol
- Purpose: Cache abstraction supporting Redis (primary) and in-memory (fallback)
- Examples: `api/services/cache.py`
- Pattern: Protocol class with `get`/`set`/`get_json`/`set_json`/`ping` methods
- `RedisCache` wraps `redis.asyncio.Redis`; `MemoryCache` uses `OrderedDict` with TTL + LRU eviction (max 500 entries)

### Listing Mapper
- Purpose: Transforms raw Kufar API ad dicts into Pydantic response schemas
- Examples: `api/services/listing_mapper.py`
- Pattern: Pure functions `build_listing_item()` and `build_listing_detail()` that take raw ad + market context and return typed schemas
- Strips PII fields (`PII_PARAMETER_KEYS`), builds image URLs from paths

### Deal Scoring
- Purpose: Compute a 0-100 score and verdict for a listing based on price position, freshness, and seller type
- Examples: `api/services/reseller_tools.py`
- Pattern: Configurable via `_ScoringConfig` frozen dataclass. Score starts at 50, modified by price delta, seller type, freshness, config match, and anomaly flags
- Verdicts: "Good price", "Below market", "Fair market", "Above market" (purely price-vs-median driven)

### Frontend Module Pattern
- Purpose: Encapsulate related UI logic into composable factory functions
- Examples: `frontend/js/render_core.js`, `frontend/js/api_listings.js`, etc.
- Pattern: Each file exports a `createXxx(context)` function. `app_renderers.js` and `app_actions.js` are composition hubs that instantiate sub-modules and return aggregated API
- Context sharing: modules receive shared `context` with `state`, `elements`, utility functions, and `_hooks` for cross-module calls

## Entry Points

### API Server
- Location: `api/main.py`
- Triggers: `uvicorn api.main:app --host 0.0.0.0 --port 8010`
- Responsibilities: Creates FastAPI app, registers routers under `/api/v1`, manages lifespan (engine, cache, currency service), adds CORS/security headers middleware

### Bot Process
- Location: `bot/main.py`
- Triggers: `python -m bot.main`
- Responsibilities: Creates aiogram Dispatcher, registers command routers, starts polling loop

### Scheduler Process
- Location: `scheduler/collector.py`
- Triggers: `python -m scheduler.collector`
- Responsibilities: Creates APScheduler, registers `check_trackers` (interval) and `run_cleanup` (cron) jobs, runs periodic DB health checks

### Frontend Entry
- Location: `frontend/js/app.js`
- Triggers: Browser loads `index.html` -> `DOMContentLoaded` event
- Responsibilities: Creates core/renderers/actions, initializes Telegram theme, loads recent searches, binds events, triggers initial data loads

### Docker Entrypoint
- Location: `Dockerfile`
- Triggers: `docker compose up`
- Responsibilities: Single Dockerfile with `SERVICE` env var to select api/bot/scheduler. Each runs via `uv run`

## Error Handling

**Strategy:** Layered with graceful degradation

**Patterns:**
- API: Global exception handler returns JSON `{"detail": "..."}` for all unhandled exceptions (`api/main.py` line 103-113)
- Kufar client: Exponential backoff retry (3 attempts, 1s base) in `KufarClient.search()`
- Cache: Redis failures silently fall back to `MemoryCache` at startup (`api/main.py` line 57-58); individual get/set failures logged and return `None`
- Currency: NBRB API failures fall back to hardcoded `DEFAULT_USD_RATE = 3.0`
- AI: Empty `content` field handled by reading `reasoning` field; non-JSON responses return `{"summary": text}`; vision failures retry text-only
- Bot: `TelegramForbiddenError` auto-deactivates trackers for that user
- Scheduler: Per-query errors logged and skipped; partial results committed (`session.commit()` after all groups processed)
- Frontend: `AbortController` with 90s timeout; offline/online detection banner; toast notifications for errors

## Cross-Cutting Concerns

**Logging:** Python `logging` module. `logging.basicConfig(level=logging.INFO)` in bot and scheduler. API uses per-module loggers.

**Validation:** Pydantic schemas for all API request/response bodies. Query length capped at 255 chars (`api/validators.py`). Telegram initData HMAC verification. HTTPS URL validation in config.

**Authentication:** Telegram WebApp `initData` verified via HMAC-SHA256 with bot token. Debug mode allows unauthenticated access (returns mock user with `user_id=0`). User-scoped endpoints require `Depends(get_telegram_user)`; public endpoints (health, price-stats, listings, currency-rates) do not.

**Rate Limiting:** SlowAPI with per-user keys (Telegram `user_id` when available, client IP as fallback). Configured in `api/limiter.py`. AI analysis has separate hourly limit via cache-based counter.

**Security Headers:** `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: strict-origin-when-cross-origin` added to all API responses. PII stripped from listing responses (`PII_PARAMETER_KEYS`). XSS prevention via `escapeHtml()` and `safeUrl()` in frontend.

**CORS:** Configured for mini app URL + API base URL. Debug mode adds localhost origins.

---

*Architecture analysis: 2026-04-22*
