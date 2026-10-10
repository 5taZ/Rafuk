# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Rafuks (package `kufar-analytics`) is a Telegram Mini App for marketplace analytics on kufar.by. It reads listings from Kufar's public JSON search API (`api.kufar.by/search-api/v2/search/rendered-paginated`). It does not scrape HTML, because kufar.by is a SPA. It computes price statistics over the full result set and serves them to a Telegram WebApp frontend. Users can track queries for new listings and price drops, and manage a watchlist and a deal pipeline.

User-facing text (Mini App UI, alert messages) is in Russian. Only BYN and USD are supported: EUR was removed on purpose, and BYN is the default.

## Commands

```bash
uv sync --extra dev                        # dev extra adds pytest, aiosqlite, ruff

uv run pytest                              # no Postgres/Redis needed, see Testing
uv run pytest tests/test_trackers_api.py -k "test_name"

uv run ruff check .                        # CI runs both of these
uv run ruff format .                       # CI: ruff format --check .

uv run alembic -c migrations/alembic.ini upgrade head

# Individual services. Run from the repo root: settings read ./.env relative to CWD
uv run uvicorn api.main:app --reload --host 0.0.0.0 --port 8010
uv run python -m bot.main
uv run python -m scheduler.collector

# Whole local stack. Linux-oriented bash (pkill, nohup, sed -i); logs and pids go to .run/
./start-local.sh [--with-tunnel]
./status-local.sh
./stop-local.sh
```

The tree is not ruff-clean right now. Both `ruff check` and `ruff format --check` report problems in existing files, so CI's lint job fails before the test job runs. Fix lint and formatting only in files you touch, unless someone asks for a cleanup.

## Local Dev Gotchas

- `start-local.sh` runs only the nginx frontend in Docker (`:8081`, `--no-deps`). It then runs migrations and starts the API (`0.0.0.0:8010`), the bot and the scheduler on the host. It does **not** start Postgres or Redis. Either run `docker compose up -d db redis` (published on host ports 5433 and 6380), or point `DATABASE_URL` at SQLite (`sqlite+aiosqlite:///./bot.db`). If Redis is unreachable, the API falls back to an in-memory cache.
- `.env.example` uses compose-internal hostnames (`db:5432`, `redis:6379`). From the host, use `localhost:5433` and `localhost:6380` instead.
- `nginx/default.conf` proxies `/api/` to `host.docker.internal:8010`, which is the API running on the host. It does not proxy to the compose `api` service (port 8000, not published). So the API must bind `0.0.0.0`, or the Mini App gets 502s. A plain `docker compose up -d` does not connect the frontend to the containerized API.
- Telegram `web_app` buttons require HTTPS, so `bot/keyboards.py` leaves out the Mini App button for `http://` URLs.
- `--with-tunnel` starts a `cloudflared` quick tunnel and rewrites `MINI_APP_URL` in `.env`. The URL changes on every run, so after each run restart the bot and send `/app` again. Buttons in older messages still point to the dead URL.

## Architecture

There are three Python processes. They share one database and one codebase. `api/` is both the FastAPI app and the shared domain library: `bot/` and `scheduler/` import `api.config`, `api.models` and `api.services.*` directly. The bot never calls the HTTP API.

- **API** (`api/main.py`, `create_app()`): routers are mounted under `/api/v1`.
  - `lifespan` puts shared objects (engine, session factory, cache, currency service) on `app.state`. Routes get them through `api/dependencies.py`.
  - User-scoped endpoints depend on `get_telegram_user`. It checks the HMAC of the `X-Telegram-Init-Data` header against `BOT_TOKEN`.
  - Market endpoints (price-stats, listings, segments and so on) are public. Only `/price-stats` has a slowapi limit, set through its own module-level `Limiter`; the `Limiter` on `app.state` is never applied. Behind nginx the limit key is the proxy IP, so all users share one bucket.
- **Bot** (`bot/`): aiogram 3 with long polling. Commands: `/app`, `/price`, `/top`, `/track`, `/tracks`, `/untrack`. It keeps its own engine singleton in `bot/database.py`.
- **Scheduler** (`scheduler/collector.py`): APScheduler in the Europe/Minsk timezone.
  - `check_trackers` runs every `ALERT_CHECK_INTERVAL` minutes.
  - A cleanup job runs daily at 03:00. It deletes tracker events older than 30 days and inactive listing states older than 90 days.
- **Frontend** (`frontend/`): static files served by nginx, with no build step.

### Market Query Pipeline

`api/services/query_pipeline.load_query_dataset` is the shared path for market endpoints:

1. `KufarClient.search_all_ads` follows cursor pagination across the whole market. Each request gets its own client. It waits `KUFAR_REQUEST_DELAY` between calls and retries 3 times with backoff.
2. `aggregator.apply_search_mode` filters by search mode. Strict mode keeps a title only if it contains all query tokens in order and no extra variant tokens such as pro, max or plus.
3. `QueryDataset` computes the stats lazily.

Segments run four filtered full searches at once through `parallel_kufar.parallel_search_all`, limited by `KUFAR_PARALLEL_SEMAPHORE`. Responses are cached (Redis or the in-memory fallback) for `CACHE_TTL_SECONDS`.

- Kufar's `price_byn` is in kopecks. `normalize_price_byn` divides it by 100 and drops prices above 100 000 BYN as anomalies.
- All stats are computed in BYN. They are converted to USD only when building the response, using NBRB rates from `CurrencyService`.
- Price history also builds up from ordinary user searches, not only from the scheduler: `/price-stats` upserts an hourly `QuerySnapshot`.
- `QuerySnapshot` and `QueryListingState` rows are keyed by `build_query_key(query, strict)`. That gives `strict::<normalized query>` or `broad::<normalized query>`. `Tracker.query`, by contrast, stores the raw query text.

### Tracker Flow

`check_trackers` goes through these steps:

1. Load the active trackers and group them by `(query, strict_mode)`, so each unique query hits Kufar only once.
2. Fetch only the first page (`size=50`). The API endpoints paginate through everything; the scheduler does not.
3. Upsert the query snapshot.
4. Compare the results with `QueryListingState` (`history_service.sync_query_listing_states`) to find new listings and price drops.
5. Filter those per tracker with `reseller_tools.matches_tracker_filters`.
6. Save `TrackerEvent` rows and send one Telegram message per tracker.

A tracker's first run only records a baseline and sends nothing. A `TelegramForbiddenError` (the user blocked the bot) deactivates all of that user's trackers. `Tracker.interval_min` is stored and shown in the UI, but the scheduler ignores it: every tracker runs on the global interval.

The reseller workflow lives in `routers/workflow.py` with `services/workflow_store.py`: leads, the watchlist and watchlist price refresh. `routers/saved_searches.py` covers saved searches and the opportunity board (`services/market_signals.py`). The Pydantic schemas accept any string for lead and watchlist statuses. On Postgres, however, migration 0008 adds CHECK constraints whose allowed values differ from the ones the UI (`frontend/js/app_renderers.js`) and the bot send.

### Data Model Caveats (`api/models.py`)

- Every `user_id` column holds the raw Telegram user ID. Routers write `telegram_user.user_id`, and the scheduler sends messages to `tracker.user_id`.
  - However, the models and migration 0009 declare these columns as foreign keys to `users.id`, an autoincrement column, and nothing ever inserts rows into `users`.
  - SQLite, which the tests use, does not enforce foreign keys. On Postgres, inserts into user-scoped tables will violate the foreign key until this is fixed.
- Migrations are written to be idempotent: they inspect existing tables, columns and indexes before creating anything. This is because local SQLite databases were first created with `Base.metadata.create_all`. Foreign key constraints are added only on non-SQLite databases. Follow this pattern in new migrations.

### Frontend

- The JS files are plain scripts: no ES modules, no bundler. `index.html` loads them with `defer` in this order: `app_core.js` → `app_renderers.js` → `app_actions.js` → `app.js`.
- Each file defines one global factory: `createAppCore`, `createAppRenderers`, `createAppActions`, `analyticsApp`. `app.js` wires core → renderers → actions through a shared context object.
- `state` in `app_core.js` is the single source of truth. Handlers in `app_actions.js` call the API, update `state`, then call the `render*()` functions from `app_renderers.js`.
- Chart.js and Alpine.js load from CDNs, but Alpine has no `x-data` root, so it does nothing.
- Views (`data-view`): overview, ads, tracking, cheap, monitoring, deals.
- Theming uses CSS custom properties and the `data-theme` attribute. Toasts come from `showToast()` and dismiss after 1400 ms.

`tests/test_app_js_syntax.py` and `tests/test_frontend_structure.py` search the frontend files for literal strings. The `runtimeMarkers` object in `app.js` and the hidden `x-text`/`x-for` block at the bottom of `index.html` exist only to pass those tests, so keep them when refactoring. The syntax test runs `node --check`, so `node` must be on PATH.

## Testing

The autouse fixtures in `tests/conftest.py` do all the setup:
- set every required env var;
- point `DATABASE_URL` at a fresh SQLite file per test (aiosqlite);
- clear the `get_settings` lru_cache;
- create the tables before each test and drop them after.

So tests need neither Postgres nor Redis; the conftest overrides CI's Postgres settings too.

`api.main` builds the app at import time and settings are cached, so tests import `create_app` inside the test function. API tests follow one pattern:
1. `monkeypatch.setattr(<router module>, "KufarClient", FakeKufarClient)`.
2. Override dependencies through `app.dependency_overrides` for `get_cache`, `get_currency_service` and `get_telegram_user`.
3. Open `TestClient(app)` and call `init_test_tables(app)`.

## Configuration

Settings live in `api/config.py` (pydantic-settings), loaded from `.env` in the current directory.

Required:
- `BOT_TOKEN`, also used as the HMAC key for initData.
- `DATABASE_URL` with an async driver: `postgresql+asyncpg://` or `sqlite+aiosqlite://`.
- `REDIS_URL`.
- `API_BASE_URL` and `MINI_APP_URL`. Both are also used as the CORS origins.

Optional, with defaults:
- `KUFAR_REQUEST_DELAY` (1.0)
- `KUFAR_PARALLEL_SEMAPHORE` (3)
- `KUFAR_TIMEOUT` (15.0)
- `ALERT_CHECK_INTERVAL` (30 minutes)
- `CACHE_TTL_SECONDS` (300)

`SCHEDULER_SNAPSHOT_HOUR` is defined but never used.

## Conventions and Repo Notes

- ruff: line length 99, rule sets E/W/F/I/N/UP/B/SIM/TCH, isort first-party packages `api`, `bot`, `scheduler`.
- Async everywhere: SQLAlchemy async sessions, httpx, aiogram 3.
- `.gitignore` excludes `*.md` and `*.db`. `CLAUDE.md`, `README.md`, `AI_HANDOFF.md` and `bot.db` are tracked anyway, so new Markdown files need `git add -f`.
- `AI_HANDOFF.md` (in Russian, decisions and roadmap) and `mvp-level1-fixed.md` (the original Level 1 plan) are historical documents. Trust the code over them. The roadmap deliberately puts off ML features such as price forecasting and clustering.
