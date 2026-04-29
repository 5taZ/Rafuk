# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Rafuks — a Telegram Mini App for marketplace analytics on kufar.by. It fetches listing data from Kufar's public JSON API (`api.kufar.by`), computes price statistics, and presents them through a Telegram WebApp frontend. Users can track queries for new listings and price drops, manage a watchlist and deal pipeline.

## Build & Run Commands

```bash
# Install dependencies
uv sync --extra dev

# Start infrastructure (PostgreSQL on :5433, Redis on :6380)
docker compose up -d db redis

# Run all services locally (frontend :8081, API :8010, bot, scheduler)
./start-local.sh
./start-local.sh --with-tunnel   # with Cloudflare tunnel for Telegram

# Stop everything
./stop-local.sh

# Individual services
uv run uvicorn api.main:app --reload --host 0.0.0.0 --port 8010
uv run python -m bot.main
uv run python -m scheduler.collector

# Database migrations (requires db running)
uv run alembic -c migrations/alembic.ini upgrade head

# Tests
uv run pytest
uv run pytest tests/test_specific.py -k "test_name"

# Lint
uv run ruff check .
uv run ruff check --fix .
```

## Architecture

Three long-running services + static frontend. All share the same PostgreSQL database.

1. **API** (`api/`) — FastAPI app on `:8010`. Stateless; all persistence in PostgreSQL. Redis for caching Kufar responses (graceful fallback to in-memory `MemoryCache`). Telegram auth via `X-Telegram-Init-Data` header with HMAC verification (`api/middleware/telegram_auth.py`).

2. **Bot** (`bot/`) — aiogram 3 Telegram bot. Commands: `/app` (open mini app), `/start`. Also handles tracker notification callbacks (inline buttons "В работу"/"Позже"). Shares the same database as the API.

3. **Scheduler** (`scheduler/collector.py`) — APScheduler loop that periodically checks all active trackers, detects new listings and price drops, notifies users via bot, and persists `TrackerEvent` rows.

4. **Frontend** (`frontend/`) — Vanilla JS single-page app served by Nginx container on `:8081`. No build step. Nginx proxies `/api/` to the API backend (`nginx/default.conf`).

### Data Flow

```
Frontend (Telegram WebApp)
  → fetch with X-Telegram-Init-Data header
  → Nginx proxy (/api/ → backend :8010)
  → FastAPI router
  → KufarClient (api.kufar.by/search-rendered-paginated)
  → aggregator computes stats (median, mean, fair range, segments)
  → response back to frontend
```

Tracker flow:
```
Scheduler (every N minutes)
  → loads active Trackers from DB
  → groups by (query, strict_mode)
  → KufarClient.search for each group
  → sync_query_listing_states detects new/dropped listings
  → matches_tracker_filters per tracker
  → persists TrackerEvent rows
  → notifies user via bot.send_message
```

### Key Models (`api/models.py`)

- `Tracker` — user's saved query with filter config (discount %, max price, seller, condition, region, config keyword, exclude duplicates)
- `TrackerEvent` — new_listing or price_drop events for a tracker
- `QuerySnapshot` — periodic price stats snapshot per query
- `QueryListingState` — tracks individual listing lifecycle per query
- `LeadItem` — deal pipeline items (status: new → researching → bought → sold)
- `WatchlistItem` — monitored listings with price tracking and workflow status
- `DealExpense` — expense tracking per lead (delivery, repair, other)

### Key Services (`api/services/`)

- `kufar_client.py` — HTTP client for Kufar API with retry/backoff and rate limiting. Uses params `cur` (not `currency`), `size`, `sort`, `rgn`, `cnd`, `otype`.
- `aggregator.py` — price stats computation (`PriceStats` dataclass), search mode filtering (strict vs loose via `STRICT_VARIANT_TOKENS`), query key building, currency normalization
- `history_service.py` — query snapshot upsert, listing state sync, price change detection
- `reseller_tools.py` — flip estimates, duplicate detection, tracker filter matching, deal score/verdict (verdict is purely price-vs-median based)
- `market_signals.py` — anomaly detection and market signal computation
- `deal_workflow.py` — lead/watchlist CRUD, status transitions, per-item liquidity scoring
- `query_pipeline.py` — `QueryDataset` dataclass and `load_query_dataset()` / `load_segment_datasets()` — shared query parameter parsing across routers
- `listing_mapper.py` — transforms raw Kufar ad dicts into `ListingItem`/`ListingDetailResponse` schemas. Image base URL: `https://rms.kufar.by/v1/gallery/`
- `ai_service.py` — OpenAI-compatible API client (Google Gemini). Gemma 4 uses internal reasoning tokens; `response_format` must NOT be used (causes empty `content`). Reads from `reasoning` field as fallback. Temperature 0.2, max_tokens 2800. 1 image (prefers 2nd/3rd photo over hero shot for better condition assessment). `_parse_json()` handles reasoning chains with balanced-brace extraction.
- `cache.py` — `RedisCache` (primary) and `MemoryCache` (OrderedDict with TTL + LRU eviction, max 500 entries). Both have `get_json`/`set_json` helpers.
- `currency_service.py` — BYN↔USD conversion. All DB prices in BYN.

### Key Routers (`api/routers/`)

- `ai_analysis.py` — `/ai/analyze` (full AI analysis with images + market context). Rate-limited, cached, uses `asyncio.wait_for(timeout=240)`.
- `listings.py` — search listings, cheap deals
- `listing_detail.py` — single listing detail
- `trackers.py` — CRUD for tracker queries
- `workflow.py` — lead/watchlist CRUD and status transitions
- `price_stats.py` — market statistics for a query
- `price_history.py` — time-series price snapshots
- `segments.py` — price segmentation
- `geography.py` — geographic distribution
- `compare.py` — side-by-side query comparison

### Frontend Architecture

**Module pattern**: each JS file exports a factory function (`createXxx`). `app.js` is the entry point:

```
app.js
  → createAppCore()         — state, DOM cache, formatters
  → createAppRenderers()    — composition hub, delegates to sub-modules
  → createAppActions()      — API calls, event binding
```

**Renderer modules** (all instantiated by `createAppRenderers` in `app_renderers.js`):
- `render_core.js` — toast, error bar, loading skeletons, view tabs, panels, summary, helper
- `render_cards.js` — listing cards, deal cards, watchlist cards
- `render_views.js` — view switching, history range buttons, deals hero stats, deal inputs, tracker inputs
- `render_modals.js` — detail modal, expenses modal
- `render_charts.js` — price distribution chart, history chart, profit dashboard, history deals
- `render_trackers.js` — tracker cards, tracker events with virtual scrolling, event filters

**API modules** (all instantiated by `createAppActions` in `app_actions.js`):
- `api_core.js` — HTTP primitives (`getJson`, `postJson`, `deleteJson`), query builder, Telegram headers
- `api_listings.js` — search, listings, detail, segments, geography, history, deals
- `api_trackers.js` — tracker CRUD, event loading
- `api_events.js` — all DOM event binding (click, touch, keyboard)
- `api_watchlist.js` — watchlist CRUD
- `api_leads.js` — lead/deal CRUD
- `api_ai.js` — AI analysis modal with progress animation

**Cross-module communication**: `context._hooks` object shared between renderer modules. Example: `render_core.js` calls `context._hooks.renderChart()` to trigger chart rendering from the charts module.

**State**: `state` object in `app_core.js` is the single source of truth. All DOM updates go through `render*()` functions. Event handlers call API endpoints, update state, then call render functions.

**Views**: overview, ads, tracking, cheap, monitoring, deals — switched via `data-view` tabs.

**Modals**: bottom-sheet style (`detail-sheet`). Body scroll is locked via `document.body.classList.add("modal-open")` when any modal is open. Content scrolls inside `.detail-sheet-content` (detail/expenses modals) or `.ai-modal-body` (AI modal) via flex layout + `overflow-y: auto; -webkit-overflow-scrolling: touch`.

### Kufar API Response Structure

Each ad in the search response is a dict with these notable fields:
- `ad_parameters` — list of `{p, v, vl, pl}` objects for listing attributes (condition, storage, RAM, etc.)
- `account_parameters` — seller info. Private sellers have only `{p: "name", v: "..."}`. Shop sellers have `company_address`, `vat_number`, `contact_person`, etc.
- `feedback_info` — seller rating (often `None` for private sellers; may only populate for shop/company sellers)
- `price_byn`, `price_usd` — prices in kopecks (divide by 100)
- `images` — list of `{path: "...", ...}`, prefix with `https://rms.kufar.by/v1/gallery/`
- `company_ad` — boolean, true for shop listings

### Dependency Injection

FastAPI dependencies in `api/dependencies.py`:
- `get_session_factory_dependency` — pulls `session_factory` from `app.state` (set in lifespan), falls back to creating a new engine
- `get_telegram_user` — validates `X-Telegram-Init-Data` header, returns `TelegramInitData` with `user_id`. In debug mode (`debug=true` in `.env`), allows requests without Telegram initData (returns mock user with `user_id=0`). CORS also allows `localhost:8081`/`localhost:8010` in debug mode.
- `get_cache` / `get_currency_service` — from `app.state`

All user-scoped endpoints require `get_telegram_user`; public endpoints (price-stats, listings, health) do not. Debug mode bypasses Telegram auth for browser testing.

## Configuration

Environment variables loaded from `.env` via pydantic-settings (`api/config.py`):

- `BOT_TOKEN` — Telegram bot token (also used for initData verification)
- `DATABASE_URL` — asyncpg PostgreSQL connection string (local: `localhost:5433`, Docker: internal `5432`)
- `REDIS_URL` — Redis connection string (local: `localhost:6380/0`)
- `API_BASE_URL` — public API URL
- `MINI_APP_URL` — public frontend URL (set by cloudflared tunnel in dev)
- `KUFAR_REQUEST_DELAY` — delay between Kufar API calls (default 1.0s)
- `KUFAR_PARALLEL_SEMAPHORE` — max parallel Kufar requests (default 3)
- `ALERT_CHECK_INTERVAL` — scheduler tracker check interval in minutes (default 30)
- `AI_API_KEY` — Google Gemini API key (SecretStr)
- `AI_BASE_URL` — OpenAI-compatible API base URL (default: `https://generativelanguage.googleapis.com/v1beta/openai`)
- `AI_MODEL` — model ID (default: `google/gemma-4-31B-it`)
- `AI_PROXY_URL` — optional HTTP proxy for AI API calls

Docker Compose maps PostgreSQL `5432→5433` and Redis `6379→6380` to avoid conflicts with local installs.

## Conventions

- Python: ruff linting (E/W/F/I/N/UP/B/SIM/TCH), line length 99, isort with known-first-party `[api, bot, scheduler]`
- Async everywhere: SQLAlchemy async sessions, httpx async client, aiogram 3
- Frontend: no framework, no build step, vanilla JS with CSS custom properties for dark/light theming via `data-theme` attribute
- XSS prevention: use `escapeHtml()` for text content, `safeUrl()` for `href`/`src` attributes (blocks `javascript:`, `data:` schemes)
- Modal scroll: `body.modal-open` locks page scroll; modal content scrolls inside `.detail-sheet-content` or `.ai-modal-body`
- Toast notifications: 3000ms auto-dismiss, created via `showToast()` in render_core.js
- All user-scoped endpoints require `X-Telegram-Init-Data` header; public endpoints do not
- Currency: all DB values in BYN. API returns in requested currency. Frontend converts using `state.usdRateByn`
- AI model (Gemma 4): does NOT support `response_format: {"type": "json_object"}` — causes empty `content`. Uses `reasoning` field for chain-of-thought. `_parse_json()` extracts JSON from either field
- Nginx `proxy_read_timeout: 300s` — AI analysis can take 30-60 seconds; must not be lower
- AI analysis context: enriched with anomaly flags, deal score, full parameters, price deltas from alternatives. System prompt includes scam detection checklist and BYN-specific negotiation guidance per category
- Frontend context sharing: `createAppRenderers` must both destructure AND return functions like `safeUrl` in its public API, otherwise downstream modules (`createApiAi`) get `undefined`

## Known Issues

- `ruff UP017` suggests `datetime.UTC` but this does not exist on the `datetime` class — use `timezone.utc` and ignore UP017
- Gemma 4 on Google Gemini uses internal reasoning tokens that consume output budget — `max_tokens` must be ≥2800 for analysis prompts
- CSS `display: flex/grid` overrides HTML `hidden` attribute — always add `[hidden] { display: none !important }` rules for elements that use both flex layout and `hidden`

## Skill routing

When the user's request matches an available skill, invoke it via the Skill tool. When in doubt, invoke the skill.

Key routing rules:
- Product ideas/brainstorming → invoke /office-hours
- Strategy/scope → invoke /plan-ceo-review
- Architecture → invoke /plan-eng-review
- Design system/plan review → invoke /design-consultation or /plan-design-review
- Full review pipeline → invoke /autoplan
- Bugs/errors → invoke /investigate
- QA/testing site behavior → invoke /qa or /qa-only
- Code review/diff check → invoke /review
- Visual polish → invoke /design-review
- Ship/deploy/PR → invoke /ship or /land-and-deploy
- Save progress → invoke /context-save
- Resume context → invoke /context-restore
