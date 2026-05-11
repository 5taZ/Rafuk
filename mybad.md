# Deep dive review: найденные проблемы

Дата аудита: 2026-05-11
Ветка/workspace: `bad-app`, текущее рабочее дерево с незакоммиченными изменениями.
Формат приоритетов:

- **P0** — критично/blocking: подтверждённая дыра или поломка, требующая немедленного исправления.
- **P1** — major/release-blocking: ломает CI/release или может сильно бить по production UX/reliability.
- **P2** — important: нужно исправить soon, но не блокирует весь продукт прямо сейчас.
- **P3** — maintainability/polish: технический долг, качество, документация, удобство сопровождения.

## Verification snapshot

| Проверка | Результат |
|---|---|
| `uv run pytest --tb=short -q` | OK: `634 passed, 1 skipped in 49.43s` |
| `uv run ruff check .` | FAIL: 46 lint errors |
| `uv run alembic -c migrations/alembic.ini heads` | OK: один head `20260511_0010 (head)` |
| `node --check frontend/js/app_bundle.js` + lazy AI JS | OK |
| generated `frontend/js/app_bundle.js` vs source modules | OK |
| `git diff --check` | OK |
| `pip-audit` на production deps | OK: no known vulnerabilities |

## P0

### P0-0. Подтверждённых P0 не найдено

**Сфера:** security / backend / frontend
**Локация:** проверенный код проекта в целом.
**Проблема:** не найдено подтверждённой проблемы уровня unauthenticated cross-user read/write, RCE, явной утечки secret values в tracked файлах или прямого SSRF через user-controlled URL.
**Ограничение:** не выполнялись browser/Telegram WebView/Lighthouse/DAST и не просматривался `.env`.
**Рекомендация:** не считать это security-сертификацией; для production отдельно прогнать DAST/browser QA и проверить real deployment exposure.

## P1

### P1-1. `ruff check` красный, CI/release gate не пройдёт

**Сфера:** tests / quality / CI
**Локации:** `api/routers/ai_tools.py`, `api/services/aggregator.py`, `api/services/ai_prompts.py`, `api/services/listing_mapper.py`, `migrations/versions/6040ea4a0fd6_unify_pk_types.py`, `migrations/versions/7b413345fcf2_add_updated_at_to_trackers.py`, `scheduler/collector.py`, `tests/*`.
**Проблема:** `uv run ruff check .` возвращает exit code 1 с 46 errors. CI workflow запускает этот gate перед docker build/release.
**Impact:** merge/release будет заблокирован даже при зелёном pytest.
**Рекомендация:** отдельная lint-only wave: исправить `E501`, `I001`, `UP007`, `UP035`, `UP037`, `N806`, `SIM102`, `UP041`, без массового `ruff format`.

### P1-2. AI provider throttle сериализует весь AI HTTP call

**Сфера:** backend / AI / performance / reliability
**Локации:** `api/services/ai_service.py::_chat`, `api/services/ai_service.py::analyze_listing_parallel`, `api/services/ai_analysis_pipeline.py::_stage_ai`.
**Проблема:** `_chat()` держит `_get_chat_lock()` вокруг sleep и всего `_post_with_retry()` HTTP call. При этом `analyze_listing_parallel()` ожидает две параллельные sub-call задачи через `asyncio.gather`.
**Impact:** фактический AI-анализ становится последовательным: Call B ждёт завершения Call A, затем spacing. Это повышает latency, риск timeout/fallback и снижает throughput AI endpoints.
**Рекомендация:** lock должен защищать только разрешение старта provider call: sleep/update `_last_chat_started_at` внутри lock, сам `_post_with_retry()` выполнять после выхода из lock. Если нужен hard concurrency cap — добавить отдельный semaphore/token bucket.

### P1-3. Nginx CORS не разрешает `X-Requested-With`

**Сфера:** frontend / security / infra
**Локации:** `nginx/default.conf`, `api/main.py`, `frontend/js/api_core.js`.
**Проблема:** backend CORS и CSRF ожидают `X-Requested-With`, frontend добавляет его на mutating requests, но nginx `Access-Control-Allow-Headers` содержит только `X-Telegram-Init-Data, Content-Type, Accept`.
**Impact:** в cross-origin Telegram/dev/proxy сценариях легитимные POST/PATCH/DELETE могут падать на browser preflight до FastAPI.
**Рекомендация:** добавить `X-Requested-With` в оба nginx `Access-Control-Allow-Headers` и покрыть config regression test.

### P1-4. Refresh endpoints держат DB transaction во время внешних Kufar calls

**Сфера:** backend / DB / performance / reliability
**Локации:** `api/routers/workflow.py::refresh_watchlist`, `api/routers/workflow.py::refresh_leads`; похожий паттерн стоит перепроверить в scheduler tracker loop.
**Проблема:** endpoints входят в `async with session_factory() as session, session.begin():`, читают DB rows, затем внутри той же transaction делают `asyncio.gather(load_query_dataset(...))` с внешними Kufar-запросами.
**Impact:** slow upstream/timeout удерживает DB connection + transaction открытыми; при росте пользователей возможны pool exhaustion, idle-in-transaction, lock contention и плохой tail latency.
**Рекомендация:** разбить на фазы: короткая DB read transaction → закрыть session → Kufar fetches with bounded concurrency/cache → короткая DB write transaction с re-load rows by id/user/status/version.

## P2

### P2-1. Expense type contract drift между DB, API и UI

**Сфера:** backend / data model / frontend
**Локации:** `api/models.py::DealExpense`, `api/schemas.py::ExpenseTypeEnum`, `frontend/index.html`, `frontend/js/render_modals.js`.
**Проблема:** DB check constraint разрешает `delivery`, `repair`, `customs`, `packaging`, `transport`, `other`; API enum и UI select разрешают только `delivery`, `repair`, `other`.
**Impact:** часть валидных DB значений нельзя создать/редактировать через публичный контракт; будущие imports/admin edits могут создать rows, которые UI не представит как first-class категории.
**Рекомендация:** выбрать canonical set и синхронизировать DB constraint, Pydantic enum, UI labels/rendering, tests и migration.

### P2-2. Listing Assistant photo limit рассинхронизирован

**Сфера:** backend / API contract / frontend / performance
**Локации:** `api/schemas.py::AIListingAssistantRequest`, `api/routers/ai_listing_assistant.py::_coerce_listing_photos`, `frontend/js/api_listing_assistant.js`, `nginx/default.conf`.
**Проблема:** schema принимает до 8 photos, router тихо оставляет максимум 4, frontend тоже ограничивает 4, nginx comment/body-size ориентирован на 4.
**Impact:** API контракт вводит клиентов в заблуждение; лишние фото парсятся и потом silently dropped.
**Рекомендация:** сделать schema `max_length=4`, вынести shared constant, обновить tests/comment/body-size assumptions.

### P2-3. Raw user query используется в cache keys

**Сфера:** backend / Redis / performance / observability
**Локации:** `api/routers/listings.py`, `api/routers/price_stats.py`, `api/routers/ai_tools.py`.
**Проблема:** response cache keys включают raw query (`listings:{query}:...`, `price-stats:{query}:...`, `ai_price_advice:{payload.query}:...`), хотя dataset cache уже использует digest helper.
**Impact:** Redis key bloat, высокая cardinality, длинные/странные ключи, сложнее invalidation/monitoring.
**Рекомендация:** перейти на digest-based key builder для всех user-controlled cache keys; normalized query хранить в cached value для debugging.

### P2-4. `/listings` для `cheap`/`deal_score` строит до 200 карточек до pagination

**Сфера:** backend / performance
**Локации:** `api/routers/listings.py`.
**Проблема:** для computed sorts endpoint строит `ListingItem` для `sorted_ads[:_MAX_LISTINGS_PAGE]`, сортирует по `deal_score`, и только потом режет `offset:limit`.
**Impact:** broad queries + infinite scroll платят CPU за карточки, которые пользователь не увидит.
**Рекомендация:** lightweight score precompute на raw ads → sort → build only requested slice; либо cache per-ad computed score/context.

### P2-5. Category totals fan-out обходит Kufar delay lock

**Сфера:** backend / upstream performance / anti-ban
**Локации:** `api/services/query_pipeline.py::fetch_category_totals`.
**Проблема:** `_fetch_one()` вызывает `search(..., bypass_delay=True)`. Semaphore ограничивает concurrency, но spacing bypass повышает burstiness.
**Impact:** cold-cache broad/diverse queries могут дать всплеск запросов к Kufar и повысить риск rate-limit/ban.
**Рекомендация:** заменить hard bypass на bounded token bucket/budget: max category calls per query, start spacing, метрики cold fan-out duration/count.

### P2-6. `get_session_factory_dependency` fail-open создаёт fallback engine

**Сфера:** backend / DB / reliability
**Локация:** `api/dependencies.py::get_session_factory_dependency`.
**Проблема:** если lifespan не выставил `app.state.session_factory`, dependency логирует warning и создаёт новый engine/session factory.
**Impact:** в production misconfigured lifespan может плодить pools и скрыть проблему до connection exhaustion.
**Рекомендация:** fail-loud в non-test runtime; tests должны использовать dependency override.

### P2-7. Rate limiter degraded mode fail-closed зависит только от `ENV=production`

**Сфера:** security / ops / rate limiting
**Локации:** `api/limiter.py`, `api/main.py`, `api/config.py`.
**Проблема:** Redis failure переводит limiter в memory fallback; app отказывается стартовать только при `ENV=production`.
**Impact:** production-like deploy с remote DB, но без `ENV=production`, может работать с per-process лимитами при нескольких workers.
**Рекомендация:** ввести explicit `environment` setting/enum или fail-closed если DB non-local и Redis unavailable, независимо от raw env var.

### P2-8. `/metrics` открыт, если API port доступен извне

**Сфера:** security / ops / observability
**Локации:** `api/main.py`, `docker-compose.yml`.
**Проблема:** `/metrics` отдаёт Prometheus text без auth; compose публикует API `8010:8000`.
**Impact:** если port открыт не только локально/private, внешний актор получает reconnaissance: routes/status/latency/error patterns.
**Рекомендация:** bind API private/internal, закрыть `/metrics` reverse-proxy ACL/basic-auth/flag, либо не публиковать API port в production profile.

### P2-9. AI price advice обещает trend/history без historical input

**Сфера:** AI / product logic
**Локация:** `api/routers/ai_tools.py::price_advice`.
**Проблема:** endpoint передаёт AI текущий market snapshot, но response schema заполняет `price_trend` и `historical_context` из модели.
**Impact:** модель может уверенно “додумать” тренд без query snapshots, что опасно для advice `buy_now/wait`.
**Рекомендация:** либо реально передавать historical snapshots/history_service, либо переименовать copy/schema в “current market context” и запрещать trend claims без данных.

### P2-10. Listing Assistant history хранит пользовательские тексты в localStorage без TTL/privacy controls

**Сфера:** frontend / privacy / UX
**Локация:** `frontend/js/api_listing_assistant.js`.
**Проблема:** `rafuk:listing-assistant:history` хранит до 20 entries с input snippets и AI output; есть individual delete, но нет TTL, clear-all, privacy toggle или “не сохранять”.
**Impact:** на shared/device compromise остаются названия товаров, цены, заметки и AI drafts.
**Рекомендация:** TTL, clear all, “не сохранять историю”, privacy microcopy, tests на trimming/expiry.

### P2-11. CSP source-of-truth drift: HTML meta слабее nginx header

**Сфера:** frontend / security / maintainability
**Локации:** `frontend/index.html`, `nginx/default.conf`.
**Проблема:** HTML meta CSP всё ещё разрешает `https://telegram.org` в `script-src`, а nginx CSP уже убрал этот источник, потому что Telegram SDK vendored.
**Impact:** при serving без nginx policy CSP слабее; два source-of-truth повышают шанс будущего drift.
**Рекомендация:** синхронизировать meta/header или генерировать CSP из одного template; добавить test.

### P2-12. Frontend accessibility gaps: unlabeled controls и состояния chips

**Сфера:** frontend / accessibility
**Локации:** `frontend/index.html`, `frontend/js/render_views.js`.
**Проблема:** минимум `select#filter-region` и disabled `input#edit-tracker-query` не имеют корректного label/aria; filter chips меняют только class `active`, без `aria-pressed`/`aria-selected`.
**Impact:** screen reader и keyboard users хуже понимают назначение controls и состояние фильтров.
**Рекомендация:** добавить `aria-label`/`for`, для chip buttons — `aria-pressed`; покрыть static structure test.

### P2-13. Touch targets местами меньше 44px

**Сфера:** frontend / mobile UX / accessibility
**Локации:** `frontend/css/style.css` (`.filter-chip`, `.period-chip`, `.la-history-delete`).
**Проблема:** filter chips имеют `min-height: 32px`, history delete `28x28`, period chips без `min-height: 44px`.
**Impact:** Telegram Mini App mobile-first; маленькие targets ухудшают usability и WCAG touch target compliance.
**Рекомендация:** визуально можно оставить компактно, но hit area довести до 44px через padding/`::before`.

### P2-14. Layout-property animations в UI

**Сфера:** frontend / performance / motion
**Локации:** `frontend/css/parts/ai.css`, generated `frontend/css/style.css`.
**Проблема:** AI progress bar анимирует `width`, recent strip анимирует `max-height`.
**Impact:** layout/reflow на слабых телефонах; хуже 60fps и против motion best practices.
**Рекомендация:** progress через `transform: scaleX` + `transform-origin: left`; recent strip через opacity/transform или grid-template-rows.

### P2-15. PDF export выбивается из design system

**Сфера:** frontend / design / maintainability
**Локация:** `frontend/js/api_ai_pdf.js`.
**Проблема:** PDF export hardcodes Arial/Courier, raw hex colors, decorative rings/side tabs, тогда как design context требует data-first Linear/Vercel style, Rubik + JetBrains Mono.
**Impact:** exported report выглядит как отдельный продукт и несёт AI-slop паттерны.
**Рекомендация:** tokenized PDF stylesheet subset, JetBrains Mono for numbers, убрать `border-left` verdict tabs, добавить snapshot test на generated HTML.

### P2-16. Generated fallback AI result может попадать в cache и требовать аккуратной invalidation

**Сфера:** backend / AI / cache correctness
**Локации:** `api/services/ai_analysis_pipeline.py::_deliver_fallback_result`, `api/routers/ai_analysis.py::analyze_listing`, `frontend/js/api_ai_render.js`.
**Проблема:** fallback result сохраняется в cache с `_ai_warning`; router уже удаляет warning-cache при sync analyze hit, но нужно убедиться, что все read paths одинаково не закрепляют degraded результат надолго.
**Impact:** пользователь может видеть рыночный черновик вместо настоящего AI-анализа, если cache invalidation/TTL path разойдётся в будущем.
**Рекомендация:** закрепить тестами все cache-hit paths для `_ai_warning`, держать fallback TTL коротким для rate-limit errors и явно показывать warning в UI.

## P3

### P3-1. Крупные god/source files усложняют review

**Сфера:** maintainability / architecture
**Локации:** `api/services/ai_service.py` (~1489 lines), `api/services/ai_analysis_pipeline.py` (~991), `frontend/js/api_listing_assistant.js` (~1017), `frontend/css/style.css` (~9911 generated), `scheduler/collector.py` (~1677).
**Проблема:** большие файлы усложняют code review и повышают шанс спрятать regressions.
**Impact:** медленнее ревью, сложнее локализовать баги, выше риск accidental coupling.
**Рекомендация:** не массовый rewrite; выделять seams wave-by-wave: AI throttle/token bucket, listing assistant history, PDF stylesheet, workflow refresh transactions.

### P3-2. AGENTS.md содержит устаревшее количество тестов

**Сфера:** docs / project rules
**Локация:** `AGENTS.md`.
**Проблема:** правила всё ещё говорят “494 tests must stay green”, фактически сейчас `634 passed, 1 skipped`.
**Impact:** вводит следующего разработчика/агента в заблуждение.
**Рекомендация:** обновить после code fixes, не отдельной большой docs wave.

### P3-3. Рабочее дерево не atomic

**Сфера:** git hygiene / process
**Локация:** текущий workspace.
**Проблема:** на момент аудита было много modified files одновременно: backend, frontend, docker, scripts, tests. Это нарушает идею small atomic waves.
**Impact:** сложнее review/rollback, выше риск случайно смешать unrelated fixes.
**Рекомендация:** перед commit разделить изменения на themed atomic waves; каждый wave — зелёные tests/lint.

### P3-4. Некоторые комментарии/контракты расходятся с текущим кодом

**Сфера:** maintainability / docs-in-code
**Локации:** nginx body-size/photo comment, CSP comments, старые notes вокруг AI/frontend modules.
**Проблема:** комментарии местами описывают старый контракт или не весь контракт.
**Impact:** следующий разработчик может чинить “по комментарию”, а не по реальному поведению.
**Рекомендация:** cleanup comments после функциональных фиксов, без изменения поведения.

### P3-5. Frontend design context drift по font stack

**Сфера:** frontend / design consistency
**Локации:** `.impeccable.md`, `frontend/index.html`, `frontend/css/style.css`.
**Проблема:** design context документирует Rubik + JetBrains Mono, текущий HTML/CSS использует Geist + JetBrains Mono. Это может быть осознанное изменение, но контекст не обновлён.
**Impact:** design audits и future UI work будут спорить с реальным продуктом.
**Рекомендация:** либо вернуть Rubik, либо обновить `.impeccable.md` с новым font direction и rationale.

### P3-6. Ops decision-gated gaps остаются открытыми

**Сфера:** devops / security / reliability
**Локации:** `AGENTS.md`, `.github/workflows/ci.yml`, `docker-compose.yml`, `scripts/backup.sh`, `api/metrics.py`.
**Проблема:** monitoring, off-host backups/restore drills, image push/CD, Docker Secrets/secret store, IaC, true `ai_audit_log` partitioning остаются deferred по пользовательской политике/нуждаются в решении.
**Impact:** production loop неполный: слабее detection/recovery/deploy automation.
**Рекомендация:** не начинать без решения пользователя; когда решение будет — вынести в отдельные ops waves.

## Рекомендуемый порядок исправления

1. **P1-1**: unblock CI (`ruff check .`).
2. **P1-2**: исправить AI throttle lock scope.
3. **P1-3**: синхронизировать nginx CORS headers.
4. **P1-4**: разнести refresh transactions и Kufar network calls.
5. **P2-1/P2-2/P2-11**: синхронизировать контракты DB/API/UI/CSP.
6. **P2-3/P2-4/P2-5**: backend performance/cache/upstream pass.
7. **P2-6/P2-7/P2-8**: production hardening для DB dependency, limiter, metrics exposure.
8. **P2-10/P2-12/P2-13/P2-14/P2-15**: frontend privacy/a11y/performance/design pass.
9. **P3**: docs/workspace hygiene и decision-gated ops items.
