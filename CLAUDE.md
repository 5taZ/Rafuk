# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Rafuks (package `kufar-analytics`) is a Telegram Mini App for marketplace analytics on kufar.by. It reads listings from Kufar's public JSON search API (`api.kufar.by/search-api/v2/search/rendered-paginated`); it does not scrape HTML, because kufar.by is a SPA. It computes price statistics over the result set and serves them to a Telegram WebApp frontend. Users track queries for new listings and price drops, and manage a watchlist and a deal (lead) pipeline.

User-facing text is Russian: the Mini App, API error details, alerts and the tracker bot commands. Some bot replies (`/start`, `/help`, `/price`, `/top`, the generic error and throttle messages) are still English, so match the language of the handler you edit. Only BYN and USD are supported (EUR was removed on purpose); BYN is the default.

## Commands

```bash
uv sync --extra dev                        # dev extra: pytest, pytest-asyncio, pytest-cov, aiosqlite, ruff

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
- **nginx** (`nginx:1.28-alpine`): `nginx/templates/default.conf.template` is rendered by the nginx image's envsubst; only `${API_UPSTREAM}` and `${API_UPSTREAM_PARAMS}` are substituted (default `api:8000 resolve`). The upstream has a shared `zone` and uses Docker's DNS (`resolver 127.0.0.11`), so with `resolve` nginx follows a recreated `api` container; `depends_on: restart: true` also restarts the frontend with `api`. Host mode passes no parameters (`host.docker.internal` lives only in `/etc/hosts`). It proxies `/api/` with a 60 s read timeout and sets `X-Forwarded-For` to the real client address, which uvicorn trusts through `--proxy-headers --forwarded-allow-ips` (Dockerfile and start-local.sh). nginx trusts `X-Forwarded-For` from Docker's private ranges, which is safe only because `:8081` is published on `FRONTEND_BIND_ADDRESS` (127.0.0.1 by default); never publish it on a public interface.
- **Without Docker**: `DATABASE_URL=sqlite+aiosqlite:///./bot.db` with `--no-infra`. If Redis is unreachable at startup, the API uses an in-memory cache.
- Telegram `web_app` buttons require HTTPS, so the bot leaves out Mini App buttons for an `http://` `MINI_APP_URL`. `--with-tunnel` starts a `cloudflared` quick tunnel and rewrites `MINI_APP_URL` in `.env`; the URL changes on every run, so restart the bot and send `/app` again (old buttons point to the dead URL).

## Architecture

There are three Python processes sharing one database and one codebase. `api/` is both the FastAPI app and the shared domain library: `bot/` and `scheduler/` import `api.config`, `api.models` and `api.services.*` directly. The bot never calls the HTTP API.

- **API** (`api/main.py`, `create_app()`): routers under `/api/v1`, plus `GET /healthz` (liveness only, no DB/Redis/Kufar calls). `lifespan` puts the engine, session factory, cache and `CurrencyService` on `app.state` (routes get them through `api/dependencies.py`) and enables the process-wide `DatasetCache`. `KufarAPIError` becomes a 502 with a Russian detail; its subclass `KufarBusyError` (the process's queue to Kufar is full) becomes a 503 with `Retry-After: 30`. Validation errors are 422 with NaN/Infinity made JSON-safe; a NUL character in a query or path parameter is a 422 too (`api/validators.reject_nul_in_request`, an app-wide dependency; bodies are covered by the schema string types). CORS origins are `MINI_APP_URL` and `API_BASE_URL` reduced to `scheme://host[:port]`.
- **Auth**: user-scoped endpoints (trackers, tracker events, saved searches, opportunity board, leads, watchlist) depend on `get_telegram_user`. It checks the HMAC of `X-Telegram-Init-Data` against `BOT_TOKEN` and requires `auth_date`: initData older than `INIT_DATA_MAX_AGE_SECONDS` (0 disables the age check) or more than 60 s in the future gets a 401. Market endpoints are public.
- **Rate limiting** (`api/rate_limit.py`): one shared slowapi `limiter` with in-process storage, in two layers. `ApplicationRateLimitMiddleware` charges every `/api/` request against one per-key budget shared by all routes: `API_RATE_LIMIT_PER_MINUTE`, or, when that is 0, half the pages per minute the Kufar pacer allows (`60 / KUFAR_REQUEST_DELAY / 2`, clamped to 30..120; 60 at the default delay). Then `SlowAPIMiddleware` applies `120/minute` per route, and Kufar-heavy routes add `@limiter.limit` (price-stats and listing detail 30/minute, compare, segments and the opportunity board 10/minute, watchlist refresh 4/minute). The key is `tg:<user id>` when initData passes the same checks as authentication (signature and age), and the client address otherwise. The 429 handler is deliberately synchronous: slowapi ignores coroutine handlers.
- **Statuses** (`api/statuses.py`): the canonical lead, watchlist and market status vocabularies as `Literal` types and tuples. Schemas use them (unknown values give 422) and the model CHECK constraints are built from them. Migration 0010, `LEAD_STATUS_OPTIONS`/`WATCHLIST_STATUS_OPTIONS` in `frontend/js/app_renderers.js` and `CALLBACK_STATUSES` in `bot/handlers/tracker.py` hold copies. A new status needs all of them plus a migration that replaces the CHECK.
- **Users** (`api/services/users.py`): every `user_id` column stores the raw Telegram user ID and is a foreign key to `users.telegram_user_id`. `ensure_user(session, telegram_user_id, ...)` upserts the `users` row without committing; call it in the same transaction before inserting any user-scoped row. The module also holds the per-user quotas (50 active trackers, 20 active saved searches, 300 watchlist items; exceeding one is a 409).
- **Trackers** (`api/services/tracker_service.py`): creation and deactivation shared by the API and the bot. It validates the query (non-empty after normalization, at most 255 chars), rejects duplicates (same `build_query_key`, mode and normalized filters) and enforces the quota; the API maps these to 422/409. `config_keyword` stays `None` unless given. `interval_min` is stored as at least `ALERT_CHECK_INTERVAL`; the API reports the real cadence as `interval_min` (`real_check_interval_min`: rounded to the nearest multiple of the global interval by `rounded_interval_min`, the rule the scheduler's `tracker_is_due` uses). Deletion (API, `/untrack`) is a soft delete: `active=False` plus `deleted_at`. The scheduler only clears `active` when the user blocked the bot, and `/start` resumes exactly those trackers (`resume_paused_trackers`); migration 0011 marked the legacy inactive trackers as deleted.
- **Workflow** (`routers/workflow.py`, `services/workflow_store.py`): leads, watchlist, watchlist price refresh. `upsert_lead` never downgrades a lead: a repeated add changes the status only while it is in an early status (`LEAD_STATUSES_OPEN_TO_UPSERT`: new, reviewing, deferred, skipped). An empty `link` becomes `https://www.kufar.by/item/<ad_id>`. Concurrent inserts are resolved through a savepoint and `IntegrityError`. Watchlist refresh and the opportunity board run their Kufar searches under one 45 s budget (`gather_within_budget`); searches that do not fit are reported (`skipped` count, `partial: true`) instead of failing the request, and a refresh never marks an item missing from a truncated result. `routers/saved_searches.py` covers saved searches and the opportunity board (`services/market_signals.py`).
- **Bot** (`bot/`): aiogram 3, long polling. Commands `/start`, `/help`, `/app`, `/price`, `/top`, `/track`, `/tracks`, `/untrack`, plus `lead:`/`later:` callbacks from alert buttons. `/price` and `/top` go through `ThrottlingMiddleware`: one command per user every 3 s, one in flight per user, at most 2 at once process-wide and no waiting queue (extra commands get a "busy" reply). `/start` resumes trackers paused because the user had blocked the bot. A dispatcher-level error handler logs and answers. `bot/database.py` keeps an engine singleton.
- **Scheduler** (`scheduler/collector.py`): APScheduler in Europe/Minsk. `check_trackers` runs every `ALERT_CHECK_INTERVAL` minutes (the first run starts immediately); the cleanup job runs daily at 03:00. At startup the scheduler waits for the database with backoff.
- **Frontend** (`frontend/`): static files served by nginx, no build step.

### Market Query Pipeline

`api/services/query_pipeline.load_query_dataset` is the shared path for market endpoints and the bot's `/price`:

1. The response cache (Redis or `MemoryCache`, TTL `CACHE_TTL_SECONDS`) is keyed by `response_cache_key`: the search mode, the raw query lightly normalized by `canonical_raw_query` (casefold, collapsed whitespace; aliases and punctuation are kept, because Kufar may answer "S23+" and "S23" differently) and the response-shaping params. `RedisCache` falls back to memory for 30 s after a Redis error.
2. `DatasetCache` (in-process; 16 entries in the API and 8 in the bot, at most 20 000 ads summed over all entries) stores normalized Kufar responses for 120 s, or 30 s when the pagination was truncated. Its key (`dataset_cache_key`) is the same light normalization of the raw query plus the Kufar filters; the search mode, the currency and the time budget are not part of it, so strict/broad and BYN/USD switches never refetch. Concurrent loads of one key share one pagination. It is off unless `configure_dataset_cache` was called (API lifespan, bot `main`); the scheduler does not use it.
3. `KufarClient.search_all_ads` follows the cursor for at most 25 pages of 200 ads and stops at a 40 s deadline (below nginx's 60 s), on a repeated cursor, on a page without new ads, or when a later page fails (the pages already fetched are kept). Then the result carries `truncated: True`, the market responses pass `truncated` on, `total_results` falls back to Kufar's total, and the Mini App shows a note. Kufar is always asked for BYN prices.
4. Every Kufar request, from any client instance, goes through a process-wide pacer (per event loop): at most `KUFAR_PARALLEL_SEMAPHORE` in flight, with starts at least `KUFAR_REQUEST_DELAY` apart, and at most `min(64, 30 s / KUFAR_REQUEST_DELAY)` requests waiting; one more raises `KufarBusyError` at once. Network errors, 429 and 5xx are retried 3 times with exponential backoff from 1 s; a `Retry-After` (up to 10 s) pauses the whole pacer. Other 4xx and non-JSON responses raise `KufarAPIError` at once.
5. `normalize_ads` coerces field types. Ads without a valid `ad_id` go to `QueryDataset.unidentified_ads`: they count in totals and stats but are never listed or tracked. `apply_search_mode` in strict mode keeps a title only if it contains every query token (in any order) and no variant token (`STRICT_VARIANT_TOKENS`: pro, max, plus, mini, ...) that the query lacks. `normalize_search_text` applies aliases on word boundaries (айфон → iphone, пс5 → ps5, ...).
6. Kufar's `price_byn` is in kopecks: `normalize_price_byn` divides by 100 and drops prices above 100 000 BYN. With 4 or more prices, `compute_price_stats` drops prices below 25 % of the median as placeholders, and deals skip them too.
7. All stats are computed in BYN and converted only when the response is built (`convert_price_stats` with `CurrencyService` rates). NBRB rates are cached for 1 h. If NBRB fails, the service uses the last good rate (`source: "stale"`) or the hardcoded 3.0 (`source: "fallback"`); either is cached for 60 s.
8. `/price-stats` also upserts the hourly `QuerySnapshot` (UTC hour bucket) on a best-effort basis: a DB error is logged and the stats are still returned. The upsert is an atomic `INSERT ... ON CONFLICT`. Empty samples are never stored, and an existing bucket is replaced only by a sample with at least as many analyzed prices. Only `/price-stats` writes snapshots: the scheduler's few recent pages are not comparable with a full scan.

Segments run four filtered full searches (new/used × private/shop) through `parallel_kufar.parallel_search_all`; each one is cached separately. `QuerySnapshot` and `QueryListingState` rows are keyed by `build_query_key(query, strict)`, which gives `strict::<normalized>` or `broad::<normalized>`, at most 255 chars (longer keys are truncated and get a hash suffix). `Tracker.query` stores the raw query text.

### Tracker Flow

`check_trackers`:

1. Load the active trackers and group them by `build_query_key`, so equivalent queries hit Kufar once. The oldest tracker's raw query represents the group. A group runs when at least one of its trackers is due (`tracker_is_due`: the rounded interval has passed since `last_checked_at`, with half a check interval of slack), and then processes all of its trackers.
2. Fetch pages of 100 (newest first) with `KufarClient.search`, looking up the known ad IDs page by page in short transactions. Paging continues while a page brings unknown listings or its oldest raw listing (`list_time`, 5 min slack) is newer than the group's previous check, for at most 3 pages. Once every 6 h per group (windows shifted by a hash of the key) a deep scan pages up to 10 pages to catch price drops of older listings; listings first seen beyond the regular window are recorded without a "new listing" alert.
3. In one transaction per group run `history_service.sync_query_listing_states`. A listing is new only if its `ad_id` was never seen for this key. A price drop must be at least 1 BYN and 1 %.
4. Filter per tracker with `reseller_tools.matches_tracker_filters`. An attribute that cannot be read from the ad passes its filter, and `config_keyword` matches tokens. Save at most 10 new-listing and 10 price-drop `TrackerEvent` rows per tracker, commit, then send.
5. A failure in one group is logged and skipped; the other groups still commit and send.

A key without any listing state (a new query) only records a baseline and sends nothing; a baseline that found no listings stores a marker state with `ad_id = 0` (`BASELINE_MARKER_AD_ID`), so the first listing of such a query still alerts. A tracker's own first check (`last_checked_at` is NULL) is a baseline too. `AlertSender` sends at most 20 messages/s; flood control (waits up to 60 s) and transient errors get up to 3 attempts with exponential backoff from 2 s, and after 3 alerts in a row were lost to transient errors the rest of the run gets one attempt each. `TelegramForbiddenError` (the user blocked the bot) deactivates all of that user's active trackers. The alert keyboard has an "Открыть запрос" deep link (`?query=&view=tracking&strict=`, HTTPS only), "Открыть лот" (http(s) links only), and "В работу"/"Позже", which create or update a lead (`in_progress`/`deferred`) and answer truthfully when the lead keeps a later status.

Daily cleanup deletes tracker events older than 30 days and snapshots older than 120 days. It deletes listing states not seen for 90 days, or for 365 days when the key still has active trackers, because forgetting an `ad_id` would turn it into a "new" listing again.

### Data Model Notes (`api/models.py`, `migrations/`)

- The `user_id` foreign keys reference `users.telegram_user_id` with `ON DELETE CASCADE` (migration 0010 repointed them from `users.id` and backfilled `users`). Unique keys: `(user_id, ad_id)` for leads and watchlist items, `(query, snapshot_at)` for snapshots, `(query, ad_id)` for listing states.
- CHECK constraints cover lead and watchlist statuses, the watchlist market status and `tracker_events.event_type`. On Postgres, 0008 adds only the event type and market status CHECKs (its old status lists rejected statuses the app already wrote). Migration 0010 normalizes legacy status values to the canonical ones and then adds the status CHECKs; its downgrade drops them again and keeps the rows. The head revision is `20261011_0011`.
- Migrations are idempotent: they inspect tables, columns, indexes and constraints before changing anything, because old local SQLite databases were created with `Base.metadata.create_all`. User FKs and status CHECKs are added only on non-SQLite databases, and `migrations/env.py` hides FKs from autogenerate on SQLite. Migrations must not import app code (vocabularies are copied), offline mode (`--sql`) is rejected, and every revision must downgrade cleanly. Follow this pattern in new migrations.
- SQLite enforces foreign keys only when the env var `SQLITE_FOREIGN_KEYS` is `1` or `true` (`api.database.get_engine` then sets `PRAGMA foreign_keys=ON`). It is off by default because old local databases built by `create_all` declare `user_id REFERENCES users(id)`, which migrations cannot rebuild; the tests turn it on.
- `scripts/partition_snapshots.sql` is a manual Postgres maintenance script; nothing runs it.

### Frontend

- Plain scripts, no modules or bundler. `index.html` loads Telegram's `telegram-web-app.js`, Chart.js from a CDN, then with `defer`: `app_core.js` → `app_renderers.js` → `app_actions.js` → `app.js`. Each defines one global factory (`createAppCore`, `createAppRenderers`, `createAppActions`, `analyticsApp`); `app.js` wires core → renderers → actions through a shared context. The UI works without Chart.js (charts are skipped).
- `state` in `app_core.js` is the single source of truth. Handlers in `app_actions.js` call the API, update `state` and call `render*()` from `app_renderers.js`.
- `state.query` mirrors the input; `state.activeQuery` is the last submitted query, and every API call, tracker and summary uses it.
- Request tokens: `beginRequest(kind)` aborts the previous request of that kind and bumps its token. Only a response whose token passes `isCurrentRequest` may write to `state` (`runTrackedRequest` wraps this). A whole search is also guarded by `state.searchRequestId`. `state.listingsLoading` is true while a listings or deals request is pending.
- `state.loadErrors` (listings, deals, leads, watchlist, board, events) holds per-section errors; renderers show them instead of an empty state.
- Markup goes through the `html` tagged template and `setHtml`: interpolated values are escaped (`escapeHtml`) unless they are nested `html` results. URLs for `href`/`src` go through `safeUrl` (absolute http(s) only). Never assign raw strings to `innerHTML`.
- Views (`data-view`): overview, ads, tracking, cheap, monitoring, deals. Deep-link params `query`, `view` and `strict` (built by `bot/keyboards.build_query_deep_link`) are applied by `applyLaunchParams`. Theming uses CSS custom properties and `data-theme`; `showToast()` keeps a toast for 1.6 to 4 s, depending on its length.
- API requests time out after 55 s (`DEFAULT_TIMEOUT_MS`), watchlist refresh and the opportunity board after 58 s (`LONG_TIMEOUT_MS`), just below nginx's 60 s. Error toasts show a Cyrillic `detail` from the API as is and map the other statuses to Russian messages.
- `index.html` loads the CSS and the four scripts with one shared `?v=` cache-busting version; bump all five together whenever a CSS or JS file changes (a test checks that they match).

## Testing

`asyncio_mode = "auto"`. The autouse fixtures in `tests/conftest.py`:
- keep the developer's `.env` and shell overrides of optional settings out of the suite (optional settings use their code defaults; set another value with `monkeypatch.setenv`);
- set every required env var plus `SQLITE_FOREIGN_KEYS=1`, and point `DATABASE_URL` at a fresh SQLite file per test (aiosqlite), overriding CI's Postgres settings;
- clear the `get_settings` lru_cache;
- run `create_all` before each test and `drop_all` after it;
- reset process-wide state between tests: the slowapi limiter, the bot engine singleton, the dataset cache, the Kufar pacers and the bot's throttling middlewares.

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
- `API_RATE_LIMIT_PER_MINUTE` (0: derived from `KUFAR_REQUEST_DELAY`, see Rate limiting)

Not a setting, read from the environment directly: `SQLITE_FOREIGN_KEYS` (see Data Model Notes).

Compose and script variables: `POSTGRES_DB`/`POSTGRES_USER`/`POSTGRES_PASSWORD`, `REDIS_PASSWORD`, `COMPOSE_DATABASE_URL`/`COMPOSE_REDIS_URL`, `NGINX_API_UPSTREAM`/`NGINX_API_UPSTREAM_PARAMS`, `FRONTEND_BIND_ADDRESS`, `LOCAL_API_HOST`/`LOCAL_API_PORT`.

## Conventions and Repo Notes

- ruff: line length 99, rule sets E/W/F/I/N/UP/B/SIM/TCH, isort first-party `api`, `bot`, `scheduler`; `.claude`, `.codex` and `.run` are excluded. CI runs lint (`ruff check`, `ruff format --check`, `docker compose config`) and tests as independent jobs; the Docker image build runs on pushes to `main` after both pass.
- Async everywhere: SQLAlchemy async sessions, httpx, aiogram 3.
- `.gitattributes` forces LF for `*.sh`, `Dockerfile`, `*.conf`, `*.template` and `*.yml`; CRLF breaks them inside containers.
- Local databases (`*.db`) and `.env` are never committed, since they may contain real Telegram user IDs and the bot token. `.dockerignore` keeps them, `.git` and the tests out of the image.
- `AI_HANDOFF.md` (Russian, state of April 2026) and `mvp-level1-fixed.md` (the original Level 1 plan) are historical. Trust the code and this file over them. The roadmap deliberately defers ML features such as price forecasting and clustering.
