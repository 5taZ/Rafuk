# Rafuks — Kufar Marketplace Analytics

## Project Overview

**Rafuks** is a Telegram Mini App for analyzing prices and listings on the Belarusian marketplace [kufar.by](https://kufar.by). It scrapes marketplace data, computes price statistics, detects price drops, and notifies users of profitable deals.

The system is built as a Python monorepo with three core services (API, Bot, Scheduler), a vanilla-JS frontend, and Docker Compose for orchestration.

### Architecture

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│   Frontend  │────>│     API     │────>│   Kufar     │
│ (nginx/8081)│     │ (FastAPI)   │     │   .by       │
└─────────────┘     └─────────────┘     └─────────────┘
                           │
                    ┌────────────┐
                    ▼             ▼
               ┌─────────┐  ┌─────────
               │PostgreSQL│  │  Redis  │
               │  (:5433) │  │ (:6380) │
               └─────────  └─────────┘

┌─────────────┐     ┌─────────────┐
│    Bot      │     │  Scheduler  │
│ (aiogram)   │────>│ (APScheduler)│
└─────────────┘     └─────────────┘
```

### Key Components

| Service | Tech | Port | Description |
|---------|------|------|-------------|
| **API** | FastAPI + uvicorn | `:8010` (local), `:8000` (Docker) | REST API with rate limiting, 18 routers |
| **Bot** | aiogram 3.x | — | Telegram bot with `/app` and `/start` commands |
| **Scheduler** | APScheduler + aiogram | — | Background price tracking, deal alerts, history snapshots |
| **Frontend** | Vanilla JS + Chart.js | `:8081` (nginx) | Telegram Mini App UI (dark/light theme) |
| **DB** | PostgreSQL 16 | `:5433` | Main data store |
| **Cache** | Redis 7 | `:6380` | Rate limiting, currency rates |

### Frontend

The frontend is a **Telegram Mini App** (no framework — pure vanilla JS, modular architecture):

- **Module pattern**: Each file exports a factory function (`createAppCore()`, `createApiEvents()`, etc.)
- **Deferred loading**: All 17 JS files load with `defer`, no render-blocking scripts
- **Dark/light theme**: Synced with Telegram's color scheme
- **Key features**: Price charts, listing comparison, auto-tracking, lead/purchase management, watchlist, filter dropdown
- **Design**: Custom CSS with design tokens, Rubik + JetBrains Mono fonts
- **Design rules**: Always follow Impeccable methodology (`.qwenrules`)

#### Frontend Architecture

The frontend uses a **module composition pattern** — no build step, no framework. Each JS file defines a factory function that returns an object, and these are composed together in `app.js`:

```javascript
const core = createAppCore();
const actions = createAppActions({ ...core });
const renderers = createAppRenderers({ ...core, actions });
```

This keeps the codebase modular while avoiding any bundling complexity.

#### Recent UX Improvements (April 2026)

- **Listings badge**: Changed from `"X из Y"` (confusing) to `"X объявлений"` (clear count)
- **Coverage metric**: Compact format `"33204 (188 с ценой)"` instead of separate "С ценой" pill
- **Category tabs → Filter dropdown**: Horizontal category tabs replaced with a filter button (🔧) that opens a dropdown containing:
  - **Categories** — all categories from search with counts
  - **Condition** — Все / Новое / Б/у (client-side filtering)
  - **Seller** — Все / Частное / Магазин (client-side filtering)
- **Recent searches**: Moved back to global position under search bar (accessible from all views)
- **Client-side filtering**: Condition and seller filters are applied client-side in `render_cards.js` via `applyFilters()` — no backend API call needed

### Backend

- **18 API routers**: price_stats, price_history, listings, segments, geography, currency, compare, listing_detail, trackers, saved_searches, workflow, contacts, expenses, health, risks, export
- **Service layer**: `aggregator`, `kufar_client`, `parallel_kufar`, `currency_service`, `history_service`, `risk_detector`, `deal_workflow`, `cache` (Redis → Memory fallback)
- **ORM**: SQLAlchemy 2.0 async with asyncpg
- **Migrations**: Alembic
- **Rate limiting**: slowapi

### Database Models

Key entities: `User`, `Tracker`, `TrackerEvent`, `SavedSearch`, `Lead`, `Watchlist`, `Expense`, `CurrencyRate`, `PriceHistory`

### CI/CD

GitHub Actions workflow (`.github/workflows/ci.yml`) runs:
- `ruff check` (linting)
- `pytest` (test suite — 31 test files)

## Building and Running

### Prerequisites

- **Python 3.12+** with `uv` installed (`pip install uv`)
- **Docker & Docker Compose** (for containerized deployment)
- **PostgreSQL 16** and **Redis 7** (for local development without Docker)
- **Bot token** from [@BotFather](https://t.me/botfather)

### Environment Setup

```bash
# Copy and edit environment
cp .env.example .env
# Edit .env with your values (BOT_TOKEN, DATABASE_URL, etc.)
```

### Local Development (without Docker)

```bash
# Install dependencies
uv sync

# Run database migrations
uv run alembic -c migrations/alembic.ini upgrade head

# Start all services
./start-local.sh

# Or start with Cloudflare Tunnel for Telegram Mini App
./start-local.sh --with-tunnel

# Check status
./status-local.sh

# Stop all services
./stop-local.sh
```

**Service ports (local):**
- API: `http://127.0.0.1:8010`
- Frontend: `http://127.0.0.1:8081`
- PostgreSQL: `localhost:5433`
- Redis: `localhost:6380`

### Docker Compose

```bash
# Build and start all services
docker compose up -d --build

# Check status
docker compose ps

# View logs
docker compose logs -f api
docker compose logs -f bot
docker compose logs -f scheduler
docker compose logs -f frontend

# Stop all
docker compose down
```

### Running Tests

```bash
# All tests
uv run pytest

# With coverage
uv run pytest --cov

# Specific test
uv run pytest tests/test_kufar_client.py -v
```

### Linting

```bash
# Check only
uv run ruff check .

# Fix auto-fixable issues
uv run ruff check --fix .
```

### Database Migrations

```bash
# Create a new migration
uv run alembic -c migrations/alembic.ini revision --autogenerate -m "description"

# Apply migrations
uv run alembic -c migrations/alembic.ini upgrade head

# Rollback one migration
uv run alembic -c migrations/alembic.ini downgrade -1
```

## Project Structure

```
myProjetctKufar/
├── api/                      # FastAPI backend
│   ├── main.py               # App factory, lifespan, middleware
│   ├── config.py             # Pydantic settings
│   ├── database.py           # Async engine & session factory
│   ├── models.py             # SQLAlchemy models
│   ├── schemas.py            # Pydantic request/response schemas
│   ├── validators.py         # Input validation
│   ├── limiter.py            # Rate limiter config
│   ├── dependencies.py       # FastAPI dependencies
│   ├── routers/              # 18 API route modules
│   └── services/             # 15 service modules (business logic)
├── bot/                      # Telegram bot (aiogram)
│   ├── main.py               # Bot entry point
│   ├── database.py           # Bot-specific DB engine
│   ├── keyboards.py          # Inline/reply keyboards
│   └── handlers/             # Command handlers (start, tracker)
├── scheduler/                # Background jobs
│   ├── collector.py          # APscheduler jobs (price checks, alerts)
├── frontend/                 # Telegram Mini App
│   ├── index.html            # Main HTML (~730 lines)
│   ├── css/style.css         # All styles (~4900 lines)
│   └── js/                   # 17 JS modules (modular pattern)
│       ├── app.js            # App entry point
│       ├── app_core.js       # Core state & helpers
│       ├── app_actions.js    # Action composition
│       ├── app_renderers.js  # Renderer composition
│       ├── api_core.js       # HTTP client
│       ├── api_events.js     # DOM event binding
│       ├── api_listings.js   # Listings API
│       ├── api_trackers.js   # Trackers API
│       ├── api_leads.js      # Leads API
│       ├── api_watchlist.js  # Watchlist API
│       ├── render_core.js    # Core rendering
│       ├── render_cards.js   # Card rendering + client-side filtering
│       ├── render_views.js   # View rendering + filter dropdown
│       ├── render_modals.js  # Modal rendering
│       ├── render_charts.js  # Chart.js integration
│       ├── render_trackers.js# Tracker rendering
│       └── virtual_list.js   # Virtual scrolling
├── migrations/               # Alembic migrations
├── nginx/                    # Nginx config for frontend
├── tests/                    # 31 test files
├── scripts/                  # Utility scripts
├── assets/                   # Static assets (logo, images)
├── docker-compose.yml        # Full stack orchestration
├── Dockerfile                # Multi-service Docker image
├── pyproject.toml            # Python project config
├── start-local.sh            # Local dev startup
├── stop-local.sh             # Local dev shutdown
└── status-local.sh           # Service status check
```

## API Endpoints

All endpoints are under `/api/v1/`:

| Router | Endpoints | Description |
|--------|-----------|-------------|
| `price_stats` | `GET /price-stats` | Price statistics for a query |
| `price_history` | `GET /price-history` | Historical price data |
| `listings` | `GET /listings` | Search listings with sorting |
| `segments` | `GET /segments` | Price segmentation (new/used, seller type) |
| `geography` | `GET /geography` | Regional price analysis |
| `currency` | `GET /currency-rates` | Currency exchange rates |
| `compare` | `GET /compare` | Compare two queries |
| `listing_detail` | `GET /listings/:id` | Single listing detail |
| `trackers` | `GET/POST/PATCH/DELETE /trackers` | Auto-tracking CRUD |
| `saved_searches` | `GET/POST /saved-searches` | Saved search management |
| `workflow` | `GET/POST /workflow` | Lead pipeline management |
| `expenses` | `GET/POST/DELETE /expenses` | Expense tracking |
| `contacts` | `GET /contacts` | Contact info |
| `health` | `GET /health/ready` | Health check |
| `risks` | `GET /risks/:id` | Risk analysis |
| `export` | `GET /export/*` | CSV export |

## Telegram Bot Commands

- `/start` — Greeting and onboarding
- `/app` — Open the Mini App

## Development Conventions

### Python

- **Linter**: `ruff` with `E, W, F, I, N, UP, B, SIM, TCH` rules, line length 99
- **Imports**: Sorted with known-first-party: `["api", "bot", "scheduler"]`
- **Type hints**: Use `X | Y` syntax (Python 3.10+), not `Union[X, Y]`
- **Testing**: `pytest-asyncio` in auto mode, `pytest-cov`

### Frontend

- **No framework**: Pure vanilla JS, no build step
- **Module composition**: Factory functions composed in `app.js`
- **Deferred loading**: All scripts use `defer` attribute
- **CSS**: Custom design tokens in `style.css`, no CSS framework
- **Accessibility**: `prefers-reduced-motion` respected, minimum 44px touch targets
- **Impeccable methodology**: Always follow design principles from `.qwenrules`
  - Primary reference: https://impeccable.style
  - Available commands: `/shape`, `/polish`, `/critique`, `/animate`

### Service Communication

- **Bot → API**: Uses `API_BASE_URL` from environment
- **Scheduler → API**: Calls API endpoints for price checks
- **Frontend → API**: Direct HTTP requests to `API_BASE_URL`
- **Frontend → Telegram**: Uses `Telegram.WebApp` SDK
- **All services**: Share the same PostgreSQL database and Redis cache

### Key Environment Variables

| Variable | Description |
|----------|-------------|
| `BOT_TOKEN` | Telegram bot token (required) |
| `DATABASE_URL` | PostgreSQL connection string |
| `REDIS_URL` | Redis connection URL |
| `API_BASE_URL` | HTTPS URL for the API |
| `MINI_APP_URL` | HTTPS URL for the Mini App |
| `KUFAR_REQUEST_DELAY` | Delay between Kufar requests (default: 1.0s) |
| `KUFAR_PARALLEL_SEMAPHORE` | Parallel request limit (default: 3) |
| `ALERT_CHECK_INTERVAL` | Scheduler check interval (default: 30s) |
| `CACHE_TTL_SECONDS` | Cache TTL (default: 300s) |
