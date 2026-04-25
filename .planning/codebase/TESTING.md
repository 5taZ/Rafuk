# Testing Patterns

**Analysis Date:** 2026-04-22

## Test Framework

**Runner:**
- pytest >= 8.3.0
- pytest-asyncio >= 0.24.0 with `asyncio_mode = "auto"`
- pytest-cov >= 6.0.0
- Config: `pyproject.toml` under `[tool.pytest.ini_options]`

**Assertion Library:**
- pytest built-in assertions (`assert`, `pytest.approx()`, `pytest.raises()`)
- No separate assertion library

**Run Commands:**
```bash
uv run pytest                              # Run all tests
uv run pytest tests/test_specific.py      # Run specific file
uv run pytest -k "test_name"              # Run by name pattern
uv run pytest tests/ -v --cov=api --cov=bot --cov=scheduler --cov-report=xml  # With coverage
```

**CI Pipeline:**
- GitHub Actions (`.github/workflows/ci.yml`)
- Lint job: `ruff check .` + `ruff format --check .`
- Test job: runs on PostgreSQL 16 service container, executes with `TEST_DATABASE_URL` env var
- Coverage uploaded to Codecov
- Docker build job on main branch pushes

## Test File Organization

**Location:**
- Separate `tests/` directory at project root
- All test files in `tests/` — NOT co-located with source

**Naming:**
- Pattern: `test_{module_or_feature}.py`
- Examples: `test_aggregator.py`, `test_kufar_client.py`, `test_trackers_api.py`

**Structure:**
```
tests/
├── __init__.py
├── conftest.py              # Global fixtures (env setup, DB tables, sample data)
├── test_aggregator.py       # Unit tests for aggregator service
├── test_ai_analysis.py      # Integration tests for AI analysis endpoint
├── test_app_js_syntax.py    # Frontend JS syntax validation
├── test_bot_price.py        # Bot price notification tests
├── test_bot_start.py        # Bot command handler tests
├── test_bot_tracker.py      # Bot tracker callback tests
├── test_cache.py            # Unit tests for cache implementations
├── test_compare_api.py      # Integration tests for compare endpoint
├── test_config.py           # Unit tests for settings/config
├── test_currency.py         # Integration tests for currency endpoint
├── test_currency_service.py # Unit tests for currency service
├── test_docker_config.py    # Docker config validation
├── test_frontend_structure.py # Frontend HTML/CSS structure tests
├── test_geography.py        # Integration tests for geography endpoint
├── test_health.py           # Integration tests for health endpoints
├── test_kufar_client.py     # Unit tests for Kufar API client
├── test_listing_detail.py   # Integration tests for listing detail
├── test_listings.py         # Integration tests for listings endpoint
├── test_main.py             # App creation smoke tests
├── test_models.py           # SQLAlchemy model/DB schema tests
├── test_parallel_kufar.py   # Parallel Kufar request tests
├── test_price_history.py    # Integration tests for price history
├── test_price_stats.py      # Integration tests for price stats
├── test_reseller_tools.py   # Unit tests for reseller tools
├── test_saved_searches.py   # Integration tests for saved searches
├── test_segments.py         # Integration tests for segments endpoint
├── test_telegram_auth.py    # Unit tests for Telegram auth middleware
├── test_trackers_api.py     # Integration tests for tracker CRUD
├── test_workflow_api.py     # Integration tests for lead/watchlist CRUD
```

## Test Structure

**Suite Organization:**
```python
# Standard pattern for sync tests
from __future__ import annotations

import pytest

from api.services.aggregator import compute_price_stats


def test_compute_price_stats_basic() -> None:
    stats = compute_price_stats([1000.0, 2000.0, 3000.0])
    assert stats.count == 3
    assert stats.median == pytest.approx(2000.0)
```

```python
# Standard pattern for async tests (asyncio_mode = "auto" — no decorator needed)
from __future__ import annotations

import pytest

from api.services.cache import MemoryCache


async def test_memory_cache_roundtrip() -> None:
    cache = MemoryCache()
    await cache.set("foo", "bar")
    assert await cache.get("foo") == "bar"
```

```python
# Standard pattern for integration tests with TestClient
from __future__ import annotations

from fastapi.testclient import TestClient


def test_currency_endpoint_returns_rates() -> None:
    from api.dependencies import get_currency_service
    from api.main import create_app

    app = create_app()
    app.dependency_overrides[get_currency_service] = lambda: FakeCurrencyService()
    with TestClient(app) as client:
        response = client.get("/api/v1/currency-rates")
    assert response.status_code == 200
```

**Patterns:**
- `from __future__ import annotations` at the top of every test file
- Return type annotation `-> None` on all test functions
- Import statements inside test functions for integration tests (avoids circular imports, allows patching)
- Use `with TestClient(app) as client:` context manager for HTTP tests

## Fixtures

**Global Fixtures (`tests/conftest.py`):**
```python
@pytest.fixture(autouse=True)
def configure_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Sets required env vars for all tests."""
    monkeypatch.setenv("BOT_TOKEN", "7123456789:AAFtesttoken")
    # Uses SQLite by default, PostgreSQL if TEST_DATABASE_URL is set
    db_url = os.environ.get("TEST_DATABASE_URL") or f"sqlite+aiosqlite:///{tmp_path / 'test.db'}"
    monkeypatch.setenv("DATABASE_URL", db_url)
    ...

@pytest.fixture(autouse=True)
async def create_test_tables():
    """Auto-create test database tables for all tests."""
    from api.database import get_engine
    from api.models import Base
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()

@pytest.fixture
def sample_ads() -> list[dict[str, object]]:
    """Returns 6 sample ad dicts with various conditions for testing."""
    return [...]
```

**Local Fixtures (per-test-file):**
- `mock_settings` — `MagicMock` with Kufar settings attributes (in `test_kufar_client.py`)
- `ok_response` — mocked `httpx.Response` with sample data (in `test_kufar_client.py`)
- `soup` / `css_text` — BeautifulSoup parsed HTML/CSS (in `test_frontend_structure.py`)
- `client` — `httpx.AsyncClient` with `ASGITransport` (in `test_health.py`)

**Key Points:**
- `autouse=True` fixtures set up environment and DB tables automatically for ALL tests
- Uses SQLite (`aiosqlite`) locally, PostgreSQL in CI (via `TEST_DATABASE_URL` env var)
- `get_settings.cache_clear()` called in conftest to reset cached settings between tests

## Mocking

**Framework:** `unittest.mock` (stdlib) — `MagicMock`, `AsyncMock`, `patch`

**Patterns:**

1. **Mocking external HTTP calls** — patch `httpx.AsyncClient.get`:
```python
with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=ok_response):
    client = KufarClient(mock_settings)
    result = await client.search(query="iPhone 15")
```

2. **Mocking KufarClient in integration tests** — replace the class on the router module via `monkeypatch`:
```python
monkeypatch.setattr(listings, "KufarClient", FakeKufarClient)
```
Where `FakeKufarClient` is a simple class:
```python
class FakeKufarClient:
    def __init__(self, settings) -> None:
        del settings
    async def search_all_ads(self, **kwargs) -> dict:
        del kwargs
        return {"total": len(FAKE_ADS), "ads": FAKE_ADS}
    async def aclose(self) -> None:
        return None
```

3. **Overriding FastAPI dependencies** — use `app.dependency_overrides`:
```python
app.dependency_overrides[get_telegram_user] = fake_telegram_user
app.dependency_overrides[get_cache] = lambda: MemoryCache()
app.dependency_overrides[get_currency_service] = lambda: FakeCurrencyService()
```

4. **Mocking bot handlers** — use `SimpleNamespace` + `AsyncMock`:
```python
message = SimpleNamespace(answer=AsyncMock())
await cmd_help(message)
message.answer.assert_awaited()
```

5. **Patching service functions** — monkeypatch the imported reference:
```python
monkeypatch.setattr(ai_analysis, "get_ai_service", lambda: FakeAIService())
monkeypatch.setattr(ai_analysis, "load_query_dataset", fake_load_query_dataset)
```

**What to Mock:**
- External HTTP calls (Kufar API, NBRB API, Together AI)
- Telegram bot API calls
- Redis (replace with `MemoryCache`)
- Telegram auth (replace with `fake_telegram_user`)

**What NOT to Mock:**
- Database operations — use real SQLite or PostgreSQL via test tables fixture
- Internal service logic (aggregator, reseller tools) — test directly
- Pydantic schema validation — let it run

## Fixtures and Factories

**Test Data:**
- `sample_ads` fixture in `conftest.py` provides 6 ad dicts covering: normal, zero price, anomaly, different conditions/seller types
- Per-test inline data for specific scenarios (e.g., `test_listings.py` defines `FAKE_ADS`)
- `FAKE_ADS` and `VERDICTS` constants defined at module level in integration test files

**Location:**
- Global fixtures: `tests/conftest.py`
- Per-file fixtures: defined at module level in each test file
- No separate `factories/` or `fixtures/` directory

**Helper pattern for DB seeding:**
```python
async def seed_tracker_event(session_factory) -> None:
    async with session_factory() as session:
        user_id = await ensure_user(session, telegram_user_id=123456, first_name="Test")
        await session.commit()
        session.add(TrackerEvent(...))
        await session.commit()
```

## Coverage

**Requirements:** No minimum threshold enforced in config. CI generates coverage reports.

**Coverage Targets (CI):**
- `--cov=api --cov=bot --cov=scheduler`
- Output: XML report uploaded to Codecov

**View Coverage:**
```bash
uv run pytest tests/ --cov=api --cov=bot --cov=scheduler --cov-report=term-missing
```

## Test Types

**Unit Tests:**
- Scope: Pure functions, service logic, model validation, config
- Examples: `test_aggregator.py`, `test_cache.py`, `test_telegram_auth.py`, `test_reseller_tools.py`, `test_models.py`, `test_config.py`
- Approach: Direct function/class instantiation, no HTTP, no database for pure logic tests
- Database tests (model validation): Use auto-created SQLite tables

**Integration Tests:**
- Scope: Full HTTP request/response cycle via `TestClient` or `httpx.AsyncClient`
- Examples: `test_listings.py`, `test_trackers_api.py`, `test_workflow_api.py`, `test_currency.py`, `test_health.py`, `test_ai_analysis.py`
- Approach: Create app with `create_app()`, override dependencies, use `TestClient` to make HTTP requests
- Pattern: Override `get_telegram_user` -> `fake_telegram_user()`, override `KufarClient` -> `FakeKufarClient`, override cache -> `MemoryCache`

**E2E Tests:**
- Not present. No Playwright, Cypress, or Selenium tests.

**Frontend Tests:**
- `test_frontend_structure.py` — validates HTML structure (viewport, scripts, canvases)
- `test_app_js_syntax.py` — validates JS syntax via `node --check` and regex-based API endpoint presence
- No DOM interaction testing, no component testing

**Bot Tests:**
- `test_bot_start.py` — tests command handlers (`/start`, `/help`, `/app`) with mocked messages
- `test_bot_price.py`, `test_bot_tracker.py` — tests bot callback handlers
- Uses `SimpleNamespace` with `AsyncMock` for message objects

## Common Patterns

**Async Testing:**
```python
# With asyncio_mode = "auto", async tests work without decorators
async def test_something() -> None:
    cache = MemoryCache()
    await cache.set("key", "value")
    result = await cache.get("key")
    assert result == "value"
```

For older tests that explicitly use the decorator:
```python
@pytest.mark.asyncio
async def test_search_returns_ads(...) -> None:
    ...
```

**Error Testing:**
```python
# Testing that an exception is raised
with pytest.raises(KufarAPIError, match="after 3 attempts"):
    await KufarClient(mock_settings).search(query="test")

# Testing HTTP error responses
assert response.status_code == 404
assert "not found" in response.json()["detail"].lower()
```

**Testing with DB Seed:**
```python
# Seed data, then test endpoint
asyncio.run(seed_tracker_event(app.state.session_factory))
response = client.get("/api/v1/tracker-events")
assert response.status_code == 200
```

**Floating Point Comparisons:**
```python
# Use pytest.approx() for float assertions
assert stats.median == pytest.approx(3000.0)
```

## Test Infrastructure Notes

**Database:**
- Local: SQLite with `aiosqlite` (auto-created in tmp_path per test session)
- CI: PostgreSQL 16 service container (`postgresql+asyncpg://test_user:test_pass@localhost:5432/kufar_test`)
- Tables auto-created/dropped per test via `autouse` fixture in `conftest.py`

**Environment Isolation:**
- `monkeypatch.setenv()` sets required env vars for all tests
- `get_settings.cache_clear()` resets cached settings
- `tmp_path` provides isolated temp directory for SQLite files

**Frontend Testing:**
- `node --check` for syntax validation (requires Node.js on PATH)
- `BeautifulSoup` for HTML structure validation (requires `beautifulsoup4`)
- No browser automation

---

*Testing analysis: 2026-04-22*
