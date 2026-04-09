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

4. **Frontend** (`frontend/`) — Vanilla JS single-page app served by Nginx container on `:8081`. No build step. Four files:
   - `app_core.js` — state object, DOM element cache, formatters, Telegram theme init
   - `app_renderers.js` — all DOM rendering functions, Chart.js charts, toast notifications
   - `app_actions.js` — API calls, event binding, user actions
   - `app.js` — entry point that wires everything together

### Data Flow

```
Frontend (Telegram WebApp)
  → fetch with X-Telegram-Init-Data header
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
- `SavedSearch` — saved search groups with filter config
- `DealExpense` — expense tracking per lead (delivery, repair, other)

### Key Services (`api/services/`)

- `kufar_client.py` — HTTP client for Kufar API with retry/backoff and rate limiting. Uses params `cur` (not `currency`), `size`, `sort`, `rgn`, `cnd`, `otype`.
- `aggregator.py` — price stats computation (`PriceStats` dataclass), search mode filtering (strict vs loose via `STRICT_VARIANT_TOKENS`), query key building, currency normalization
- `history_service.py` — query snapshot upsert, listing state sync, price change detection
- `reseller_tools.py` — flip estimates, duplicate detection, tracker filter matching, deal score/verdict (verdict is purely price-vs-median based)
- `market_signals.py` — market signal computation for opportunity board, anomaly detection
- `deal_workflow.py` — lead/watchlist CRUD, status transitions, per-item liquidity scoring
- `query_pipeline.py` — `QueryDataset` dataclass and `load_query_dataset()` / `load_segment_datasets()` — shared query parameter parsing across routers
- `listing_mapper.py` — transforms raw Kufar ad dicts into `ListingItem`/`ListingDetailResponse` schemas. Image base URL: `https://rms.kufar.by/v1/gallery/`
- `currency_service.py` — BYN↔USD conversion. All DB prices in BYN. `state.usdRateByn` in frontend = BYN per 1 USD.

### Frontend Architecture

Module pattern: each JS file exports a factory function. `app.js` creates core → renderers → actions, passing shared context. The `state` object in `app_core.js` is the single source of truth. All DOM updates go through `render*()` functions in `app_renderers.js`. Event handlers in `app_actions.js` call API endpoints, update state, then call render functions.

Views: overview, ads, tracking, cheap, monitoring, deals — switched via `data-view` tabs. Sections within overview are collapsible panels.

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
- `get_telegram_user` — validates `X-Telegram-Init-Data` header, returns `TelegramInitData` with `user_id`
- `get_cache` / `get_currency_service` — from `app.state`

All user-scoped endpoints require `get_telegram_user`; public endpoints (price-stats, listings, currency-rates, health) do not.

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

Docker Compose maps PostgreSQL `5432→5433` and Redis `6379→6380` to avoid conflicts with local installs.

## Conventions

- Python: ruff linting (E/W/F/I/N/UP/B/SIM/TCH), line length 99, isort with known-first-party `[api, bot, scheduler]`
- Async everywhere: SQLAlchemy async sessions, httpx async client, aiogram 3
- Frontend: no framework, no build step, vanilla JS with CSS custom properties for dark/light theming via `data-theme` attribute
- Toast notifications: 1400ms auto-dismiss, created via `showToast()` in app_renderers.js
- All user-scoped endpoints require `X-Telegram-Init-Data` header; public endpoints (price-stats, listings, etc.) do not
- Currency: all DB values in BYN. API returns in requested currency. Frontend converts using `state.usdRateByn` (BYN per 1 USD, so BYN→USD divides by this rate)
