# Technology Stack

**Analysis Date:** 2026-04-22

## Languages

**Primary:**
- Python 3.12 — Backend services (API, bot, scheduler), all in `api/`, `bot/`, `scheduler/`
- JavaScript (ES2020+, vanilla) — Frontend single-page app, all in `frontend/js/`

**Secondary:**
- HTML5/CSS3 — Frontend markup and styling (`frontend/index.html`, `frontend/css/style.css`)
- SQL (PostgreSQL dialect) — Database schema via SQLAlchemy models and Alembic migrations
- Nginx config — Reverse proxy routing (`nginx/default.conf`)
- Bash — Local dev orchestration (`start-local.sh`, `stop-local.sh`)
- Docker Compose YAML — Infrastructure definition (`docker-compose.yml`)

## Runtime

**Environment:**
- Python 3.12 (specified in `.python-version` and `pyproject.toml`)
- No Node.js runtime required (frontend is static, no build step)

**Package Manager:**
- uv — Python dependency management with lockfile
- Lockfile: `uv.lock` (present)
- Config: `pyproject.toml`

## Frameworks

**Core:**
- FastAPI >=0.115.0 — REST API framework (`api/main.py`)
- aiogram >=3.15.0 — Telegram Bot framework (`bot/main.py`)
- APScheduler >=3.10.4 — Job scheduling for tracker checks (`scheduler/collector.py`)
- SQLAlchemy >=2.0.36 (async) — ORM with asyncpg driver (`api/database.py`, `api/models.py`)
- Alembic >=1.14.0 — Database migrations (`migrations/`)

**Frontend:**
- Vanilla JS (no framework) — Module pattern with factory functions
- Chart.js 4.4.7 — Data visualization (loaded via CDN)
- Telegram WebApp SDK — `telegram-web-app.js` loaded from `telegram.org`

**Testing:**
- pytest >=8.3.0 — Test runner
- pytest-asyncio >=0.24.0 — Async test support (`asyncio_mode = "auto"`)
- pytest-cov >=6.0.0 — Coverage reporting
- aiosqlite >=0.20.0 — SQLite for isolated test DBs

**Build/Dev:**
- uv — Dependency resolution and venv management
- ruff >=0.8.0 — Linting and formatting (rules: E/W/F/I/N/UP/B/SIM/TCH, line-length 99)
- Docker — Multi-service containerization
- Nginx 1.27-alpine — Static file serving and reverse proxy

## Key Dependencies

**Critical:**
- asyncpg >=0.30.0 — Async PostgreSQL driver (core data layer)
- redis[hiredis] >=5.2.0 — Caching with C extension for performance
- httpx >=0.27.0 — Async HTTP client for Kufar API, AI API, currency rates
- httpx-socks >=0.10.0 — SOCKS proxy support for AI API calls
- pydantic >=2.10.0 — Data validation and serialization (schemas in `api/schemas.py`)
- pydantic-settings >=2.6.0 — Environment configuration (`api/config.py`)
- slowapi >=0.1.9 — Rate limiting per user/IP (`api/limiter.py`)
- beautifulsoup4 >=4.12.0 — HTML parsing (for Kufar page scraping)
- Pillow (PIL) — Image compression for AI vision input (imported in `api/services/ai_service.py`)

**Infrastructure:**
- uvicorn[standard] >=0.32.0 — ASGI server with uvloop
- python-dotenv >=1.0.1 — `.env` file loading
- google-genai >=1.73.1 — Listed in dependencies (not imported in current codebase; may be for future use)

## Configuration

**Environment:**
- pydantic-settings loads from `.env` file (`api/config.py`)
- All secrets via `SecretStr` fields
- Debug mode (`debug=true`) bypasses Telegram auth and opens CORS to localhost

**Build:**
- `pyproject.toml` — Project metadata, dependencies, tool configs
- `Dockerfile` — Multi-service container (runs api/bot/scheduler via `$SERVICE` env var)
- `docker-compose.yml` — 5 services: api, bot, scheduler, frontend, db, redis
- `nginx/default.conf` — Reverse proxy `/api/` to backend, static files for frontend
- `migrations/alembic.ini` — Alembic migration config

## Platform Requirements

**Development:**
- Python 3.12+
- Docker and Docker Compose (for PostgreSQL :5433, Redis :6380)
- uv package manager
- cloudflared (optional, for Telegram webhook tunnel via `--with-tunnel`)

**Production:**
- Docker Compose deployment (all services containerized)
- PostgreSQL 16 (Alpine image)
- Redis 7 (Alpine image)
- Nginx 1.27-alpine for frontend
- Cloudflare Tunnel for public HTTPS (dev), or any reverse proxy for production
- Together AI API key for AI analysis feature

---

*Stack analysis: 2026-04-22*
