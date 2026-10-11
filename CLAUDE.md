# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Rafuks (package `kufar-analytics`) is a Telegram Mini App for marketplace analytics on kufar.by. It reads listings from Kufar's public JSON search API (`api.kufar.by/search-api/v2/search/rendered-paginated`); it does not scrape HTML, because kufar.by is a SPA. It computes price statistics over the result set and serves them to a Telegram WebApp frontend. Users track queries for new listings and price drops, and manage a watchlist and a deal (lead) pipeline.

User-facing text is Russian: the Mini App, API error details, alerts and the tracker bot commands. Some bot replies (`/start`, `/help`, `/price`, `/top`, the generic error and throttle messages) are still English, so match the language of the handler you edit. Only BYN and USD are supported (EUR was removed on purpose); BYN is the default.

## Commands

```bash
uv sync --extra dev                        # dev extra: pytest, pytest-asyncio, aiosqlite, ruff

uv run pytest                              # no Postgres/Redis needed, see Testing
uv run pytest tests/test_trackers_api.py -k "test_name"

uv run ruff check .                        # CI runs both; the tree is clean, keep it that way
uv run ruff format --check .

uv run alembic -c migrations/alembic.ini upgrade head

# Individual services. Run from the repo root: settings read ./.env relative to CWD
uv run uvicorn api.main:app --reload --host 127.0.0.1 --port 8010
uv run python -m bot.main
uv run python -m scheduler.collector

# Host mode (bash; logs and PID files go to .run/)
./start-local.sh [--with-tunnel] [--no-infra]
./status-local.sh
./stop-local.sh [--keep-containers | --all]

# Compose mode: the whole stack in containers
docker compose up -d --build
```

## Local Dev and Compose Modes

One `docker-compose.yml` serves two modes, and `.env.example` documents both.

- **Compose mode** (`docker compose up -d --build`): `db`, `redis`, a one-shot `migrate` service (`alembic upgrade head`), then `api`, `bot`, `scheduler` and the nginx `frontend` on `:8081`. The Python services share one image (the `SERVICE` env var picks `api|bot|scheduler|migrate`) and one environment block. Containers ignore `DATABASE_URL`/`REDIS_URL` from `.env`: they connect to `db:5432`/`redis:6379`, built from `POSTGRES_*` and an optional `REDIS_PASSWORD`; `COMPOSE_DATABASE_URL`/`COMPOSE_REDIS_URL` override that. The API is not published; its healthcheck uses `GET /healthz`, and compose turns the FastAPI docs off (`API_DOCS_ENABLED`).
- **Host mode** (`./start-local.sh`): Postgres, Redis and the nginx frontend run in Docker; the API, bot and scheduler run on the host through `uv`. The script refuses `.env` URLs that point at compose hostnames (`db`, `redis`); host mode uses `localhost:5433` and `localhost:6380`, which compose publishes on 127.0.0.1 only. It stops a previous run, starts `db`/`redis` unless `--no-infra` is given, starts the frontend with `NGINX_API_UPSTREAM=host.docker.internal:<port>`, runs migrations, starts the API and waits for `/healthz`, then starts the bot and the scheduler. The API binds 127.0.0.1 on Docker Desktop or the docker0 gateway on native Linux (`LOCAL_API_HOST`, `LOCAL_API_PORT` default 8010 override this).
- **nginx**: `nginx/templates/default.conf.template` is rendered by the nginx image's envsubst; only `${API_UPSTREAM}` is substituted (default `api:8000`). It proxies `/api/` with a 60 s read timeout and sets `X-Forwarded-For` to the real client address, which uvicorn trusts through `--proxy-headers --forwarded-allow-ips` (Dockerfile and start-local.sh).
- **Without Docker**: `DATABASE_URL=sqlite+aiosqlite:///./bot.db` with `--no-infra`. If Redis is unreachable at startup, the API uses an in-memory cache.
- Telegram `web_app` buttons require HTTPS, so the bot leaves out Mini App buttons for an `http://` `MINI_APP_URL`. `--with-tunnel` starts a `cloudflared` quick tunnel and rewrites `MINI_APP_URL` in `.env`; the URL changes on every run, so restart the bot and send `/app` again (old buttons point to the dead URL).

## Architecture

There are three Python processes sharing one database and one codebase. `api/` is both the FastAPI app and the shared domain library: `bot/` and `scheduler/` import `api.config`, `api.models` and `api.services.*` directly. The bot never calls the HTTP API.

- **API** (`api/main.py`, `create_app()`): routers under `/api/v1`, plus `GET /healthz` (liveness only, no DB/Redis/Kufar calls). `lifespan` puts the engine, session factory, cache and `CurrencyService` on `app.state` (routes get them through `api/dependencies.py`) and enables the process-wide `DatasetCache`. `KufarAPIError` becomes a 502 with a Russian detail; validation errors are 422 with NaN/Infinity made JSON-safe. CORS origins are `MINI_APP_URL` and `API_BASE_URL` reduced to `scheme://host[:port]`.
- **Auth**: user-scoped endpoints (trackers, tracker events, saved searches, opportunity board, leads, watchlist) depend on `get_telegram_user`. It checks the HMAC of `X-Telegram-Init-Data` against `BOT_TOKEN` and requires `auth_date`: initData older than `INIT_DATA_MAX_AGE_SECONDS` (0 disables the age check) or more than 60 s in the future gets a 401. Market endpoints are public.
- **Rate limiting** (`api/rate_limit.py`): one shared slowapi `limiter` with in-process storage. `SlowAPIMiddleware` applies the default `120/minute` to every route; `/price-stats` adds `@limiter.limit("30/minute")`. The key is `tg:<user id>` when the request carries validly signed initData (its age does not matter here) and the client address otherwise. The 429 handler is deliberately synchronous: slowapi ignores coroutine handlers.
- **Statuses** (`api/statuses.py`): the canonical lead, watchlist and market status vocabularies as `Literal` types and tuples. Schemas use them (unknown values give 422) and the model CHECK constraints are built from them. Migration 0010, `LEAD_STATUS_OPTIONS`/`WATCHLIST_STATUS_OPTIONS` in `frontend/js/app_renderers.js` and `CALLBACK_STATUSES` in `bot/handlers/tracker.py` hold copies. A new status needs all of them plus a migration that replaces the CHECK.
- **Users** (`api/services/users.py`): every `user_id` column stores the raw Telegram user ID and is a foreign key to `users.telegram_user_id`. `ensure_user(session, telegram_user_id, ...)` upserts the `users` row without committing; call it in the same transaction before inserting any user-scoped row. The module also holds the per-user quotas (50 active trackers, 20 active saved searches, 300 watchlist items; exceeding one is a 409).
- **Trackers** (`api/services/tracker_service.py`): creation and deactivation shared by the API and the bot. It validates the query (non-empty after normalization, at most 255 chars), rejects duplicates (same `build_query_key`, mode and normalized filters) and enforces the quota; the API maps these to 422/409. `config_keyword` stays `None` unless given. `interval_min` is clamped to at least `ALERT_CHECK_INTERVAL`. Deletion is a soft delete (`active=False`, `deleted_at`).
- **Workflow** (`routers/workflow.py`, `services/workflow_store.py`): leads, watchlist, watchlist price refresh. `upsert_lead` never downgrades a lead: a repeated add changes the status only while it is `new` or `deferred`. Concurrent inserts are resolved through a savepoint and `IntegrityError`. `routers/saved_searches.py` covers saved searches and the opportunity board (`services/market_signals.py`).
- **Bot** (`bot/`): aiogram 3, long polling. Commands `/start`, `/help`, `/app`, `/price`, `/top`, `/track`, `/tracks`, `/untrack`, plus `lead:`/`later:` callbacks from alert buttons. `/price` and `/top` go through `ThrottlingMiddleware` (one command per user every 3 s, at most 2 concurrent). `/start` reactivates trackers that were deactivated because the user had blocked the bot. A dispatcher-level error handler logs and answers. `bot/database.py` keeps an engine singleton.
- **Scheduler** (`scheduler/collector.py`): APScheduler in Europe/Minsk. `check_trackers` runs every `ALERT_CHECK_INTERVAL` minutes (the first run starts immediately); the cleanup job runs daily at 03:00. At startup the scheduler waits for the database with backoff.
- **Frontend** (`frontend/`): static files served by nginx, no build step.

### Market Query Pipeline

`api/services/query_pipeline.load_query_dataset` is the shared path for market endpoints and the bot's `/price`:

1. The response cache (Redis or `MemoryCache`, TTL `CACHE_TTL_SECONDS`) is keyed by `response_cache_key`, which is built on `build_query_key`, so case and spacing variants share entries. `RedisCache` falls back to memory for 30 s after a Redis error.
2. `DatasetCache` (in-process, 120 s TTL, LRU: 16 entries in the API and 8 in the bot) stores normalized Kufar responses. Its key is the query key plus the Kufar filters, without the currency, so switching BYN/USD never refetches; concurrent loads of one key share one pagination. It is off unless `configure_dataset_cache` was called (API lifespan, bot `main`); the scheduler does not use it.
3. `KufarClient.search_all_ads` follows the cursor for at most 25 pages of 200 ads and stops at a 40 s deadline (below nginx's 60 s), on a repeated cursor or on a page without new ads. Then the result carries `truncated: True`. Kufar is always asked for BYN prices.
4. Every Kufar request, from any client instance, goes through a process-wide pacer (per event loop): at most `KUFAR_PARALLEL_SEMAPHORE` in flight, with starts at least `KUFAR_REQUEST_DELAY` apart. Network errors, 429 and 5xx are retried 3 times with exponential backoff from 1 s, honoring `Retry-After` up to 10 s. Other 4xx and non-JSON responses raise `KufarAPIError` at once.
5. `normalize_ads` coerces field types. Ads without a valid `ad_id` go to `QueryDataset.unidentified_ads`: they count in totals and stats but are never listed or tracked. `apply_search_mode` in strict mode keeps a title only if it contains every query token (in any order) and no variant token (`STRICT_VARIANT_TOKENS`: pro, max, plus, mini, ...) that the query lacks. `normalize_search_text` applies aliases on word boundaries (айфон → iphone, пс5 → ps5, ...).
6. Kufar's `price_byn` is in kopecks: `normalize_price_byn` divides by 100 and drops prices above 100 000 BYN. With 4 or more prices, `compute_price_stats` drops prices below 25 % of the median as placeholders, and deals skip them too.
7. All stats are computed in BYN and converted only when the response is built (`convert_price_stats` with `CurrencyService` rates). NBRB rates are cached for 1 h. If NBRB fails, the service uses the last good rate (`source: "stale"`) or the hardcoded 3.0 (`source: "fallback"`); either is cached for 60 s.
8. `/price-stats` also upserts the hourly `QuerySnapshot` (UTC hour bucket) on a best-effort basis: a DB error is logged and the stats are still returned. The upsert is an atomic `INSERT ... ON CONFLICT`. Empty samples are never stored, and an existing bucket is replaced only by a sample with at least as many analyzed prices. The scheduler only fills empty buckets.

Segments run four filtered full searches (new/used × private/shop) through `parallel_kufar.parallel_search_all`; each one is cached separately. `QuerySnapshot` and `QueryListingState` rows are keyed by `build_query_key(query, strict)`, which gives `strict::<normalized>` or `broad::<normalized>`, at most 255 chars (longer keys are truncated and get a hash suffix). `Tracker.query` stores the raw query text.

### Tracker Flow

`check_trackers`:

1. Load the active trackers and group them by `build_query_key`, so equivalent queries hit Kufar once. The oldest tracker's raw query represents the group. A group is skipped unless at least one of its trackers is due according to its `interval_min`.
2. Read the group's known ad IDs in a short transaction, then fetch up to 3 pages of 100 (newest first) with `KufarClient.search`. Paging stops early once a page has no unknown listings.
3. In one transaction per group: fill the snapshot bucket if it is empty, and run `history_service.sync_query_listing_states`. A listing is new only if its `ad_id` was never seen for this key. A price drop must be at least 1 BYN and 1 %.
4. Filter per tracker with `reseller_tools.matches_tracker_filters`. An attribute that cannot be read from the ad passes its filter, and `config_keyword` matches tokens. Save at most 10 new-listing and 10 price-drop `TrackerEvent` rows per tracker, commit, then send.
5. A failure in one group is logged and skipped; the other groups still commit and send.

A tracker's first check only records a baseline. `AlertSender` sends at most 20 messages/s and retries `TelegramRetryAfter` once when the wait is 60 s or less. `TelegramForbiddenError` (the user blocked the bot) deactivates all of that user's active trackers. Other send errors are logged per message. The alert keyboard has an "Открыть запрос" deep link (`?query=&view=tracking&strict=`, HTTPS only), "Открыть лот" (http(s) links only), and "В работу"/"Позже", which create or update a lead (`in_progress`/`deferred`).

Daily cleanup deletes tracker events older than 30 days and snapshots older than 120 days. It deletes listing states not seen for 90 days, or for 365 days when the key still has active trackers, because forgetting an `ad_id` would turn it into a "new" listing again.

### Data Model Notes (`api/models.py`, `migrations/`)

- The `user_id` foreign keys reference `users.telegram_user_id` with `ON DELETE CASCADE` (migration 0010 repointed them from `users.id` and backfilled `users`). Unique keys: `(user_id, ad_id)` for leads and watchlist items, `(query, snapshot_at)` for snapshots, `(query, ad_id)` for listing states.
- CHECK constraints cover lead and watchlist statuses, the watchlist market status and `tracker_events.event_type`. Migration 0010 normalized legacy status values to the canonical ones.
- Migrations are idempotent: they inspect tables, columns, indexes and constraints before changing anything, because old local SQLite databases were created with `Base.metadata.create_all`. User FKs and status CHECKs are added only on non-SQLite databases, and `migrations/env.py` hides FKs from autogenerate on SQLite. Migrations must not import app code (vocabularies are copied), offline mode (`--sql`) is rejected, and every revision must downgrade cleanly. Follow this pattern in new migrations.
- `api.database.get_engine` enables `PRAGMA foreign_keys=ON` for SQLite connections, so FKs declared on the tables are enforced there too (tests create tables with `create_all`, which includes them).
- `scripts/partition_snapshots.sql` is a manual Postgres maintenance script; nothing runs it.

### Frontend

- Plain scripts, no modules or bundler. `index.html` loads Telegram's `telegram-web-app.js`, Chart.js from a CDN, then with `defer`: `app_core.js` → `app_renderers.js` → `app_actions.js` → `app.js`. Each defines one global factory (`createAppCore`, `createAppRenderers`, `createAppActions`, `analyticsApp`); `app.js` wires core → renderers → actions through a shared context. The UI works without Chart.js (charts are skipped).
- `state` in `app_core.js` is the single source of truth. Handlers in `app_actions.js` call the API, update `state` and call `render*()` from `app_renderers.js`.
- `state.query` mirrors the input; `state.activeQuery` is the last submitted query, and every API call, tracker and summary uses it.
- Request tokens: `beginRequest(kind)` aborts the previous request of that kind and bumps its token. Only a response whose token passes `isCurrentRequest` may write to `state` (`runTrackedRequest` wraps this). A whole search is also guarded by `state.searchRequestId`. `state.listingsLoading` is true while a listings or deals request is pending.
- `state.loadErrors` (listings, deals, leads, watchlist, board, events) holds per-section errors; renderers show them instead of an empty state.
- Markup goes through the `html` tagged template and `setHtml`: interpolated values are escaped (`escapeHtml`) unless they are nested `html` results. URLs for `href`/`src` go through `safeUrl` (absolute http(s) only). Never assign raw strings to `innerHTML`.
- Views (`data-view`): overview, ads, tracking, cheap, monitoring, deals. Deep-link params `query`, `view` and `strict` (built by `bot/keyboards.build_query_deep_link`) are applied by `applyLaunchParams`. Theming uses CSS custom properties and `data-theme`; `showToast()` keeps a toast for 1.6 to 4 s, depending on its length.

## Testing

`asyncio_mode = "auto"`. The autouse fixtures in `tests/conftest.py`:
- set every required env var and point `DATABASE_URL` at a fresh SQLite file per test (aiosqlite), overriding CI's Postgres settings;
- clear the `get_settings` lru_cache;
- run `create_all` before each test and `drop_all` after it;
- reset process-wide state between tests: the slowapi limiter, the bot engine singleton, the dataset cache and the Kufar pacer.

Foreign keys are enforced in tests, so a test that inserts user-scoped rows directly must create the `users` row first (`ensure_user` or a `User` model).

Patterns:
- **API**: `api.main` builds the app at import time, so import `create_app` inside the test. `monkeypatch.setattr(<router module>, "KufarClient", FakeKufarClient)`, override `get_cache`, `get_currency_service` and `get_telegram_user` through `app.dependency_overrides`, then use `with TestClient(app)`, which runs the lifespan against the same SQLite file.
- **Scheduler**: a `FakeKufarClient` subclass serving a catalog by normalized query, `monkeypatch.setattr(collector, "KufarClient", ...)`, `SEND_MIN_INTERVAL_SECONDS = 0`, a fake bot with `send_message`, then call `collector.check_trackers` directly.
- **Bot**: call the handlers directly with `SimpleNamespace` messages and `AsyncMock` `answer`; the DB goes through `bot.database`.
- **Kufar client**: patch `_backoff_sleep` to skip retry backoff.
- **Migrations**: `tests/test_migrations.py` runs alembic on a temp SQLite file: `upgrade head` must match the models, `downgrade base` and back must work, and legacy statuses must be normalized. CI also runs an up/down migration cycle on Postgres.
- `tests/test_frontend_structure.py` and `tests/test_app_js_syntax.py` check the real frontend structure. `test_node_syntax_check` runs `node --check` on each script; it is skipped when `node` is not on PATH and runs in CI.

## Configuration

Settings live in `api/config.py` (pydantic-settings), loaded from `.env` in the current directory.

Required:
- `BOT_TOKEN`, also the HMAC key for initData.
- `DATABASE_URL` with an async driver: `postgresql+asyncpg://` or `sqlite+aiosqlite://`.
- `REDIS_URL`.
- `API_BASE_URL` and `MINI_APP_URL`, which are also the CORS origins.

Optional, with defaults:
- `KUFAR_REQUEST_DELAY` (0.5 s minimum gap between Kufar request starts, process-wide)
- `KUFAR_PARALLEL_SEMAPHORE` (3 concurrent Kufar requests)
- `KUFAR_TIMEOUT` (15.0 s)
- `ALERT_CHECK_INTERVAL` (30 minutes; also the lower bound for `Tracker.interval_min`)
- `CACHE_TTL_SECONDS` (300)
- `INIT_DATA_MAX_AGE_SECONDS` (86400; 0 disables the age check)
- `API_DOCS_ENABLED` (FastAPI docs; compose turns them off)

Compose and script variables: `POSTGRES_DB`/`POSTGRES_USER`/`POSTGRES_PASSWORD`, `REDIS_PASSWORD`, `COMPOSE_DATABASE_URL`/`COMPOSE_REDIS_URL`, `NGINX_API_UPSTREAM`, `LOCAL_API_HOST`/`LOCAL_API_PORT`.

## Conventions and Repo Notes

- ruff: line length 99, rule sets E/W/F/I/N/UP/B/SIM/TCH, isort first-party `api`, `bot`, `scheduler`; `.claude`, `.codex` and `.run` are excluded. CI runs lint (`ruff check`, `ruff format --check`, `docker compose config`) and tests as independent jobs; the Docker image build runs on pushes to `main` after both pass.
- Async everywhere: SQLAlchemy async sessions, httpx, aiogram 3.
- `.gitattributes` forces LF for `*.sh`, `Dockerfile`, `*.conf`, `*.template` and `*.yml`; CRLF breaks them inside containers.
- Local databases (`*.db`) and `.env` are never committed, since they may contain real Telegram user IDs and the bot token. `.dockerignore` keeps them, `.git` and the tests out of the image.
- `AI_HANDOFF.md` (Russian, state of April 2026) and `mvp-level1-fixed.md` (the original Level 1 plan) are historical. Trust the code and this file over them. The roadmap deliberately defers ML features such as price forecasting and clustering.
