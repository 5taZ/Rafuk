from __future__ import annotations

import itertools
import os
from datetime import UTC, datetime
from pathlib import Path

import fastapi.testclient as _ftc
import pytest
from sqlalchemy import Integer as _Integer

os.environ["ENV"] = "test"
os.environ["DEBUG"] = "true"
os.environ["AUTH_BYPASS"] = "true"
os.environ.setdefault("BOT_TOKEN", "7123456789:AAFtesttoken")
os.environ.setdefault("DATABASE_URL", os.environ.get("TEST_DATABASE_URL", "sqlite+aiosqlite:///:memory:"))
os.environ.setdefault("REDIS_URL", "redis://localhost:6380/15")
os.environ.setdefault("API_BASE_URL", "https://kufar-analytics.example.com")
os.environ.setdefault("MINI_APP_URL", "https://kufar-analytics.example.com/app")


@pytest.fixture(autouse=True)
def configure_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("BOT_TOKEN", "7123456789:AAFtesttoken")

    # Use TEST_DATABASE_URL if set (CI uses PostgreSQL), otherwise SQLite for local dev
    db_url = os.environ.get("TEST_DATABASE_URL")
    if db_url is None:
        db_url = f"sqlite+aiosqlite:///{tmp_path / 'test.db'}"
    monkeypatch.setenv("DATABASE_URL", db_url)

    # Point tests at a separate Redis logical DB so they don't pollute the
    # dev cache when a real Redis is reachable. Falls back gracefully to
    # MemoryCache via ping() if Redis is unavailable.
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6380/15")
    monkeypatch.setenv("API_BASE_URL", "https://kufar-analytics.example.com")
    monkeypatch.setenv("MINI_APP_URL", "https://kufar-analytics.example.com/app")
    monkeypatch.setenv("ENV", "test")
    # Tests must never hit real telegram auth (they use dependency_overrides).
    # Debug mode is on for verbose logging / extra dev origins.
    monkeypatch.setenv("DEBUG", "true")
    # AUTH_BYPASS=true lets tests skip Telegram initData *and* AI-consent
    # checks (see api/services/ai_guards._check_ai_consent). The flag is
    # rejected by the Settings validator in production / against a remote
    # DATABASE_URL, so this is dev/test-only by construction.
    monkeypatch.setenv("AUTH_BYPASS", "true")

    try:
        from api.config import get_settings

        get_settings.cache_clear()
    except Exception:
        pass


# ── CSRF fix: auto-add Origin + X-Requested-With to TestClient ───────────
# The CSRF middleware requires:
#   1. An Origin header on POST/PATCH/DELETE (debug mode allows
#      "http://localhost:8081").
#   2. (FE-H7) X-Requested-With: XMLHttpRequest — the browser-only
#      header we now require as defence in depth. The mini-app sets
#      it automatically in api_core.js; tests have to do the same so
#      they don't trip the 403 guard.
# Wrapping TestClient in one place keeps every test file clean.

_OriginalTestClient = _ftc.TestClient


class _CSRFTestClient(_OriginalTestClient):
    def __init__(self, app, **kwargs):
        headers = dict(kwargs.pop("headers", None) or {})
        headers.setdefault("origin", "http://localhost:8081")
        headers.setdefault("x-requested-with", "XMLHttpRequest")
        super().__init__(app, headers=headers, **kwargs)


_ftc.TestClient = _CSRFTestClient


# ── SQLite BigInteger fix ────────────────────────────────────────────────
# SQLite only auto-increments INTEGER PRIMARY KEY, not BIGINT PRIMARY KEY.
# The User model uses BigInteger for id. Patch the column type so DDL
# generates INTEGER PRIMARY KEY which auto-increments correctly.

try:
    from api.models import User as _User

    _User.__table__.c.id.type = _Integer()
except Exception:
    pass

# DB-H3 (Wave 8): same patch for ai_audit_log — its id is BIGINT
# autoincrement which SQLite refuses to auto-assign. The new
# test_cleanup_ai_audit_log inserts rows directly, so without this
# patch the INSERT trips a NOT NULL on id.
try:
    from api.models import AIAuditLog as _AIAuditLog

    _AIAuditLog.__table__.c.id.type = _Integer()
except Exception:
    pass


# ── SQLite BigInteger fix: User.id auto-assignment ───────────────────────
# Fallback helper for tests that create User objects directly — provides
# an explicit id in case the type patch above isn't sufficient.

_user_id_seq = itertools.count(1)


def make_user(*, telegram_user_id: int, first_name: str, **kwargs):
    from api.models import User

    uid = kwargs.pop("id", None) or next(_user_id_seq)
    return User(id=uid, telegram_user_id=telegram_user_id, first_name=first_name, **kwargs)


@pytest.fixture(autouse=True)
async def _flush_test_redis() -> None:
    """Flush the test Redis DB before each test so tests are isolated.

    Uses redis-py directly (not the async cache) to avoid event-loop
    binding issues. Silent no-op when Redis isn't reachable — tests fall
    back to MemoryCache automatically.
    """
    try:
        import redis

        client = redis.Redis.from_url(
            "redis://localhost:6380/15",
            socket_connect_timeout=0.2,
            socket_timeout=0.2,
        )
        client.flushdb()
        client.close()
    except Exception:
        pass

    try:
        from api.limiter import limiter

        limiter.reset()
    except Exception:
        pass

    yield


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


async def init_test_tables(app) -> None:
    """Create all tables for a test app instance."""
    from api.models import Base

    async with app.state.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


class FakeCurrencyService:
    def __init__(self, *, rates: dict[str, float] | None = None) -> None:
        self._rates = rates or {"USD": 3.2, "EUR": 3.5}

    async def get_rates(self) -> dict[str, object]:
        return {
            "base": "BYN",
            "rates": self._rates,
            "source": "test",
            "fetched_at": datetime.now(UTC).isoformat(),
        }

    def convert_from_byn(self, amount_byn: float, currency: str, rates: dict[str, float]) -> float:
        if currency == "BYN":
            return round(amount_byn, 2)
        return round(amount_byn / rates[currency], 2)


DEFAULT_FAKE_ADS: list[dict[str, object]] = [
    {
        "ad_id": 1,
        "subject": "iPhone 15 256GB",
        "price_byn": 2000,
        "ad_link": "https://www.kufar.by/item/1",
        "list_time": "2026-04-01T10:00:00",
        "region_id": 6,
        "category": "1000",
        "ad_parameters": [
            {"p": "condition", "v": "Новый"},
            {"p": "category", "v": "1000", "vl": "Телефоны"},
        ],
    },
    {
        "ad_id": 2,
        "subject": "iPhone 15 Pro 256GB",
        "price_byn": 2600,
        "ad_link": "https://www.kufar.by/item/2",
        "list_time": "2026-04-01T11:00:00",
        "region_id": 6,
        "category": "1000",
        "ad_parameters": [
            {"p": "condition", "v": "Новый"},
            {"p": "category", "v": "1000", "vl": "Телефоны"},
        ],
    },
    {
        "ad_id": 3,
        "subject": "iPhone 15 mini 128GB",
        "price_byn": 1500,
        "ad_link": "https://www.kufar.by/item/3",
        "list_time": "2026-04-01T09:00:00",
        "region_id": 6,
        "category": "1000",
        "ad_parameters": [
            {"p": "condition", "v": "Б/у"},
            {"p": "category", "v": "1000", "vl": "Телефоны"},
        ],
    },
]


class FakeKufarClient:
    def __init__(self, settings, *, ads: list[dict[str, object]] | None = None) -> None:
        del settings
        self._ads = ads or DEFAULT_FAKE_ADS

    async def search_all_ads(self, **kwargs) -> dict:
        del kwargs
        return {"total": len(self._ads), "ads": self._ads}

    async def aclose(self) -> None:
        return None


@pytest.fixture
def sample_ads() -> list[dict[str, object]]:
    return [
        {
            "ad_id": 1,
            "subject": "iPhone 15",
            "price_byn": 2000,
            "currency": "BYN",
            "ad_link": "https://www.kufar.by/item/1",
            "list_time": "2026-04-01T10:00:00",
            "region_id": 6,
            "company_ad": False,
            "ad_parameters": [
                {"p": "condition", "v": "Новый"},
            ],
        },
        {
            "ad_id": 2,
            "subject": "iPhone 15 Pro",
            "price_byn": 2200,
            "currency": "BYN",
            "ad_link": "https://www.kufar.by/item/2",
            "list_time": "2026-04-01T12:00:00",
            "region_id": 6,
            "company_ad": True,
            "ad_parameters": [
                {"p": "condition", "v": "Б/у"},
            ],
        },
        {
            "ad_id": 3,
            "subject": "iPhone 15 Pro Max",
            "price_byn": 2500,
            "currency": "BYN",
            "ad_link": "https://www.kufar.by/item/3",
            "list_time": "2026-04-01T09:00:00",
            "region_id": 6,
            "company_ad": True,
            "ad_parameters": [
                {"p": "condition", "v": "Новый"},
            ],
        },
        {
            "ad_id": 4,
            "subject": "iPhone 15 Used",
            "price_byn": 1800,
            "currency": "BYN",
            "ad_link": "https://www.kufar.by/item/4",
            "list_time": "2026-03-31T18:00:00",
            "region_id": 6,
            "company_ad": False,
            "ad_parameters": [
                {"p": "condition", "v": "Б/у"},
            ],
        },
        {
            "ad_id": 5,
            "subject": "Broken listing",
            "price_byn": 0,
            "currency": "BYN",
            "ad_link": "https://www.kufar.by/item/5",
            "list_time": "",
            "region_id": 6,
            "ad_parameters": [],
        },
        {
            "ad_id": 6,
            "subject": "Anomaly",
            "price_byn": 150000000,
            "currency": "BYN",
            "ad_link": "https://www.kufar.by/item/6",
            "list_time": "",
            "region_id": 6,
            "ad_parameters": [],
        },
    ]
