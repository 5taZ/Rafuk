# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Rafuks — a Telegram Mini App for marketplace analytics on kufar.by. It fetches listing data from Kufar's public JSON API (`api.kufar.by`), computes price statistics, and presents them through a Telegram WebApp frontend. Users can track queries for new listings and price drops, manage a watchlist and deal pipeline.

## Build & Run Commands

```bash
# Install dependencies
uv sync --extra dev

# Run all services locally (frontend on :8081, API on :8010, bot, scheduler)
./start-local.sh
./start-local.sh --with-tunnel   # with Cloudflare tunnel for Telegram

# Stop everything
./stop-local.sh

# Individual services
uv run uvicorn api.main:app --reload --host 0.0.0.0 --port 8010
uv run python -m bot.main
uv run python -m scheduler.collector

# Database migrations
uv run alembic -c migrations/alembic.ini upgrade head

# Docker Compose (production)
docker compose up -d

# Tests
uv run pytest
uv run pytest tests/test_specific.py -k "test_name"

# Lint
uv run ruff check .
uv run ruff check --fix .
```

## Architecture

Three long-running services + static frontend:

1. **API** (`api/`) — FastAPI app on `:8010`. Stateless; all persistence in PostgreSQL. Redis for caching Kufar responses (graceful fallback to in-memory cache). Telegram auth via `X-Telegram-Init-Data` header with HMAC verification.

2. **Bot** (`bot/`) — aiogram 3 Telegram bot. Commands: `/app`, `/price`, `/top`, `/track`, `/tracks`, `/untrack`. Shares the same database as the API.

3. **Scheduler** (`scheduler/collector.py`) — APScheduler loop that periodically checks all active trackers, detects new listings and price drops, notifies users via bot, and persists `TrackerEvent` rows.

4. **Frontend** (`frontend/`) — Vanilla JS single-page app served by Nginx. No build step. Four files:
   - `app_core.js` — state object, DOM element cache, formatters, Telegram theme init
   - `app_renderers.js` — all DOM rendering functions, Chart.js charts, toast notifications
   - `app_actions.js` — API calls, event binding, user actions
   - `app.js` — entry point that wires everything together

### Data Flow

```
Frontend (Telegram WebApp)
  → fetch with X-Telegram-Init-Data header
  → FastAPI router
  → KufarClient (api.kufar.by JSON endpoint)
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

### Key Models (api/models.py)

- `Tracker` — user's saved query with filter config (discount %, max price, seller, condition, region, config keyword, exclude duplicates)
- `TrackerEvent` — new_listing or price_drop events for a tracker
- `QuerySnapshot` — periodic price stats snapshot per query
- `QueryListingState` — tracks individual listing lifecycle per query
- `LeadItem` — deal pipeline items (status: new → researching → bought → sold)
- `WatchlistItem` — monitored listings with price tracking and workflow status
- `SavedSearch` — saved search groups with filter config

### Key Services (api/services/)

- `kufar_client.py` — HTTP client for `api.kufar.by` with retry/backoff and rate limiting
- `aggregator.py` — price stats computation, search mode filtering (strict vs loose), query key building
- `history_service.py` — query snapshot upsert, listing state sync, price change detection
- `reseller_tools.py` — flip estimates, duplicate detection, tracker filter matching
- `market_signals.py` — market signal computation for opportunity board
- `deal_workflow.py` — lead/watchlist CRUD and status transitions
- `query_pipeline.py` — shared query parameter parsing across routers

### Frontend Architecture

Module pattern: each JS file exports a factory function. `app.js` creates core → renderers → actions, passing shared context. The `state` object in `app_core.js` is the single source of truth. All DOM updates go through `render*()` functions in `app_renderers.js`. Event handlers in `app_actions.js` call API endpoints, update state, then call render functions.

Views: overview, ads, tracking, cheap, monitoring, deals — switched via `data-view` tabs. Sections within overview are collapsible panels.

## Configuration

Environment variables loaded from `.env` via pydantic-settings (`api/config.py`):

- `BOT_TOKEN` — Telegram bot token (also used for initData verification)
- `DATABASE_URL` — asyncpg PostgreSQL connection string
- `REDIS_URL` — Redis connection string
- `API_BASE_URL` — public API URL
- `MINI_APP_URL` — public frontend URL (set by cloudflared tunnel in dev)
- `KUFAR_REQUEST_DELAY` — delay between Kufar API calls (default 1.0s)
- `KUFAR_PARALLEL_SEMAPHORE` — max parallel Kufar requests (default 3)
- `ALERT_CHECK_INTERVAL` — scheduler tracker check interval in minutes (default 30)

## Conventions

- Python: ruff linting (E/W/F/I/N/UP/B/SIM/TCH), line length 99, isort with known-first-party `[api, bot, scheduler]`
- Async everywhere: SQLAlchemy async sessions, httpx async client, aiogram 3
- Frontend: no framework, no build step, vanilla JS with CSS custom properties for dark/light theming via `data-theme` attribute
- Toast notifications: 1400ms auto-dismiss, created via `showToast()` in app_renderers.js
- All user-scoped endpoints require `X-Telegram-Init-Data` header; public endpoints (price-stats, listings, etc.) do not
