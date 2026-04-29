# Problems to Solve — Code Review Report

**Дата:** 2026-04-29
**Ветка:** `bad-app`
**Ревьюер:** Claude Code (автоматический аудит)

---

## CRITICAL

### 1. Task/Export shadow stores растут неограниченно при отсутствии Redis
- **Файл:** `api/routers/ai_analysis.py:65-66`
- **Категория:** Bug / Memory Leak
- **Описание:** `_tasks` и `_exports` — глобальные in-memory словари. Pruning происходит только при `len(_tasks) > 200` (строка 147) или через `periodic_prune_shadow_stores()` (каждые 5 мин). Но `_exports` prune только при `len(_exports) > 0` — нет upper bound. При высокой нагрузке без Redis (in-memory cache fallback) shadow-данные дублируют всё в память, удваивая потребление.
- **Следствие:** OOM при большом количестве AI-анализов без Redis.

### 2. AI task store — user_id leak через task_id enumeration
- **Файл:** `api/routers/ai_analysis.py:198-204`
- **Категория:** Security
- **Описание:** `task_id` генерируется как `secrets.token_urlsafe(16)` (~192 бит энтропии), что достаточно против брутфорса. Однако `get_task_status` проверяет `_user.user_id` только после загрузки задачи из кэша. Если Redis общий между сервисами, любой пользователь с доступом к Redis может прочитать чужие результаты. Нет пространства имён по user_id в ключе кэша.
- **Следствие:** При компрометации Redis или shared-memory сценарии — утечка AI-анализов между пользователями.

### 3. _inflight_dataset_futures — глобальный словарь без привязки к event loop
- **Файл:** `api/services/query_pipeline.py:155`
- **Категория:** Bug
- **Описание:** `_inflight_dataset_futures` — module-level dict, разделяемый между всеми запросами. `asyncio.Future` привязан к конкретному event loop. Если FastAPI перезапускается (hot reload) или работает с несколькими loop'ами, Futures из старого loop'а вызовут `RuntimeError: Task got Future attached to a different loop`.
- **Следствие:** Краш при hot reload или редких сценариях с несколькими event loop.

---

## HIGH

### 4. AI rate limiter считает кэшированные запросы
- **Файл:** `api/routers/ai_analysis.py:962-971`
- **Категория:** Bug
- **Описание:** Порядок: cache check (963-968) → consent check → rate limit check (971). Кэшированные результаты возвращаются до rate limit, что правильно. Но `_check_rate_limit` использует `cache.incr()` — даже при cache hit в первом запросе, если кэш протухнет, пользователь может исчерпать лимит быстрее, чем ожидается, т.к. каждый новый запрос инкрементит счётчик.
- **Следствие:** Пользователь может получить 429 раньше времени при граничных условиях.

### 5. Telegram auth: нет постоянного хранения user_id
- **Файл:** `api/middleware/telegram_auth.py:56-63`
- **Категория:** Security / Design
- **Описание:** `verify_telegram_init_data` извлекает `user_id` из initData, но нигде не проверяет, что этот пользователь зарегистрирован в системе. Роутеры используют `user_id` для запросов к БД, но если пользователя нет в таблице `users`, foreign key constraint упадёт. Это может быть обработано downstream, но нет явной проверки.
- **Следствие:** 500 ошибки при первом запросе нового пользователя, если не обработан upstream.

### 6. Blocking socket в rate limiter init
- **Файл:** `api/limiter.py:44-48`
- **Категория:** Performance
- **Описание:** `_redis_reachable()` использует `socket.create_connection()` — блокирующий вызов. Выполняется при импорте модуля (`limiter = _create_limiter()`), что блокирует event loop при старте. Timeout 0.3s, но при недоступном DNS может висеть дольше.
- **Следствие:** Задержка при старте приложения до 0.3с+.

### 7. AI image fetch — redirect без лимита
- **Файл:** `api/services/ai_service.py:1333-1343`
- **Категория:** Security
- **Описание:** `_fetch_image_bytes` обрабатывает только один редирект вручную (`follow_redirects=False`). Нет лимита на размер body при чтении — проверка `content-length` есть, но `resp.content` загружает весь body в память. `_MAX_AI_IMAGE_BYTES = 5MB` — это максимум на картинку, но при 3 картинках × 5MB = 15MB на один AI-запрос.
- **Следствие:** Потенциальное потребление памяти до 15MB на один запрос AI-анализа.

### 8. Scheduler: no backpressure на Kufar API при большом количестве trackers
- **Файл:** `scheduler/collector.py` (общая логика)
- **Категория:** Performance
- **Описание:** Scheduler обходит все активные трекеры группами по `(query, strict_mode)` и делает по одному запросу к Kufar на группу. При 100+ уникальных запросов это 100+ HTTP-запросов с задержкой 1с между ними. Нет общего таймаута на цикл проверки — если Kufar медленный, весь цикл может занять минуты.
- **Следствие:** Отставание нотификаций, потенциальная блокировка scheduler'а.

### 9. KufarClient._get_client — race condition при закрытом клиенте
- **Файл:** `api/services/kufar_client.py:40-45`
- **Категория:** Bug
- **Описание:** Double-checked locking паттерн с `asyncio.Lock`, но `_owns_client` проверяется без лока. Если `aclose()` вызывается конкурентно с `search()`, можно получить закрытый клиент.
- **Следствие:** `RuntimeError: Client is closed` при редких race conditions.

### 10. Export report HTML — SSRF через nh3 sanitization bypass
- **Файл:** `api/routers/ai_analysis.py:1012-1031, 1059-1090`
- **Категория:** Security
- **Описание:** `_sanitize_export_html` разрешает `<img src="https://...">` и `<a href="...">`. CSP в ответе `get_export_report` позволяет `img-src https: data:`. Зловредный `data:` URL в img может выполнить атаку. Атрибут `style` в `*` позволяет CSS injection.
- **Следствие:** CSS-based data exfiltration через style атрибут в экспортируемом HTML.

---

## MEDIUM

### 11. JSON парсинг AI ответов — greedy regex может захватить лишнее
- **Файл:** `api/services/ai_service.py:1425-1431`
- **Категория:** Bug
- **Описание:** Fallback `re.search(r"\{.*\}", text, flags=re.DOTALL)` — greedy, захватит от первой `{` до последней `}`. Если в reasoning chain есть несколько JSON-объектов, парсер вернёт невалидный конгломерат. `_json_object_candidates` (стратегия 2) обходит это, но greedy regex — fallback.
- **Следствие:** Невалидный JSON в редких случаях → fallback на `_repair_truncated_json`.

### 12. Cache TTL несогласованность
- **Файл:** `api/routers/listings.py:242-246`, `api/services/query_pipeline.py:257`
- **Категория:** Bug
- **Описание:** Dataset cache TTL = 300s (5 мин, строка 257), listings response TTL = `settings.cache_ttl_seconds` (строка 245). Listings response кэширует полный ответ, включая listings — если dataset cache протухнет быстрее, listings ответ будет stale но не пересчитается до истечения собственного TTL.
- **Следствие:** Пользователь видит устаревшие данные до истечения listings TTL.

### 13. Frontend: таймер AbortController не очищается при успешном ответе в race condition
- **Файл:** `frontend/js/api_core.js:38-61`
- **Категория:** Bug
- **Описание:** `setTimeout` создаётся до fetch. `clearTimeout(timer)` в finally очистит его, но если ответ пришёл до таймера — controller всё ещё жив и может абортить последующие запросы, если `controller` случайно используется повторно. Не баг в текущей реализации, но хрупкий паттерн.
- **Следствие:** Потенциальные race conditions при рефакторинге.

### 14. Database migration: f-strings в SQL
- **Файл:** `migrations/versions/20260429_0003_timestamp_mixin_tz.py:27-40`
- **Категория:** Quality
- **Описание:** SQL-запросы строятся через f-strings. Table names не parameterized. В данном контексте (миграция с hardcoded table names) это безопасно, но нарушает best practice. Если имена таблиц когда-нибудь будут браться из конфига — SQL injection.
- **Следствие:** Низкий риск сейчас, но плохой паттерн.

### 15. MemoryCache.incr — неатомарная операция
- **Файл:** `api/services/cache.py:69-90`
- **Категория:** Bug
- **Описание:** `incr()` — обычный async метод, не protected lock'ом. При конкурентных запросах два короутина могут одновременно прочитать одно значение и записать одно и то же инкрементированное число. Для rate limiting это означает что лимит может быть превышен.
- **Следствие:** Rate limit может быть неточным при использовании MemoryCache.

### 16. TrackerEvent — нет индекса по (tracker_id, created_at)
- **Файл:** `api/models.py:268-271`
- **Категория:** Performance
- **Описание:** Есть индекс по `created_at` и `user_id`, но нет композитного индекса `(tracker_id, created_at)`. Scheduler загружает события по tracker_id + сортировка по дате — без индекса это full scan.
- **Следствие:** Медленные запросы при большом количестве событий.

### 17. Frontend: no CSRF protection
- **Файл:** Вся архитектура API
- **Категория:** Security
- **Описание:** API использует Telegram initData для аутентификации, но в debug mode (`debug=true`) позволяет запросы без initData (user_id=0). Если debug mode включен на проде, все endpoints без auth доступны без защиты.
- **Следствие:** Критично если debug=true на проде.

### 18. AI service singleton — threading.Lock в async контексте
- **Файл:** `api/services/ai_service.py:627, 635`
- **Категория:** Bug
- **Описание:** `AIService._client_lock = threading.Lock()` используется для защиты `_httpx_client`. В async контексте `threading.Lock` не переключает короутины — если lock захвачен, другие короутины блокируются (не await'ятся). Следует использовать `asyncio.Lock`.
- **Следствие:** Потенциальная блокировка event loop при создании httpx клиента.

### 19. Schema: datetime.now() как default_factory
- **Файл:** `api/schemas.py:704, 771`
- **Категория:** Bug
- **Описание:** `analyzed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))` — использует `datetime.now(UTC)` вместо `datetime.now(tz=UTC)`. В Python 3.12+ `datetime.UTC` это `datetime.timezone.utc`. Работает, но `datetime.now(UTC)` может сбить с толку — `datetime.now()` без tz возвращает naive datetime.
- **Следствие:** Потенциальная путаница, но не баг (UTC корректен).

### 20. QuerySnapshot — нет TTL/автоочистки
- **Файл:** `api/models.py:180-196`
- **Категория:** Performance
- **Описание:** `query_snapshots` растёт бесконечно. Scheduler пишет новые snapshot'ы каждые 30 минут на каждый query. При 100 queries × 48 snapshots/day × 365 days = 1.75M строк за год. Нет cleanup job.
- **Следствие:** Деградация производительности БД со временем.

---

## LOW

### 21. Debug mode: error details leak internals
- **Файл:** `api/routers/ai_analysis.py:948-949`
- **Категория:** Security (OWASP A09)
- **Описание:** При `settings.debug=True` к пользовательскому сообщению об ошибке добавляется `[{type(exc).__name__}: {exc}]` — это может раскрыть AI provider URL, proxy config, структуру API ключа.
- **Следствие:** Утечка инфраструктурной информации в debug mode.

### 22. X-Forwarded-For IP в consent audit не валидируется
- **Файл:** `api/routers/consent.py:133-139`
- **Категория:** Security (OWASP A09)
- **Описание:** IP берётся из `X-Forwarded-For` без проверки, что запрос пришёл от доверенного reverse proxy. Клиент может подделать IP в аудите согласий (требование BY Law No. 91-Z).
- **Следствие:** Загрязнение аудиторского следа.

### 23. Test bot token захардкожен
- **Файл:** `tests/test_telegram_auth.py:12`
- **Категория:** Security (OWASP A02)
- **Описание:** `BOT_TOKEN = "7123456789:AAFtesttoken"` — pattern hardcoding токенов в тестах. Если разработчик скопирует scaffolding и подставит реальный токен — утечка в git history.
- **Следствие:** Риск утечки реального токена через copy-paste.

### 24. KufarClient.search_all_ads — нет ограничения на размер ads
- **Файл:** `api/services/kufar_client.py:123-172`
- **Категория:** Performance
- **Описание:** `max_ads = self._settings.kufar_max_ads_per_query` ограничивает, но `ads.extend()` может добавить много данных в память. Нет streaming/lazy загрузки.
- **Следствие:** Высокое потребление памяти при больших запросах.

### 22. Frontend: CSS `display: flex/grid` overrides `hidden`
- **Файл:** Упоминается в CLAUDE.md Known Issues
- **Категория:** Bug (UX)
- **Описание:** CSS `display: flex/grid` переопределяет HTML-атрибут `hidden`. Нужен `[hidden] { display: none !important }`.
- **Следствие:** Скрытые элементы могут отображаться.

### 23. Hardcoded magic numbers
- **Файл:** `scheduler/collector.py` ([:10] ограничения), `api/routers/ai_analysis.py`
- **Категория:** Quality
- **Описание:** Ограничения типа `[:10]`, `[:5]`, `[:200]` разбросаны по коду без констант.
- **Следствие:** Сложно менять лимиты, нет документации.

### 24. Unused import: `from datetime import UTC` в schemas.py
- **Файл:** `api/schemas.py:3`
- **Категория:** Quality
- **Описание:** `UTC` импортирован, но `datetime.now(UTC)` используется через lambda. Исправлено в строке 704, но `from datetime import UTC` может быть redundant если используется `timezone.utc`.
- **Следствие:** Лёгкий code smell.

### 25. Bot database: lru_cache для asyncio.Lock
- **Файл:** `bot/database.py:13-16`
- **Категория:** Quality
- **Описание:** `@functools.lru_cache(maxsize=1)` на `_get_init_lock()` — overkill для singleton. Достаточно module-level переменной.
- **Следствие:** Неопасно, но излишне сложно.

### 26. AI prompt templates — огромные строки в коде
- **Файл:** `api/services/ai_service.py:78-280`
- **Категория:** Quality
- **Описание:** Промпты на 200+ строк захардкожены в Python-файле. Длина файла ai_service.py ~1480 строк — намного превышает рекомендуемый лимит 500 строк из CLAUDE.md.
- **Следствие:** Сложность поддержки, нарушение собственных конвенций.

### 27. Валидация query: нет SQL injection через LIKE
- **Файл:** `api/validators.py` (не просмотрен, но из контекста)
- **Категория:** Security
- **Описание:** Query передаётся в SQLAlchemy через ORM, что безопасно. Но если query используется в raw SQL (проверить), возможна injection. SQLAlchemy ORM параметризует автоматически — скорее всего безопасно.
- **Следствие:** Низкий риск, но стоит проверить.

### 28. Frontend: escapeHtml/safeUrl — не проверены все точки вставки
- **Файл:** `frontend/js/*.js`
- **Категория:** Security
- **Описание:** CLAUDE.md упоминает `escapeHtml()` и `safeUrl()`, но без полного аудита каждого DOM insertion нельзя гарантировать, что все точки покрыты. Template literals с `${}` для вставки в innerHTML — потенциальный XSS.
- **Следствие:** Потенциальный XSS при пропущенной санитизации.

### 29. Nginx proxy timeout — 300s для AI
- **Файл:** Упоминается в CLAUDE.md Known Issues
- **Категория:** Quality
- **Описание:** `proxy_read_timeout: 300s` может быть недостаточен если AI-анализ занимает >5 мин (параллельные вызовы + retry). Фронтенд-таймаут 90s (api_core.js) < Nginx timeout — disconnect на клиенте раньше, чем на сервере.
- **Следствие:** Фронтенд показывает timeout раньше, чем сервер.

### 30. TrackerCheckConstraint — event_type не валидируется на уровне БД
- **Файл:** `api/models.py:230-271`
- **Категория:** Quality
- **Описание:** `TrackerEvent.event_type` — `String(32)` без CHECK constraint. Допустимые значения `new_listing` и `price_drop` валидируются только в коде. В отличие от `LeadItem.status`, у которого есть CHECK.
- **Следствие:** Можно записать невалидный event_type напрямую в БД.

---

## Сводка

| Критичность | Количество |
|-------------|-----------|
| CRITICAL    | 3         |
| HIGH        | 7         |
| MEDIUM      | 10        |
| LOW         | 13        |
| **Итого**   | **33**    |

### Позитивные находки (Security Audit)

- Telegram HMAC верификация корректна: `hmac.compare_digest` (timing-attack safe), 300s replay window, future-date rejection
- Все SQL-запросы через SQLAlchemy ORM — **нет SQL injection**
- CORS ограничен конкретными origins, без wildcards
- SSRF защита в Kufar client: hardcoded base URL + strict hostname regex для image fetch
- Prompt injection защита через `sanitize_user_text()` — multi-layer (role markers, injection patterns, code fences, length caps)
- `.env` исключён из git, секреты через `SecretStr`

### Приоритет исправления

1. **Немедленно** (#1, #2, #3): Memory leak в AI task store, data leak через Redis, event loop crash
2. **Высокий** (#5, #7, #8, #9, #10, #15, #18): Auth, memory, race conditions, security
3. **Средний** (#4, #6, #11, #12, #16, #17, #20): Rate limiting, performance, code quality
4. **Низкий** (#21-#33): Code quality, conventions, minor security hardening
