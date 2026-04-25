# Coding Conventions

**Analysis Date:** 2026-04-22

## Naming Patterns

**Files:**
- Python: `snake_case.py` — e.g., `kufar_client.py`, `telegram_auth.py`, `currency_service.py`
- Test files: `test_{module_name}.py` — e.g., `test_aggregator.py`, `test_kufar_client.py`
- Frontend JS: `camelCase.js` with dot-separated prefixes — e.g., `api_core.js`, `app_actions.js`, `render_cards.js`
- CSS: single `style.css` file at `frontend/css/style.css`
- Migrations: Alembic auto-generated files in `migrations/versions/`

**Functions:**
- Python: `snake_case` — e.g., `compute_price_stats()`, `normalize_price_byn()`, `build_query_key()`
- Private helpers: prefix with underscore — e.g., `_remove_outliers()`, `_percentile()`, `_build_system_prompt()`
- Frontend JS: `camelCase` — e.g., `createApiCore()`, `escapeHtml()`, `safeUrl()`
- Frontend factory functions: `create{Module}` pattern — e.g., `createAppCore()`, `createApiCore(context)`

**Variables:**
- Python: `snake_case` — e.g., `cache_key`, `sorted_prices`, `tracker_ids`
- Constants: `UPPER_SNAKE_CASE` — e.g., `MAX_RETRIES`, `KOPECKS`, `KUFAR_BASE_URL`
- Frontend JS: `camelCase` for variables and state properties — e.g., `searchAbortController`, `discountFromPercent`

**Types/Classes:**
- SQLAlchemy models: `PascalCase` — e.g., `Tracker`, `LeadItem`, `WatchlistItem`
- Pydantic schemas: `PascalCase` with suffix convention — e.g., `TrackerCreate`, `TrackerRead`, `TrackerUpdate`
- Dataclasses: `PascalCase` with optional underscore prefix for private ones — e.g., `PriceStats`, `_ScoringConfig`
- Frontend: no formal types (vanilla JS)

**Mixins (models):**
- Use `PascalCase` with `Mixin` suffix — e.g., `UserIDMixin`, `ActiveMixin`, `TimestampMixin`, `QueryTrackingMixin`, `TrackerFiltersMixin`
- Location: `api/models.py` — defined in the same file as models that use them

## Code Style

**Formatting:**
- Tool: `ruff format` (compatible with Black)
- Line length: 99 characters
- Config: `pyproject.toml` under `[tool.ruff]`

**Linting:**
- Tool: `ruff check`
- Rule set: `E`, `W`, `F`, `I`, `N`, `UP`, `B`, `SIM`, `TCH`
- Ignored rules: `B008` (function calls in argument defaults), `TC001`/`TC002`/`TC003` (type-checking only blocks)
- isort: configured with `known-first-party = ["api", "bot", "scheduler"]`
- Target: Python 3.12

**Run commands:**
```bash
uv run ruff check .              # Lint
uv run ruff check --fix .        # Lint with auto-fix
uv run ruff format --check .     # Check formatting
```

## Import Organization

**Order (enforced by ruff/isort):**
1. Standard library (`from __future__ import annotations` always first)
2. Third-party packages (`fastapi`, `sqlalchemy`, `pydantic`, `httpx`, etc.)
3. First-party (`api.*`, `bot.*`, `scheduler.*`)

**Pattern:**
```python
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from api.config import Settings, get_settings
from api.services.aggregator import PriceStats
```

**Path Aliases:**
- No path aliases configured. All imports use full package paths (`api.services.cache`, `bot.handlers.start`).

## Error Handling

**Python Backend:**
- Use `from __future__ import annotations` in every file (delays type evaluation)
- Custom exceptions inherit from `Exception` — e.g., `KufarAPIError(Exception)` in `api/services/kufar_client.py`
- FastAPI endpoints raise `HTTPException` with explicit status codes — e.g., `HTTPException(status_code=404, detail="Tracker not found")`
- Database errors: catch `IntegrityError` for constraint violations, rollback and re-raise as `HTTPException(409)`
- Global exception handler in `api/main.py` catches all unhandled exceptions, returns `{"detail": "..."}` JSON with status 500
- External API calls: wrap in try/except, use retry with exponential backoff (see `KufarClient`)
- Logging: use `logging.getLogger(__name__)` per module, `logger.warning()` for recoverable errors, `logger.error()` for failures

**Frontend:**
- `requestJson()` in `frontend/js/api_core.js` handles HTTP errors uniformly — parses `detail` from JSON body, falls back to generic Russian-language messages
- AbortController with 90s default timeout for all fetch requests
- `try/catch` around JSON parsing of error responses

## Logging

**Framework:** Python `logging` module (stdlib)

**Pattern:**
```python
import logging
logger = logging.getLogger(__name__)
```

**Usage:**
- `logger.warning()` for recoverable issues (Redis failures, corrupt cache, Kufar retries)
- `logger.error()` for unhandled exceptions (global handler in `api/main.py`)
- `logging.basicConfig(level=logging.INFO)` in scheduler (`scheduler/collector.py`)
- Frontend: `console.error()` for debug purposes; user-facing errors shown via toast notifications

## Comments

**When to Comment:**
- Module-level docstrings for services (e.g., `ai_service.py` has a module docstring with config instructions)
- Inline comments for non-obvious business logic (e.g., kopeck conversion, strict variant matching)
- Comment blocks with `# ===` separators for section headers within larger files

**Docstrings:**
- Used on classes and key functions — e.g., `KufarClient`, `MemoryCache`, `verify_telegram_init_data()`
- Triple-double-quoted (`"""..."""`) style
- Russian-language strings in user-facing schemas (error details, UI labels)

**JSDoc:**
- Frontend uses block comments (`/** ... */`) at the top of factory functions to describe module purpose
- e.g., `frontend/js/api_core.js`: `/** api_core.js — Shared HTTP primitives... */`

## Function Design

**Size:** Keep functions focused. Router handlers in `api/routers/` delegate to services. Business logic lives in `api/services/`.

**Parameters:**
- Use keyword arguments with type annotations: `async def search(self, query: str, size: int = 200)`
- Use `**kwargs` sparingly — only when forwarding to underlying APIs
- Pydantic models for request/response schemas — separate `Create`, `Read`, `Update` schemas

**Return Values:**
- Services return Pydantic models or typed dataclasses — e.g., `PriceStats`, `QueryDataset`
- Routers return Pydantic response models with `response_model=` parameter
- Use `None` to signal missing/unavailable data (not empty strings or sentinel values)

**Async:**
- All database operations are async (`SQLAlchemy async sessions`)
- All HTTP client operations are async (`httpx.AsyncClient`)
- Use `async with` for session management: `async with session_factory() as session`

## Module Design

**Exports:**
- Python: no `__all__` exports. Public API defined by what's imported elsewhere.
- Frontend: factory functions return objects with named exports — e.g., `return { telegramHeaders, requestJson, getJson, postJson, deleteJson }`

**Barrel Files:**
- Not used in Python. Direct imports from modules.
- Frontend: `app.js` is the entry point that wires together all factory functions.

## Database Conventions

**Models:**
- Use `Mapped[type]` annotation style (SQLAlchemy 2.0+)
- Use `mapped_column()` for column definitions
- Always set both `default` (Python) and `server_default` (DB) for boolean/integer defaults
- Use `cascade="all, delete-orphan"` on relationships
- Use `ondelete="CASCADE"` on foreign keys
- Define `__table_args__` tuple with explicit indexes and constraints
- Override `__init__` to set defaults with `kwargs.setdefault()`

**Migrations:**
- Alembic with config at `migrations/alembic.ini`
- Run: `uv run alembic -c migrations/alembic.ini upgrade head`

## Frontend Conventions

**Module Pattern:**
- Every JS file exports a factory function: `function createXxx(context) { ... return { ... }; }`
- Factory receives a `context` object with destructured dependencies
- `app.js` wires everything together via `createAppCore()` -> `createAppRenderers()` -> `createAppActions()`

**XSS Prevention:**
- Use `escapeHtml()` for all text content inserted via `innerHTML`
- Use `safeUrl()` for all `href` and `src` attributes (blocks `javascript:` and `data:` schemes)
- Both defined in `frontend/js/render_core.js`, propagated to sub-modules via context

**State Management:**
- Single `state` object in `createAppCore()` (`frontend/js/app_core.js`)
- All DOM updates go through `render*()` functions
- Event handlers call API, update state, then call render functions
- Cross-module communication via `context._hooks` object

**Views:**
- Tab-based navigation via `data-view` attributes
- Views: overview, ads, tracking, cheap, monitoring, deals

**Theme:**
- CSS custom properties for dark/light theming
- `data-theme` attribute on document element
- Dark is default (in `:root`), light overrides in `[data-theme="light"]`

**No build step:** All JS loaded via `<script>` tags in `frontend/index.html`. No bundler, no transpiler.

## Rate Limiting

- Use `slowapi` limiter with `@limiter.limit("N/minute")` decorator on mutation endpoints
- Key function in `api/limiter.py` uses Telegram user_id when available, falls back to IP
- All write endpoints: `20/minute` default
- AI endpoints: `10/minute`
- Public read endpoints: `30/minute`

## Currency Conventions

- All database prices stored in BYN
- API accepts `currency` parameter and converts using `CurrencyService`
- Frontend stores `state.usdRateByn` for display conversion
- Kufar API prices returned in kopecks — divide by 100 (`KOPECKS = 100` constant in `api/services/aggregator.py`)

---

*Convention analysis: 2026-04-22*
