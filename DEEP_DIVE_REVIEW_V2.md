# 🔍 Deep Dive Review V2: Kufar Analytics — POST-FIX AUDIT

**Дата аудита:** 2026-05-09  
**Рабочая директория:** /home/staz/Downloads/myProjetctKufar  
**Методология:** Повторный аудит после 2 коммитов фиксов (`d41f5aa`, `3f78f6b`)  
**Предыдущий отчёт:** `DEEP_DIVE_REVIEW.md`

---

## Executive Summary

| Сфера | Исправлено ✅ | Осталось ❌ | Новое ⚠️ | Статус |
|-------|--------------|-------------|----------|--------|
| **Безопасность** | 1 | 10 (2 P0) | 1 P0 | 🔴 Критично |
| **Бэкенд / Логика** | 1 | 4 (1 P0) | 0 | 🟠 Требует внимания |
| **Фронтенд / UX** | 2 | 4 (2 P1) | 2 | 🟠 Требует внимания |
| **База данных** | 0 | 7 (1 P0) | 1 | 🔴 Критично |
| **Производительность** | 0 | 5 (3 P1) | 0 | 🟠 Требует внимания |
| **Инфраструктура** | 0 | 11 (1 P0) | 1 P0 | 🔴 Критично |
| **Качество кода / Тесты** | 2 | 3 (1 P0) | 2 | 🟠 Требует внимания |

**Общий статус:** Из ~45 проблем предыдущего аудита **исправлено только 6**, **частично исправлено 3**, **не исправлено 36**. Дополнительно выявлено **5 новых проблем** (включая 2 P0).

---

## Severity Legend

| Severity | Описание |
|----------|----------|
| **P0** | Критично — немедленное действие требуется |
| **P1** | Высокий — серьёзный риск стабильности, безопасности |
| **P2** | Средний — недостающие production-возможности |
| **P3** | Низкий — рекомендации по улучшению |

---

## ✅ ЧТО БЫЛО ИСПРАВЛЕНО

| # | Проблема | Где | Коммит |
|---|----------|-----|--------|
| 1 | **IDOR защита в workflow.py** | `api/routers/workflow.py` | `3f78f6b` |
| 2 | **Prompt injection в AI Listing Assistant** | `api/services/ai_service.py:1073-1075` | `3f78f6b` |
| 3 | **Rate limiting на `/analytics/leads`** | `api/routers/analytics.py:80` | `3f78f6b` |
| 4 | **XSS во фронтенде** | `render_cards.js`, `render_core.js` — `textContent`, `escapeHtml` | `3f78f6b` |
| 5 | **Google Fonts `font-display: swap`** | `frontend/index.html` | `3f78f6b` |
| 6 | **`test_main.py` проверяет правильные роутеры** | `tests/test_main.py` | `3f78f6b` |

### Частично исправлено (⚠️)

| # | Проблема | Что сделано | Что осталось |
|---|----------|-------------|--------------|
| 1 | **Rate limiting** | Добавлен `@limiter.limit("30/minute")` на `/analytics/leads` | `/leads`, `/watchlist`, `/trackers` — без защиты |
| 2 | **Prompt injection** | `sanitize_user_text()` в `ai_listing_assistant.py` | `ai_tools.py` (`/negotiate`, `/price-advice`) — без защиты |
| 3 | **MemoryCache thread-safety** | `asyncio.Lock` добавлен в `__init__` | Используется **только в `incr()`**, `get()` и `set()` без lock |

---

## 🔴 P0 — КРИТИЧЕСКИЕ ПРОБЛЕМЫ (ОСТАЛИСЬ)

### 1. CSRF bypass при отсутствии заголовка `Origin`
- **Файл:** `api/main.py:154-156`
- **Статус:** ❌ Не исправлено
- **Код:**
```python
if request.method in ("POST", "PATCH", "DELETE"):
    origin = request.headers.get("origin")
    if origin:  # <-- если origin is None, блок пропускается
```
- **Проблема:** Если заголовок `Origin` отсутствует (curl, embedded WebViews, прокси), проверка полностью пропускается.
- **Фикс:**
```python
if request.method in ("POST", "PATCH", "DELETE"):
    origin = request.headers.get("origin")
    if origin is None:
        return JSONResponse(
            status_code=403,
            content={"detail": "CSRF: origin header required"},
        )
```

### 2. Hardcoded production secrets в `.env` (закоммичены в git)
- **Файл:** `.env`
- **Статус:** ❌ Не исправлено + 🆕 Ухудшилось (секреты закоммичены)
- **Что утекает:**
  - `BOT_TOKEN=79...` (реальный Telegram Bot Token)
  - `AI_API_KEY=AIzaSy...` (реальный Google AI API Key)
  - `DATABASE_URL=postgresql://postgres:marketplace_pass_2024@db:5432/kufar`
  - `AI_PROXY_URL=http://wjylzhba:1s8vpc3vlmvf@91.211.87.224:7214`
- **Фикс:**
  1. Срочно сменить все скомпрометированные токены
  2. Перенести `.env` в `.gitignore` (дважды проверить!)
  3. Использовать Docker Secrets или HashiCorp Vault
  4. Создать `.env.example` с placeholder'ами

### 3. Отсутствуют ORM-модели для `saved_searches` и `contacts`
- **Файл:** `api/models.py`
- **Статус:** ❌ Не исправлено
- **Проблема:** Таблицы созданы миграциями (`20260406_0005`, `20260407_0010`), но ORM-моделей нет. Любой код через ORM к `User.saved_searches` или `User.contacts` — `AttributeError`.
- **Фикс:**
```python
class SavedSearch(Base, UserIDMixin, TimestampMixin):
    __tablename__ = "saved_searches"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    query: Mapped[str] = mapped_column(String(255), nullable=False)
    filters: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    user = relationship("User", back_populates="saved_searches")

class Contact(Base, UserIDMixin, TimestampMixin):
    __tablename__ = "contacts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    user = relationship("User", back_populates="contacts")
```

### 4. Отсутствие HTTPS/TLS termination
- **Файл:** `nginx/default.conf`
- **Статус:** ❌ Не исправлено
- **Проблема:** Только `listen 80;`. Нет `443 ssl`, нет сертификатов. Telegram initData передаётся в открытом виде.
- **Фикс:**
```nginx
server {
    listen 443 ssl http2;
    ssl_certificate /etc/nginx/ssl/cert.pem;
    ssl_certificate_key /etc/nginx/ssl/key.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    add_header Strict-Transport-Security "max-age=31536000" always;
}
```

### 5. `asyncio.run()` в синхронном тесте
- **Файл:** `tests/test_trackers_api.py:103`
- **Статус:** ❌ Не исправлено
- **Код:**
```python
def test_tracker_events_endpoint():
    asyncio.run(seed_tracker_event(app.state.session_factory))
```
- **Проблема:** Создаёт новый event loop, конфликтует с pytest-asyncio. Flaky тесты.
- **Фикс:**
```python
@pytest.mark.asyncio
async def test_tracker_events_endpoint(client, db_session):
    await seed_tracker_event(db_session, ...)
    response = client.get("/api/v1/tracker-events")
```

---

## 🟠 P1 — СЕРЬЁЗНЫЕ ПРОБЛЕМЫ (ОСТАЛИСЬ)

### 6. Redis credentials leak в логи
- **Файл:** `api/limiter.py:78-82`
- **Статус:** ❌ Не исправлено
- **Код:**
```python
logger.error("Redis at %s unreachable", storage_uri or "<unset>")
```
- **Фикс:**
```python
from urllib.parse import urlparse

def _sanitize_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.password:
        return url.replace(f":{parsed.password}@", ":***@")
    return url
```

### 7. Debug mode полностью отключает аутентификацию
- **Файл:** `api/dependencies.py:68-73`
- **Статус:** ❌ Не исправлено
- **Код:**
```python
if not x_telegram_init_data:
    if settings.debug:
        user = TelegramInitData(user_id=0, first_name="Debug", raw={})
        return user
```
- **Фикс:** Запретить `debug=True` на уровне entrypoint, если `ENV=production`.

### 8. Отсутствие traceback в global exception handler
- **Файл:** `api/main.py:127-139`
- **Статус:** ❌ Не исправлено
- **Фикс:** Добавить `exc_info=True` в `logger.error()`.

### 9. Отсутствие graceful fallback для Currency API
- **Файлы:** `api/routers/listings.py:121`, `api/routers/price_stats.py:104`
- **Статус:** ❌ Не исправлено
- **Код:**
```python
rates_payload = await currency_service.get_rates()  # падает с 500
```
- **Фикс:**
```python
try:
    rates = await currency_service.get_rates()
except Exception:
    rates = {"BYN": 1.0}
```

### 10. Несоответствие типов PK/FK
- **Файл:** `api/models.py`
- **Статус:** ❌ Не исправлено
- **Проблема:** `users.id = Integer`, `UserIDMixin.user_id = BigInteger`.
- **Фикс:** Унифицировать на `BigInteger` или `Integer`.

### 11. Redis exposed на хостовый порт
- **Файл:** `docker-compose.yml:78`
- **Статус:** ❌ Не исправлено
- **Код:** `6380:6379`
- **Фикс:** Убрать `ports:` или добавить `requirepass`.

### 12. Отсутствуют restart policies
- **Файл:** `docker-compose.yml`
- **Статус:** ❌ Не исправлено
- **Фикс:** `restart: unless-stopped` для всех сервисов.

### 13. Отсутствуют resource limits
- **Файл:** `docker-compose.yml`
- **Статус:** ❌ Не исправлено
- **Фикс:**
```yaml
deploy:
  resources:
    limits:
      memory: 512M
      cpus: '1.0'
```

### 14. Dockerfile — сломан layer caching
- **Файл:** `Dockerfile`
- **Статус:** ❌ Не исправлено
- **Проблема:** `COPY . .` перед `uv sync`. Multi-stage отсутствует.
- **Фикс:**
```dockerfile
COPY pyproject.toml uv.lock ./
RUN uv sync --no-dev
COPY . .
```

### 15. CSS файл 9558 строк
- **Файл:** `frontend/css/style.css`
- **Статус:** ❌ Не исправлено
- **Проблема:** Монолитный CSS без minification/purge.
- **Фикс:** PurgeCSS + cssnano для production build.

### 16. 22 JS файла без bundling
- **Файл:** `frontend/index.html`
- **Статус:** ❌ Не исправлено
- **Проблема:** 22 `<script defer>` тега. Нет Vite/Webpack/Rollup.
- **Фикс:** Внедрить Vite или Rollup для bundling и code splitting.

### 17. Semaphore в `KufarClient`
- **Файл:** `api/services/kufar_client.py`
- **Статус:** ❌ Не исправлено
- **Проблема:** Нет ограничения на одновременные HTTP-запросы к Kufar.
- **Фикс:** `asyncio.Semaphore(settings.kufar_parallel_semaphore)`.

### 18. AI read timeout 180 секунд
- **Файл:** `api/services/ai_service.py:730`
- **Статус:** ❌ Не исправлено
- **Код:** `httpx.Timeout(connect=15, read=180, write=20, pool=10)`
- **Фикс:** `read=45` или background jobs с webhook.

### 19. Нет пагинации для `/leads` и `/watchlist`
- **Файлы:** `api/routers/workflow.py:99`, `api/routers/workflow.py:322`
- **Статус:** ❌ Не исправлено
- **Проблема:** Возвращают **все** записи пользователя.
- **Фикс:** Добавить `limit`/`offset` параметры.

### 20. Глобальный mutable state
- **Файл:** `api/services/query_pipeline.py:170`
- **Статус:** ❌ Не исправлено
- **Код:** `_inflight_dataset_futures: dict[str, asyncio.Future] = {}`
- **Проблема:** При `gunicorn` с несколькими workers singleflight ломается.

---

## 🟡 P2 — УМЕРЕННЫЕ ПРОБЛЕМЫ (ОСТАЛИСЬ)

### 21. X-Forwarded-For trust без whitelist
- **Файл:** `api/routers/consent.py:143-152`
- **Статус:** ❌ Не исправлено
- **Фикс:** Добавить whitelist доверенных прокси.

### 22. Image proxy race condition
- **Файл:** `api/routers/image_proxy.py:83-96`
- **Статус:** ❌ Не исправлено
- **Фикс:** Добавить `asyncio.Lock` для `_transcoded_cache`.

### 23. MemoryCache thread-safety (lock только в incr)
- **Файл:** `api/services/cache.py`
- **Статус:** ⚠️ Частично
- **Проблема:** `asyncio.Lock` есть, но используется **только в `incr()`**. `get()` и `set()` без lock.
- **Фикс:** Оборачивать все операции с `_storage` в `async with self._lock`.

### 24. Partial indexes sync (ORM ↔ миграции)
- **Файл:** `api/models.py` vs `migrations/versions/20260407_0008`
- **Статус:** ❌ Не исправлено
- **Проблема:** ORM — обычный индекс, миграция — `postgresql_where=active=true`.
- **Фикс:**
```python
Index("idx_trackers_user_active", "user_id", "active",
      postgresql_where=text("active = true"))
```

### 25. Отсутствует индекс на `trackers.last_checked_at`
- **Файл:** `api/models.py`
- **Статус:** ❌ Не исправлено
- **Фикс:** `Index("idx_trackers_last_checked", "last_checked_at")`.

### 26. Отсутствует `DESC` в индексе tracker_events
- **Файл:** `api/models.py:276`
- **Статус:** ❌ Не исправлено
- **Фикс:** `Index("idx_tracker_events_tracker_created", "tracker_id", "created_at DESC")`.

### 27. Timezone для `QueryListingState.updated_at`
- **Файл:** `api/models.py:221`
- **Статус:** ❌ Не исправлено
- **Проблема:** `updated_at` без `timezone=True`.
- **Фикс:** Добавить `DateTime(timezone=True)`.

### 28. `.env.example` неполный
- **Файл:** `.env.example`
- **Статус:** ❌ Не исправлено
- **Проблема:** 18 переменных вместо ~30. Отсутствуют `AI_PROXY_URL`, `LOG_LEVEL`, `DB_POOL_SIZE`, `AI_HOURLY_LIMIT` и др.
- **Фикс:** Добавить все переменные из `api/config.py`.

### 29. Nginx rate limiting
- **Файл:** `nginx/default.conf`
- **Статус:** ❌ Не исправлено
- **Фикс:**
```nginx
limit_req_zone $binary_remote_addr zone=api:10m rate=10r/s;
limit_req zone=api burst=20 nodelay;
```

### 30. Nginx `server_tokens off`
- **Файл:** `nginx/default.conf`
- **Статус:** ❌ Не исправлено
- **Фикс:** `server_tokens off;`.

### 31. Graceful shutdown
- **Файл:** `stop-local.sh`
- **Статус:** ❌ Не исправлено
- **Проблема:** `pkill` без `SIGTERM` → `sleep` → `SIGKILL`.
- **Фикс:**
```bash
pkill -f 'python -m api.main' && sleep 5 || true
pkill -9 -f 'python -m api.main' || true
```

### 32. Отсутствие Prometheus/metrics
- **Проблема:** Нет `/metrics` endpoint.
- **Статус:** ❌ Не исправлено
- **Фикс:** Добавить `prometheus_client` + `/metrics` endpoint.

### 33. Bulk INSERT snapshots
- **Файл:** `api/routers/workflow.py:629`
- **Статус:** ❌ Не исправлено
- **Проблема:** `session.add(snapshot)` в цикле.
- **Фикс:** `session.execute(insert(LeadItemPriceSnapshot).values(snapshots))`.

### 34. Virtual scrolling не используется
- **Файл:** `frontend/js/render_cards.js`
- **Статус:** ❌ Не исправлено
- **Проблема:** Файл `virtual_list.js` подключён, но в комментариях "Virtual scrolling disabled".
- **Фикс:** Либо включить, либо удалить мёртвый код.

### 35. Circuit breaker для KufarClient
- **Файл:** `api/services/kufar_client.py`
- **Статус:** ❌ Не исправлено
- **Проблема:** Только retry с backoff, нет circuit breaker.
- **Фикс:** Внедрить `aiobreaker`.

---

## 🆕 НОВЫЕ ПРОБЛЕМЫ (ВЫЯВЛЕНЫ ПРИ ПОВТОРНОМ АУДИТЕ)

### 36. 🆕 `.env` с реальными секретами закоммичен в git
- **Уровень:** 🔴 P0
- **Файл:** `.env`
- **Проблема:** Реальный `BOT_TOKEN`, `AI_API_KEY`, `DATABASE_URL` с паролем, `AI_PROXY_URL` с креденшелами — всё в git history. Даже если удалить из текущего коммита, останется в истории.
- **Фикс:**
  1. Срочно сменить ВСЕ скомпрометированные токены
  2. `git filter-repo --path .env --invert-paths` (или BFG Repo-Cleaner)
  3. Добавить `.env` в `.gitignore` и проверить `.gitignore` на правильность
  4. Использовать `.env.example` с placeholder'ами

### 37. 🆕 Nginx `proxy_pass` на `host.docker.internal`
- **Уровень:** 🟡 P2
- **Файл:** `nginx/default.conf:77`
- **Код:** `proxy_pass http://host.docker.internal:8010/api/;`
- **Проблема:** Работает только в dev. В production должен быть `upstream api { server api:8000; }`.
- **Фикс:**
```nginx
upstream api {
    server api:8000;
}
server {
    location /api/ {
        proxy_pass http://api/api/;
    }
}
```

### 38. 🆕 Мёртвый код virtual scrolling
- **Уровень:** 🟢 P3
- **Файл:** `frontend/js/render_cards.js`
- **Проблема:** `_resetContainer` очищает `container._virtualList`, а `renderListingsCollection` принимает `itemHeight = 180`, но virtual scrolling нигде не используется. Параметр `_virtualList` — мёртвый код.
- **Фикс:** Удалить неиспользуемую логику `_virtualList` или включить virtual scrolling.

### 39. 🆕 22 HTTP-запроса на старт
- **Уровень:** 🟡 P2
- **Файл:** `frontend/index.html`
- **Проблема:** 22 JS-файла + CSS + Fonts = 25+ запросов. Даже с `defer` замедляет First Contentful Paint.
- **Фикс:** Bundling через Vite/Rollup.

### 40. 🆕 Prompt injection в `ai_tools.py` (не исправлено)
- **Уровень:** 🔴 P0
- **Файл:** `api/routers/ai_tools.py:117-125`
- **Код:**
```python
user_content = (
    f"Товар: {payload.query}\n"
    f"Цена продавца: {payload.asking_price_byn} BYN\n"
    f"Моя цена: {payload.my_offer_byn} BYN\n"
)
```
- **Проблема:** Пользовательский ввод (`query`, `condition`, `market_context`) передаётся в LLM напрямую без `sanitize_user_text()`.
- **Фикс:**
```python
from api.services.ai_service import sanitize_user_text
user_content = (
    f"Товар: {sanitize_user_text(payload.query)}\n"
    f"Цена продавца: {payload.asking_price_byn} BYN\n"
    ...
)
```

---

## 📋 ПРИОРИТИЗИРОВАННЫЙ ПЛАН ИСПРАВЛЕНИЙ

### 🔴 Немедленно (P0) — 1-2 дня

| # | Задача | Файлы | Сложность |
|---|--------|-------|-----------|
| 1 | **CSRF middleware — требовать `Origin`** | `api/main.py:154` | Низкая |
| 2 | **Вычистить `.env` из git + сменить токены** | `.env`, `.gitignore` | Высокая |
| 3 | **Добавить `sanitize_user_text()` в `ai_tools.py`** | `api/routers/ai_tools.py` | Низкая |
| 4 | **Добавить ORM-модели `saved_searches`, `contacts`** | `api/models.py` | Низкая |
| 5 | **Добавить TLS termination** | `nginx/default.conf` | Средняя |
| 6 | **Убрать `asyncio.run()` из `test_trackers_api.py`** | `tests/test_trackers_api.py` | Низкая |

### 🟠 В ближайшем релизе (P1) — 1 неделя

| # | Задача | Файлы | Сложность |
|---|--------|-------|-----------|
| 7 | **Добавить `exc_info=True` в global exception handler** | `api/main.py:129` | Низкая |
| 8 | **Санитизировать Redis URL в логах** | `api/limiter.py:78` | Низкая |
| 9 | **Навесить `@limiter.limit` на `/leads`, `/watchlist`, `/trackers`** | `api/routers/*.py` | Низкая |
| 10 | **Добавить graceful fallback для currency service** | `api/routers/*.py` | Низкая |
| 11 | **Выравнять типы PK/FK** | `api/models.py` | Средняя |
| 12 | **Исправить Dockerfile (layer caching)** | `Dockerfile` | Средняя |
| 13 | **Добавить restart policies + resource limits** | `docker-compose.yml` | Низкая |
| 14 | **Убрать Redis port mapping** | `docker-compose.yml` | Низкая |
| 15 | **Уменьшить AI read timeout до 45 сек** | `api/services/ai_service.py:730` | Низкая |
| 16 | **Добавить пагинацию `/leads`, `/watchlist`** | `api/routers/workflow.py` | Средняя |
| 17 | **Добавить семафор в `KufarClient`** | `api/services/kufar_client.py` | Средняя |
| 18 | **Добавить CSP meta-tag** | `frontend/index.html` | Низкая |
| 19 | **Добавить Nginx rate limiting** | `nginx/default.conf` | Низкая |
| 20 | **Добавить `server_tokens off`** | `nginx/default.conf` | Низкая |
| 21 | **Добавить graceful shutdown** | `stop-local.sh` | Низкая |

### 🟡 Техдолг (P2) — 2-4 недели

| # | Задача | Файлы | Сложность |
|---|--------|-------|-----------|
| 22 | **Добавить lock во все операции MemoryCache** | `api/services/cache.py` | Низкая |
| 23 | **Добавить trusted proxies whitelist** | `api/routers/consent.py` | Низкая |
| 24 | **Синхронизировать partial indexes** | `api/models.py`, `migrations/` | Средняя |
| 25 | **Добавить индекс `trackers.last_checked_at`** | `api/models.py` | Низкая |
| 26 | **Добавить `DESC` к индексу tracker_events** | `api/models.py` | Низкая |
| 27 | **Добавить timezone к `QueryListingState.updated_at`** | `api/models.py` | Низкая |
| 28 | **Дополнить `.env.example`** | `.env.example` | Низкая |
| 29 | **Добавить Prometheus `/metrics`** | `api/routers/health.py` | Средняя |
| 30 | **Оптимизировать bulk INSERT snapshots** | `api/routers/workflow.py` | Средняя |
| 31 | **Bundle/minify CSS и JS** | `frontend/` | Высокая |
| 32 | **Добавить circuit breaker для KufarClient** | `api/services/kufar_client.py` | Средняя |
| 33 | **Исправить Nginx `proxy_pass` на `api:8000`** | `nginx/default.conf` | Низкая |
| 34 | **Удалить мёртвый код virtual scrolling** | `frontend/js/render_cards.js` | Низкая |

---

## 📊 Сравнительная сводка

| Метрика | До фиксов | После фиксов | Дельта |
|---------|-----------|--------------|--------|
| **P0 проблем** | 8 | 6 | -2 ✅ |
| **P1 проблем** | 22 | 15 | -7 ✅ |
| **P2 проблем** | 20+ | 14 | -6 ✅ |
| **Исправлено полностью** | — | 6 | — |
| **Частично исправлено** | — | 3 | — |
| **Новые проблемы** | — | 5 | 🆕 |
| **Всего осталось** | ~50 | ~35 | -15 |

### Ключевые выводы:

1. **6 проблем исправлены** — в основном XSS, тесты, IDOR защита, rate limiting на 1 endpoint.
2. **3 проблемы частично исправлены** — rate limiting (только 1 endpoint), prompt injection (только 1 модуль), MemoryCache lock (только 1 метод).
3. **35+ проблем остались** — включая 6 P0 (CSRF, secrets, ORM-модели, HTTPS, asyncio.run в тестах, prompt injection в ai_tools).
4. **5 новых проблем** — `.env` закоммичен в git (P0!), Nginx proxy_pass на host.docker.internal, мёртвый код virtual scrolling, 22 HTTP-запроса на старт.
5. **Инфраструктура практически не тронута** — 0 из 11 проблем исправлено.
6. **База данных не тронута** — 0 из 7 проблем исправлено.

---

## 🎯 Рекомендации

### Что делать прямо сейчас (сегодня):
1. 🔴 **Сменить ВСЕ секреты** (BOT_TOKEN, AI_API_KEY, DB password, proxy credentials)
2. 🔴 **Удалить `.env` из git history** через `git filter-repo`
3. 🔴 **Добавить `.env` в `.gitignore`**
4. 🔴 **Исправить CSRF middleware** — требовать `Origin` для mutable методов

### Что делать на этой неделе:
5. 🟠 Добавить `sanitize_user_text()` в `ai_tools.py`
6. 🟠 Добавить ORM-модели для `saved_searches` и `contacts`
7. 🟠 Добавить TLS termination
8. 🟠 Убрать `asyncio.run()` из тестов
9. 🟠 Добавить rate limiting на `/leads`, `/watchlist`, `/trackers`
10. 🟠 Добавить graceful fallback для currency service

### Что делать в следующем спринте:
11. 🟡 Исправить Dockerfile + docker-compose
12. 🟡 Улучшить observability (Prometheus, structured logging)
13. 🟡 Bundle/minify frontend
14. 🟡 Добавить circuit breaker для KufarClient

---

*Отчёт сгенерирован на основе повторного аудита 5 субагентов после 2 коммитов фиксов.*
