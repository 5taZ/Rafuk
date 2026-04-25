# Codebase Structure

**Analysis Date:** 2026-04-22

## Directory Layout

```
[project-root]/
├── api/                     # FastAPI backend — REST API
│   ├── middleware/           # Auth middleware (Telegram HMAC verification)
│   ├── routers/              # Endpoint handlers (16 routers)
│   ├── services/             # Business logic and external integrations (13 services)
│   ├── __init__.py
│   ├── config.py             # Pydantic Settings, env var loading
│   ├── database.py           # SQLAlchemy async engine + session factory
│   ├── dependencies.py       # FastAPI dependency injection (auth, cache, session)
│   ├── limiter.py            # SlowAPI rate limiter with per-user keys
│   ├── main.py               # FastAPI app factory, lifespan, router registration
│   ├── models.py             # SQLAlchemy ORM models (10 models + 4 mixins)
│   ├── schemas.py            # Pydantic request/response schemas
│   └── validators.py         # Shared validators (query length cap)
├── bot/                      # aiogram 3 Telegram bot
│   ├── handlers/             # Command and callback handlers
│   ├── __init__.py
│   ├── database.py           # Singleton engine for bot process
│   ├── keyboards.py          # Inline keyboard builders
│   └── main.py               # Dispatcher setup, polling loop
├── scheduler/                # APScheduler background jobs
│   ├── __init__.py
│   └── collector.py          # Tracker checking, notifications, cleanup
├── frontend/                 # Vanilla JS Telegram Mini App
│   ├── css/
│   │   └── style.css         # Single stylesheet with CSS custom properties for theming
│   ├── js/                   # 18 JS modules (factory function pattern)
│   │   ├── app.js            # Entry point — creates core/renderers/actions, binds events
│   │   ├── app_core.js       # State, DOM cache, formatters, utilities
│   │   ├── app_renderers.js  # Composition hub for renderer modules
│   │   ├── app_actions.js    # Composition hub for API/action modules
│   │   ├── render_core.js    # Toast, error bar, loading, tabs, panels, summary
│   │   ├── render_cards.js   # Listing cards, deal cards, watchlist cards
│   │   ├── render_views.js   # View switching, hero stats, inputs
│   │   ├── render_modals.js  # Detail modal, expenses modal
│   │   ├── render_charts.js  # Price distribution, history, profit charts
│   │   ├── render_trackers.js # Tracker cards, events with virtual scrolling
│   │   ├── api_core.js       # HTTP primitives (getJson, postJson, deleteJson), query builder
│   │   ├── api_listings.js   # Search, listings, detail, segments, geography, history, deals
│   │   ├── api_trackers.js   # Tracker CRUD, event loading
│   │   ├── api_events.js     # All DOM event binding (click, touch, keyboard)
│   │   ├── api_watchlist.js  # Watchlist CRUD
│   │   ├── api_leads.js      # Lead/deal CRUD, expense management
│   │   ├── api_ai.js         # AI analysis modal with progress animation
│   │   └── virtual_list.js   # Virtual scrolling for large lists
│   └── index.html            # SPA shell (served by Nginx)
├── migrations/               # Alembic database migrations
│   ├── versions/             # 18 migration files
│   └── env.py                # Alembic environment config
├── nginx/                    # Nginx configuration
│   └── default.conf          # Reverse proxy: /api/ -> backend:8010, static files
├── tests/                    # Test suite (30 test files)
│   ├── conftest.py           # Shared fixtures (async client, mock DB, mock settings)
│   └── test_*.py             # Per-feature test files
├── scripts/                  # Utility scripts
│   └── partition_snapshots.sql # SQL for snapshot table partitioning
├── assets/                   # Static assets served by Nginx
├── .run/                     # Runtime PIDs and logs (created by start-local.sh)
├── .github/workflows/        # CI/CD pipelines
├── .env                      # Environment variables (NOT committed)
├── .env.example              # Example env vars template
├── docker-compose.yml        # Multi-service Docker Compose (api, bot, scheduler, frontend, db, redis)
├── Dockerfile                # Single Dockerfile, SERVICE env selects role
├── pyproject.toml            # Python project config, dependencies, ruff/pytest settings
├── start-local.sh            # Local development launcher (API, bot, scheduler, optional tunnel)
├── stop-local.sh             # Stops all local services
├── status-local.sh           # Shows status of running services
├── CLAUDE.md                 # AI assistant project instructions
└── uv.lock                   # uv lockfile for reproducible installs
```

## Directory Purposes

### `api/` -- FastAPI Backend
- Purpose: Core backend service providing REST API for the Telegram Mini App
- Contains: FastAPI application, 16 routers, 13 service modules, ORM models, Pydantic schemas, middleware
- Key files:
  - `api/main.py` -- App factory with lifespan management, CORS, security headers, router registration
  - `api/config.py` -- All configuration via `pydantic-settings` from `.env`
  - `api/models.py` -- SQLAlchemy ORM models: `User`, `Tracker`, `TrackerEvent`, `QuerySnapshot`, `QueryListingState`, `LeadItem`, `WatchlistItem`, `SavedSearch`, `DealExpense`, `Contact` plus mixins (`UserIDMixin`, `ActiveMixin`, `TimestampMixin`, `QueryTrackingMixin`, `TrackerFiltersMixin`)
  - `api/schemas.py` -- Pydantic request/response models for all API endpoints
  - `api/dependencies.py` -- FastAPI dependencies: `get_telegram_user`, `get_cache`, `get_currency_service`, `get_session_factory_dependency`

### `api/routers/` -- API Endpoints
- Purpose: HTTP endpoint handlers, one file per domain
- Contains: 16 router modules, each with `APIRouter` and typed endpoint functions
- Key files:
  - `api/routers/listings.py` -- Search listings, cheap deals
  - `api/routers/listing_detail.py` -- Single listing detail
  - `api/routers/trackers.py` -- Tracker CRUD
  - `api/routers/workflow.py` -- Lead/watchlist CRUD and status transitions
  - `api/routers/ai_analysis.py` -- AI analysis (`/ai/analyze`, `/ai/quick-condition`)
  - `api/routers/price_stats.py` -- Market statistics
  - `api/routers/price_history.py` -- Time-series price snapshots
  - `api/routers/segments.py` -- Price segmentation (new/used x private/shop)
  - `api/routers/geography.py` -- Geographic distribution
  - `api/routers/compare.py` -- Side-by-side query comparison
  - `api/routers/risks.py` -- Listing risk assessment
  - `api/routers/saved_searches.py` -- Saved search groups for opportunity board
  - `api/routers/contacts.py` -- Seller contacts
  - `api/routers/expenses.py` -- Deal expense tracking
  - `api/routers/export.py` -- Data export
  - `api/routers/currency.py` -- BYN/USD rates from NBRB
  - `api/routers/health.py` -- Health/readiness checks

### `api/services/` -- Business Logic
- Purpose: Core domain logic, external API clients, data transformation
- Contains: 13 service modules with no FastAPI dependencies (pure business logic)
- Key files:
  - `api/services/kufar_client.py` -- HTTP client for Kufar search API with retry/backoff and rate limiting
  - `api/services/aggregator.py` -- Price stats computation, search mode filtering, query normalization, IQR outlier removal
  - `api/services/query_pipeline.py` -- `QueryDataset` dataclass, `load_query_dataset()`, `load_segment_datasets()` -- shared query parameter parsing across routers
  - `api/services/listing_mapper.py` -- Transforms raw Kufar ad dicts into `ListingItem`/`ListingDetailResponse` schemas
  - `api/services/ai_service.py` -- OpenAI-compatible API client (Together AI), category detection, image processing, JSON parsing
  - `api/services/history_service.py` -- Query snapshot upsert, listing state sync, price change detection
  - `api/services/deal_workflow.py` -- Liquidity scoring, flip estimate computation
  - `api/services/reseller_tools.py` -- Deal scoring, query text analysis (storage/RAM extraction), tracker filter matching, config keyword matching
  - `api/services/market_signals.py` -- Anomaly detection, fair price band classification, region/area label extraction
  - `api/services/risk_detector.py` -- Listing risk assessment (too cheap, suspicious words, no photos)
  - `api/services/workflow_store.py` -- User management, lead/watchlist upsert operations
  - `api/services/cache.py` -- `RedisCache` (primary) and `MemoryCache` (fallback) implementing `CacheBackend` protocol
  - `api/services/currency_service.py` -- BYN/USD conversion via NBRB API with cache
  - `api/services/parallel_kufar.py` -- Semaphore-bounded parallel Kufar API requests

### `api/middleware/` -- Auth Middleware
- Purpose: Telegram authentication verification
- Key files:
  - `api/middleware/telegram_auth.py` -- `TelegramInitData` dataclass, `verify_telegram_init_data()` HMAC-SHA256 verification

### `bot/` -- Telegram Bot
- Purpose: aiogram 3 bot for command handling and tracker notification callbacks
- Key files:
  - `bot/main.py` -- Dispatcher setup, polling loop entry point
  - `bot/database.py` -- Singleton SQLAlchemy engine for bot process
  - `bot/handlers/start.py` -- `/start`, `/help`, `/app` commands
  - `bot/handlers/tracker.py` -- Callback handlers for "To work"/"Later" buttons from tracker alerts
  - `bot/keyboards.py` -- Inline keyboard builders for mini app button and tracker alerts

### `scheduler/` -- Background Jobs
- Purpose: Periodic tracker checking and database cleanup
- Key files:
  - `scheduler/collector.py` -- `check_trackers()` (interval job), `run_cleanup()` (cron 3 AM), DB health check, event persistence, notification formatting

### `frontend/` -- Telegram Mini App
- Purpose: Single-page application for marketplace analytics UI
- Contains: 18 JS modules using factory function pattern, 1 CSS file, 1 HTML shell
- Key files:
  - `frontend/js/app.js` -- Entry point, wires core/renderers/actions together
  - `frontend/js/app_core.js` -- Central `state` object, DOM element cache, formatting utilities, Telegram theme init
  - `frontend/js/app_renderers.js` -- Composition hub for renderer sub-modules
  - `frontend/js/app_actions.js` -- Composition hub for API/action sub-modules
  - `frontend/js/api_core.js` -- `getJson`, `postJson`, `deleteJson`, `telegramHeaders()`, `buildCommonQuery()`
  - `frontend/js/virtual_list.js` -- Virtual scrolling component for tracker events

### `migrations/` -- Database Migrations
- Purpose: Alembic-managed PostgreSQL schema migrations
- Key files:
  - `migrations/env.py` -- Alembic environment configuration
  - `migrations/versions/` -- 18 migration files (20260405 through 20260410)

### `tests/` -- Test Suite
- Purpose: Automated tests for API, services, bot, and frontend structure
- Key files:
  - `tests/conftest.py` -- Shared fixtures
  - `tests/test_*.py` -- 30 test files covering API endpoints, services, bot, frontend

### `nginx/` -- Reverse Proxy
- Purpose: Nginx config for frontend serving and API proxying
- Key files:
  - `nginx/default.conf` -- Static file serving, `/api/` proxy to backend with 300s read timeout

## Key File Locations

### Entry Points
- `api/main.py`: FastAPI app factory and ASGI entry point (`uvicorn api.main:app`)
- `bot/main.py`: Bot polling loop entry point (`python -m bot.main`)
- `scheduler/collector.py`: Scheduler entry point (`python -m scheduler.collector`)
- `frontend/js/app.js`: Frontend SPA entry point (`DOMContentLoaded`)
- `Dockerfile`: Docker entry point with `SERVICE` env var selector
- `start-local.sh`: Local development launcher script

### Configuration
- `api/config.py`: Pydantic Settings class loading from `.env`
- `pyproject.toml`: Python dependencies, ruff/pytest config
- `docker-compose.yml`: Multi-service orchestration (6 services)
- `nginx/default.conf`: Reverse proxy configuration
- `.env.example`: Template for required environment variables
- `api/validators.py`: Shared validation constants (`MAX_QUERY_LENGTH = 255`)

### Core Logic
- `api/services/aggregator.py`: Price computation, search mode filtering, query normalization
- `api/services/query_pipeline.py`: Shared `QueryDataset` and dataset loading across routers
- `api/services/reseller_tools.py`: Deal scoring engine with configurable weights
- `api/services/ai_service.py`: AI analysis with category-aware prompts and image processing

### Data Models
- `api/models.py`: All SQLAlchemy ORM models (10 models, 4 mixins, ~490 lines)
- `api/schemas.py`: All Pydantic request/response schemas (~600 lines)
- `migrations/versions/`: 18 Alembic migration files

### Testing
- `tests/conftest.py`: Shared fixtures
- `tests/test_*.py`: 30 test files

## Naming Conventions

### Files
- Python modules: `snake_case.py` (e.g., `kufar_client.py`, `deal_workflow.py`)
- Frontend JS: `snake_case.js` for modules (e.g., `app_core.js`, `render_cards.js`)
- Migrations: `YYYYMMDD_NNNN_description.py` (e.g., `20260405_0001_create_trackers.py`)
- Tests: `test_<feature>.py` (e.g., `test_listings.py`, `test_ai_analysis.py`)

### Directories
- Python packages: `snake_case/` (e.g., `api/`, `bot/handlers/`)
- Frontend: `js/`, `css/` (flat structure, no subdirectories)

### Python
- Service modules: `snake_case` matching domain (e.g., `kufar_client`, `history_service`, `reseller_tools`)
- Router modules: `snake_case` matching endpoint prefix (e.g., `listings.py` for `/listings`, `ai_analysis.py` for `/ai`)
- Models: PascalCase classes (`Tracker`, `LeadItem`, `WatchlistItem`)
- Schemas: PascalCase with suffix indicating type (`TrackerCreate`, `TrackerRead`, `TrackerUpdate`)
- Mixins: PascalCase with suffix `Mixin` (`UserIDMixin`, `ActiveMixin`, `TimestampMixin`)

### Frontend JavaScript
- Factory functions: `createXxx` pattern (e.g., `createAppCore`, `createRenderCards`, `createApiCore`)
- State fields: `camelCase` (e.g., `strictSearch`, `discountFromPercent`, `trackerEvents`)
- DOM element references: `camelCase` matching element IDs (e.g., `searchInput`, `listingsList`)
- Render functions: `renderXxx` prefix (e.g., `renderAll`, `renderDetailModal`, `renderTrackers`)

## Where to Add New Code

### New API Endpoint
- Router: `api/routers/<domain>.py` -- create new file or add to existing router
- Register in: `api/main.py` `create_app()` -- add `app.include_router(<domain>.router, prefix="/api/v1")`
- Schema: `api/schemas.py` -- add request/response Pydantic models
- Service logic: `api/services/<domain>.py` -- create new service or extend existing

### New Database Table
- Model: `api/models.py` -- add new class extending `Base` with appropriate mixins
- Migration: `uv run alembic -c migrations/alembic.ini revision --autogenerate -m "description"`
- Schema: `api/schemas.py` -- add `Create`, `Read`, `Update` schemas

### New Frontend View/Feature
- Module: `frontend/js/<module_name>.js` -- create new factory function `createXxx(context)`
- Wire into renderers: `frontend/js/app_renderers.js` -- import and instantiate in composition hub
- Wire into actions: `frontend/js/app_actions.js` -- import and instantiate in composition hub
- State: `frontend/js/app_core.js` -- add fields to `state` object
- DOM: `frontend/index.html` -- add HTML structure
- Styles: `frontend/css/style.css` -- add CSS rules

### New Service/Business Logic
- File: `api/services/<domain>.py` -- new file for distinct domain logic
- Keep services free of FastAPI dependencies -- they receive config, clients, and sessions as parameters
- Use Protocols for dependency injection (e.g., `SupportsSearchAllAds` in `query_pipeline.py`)

### New Bot Command
- Handler: `bot/handlers/<domain>.py` -- add new router with command handlers
- Register in: `bot/main.py` `build_dispatcher()` -- add `dispatcher.include_router(<domain>_router)`

### New Scheduler Job
- Job function: `scheduler/collector.py` -- add new async function
- Register in: `scheduler/collector.py` `create_scheduler()` -- add `scheduler.add_job(...)`

### New Frontend API Module
- File: `frontend/js/api_<domain>.js` -- create new factory function
- Wire into: `frontend/js/app_actions.js` -- import and call in `createAppActions`

### New Frontend Renderer Module
- File: `frontend/js/render_<domain>.js` -- create new factory function
- Wire into: `frontend/js/app_renderers.js` -- import and call in `createAppRenderers`

## Special Directories

### `.run/`
- Purpose: Runtime PIDs and log files for local development
- Generated: Yes (created by `start-local.sh`)
- Committed: No (in `.gitignore`)

### `migrations/versions/`
- Purpose: Alembic migration files for database schema changes
- Generated: Yes (via `alembic revision --autogenerate`)
- Committed: Yes

### `assets/`
- Purpose: Static assets served by Nginx at `/assets/`
- Generated: No
- Committed: Yes

### `.planning/`
- Purpose: Codebase analysis documents for AI-assisted development
- Generated: Yes (by `/gsd-map-codebase`)
- Committed: Yes

---

*Structure analysis: 2026-04-22*
