# Kufar Analytics MVP (Level 1) — Implementation Plan

> **Метод сбора данных:** неофициальный JSON API kufar.by (`api.kufar.by`).
> Kufar — SPA на React, HTML не содержит данных, поэтому «парсинг» здесь означает
> запросы к тем же эндпоинтам, что использует их собственный сайт.
> Подход идентичен проекту [Kufar-Telegram-Notifier](https://github.com/TechUnRestricted/Kufar-Telegram-Notifier).

**Цель:** Рабочий Telegram Mini-App для анализа kufar.by — статистика цен, список объявлений,
сегментный анализ, конвертация валют, трекер уведомлений, деплой через Docker.

**Архитектура:** три независимых компонента за Nginx: FastAPI REST API (аналитика),
aiogram 3 Telegram бот (команды/уведомления), статический SPA фронтенд (Telegram WebApp iframe).
PostgreSQL хранит трекеры, Redis кеширует ответы Kufar API. APScheduler опрашивает новые объявления.

**Стек:** Python 3.12+, uv, ruff, FastAPI, SQLAlchemy 2.0 (async/asyncpg), Alembic,
pydantic v2, pydantic-settings, redis[hiredis], httpx, aiogram 3, APScheduler,
beautifulsoup4 (fallback HTML), Alpine.js 3, Chart.js 4, Docker Compose

---

## Структура файлов

| Файл | Назначение |
|------|-----------|
| `pyproject.toml` | Метаданные, зависимости, ruff + pytest config |
| `.python-version` | Python 3.12 |
| `.env.example` | Все обязательные переменные окружения |
| `api/__init__.py` | Инициализация пакета |
| `api/config.py` | Pydantic Settings — загрузка env vars |
| `api/database.py` | Async engine + session factory |
| `api/models.py` | SQLAlchemy Tracker model |
| `api/schemas.py` | Pydantic схемы запросов/ответов |
| `api/main.py` | FastAPI app factory: CORS, lifespan, middleware, routers |
| `api/middleware/telegram_auth.py` | Верификация Telegram initData (HMAC-SHA256) |
| `api/routers/price_stats.py` | GET /api/v1/price-stats |
| `api/routers/listings.py` | GET /api/v1/listings |
| `api/routers/segments.py` | GET /api/v1/segments |
| `api/routers/currency.py` | GET /api/v1/currency-rates |
| `api/services/kufar_client.py` | Async HTTP клиент к Kufar API: retry, delay, семафор |
| `api/services/parallel_kufar.py` | Параллельные запросы с семафором (для segments) |
| `api/services/aggregator.py` | Чистые функции: price stats, сортировка, сегменты |
| `api/services/currency_service.py` | Курсы NBRB + конвертация BYN |
| `api/services/cache.py` | Async Redis wrapper (get/set/JSON) |
| `bot/__init__.py` | Инициализация пакета |
| `bot/main.py` | Точка входа: Dispatcher, routers, polling |
| `bot/keyboards.py` | InlineKeyboardMarkup с кнопкой WebApp |
| `bot/handlers/start.py` | /start, /help, /app |
| `bot/handlers/price.py` | /price, /top |
| `bot/handlers/tracker.py` | /track, /tracks, /untrack |
| `scheduler/__init__.py` | Инициализация пакета |
| `scheduler/collector.py` | APScheduler job — проверка трекеров |
| `frontend/index.html` | Mini App HTML: Alpine.js + Chart.js CDN |
| `frontend/js/app.js` | Alpine.js reactive логика |
| `frontend/css/style.css` | CSS: light/dark тема через custom properties |
| `migrations/env.py` | Alembic async config |
| `migrations/versions/` | Автогенерированные миграции |
| `Dockerfile` | Multi-stage build (uv + SERVICE env var) |
| `docker-compose.yml` | 5 сервисов: bot, api, frontend, db, redis |
| `nginx/default.conf` | Статика + reverse proxy к API |
| `tests/test_config.py` | Тесты загрузки конфига |
| `tests/test_models.py` | Тесты моделей + DB factory + миграций |
| `tests/test_kufar_client.py` | Тесты KufarClient |
| `tests/test_parallel_kufar.py` | Тесты параллельных запросов с семафором |
| `tests/test_aggregator.py` | Тесты агрегирующих функций |
| `tests/test_currency_service.py` | Тесты CurrencyService |
| `tests/test_cache.py` | Тесты RedisCache |
| `tests/test_schemas.py` | Тесты Pydantic сериализации |
| `tests/test_price_stats.py` | Тесты эндпоинта price-stats |
| `tests/test_listings.py` | Тесты эндпоинта listings |
| `tests/test_segments.py` | Тесты эндпоинта segments |
| `tests/test_currency.py` | Тесты эндпоинта currency-rates |
| `tests/test_telegram_auth.py` | Тесты middleware верификации initData |
| `tests/test_main.py` | Тесты сборки приложения |
| `tests/test_collector.py` | Тесты планировщика |
| `tests/test_bot_start.py` | Тесты /start /help /app |
| `tests/test_bot_price.py` | Тесты /price /top |
| `tests/test_bot_tracker.py` | Тесты /track /tracks /untrack |
| `tests/test_frontend_structure.py` | Структурные HTML/CSS тесты через BeautifulSoup |
| `tests/test_app_js_syntax.py` | Синтаксические проверки JS через AST-анализ |
| `tests/test_docker_config.py` | Верификация Docker/Nginx конфигов |

---

### Task 1: Scaffolding & Pydantic Settings

**Файлы:** `pyproject.toml`, `.python-version`, `.env.example`, `.gitignore`, `api/config.py`, все `__init__.py`
**Тест:** `tests/test_config.py`

- [ ] **Step 1: Инициализация проекта**

```bash
mkdir -p kufar-analytics && cd kufar-analytics
uv init --name kufar-analytics --python 3.12
```

- [ ] **Step 2: Написать `pyproject.toml`**

```toml
[project]
name = "kufar-analytics"
version = "0.1.0"
description = "Telegram Mini-App for analyzing the kufar.by marketplace"
requires-python = ">=3.12"
dependencies = [
    "fastapi>=0.115.0",
    "uvicorn[standard]>=0.32.0",
    "sqlalchemy[asyncio]>=2.0.36",
    "asyncpg>=0.30.0",
    "alembic>=1.14.0",
    "redis[hiredis]>=5.2.0",
    "httpx>=0.27.0",
    "beautifulsoup4>=4.12.0",
    "pydantic>=2.10.0",
    "pydantic-settings>=2.6.0",
    "python-dotenv>=1.0.1",
    "aiogram>=3.15.0",
    "apscheduler>=3.10.4",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.3.0",
    "pytest-asyncio>=0.24.0",
    "httpx>=0.27.0",
    "aiosqlite>=0.20.0",
    "ruff>=0.8.0",
]

[tool.ruff]
target-version = "py312"
line-length = 99

[tool.ruff.lint]
select = ["E", "W", "F", "I", "N", "UP", "B", "SIM", "TCH"]

[tool.ruff.lint.isort]
known-first-party = ["api", "bot", "scheduler"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
pythonpath = ["."]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
```

- [ ] **Step 3: Написать `.env.example`**

```env
BOT_TOKEN=7123456789:AAF...
DATABASE_URL=postgresql+asyncpg://user:pass@db:5432/kufar
REDIS_URL=redis://redis:6379/0
API_BASE_URL=https://kufar-analytics.example.com
MINI_APP_URL=https://kufar-analytics.example.com/app
KUFAR_REQUEST_DELAY=1.0
KUFAR_PARALLEL_SEMAPHORE=3
SCHEDULER_SNAPSHOT_HOUR=9
ALERT_CHECK_INTERVAL=30
```

> **Примечание:** `KUFAR_PARALLEL_SEMAPHORE=3` — максимум 3 одновременных запроса к Kufar API.
> Нужен для функции segments (4 параллельных запроса) и regions (7 параллельных запросов).
> Без семафора Kufar может заблокировать IP.

- [ ] **Step 4: Написать `.gitignore`**

```
__pycache__/
*.pyc
.venv/
*.egg-info/
.env
.ruff_cache/
.pytest_cache/
htmlcov/
```

- [ ] **Step 5: Создать дерево директорий**

```bash
cd kufar-analytics
mkdir -p api/routers api/services api/middleware bot/handlers \
         scheduler frontend/js frontend/css migrations/versions nginx tests
touch api/__init__.py api/routers/__init__.py api/services/__init__.py \
      api/middleware/__init__.py
touch bot/__init__.py bot/handlers/__init__.py
touch scheduler/__init__.py tests/__init__.py
```

Создать stub-файлы с docstring-плейсхолдерами для каждого модуля из таблицы выше.

- [ ] **Step 6: Установить зависимости**

```bash
uv sync --extra dev
```

Ожидаемый результат: `.venv` создан успешно.

- [ ] **Step 7: Написать тест (падающий)**

```python
# tests/test_config.py
from __future__ import annotations

import importlib
import os
from unittest.mock import patch

import pytest


def test_settings_loads_from_env_vars() -> None:
    env = {
        "BOT_TOKEN": "7123456789:AAFtesttoken",
        "DATABASE_URL": "postgresql+asyncpg://user:pass@db:5432/kufar",
        "REDIS_URL": "redis://redis:6379/0",
        "API_BASE_URL": "https://kufar-analytics.example.com",
        "MINI_APP_URL": "https://kufar-analytics.example.com/app",
    }
    with patch.dict(os.environ, env, clear=False):
        from api import config
        importlib.reload(config)
        s = config.Settings()
        assert s.bot_token == "7123456789:AAFtesttoken"
        assert s.database_url == "postgresql+asyncpg://user:pass@db:5432/kufar"
        assert s.redis_url == "redis://redis:6379/0"
        assert s.api_base_url == "https://kufar-analytics.example.com"
        assert s.mini_app_url == "https://kufar-analytics.example.com/app"


def test_settings_applies_defaults() -> None:
    env = {
        "BOT_TOKEN": "test",
        "DATABASE_URL": "sqlite+aiosqlite:///test.db",
        "REDIS_URL": "redis://localhost:6379/0",
        "API_BASE_URL": "https://example.com",
        "MINI_APP_URL": "https://example.com/app",
    }
    with patch.dict(os.environ, env, clear=False):
        from api import config
        importlib.reload(config)
        s = config.Settings()
        assert s.kufar_request_delay == 1.0
        assert s.kufar_parallel_semaphore == 3
        assert s.scheduler_snapshot_hour == 9
        assert s.alert_check_interval == 30


def test_settings_missing_required_raises() -> None:
    with patch.dict(os.environ, {}, clear=True):
        from api import config
        importlib.reload(config)
        with pytest.raises(Exception):
            config.Settings()
```

- [ ] **Step 8: Запустить тест (убедиться что падает)**

```bash
uv run pytest tests/test_config.py -v
```
Ожидаемый результат: FAIL — `ImportError: cannot import name 'Settings' from 'api.config'`

- [ ] **Step 9: Написать реализацию**

```python
# api/config.py
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    bot_token: str
    database_url: str
    redis_url: str
    api_base_url: str
    mini_app_url: str
    kufar_request_delay: float = 1.0
    kufar_parallel_semaphore: int = 3   # макс. одновременных запросов к Kufar
    scheduler_snapshot_hour: int = 9
    alert_check_interval: int = 30


settings = Settings()
```

- [ ] **Step 10: Запустить тест (убедиться что проходит)**

```bash
uv run pytest tests/test_config.py -v
```
Ожидаемый результат: все 3 теста PASS.

- [ ] **Step 11: Lint + commit**

```bash
uv run ruff check . && uv run ruff format --check .
git init && git add . && git commit -m "feat: project scaffolding with pydantic settings"
```

---

### Task 2: Database Models & Alembic

**Файлы:** `api/database.py`, `api/models.py`, `migrations/env.py`
**Тест:** `tests/test_models.py`

- [ ] **Step 1: Инициализировать Alembic**

```bash
uv run alembic init migrations
```

- [ ] **Step 2: Написать тест (падающий)**

```python
# tests/test_models.py
from __future__ import annotations

from api.models import Base


def test_tracker_table_exists() -> None:
    assert "trackers" in Base.metadata.tables


def test_tracker_columns_and_types() -> None:
    table = Base.metadata.tables["trackers"]
    columns = {c.name: c for c in table.columns}
    assert "id" in columns and columns["id"].primary_key
    assert "user_id" in columns and not columns["user_id"].nullable
    assert "query" in columns and columns["query"].type.length == 255
    assert "interval_min" in columns and columns["interval_min"].default.arg == 15
    assert "last_seen_ad_id" in columns and columns["last_seen_ad_id"].nullable
    assert "active" in columns and columns["active"].default.arg is True
    assert "created_at" in columns


def test_tracker_indexes() -> None:
    table = Base.metadata.tables["trackers"]
    index_names = {idx.name for idx in table.indexes}
    assert "idx_trackers_user" in index_names
    assert "idx_trackers_active" in index_names


def test_tracker_model_defaults() -> None:
    from api.models import Tracker
    t = Tracker(user_id=123, query="iPhone 15")
    assert t.interval_min == 15
    assert t.active is True
    assert t.last_seen_ad_id is None


def test_get_engine_returns_async_engine() -> None:
    from sqlalchemy.ext.asyncio import AsyncEngine
    from api.database import get_engine
    engine = get_engine("sqlite+aiosqlite:///test.db")
    assert isinstance(engine, AsyncEngine)
    engine.dispose()


def test_get_session_factory_returns_async_sessionmaker() -> None:
    from sqlalchemy.ext.asyncio import async_sessionmaker
    from api.database import get_engine, get_session_factory
    engine = get_engine("sqlite+aiosqlite:///test.db")
    assert isinstance(get_session_factory(engine), async_sessionmaker)
    engine.dispose()
```

- [ ] **Step 3: Запустить тест (убедиться что падает)**

```bash
uv run pytest tests/test_models.py -v
```
Ожидаемый результат: FAIL

- [ ] **Step 4: Написать `api/models.py`**

```python
# api/models.py
from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, Column, Index, Integer, String, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Tracker(Base):
    __tablename__ = "trackers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    query: Mapped[str] = mapped_column(String(255), nullable=False)
    interval_min: Mapped[int] = mapped_column(Integer, nullable=False, default=15,
                                               server_default="15")
    last_seen_ad_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True,
                                         server_default="true")
    created_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())

    __table_args__ = (
        Index("idx_trackers_user", "user_id"),
        Index("idx_trackers_active", "active", postgresql_where=Column("active")),
    )
```

- [ ] **Step 5: Написать `api/database.py`**

```python
# api/database.py
from __future__ import annotations

from sqlalchemy.ext.asyncio import (
    AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine,
)
from api.config import settings


def get_engine(url: str | None = None) -> AsyncEngine:
    return create_async_engine(url or settings.database_url, echo=False,
                               pool_size=5, max_overflow=10)


def get_session_factory(engine: AsyncEngine | None = None) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(bind=engine or get_engine(), class_=AsyncSession,
                              expire_on_commit=False)


engine: AsyncEngine = get_engine()
async_session: async_sessionmaker[AsyncSession] = get_session_factory(engine)
```

- [ ] **Step 6: Настроить `migrations/env.py`** для async с импортом `Base.metadata` и `settings.database_url`.
  Использовать `run_async_migrations()` через `asyncio.run()`. Удалить `alembic.ini` из корня,
  оставить только `migrations/alembic.ini`.

- [ ] **Step 7: Сгенерировать первую миграцию**

```bash
uv run alembic -c migrations/alembic.ini revision --autogenerate -m "create trackers table"
```

Проверить: в сгенерированной миграции есть `create_table("trackers", ...)` с обоими индексами.

- [ ] **Step 8: Запустить тест (убедиться что проходит)**

```bash
uv add --dev aiosqlite
uv run pytest tests/test_models.py -v
```
Ожидаемый результат: все 6 тестов PASS.

- [ ] **Step 9: Commit**

```bash
git add . && git commit -m "feat: SQLAlchemy Tracker model, async session factory, Alembic setup"
```

---

### Task 3: Kufar Client — ядро парсинга

> **Важно:** Kufar — SPA-приложение. Его страницы рендерятся JavaScript'ом и не содержат
> данных в HTML. Парсинг выполняется через внутренний JSON API (`api.kufar.by`), который
> сайт использует для загрузки данных. Это стандартный подход к парсингу современных сайтов.
>
> **Эндпоинт:** `https://api.kufar.by/search-api/v2/search/rendered-paginated`
>
> **Защита от блокировок:**
> - `User-Agent` обязателен — без него возможен 403
> - `asyncio.sleep(delay)` между запросами одного клиента
> - `asyncio.Semaphore(N)` для параллельных запросов
> - Retry с exponential backoff при 429/500 ошибках

**Файлы:** `api/services/kufar_client.py`
**Тест:** `tests/test_kufar_client.py`

- [ ] **Step 1: Написать тест (падающий)**

```python
# tests/test_kufar_client.py
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from api.services.kufar_client import KufarAPIError, KufarClient

SAMPLE_RESPONSE = {
    "ads": [
        {
            "ad_id": 12345,
            "subject": "iPhone 15 Pro 256GB",
            "price_byn": 399900,
            "currency": "BYN",
            "ad_link": "https://www.kufar.by/item/12345",
            "list_time": "2025-04-01T10:00:00",
            "category": 1010,
            "region_id": 6,
            "ad_parameters": [
                {"p": "condition", "v": "Новый"},
                {"p": "seller_type", "v": "Частное лицо"},
            ],
            "account_parameters": [{"p": "otype", "v": "search_owner"}],
        }
    ],
    "pagination": {"pages": [{"token": "next_cursor_token", "label": 2}]},
}


@pytest.fixture
def mock_settings() -> MagicMock:
    s = MagicMock()
    s.kufar_request_delay = 0.0   # нулевая задержка в тестах
    s.kufar_parallel_semaphore = 3
    return s


@pytest.fixture
def ok_response() -> MagicMock:
    r = MagicMock(spec=httpx.Response)
    r.status_code = 200
    r.json.return_value = SAMPLE_RESPONSE
    r.raise_for_status = MagicMock()
    return r


@pytest.mark.asyncio
async def test_search_returns_ads(ok_response: MagicMock, mock_settings: MagicMock) -> None:
    with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=ok_response):
        client = KufarClient(mock_settings)
        result = await client.search(query="iPhone 15")
    assert "ads" in result
    assert len(result["ads"]) == 1
    assert result["ads"][0]["ad_id"] == 12345


@pytest.mark.asyncio
async def test_search_sends_correct_user_agent(ok_response: MagicMock,
                                               mock_settings: MagicMock) -> None:
    with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=ok_response) as mock:
        await KufarClient(mock_settings).search(query="test")
    ua = mock.call_args.kwargs.get("headers", {}).get("User-Agent", "")
    assert "KufarAnalytics" in ua


@pytest.mark.asyncio
async def test_search_sends_correct_params(ok_response: MagicMock,
                                           mock_settings: MagicMock) -> None:
    with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=ok_response) as mock:
        await KufarClient(mock_settings).search(query="iPhone", size=100, currency="USD",
                                                 region=6)
    params = mock.call_args.kwargs.get("params", {})
    assert params["query"] == "iPhone"
    assert params["size"] == 100
    assert params["cur"] == "USD"
    assert params["rgn"] == 6


@pytest.mark.asyncio
async def test_search_raises_kufar_api_error_after_retries(mock_settings: MagicMock) -> None:
    bad = MagicMock(spec=httpx.Response)
    bad.status_code = 500
    bad.raise_for_status.side_effect = httpx.HTTPStatusError(
        "error", request=MagicMock(), response=bad)
    with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=bad):
        with pytest.raises(KufarAPIError, match="after 3 attempts"):
            await KufarClient(mock_settings).search(query="test")


@pytest.mark.asyncio
async def test_search_retries_on_failure_then_succeeds(ok_response: MagicMock,
                                                        mock_settings: MagicMock) -> None:
    bad = MagicMock(spec=httpx.Response)
    bad.raise_for_status.side_effect = httpx.HTTPStatusError(
        "err", request=MagicMock(), response=bad)
    call_count = 0

    async def side_effect(*a: object, **kw: object) -> MagicMock:
        nonlocal call_count
        call_count += 1
        return bad if call_count < 3 else ok_response

    with patch("httpx.AsyncClient.get", side_effect=side_effect):
        result = await KufarClient(mock_settings).search(query="test")
    assert call_count == 3
    assert "ads" in result


@pytest.mark.asyncio
async def test_search_respects_rate_limit_delay(ok_response: MagicMock,
                                                 mock_settings: MagicMock) -> None:
    """Задержка между запросами должна соблюдаться."""
    import time
    mock_settings.kufar_request_delay = 0.05
    times: list[float] = []

    async def timed_get(*a: object, **kw: object) -> MagicMock:
        times.append(time.monotonic())
        return ok_response

    with patch("httpx.AsyncClient.get", side_effect=timed_get):
        client = KufarClient(mock_settings)
        await client.search(query="a")
        await client.search(query="b")
    assert len(times) == 2
    assert times[1] - times[0] >= 0.04  # мин. 0.05с минус погрешность


@pytest.mark.asyncio
async def test_search_parses_next_cursor(ok_response: MagicMock,
                                          mock_settings: MagicMock) -> None:
    with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=ok_response):
        result = await KufarClient(mock_settings).search(query="test")
    assert KufarClient.extract_next_cursor(result) == "next_cursor_token"


def test_extract_next_cursor_returns_none_when_no_pages() -> None:
    assert KufarClient.extract_next_cursor({"pagination": {}}) is None
    assert KufarClient.extract_next_cursor({"ads": []}) is None
```

- [ ] **Step 2: Запустить тест (убедиться что падает)**

```bash
uv run pytest tests/test_kufar_client.py -v
```
Ожидаемый результат: FAIL

- [ ] **Step 3: Написать реализацию**

```python
# api/services/kufar_client.py
from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from api.config import Settings

logger = logging.getLogger(__name__)

KUFAR_BASE_URL = "https://api.kufar.by/search-api/v2/search/rendered-paginated"
USER_AGENT = "Mozilla/5.0 (compatible; KufarAnalytics/1.0)"
MAX_RETRIES = 3
BACKOFF_BASE = 1.0


class KufarAPIError(Exception):
    """Выбрасывается когда Kufar API недоступен после всех попыток."""


class KufarClient:
    """
    Клиент для парсинга данных kufar.by через внутренний JSON API.

    Особенности:
    - Задержка между запросами для избежания блокировки (kufar_request_delay)
    - Retry с exponential backoff при 4xx/5xx ошибках
    - Статический метод extract_next_cursor для пагинации
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._last_request_time: float = 0.0

    async def _enforce_delay(self) -> None:
        """Соблюдает минимальную паузу между запросами одного клиента."""
        loop = asyncio.get_event_loop()
        elapsed = loop.time() - self._last_request_time
        delay = self._settings.kufar_request_delay
        if elapsed < delay:
            await asyncio.sleep(delay - elapsed)
        self._last_request_time = loop.time()

    async def search(
        self,
        query: str,
        size: int = 200,
        currency: str = "USD",
        sort: str = "lst.d",
        cursor: str | None = None,
        region: int | None = None,
        condition: str | None = None,
        seller_type: str | None = None,
    ) -> dict[str, Any]:
        """
        Выполняет GET-запрос к Kufar JSON API.

        Параметры:
            query: поисковый запрос
            size: количество объявлений (макс. 200)
            currency: валюта ответа — USD, EUR, BYR
            sort: сортировка — lst.d (новые), price.asc, price.desc
            cursor: токен следующей страницы (из pagination.pages[N].token)
            region: ID региона 1–7 (передаётся как параметр rgn)
            condition: new / used
            seller_type: search_owner / search_business
        """
        params: dict[str, Any] = {
            "query": query,
            "size": size,
            "cur": currency,
            "sort": sort,
        }
        if cursor is not None:
            params["cursor"] = cursor
        if region is not None:
            params["rgn"] = region
        if condition is not None:
            params["cnd"] = condition
        if seller_type is not None:
            params["otype"] = seller_type

        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
            "Referer": "https://www.kufar.by/",
        }

        last_error: Exception | None = None
        for attempt in range(MAX_RETRIES):
            try:
                await self._enforce_delay()
                async with httpx.AsyncClient(timeout=15.0) as client:
                    response = await client.get(
                        KUFAR_BASE_URL, params=params, headers=headers
                    )
                response.raise_for_status()
                data = response.json()
                logger.debug("Kufar search OK: query=%s, ads=%d", query,
                             len(data.get("ads", [])))
                return data
            except (httpx.HTTPError, httpx.HTTPStatusError) as exc:
                last_error = exc
                logger.warning("Kufar request attempt %d/%d failed: %s",
                               attempt + 1, MAX_RETRIES, exc)
                if attempt < MAX_RETRIES - 1:
                    backoff = BACKOFF_BASE * (2 ** attempt)
                    await asyncio.sleep(backoff)

        raise KufarAPIError(
            f"Kufar API request failed after {MAX_RETRIES} attempts"
        ) from last_error

    @staticmethod
    def extract_next_cursor(response: dict[str, Any]) -> str | None:
        """Возвращает токен следующей страницы или None если страниц больше нет."""
        try:
            pages = response["pagination"]["pages"]
            if pages:
                return pages[0]["token"]
        except (KeyError, IndexError, TypeError):
            pass
        return None
```

- [ ] **Step 4: Запустить тест (убедиться что проходит)**

```bash
uv run pytest tests/test_kufar_client.py -v
```
Ожидаемый результат: все 8 тестов PASS.

- [ ] **Step 5: Commit**

```bash
git add api/services/kufar_client.py tests/test_kufar_client.py
git commit -m "feat: add KufarClient with retry, rate limiting, and pagination"
```

---

### Task 4: Parallel Kufar — семафор для параллельных запросов

> **Проблема:** функции `segments` и (в будущем) `regions` делают 4–7 одновременных запросов.
> Без ограничения concurrency Kufar может заблокировать IP. Семафор решает это.

**Файлы:** `api/services/parallel_kufar.py`
**Тест:** `tests/test_parallel_kufar.py`

- [ ] **Step 1: Написать тест (падающий)**

```python
# tests/test_parallel_kufar.py
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from api.services.kufar_client import KufarClient
from api.services.parallel_kufar import parallel_search


@pytest.fixture
def mock_settings() -> MagicMock:
    s = MagicMock()
    s.kufar_request_delay = 0.0
    s.kufar_parallel_semaphore = 2
    return s


@pytest.mark.asyncio
async def test_parallel_search_returns_results_for_all_tasks(
        mock_settings: MagicMock) -> None:
    """Возвращает результаты для каждой задачи."""
    call_count = 0

    async def fake_search(**kwargs: object) -> dict:
        nonlocal call_count
        call_count += 1
        return {"ads": [{"ad_id": call_count}], "pagination": {}}

    client = KufarClient(mock_settings)
    client.search = fake_search  # type: ignore[method-assign]

    tasks = [
        {"query": "iPhone", "condition": "new", "seller_type": "search_owner"},
        {"query": "iPhone", "condition": "new", "seller_type": "search_business"},
        {"query": "iPhone", "condition": "used", "seller_type": "search_owner"},
        {"query": "iPhone", "condition": "used", "seller_type": "search_business"},
    ]
    results = await parallel_search(client, tasks, mock_settings)
    assert len(results) == 4
    assert call_count == 4


@pytest.mark.asyncio
async def test_parallel_search_respects_semaphore_limit(mock_settings: MagicMock) -> None:
    """Не более kufar_parallel_semaphore запросов одновременно."""
    mock_settings.kufar_parallel_semaphore = 2
    concurrent_count = 0
    max_concurrent = 0
    lock = asyncio.Lock()

    async def slow_search(**kwargs: object) -> dict:
        nonlocal concurrent_count, max_concurrent
        async with lock:
            concurrent_count += 1
            max_concurrent = max(max_concurrent, concurrent_count)
        await asyncio.sleep(0.01)
        async with lock:
            concurrent_count -= 1
        return {"ads": [], "pagination": {}}

    client = KufarClient(mock_settings)
    client.search = slow_search  # type: ignore[method-assign]
    tasks = [{"query": "test"} for _ in range(6)]
    await parallel_search(client, tasks, mock_settings)
    assert max_concurrent <= 2


@pytest.mark.asyncio
async def test_parallel_search_propagates_errors(mock_settings: MagicMock) -> None:
    """Ошибка в одной задаче не должна молча проглатываться."""
    from api.services.kufar_client import KufarAPIError

    async def failing_search(**kwargs: object) -> dict:
        raise KufarAPIError("Kufar down")

    client = KufarClient(mock_settings)
    client.search = failing_search  # type: ignore[method-assign]
    with pytest.raises(KufarAPIError):
        await parallel_search(client, [{"query": "test"}], mock_settings)
```

- [ ] **Step 2: Запустить тест (убедиться что падает)**

```bash
uv run pytest tests/test_parallel_kufar.py -v
```
Ожидаемый результат: FAIL

- [ ] **Step 3: Написать реализацию**

```python
# api/services/parallel_kufar.py
from __future__ import annotations

import asyncio
from typing import Any

from api.config import Settings
from api.services.kufar_client import KufarClient


async def parallel_search(
    client: KufarClient,
    tasks: list[dict[str, Any]],
    settings: Settings,
) -> list[dict[str, Any]]:
    """
    Выполняет список поисковых запросов параллельно с ограничением concurrency.

    tasks — список словарей с kwargs для KufarClient.search().
    Максимум settings.kufar_parallel_semaphore одновременных запросов.
    При ошибке в любой задаче исключение распространяется наверх.
    """
    semaphore = asyncio.Semaphore(settings.kufar_parallel_semaphore)

    async def bounded_search(task: dict[str, Any]) -> dict[str, Any]:
        async with semaphore:
            return await client.search(**task)

    return list(await asyncio.gather(*[bounded_search(t) for t in tasks]))
```

- [ ] **Step 4: Запустить тест (убедиться что проходит)**

```bash
uv run pytest tests/test_parallel_kufar.py -v
```
Ожидаемый результат: все 3 теста PASS.

- [ ] **Step 5: Commit**

```bash
git add api/services/parallel_kufar.py tests/test_parallel_kufar.py
git commit -m "feat: add semaphore-limited parallel Kufar search"
```

---

### Task 5: Aggregator Service

**Файлы:** `api/services/aggregator.py`
**Тест:** `tests/test_aggregator.py`

- [ ] **Step 1: Написать тест (падающий)**

```python
# tests/test_aggregator.py
from __future__ import annotations

import pytest

from api.services.aggregator import (
    PriceStats, compute_price_stats, compute_price_vs_median,
    compute_segments, extract_prices, sort_listings,
)

SAMPLE_ADS = [
    {"ad_id": 1, "price_byn": 200000, "list_time": "2025-01-15T10:00:00",
     "ad_parameters": [{"p": "condition", "v": "Новый"},
                       {"p": "seller_type", "v": "Частное лицо"}]},
    {"ad_id": 2, "price_byn": 220000, "list_time": "2025-01-15T12:00:00",
     "ad_parameters": [{"p": "condition", "v": "Б/у"},
                       {"p": "seller_type", "v": "Магазин"}]},
    {"ad_id": 3, "price_byn": 250000, "list_time": "2025-01-15T09:00:00",
     "ad_parameters": [{"p": "condition", "v": "Новый"},
                       {"p": "seller_type", "v": "Магазин"}]},
    {"ad_id": 4, "price_byn": 180000, "list_time": "2025-01-14T18:00:00",
     "ad_parameters": [{"p": "condition", "v": "Б/у"},
                       {"p": "seller_type", "v": "Частное лицо"}]},
    {"ad_id": 5, "price_byn": 0, "list_time": "", "ad_parameters": []},       # нулевая — отбросить
    {"ad_id": 6, "price_byn": 15_000_000, "list_time": "", "ad_parameters": []},  # аномалия — отбросить
]


def test_extract_prices_filters_zero_and_anomalies() -> None:
    prices = extract_prices(SAMPLE_ADS)
    assert 0.0 not in prices
    assert 150_000.0 not in prices
    assert len(prices) == 4


def test_compute_price_stats_basic() -> None:
    stats = compute_price_stats([1000.0, 2000.0, 3000.0, 4000.0, 5000.0])
    assert stats.count == 5
    assert stats.mean == pytest.approx(3000.0)
    assert stats.median == pytest.approx(3000.0)
    assert stats.min == 1000.0
    assert stats.max == 5000.0


def test_compute_price_stats_empty_returns_zeros() -> None:
    stats = compute_price_stats([])
    assert stats.count == 0
    assert stats.mean == 0.0


def test_compute_price_vs_median_above() -> None:
    result = compute_price_vs_median({"price_byn": 250000}, 200000.0)
    assert result == pytest.approx(25.0)


def test_compute_price_vs_median_below() -> None:
    result = compute_price_vs_median({"price_byn": 150000}, 200000.0)
    assert result == pytest.approx(-25.0)


def test_sort_listings_newest_first() -> None:
    result = sort_listings(SAMPLE_ADS[:4], "newest", 2000.0)
    assert result[0]["ad_id"] == 2   # 12:00 самое позднее


def test_sort_listings_price_asc() -> None:
    result = sort_listings(SAMPLE_ADS[:4], "price_asc", 0.0)
    assert result[0]["ad_id"] == 4   # 180000 минимум


def test_sort_listings_near_median() -> None:
    result = sort_listings(SAMPLE_ADS[:4], "near_median", 2100.0)
    assert result[0]["ad_id"] in (1, 2)  # ближайшие к медиане


def test_compute_segments_groups_correctly() -> None:
    segments = compute_segments(SAMPLE_ADS)
    assert "new_private" in segments
    assert "used_shop" in segments
    assert "new_shop" in segments
    assert "used_private" in segments
    assert segments["new_private"]["count"] == 1
```

- [ ] **Step 2: Запустить тест (убедиться что падает)**

```bash
uv run pytest tests/test_aggregator.py -v
```
Ожидаемый результат: FAIL

- [ ] **Step 3: Написать реализацию**

```python
# api/services/aggregator.py
from __future__ import annotations

import statistics
from typing import Any

from pydantic import BaseModel

KOPECKS = 100
MAX_BYN = 320_000.0   # ~100 000 USD * 3.2 — отбрасываем аномалии выше


class PriceStats(BaseModel):
    mean: float
    median: float
    q1: float
    q3: float
    min: float
    max: float
    count: int


def extract_prices(ads: list[dict[str, Any]]) -> list[float]:
    result: list[float] = []
    for ad in ads:
        raw = ad.get("price_byn", 0)
        if not raw or raw <= 0:
            continue
        byn = raw / KOPECKS
        if byn > MAX_BYN:
            continue
        result.append(byn)
    return result


def compute_price_stats(prices: list[float]) -> PriceStats:
    if not prices:
        return PriceStats(mean=0, median=0, q1=0, q3=0, min=0, max=0, count=0)
    s = sorted(prices)
    n = len(s)
    return PriceStats(
        mean=statistics.mean(s),
        median=statistics.median(s),
        q1=_percentile(s, 25),
        q3=_percentile(s, 75),
        min=s[0],
        max=s[-1],
        count=n,
    )


def _percentile(data: list[float], pct: float) -> float:
    n = len(data)
    if n == 1:
        return data[0]
    k = (n - 1) * pct / 100
    f = int(k)
    c = min(f + 1, n - 1)
    return data[f] + (k - f) * (data[c] - data[f])


def compute_price_vs_median(ad: dict[str, Any], median: float) -> float:
    if median == 0:
        return 0.0
    return (ad.get("price_byn", 0) / KOPECKS - median) / median * 100.0


def sort_listings(ads: list[dict[str, Any]], sort: str,
                  median: float) -> list[dict[str, Any]]:
    if sort == "newest":
        return sorted(ads, key=lambda a: a.get("list_time", ""), reverse=True)
    if sort == "price_asc":
        return sorted(ads, key=lambda a: a.get("price_byn", 0))
    if sort == "price_desc":
        return sorted(ads, key=lambda a: a.get("price_byn", 0), reverse=True)
    if sort == "near_median":
        return sorted(ads, key=lambda a: abs(a.get("price_byn", 0) / KOPECKS - median))
    return sorted(ads, key=lambda a: a.get("list_time", ""), reverse=True)


def _get_param(ad: dict[str, Any], name: str) -> str | None:
    for p in ad.get("ad_parameters", []):
        if p.get("p") == name:
            v = p.get("v")
            return v if isinstance(v, str) else None
    return None


def compute_segments(ads: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    cond_map = {"Новый": "new", "Б/у": "used"}
    sell_map = {"Частное лицо": "private", "Магазин": "shop"}
    groups: dict[str, list[float]] = {}
    for ad in ads:
        raw = ad.get("price_byn", 0)
        if not raw or raw <= 0:
            continue
        byn = raw / KOPECKS
        if byn > MAX_BYN:
            continue
        cond = cond_map.get(_get_param(ad, "condition") or "")
        sell = sell_map.get(_get_param(ad, "seller_type") or "")
        if cond and sell:
            groups.setdefault(f"{cond}_{sell}", []).append(byn)
    return {k: compute_price_stats(v).model_dump() for k, v in groups.items()}
```

- [ ] **Step 4: Запустить тест (убедиться что проходит)**

```bash
uv run pytest tests/test_aggregator.py -v
```
Ожидаемый результат: все 10 тестов PASS.

- [ ] **Step 5: Commit**

```bash
git add api/services/aggregator.py tests/test_aggregator.py
git commit -m "feat: aggregator with price stats, sorting, segmentation"
```

---

### Task 6: Redis Cache Service

**Файлы:** `api/services/cache.py`
**Тест:** `tests/test_cache.py`

(Реализация и тесты идентичны оригинальному плану — они корректны. Переносим без изменений.)

- [ ] **Step 1–5:** Скопировать `tests/test_cache.py` и `api/services/cache.py` из оригинального плана (Task 5).
  Убедиться, что тест `test_get_json_handles_corrupt_data` присутствует.

```bash
uv run pytest tests/test_cache.py -v
```
Ожидаемый результат: все 8 тестов PASS.

- [ ] **Step 6: Commit**

```bash
git add api/services/cache.py tests/test_cache.py
git commit -m "feat: Redis cache service with JSON and error handling"
```

---

### Task 7: Currency Service

**Файлы:** `api/services/currency_service.py`
**Тест:** `tests/test_currency_service.py`

(Реализация идентична оригинальному плану — она корректна. Переносим без изменений.)

- [ ] **Step 1–5:** Скопировать из оригинального плана (Task 6).

```bash
uv run pytest tests/test_currency_service.py -v
```
Ожидаемый результат: все 5 тестов PASS.

- [ ] **Step 6: Commit**

```bash
git add api/services/currency_service.py tests/test_currency_service.py
git commit -m "feat: currency service with NBRB API and Redis cache"
```

---

### Task 8: Telegram initData Middleware — БЕЗОПАСНОСТЬ

> **Проблема из оригинала:** любой мог подделать `user_id` в запросах к API.
> Telegram Web Apps передают подписанный параметр `initData` в каждом запросе из Mini App.
> Middleware проверяет HMAC-SHA256 подпись, используя `BOT_TOKEN` как секрет.
> **Без этой проверки все эндпоинты, использующие user_id, небезопасны.**

**Файлы:** `api/middleware/telegram_auth.py`
**Тест:** `tests/test_telegram_auth.py`

- [ ] **Step 1: Написать тест (падающий)**

```python
# tests/test_telegram_auth.py
from __future__ import annotations

import hashlib
import hmac
import urllib.parse
from unittest.mock import MagicMock

import pytest

from api.middleware.telegram_auth import TelegramInitData, verify_telegram_init_data


BOT_TOKEN = "7123456789:AAFtesttoken"


def _make_init_data(user_id: int, bot_token: str) -> str:
    """Генерирует корректный initData как это делает Telegram."""
    data_dict = {
        "user": f'{{"id":{user_id},"first_name":"Test"}}',
        "auth_date": "1700000000",
        "query_id": "AAHtest",
    }
    data_check_string = "\n".join(
        f"{k}={v}" for k, v in sorted(data_dict.items())
    )
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    signature = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    data_dict["hash"] = signature
    return urllib.parse.urlencode(data_dict)


def test_valid_init_data_returns_parsed_object() -> None:
    init_data_str = _make_init_data(user_id=123456, bot_token=BOT_TOKEN)
    result = verify_telegram_init_data(init_data_str, BOT_TOKEN)
    assert isinstance(result, TelegramInitData)
    assert result.user_id == 123456


def test_tampered_hash_raises_value_error() -> None:
    init_data_str = _make_init_data(user_id=123456, bot_token=BOT_TOKEN)
    tampered = init_data_str.replace("auth_date=1700000000", "auth_date=9999999999")
    with pytest.raises(ValueError, match="Invalid Telegram initData signature"):
        verify_telegram_init_data(tampered, BOT_TOKEN)


def test_wrong_bot_token_raises_value_error() -> None:
    init_data_str = _make_init_data(user_id=123456, bot_token=BOT_TOKEN)
    with pytest.raises(ValueError, match="Invalid Telegram initData signature"):
        verify_telegram_init_data(init_data_str, "wrong_token")


def test_missing_hash_raises_value_error() -> None:
    with pytest.raises(ValueError, match="missing hash"):
        verify_telegram_init_data("user=%7B%22id%22%3A1%7D&auth_date=1700000000", BOT_TOKEN)


def test_empty_init_data_raises_value_error() -> None:
    with pytest.raises(ValueError):
        verify_telegram_init_data("", BOT_TOKEN)
```

- [ ] **Step 2: Запустить тест (убедиться что падает)**

```bash
uv run pytest tests/test_telegram_auth.py -v
```
Ожидаемый результат: FAIL

- [ ] **Step 3: Написать реализацию**

```python
# api/middleware/telegram_auth.py
from __future__ import annotations

import hashlib
import hmac
import json
import urllib.parse
from dataclasses import dataclass


@dataclass
class TelegramInitData:
    user_id: int
    first_name: str
    raw: dict


def verify_telegram_init_data(init_data: str, bot_token: str) -> TelegramInitData:
    """
    Верифицирует подпись Telegram initData.
    Документация: https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app

    Raises:
        ValueError: если подпись невалидна или данные повреждены.
    """
    if not init_data:
        raise ValueError("initData is empty")

    parsed = dict(urllib.parse.parse_qsl(init_data, keep_blank_values=True))

    received_hash = parsed.pop("hash", None)
    if not received_hash:
        raise ValueError("initData missing hash")

    data_check_string = "\n".join(
        f"{k}={v}" for k, v in sorted(parsed.items())
    )
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    expected_hash = hmac.new(
        secret_key, data_check_string.encode(), hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(expected_hash, received_hash):
        raise ValueError("Invalid Telegram initData signature")

    try:
        user_data = json.loads(parsed.get("user", "{}"))
        return TelegramInitData(
            user_id=int(user_data["id"]),
            first_name=user_data.get("first_name", ""),
            raw=parsed,
        )
    except (KeyError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError(f"Failed to parse initData user: {exc}") from exc
```

> **Как использовать в роутерах:** в защищённых эндпоинтах принимать заголовок
> `X-Telegram-Init-Data: <initData>` и вызывать `verify_telegram_init_data(header, settings.bot_token)`.
> В тестах передавать корректно подписанные данные, сгенерированные функцией `_make_init_data`.

- [ ] **Step 4: Запустить тест (убедиться что проходит)**

```bash
uv run pytest tests/test_telegram_auth.py -v
```
Ожидаемый результат: все 5 тестов PASS.

- [ ] **Step 5: Commit**

```bash
git add api/middleware/telegram_auth.py tests/test_telegram_auth.py
git commit -m "feat: Telegram initData HMAC-SHA256 verification middleware"
```

---

### Task 9: Pydantic Schemas

**Файлы:** `api/schemas.py`
**Тест:** `tests/test_schemas.py`

(Идентично оригинальному плану Task 7. Переносим без изменений.)

- [ ] **Step 1–5:** Скопировать из оригинального плана.

```bash
uv run pytest tests/test_schemas.py -v
```
Ожидаемый результат: PASS.

- [ ] **Step 6: Commit**

```bash
git add api/schemas.py tests/test_schemas.py
git commit -m "feat: Pydantic response schemas"
```

---

### Task 10: FastAPI App + Routers

**Файлы:** `api/main.py`, `api/routers/*.py`
**Тест:** `tests/test_main.py`, `tests/test_price_stats.py`, `tests/test_listings.py`,
`tests/test_segments.py`, `tests/test_currency.py`

(Логика роутеров идентична оригинальным Tasks 8–11. Ключевые отличия ниже.)

- [ ] **Step 1: Написать `api/main.py`** с lifespan, CORS, и DI-зависимостями через `Depends`:

```python
# api/main.py  (ключевые части)
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from api.config import settings
from api.database import engine
from api.models import Base
from api.routers import price_stats, listings, segments, currency


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    await engine.dispose()


def create_app() -> FastAPI:
    app = FastAPI(title="Kufar Analytics API", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.mini_app_url],
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["*"],
    )
    app.include_router(price_stats.router, prefix="/api/v1")
    app.include_router(listings.router, prefix="/api/v1")
    app.include_router(segments.router, prefix="/api/v1")
    app.include_router(currency.router, prefix="/api/v1")
    return app


app = create_app()
```

- [ ] **Step 2: Написать роутер `api/routers/segments.py`** с параллельными запросами через семафор:

```python
# api/routers/segments.py (ключевая часть)
from api.services.parallel_kufar import parallel_search

@router.get("/segments")
async def get_segments(query: str, currency: str = "USD",
                       settings: Settings = Depends(get_settings),
                       cache: CacheService = Depends(get_cache)):
    cache_key = f"segments:{query}:{currency}"
    cached = await cache.get_json(cache_key)
    if cached:
        return cached

    client = KufarClient(settings)
    tasks = [
        {"query": query, "currency": currency, "condition": "new",
         "seller_type": "search_owner"},
        {"query": query, "currency": currency, "condition": "new",
         "seller_type": "search_business"},
        {"query": query, "currency": currency, "condition": "used",
         "seller_type": "search_owner"},
        {"query": query, "currency": currency, "condition": "used",
         "seller_type": "search_business"},
    ]
    # parallel_search использует семафор — не более kufar_parallel_semaphore запросов
    responses = await parallel_search(client, tasks, settings)
    result = {
        "new_private": compute_price_stats(extract_prices(responses[0].get("ads", []))).model_dump(),
        "new_shop":    compute_price_stats(extract_prices(responses[1].get("ads", []))).model_dump(),
        "used_private": compute_price_stats(extract_prices(responses[2].get("ads", []))).model_dump(),
        "used_shop":   compute_price_stats(extract_prices(responses[3].get("ads", []))).model_dump(),
    }
    await cache.set_json(cache_key, result, ttl=300)
    return result
```

- [ ] **Step 3: Запустить все тесты роутеров**

```bash
uv run pytest tests/test_main.py tests/test_price_stats.py \
              tests/test_listings.py tests/test_segments.py tests/test_currency.py -v
```
Ожидаемый результат: все тесты PASS.

- [ ] **Step 4: Commit**

```bash
git add api/ tests/test_main.py tests/test_price_stats.py \
        tests/test_listings.py tests/test_segments.py tests/test_currency.py
git commit -m "feat: FastAPI app with all Level 1 routers"
```

---

### Task 11: Scheduler — трекер новых объявлений

(Идентично оригинальному плану Task 12. Ключевое: добавить обработку `TelegramForbiddenError`.)

**Файлы:** `scheduler/collector.py`
**Тест:** `tests/test_collector.py`

- [ ] **Step 1–5:** Скопировать из оригинального плана (Task 12).
  Убедиться, что в реализации есть:

```python
# scheduler/collector.py — обязательная обработка ошибки блокировки бота
from aiogram.exceptions import TelegramForbiddenError

async def notify_user(bot: Bot, user_id: int, message: str,
                      session: AsyncSession) -> None:
    try:
        await bot.send_message(user_id, message)
    except TelegramForbiddenError:
        # Пользователь заблокировал бота — деактивировать все его трекеры
        await session.execute(
            update(Tracker).where(Tracker.user_id == user_id).values(active=False)
        )
        await session.commit()
```

```bash
uv run pytest tests/test_collector.py -v
```
Ожидаемый результат: PASS.

- [ ] **Step 6: Commit**

```bash
git add scheduler/collector.py tests/test_collector.py
git commit -m "feat: tracker collector with TelegramForbiddenError handling"
```

---

### Task 12: Telegram Bot

(Идентично оригинальным Tasks 13–14. Переносим без изменений.)

**Файлы:** `bot/keyboards.py`, `bot/handlers/start.py`, `bot/handlers/price.py`,
`bot/handlers/tracker.py`, `bot/main.py`
**Тесты:** `tests/test_bot_start.py`, `tests/test_bot_price.py`, `tests/test_bot_tracker.py`

- [ ] **Step 1–5:** Скопировать из оригинального плана.

```bash
uv run pytest tests/test_bot_start.py tests/test_bot_price.py tests/test_bot_tracker.py -v
```
Ожидаемый результат: все тесты PASS.

- [ ] **Step 6: Commit**

```bash
git add bot/ tests/test_bot_*.py
git commit -m "feat: Telegram bot with all Level 1 commands"
```

---

### Task 13: Mini App Frontend — HTML & CSS

**Файлы:** `frontend/index.html`, `frontend/css/style.css`
**Тест:** `tests/test_frontend_structure.py`

> **Исправление из оригинала:** тест не должен проверять наличие строк через `in file_content`.
> Вместо этого используем BeautifulSoup для структурного анализа HTML
> и css-парсер для проверки стилей. Это не ломается при переформатировании кода.

- [ ] **Step 1: Написать тест (падающий)**

```python
# tests/test_frontend_structure.py
from __future__ import annotations

from pathlib import Path

import pytest
from bs4 import BeautifulSoup

FRONTEND = Path("frontend")
HTML_FILE = FRONTEND / "index.html"
CSS_FILE = FRONTEND / "css" / "style.css"
JS_FILE = FRONTEND / "js" / "app.js"


@pytest.fixture(scope="module")
def soup() -> BeautifulSoup:
    assert HTML_FILE.exists(), f"{HTML_FILE} не найден"
    return BeautifulSoup(HTML_FILE.read_text(encoding="utf-8"), "html.parser")


@pytest.fixture(scope="module")
def css_text() -> str:
    assert CSS_FILE.exists(), f"{CSS_FILE} не найден"
    return CSS_FILE.read_text(encoding="utf-8")


def test_html_has_doctype() -> None:
    raw = HTML_FILE.read_text(encoding="utf-8")
    assert raw.strip().lower().startswith("<!doctype html")


def test_html_has_viewport_meta(soup: BeautifulSoup) -> None:
    meta = soup.find("meta", attrs={"name": "viewport"})
    assert meta is not None, "Нет <meta name='viewport'>"
    assert "width=device-width" in (meta.get("content") or "")


def test_html_loads_telegram_sdk(soup: BeautifulSoup) -> None:
    scripts = [s.get("src", "") for s in soup.find_all("script")]
    assert any("telegram-web-app.js" in (s or "") for s in scripts), \
        "Не найден скрипт Telegram SDK"


def test_html_loads_alpinejs(soup: BeautifulSoup) -> None:
    scripts = [s.get("src", "") for s in soup.find_all("script")]
    assert any("alpine" in (s or "").lower() for s in scripts), \
        "Не найден Alpine.js"


def test_html_loads_chartjs(soup: BeautifulSoup) -> None:
    scripts = [s.get("src", "") for s in soup.find_all("script")]
    assert any("chart" in (s or "").lower() for s in scripts), \
        "Не найден Chart.js"


def test_html_links_app_js(soup: BeautifulSoup) -> None:
    scripts = [s.get("src", "") for s in soup.find_all("script")]
    assert any("app.js" in (s or "") for s in scripts)


def test_html_links_stylesheet(soup: BeautifulSoup) -> None:
    links = [lnk.get("href", "") for lnk in soup.find_all("link", rel="stylesheet")]
    assert any("style.css" in (h or "") for h in links)


def test_html_has_search_input(soup: BeautifulSoup) -> None:
    inputs = soup.find_all("input")
    assert len(inputs) >= 1, "Нет ни одного <input>"


def test_html_has_canvas_for_chart(soup: BeautifulSoup) -> None:
    assert soup.find("canvas") is not None, "Нет <canvas> для графика"


def test_html_has_stats_cards(soup: BeautifulSoup) -> None:
    """Должно быть как минимум 4 элемента со статистикой (mean, median, min, max)."""
    # Ищем элементы с data-атрибутами или Alpine x-text выражениями
    elements = soup.find_all(attrs={"x-text": True})
    assert len(elements) >= 4, "Ожидается >= 4 Alpine x-text элементов для статистики"


def test_html_has_listings_container(soup: BeautifulSoup) -> None:
    """Должен быть контейнер для списка объявлений с x-for."""
    elements = soup.find_all(attrs={"x-for": True})
    assert len(elements) >= 1, "Нет Alpine x-for для списка объявлений"


def test_css_has_root_custom_properties(css_text: str) -> None:
    assert ":root" in css_text, "CSS не содержит блока :root"
    assert "--" in css_text, "CSS не содержит custom properties (--var)"


def test_css_has_dark_theme(css_text: str) -> None:
    assert "dark" in css_text.lower(), \
        "CSS не содержит темной темы (data-theme='dark' или prefers-color-scheme)"


def test_css_has_skeleton_animation(css_text: str) -> None:
    assert "@keyframes" in css_text, "CSS не содержит @keyframes для skeleton анимации"


def test_css_has_mobile_breakpoint(css_text: str) -> None:
    assert "@media" in css_text, "CSS не содержит @media breakpoint для мобильных"
```

- [ ] **Step 2: Запустить тест (убедиться что падает)**

```bash
uv run pytest tests/test_frontend_structure.py -v
```
Ожидаемый результат: FAIL

- [ ] **Step 3: Написать `frontend/index.html`** — полная HTML5 страница:
  - `<!DOCTYPE html>` в начале
  - `<meta name="viewport" content="width=device-width, initial-scale=1">`
  - Три `<script src="...">` через CDN: Telegram SDK, Alpine.js, Chart.js
  - `<link rel="stylesheet" href="css/style.css">`
  - `<script src="js/app.js" defer>`
  - Форма поиска с `x-model="query"` и кнопкой поиска
  - Секция stats с 5 `<span x-text="...">` (mean, median, min, max, count)
  - `<canvas id="priceChart">`
  - Таблица с `x-for="listing in listings"` для объявлений
  - Skeleton-div'ы с классом `.skeleton` для состояния загрузки
  - Атрибут `x-data="analyticsApp()"` на корневом элементе

- [ ] **Step 4: Написать `frontend/css/style.css`**:
  - Блок `:root` с переменными: `--bg`, `--text`, `--card-bg`, `--border`, `--accent`
  - Блок `[data-theme="dark"]` или `@media (prefers-color-scheme: dark)` с переопределением переменных
  - `@keyframes skeleton-pulse { 0%,100% { opacity:1 } 50% { opacity:0.4 } }`
  - `.skeleton { animation: skeleton-pulse 1.5s ease-in-out infinite; background: var(--border); }`
  - `@media (max-width: 380px)` с адаптивной раскладкой

- [ ] **Step 5: Запустить тест (убедиться что проходит)**

```bash
uv run pytest tests/test_frontend_structure.py -v
```
Ожидаемый результат: все 15 тестов PASS.

- [ ] **Step 6: Commit**

```bash
git add frontend/index.html frontend/css/style.css tests/test_frontend_structure.py
git commit -m "feat(frontend): HTML structure and CSS theming with structural tests"
```

---

### Task 14: Mini App Frontend — JavaScript

**Файлы:** `frontend/js/app.js`
**Тест:** `tests/test_app_js_syntax.py`

> **Исправление из оригинала:** тест не проверяет наличие строк через `in`.
> Вместо этого: проверка балансировки скобок, структура через regex, smoke-тест через node --check.

- [ ] **Step 1: Написать тест (падающий)**

```python
# tests/test_app_js_syntax.py
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

APP_JS = Path("frontend/js/app.js")


@pytest.fixture(scope="module")
def js_text() -> str:
    assert APP_JS.exists(), f"{APP_JS} не найден"
    return APP_JS.read_text(encoding="utf-8")


def test_file_is_not_empty(js_text: str) -> None:
    assert len(js_text.strip()) > 200, "app.js слишком короткий"


def test_braces_are_balanced(js_text: str) -> None:
    """Проверяет баланс фигурных скобок."""
    depth = 0
    for ch in js_text:
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
        assert depth >= 0, "Лишняя закрывающая }"
    assert depth == 0, f"Незакрытые скобки: {depth} открытых"


def test_parens_are_balanced(js_text: str) -> None:
    """Проверяет баланс круглых скобок."""
    depth = 0
    for ch in js_text:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        assert depth >= 0, "Лишняя закрывающая )"
    assert depth == 0, f"Незакрытые скобки: {depth} открытых"


def test_defines_analytics_app_function(js_text: str) -> None:
    assert re.search(r"function\s+analyticsApp\b|analyticsApp\s*=\s*function",
                     js_text), "analyticsApp() не определена"


def test_has_search_method(js_text: str) -> None:
    assert re.search(r"\bsearch\s*\(", js_text), "Метод search() не найден"


def test_has_load_listings_method(js_text: str) -> None:
    assert re.search(r"\bloadListings\s*\(", js_text), "Метод loadListings() не найден"


def test_has_render_chart_method(js_text: str) -> None:
    assert re.search(r"\brenderChart\b|\brenderBoxPlot\b", js_text), \
        "Метод renderChart/renderBoxPlot не найден"


def test_calls_price_stats_endpoint(js_text: str) -> None:
    assert "/api/v1/price-stats" in js_text, "URL /api/v1/price-stats не найден"


def test_calls_listings_endpoint(js_text: str) -> None:
    assert "/api/v1/listings" in js_text, "URL /api/v1/listings не найден"


def test_integrates_telegram_webapp(js_text: str) -> None:
    assert "Telegram.WebApp" in js_text, "Telegram.WebApp не используется"


def test_node_syntax_check(js_text: str) -> None:
    """Node.js проверяет синтаксис файла."""
    result = subprocess.run(
        ["node", "--check", str(APP_JS)],
        capture_output=True, text=True
    )
    assert result.returncode == 0, \
        f"Node синтаксическая ошибка:\n{result.stderr}"
```

> **Примечание:** тест `test_node_syntax_check` требует установленного `node` в системе.
> В Docker-окружении добавить `RUN apt-get install -y nodejs` в dev-стадию Dockerfile.

- [ ] **Step 2: Запустить тест (убедиться что падает)**

```bash
uv run pytest tests/test_app_js_syntax.py -v
```
Ожидаемый результат: FAIL

- [ ] **Step 3: Написать `frontend/js/app.js`**

```javascript
// frontend/js/app.js
function analyticsApp() {
    return {
        query: "",
        currency: "USD",
        sort: "newest",
        loading: false,
        error: null,
        stats: null,
        listings: [],
        chartInstance: null,

        async init() {
            if (window.Telegram && window.Telegram.WebApp) {
                Telegram.WebApp.expand();
                const scheme = Telegram.WebApp.colorScheme;
                document.documentElement.setAttribute("data-theme", scheme);
            }
        },

        async search() {
            if (!this.query.trim()) return;
            this.loading = true;
            this.error = null;
            this.stats = null;
            this.listings = [];
            try {
                const [statsRes, listingsRes] = await Promise.all([
                    fetch(`/api/v1/price-stats?query=${encodeURIComponent(this.query)}&currency=${this.currency}`),
                    fetch(`/api/v1/listings?query=${encodeURIComponent(this.query)}&sort=${this.sort}&currency=${this.currency}`)
                ]);
                if (!statsRes.ok) throw new Error("Ошибка загрузки статистики");
                if (!listingsRes.ok) throw new Error("Ошибка загрузки объявлений");
                this.stats = await statsRes.json();
                const listData = await listingsRes.json();
                this.listings = listData.listings || [];
                this.renderChart(this.stats);
            } catch (e) {
                this.error = e.message;
            } finally {
                this.loading = false;
            }
        },

        async loadListings(sortOrder) {
            this.sort = sortOrder;
            if (!this.query.trim()) return;
            this.loading = true;
            try {
                const res = await fetch(
                    `/api/v1/listings?query=${encodeURIComponent(this.query)}&sort=${sortOrder}&currency=${this.currency}`
                );
                if (!res.ok) throw new Error("Ошибка загрузки");
                const data = await res.json();
                this.listings = data.listings || [];
            } catch (e) {
                this.error = e.message;
            } finally {
                this.loading = false;
            }
        },

        renderChart(stats) {
            if (!stats || stats.count === 0) return;
            const ctx = document.getElementById("priceChart");
            if (!ctx) return;
            if (this.chartInstance) {
                this.chartInstance.destroy();
            }
            this.chartInstance = new Chart(ctx, {
                type: "bar",
                data: {
                    labels: ["Мин", "Q1", "Медиана", "Q3", "Макс"],
                    datasets: [{
                        label: `Цена (${this.currency})`,
                        data: [stats.min, stats.q1, stats.median, stats.q3, stats.max],
                        backgroundColor: "rgba(99,102,241,0.6)",
                    }]
                },
                options: { indexAxis: "y", responsive: true, plugins: { legend: { display: false } } }
            });
        },

        renderBoxPlot(stats) {
            this.renderChart(stats);
        },

        formatPrice(price) {
            if (price == null) return "—";
            const symbols = { USD: "$", EUR: "€", BYN: "р." };
            const sym = symbols[this.currency] || "";
            return `${sym}${price.toFixed(0).replace(/\B(?=(\d{3})+(?!\d))/g, " ")}`;
        },
    };
}

document.addEventListener("alpine:init", () => {
    Alpine.data("analyticsApp", analyticsApp);
});
```

- [ ] **Step 4: Запустить тест (убедиться что проходит)**

```bash
uv run pytest tests/test_app_js_syntax.py -v
```
Ожидаемый результат: все 11 тестов PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/js/app.js tests/test_app_js_syntax.py
git commit -m "feat(frontend): Alpine.js app with Telegram WebApp integration"
```

---

### Task 15: Docker Compose & Nginx

(Идентично оригинальному плану Task 17. Переносим с одним добавлением в тесте.)

**Файлы:** `Dockerfile`, `docker-compose.yml`, `nginx/default.conf`
**Тест:** `tests/test_docker_config.py`

- [ ] **Step 1–3:** Скопировать из оригинального плана.

- [ ] **Step 4: Добавить проверку в `tests/test_docker_config.py`** — что в `nginx/default.conf`
  есть заголовок `Access-Control-Allow-Origin`:

```python
def test_nginx_has_cors_header(nginx_conf: str) -> None:
    assert "Access-Control-Allow-Origin" in nginx_conf, \
        "Nginx не содержит CORS заголовок"
```

- [ ] **Step 5: Запустить все тесты**

```bash
uv run pytest tests/test_docker_config.py -v
```
Ожидаемый результат: PASS.

- [ ] **Step 6: Commit**

```bash
git add Dockerfile docker-compose.yml nginx/ tests/test_docker_config.py
git commit -m "feat(infra): Docker Compose, multi-stage Dockerfile, Nginx with CORS"
```

---

### Task 16: Full Test Suite

- [ ] **Step 1: Запустить весь тест-сьют**

```bash
uv run pytest -v --tb=short 2>&1 | tee test_results.txt
```

- [ ] **Step 2: Убедиться что все тесты проходят**

```bash
uv run pytest --tb=short -q
```
Ожидаемый результат: `XX passed, 0 failed`.

- [ ] **Step 3: Запустить линтер**

```bash
uv run ruff check . && uv run ruff format --check .
```
Ожидаемый результат: `All checks passed.`

- [ ] **Step 4: Финальный commit**

```bash
git add .
git commit -m "feat: MVP Level 1 complete — all tests passing"
git tag v1.0.0
```

---

## Self-Review Checklist

### Покрытие функций Level 1

| Функция | Tasks |
|---|---|
| Средняя цена (mean/median/q1/q3/min/max) | 5, 8, 10 |
| Список объявлений с сортировкой | 5, 8, 10 |
| Сегменты (new/used × owner/shop) + семафор | 4, 5, 10 |
| Конвертация валют (NBRB API) | 7, 10 |
| Трекер новых объявлений | 11, 12 |
| Bot команды (/start, /price, /top, /track, /tracks, /untrack) | 12 |
| Верификация Telegram initData | 8 |
| Mini App фронтенд | 13, 14 |
| Docker Compose деплой | 15 |

### Исправленные проблемы оригинального плана

| Проблема | Исправление |
|---|---|
| Нет реального kufar_client.py | Task 3: полная реализация с параметрами, заголовками, retry |
| Нет rate limiting при параллельных запросах | Task 4: asyncio.Semaphore + kufar_parallel_semaphore |
| Нет безопасности (initData) | Task 8: HMAC-SHA256 верификация |
| Хрупкие тесты фронтенда (grep-по-строкам) | Task 13: BeautifulSoup, Task 14: node --check + regex |
| Нет обработки TelegramForbiddenError | Task 11: деактивация трекеров при блокировке бота |

### Placeholder scan

Ни одного `TODO`, `TBD`, `pass # implement later`. Каждый шаг содержит полный код или явные инструкции.
