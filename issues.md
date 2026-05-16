# Kufar Analytics — Deep Dive Issue Log

Дата отчёта: 2026-05-16. Источники: 12 параллельных специализированных агентов
(backend, AI, DB, scheduler/bot, frontend JS, frontend design, security,
performance, infrastructure, tests, business-logic correctness, consistency)
плюс прямые проверки в исходниках. Каждый пункт указывает `файл:строка` и
содержит severity. Ложные срабатывания агентов отброшены (см. Appendix A).

Соглашения:

- **CRITICAL** — потенциальная потеря данных, обход аутентификации, видимый
  пользователю баг в денежной логике, RCE/SSRF/PII-leak.
- **HIGH** — ломает функциональную область при типичных условиях, но не
  компрометирует безопасность мгновенно.
- **MEDIUM** — деградация UX/perf/maintainability, потенциально опасные
  паттерны.
- **LOW** — стиль, мелкая чистка, документация.

> Политика из `AGENTS.md` учтена: ротация секретов, off-host backup, IaC,
> мониторинг, Vault и pkill-в-локальных-скриптах — намеренно отложены и
> здесь не повторяются. Перечисленные ниже пункты — это всё, что *должно*
> быть исправлено в коде.

---

## 0. Сводка по доменам

| Домен | CRITICAL | HIGH | MEDIUM | LOW |
|-------|----------|------|--------|-----|
| Security | 0 | 1 | 7 | 9 |
| Backend Core / Routers / Services | 0 | 4 | 14 | 11 |
| AI subsystem | 2 | 5 | 7 | 5 |
| DB / Models / Migrations | 1 | 0 | 8 | 7 |
| Scheduler / Bot | 0 | 3 | 9 | 5 |
| Frontend JS | 0 | 0 | 9 | 6 |
| Frontend Design / CSS | 0 | 2 | 11 | 6 |
| Performance | 0 | 5 | 11 | 4 |
| Infrastructure | 0 | 1 | 9 | 11 |
| Tests / QA | 0 | 0 | 9 | 5 |
| Business-logic correctness | 0 | 3 | 6 | 13 |
| Consistency / Docs | 0 | 0 | 4 | 6 |

Всего ≈ 220 находок. Самые «горячие» зоны: AI pipeline (concurrency
bottleneck + privacy gaps), workflow state machine (нет валидации
переходов, abs() на скидках, expense double-add), DB partitioning skript,
бандл фронта vs gzip-only nginx, Cloudflare IPv6 missing.

---

## 1. Security

### 1.1 Auth & Sessions

- **[SEC-CRITICAL]** Нет — 0 пунктов.
- **[SEC-HIGH]** `api/services/workflow_store.py:7-30` — `ensure_user` использует
  `pg_insert(...).on_conflict_do_nothing()` безусловно. На SQLite (тест-стенд
  и любой fallback-режим) это валится `OperationalError`. `api/dependencies.py`
  делает диалект-детект, а здесь — нет. Скопировать ту же логику.
- **[SEC-MEDIUM]** `api/config.py:113` — `telegram_init_data_max_age=7200` (2 часа).
  Украденный initData валиден 2 часа. `track_init_data_use` логирует
  IP-mismatch и блокирует после 5 предупреждений, но replay с того же IP
  невидим. Сократить до 900–1800 с.
- **[SEC-MEDIUM]** `api/services/session_security.py:55` — fallback на
  «разрешить» при недоступности Redis. Смягчает DoS, но единственный канал
  отзыва токенов превращается в no-op при выпадении кэша.
- **[SEC-LOW]** `api/dependencies.py:92` — guard `os.environ.get("ENV") == "production"`
  для `auth_bypass` живёт рядом с настоящим валидатором в `config.py:126`.
  Дублирующая проверка, но через ENV — фрагильно при использовании systemd
  unit с переопределённым окружением.
- **[SEC-LOW]** `api/dependencies.py:117` — service-token путь не пишет в
  `track_init_data_use`. Скомпрометированный `INTERNAL_SERVICE_TOKEN` даёт
  безграничный impersonation без IP-телеметрии.
- **[SEC-LOW]** `bot/auth.py:13-29` — бот выпускает initData для любого
  `telegram_user_id` через общий BOT_TOKEN. Компрометация процесса бота =
  impersonation любого пользователя. `INTERNAL_SERVICE_TOKEN` уже есть —
  устаревший forge-путь стоит удалить.

### 1.2 CSRF, CORS, Headers

- **[SEC-MEDIUM]** `nginx/default.conf:195` vs `api/main.py:195-205` —
  `webz.telegram.org` пропускается nginx CORS, но FastAPI CSRF-allowlist
  его не содержит. Telegram Web Z получит 403 на любой POST/PUT/DELETE.
  Добавить `https://webz.telegram.org` в `_csrf_allowed`.
- **[SEC-MEDIUM]** `nginx/default.conf` — отсутствует `server_tokens off;`.
  Версия nginx светится в `Server:` и в страницах ошибок.
- **[SEC-LOW]** `nginx/default.conf` — HSTS установлен только в FastAPI
  (`api/main.py:227`), для статики, отдаваемой nginx-ом, — нет. Добавить
  `add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;`
  в `server` блок.
- **[SEC-LOW]** `nginx/default.conf:82,233` — CSP `frame-ancestors`
  перечисляет `web.telegram.org` и `webk.telegram.org`, но не `webz.telegram.org`.
- **[SEC-LOW]** `api/main.py:248` — CSRF Origin сверяется case-sensitive.
  Браузеры нормализуют, но если хост-приложение пришлёт UPPERCASE — отказ.

### 1.3 IDOR & Authorization

- *Положительно:* все id-в-пути роутеры (`trackers.py:224,249`,
  `workflow.py:279,366`, `expenses.py:87,113,137`, `reminders.py:97,137`)
  фильтруют по `user_id` в SQL-WHERE. IDOR не найден.
- **[SEC-LOW]** `api/routers/trackers.py:44` — `get_tracker_events` принимает
  `tracker_id` без подтверждения принадлежности. Данные не утекают, но
  можно зондировать существование чужих tracker_id (information disclosure).

### 1.4 SSRF & Untrusted IO

- *Положительно:* `image_proxy.py` хардкодит `https://rms.kufar.by/v1/gallery/`,
  `follow_redirects=False`, max_bytes, timeout — SSRF закрыт.
- **[SEC-LOW]** `proxy-server.mjs:40` — `join(FRONTEND_DIR, url.pathname)` не
  проверяет, что результат остаётся внутри `FRONTEND_DIR`. Path traversal на
  dev-сервере. Не используется в проде, но в `Dockerfile` нет явного
  исключения.

### 1.5 Rate Limiting & Abuse

- **[SEC-MEDIUM]** `api/limiter.py:22-26` + `docker-compose.yml:79` — порт
  API `8010:8000` опубликован на `0.0.0.0`. Прямой доступ обходит nginx
  rate-limit и trusted-proxy fence; `X-Forwarded-For` спуфится. Привязать
  к `127.0.0.1:8010:8000`.
- **[SEC-MEDIUM]** `api/services/client_ip.py:5-19` и `nginx/default.conf:5-22` —
  Cloudflare IPv6-диапазоны (`2400:cb00::/32`, `2606:4700::/32`,
  `2803:f800::/32`, `2c0f:f248::/32`, `2a06:98c0::/29`) отсутствуют. IPv6
  трафик через Cloudflare → весь трафик «бакетится» по edge-IP, rate-limit
  ломается. См. также Recurring Ops в `AGENTS.md` — годовое обновление.

### 1.6 Logging & PII

- **[SEC-LOW]** `api/services/session_security.py:107` — `digest[:12]` initData
  + `first_ip` + `observed_ip` в одной строке. Частичный хеш + IP помогают
  коррелировать сессии.
- **[SEC-LOW]** `api/routers/consent.py:350` — экспорт согласия включает
  `ip_address`. Это сделано осознанно для Закона 99-З (право доступа к
  своим данным), но клиент получает поле без дополнительного auth-шага.
- **[SEC-LOW]** `api/services/ai_audit.py:20-24` — `audit_text_sha256` без
  соли. Идентичные запросы разных пользователей дают одинаковый хеш →
  rainbow-таблица популярных Kufar-запросов сводит хеши обратно в plaintext.

### 1.7 Secrets & Containers

- **[SEC-MEDIUM]** `Dockerfile.frontend` — нет `USER`. nginx внутри контейнера
  запускается root-ом. Добавить `USER nginx`.
- **[SEC-MEDIUM]** `docker-compose.yml` — ни у одного сервиса нет
  `read_only: true`, `cap_drop: [ALL]`, `security_opt: [no-new-privileges:true]`.
- **[SEC-LOW]** `docker-compose.yml:261` (`postgres`) — `5433:5432` биндится
  на `0.0.0.0`. Переключить на `127.0.0.1:5433:5432` (или удалить, оно
  и так под profile `local-db`).
- **[SEC-LOW]** `docker-compose.yml:213` (`redis`) — пароль идёт через ENV,
  виден в `docker inspect`. AGENTS.md помечает Vault как H3 deferred —
  оставить как есть, задокументировать в RUNBOOK.

### 1.8 CI / supply-chain

- **[SEC-HIGH-effective-MEDIUM]** `.github/workflows/ci.yml` — все
  `uses:` пинятся по `@v3/@v4/@v5` (тег, не SHA). Компрометированный
  тэг → выполнение произвольного кода в CI. Закрепить через 40-знаковый SHA
  (`actions/checkout@<sha>`, `astral-sh/setup-uv@<sha>`,
  `codecov/codecov-action@<sha>`).
- **[SEC-LOW]** `.github/workflows/ci.yml:117` — `codecov/codecov-action@v4`
  без `token`. На приватный репозиторий тихо мажет загрузку покрытия.
- **[SEC-LOW]** `.github/workflows/ci.yml:5` — триггер CI содержит ветку
  `bad-app` (рабочая, не main). Если ветка по политике временная — убрать.

### 1.9 Telegram & Bot

- *Положительно:* `bot/handlers/start.py:27-30` — deep-link param-allowlist
  (`tracking|deals|monitoring`), нет утечки данных других пользователей,
  health-port 8001 без секретных полей.
- **[SEC-LOW]** `bot/handlers/analytics.py:67-90` — handlers `/deals`/`/profit`
  собирают HTML без `html.escape` для значений. Сейчас всё числовое, но
  как только в формат попадёт user-controlled string — поломка parse_mode
  или micro-injection.

---

## 2. Backend (FastAPI core, routers, services)

### 2.1 Core (main, config, deps, middleware)

- **[BE-MEDIUM]** `api/main.py:247` — `_provisioned_users` dict и `_last_sweep_at`
  модифицируются из middleware без `asyncio.Lock`. Sweep-цикл `dict.pop` во
  время чужой `dict.__setitem__` может бросить `RuntimeError: dictionary
  changed size during iteration`.
- **[BE-MEDIUM]** `api/main.py:172` — комментарий о `null` Origin вводит в
  заблуждение: `allow_credentials=False` сам по себе блокирует credentialed
  cross-origin запросы независимо от списка origin'ов.
- **[BE-MEDIUM]** `api/dependencies.py:100` — `import secrets` находится в
  теле функции, выполняется на каждом service-token запросе. Перенести на
  модульный уровень.
- **[BE-LOW]** `api/main.py:195` — `import secrets as _secrets` тоже внутри
  middleware, та же проблема.
- **[BE-LOW]** `api/config.py:87` — `field_validator("debug")` читает
  `info.data.get("database_url")`. Pydantic v2 валидаторы выполняются в
  порядке определения полей. Сейчас работает, потому что `database_url`
  объявлен раньше; перетряска класса всё сломает молча.
- **[BE-LOW]** `api/validators.py:1-14` — модуль остался ради единственной
  константы `MAX_QUERY_LENGTH = 255`. Инлайнить в `schemas.py` или общий
  `constants.py`.

### 2.2 Routers (общие проблемы)

- **[BE-HIGH]** `api/schemas.py:318,353,400,465,547` — `TrackerRead`,
  `TrackerEventRead`, `LeadRead`, `WatchlistRead`, `DealExpenseRead` отдают
  внутренний autoincrement `user_id` (PK таблицы users), а не
  `telegram_user_id`. Это утечка внутренних идентификаторов клиенту.
  Убрать поле или замапить на `telegram_user_id`.
- **[BE-MEDIUM]** `api/routers/workflow.py:149,393` — `get_leads`,
  `get_watchlist`: `offset` ограничен снизу `ge=0`, верхней границы нет.
  `offset=999_999_999` вынудит DB сканировать миллионы строк. Добавить
  `le=10_000`.
- **[BE-MEDIUM]** `api/routers/trackers.py:30` — `limit: int = 20` без
  `Query(...)` валидации. `min(limit, 50)` строкой ниже клиппит, но
  `limit=-1` ещё попадает в тело. Добавить `Query(ge=1, le=50)`.
- **[BE-MEDIUM]** `api/routers/price_history.py:35` — `days: int = 7` без
  `Query(ge=1, le=90)`. Зажим есть, но 422 не возвращается, OpenAPI не
  отражает контракт.
- **[BE-MEDIUM]** Кэш-ключи строятся через f-string с разделителем `:`
  при «сыром» query. Конфликты при наличии `:` внутри запроса:
  - `api/routers/geography.py:44`,
  - `api/routers/segments.py:48`,
  - `api/routers/listing_detail.py:46`,
  - `api/routers/price_history.py:44` (двойной `::` в `build_query_key`).
  Использовать `digest_cache_key(...)` как в `listings.py`.
- **[BE-MEDIUM]** `api/routers/geography.py:52` — нет try/except вокруг
  `currency_service.get_rates()`. NBRB upstream падает → 500. Обернуть
  как в `listings.py`/`price_stats.py`.
- **[BE-MEDIUM]** `api/schemas.py:82-92` — `PriceStatsResponse` использует
  `float` для денежных полей. ORM возвращает `Decimal` (`Numeric(12,2)`),
  smartcast в float теряет точность на больших суммах (>= 9 999 999.99 BYN).
  Использовать `Decimal` или round в граничном слое.
- **[BE-LOW]** `api/routers/ai_analysis.py:145` — кэш AI не user-scoped.
  Это намеренно (стоимость), но второй пользователь технически наблюдает
  обработку под чужое согласие. См. также блок «AI / Privacy».
- **[BE-LOW]** `api/routers/listings.py:1` — лишний `Any` в импорте typing.
- **[BE-LOW]** `api/routers/workflow.py:1-600` — 600+ строк объединяют leads
  и watchlist. Разбить на `leads.py` + `watchlist.py`.

### 2.3 Services

- **[BE-HIGH]** `api/services/workflow_store.py:7-30` — см. SEC §1.1: `pg_insert`
  безусловно. Дубль для удобства поиска: добавить dialect-detect.
- **[BE-MEDIUM]** `api/services/query_pipeline.py:207-210` — `_inflight_lock`
  ленивая инициализация без guard. Две корутины могут создать два разных
  `asyncio.Lock`. На практике первой вызов случается на startup, но
  паттерн опасный.
- **[BE-MEDIUM]** `api/services/currency_service.py:50` — `get_rates()` ловит
  широкий tuple, включая `RuntimeError`. Если httpx-клиент закрыт во время
  shutdown, мы тихо отдадим fallback rates вместо ошибки.
- **[BE-MEDIUM]** `api/services/history_service.py:85` — `upsert_query_snapshot`
  использует `session.begin_nested()`. Если в `IntegrityError`-ветке re-SELECT
  возвращает `None`, сессия уже в неконсистентном состоянии. Выше
  `_persist_snapshot_safe` глотает всё, но снимок теряется без логов.
- **[BE-MEDIUM]** `api/services/deal_workflow.py:1-230` — `compute_flip_estimates`
  и `compute_liquidity_insight` используют `float`-арифметику для денег и
  процентов. На BYN до 10M округление накапливается. Перевести на `Decimal`
  или round промежуточных.
- **[BE-MEDIUM]** `api/services/cache.py:95` — `MemoryCache.incr` в `except`-ветке
  при corruption переиспользует старый `expires_at`, вместо TTL-сброса.
- **[BE-MEDIUM]** `api/services/aggregator.py` — 37 KB файл с
  смешанными ответственностями (search normalize, price stats, segments,
  category, cluster, deal-filter). Разбить на 3-4 модуля.
- **[BE-MEDIUM]** `api/services/workflow_store.py:80` — `load_last_snapshot_prices`
  использует `func.max(LeadItemPriceSnapshot.id)` через `.scalar_subquery()`,
  но субзапрос возвращает несколько строк (по одному `max(id)` на группу).
  Заменить на `.subquery()` или коррелированную форму.
- **[BE-LOW]** `api/services/parallel_kufar.py:35-50` — `parallel_search`
  глотает исключения по тасках и подменяет пустыми. Вызывающий не отличит
  «0 объявлений» от «Kufar упал». Возвращать богаче, либо WARNING (есть).
- **[BE-LOW]** `api/services/session_security.py:27` — `_INITDATA_TTL_SECONDS=7200`
  захардкожен под `telegram_init_data_max_age`. Если settings меняется —
  расходятся.
- **[BE-LOW]** `api/services/client_ip.py:72` — `_TRUSTED_PROXY_IP_RANGES`
  включает `172.16.0.0/12` (Docker default). Скомпрометированный соседний
  контейнер сможет спуфить `X-Forwarded-For`.
- **[BE-LOW]** `api/services/consent_policy.py:1-3` — модуль на 3 строки.
  Инлайнить.
- **[BE-LOW]** `api/services/kufar_client.py:95` — `_enforce_delay` при первом
  вызове `loop.time() - 0.0` всегда «давно», поэтому первый запрос идёт
  без задержки. По факту OK, но не задокументировано.
- **[BE-LOW]** `api/services/kufar_client.py:100` — `search()` не валидирует
  длину `query` — внешний вызов из scheduler/AI может прислать неограниченный
  текст. Защитный truncate.
- **[BE-LOW]** `api/services/query_pipeline.py:28` — `_CATEGORY_TOTAL_MAX_CALLS = CATEGORY_TOTAL_MAX_CALLS`
  лишний alias.

---

## 3. AI subsystem

### 3.1 Pipeline & Service

- **[AI-CRITICAL]** `api/services/ai_sanitize.py:144-146` — комментарий явно
  признаёт, что `_PROMPT_INJECTION_PATTERNS` и `_PROMPT_ROLE_MARKERS` обходятся
  Unicode-гомоглифами (кириллическая «а» вместо латинской, fullwidth `[`).
  NFKC/confusables-нормализация перед regex отсутствует. Crafted Kufar-тайтл
  пройдёт фильтр.
- **[AI-CRITICAL]** `api/services/ai_service.py:_parse_json` (~ str. 580) —
  парсит произвольный JSON и возвращает сырой dict. Ни `analyze_listing_parallel`,
  ни `generate_listing` не валидируют его Pydantic-схемой. `_repair_truncated_json`
  (~ str. 200) умеет вернуть частичный dict с None — он молча течёт дальше.
- **[AI-HIGH]** `api/services/ai_images.py:114-131` — `compress_image` делает
  синхронный `PIL.Image.open/resize/save` внутри асинхронной цепочки
  (`fetch_image_b64` → `compress_image`). 3 × 5 МБ блокируют event loop на
  1–3 c. Обернуть в `asyncio.to_thread`.
- **[AI-HIGH]** `api/services/ai_marketplace.py:34-35,524-525` — синхронные
  `open()` + `json.load()` крупных файлов на import-time. Блок event loop
  при первом импорте в worker.
- **[AI-HIGH]** `api/services/ai_analysis_pipeline.py:250,931` — кэш-ключ
  `ai_analysis:v5:{ad_id}:{query}:cat={category}` хардкодит «v5». Любое
  изменение `ai_prompts.py` не инвалидирует кэш до TTL (3600 c). Та же
  проблема в `api/routers/ai_listing_assistant.py` (`"v","3"` в hash).
- **[AI-HIGH]** `api/services/ai_analysis_pipeline.py:195` —
  `_ANALYSIS_RUN_LIMIT = 1`: один анализ за раз на worker. С 30–90 c на
  анализ это жёсткое горлышко. `_BG_TASK_LIMIT = 16` не помогает —
  очередь упирается в 1.
- **[AI-MEDIUM]** `api/services/ai_analysis_pipeline.py:180-193` и
  `api/services/ai_task_store.py:_task_lock` — ленивая инициализация
  `Semaphore`/`Lock` без guard. На CPython «случайно» работает из-за GIL.
- **[AI-MEDIUM]** `api/services/ai_task_store.py:60` — watchdog 240 c, тогда
  как `ai_analysis_timeout=150` c. Зазор 90 c, в течение которого таска
  может «висеть» на не-AI работе (поиск/скоринг) без прогресса.
- **[AI-MEDIUM]** `api/services/ai_analysis_pipeline.py:_run_analysis` —
  ловит широкий `_AI_ANALYSIS_ERRORS` (включает `RuntimeError`), но не
  re-raise-ит `asyncio.CancelledError`. При shutdown таск молча отдаёт
  fallback вместо распространения отмены.

### 3.2 Routers & Consent

- **[AI-CRITICAL]** `api/services/ai_guards.py:82` — `if get_settings().debug: return`
  пропускает все consent-проверки в debug-режиме. Случайно включённый
  `DEBUG=True` в проде = AI-эндпоинты без согласия = нарушение Закона 99-З.
- **[AI-HIGH]** `api/services/ai_privacy.py:57` — отзыв согласия не очищает
  `ai_analysis:v5:*` (комментарий гласит «intentionally untouched, no
  user_id»). Результаты, посчитанные под старое согласие, продолжают
  сервиться другим пользователям. С точки зрения 99-З — gap.
- **[AI-HIGH]** `api/routers/ai_tools.py:87-95` — кэш-ключ
  `digest_cache_key("ai_price_advice", {...})` без `user_id`. Анонимно
  шарится. То же подтверждено комментариями (~ str. 170). Учесть в политике
  отзыва.
- **[AI-MEDIUM]** `api/services/ai_audit.py:57` — `cached: bool = False` не
  сохраняется как колонка (`AIAuditLog` в `models.py:641+` её не имеет).
  Запросы по cache-hit/miss требуют парсинга `result_summary`.
- **[AI-LOW]** `api/services/ai_guards.py` — лимиты считают вызовы, не
  токены и стоимость. Анализ с 3 фото ≠ короткий negotiate, но кол-во
  «единиц» одинаковое.

### 3.3 Privacy, Audit & Guardrails

- **[AI-HIGH]** `api/services/ai_sanitize.py` — нет паттерна для физических
  адресов (улица, дом, квартира, индекс). Kufar-объявления часто содержат
  «ул. Немига 12, кв. 45» — уходит в провайдер незачищённым.
- **[AI-HIGH]** `api/services/ai_audit.py:20-24` — `audit_text_sha256` без
  соли (ещё раз; см. SEC).
- **[AI-MEDIUM]** `api/services/ai_guardrails.py:apply_ai_market_guardrails` —
  guardrail смотрит `title/description` для `contains_financing_bait`, но
  `ad_parameters` не проверяет. Селлер прячет «приманку» в параметрах →
  guardrail не срабатывает.
- **[AI-MEDIUM]** `api/services/ai_sanitize.py:_PII_PHONE_RE` — `(?<!\d)\+?\d(?:[\s\-.()]?\d){7,14}`
  ловит длинные числовые строки (старая деноминация «12 500 000»,
  VIN-номера) как [phone].
- **[AI-LOW]** `api/services/ai_task_store.py:_get_task` — читает только из
  Redis. Shadow-store (`ai_shadow_store.py:_tasks`) используется только
  для privacy/cleanup, не как fallback на чтение. Eviction Redis = таска
  потеряна, хотя в shadow-store она есть.
- **[AI-LOW]** `api/models.py:648` — `AIAuditLog.id` объявлен как одиночный PK,
  миграция партиционирования вводит `(id, created_at)`. ORM может заблудиться
  на `session.get(AIAuditLog, id)`. Привести модель к составному PK.

### 3.4 Data & Prompts

- **[AI-HIGH]** `api/services/ai_category_data.py:detect_category` — O(n×m) на
  каждый запрос: ~2000+ substring-проверок (десятки категорий × сотни
  ключевых слов в файле 72 KB). Скомпилировать единый regex или
  Aho-Corasick.
- **[AI-MEDIUM]** `api/services/ai_category_data.py:800+` — ключи с trailing
  space («бра », «bosch ») ломают match при конце строки/пунктуации.
  «Люстра/бра» не матчит «бра ».
- **[AI-MEDIUM]** `api/services/ai_category_data.py` — словарь определён
  inline в .py 72 KB. Парсится как Python source при импорте. Перевести в
  JSON, грузить лениво.
- **[AI-MEDIUM]** `api/services/ai_prompts.py:_PRICE_MARKET_SCHEMA`,
  `_CONDITION_RISKS_SCHEMA` — pseudo-JSON в виде free text. Модель
  «угадывает» схему. Поэтому `_repair_truncated_json` нужен. Дать формальный
  JSON-Schema или function-calling.
- **[AI-LOW]** `api/services/ai_prompts.py` — нет version-id у prompt'ов;
  audit-лог не корреллируется с версией промпта.
- **[AI-LOW]** `api/services/ai_category_data.py:_VALID_CONDITION_LABELS` —
  составные метки («Хорошее/Удовлетворительное») не нормализуются.

---

## 4. Database, models, migrations

### 4.1 Models & Schemas

- **[DB-CRITICAL]** `migrations/versions/20260511_0008_consent_version_constraint.py:28`
  vs `api/models.py:540` — миграция 0008 ставит `CHECK (version = '2026.1')`,
  модель допускает `IN ('2026.1','2026.2')`, дефолт модели уже `2026.2`.
  Если deploy проедет 0008 без следующей за ней 0011 — все INSERT в
  `user_consents` падают `IntegrityError`. Сворачивать 0008+0011 в один
  атомарный шаг или сразу писать `IN`.
- **[DB-MEDIUM]** `api/models.py:540` — `chk_user_consents_version_known`
  не forward-compat: каждое повышение версии политики требует миграции
  ALTER CONSTRAINT перед деплоем кода. Заменить на regex или убрать.
- **[DB-MEDIUM]** `api/models.py` — `LeadItem.version` для optimistic locking
  проверяется в `api/routers/workflow.py` через `_check_lead_version`, но
  сам `UPDATE` **не содержит** `WHERE version = :expected`. Две одновременные
  команды проходят чек, оба коммитят — последний выигрывает. Добавить
  `where(LeadItem.version == expected_version)` и проверять `rowcount == 1`,
  либо `with_for_update()`.
- **[DB-MEDIUM]** `api/models.py:509` — `TelegramNotificationDLQ.retry_count`
  без CHECK на верхнюю границу. Код-ограничитель в коллекторе можно
  обойти багом. Добавить `CheckConstraint("retry_count <= 10")`.
- **[DB-MEDIUM]** `api/models.py` — `DealExpense.amount_byn` без
  `CHECK (amount_byn > 0)`. Pydantic защищает API-вход, но прямой SQL/CLI
  пройдёт.
- **[DB-LOW]** `api/schemas.py LeadUpdate.version` — `Optional[int]` со зримой
  семантикой "обязательное", но 428 кидается уже в роутере. Сделать
  `version` обязательным или задокументировать.
- **[DB-LOW]** `api/models.py` — `User.id` BigInteger, остальные таблицы
  Integer. FK консистентны, но «потолок» 2^31-1 не задокументирован.

### 4.2 Migrations

- **[DB-MEDIUM]** `migrations/versions/20260509_0002_db_constraints_and_cleanup.py:40`
  vs `migrations/versions/20260510_0006_wave17_db_tuning.py` — оба создают
  партиционный/частичный индекс на `(user_id, consent_type) WHERE revoked_at IS NULL`
  под разными именами. После полного прогона остаются оба. Дроп старого
  в 0006.
- **[DB-MEDIUM]** `migrations/versions/20260514_0016_ai_audit_partitioning.py:100` —
  `clean_ai_audit_log` использует `DELETE` на партиционированной таблице.
  По-партиционному ретеншну `DROP/DETACH PARTITION` это O(1) и без WAL.
  DELETE остаётся как fallback на default-партицию.
- **[DB-MEDIUM]** `migrations/versions/20260514_0016_ai_audit_partitioning.py` —
  DDL партиционированной таблицы не включает `query_hash`/`result_summary_hash`,
  они добавляются в 0018. При повторном запуске 0016 на DB с уже
  существующими колонками возможна потеря данных. Документировать
  forward-only.
- **[DB-MEDIUM]** `migrations/versions/20260509_0001_db_hardening.py` — чистый
  PostgreSQL DDL (PL/pgSQL функции, ALTER COLUMN TYPE). На SQLite-тестах
  крашит. Добавить `if bind.dialect.name == 'sqlite': return`.
- **[DB-MEDIUM]** `migrations/versions/20260509_0002_db_constraints_and_cleanup.py` —
  то же самое, отсутствует диалект-guard.
- **[DB-MEDIUM]** `scripts/partition_snapshots.sql:18` — у партиционированной
  `query_snapshots` нет PRIMARY KEY. Должен быть `PRIMARY KEY (id, snapshot_at)`
  (partition key обязан входить в PK).
- **[DB-MEDIUM]** `scripts/partition_snapshots.sql:30` — нет уникального
  индекса, эквивалентного `uq_query_snapshot_bucket("query","snapshot_at")` —
  возможны дубликаты после партицирования.
- **[DB-LOW]** `scripts/partition_snapshots.sql` — нет DEFAULT-партиции;
  значение `snapshot_at` вне диапазона валит INSERT.
- **[DB-LOW]** `scripts/partition_snapshots.sql:44` — `INSERT ... SELECT *`
  без явного списка колонок. Добавление/перестановка ломает данные.
- **[DB-LOW]** `migrations/versions/20260427_0002_user_consents.py:8` —
  `down_revision = "20260428_0002"`. ID назван «0427», но depends_on более
  поздний. Корректно для Alembic, но путает.
- **[DB-LOW]** `migrations/versions/20260514_0016_ai_audit_partitioning.py` —
  `ACCESS EXCLUSIVE LOCK` на `ai_audit_log` блокирует AI-эндпоинты во время
  переноса. Указать в RUNBOOK + рассмотреть pg_partman.

### 4.3 Indexes & Constraints

- **[DB-HIGH]** `api/models.py LeadItemPriceSnapshot` — нет ретенции. Все
  остальные большие таблицы (`query_snapshots` 90 дн, `tracker_events` 30 дн,
  `ai_audit_log` 365 дн, `query_listing_states` 90 дн) чистятся
  scheduler-ом, эта — растёт неограниченно. Docstring модели обещает
  rolling window — не реализован. Добавить cleanup (90 дней или последние
  100 точек на `lead_item_id`).
- **[DB-MEDIUM]** `api/models.py QuerySnapshot` — два независимых индекса
  по `query` и `snapshot_at`. Реальный паттерн —
  `WHERE query=:q ORDER BY snapshot_at DESC LIMIT N`. Композитный
  `(query, snapshot_at DESC)` уберёт sort.
- **[DB-LOW]** `api/models.py TrackerEvent` — `__table_args__` содержит
  `Index("idx_tracker_events_created", "created_at")`, который дропнут в
  миграции 20260509_0001. Schema-drift; `alembic check` диагностирует.
- **[DB-LOW]** `api/models.py LeadReminder` — индексы по `lead_id`/`user_id`/
  `remind_at` + композитный `(remind_at, sent)`. Запрос
  `WHERE sent=false AND remind_at<=now()` лучше обслуживается частичным
  `(remind_at) WHERE sent=false`.
- **[DB-LOW]** `api/models.py DealExpense` — `idx_deal_expenses_user`
  нужен только под CASCADE-delete. OK.
- **[DB-LOW]** `api/models.py` — `idx_trackers_user_active_partial`
  (`WHERE active=True`) и `idx_trackers_user_active` (полный) дублируют
  hot-path. Дропнуть полный.
- **[DB-LOW]** `api/models.py SavedSearch` — индекс `idx_saved_searches_active`
  на одиночном bool. Postgres его не использует. Дропнуть или сделать
  частичным.
- **[DB-LOW]** `api/models.py Contact` — нет `updated_at`. Если когда-нибудь
  включится редактирование контактов — потеряем audit-trail.

---

## 5. Scheduler & Bot

### 5.1 Scheduler / Collector

- **[SCH-HIGH]** `scheduler/collector.py:1283-1290` — `AsyncIOScheduler`
  использует дефолтный `MemoryJobStore`. После рестарта весь job-state
  (next_run_time, misfire grace) теряется. С `replace_existing=True` и
  быстрым рестарт-loop возможны overlapping запуски.
- **[SCH-HIGH]** `scheduler/collector.py:700-710` — `_TRACKER_QUERY_ERRORS`
  ловит сбой Kufar и просто пропускает группу. Нет circuit-breaker:
  при недоступности Kufar скрипт долбит API на каждой группе подряд (до 2k
  итераций). Добавить early-exit / экспоненциальный backoff.
- **[SCH-HIGH]** `scheduler/collector.py:529` — discount-alert считает
  `discount_pct = abs(compute_price_vs_reference(...))`. `abs()` объединяет
  «дешевле медианы» и «дороже медианы». Объявление, которое на 30% дороже,
  фейково триггерит «скидка ≥ 30%». Снять `abs()` и использовать только
  отрицательное значение.
- **[SCH-MEDIUM]** `scheduler/collector.py:380-395` — `check_trackers`
  открывает один `AsyncSession` на весь цикл (минуты). Identity map копит
  устаревшие объекты после savepoint-rollback.
- **[SCH-MEDIUM]** `scheduler/collector.py:700` — `_TRACKER_QUERY_ERRORS`
  включает `SQLAlchemyError`. Уникальные нарушения молча проглатывают
  всю группу, а не строку.
- **[SCH-MEDIUM]** `scheduler/collector.py:750-760` — после `commit()`
  отправка нотификаций. SIGKILL между ними → `last_checked_at` уехал, юзер
  без уведомления; следующая итерация уже не пошлёт (в `seen_by_tracker`).
  Нужен outbox-паттерн или DLQ-семейство для «не отправлено вообще».
- **[SCH-MEDIUM]** `scheduler/collector.py:1283` — `misfire_grace_time` не
  задан → дефолт 1 c. Долгий цикл блокирует loop, следующий тик помечается
  misfire и пропускается. Поставить `misfire_grace_time=60, coalesce=True`.
- **[SCH-MEDIUM]** `scheduler/collector.py:1440-1480` — DLQ retry pump делает
  по `UPDATE` на каждую строку (50 round-trip-ов за тик). Bulk update.
- **[SCH-MEDIUM]** `scheduler/collector.py` — поведение при «Kufar вернул 0
  ответов из-за outage» — `sync_query_listing_states` пометит все listings
  inactive, на следующий цикл false-positive «всё пропало». Добавить
  чёткий guard `if upstream_failed: skip state-sync`.
- **[SCH-LOW]** `scheduler/collector.py:1395` — `_dlq_next_retry_after`
  для retry_count=0 даёт 1 минуту, что меньше pump-интервала: первая
  строка моментально eligible.
- **[SCH-LOW]** `scheduler/collector.py:1230-1250` — для `outcome=="blocked"`
  reminder остаётся `sent=False`. Когда юзер разблокирует бота, может
  прилететь устаревший reminder через дни/недели. Либо помечать sent с
  отдельным выходом «delivered_via=blocked», либо чистить через TTL.
- **[SCH-LOW]** `scheduler/collector.py:1290` — cron `hour=3` в
  `Europe/Minsk`. На DST 3:00 либо отсутствует, либо повторяется. APScheduler
  это терпит, но `jitter` явно стоит выставить.
- **[SCH-LOW]** `scheduler/collector.py:1530` — в DEBUG aiogram/httpx могут
  залогать Authorization-заголовок с BOT_TOKEN. Установить redacting filter
  на корневой logger.
- **[SCH-LOW]** `scheduler/collector.py:600-750` — повторяющиеся блоки
  по `new_listings[:3]` и `price_drops[:3]` (~40 строк каждый). Свернуть
  в helper.

### 5.2 Scheduler / Messages

- **[SCH-LOW]** `scheduler/messages.py:91` — `f"(-{math.ceil(float(delta))} BYN)"`
  фильтр `delta >= 0.5` + `math.ceil` превращает 0.5 → 1, отображая
  «-1 BYN» вместо реального -0.5.
- **[SCH-LOW]** `scheduler/messages.py:47-48` — повторение `event.parameters.get(...)`
  без хелпера. Минор.

### 5.3 Bot / Core

- **[BOT-MEDIUM]** `bot/main.py:72` — `dispatcher.start_polling(bot)` без
  custom error-handler на dispatcher. При длительном Telegram-outage —
  тихие ретраи без алертов и метрик.
- **[BOT-MEDIUM]** `bot/main.py:30-33` — `build_dispatcher()` не регистрирует
  `dispatcher.errors.register(...)`. Любое исключение в handler не доходит
  до пользователя осмысленным сообщением.
- **[BOT-MEDIUM]** `bot/api_client.py:120-130` — `_api_get/post` ловят
  широкий `httpx.HTTPError` и возвращают `None`. 401, 503, network-error —
  всё одинаково. Нет retry на transient.
- **[BOT-MEDIUM]** `bot/api_client.py:93-95` — `build_api_headers` кладёт
  `INTERNAL_SERVICE_TOKEN` в dict; нет header-redaction в httpx logger.
- **[BOT-LOW]** `bot/database.py:20-25` — ленивая инициализация `asyncio.Lock`
  без guard. На CPython «случайно» работает, паттерн фрагильный.
- **[BOT-LOW]** `bot/api_client.py:108,127` — `_api_get/_api_post` под
  underscore (private), но импортируются в `handlers/*`. Сделать публичными
  или вынести в Service-класс.

### 5.4 Bot / Handlers

- **[BOT-MEDIUM]** `bot/handlers/callbacks.py:56-60` — `_resolve_listing_context`
  ищет `TrackerEvent` по `(telegram_user_id, ad_id)`. ad_id в callback_data
  не привязан HMAC-ом; перенаправить алерт другому пользователю → у него
  не сработает (UX-gap, не security).
- **[BOT-MEDIUM]** `bot/handlers/callbacks.py:62-65` — на crafted
  `callback.data="add_lead"` ловит `IndexError`, отдаёт «Неверный ID
  объявления» — вводит в заблуждение про повреждённый payload.
- **[BOT-MEDIUM]** `bot/handlers/analytics.py:67-90` — `/deals`, `/profit`
  собирают HTML без `html.escape` (см. SEC §1.9).
- **[BOT-LOW]** `bot/handlers/analytics.py:105-110` — `/stats` не учитывает
  `@botname`-suffix; работает «по совпадению».
- **[BOT-LOW]** `bot/handlers/start.py` — нет rate-limit / dedup на /start.
  В текущей реализации без БД-записей это не критично.
- **[BOT-LOW]** `bot/handlers/callbacks.py` — нулевое логирование. Дебажить
  user-issued баги тяжело.

---

## 6. Frontend (JavaScript)

### 6.1 Core (app.js, app_core, app_core_dom, dom_helpers)

- **[FE-MEDIUM]** `frontend/js/app.js:254` vs `frontend/js/app_core.js:229` —
  `themeChanged` подписывается дважды (в DOMContentLoaded и в
  `initTelegramTheme()`). На каждое изменение темы CSS-переменные
  применяются дважды, возможен flicker.
- **[FE-MEDIUM]** `frontend/js/app.js:105` — `MutationObserver` на `document.body`
  с `subtree:true, childList:true, attributes:true`. Срабатывает на любые
  DOM-изменения (виртуальный список, скроллинг карточек). Дебаунсить
  через `requestAnimationFrame` или сузить subtree.
- **[FE-MEDIUM]** `frontend/js/virtual_list.js:~195` — `window.addEventListener("resize",…)`
  добавляется на каждый VL-инстанс, удаляется только в `destroy()`. Если
  контейнер пере-рендерится без `destroy()` старого VL — листенер течёт.
- **[FE-MEDIUM]** `frontend/js/dom_helpers.js:1102` — единственное оставшееся
  `innerHTML = svgEl.outerHTML` после allowlist-санитизации. Безопасно при
  текущем allowlist, но фрагильно. Заменить на `appendChild(document.importNode(svgEl, true))`.
- **[FE-MEDIUM]** `frontend/js/api_image_proxy.js:~20` — object URL'ы не
  ревокаются до явного `clearProxyImageObjectUrls()`. Длинная сессия с
  просмотром множества деталей объявлений → memory leak. LRU-eviction
  (например, 50 последних).
- **[FE-LOW]** `frontend/js/config.js` — `window.APP_CONFIG` определён, но
  ни одним модулем не читается; реальные константы захардкожены
  (`CACHE_TTL`, `PAGE_SIZE`, `INFLIGHT_GUARD_MS`). Файл мёртв; либо
  подключить, либо удалить (см. также §11 Consistency).

### 6.2 API modules

- **[FE-MEDIUM]** `frontend/js/api_listings.js:260` — `loadListings()` без
  `AbortController`. Быстрые клики по сортировке стакают inflight-запросы;
  `requestId`-guard не отменяет сетевую часть.
- **[FE-MEDIUM]** `frontend/js/api_leads.js:~280` и `frontend/js/api_watchlist.js:~200` —
  `openLeadDetail` и `openWatchlistDetail` идентичны до уровня URL/error.
  Вынести `_openItemDetail(item, fromWatchlist)`.
- **[FE-MEDIUM]** `frontend/js/api_trackers.js:~95` — `refreshTrackerEvents()`
  catch-empty: при сетевом сбое тихо игнорируем 30-c интервал. После N
  фейлов показывать «Обновление не удалось» / ставить интервал на паузу.
- **[FE-LOW]** `frontend/js/app_core.js:~270` — `formatPrice`: нет
  `Intl.NumberFormat` группировки; «1234567 BYN» вместо «1 234 567 BYN».

### 6.3 Render modules

- **[FE-MEDIUM]** `frontend/js/render_charts.js:~85` — `renderChart()` через
  `ensureChartLib().then(...)` может выполниться, когда панель уже закрыта
  (canvas невидим). Guard `if (!state.panels.distribution) return;` в
  колбэке.
- **[FE-MEDIUM]** `frontend/js/render_card_builders.js:~170` — `aria-label`
  у listing-card кладётся целиком из `item.subject||item.title`. Длинный
  заголовок (200+ chars) озвучивается полностью. Truncate до 80.
- **[FE-MEDIUM]** `frontend/js/render_cards.js:~344` — `IntersectionObserver`
  для пагинационного sentinel создаётся на каждом `renderListings()`.
  `_resetContainer` (str. 225) очищает DOM до того, как loop соберёт старые
  sentinels — orphan-IO остаются. Сохранять observer на самом контейнере
  и `disconnect()` в `_resetContainer`.
- **[FE-LOW]** `frontend/js/render_cards.js:~208` — `_hasActiveListingFilters()`
  дублирует логику из `api_listings.js:~130`. Прокинуть через `context`.

### 6.4 Service Worker & Offline

- *Положительно:* `sw.js` корректно делает `skipWaiting()` + `clients.claim()`,
  использует cache-first для статики и stale-while-revalidate для read-only
  API; `BYPASS_PATHS` исключает `/leads`, `/watchlist`, `/trackers`.
- **[FE-MEDIUM]** `frontend/sw.js` — комментарий обещает
  «v6 added staleWhileRevalidate max-age check», но фактическое значение
  staleness не вычисляется по `Date`-заголовку. На длинном офлайне юзер
  видит старые данные без визуального таймстампа. Добавить в API-кэше
  поле «получено в …» в UI.

### 6.5 Build & Bundle

- *Положительно:* `frontend/js/app_bundle.js` (165 451 B) синхронизирован с
  источниками; SHA-проверки в `tests/test_app_js_syntax.py` прошли;
  `?v=20260516-3237e01` единое.
- **[FE-LOW]** `scripts/minify_js.py` использует `rjsmin` (whitespace +
  comments). Tree-shaking/dead-code elimination отсутствуют. См. также
  Performance §8.4.
- **[FE-LOW]** Source map не генерируется — debugging минифицированного
  бандла в проде требует unminified исходников из репозитория.

### 6.6 Lazy stubs & module wiring

- *Положительно:* стабы `_lazy_*_stub.js` корректно ссылаются на реальные
  файлы; никаких dead reference'ов.
- **[FE-LOW]** `frontend/js/_lazy_deals_stub.js:~60` — `_delegate` async,
  колбэки в `app_actions.js:~140` используют `void`/`await`. Поведение
  корректное, но любой будущий синхронный consumer словит unawaited Promise.

---

## 7. Frontend / Дизайн и CSS

### 7.1 Tokens & Theming

- **[DESIGN-HIGH]** `_applyTelegramTheme()` (`app_bundle.js:511`) пишет
  `--tg-theme-bg-color` и др. в `:root`, но **ни одно правило в CSS не
  читает `var(--tg-theme-*)`**. Telegram-палитра полностью игнорируется,
  приложение всегда показывает свою тему. Привязать минимум фон/текст к
  `var(--tg-theme-*, fallback)`.
- **[DESIGN-MEDIUM]** Light/dark переключение базируется только на
  `data-theme`. На событие Telegram `themeChanged` хэндлер short-circuit-ит
  при сохранённом `userPreference` → mismatch chrome ↔ контент.
- **[DESIGN-MEDIUM]** `--text-muted` ≡ `--text-secondary` (одинаковое
  значение в обеих темах). Удалить один.
- **[DESIGN-MEDIUM]** `--white = #e8e8e8` в dark-теме — не белый, но
  используется как `color` в `.wl-btn--accent` поверх синего.
- **[DESIGN-MEDIUM]** Z-index из комментария tokens.css:1-9 нарушается
  минимум в 5 местах: `ai-modal-overlay=600`, `lp-menu-overlay=1050`,
  `#offline-banner=10000`, `ptr-indicator=1200`, и др.
- **[DESIGN-MEDIUM]** 16 shadow-токенов; пары `--shadow-panel-soft` /
  `--shadow-loading-panel` практически идентичны.
- **[DESIGN-MEDIUM]** `color-mix(in srgb, …)` используется массово без
  `@supports` fallback. На Safari < 16.2 / старом Chromium-WebView
  Telegram (Android low-end) это просто пропускается.

### 7.2 Layout & Responsive

- **[DESIGN-MEDIUM]** `.app { max-width: 480px }` — нет breakpoint выше
  640px. iPad/landscape видит узкую колонку. Если требуется adaptivity —
  добавить.
- **[DESIGN-MEDIUM]** Breakpoints стартуют с 340px. Реальный 320px
  (iPhone 5/SE1) не покрыт; в `.view-nav` 2-колоночная сетка на 320px
  оставляет ≤ 148px на таб → label обрежется агрессивно.
- **[DESIGN-MEDIUM]** `body.modal-open { position: fixed; width: 100% }` без
  `top: -scrollY`. На iOS — известная скачка контента при открытии модалки.
- **[DESIGN-MEDIUM]** Нет `visualViewport` / keyboard-avoidance. Поля внутри
  `.detail-sheet` (max-height 92vh) перекрываются клавиатурой на iOS.

### 7.3 Accessibility

- *Положительно:* модалки имеют `role="dialog"`, `aria-modal="true"`,
  `aria-labelledby`, focus-trap, ESC-close, scroll-lock. `:focus-visible`
  настроен. Skip-link, `.sr-only`, `role="status"` для toast'ов — есть.
- **[DESIGN-MEDIUM]** `.primary-btn:disabled { opacity: 0.5 }` поверх
  `--accent` (#3b82f6) — контраст текста на dark fallback ≈ 2.2:1, **fail
  AA**.
- **[DESIGN-MEDIUM]** `.listing-badge.fresh-hot` пульсирует opacity → 0.65,
  кратковременно опускает контраст ниже 3:1.
- **[DESIGN-MEDIUM]** `.ai-action-btn` 36×36 px, `.expense-delete-btn` 36×36 —
  ниже Apple HIG 44 px и Telegram 48 dp.
- **[DESIGN-MEDIUM]** `.detail-close` содержит только символ «×»,
  `aria-label` не задан → screen reader произнёс бы «times».
- **[DESIGN-MEDIUM]** `.chart-box` обёртка не имеет `aria-label`, описывающего
  содержимое графика.

### 7.4 Components (modals / cards / pipeline)

- *Положительно:* fix из FIX_PIPELINE_OVERLAP.md действительно приземлился
  (vertical-stack 10 px / 24 px / breakpoint 380 px).
- **[DESIGN-HIGH]** **Pipeline status enum mismatch:** JS `ACTIVE_LEAD_STATUSES`
  (`app_actions.js:350`) содержит `"researching"`, в CSS такой класс
  отсутствует — есть `.lead-status-badge.reviewing`. Объявления в
  «researching» рисуются без визуального стиля.
- **[DESIGN-MEDIUM]** Два конкурирующих skeleton-анимации (`skeleton-shimmer`
  в tokens.css и `shimmer` в states.css). Карточки используют sweep,
  overview — pulse. Унифицировать.
- **[DESIGN-MEDIUM]** Типографическая шкала только в px (10–28 px) +
  `html { font-size: var(--text-md) }` (15 px) перебивает user-prefs.
- **[DESIGN-LOW]** Нет `print` стилей.

### 7.5 CSS Hygiene

- **[DESIGN-MEDIUM]** `style.css` 156 KB одной строкой — конкатенация
  `parts/*.css`. Проверить, что в HTML грузится **ровно один** из них —
  иначе все правила задвоены.
- **[DESIGN-MEDIUM]** `[data-theme="light"]` встречается 50+ раз — каскад
  фрагилен по порядку deklaracji.
- **[DESIGN-MEDIUM]** `backdrop-filter: blur(...)` на 4+ элементах
  (header, AI header, toast, LA tabs) — дорогая композиция в Telegram
  WebView на low-end Android.
- **[DESIGN-MEDIUM]** `will-change: transform` на `.detail-sheet` на постоянной
  основе — навсегда промоутит элемент в композитный слой.
- **[DESIGN-LOW]** Дубль `@keyframes spin` в ai.css и states.css.

### 7.6 Dead CSS

- См. §11 Consistency: `.render-error`, `.quick-chip` (множество вхождений
  в tokens/brand/states.css), `.listing-card` и др. — определены, но не
  используются.

---

## 8. Performance

### 8.1 DB & ORM

- **[PERF-HIGH]** Никакого `selectinload`/`joinedload` в API-роутерах.
  `workflow.py`, `export.py` подгружают `LeadItem` и далее обращаются к
  relationships → N+1. Только `scheduler/collector.py:621` использует
  `joinedload(Tracker.user)`.
- **[PERF-HIGH]** `api/services/aggregator.py:310-380 precompute_cluster_stats`
  — O(n²), вложенный цикл по всем парам ads, без `await`. На 1500 ads ≈
  2.25 M итераций — блок event loop сотни мс. Вызывается синхронно из
  `api/routers/listings.py:325`.
- **[PERF-MEDIUM]** `api/services/history_service.py:130 load_listing_states()` —
  до 5000 ORM-объектов на тик scheduler-а на запрос. Заменить на
  `select(...)` возвращающий tuples, и dict-comprehension.
- **[PERF-MEDIUM]** `api/services/workflow_store.py:62-78 load_last_snapshot_prices` —
  коррелированный subquery `func.max(id) GROUP BY lead_item_id` без
  covering index. Lateral/window function + индекс.
- **[PERF-MEDIUM]** `api/routers/listings.py:420` — `payload.model_dump()`
  для кэширования, потом FastAPI ещё раз сериализует JSON. Двойная
  сериализация на hot-path. Кэшировать байт-строкой и отдавать
  `Response(content=..., media_type="application/json")`.

### 8.2 Caching & Singleflight

- **[PERF-HIGH]** Нет negative-caching. `query_pipeline.py:218` кэширует
  не-None ответ, но empty list тоже может быть валиден; singleflight key
  очищается сразу. На «всегда-пустых» запросах каждый раз поход к Kufar.
  Завести negative-marker с коротким TTL (60 c).
- **[PERF-MEDIUM]** `api/services/cache.py:47 MemoryCache` — единственный
  `asyncio.Lock` на все операции. Шардирование (16 локов по hash-prefix).
- **[PERF-MEDIUM]** `api/metrics.py:120-130 observe_http_request_with_backend()` —
  3 отдельные Redis-команды на каждый HTTP-запрос. Свернуть в `pipeline()`.
- **[PERF-MEDIUM]** `api/routers/listings.py:420` — кэш-ключ включает
  `limit/offset`. Один и тот же raw dataset кэшируется отдельной записью
  на каждую пару `(offset, limit)`. Кэшировать «сырой» результат, postprocess
  в коде.
- **[PERF-LOW]** Cache TTL фиксирован 300 c. Адаптивный TTL по частоте
  запросов.

### 8.3 Async & Event Loop

- **[PERF-HIGH]** `precompute_cluster_stats` (см. выше) — CPU-bound в async
  обработчике.
- **[PERF-MEDIUM]** `api/services/ai_marketplace.py:1` — load JSON на
  module-import. Блок event loop при первом импорте в worker.
- **[PERF-MEDIUM]** `api/services/listing_mapper.py build_listing_item()` —
  на 50 ads ~200 синхронных вызовов scoring/risk/flip без `await`.
- **[PERF-LOW]** Scheduler-нотификации идут последовательно
  (`_dispatch_tracker_notifications`); bounded `asyncio.gather(5)` ускорит.

### 8.4 Outbound HTTP & Scraping

- **[PERF-MEDIUM]** `scheduler/collector.py:700` — фиксированный `size=50`
  без пагинации. Популярный запрос с 200+ новыми объявлениями получит
  только первую страницу.
- **[PERF-LOW]** `api/services/query_pipeline.py:260` — `client_factory`
  путь создаёт новый `KufarClient` (и httpx) на вызов. Тест-only, но
  стоит документировать.

### 8.5 Frontend

- **[PERF-HIGH]** Полный JS на холодной загрузке: `app_bundle.js` (165 KB)
  + 30 модулей ≈ 700 KB не-сжато. Без tree-shaking. Все lazy-модули
  загружаются при первом обращении к табу.
- **[PERF-HIGH]** `nginx/default.conf` — `gzip on`, **brotli не настроен**.
  На JS/CSS Brotli даёт +15–25% к gzip. Внести `ngx_brotli` или
  preсжатие в build-step (`*.br` файлы) и `brotli_static on`.
- **[PERF-MEDIUM]** Static assets: `expires 365d` + `immutable`, но
  filename НЕ содержит content-hash. `bump_static_version.sh` проставляет
  `?v=...` вручную; забыли запустить → пользователи висят на старом году.
- **[PERF-MEDIUM]** `frontend/index.html` 81 KB — крупный shell для SPA.
  Inline-данные/шаблоны можно вынести.
- **[PERF-MEDIUM]** Service worker stale-while-revalidate без max-age
  badge — стейл может быть многодневным.

### 8.6 Infra (nginx / Docker)

- **[PERF-HIGH]** `Dockerfile` — однослойный (`FROM python:3.12-slim` +
  `uv sync --no-dev` + `COPY . .` + `chown -R`). Размер образа ~500 MB при
  возможных ~200 MB через multistage.
- **[PERF-HIGH]** `api/metrics.py` — нет histogram-buckets. `_sum`/`_count`
  не дают p95/p99. Для SLO-мониторинга бесполезно. Перейти на
  `Histogram` (prometheus-client) с разумными buckets.
- **[PERF-MEDIUM]** `WORKERS=4` × `db_pool_size=5` + `db_max_overflow=10` =
  60 connections. `pool_timeout=30` → запросы ждут до 30 c. На burst-нагрузке
  будут 504/timeout.
- **[PERF-MEDIUM]** `nginx` без `proxy_cache` для read-only API. 5–10 c
  кэш для `/price-stats`, `/segments`, `/geography` снимет львиную долю
  Python-времени.
- **[PERF-LOW]** `limit_req` `burst=60 nodelay`: один поиск стоит 6
  endpoint-ов, 10 одновременных юзеров уже дренят burst.
- **[PERF-LOW]** `Dockerfile` `COPY . .` без явных `tests/`, `docs/`,
  `.planning/` в `.dockerignore`. Проверить.

---

## 9. Infrastructure / DevOps

### 9.1 Docker

- **[INF-MEDIUM]** `Dockerfile:1-15` — нет multistage. `COPY . .` тянет
  `tests/`, `migrations/`, `scripts/`, `docs/` в финальный образ. Сборка
  колёс происходит на runtime-image. Reduce ~40% размера через
  `builder → runtime` split.
- **[INF-LOW]** `Dockerfile:7` — `pip install --no-cache-dir uv` без пина.
  Breaking-релиз uv тихо ломает CI/build. Закрепить
  `pip install --no-cache-dir uv==0.5.x`.
- **[INF-LOW]** `Dockerfile:14` — `COPY . .` дублирует `chown -R appuser`.
  Используйте `COPY --chown=appuser:appuser . .` или multistage.
- **[INF-LOW]** `.dockerignore:38-40` — исключаются `docker-compose.yml`,
  но `docker-compose.override.yml` явно не назван.
- **[INF-LOW]** `Dockerfile.frontend:42` — `nginx -t` smoke-test есть. OK.

### 9.2 Compose

- **[INF-MEDIUM]** `docker-compose.yml` (postgres) — `5433:5432` биндит
  `0.0.0.0`. См. SEC §1.7.
- **[INF-MEDIUM]** `docker-compose.yml api` — `REDIS_URL` с паролем виден в
  `docker inspect`. См. SEC §1.7.
- **[INF-LOW]** `docker-compose.yml frontend healthcheck` — `curl -f
  http://localhost:80`, но `nginx:alpine` не содержит curl, и Dockerfile
  его не ставит. Healthcheck **всегда падает**. Заменить на
  `wget --spider -q http://localhost:80` или поставить curl.
- **[INF-LOW]** `docker-compose.yml redis` — пароль в `command:` через
  shell-expansion (`$$REDIS_PASSWORD`) виден в `docker inspect`.

### 9.3 nginx

- **[INF-MEDIUM]** `nginx/default.conf` — нет `server_tokens off;`. (см. SEC)
- **[INF-MEDIUM]** `nginx/default.conf` — нет custom-страниц 502/503/504.
  Юзер видит сырой nginx-error.
- **[INF-LOW]** `nginx/default.conf` — нет HTTP/2 на upstream-стороне
  (`listen 80 http2;` или `http2 on;`).
- **[INF-LOW]** `nginx/default.conf` — нет brotli-модуля (см. PERF §8.5).
- **[INF-LOW]** `nginx/default.conf /api/v1/health` — health-эндпоинт
  попадает под общий rate-limit `/api/`.
- **[INF-LOW]** `nginx/default.conf` — статический regex-location перебивает
  security-заголовки server-блока. Static assets не получают X-Content-Type-Options/CSP.

### 9.4 CI

- **[INF-HIGH→MEDIUM]** `.github/workflows/ci.yml` — все actions по тегу
  `@v3/@v4/@v5`. Закрепить SHA. (См. SEC.)
- **[INF-MEDIUM]** `.github/workflows/ci.yml:117` — `codecov-action` без
  `token`.
- **[INF-LOW]** Нет matrix по версиям Python. `requires-python=">=3.12"`,
  но фактически тестируется одна 3.12. Добавить 3.13 в matrix.
- **[INF-LOW]** `.github/workflows/ci.yml:5` — триггер по ветке `bad-app`.
  Если ветка временная — убрать.

### 9.5 Local scripts

- **[INF-MEDIUM]** `status-local.sh:39,45` — fallback на `pgrep -f 'python -m bot.main'`,
  совпадёт с любыми процессами в системе. AGENTS.md помечает `pkill -f`
  как deferred, но `pgrep -f` всё равно лучше заменить на чтение PID-файла
  из `.run/`.
- **[INF-LOW]** `start-local.sh` — не делает `set -a; source .env; set +a`
  перед запуском процессов. Полагается на python-dotenv в каждом сервисе.
- **[INF-LOW]** `proxy-server.mjs` — нет защиты от path traversal (см. SEC §1.4),
  нет `NODE_ENV` guard.

### 9.6 Build & Static

- **[INF-MEDIUM]** `scripts/partition_snapshots.sql` — schema drift: нет PK
  и UNIQUE, см. DB §4.2.
- **[INF-LOW]** `scripts/build_frontend_bundle.sh` — порядок
  детерминированный (hardcoded array). `node --check` после минификации.
- **[INF-LOW]** `scripts/bump_static_version.sh` — идемпотентен, покрывает
  index.html / sw.js / js/*.js. CSS в index.html cover-ит. Имеет смысл
  добавить sanity-check что новый tag отличается от предыдущего.
- **[INF-LOW]** `scripts/backup.sh` — локальный, без off-host hint в
  коде. INF-H1 deferred, но добавить хотя бы `# WARNING: off-host copy not
  configured` коммент.

### 9.7 Project metadata

- **[INF-MEDIUM]** `pyproject.toml` — все зависимости с `>=`, без upper-bounds.
  `uv.lock` спасает, но `uv sync` без `--frozen` может улететь в
  breaking-релиз.
- **[INF-MEDIUM]** `pyproject.toml:56-57` — `rjsmin`/`rcssmin` в
  `[project.optional-dependencies] dev`, но нужны на build-time для
  `scripts/minify_js.py`. Если в будущем prod-образ ребилдит бандл —
  упадёт.
- **[INF-LOW]** `pyproject.toml:62-64` — двойное объявление dev-deps
  (`[project.optional-dependencies] dev` + `[dependency-groups] dev`)
  с конфликтом версий pytest (`>=8.3.0` vs `>=9.0.3`). Удалить один.
- **[INF-LOW]** `pyproject.toml` — нет `license` и `authors`. README
  декларирует «Private — not yet open-sourced». Добавить
  `license = "LicenseRef-Proprietary"`.
- **[INF-LOW]** `.env.example` — безопасные дефолты (DEBUG/AUTH_BYPASS
  закомментированы). OK.

---

## 10. Tests / QA

### 10.1 Coverage gaps

- **[QA-MEDIUM]** `tests/test_image_proxy.py` — нет тестов на запросы к
  internal/private IP. Хотя base-URL хардкоден, DNS-rebinding/CDN-CNAME
  гипотетически указуем во внутреннюю сеть. Добавить unit-тест на
  IP-валидатор (даже если он сейчас неявный — сделать явным и протестить).
- **[QA-MEDIUM]** Нет теста на restart/resumption scheduler-а. После
  крэша pending tracker checks или DLQ-retry должны корректно восстанавливаться
  на следующем старте.
- **[QA-MEDIUM]** `tests/test_currency_service.py` — всего 3 ассерта; нет
  edge-cases (`1/3`, sub-cent, накопление округлений на портфеле).
- **[QA-MEDIUM]** `tests/test_backup_script.py` валидирует только формирование
  dump-файла; restore-drill не покрыт. (BACKUP_RUNBOOK его обещает.)
- **[QA-MEDIUM]** `tests/test_migrations.py` — 3 статических чека +
  round-trip под `skipif(not _has_postgres_test_db())`. Локально/в CI без
  Postgres — миграции *не выполняются*. Добавить on-Postgres-job в CI как
  required.
- **[QA-MEDIUM]** `tests/test_ai_services_unit.py` — успешный путь
  `_log_ai_audit` не проверяется (только path-fail). Добавить ассерты на
  `query_hash`, `result_summary_hash`, `latency_ms`.
- **[QA-MEDIUM]** DLQ — `tests/test_scheduler_collector.py` покрывает
  sent/retry/blocked, но нет теста, что при `retry_count == _DLQ_MAX_RETRIES`
  cleanup действительно удаляет строку.
- **[QA-MEDIUM]** Consent revocation — нет теста, что in-flight AI-запрос
  отклоняется после revoke без cache-race.
- **[QA-MEDIUM]** IDOR-matrix не покрывает AI-результаты, price-history,
  analytics/export.
- **[QA-MEDIUM]** Нет negative-теста на 401/403 для `/api/v1/img/...`.

### 10.2 Flakiness & Hygiene

- **[QA-LOW]** Жёсткие `asyncio.sleep(0.02–0.05)` в 7 тестах
  (`test_listings.py`, `test_parallel_kufar.py`, `test_query_pipeline.py`,
  `test_main.py`, `test_health.py`, `test_ai_services_unit.py`,
  `test_ai_error_paths.py`). На медленных CI-хостах будет flake. Использовать
  event/condition-based ожидания.
- **[QA-LOW]** 180 избыточных `@pytest.mark.asyncio` декораторов при
  `asyncio_mode="auto"`. Будут DeprecationWarning в pytest-asyncio ≥ 0.24.
- **[QA-LOW]** Нет `markers` в `pyproject.toml` → `pytest --strict-markers`
  упадёт.
- **[QA-LOW]** `tests/test_app_js_syntax.py` — 13 `subprocess.run(["node"], ...)`
  без guard `skipif(shutil.which("node") is None)`. Без Node CI-job
  падает, а не skip-ится.
- **[QA-LOW]** Frontend structure tests читают живую FS; staleness
  bundle/CSS — это intended (защита от пропущенного rebuild), но в CI
  build-step должен предшествовать тесту.

### 10.3 Fixtures & DB

- **[QA-MEDIUM]** `tests/conftest.py` патчит `User.id`/`AIAuditLog.id` с
  BigInteger на Integer для SQLite. Postgres-фичи (JSONB, partial indexes,
  partitioning) локально не покрыты.
- **[QA-MEDIUM]** `create_test_tables` использует `create_all/drop_all`
  per-test. Медленно; вдобавок несколько engine-инстансов (TestClient
  создаёт свои). Возможны «table not found» гонки при изменении фикстур.
- **[QA-MEDIUM]** `_flush_test_redis` синхронный, 0.2 c × 2 timeout,
  тихо тратит время если Redis не доступен; на 874 тестах = ~6 минут пустых.
- **[QA-LOW]** `DEFAULT_FAKE_ADS` — module-level mutable list. Никто
  сейчас не мутирует, но фрагильно.
- **[QA-LOW]** `make_user` — глобальный `itertools.count(1)`. Не безопасно
  для `pytest-xdist`.
- **[QA-LOW]** `_CSRFTestClient` monkey-patches `fastapi.testclient.TestClient`
  глобально → ordering-dep на conftest.

### 10.4 Frontend & static-asset checks

- *Положительно:* `test_app_js_syntax.py` не «syntax check», а реальные
  runtime-тесты XSS-сanitizer'ов, image-proxy, virtual-list
  contract'ов. Bundle-staleness через SHA256 — sound.
- **[QA-LOW]** `_frontend_bundle_modules()` парсит `build_frontend_bundle.sh`
  как bash-array. Если формат сменится (на JSON-manifest), парсер тихо
  возвращает `[]` и SHA256 проходит вакуумно.

### 10.5 Collection results

- ✅ `pytest --collect-only -q` собирает 874 теста за ~2.8 c, 0 ошибок
  импорта, 0 collection-warning. Все 55 файлов парсятся.
- ✅ Ровно один `skipif` (Postgres round-trip).
- ✅ 0 `xfail` — суит ожидает зелёного 100%.

---

## 11. Бизнес-логика

### 11.1 Pricing & Stats

- **[LOGIC-HIGH]** `normalize_price_byn(ad.get("price_byn"))` вызывается
  без `ad`-аргумента в:
  - `api/services/risk_detector.py:83`,
  - `api/services/market_signals.py:86`,
  - `api/services/deal_workflow.py:217`,
  - `api/services/reseller_tools.py:361`,
  - `api/services/ai_marketplace.py:1546`,
  - `api/routers/ai_listing_assistant.py:267`,
  - `api/routers/workflow.py:692`,
  - `scheduler/collector.py:757`.
  Подтверждено через grep. Без `ad`-context free-листинги (price=0 + giveaway-фразы)
  трактуются как negotiable (None) и тихо исключаются из anomaly-scoring,
  flip-estimates и `matches_tracker_filters` (последний вообще роняет
  фильтр по `min_discount_percent`). Прокинуть `ad` во все вызовы (как
  делает `aggregator.py:638,710`, `listing_mapper.py:84-368`).
- **[LOGIC-LOW]** `aggregator._percentile()` для `len==1` возвращает само
  значение → `q1==q3==median==price` → `_market_spread()=0`. По текущему
  guard `count<4` это безопасно, но подтверждённо документировать.
- **[LOGIC-LOW]** `_remove_outliers` при `len==8` может схлопнуть до 0;
  downstream использует `count` guards, но UX «8 → 0 учтено» путает.
- **[LOGIC-MEDIUM]** `compute_price_vs_median` для negotiable отдаёт `0.0`,
  не `None`. В `reseller_tools.matches_tracker_filters` это даёт
  `discount_pct=0`, и фильтр `min_discount_percent` молча исключает free.

### 11.2 Currency & Money

- **[LOGIC-MEDIUM]** `currency_service.py:67` — TTL 1 час, но история
  (`price_history.py:56-63`) конвертит снимки **сегодняшним** курсом.
  7-дневный график USD = смесь реальной цены и курса. Хранить рейт на
  момент снимка либо считать в BYN и конвертировать на клиенте.
- **[LOGIC-MEDIUM]** `currency_service._extract_rates()` падает только если
  пропал USD; пропавшие EUR/RUB → fallback в BYN без ошибки → юзер видит
  BYN с лейблом «EUR».
- **[LOGIC-LOW]** `DEFAULT_USD_RATE = 3.0` хардкод. Дрейф во времени.
  Поставить алерт на «using fallback rate».

### 11.3 Signals & Alerts

- **[LOGIC-HIGH]** `scheduler/collector.py:529` — `discount_pct = abs(compute_price_vs_reference(...))`.
  `abs()` объединяет «дешевле» и «дороже». Завышение цены на 30% =
  fake-alert «-30%». См. также §5.1.
- **[LOGIC-MEDIUM]** Restock-detection — чисто по `ad_id`. Тот же селлер,
  re-post с новым `ad_id` всегда «new». Не баг, документировано, но
  стоит прописать в RUNBOOK.
- **[LOGIC-MEDIUM]** Outage Kufar → 0 объявлений → `sync_query_listing_states`
  пометит все existing inactive → false-positive «всё пропало». Guard
  `if upstream_failed: skip` (см. §5.1).
- **[LOGIC-LOW]** `detect_trend_reversal_up` — 3% rebound/decline, на BYN-100
  это 3 BYN — шум. Поднять минимум или абсолютный порог.

### 11.4 Deal Workflow

- **[LOGIC-HIGH]** `api/routers/workflow.py:~280 update_lead` принимает
  любой `LeadStatusEnum` и применяет напрямую. **Нет state-машины**,
  «sold → new» / «watching → sold напрямую» проходят. Завести
  `ALLOWED_TRANSITIONS` map с верификацией и 422.
- **[LOGIC-MEDIUM]** Двойная продажа: установка `sold_price_byn` дважды
  просто перезатирает. Optimistic-locking предотвращает гонку, но не
  intentional re-sell. Залочить status `sold` от update'а.
- **[LOGIC-MEDIUM]** `api/routers/expenses.py:~50 create_expense` — нет
  idempotency-key. Mini-app retry → дубликат расхода. Принимать
  `Idempotency-Key` header и деддупать в 24-час. окне (Redis SETNX).
- **[LOGIC-LOW]** `api/routers/analytics.py:135 total_expenses_byn` ≠
  `total_cost_byn`. Поля живут вместе в ответе и в UI; имя
  `total_expenses_byn` интуитивно подразумевает участие в profit-calc, но
  это ALL expenses, а profit использует только won-deals. Переименовать в
  `total_expenses_byn_all` или вернуть оба под именами `won_expenses_byn`,
  `all_expenses_byn`.

### 11.5 Scraping & Mapping

- **[LOGIC-LOW]** `query_pipeline._normalize_response_ads` — эвристика
  «kopecks vs BYN». Прод имеет `price_usd`, влияет только на тестовые
  фикстуры. Документировать инвариант.
- **[LOGIC-LOW]** Listings без image/price/location идут в API как-есть.
  Это сделано осознанно (юзер видит всё, stats исключают unpriced).

### 11.6 AI dedupe & analysis

- **[LOGIC-LOW]** `ai_dedupe.py` — текстовый дедуп; не учитывает временные
  метки (это и не нужно).

### 11.7 Bot & Reminders

- **[LOGIC-MEDIUM]** `scheduler/collector.py:1230-1250` — для `outcome=="blocked"`
  reminder.sent остаётся False. После разблокировки бота полетит stale
  напоминание (см. §5.1).
- **[LOGIC-LOW]** Reminder double-send риск низкий из-за быстрых тиков.
  Но `_REMINDER_SEND_CONCURRENCY=5` — пер-tick, не cross-tick. Если
  понадобится защита — distributed-lock в Redis.
- **[LOGIC-LOW]** `bot/handlers/callbacks.py add_lead/add_watch` идемпотентны
  через `INSERT ON CONFLICT` в `workflow_store.upsert_lead`. OK.
- **[LOGIC-LOW]** TrackerEvent retention 30 дней → callback enrichment
  деградирует на старых нотификациях, fallback «откройте mini-app».

### 11.8 Frontend↔Backend contract

- *Положительно:* `formatPrice` ожидает BYN, бэк возвращает BYN; контракт
  сходится.
- **[LOGIC-MEDIUM]** `render_charts.js:~370` — «расходы» в profit-карточке
  отображает `total_expenses_byn` (все расходы), но `profit` вычитает только
  won-deal expenses → визуальное несоответствие. См. §11.4.
- **[LOGIC-LOW]** `bot/handlers/analytics.py:~130` — `count` после outlier-removal
  показывается как «Выборка», `total_results` — как «На рынке». При
  count << total_results юзер недоумевает. Tooltip / документация.
- **[LOGIC-LOW]** `render_charts.js:~290` — labelChart-tooltip говорит
  «Медиана» без указания валюты; смена currency mid-session с stale-кэшем
  показывает старую валюту.
- **[LOGIC-LOW]** `saved_search ↔ tracker` — несвязанные сущности; ренейм
  не пропагируется. By design.

---

## 12. Consistency / Docs / Dead code

### 12.1 Docs vs code

- **[CON-MEDIUM]** `README.md:10,137` — ссылается на `konechno.md`, файл
  отсутствует и **не gitignored**. Битая ссылка в публичной документации.
- **[CON-MEDIUM]** `AGENTS.md:4,43,166` — ссылается на `claude.md` и
  `mybad.md`, оба отсутствуют и не gitignored. Привести к gitignore либо
  убрать упоминания.
- **[CON-MEDIUM]** `docs/UI_UX_OPTIMIZATION_REPORT.md` — заголовок утверждает
  «отчёт исторический», но статус-маркеры внутри (❌ против реализованных
  пунктов) устарели и противоречат коду:
  - SVG-иконки в табах ✅ (index.html содержит inline SVG).
  - `touch-action: manipulation` ✅ (`tokens.css:193,223`).
  - disabled-стили ✅ (`states.css:315-318`).
- **[CON-LOW]** README.md → 5 проверенных пунктов соответствуют коду
  (scrape, leads, signals, AI analysis, /metrics).

### 12.2 Dead code & dead assets

- **[CON-MEDIUM]** `frontend/js/config.js` — `window.APP_CONFIG` нигде не
  читается. Не подключён из index.html, не входит в bundle.
  Полностью мёртв.
- **[CON-MEDIUM]** `api/services/parallel_kufar.py` — импортируется только
  `tests/test_parallel_kufar.py`. Прода-код его не использует. Test-only
  утилита.
- **[CON-LOW]** Мёртвые CSS-селекторы (определены, не используются):
  `.render-error` (`states.css:91`), `.quick-chip` (множество вхождений
  в `tokens.css:213,241,305,679,870`, `brand.css:91,103,1321,1335`,
  `states.css:27`), `.listing-card:active` (`tokens.css:248` — реальные
  карточки используют `listing`).

### 12.3 Duplication

- **[CON-MEDIUM]** `pyproject.toml` — два механизма dev-зависимостей
  (`[project.optional-dependencies] dev` с `pytest>=8.3.0` и
  `[dependency-groups] dev` с `pytest>=9.0.3`). Установки `--extra dev`
  и `--group dev` дают разные версии. Удалить один (рекомендую сохранить
  `[project.optional-dependencies]`).
- **[CON-LOW]** Дубль fetch-логики в `api_leads.js` ↔ `api_watchlist.js`
  и фильтр-чек в `render_cards.js` ↔ `api_listings.js` — см. §6.

### 12.4 TODO/FIXME

- ✅ Грep по `TODO|FIXME|XXX|HACK` в `api/`, `bot/`, `scheduler/`,
  `frontend/`, `scripts/`, `migrations/` — **ноль actionable items**.
  Единственное упоминание «hacks» в `frontend/js/virtual_list.js:14` —
  декларация о намерении, не баг.

### 12.5 Project metadata

- См. INF §9.7 — license/authors/dual-pytest.

### 12.6 Branch / version drift

- **[CON-LOW]** API-роутеры с ad-hoc dict-ответами (без `response_model`):
  - `api/routers/ai_analysis.py:93` — `GET /task/{task_id}`,
  - `api/routers/ai_analysis.py:124` — `POST /analyze`,
  - `api/routers/health.py:17` — `GET /health` (для health приемлемо).
- **[CON-LOW]** Naming-стиль роутер-функций отклоняется в:
  - `ai_analysis.py:131 analyze_listing`,
  - `ai_tools.py:108 negotiate_price`,
  - `ai_tools.py:216 price_advice`,
  - `ai_listing_assistant.py:459 listing_assistant`,
  - `export.py:190 export_leads`.
  Привести к доминирующему `get_X/create_X/update_X/delete_X`.
- **[CON-LOW]** Нет упоминаний Python 3.13 (соответствует `.python-version` 3.12). OK.
- **[CON-LOW]** Frontend cache-busting (`bump_static_version.sh`) покрывает
  index.html / sw.js / js/*.js; CSS попадает через index.html. OK.

---

## Appendix A — Ложные срабатывания агентов (НЕ являются багами)

Эти пункты были отброшены после ручной верификации:

- ❌ «`hmac.new` is deprecated / doesn't exist» — `bot/auth.py:24-25`,
  `api/middleware/telegram_auth.py:34`. На самом деле `hmac.new` —
  стандартный, поддерживаемый module-level alias `hmac.HMAC` с момента
  Python 1.5; используется идиоматически. Оба места корректны.
- ❌ «`api/middleware/telegram_auth.py:34` — `hmac.new` deprecated»
  (повторно, тот же false positive).

## Appendix B — Положительные находки (не править)

Эти зоны явно проверены и **в норме**:

- IDOR — все id-в-пути роутеры фильтруют по `user_id` в SQL-WHERE.
- Image proxy — base URL хардкоден, `follow_redirects=False`,
  size/timeout капы → SSRF закрыт.
- Bot `/start` deep-link param-allowlist `(tracking|deals|monitoring)`.
- `frontend/sw.js` — `skipWaiting()` + `clients.claim()`, BYPASS_PATHS на
  изменяемые ресурсы.
- Modal a11y (role=dialog, focus-trap, ESC, scroll-lock) — реализованы
  корректно.
- `scripts/bump_static_version.sh` идемпотентен и покрывает все ?v= точки.
- Bundle (`app_bundle.js`) синхронизирован с источниками (тесты SHA OK).

---

## Appendix C — Рекомендованный порядок устранения

**Wave 1 (CRITICAL + блокеры релиза):**

1. `migrations/20260511_0008` consent CHECK rollback risk — squash 0008+0011.
2. `ai_guards.py:82` debug-bypass — закрыть.
3. `ai_sanitize.py` Unicode-confusables — добавить NFKC normalize.
4. `ai_service._parse_json` — Pydantic-валидация ответа.
5. `scheduler/collector.py:529 abs()` — снять, использовать только negative.
6. `workflow_store.ensure_user pg_insert` — добавить dialect-detect.
7. `workflow.py update_lead` — state-machine с allowed transitions.
8. `image_proxy.py SSRF` — добавить явный IP-валидатор как defense-in-depth
   (даже при хардкоден base) + unit-тест.
9. `api/services/client_ip.py` + `nginx/default.conf` — Cloudflare IPv6.

**Wave 2 (HIGH + privacy):**

10. `_ANALYSIS_RUN_LIMIT=1` → bump до 4–8 + bench.
11. `compress_image` → `asyncio.to_thread`.
12. `ai_marketplace.py` JSON load → lifespan startup.
13. `ai_privacy.py revoke` — инвалидация общего AI-кэша по hash query.
14. `LeadItem.version` optimistic locking — добавить `WHERE version=:expected`.
15. `LeadItemPriceSnapshot` ретенция — cleanup-task.
16. `precompute_cluster_stats` O(n²) — переход на single-pass или offload.
17. `nginx` `server_tokens off`, HSTS, `webz.telegram.org` в CORS+CSP.
18. CI-actions pin к SHA.

**Wave 3 (MEDIUM):**

19. Cache-key digest для `geography/segments/listing_detail/price_history`.
20. `offset` upper-bound на `workflow.py`.
21. `Decimal` для `PriceStatsResponse` и `deal_workflow` calc.
22. Pipeline status `researching` — добавить CSS-класс.
23. `scripts/partition_snapshots.sql` — PK + UNIQUE + DEFAULT-партиция.
24. Brotli + multistage Dockerfile + Prometheus histograms.
25. Frontend: AbortController в `api_listings.loadListings`,
    LRU revoke в `api_image_proxy`, фикс `MutationObserver` flood.

**Wave 4 (LOW и чистка):**

26. Дроп `frontend/js/config.js` или подключить.
27. Удалить мёртвый CSS (`.quick-chip`, `.render-error`, `.listing-card`).
28. Привести `pyproject.toml` (одна dev-секция, license, authors).
29. Восстановить или убрать `konechno.md`/`claude.md`/`mybad.md` ссылки.
30. Naming-привод роутер-функций.

---

_Конец отчёта. Всего ≈ 220 находок по 12 доменам._
