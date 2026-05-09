# Промпт для Agent Swarm: Kufar Analytics — Internal Fixes

## Контекст

Ты — оркестратор Agent Swarm. В рабочей директории `/home/staz/Downloads/myProjetctKufar` лежит Python/FastAPI проект (Kufar Analytics) с фронтендом на vanilla JS.

Проведён аудит безопасности и качества кода. Результаты в файле `DEEP_DIVE_REVIEW_V2.md`.
Из ~45 проблем исправлено только 6. Осталось 35+ проблем, включая 6 критических (P0).

**Архитектура деплоя:** Проект запускается в Docker и проксируется через **Cloudflare tunnel** (не nginx reverse proxy). Cloudflare обрабатывает HTTPS/TLS termination. Все запросы приходят от Cloudflare IP ranges. Telegram Mini App открывается по URL через Cloudflare tunnel.

**Важно:** НЕ нужно настраивать production-деплой (HTTPS, nginx hardening, resource limits, restart policies, Prometheus, CSS/JS bundling). Нужно исправить внутренние баги, дыры, мёртвый код и логику с учётом Cloudflare tunnel.

## Цель

Исправить ВСЕ внутренние проблемы из `DEEP_DIVE_REVIEW_V2.md` за один запуск через параллельных специализированных агентов.
После фиксов должен быть создан единый git commit с сообщением `fix(audit): resolve internal P0-P2 issues`.

## Структура Swarm (4 агента)

### Агент 1: @security-critical

**Фокус:** Все P0 проблемы безопасности, CSRF, CORS, IP-определение через Cloudflare, Telegram Mini App.

**Задачи:**
1. `api/main.py:154-156` — CSRF middleware: если `Origin` заголовок отсутствует для POST/PATCH/DELETE, возвращать 403 (сейчас пропускается).
2. `api/routers/ai_tools.py:117-125` — добавить `sanitize_user_text()` из `api/services/ai_service.py` для всех пользовательских строк, передаваемых в LLM (`payload.query` и др.).
3. `api/models.py` — добавить ORM-модели `SavedSearch` и `Contact` (таблицы уже созданы миграциями, но ORM моделей нет). Добавить `back_populates` в `User`.
4. `tests/test_trackers_api.py:103` — заменить `asyncio.run()` на `@pytest.mark.asyncio async def`.
5. `.env` — проверить, что файл добавлен в `.gitignore` (если нет — добавить). Создать `.env.example` с placeholder'ами (взять все переменные из `api/config.py`).
6. `api/dependencies.py:68-73` — добавить проверку: если `settings.debug=True` И `ENV=production`, выбрасывать ошибку при старте (или хотя бы warning в логи с level=error). Это защита от случайного отключения аутентификации.
7. `api/routers/consent.py:143-152` — IP определение через Cloudflare:
   - Проверять `CF-Connecting-IP` заголовок (от Cloudflare) в первую очередь.
   - Валидировать `X-Forwarded-For` только если запрос от Cloudflare IP ranges.
   - Игнорировать `X-Forwarded-For` если запрос НЕ от Cloudflare.
   - Cloudflare IP ranges:
     ```
     173.245.48.0/20, 103.21.244.0/22, 103.22.200.0/22, 103.31.4.0/22,
     141.101.64.0/18, 108.162.192.0/18, 190.93.240.0/20, 188.114.96.0/20,
     197.234.240.0/22, 198.41.128.0/17, 162.158.0.0/15, 104.16.0.0/13,
     104.24.0.0/14, 172.64.0.0/13, 131.0.72.0/22
     ```
8. `api/main.py:109` — CORS origins:
   - Проверить, что `mini_app_url` и `api_base_url` настроены на реальный Cloudflare tunnel URL.
   - Убедиться, что `https://web.telegram.org`, `https://webk.telegram.org` и `null` (iOS WebView) в whitelist origins.
9. `docker-compose.yml` (api service) и `Dockerfile` (CMD) — проверить, что uvicorn слушает на `0.0.0.0:8000`:
   - Dockerfile CMD должно быть: `["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]`
   - Если `--host` отсутствует или `127.0.0.1` — исправить на `0.0.0.0`
10. `frontend/index.html` — CSP meta-tag должен разрешать Telegram WebApp:
     ```html
     <meta http-equiv="Content-Security-Policy"
           content="default-src 'self';
                    script-src 'self' 'unsafe-inline' https://telegram.org;
                    style-src 'self' 'unsafe-inline' https://fonts.googleapis.com;
                    font-src https://fonts.gstatic.com;
                    connect-src 'self';
                    img-src 'self' https://*.kufar.by data:;">
     ```
     (API на том же origin через Cloudflare tunnel — `connect-src 'self'` достаточно)
11. `api/config.py:44` — увеличить `telegram_init_data_max_age` с `3600` до `7200` (2 часа). 
    Cloudflare latency + время навигации в Mini App может превысить 1 час. HMAC-подпись всё равно защищает от подделки.

**Что НЕ делать:** НЕ трогать секреты в git history (BFG/filter-repo) — это ручная операция.

### Агент 2: @backend-logic

**Фокус:** P1 проблемы бэкенда, обработка ошибок, race conditions, логика, Cache-Control headers.

**Задачи:**
1. `api/main.py:127-139` (global exception handler) — добавить `exc_info=True` в `logger.error()`, чтобы traceback не терялся.
2. `api/limiter.py:78-82` — санитизировать Redis URL перед логированием (скрывать password через `urlparse`).
3. `api/routers/listings.py:121` и `api/routers/price_stats.py:104` — добавить graceful fallback для currency service (try/except, возвращать `{"BYN": 1.0}` при ошибке).
4. `api/routers/workflow.py` — добавить `@limiter.limit("30/minute")` на эндпоинты `/leads`, `/watchlist`, `/trackers`.
5. `api/routers/workflow.py:99` и `:322` — добавить пагинацию `limit`/`offset` для `/leads` и `/watchlist` (дефолт limit=50, max=200).
6. `api/services/ai_service.py:730` — уменьшить `read=180` до `read=45` в httpx.Timeout (Cloudflare таймаут ~100s, AI должен уложиться).
7. `api/routers/image_proxy.py:83-96` — добавить `asyncio.Lock` для `_transcoded_cache` (race condition при параллельных запросах).
8. `api/main.py` — добавить Cache-Control middleware (после security headers middleware):
    ```python
    @app.middleware("http")
    async def add_cache_control(request: Request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/api/v1/health"):
            pass  # health checks без кэша
        elif request.method in ("POST", "PATCH", "DELETE"):
            response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        elif request.url.path.startswith(("/api/v1/price-stats", "/api/v1/listings")):
            response.headers["Cache-Control"] = "max-age=300"
        return response
    ```
9. `api/services/ai_service.py` — вынести AI httpx.AsyncClient в отдельный connection pool:
    - Создать `_ai_client` с `limits=Limits(max_connections=20, max_keepalive_connections=10)`
    - Это предотвратит блокировку общего connection pool при долгих AI-запросах (read=45s).

### Агент 3: @database-performance

**Фокус:** Модели БД, индексы, производительность, глобальный mutable state, KufarClient resilience.

**Задачи:**
1. `api/models.py` — унифицировать типы PK/FK на `BigInteger`:
   - `users.id` → `BigInteger`
   - `UserIDMixin.user_id` → `BigInteger` (уже BigInteger, проверить)
   - ВАЖНО: сгенерировать Alembic-миграцию `alembic revision --autogenerate -m "unify pk types"`
   - ИЛИ (если локальная БД пересоздаётся) — синхронизировать ORM и миграции вручную
2. `api/models.py` — добавить partial index для trackers:
   ```python
   Index("idx_trackers_user_active", "user_id", "active", postgresql_where=text("active = true"))
   ```
3. `api/models.py` — добавить индекс `idx_trackers_last_checked` на `last_checked_at`.
4. `api/models.py:276` — добавить `DESC` к индексу tracker_events: `"created_at DESC"`.
5. `api/models.py:221` — `QueryListingState.updated_at` добавить `timezone=True`.
6. `api/services/cache.py` — `MemoryCache`: оборачивать ВСЕ операции с `_storage` (`get`, `set`, `delete`, `incr`) в `async with self._lock` (сейчас lock только в `incr()`).
7. `api/routers/workflow.py:629` — bulk INSERT для snapshots: использовать `session.execute(insert(LeadItemPriceSnapshot).values(snapshots))` вместо `session.add()` в цикле. Также проверить `refresh_watchlist` — если там тоже `record_price_snapshot` в цикле, собирать snapshots в список и делать bulk insert.
8. `api/services/kufar_client.py` — добавить `asyncio.Semaphore(settings.kufar_parallel_semaphore)` для ограничения параллельных HTTP-запросов.
9. `api/services/kufar_client.py` — добавить базовый circuit breaker (простой счётчик ошибок подряд, после N ошибок — fallback на заглушку на 30 секунд). Локально это важно, чтобы не забанили IP при частых запросах.
10. `api/services/query_pipeline.py:170` — `_inflight_dataset_futures: dict[str, asyncio.Future] = {}` — глобальный mutable state.
    - При Docker с одним worker `asyncio.Lock` бессмысленен (GIL + один event loop).
    - Добавить комментарий с предупреждением:
      ```python
      # NOTE: singleflight pattern breaks with multiple uvicorn workers (gunicorn).
      # TODO: migrate to Redis-based singleflight if scaling beyond 1 worker.
      ```

### Агент 4: @cleanup-frontend

**Фокус:** Мёртвый код, frontend cleanup, внутренние улучшения.

**Задачи:**
1. `frontend/js/render_cards.js` — удалить мёртвый код virtual scrolling (`_virtualList`, `itemHeight = 180`, параметры `_resetContainer`, `_virtualList`). Файл `virtual_list.js` подключён, но в комментариях "Virtual scrolling disabled" — либо включить, либо удалить мёртвый код и параметры.
2. Проверить `frontend/index.html` на 22 `<script defer>` тега — если есть дубли или неиспользуемые скрипты, убрать. НЕ нужно настраивать bundler, только убрать лишнее.
3. `docker-compose.yml` — добавить опциональный `cloudflared` сервис (закомментированный или с `profiles: ["tunnel"]`):
   ```yaml
   cloudflared:
     image: cloudflare/cloudflared:latest
     command: tunnel run
     environment:
       TUNNEL_TOKEN: ${CLOUDFLARE_TUNNEL_TOKEN}
     depends_on:
       api:
         condition: service_healthy
     networks:
       - kufar-net
     profiles: ["tunnel"]
   ```
4. `api/routers/health.py` — убедиться, что `/api/v1/health/ready` корректно работает и проверяет все зависимости (БД, Redis). Cloudflare tunnel использует healthchecks.

**Что НЕ делать:** НЕ настраивать Vite/Webpack/Rollup, НЕ минифицировать CSS — это задачи production-сборки.

## Вне scope (не добавлять)

| Проблема | Почему не добавлять |
|----------|---------------------|
| Nginx TLS termination | Cloudflare обрабатывает HTTPS |
| Nginx rate limiting | Cloudflare имеет built-in rate limiting |
| `server_tokens off` | Nginx не используется |
| Resource limits в docker-compose | Только для production Swarm/K8s |
| Prometheus / Grafana | Только для production monitoring |
| CSS/JS bundling | Production optimization, не критично |
| Restart policies | Полезно, но не критично для локального Docker |
| Webhook vs polling для бота | Рекомендация, не обязательно для локальной разработки |

## Правила координации

1. **Параллельный старт:** Все 4 агента стартуют одновременно.
2. **Конфликты:**
    - **`api/models.py`** — Агент 1 добавляет классы `SavedSearch` и `Contact`. Агент 3 меняет типы PK/FK и индексы.
      **Решение:** Агент 1 и Агент 3 читают файл ДО изменений и применяют `patch`-стратегию (добавлять, не перезаписывать). Либо оркестратор сначала запускает Агента 1, затем Агента 3.
    - **`api/main.py`** — Агент 1 (CSRF middleware, CORS origins, uvicorn bind) и Агент 2 (global exception handler, Cache-Control middleware).
      **Решение:** Агент 2 патчит `api/main.py` ПОСЛЕ Агента 1. Оркестратор: сначала Агент 1, потом Агент 2 (или Агент 2 читает актуальный файл после Агента 1).
    - **`api/routers/workflow.py`** — Агент 2 (rate limit + пагинация) и Агент 3 (bulk INSERT snapshots).
      **Решение:** Агент 2 запускается перед Агентом 3. Агент 3 читает актуальный файл.
3. **Проверка:** Каждый агент перед завершением запускает:
   - `python -m py_compile <changed_files>` (синтаксис Python)
   - `grep -n` для проверки, что изменения действительно применены
4. **Git:** После завершения ВСЕХ агентов, оркестратор делает:
   ```bash
   git add -A
   git diff --cached --stat  # показать что изменено
   git commit -m "fix(audit): resolve internal P0-P2 issues"
   ```

## Пример вызова агента

Для каждого агента используй:

```
task(description="<agent-name>", prompt="<полный текст задачи для агента из раздела выше>", subagent_type="general")
```

## Формат ответа

Оркестратор должен вернуть:
1. Список запущенных агентов с task_id
2. Результат каждого агента (успех/ошибка + diff)
3. Итоговый git status
4. Список проблем, которые НЕ удалось исправить (если есть)
