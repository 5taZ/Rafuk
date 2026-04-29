# Полный отчёт код-ревью — Rafuks / Kufar Analytics

**Дата:** 2026-04-29
**Ветка:** `bad-app`
**Глубина:** deep
**Файлов проверено:** 57 (28 backend, 19 frontend, 10 bot/scheduler/tests)
**Найдено:** 5 Critical, 24 Warning, 18 Info

---

## CRITICAL (5 багов)

### CR-API-01: AI Task Store — утечка данных между пользователями
- **Файл:** `api/routers/ai_analysis.py:154-170`
- **Проблема:** Теневой словарь `_tasks` (module-level) хранит задачи по ключу `task_id` без неймспейса пользователя. Функция `_update_task` вызывает `_get_task(cache, task_id)` без `user_id`, поэтому может вернуть данные чужой задачи. Два пользователя с совпавшими/угаданными task_id получат доступ к анализам друг друга.
- **Решение:** Добавить проверку владельца в `_get_task` для shadow-записей; прокидывать `user_id` через `_update_task`.

### CR-API-02: Telegram Auth — окно будущих timestamp расширяет replay-атаку
- **Файл:** `api/middleware/telegram_auth.py:51`
- **Проблема:** Проверка `auth_date > now + 60` принимает timestamp на 60 секунд в будущем. Вместе с `max_age=300` это даёт окно replay-атаки 360 секунд вместо 300. Злоумышленник с перехваченным `initData` имеет лишнюю минуту.
- **Решение:** Уменьшить до `now + 5` (допустимый сдвиг часов).

### CR-BOT-01: Module-level asyncio.Lock ломается при uvicorn --reload
- **Файл:** `bot/database.py:12`
- **Проблема:** `_init_lock = asyncio.Lock()` создаётся на уровне модуля. При fork (reload, multi-worker) дочерний процесс наследует lock, привязанный к event loop родителя. Вызовы `init_bot_engine()` / `close_bot_engine()` падают с `RuntimeError: Task got Future attached to a different loop`.
- **Решение:** Создавать lock лениво (внутри функции) или через singleton-класс с `_get_lock()`.

### CR-SCHED-01: Неограниченный SQL IN(...) — краш при большом числе трекеров
- **Файл:** `scheduler/collector.py:164, 190`
- **Проблема:** `_recent_events_by_tracker()` и `_recent_trend_event_tracker_ids()` передают полный список tracker IDs в `IN(...)`. PostgreSQL лимит — 65535 bind-параметров. При тысячах трекеров — краш БД.
- **Решение:** Батчить IN-список по 500 или использовать подзапрос по активным трекерам.

### CR-FE-01: innerHTML в _showConfirmDialog — паттерн XSS-риска
- **Файл:** `frontend/js/app_actions.js:564-570`
- **Проблема:** Диалог подтверждения строится через `innerHTML` с интерполяцией строк. Сейчас все вызовы передают хардкод — уязвимости нет. Но паттерн хрупок: любой будущий вызов с user-controlled данными создаст XSS-вектор в контексте Telegram Mini App (где можно украсть `initData`).
- **Решение:** Переписать на DOM-конструирование через `document.createElement` + `textContent`.

---

## WARNING (24 бага)

### Backend API (8)

#### WR-API-01: CSRF не пропускает Telegram WebApp origins в production
- **Файл:** `api/main.py:152-183`
- **Проблема:** `web.telegram.org` и `webk.telegram.org` добавлены в allowed origins **только** в debug-режиме. В production Telegram WebView, отправляющий `Origin`, получит 403.
- **Решение:** Добавить Telegram origins в allowed set безусловно.

#### WR-API-02: MemoryCache.incr() сохраняет истёкший TTL для пересозданных счётчиков
- **Файл:** `api/services/cache.py:73-93`
- **Проблема:** При пересоздании счётчика после истечения TTL, новый счётчик получает старое (уже прошедшее) время истечения. Rate-limit счётчики мгновенно исчезают — лимиты обходятся.
- **Решение:** При создании нового счётчика после expired использовать свежий TTL: `new_expires = time.monotonic() + ttl`.

#### WR-API-03: get_cache утечка Redis-соединений при fallback
- **Файл:** `api/dependencies.py:26-29`
- **Проблема:** Когда `app.state.cache` is None, `get_cache` создаёт новый `RedisCache.from_url()` на каждый вызов. Каждый создаёт connection pool, который никогда не закрывается.
- **Решение:** Бросать `RuntimeError` вместо молчаливого создания утечки.

#### WR-API-04: ensure_user_exists — race condition при конкурентных первых запросах
- **Файл:** `api/dependencies.py:87-108`
- **Проблема:** Два одновременных запроса от нового пользователя проходят проверку `scalar_one_or_none() is None` и оба пытаются INSERT → `IntegrityError`. Не обрабатывается, сессия остаётся в сломанном состоянии.
- **Решение:** Обернуть INSERT в `try/except IntegrityError` с `rollback`.

#### WR-API-05: Export не проверяет user_id на расходах
- **Файл:** `api/routers/export.py:200-209`
- **Проблема:** Запрос расходов по `lead_id` без фильтра `user_id`. Если баг в другом месте создаст расход с чужим `user_id` на совпавший `lead_id` — утечка данных.
- **Решение:** Добавить `.where(DealExpense.user_id == user_id)`.

#### WR-API-06: TrackerUpdate позволяет установить required-поля в None → 500
- **Файл:** `api/schemas.py:277-289`, `api/routers/trackers.py:252-254`
- **Проблема:** `TrackerUpdate` позволяет все поля = None. `setattr(tracker, "interval_min", None)` → NOT NULL violation → 500 вместо валидации.
- **Решение:** Фильтровать None в update_data: `{k: v for k, v in payload.items() if v is not None}`.

#### WR-API-07: Auth-ошибки раскрывают детали валидации клиенту
- **Файл:** `api/dependencies.py:78-82`
- **Проблема:** `detail=str(exc)` передаёт клиенту "Invalid Telegram initData signature" и "initData is too old". Атакующий может энумерировать валидные/невалидные подписи и определить окно возраста.
- **Решение:** Заменить на generic "Invalid Telegram authentication", детали логировать сервером.

#### WR-API-08: Export грузит все tracker events без лимита
- **Файл:** `api/routers/consent.py:329-342`
- **Проблема:** Запрос всех событий трекера без `.limit()`. Пользователь с тысячами событий = медленный ответ и high memory.
- **Решение:** Добавить `.limit(N)` или курсорную пагинацию.

---

### Bot / Scheduler (10)

#### WR-SCHED-01: First-check трекеры теряют все события
- **Файл:** `scheduler/collector.py:768-777`
- **Проблема:** При первой проверке (`last_checked_at is None`) трекер получает baseline и `continue`. Но `sync_query_listing_states()` уже вызван на уровне query-group и записал все текущие объявления как "new". На следующем цикле они уже seen — пользователь никогда не получит уведомлений.
- **Решение:** Вынести first-check трекеры до вызова `sync_query_listing_states()`, или пропускать sync для них.

#### WR-SCHED-02: notify_user деактивирует трекеры без commit/flush
- **Файл:** `scheduler/collector.py:104-113`
- **Проблема:** UPDATE трекеров в `active=False` не следует за `flush()`. Деактивация вступает в силу только на финальном `commit()` — остальные трекеры этого пользователя в текущем цикле продолжают генерировать уведомления.
- **Решение:** Добавить `await session.flush()` после UPDATE в notify_user.

#### WR-SCHED-03: Bot handlers создают httpx.AsyncClient на каждый запрос
- **Файл:** `bot/handlers/analytics.py:61`, `bot/handlers/callbacks.py:53`
- **Проблема:** Каждый `_api_get()` / `_api_post()` создаёт новый `httpx.AsyncClient` → TCP connection + TLS handshake + teardown. Под нагрузкой — лишняя задержка и churn.
- **Решение:** Шарить один httpx client на уровне модуля/приложения.

#### WR-SCHED-04: round(0.5) = 0 в Python (banker's rounding) — delta не показывается
- **Файл:** `scheduler/collector.py:618, 546`
- **Проблема:** `round(float(delta))` на 0.5 даёт 0 (banker's rounding). Условие `> 0` не проходит — строка "(-X р.)" не выводится. Пользователь не видит размер скидки.
- **Решение:** Использовать `math.ceil(float(delta))` или `float(delta) >= 0.5`.

#### WR-SCHED-05: _detect_threshold_alerts не ограничивает число событий
- **Файл:** `scheduler/collector.py:382-467`
- **Проблема:** Нет cap на количество threshold-событий. Много matching ads → огромное число событий и длинное уведомление.
- **Решение:** Добавить `_MAX_THRESHOLD_EVENTS = 10`, возвращать `events[:_MAX_THRESHOLD_EVENTS]`.

#### WR-SCHED-06: Пропущен session.rollback() в error handler scheduler'а
- **Файл:** `scheduler/collector.py:958-964`
- **Проблема:** `except _TRACKER_QUERY_ERRORS` не делает `rollback()`. Сессия остаётся в "pending rollback" состоянии — все последующие query groups в цикле тоже падают.
- **Решение:** Добавить `await session.rollback()` в except-блок.

#### WR-SCHED-07: Scheduler reconnection создаёт новый engine, но jobs используют старую factory
- **Файл:** `scheduler/collector.py:1199-1203`
- **Проблема:** При потере connection: dispose old engine → create new engine + new session_factory. Но scheduler jobs зарегистрированы с kwargs, ссылающимися на старую factory. После реконнекта jobs продолжают использовать disposed factory.
- **Решение:** Перезапускать scheduler с новой factory, или использовать mutable holder.

#### WR-BOT-01: asyncio.run() внутри TestClient context — хрупкий тест
- **Файл:** `tests/test_ai_analysis.py:242`
- **Проблема:** `asyncio.run(cache.set_json(...))` внутри `with TestClient(app)`. TestClient запускает ASGI в отдельном потоке, но `asyncio.run()` создаёт новый loop. Если httpx/starlette поменяет поведение — тест сломается.
- **Решение:** Предзаполнять cache до входа в TestClient context.

#### WR-BOT-02: Bot handlers не экранируют user input в HTML-сообщениях
- **Файл:** `bot/handlers/analytics.py:94-101, 162-167`
- **Проблема:** `search_query` из пользовательского ввода (команда `/stats <query>`) вставляется в Telegram HTML-сообщение без экранирования. `/stats <b>test</b>` ломает форматирование.
- **Решение:** Использовать `from html import escape` для `search_query`.

#### WR-BOT-03: Дублирование _build_init_data_header в bot handlers
- **Файл:** `bot/handlers/analytics.py:24-43`, `bot/handlers/callbacks.py:23-37`
- **Проблема:** Функция `_build_init_data_header()` дублирована verbatim в двух файлах. Изменения HMAC-логики нужно применять в обоих местах.
- **Решение:** Вынести в общий `bot/auth.py`.

---

### Frontend (6)

#### WR-FE-01: Global keydown listener в listing assistant не удаляется (memory leak)
- **Файл:** `frontend/js/api_listing_assistant.js:828`
- **Проблема:** `document.addEventListener("keydown", ...)` регистрируется при создании модуля, но `destroy()` удаляет только submit listener. Keydown listener утечка; при hot-reload — накапливаются.
- **Решение:** Сохранить ссылку на handler, удалять в `destroy()`.

#### WR-FE-02: innerHTML для SVG-иконок — нарушение конвенции
- **Файл:** `frontend/js/render_trackers.js`, `frontend/js/render_card_builders.js`
- **Проблема:** SVG-иконки вставляются через `innerHTML` с хардкодом. Не уязвимость, но нарушение конвенции (`domEl()`). Будущие изменения могут добавить dynamic attributes.
- **Решение:** Использовать `document.createElementNS` или добавить комментарий "hardcoded SVG — safe for innerHTML".

#### WR-FE-03: escapeHtml() на textContent — двойное экранирование
- **Файл:** `frontend/js/render_modals.js:193`
- **Проблема:** `msg.textContent = escapeHtml(rf.message || ...)` — textContent не парсит HTML, поэтому `&lt;` отображается буквально. Сообщение `"Price < 100"` покажется как `"Price &lt; 100"`.
- **Решение:** Убрать `escapeHtml()`: `msg.textContent = rf.message || rf.type || ""`.

#### WR-FE-04: exportLeads использует fetch без timeout
- **Файл:** `frontend/js/app_actions.js:410`
- **Проблема:** Прямой `fetch()` вместо `getJson()` с AbortController-timeout. При зависании API (большой XLSX) — бесконечное ожидание без фидбека.
- **Решение:** Добавить AbortController с таймаутом ~120s.

#### WR-FE-05: Virtual list insertion scan O(n)
- **Файл:** `frontend/js/virtual_list.js:142-152`
- **Проблема:** `_mergeRenderedItems` сканирует весь `renderedMap` для поиска insertion point. При сотнях entries — замедление. Практически tolerable (viewport ~20-50 items), но при росте dataset станет проблемой.
- **Решение:** Binary search по отсортированным ключам.

#### WR-FE-06: console.warn в production-коде
- **Файл:** `frontend/js/app_core.js:394`
- **Проблема:** `measureRender()` вызывает `console.warn` при рендере > 100ms. На слабых устройствах — мусор в консоли.
- **Решение:** Убрать или условить на dev-режим.

---

## INFO (18 замечаний)

### Backend API (6)

| ID | Файл | Описание |
|----|------|----------|
| IN-API-01 | `api/services/ai_service.py:1490` | `lru_cache(maxsize=1)` singleton не обновляется при изменении env-vars без restart |
| IN-API-02 | `api/routers/ai_analysis.py:1125`, `ai_listing_assistant.py:113` | `_coerce_string_list` дублирован — нужно импортировать из одного места |
| IN-API-03 | `api/routers/consent.py:6` | `import json` — первоначально помечен как unused, но используется на line 445 (false positive) |
| IN-API-04 | `api/services/ai_service.py:1435` | `_parse_json` Strategy 3 (non-greedy regex) может match inner object — рассмотреть удаление |
| IN-API-05 | `api/routers/segments.py:25-28` | `_EMPTY_SEGMENT` — plain dict вместо `PriceStats` модели, обходит type checking |
| IN-API-06 | `api/models.py:93` | `TimestampMixin.created_at` без `timezone=True` — tz-naive vs tz-aware comparison → TypeError |

### Bot / Scheduler (7)

| ID | Файл | Описание |
|----|------|----------|
| IN-SCHED-01 | `bot/handlers/analytics.py:24-43`, `callbacks.py:23-37` | `_build_init_data_header` дублирован — вынести в `bot/auth.py` |
| IN-SCHED-02 | `tests/test_ai_analysis.py` | 1450 строк, смешивает unit/integration тесты — разделить на файлы |
| IN-SCHED-03 | `tests/test_health.py:19-26` | Не мокает DB/Redis — тест недетерминированный (зависит от инфраструктуры) |
| IN-SCHED-04 | `tests/test_parallel_kufar.py` | `type: ignore[method-assign]` для monkey-patching — лучше использовать `monkeypatch` |
| IN-SCHED-05 | `tests/test_telegram_auth.py:14` | Hardcoded test token — допустимо, комментарий есть |
| IN-SCHED-06 | `scheduler/collector.py:353-360` | `_format_price_byn(0)` = "0 р." — misleading, должно быть "договорная" |
| IN-SCHED-07 | `scheduler/collector.py:1043` | `reminder.sent = True` ставится даже при неудачной отправке — retry невозможен |

### Frontend (5)

| ID | Файл | Описание |
|----|------|----------|
| IN-FE-01 | `frontend/css/parts/tokens.css:425,427` | Дублирующее `color: var(--text)` в `.logo-name` |
| IN-FE-02 | `frontend/css/parts/tokens.css` | Несколько CSS custom properties определены, но не используются (dead weight) |
| IN-FE-03 | `frontend/js/app_core_dom.js:209` | `domEl()` вызывается, но не определена в этом файле — неявная зависимость от порядка загрузки |
| IN-FE-04 | `frontend/js/virtual_list.js:158` | `el._vlIndex` — expando property на DOM-элементе, лучше `WeakMap` |
| IN-FE-05 | `frontend/css/style.css` | `@import` блокирует рендер — для no-build-step допустимо, при добавлении build — конкатенировать |

---

## Приоритет исправления

1. **Сначала security** (CR-API-01, CR-API-02, WR-API-07) — утечка данных и auth
2. **Затем stability** (CR-BOT-01, CR-SCHED-01, WR-SCHED-06) — краши при продакшн-нагрузке
3. **Потом UX-баги** (WR-SCHED-04, WR-FE-03, WR-SCHED-01) — пользователи видят некорректные данные
4. **После — quality** (остальные WR-*) — memory leaks, race conditions, edge cases
5. **IN-* — по желанию** — code quality, не влияет на работу

---

_Отчёт сгенерирован: 2026-04-29_
_Ревьюер: Claude (gsd-code-reviewer, deep mode)_
