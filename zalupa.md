# Deep-dive аудит Rafuk / Kufar Analytics

Дата: 2026-05-14  
Ветка: текущая рабочая копия `bad-app`  
Режим: обзор без фиксов, без параллельных subagents. Использованы профильные skills: `audit`, `impeccable`, `security-review`, `code-review`.

## Границы аудита

Проверены текущие исходники backend/API, frontend, scheduler/bot, nginx/docker/CI, приватность/AI consent, производительность, UX/a11y, тесты и эксплуатационные риски. `.env` намеренно не читался и секреты не ротировались: это явно закреплено в `AGENTS.md`.

В рабочем дереве до отчёта уже были чужие удаления `konechno.md`, `restissues.md`, `search.md`, `searchproblems.md`; я их не трогал.

## Быстрый verdict

Проект сейчас в заметно лучшем состоянии, чем типичный Mini App: есть Telegram HMAC auth, CSRF Origin + `X-Requested-With`, CSP без `unsafe-inline`, SRI для vendored Telegram SDK, Redis-backed лимиты, AI consent, PII/prompt-injection sanitizer, image allowlist, typed account deletion, service worker с bypass для write-heavy endpoints, nginx rate/conn limits, CI lint/test/security jobs.

Оставшиеся проблемы в основном не «всё горит», а системные: большие god-файлы, приватность AI/local history, пара runtime UX/a11y дыр, Redis readiness edge-case, тяжёлый frontend shell и нехватка браузерных regression tests.

**P0 blocking:** 0  
**P1 major:** 5  
**P2 minor/next pass:** 8  
**P3 polish:** 4

## Health score по skill `audit`

| Dimension | Score | Ключевой вывод |
|---|---:|---|
| Accessibility | 3/4 | Много хорошего: skip link, labels, focus styles, `aria-*`; остаются long-press focus и мелкие tap-targets. |
| Performance | 3/4 | Есть single-flight/cache/virtualization/lazy chunks; остаются тяжёлый shell, write amplification и крупные modules. |
| Theming | 3/4 | Token system и dark/light есть; вне token-файла ещё остаются hard-coded rgba/shadow values. |
| Responsive | 3/4 | Mobile-first shell в целом норм; отдельные controls меньше 44px. |
| Anti-patterns/design | 3/4 | Дизайн соответствует data-first dark dashboard; местами generic glass/dashboard patterns, но это осознанный контекст проекта. |
| **Total** | **15/20** | **Good: адресовать P1/P2, не переписывать всё с нуля.** |

---

# P1 — major issues

## P1-SEC-INF-01 — Redis/rate-limiter readiness проверяет TCP, но не AUTH/команды

**Где:** `api/limiter.py:32-55`, `api/limiter.py:88-101`, `api/main.py:116-135`  
**Категория:** Security / Infra / Reliability

`_redis_reachable()` делает только TCP connect. Если Redis доступен по сети, но пароль/DB/ACL неверные, `rate_limiter_degraded` останется `False`, production-like fail-closed guard в `main.py` не сработает, а SlowAPI storage может начать падать уже на реальных запросах. Основной `RedisCache.ping()` при этом может откатиться в `MemoryCache`, но limiter всё равно считает себя Redis-backed.

**Почему важно:** abuse protection — security boundary. Ошибка Redis AUTH не должна превращаться в 500 на первом трафике или в per-process limits на multi-worker API.

**Рекомендация:** заменить TCP probe на настоящий Redis `PING` через тот же `storage_uri` или добавить startup self-test SlowAPI storage (`INCR` тестового ключа + delete) и fail-closed в production-like окружении. Отдельно покрыть тестом сценарий «TCP открыт, AUTH неверный».

## P1-PRIV-01 — AI audit log хранит raw query/title, хотя AI-bound текст sanitizes PII

**Где:** `api/services/ai_audit.py:57-62`, `api/routers/ai_analysis.py:139-158`, `api/routers/ai_tools.py:121-234`, `api/routers/ai_listing_assistant.py:498-516`  
**Категория:** Privacy / Security / Data minimization

`sanitize_user_text()` хорошо маскирует телефоны/e-mail/handles/doc IDs перед отправкой в AI, но `_log_ai_audit()` пишет raw `query`/`payload.title` в `ai_audit_log` на срок до политики retention. Для AI tools и listing assistant пользовательский query/title может содержать PII или коммерчески чувствительный текст.

**Почему важно:** получается длинноживущий серверный лог с raw user text + endpoint/model/IP. Даже при наличии согласия лучше хранить минимально необходимое: sanitized query, hash, truncated normalized text или отдельные поля `query_hash` + `query_preview_scrubbed`.

**Рекомендация:** перед записью audit log прогонять `query`/`result_summary` через `sanitize_user_text()` или отдельный `sanitize_audit_text()`; для точной трассировки добавить SHA-256 hash raw текста без сохранения raw. Миграцией/cleanup можно scrub старые строки.

## P1-PRIV-02 — consent copy hard-coded и может не соответствовать фактическому AI provider/model

**Где:** `frontend/index.html:1136-1138`, `api/config.py:175-177`, `docker-compose.yml:62-63`  
**Категория:** Privacy / Legal UX / Configuration drift

В consent modal текст жёстко говорит: `Together API-совместимая модель Gemini (США)`. В конфиге же provider/model настраиваются через `AI_BASE_URL` / `AI_MODEL`, а дефолт сейчас — Google Gemini OpenAI-compatible endpoint. Если ops поменяет provider/model/region, UI останется старым.

**Почему важно:** consent должен описывать реального обработчика/трансграничную передачу. Hard-coded provider в статическом HTML легко устаревает и создаёт юридически неприятный drift.

**Рекомендация:** отдавать публичный AI processing descriptor из backend (`provider_label`, `model_label`, `country_or_region`, `policy_version`) и рендерить его в consent/privacy modal. Минимум — заменить copy на provider-neutral текст и явно вынести актуальные subprocessors в policy config.

## P1-UX-PRIV-03 — AI consent modal показывается на старте даже без AI intent

**Где:** `frontend/js/app.js:143-150`, `frontend/js/app_actions.js:587-603`  
**Категория:** UX / Privacy / Consent quality

`init()` сразу вызывает `actions.checkAiConsent()`, который проверяет все 3 consent types и показывает modal, если чего-то нет. Пользователь может открыть приложение просто для поиска/аналитики/трекеров, но получает AI/PD consent gate до явного AI действия.

**Почему важно:** это повышает consent fatigue и выглядит как принуждение к AI consent для non-AI сценариев. Для качества согласия лучше показывать gate в момент действия: «AI-анализ», «AI помощник продавцу», etc.

**Рекомендация:** сделать startup check soft/non-modal: badge/banner «AI функции требуют согласия» или вообще lazy gate only on AI entrypoints. Если нужен отдельный PD consent для всего приложения, разнести его текст/тип от AI/cross-border consent.

## P1-ARCH-01 — слишком много god-файлов в runtime-критичных зонах

**Где:** line count scan текущего repo  
**Категория:** Architecture / Maintainability / Reviewability

Крупнейшие hotspots:

- `scheduler/collector.py` — 1740 строк
- `api/services/ai_marketplace.py` — 1649
- `api/services/ai_category_data.py` — 1628
- `api/services/ai_service.py` — 1397
- `api/services/aggregator.py` — 1085
- `api/services/ai_analysis_pipeline.py` — 994
- `api/services/query_pipeline.py` — 876
- `api/schemas.py` — 856
- `api/routers/workflow.py` — 840
- `api/models.py` — 752
- `api/routers/consent.py` — 744
- frontend: `dom_helpers.js` 1223, `api_events.js` 1114, `api_listing_assistant.js` 1096, `app_actions.js` 972
- CSS: `layout.css` 2008, `brand.css` 1908, `pipeline.css` 1753, `modals.css` 1525

**Почему важно:** bugs в таких файлах дороже искать и ревьюить; security/privacy fixes легче пропустить; тесты становятся «большими интеграционными простынями» вместо узких unit seams.

**Рекомендация:** не делать big-bang rewrite. Резать волнами по bounded contexts с backward-compatible re-exports, как уже описано в `AGENTS.md`: scheduler health/alerts/snapshot cleanup отдельно, AI marketplace/category data отдельно, workflow router service layer отдельно, CSS by view/component.

---

# P2 — next pass issues

## P2-BE-PERF-01 — auto-provision user запускает DB task после каждого authenticated request

**Где:** `api/main.py:378-423`, `api/dependencies.py:202-228`  
**Категория:** Backend performance / Logic

Комментарий говорит «first authenticated request», но middleware фактически после каждого response создаёт background task с `ensure_user_exists()`. На холодном открытии Mini App frontend делает fan-out по нескольким endpoints — каждый ответ создаёт отдельный DB INSERT ... ON CONFLICT DO NOTHING.

**Почему важно:** это write amplification и лишний DB churn на read-heavy analytics. На single-user dev почти незаметно, но при многопользовательском трафике создаёт постоянные конфликтные insert/check операции.

**Рекомендация:** держать small TTL cache `provisioned_user:{telegram_user_id}` в Redis/in-memory per worker или provision делать в auth dependency только при cache miss. Покрыть тестом: 5 authenticated GET подряд вызывают provisioning один раз в TTL.

## P2-PRIV-04 — Listing Assistant history default-on в localStorage

**Где:** `frontend/js/api_listing_assistant.js:72-76`, `frontend/js/api_listing_assistant.js:96-113`, `frontend/js/api_listing_assistant.js:979-993`, policy disclosure `frontend/index.html:1199-1200`  
**Категория:** Privacy / Frontend storage

История AI помощника хранит title, draft price, condition, notes, photos_count и AI output до 20 записей / 30 дней. Есть checkbox, TTL и clear button — это хорошо. Но default: `localStorage.getItem(HISTORY_SAVE_KEY) !== "0"`, то есть сохранение включено до явного отказа.

**Почему важно:** Telegram WebView/localStorage живёт на устройстве, может попадать в backup/debug dumps/shared device сценарии. Для seller notes это чувствительнее, чем recent searches.

**Рекомендация:** сделать first-use opt-in («Сохранять историю на этом устройстве 30 дней?») или default-off для notes/output. Альтернатива: сохранять только scrubbed title + pricing summary, а полный output держать session-only.

## P2-A11Y-01 — long-press/context menu без focus management и с leak при повторном open

**Где:** `frontend/js/dom_helpers.js:1021-1101`, `frontend/js/dom_helpers.js:1104-1111`  
**Категория:** Accessibility / Frontend runtime

`showLongPressMenu()` создаёт `role="menu"` / `role="menuitem"`, но после открытия не переводит focus на первый menuitem, не trap-ит focus, не inert-ит фон и не восстанавливает focus на исходную карточку. Плюс если menu открыть повторно до `hideLongPressMenu()`, старый `_longPressEscHandler` уже нельзя удалить: переменная перезаписывается, а старый document listener остаётся.

**Почему важно:** keyboard/screen-reader пользователь может не понять, что menu открылось, и продолжит tabbing по фону. Повторные открытия создают мелкий listener leak.

**Рекомендация:** в начале `showLongPressMenu()` вызывать cleanup старого handler, сохранять `document.activeElement`, после render `focus()` первый item, Escape/backdrop возвращают focus; использовать общий `trapFocus()` / inert helpers, как consent/privacy modals.

## P2-PERF-01 — frontend shell всё ещё тяжёлый для Telegram WebView

**Где:** asset size scan текущего repo  
**Категория:** Frontend performance

Текущие размеры:

- `frontend/index.html` — 81,413 B raw / 15,667 B gzip
- `frontend/js/app_bundle.js` — 162,511 B raw / 37,915 B gzip
- `frontend/css/style.css` — 153,332 B raw / 24,523 B gzip
- `frontend/vendor/telegram-web-app.js` — 116,341 B raw / 18,259 B gzip

Gzip нормальный, но parse/execute/layout cost остаётся: HTML содержит много hidden modal/policy/detail markup, CSS общий для всех views, app bundle несёт базовый orchestration слой.

**Почему важно:** Telegram WebView на слабых Android телефонах часто ограничен CPU сильнее, чем сетью. Большой DOM/CSSOM до первого поиска ухудшает perceived start.

**Рекомендация:** lazy-mount privacy/account/detail/listing-assistant markup, split CSS by always-needed shell vs view/modal chunks, оставить critical CSS минимальным. Сохранять CSP без inline handlers.

## P2-TEST-01 — нет настоящих browser/a11y/user-flow regression tests

**Где:** `tests/` содержит 65 файлов; текущий полный run дал 839 passed / 1 skipped. Есть `test_app_js_syntax.py` и `test_frontend_structure.py`, но нет Playwright/browser flow suite.  
**Категория:** Testing / Frontend reliability

Статические frontend tests полезные, но большинство прошлых frontend багов такого класса ловится только runtime: lazy script registration, focus trap, modal Escape, IntersectionObserver load-more, localStorage consent/history, service worker cache bypass.

**Почему важно:** vanilla JS без framework/compiler даёт мало safety net. Syntax/DOM structure tests не доказывают, что пользовательский сценарий работает в браузере.

**Рекомендация:** добавить маленький Playwright smoke suite: search page renders, filter apply/reset, detail modal open/close/Escape, consent modal focus/Escape, long-press menu keyboard path, listing assistant history opt-in/off. Можно запускать отдельно от fast pytest.

## P2-OPS-01 — monitoring/backups/CD/IaC остаются ручными/отложенными operational decisions

**Где:** `AGENTS.md` deferred ops table, `.github/workflows/ci.yml`, `docs/BACKUP_RUNBOOK.md`, `scripts/backup.sh`  
**Категория:** Ops / Reliability

В коде уже есть metrics endpoint, request IDs, backup script и CI. Но production-grade контур ещё зависит от решений пользователя: off-host backup target, alerting/uptime, deployment target, CD pipeline, IaC/secrets backend.

**Почему важно:** приложение может быть безопасным на уровне кода, но без external monitoring/backups/restore drills реальные incident recovery и SLA слабые.

**Рекомендация:** не внедрять без согласования (это прямо в `AGENTS.md`). Когда пользователь разрешит: выбрать backup target, добавить cron/systemd timer или managed backup, Prometheus/UptimeRobot alerts, CD deploy target, registry/image promotion.

## P2-RESP-01 — часть touch targets меньше мобильного 44px guideline

**Где:** `frontend/css/parts/layout.css:663-666`, `frontend/css/parts/pipeline.css:805-812`  
**Категория:** Responsive / Mobile UX

Примеры: `.totals-refresh-btn` — 28x28 min, `.lead-btn--emoji` — 36px min-height. Это не обязательно WCAG AA blocker, но для Telegram Mini App на телефоне 44px guideline практичнее.

**Почему важно:** маленькие icon-only controls рядом с плотными данными дают mis-taps, особенно в движении/на улице.

**Рекомендация:** сохранить визуально компактный icon, но увеличить hit area через padding/min-size/transparent wrapper до 44px. Если дизайн боится шума — использовать invisible hit slop.

## P2-PERF-02 — CSS/component token drift: hard-coded colors остаются вне tokens

**Где:** scan `frontend/css/parts/*.css`  
**Категория:** Theming / Design system

Вне `tokens.css` найдено примерно 29 hard-coded color/function hits: `brand.css` 22, `modals.css` 4, `ai.css` 2, `layout.css` 1. В основном это shadows/overlays, но есть явные `rgba(...)` в component files.

**Почему важно:** dark/light темы уже есть, но hard-coded values в компонентах усложняют будущий palette refresh и audit contrast.

**Рекомендация:** завести semantic tokens для overlay/shadow/surface tint и перевести component CSS на них. Не обязательно делать OKLCH миграцию сразу, но новые цвета лучше добавлять только как tokens.

## P2-LOGIC-01 — server-side cap на listings честно раскрывается, но UX всё ещё может путать total vs reachable

**Где:** `api/routers/listings.py:319-441`, `frontend/js/render_cards.js:409-428`  
**Категория:** Product logic / UX

Backend возвращает Kufar `total`, `served_cap`, `is_limited`, frontend пишет «Показана быстрая выборка: до N из M». Это уже хорошо. Но badge всё равно показывает полный total, а фактически reachable list ограничен 200.

**Почему важно:** power users могут думать, что scrolling покажет все `M`, хотя сервер ограничивает выборку.

**Рекомендация:** рядом с total badge показывать persistent compact label `200 из 12 345` / `выборка`, а не только bottom sentinel note. Для broad queries сразу предлагать уточнить запрос/категорию.

---

# P3 — polish / low-risk cleanup

## P3-FE-01 — console logging остаётся в production code paths

**Где:** `frontend/js/app_renderers.js`, lazy stubs, `api_events.js`, `render_core.js`, `api_listing_assistant.js`  
**Категория:** Frontend polish / Observability

Есть `console.error/warn` на lazy-load/render failures and fallbacks. Это не security bug, но production console будет шуметь на слабых WebViews/network issues.

**Рекомендация:** завести маленький `logClientError()` с debug flag / sampling, а user-facing toast оставить как сейчас.

## P3-CI-01 — coverage upload есть, но quality gate не enforced

**Где:** `.github/workflows/ci.yml:143-157`  
**Категория:** Testing / CI

CI запускает pytest с coverage и загружает Codecov, но `fail_ci_if_error: false`, threshold не виден. Это нормально для early project, но не защищает от постепенного снижения покрытия.

**Рекомендация:** после стабилизации добавить мягкий threshold по backend пакетам или хотя бы changed-lines coverage gate.

## P3-FE-02 — policy/privacy modal грузится в HTML shell целиком

**Где:** `frontend/index.html:1159-1218`  
**Категория:** Performance / Content architecture

Privacy policy важна, но редко открывается на first paint. Сейчас её table/list content включён в shell HTML.

**Рекомендация:** lazy-load privacy content из static fragment/JSON или template, сохранив доступность и offline fallback.

## P3-DESIGN-01 — dark data-dashboard aesthetic местами близок к generic AI dashboard

**Где:** overall frontend visual system, `.impeccable.md` context  
**Категория:** Design / Anti-patterns

Проект сознательно выбрал Linear/Vercel/Raycast dark data UI. Это уместно для price analytics, но hero stats/glass/electric blue могут выглядеть шаблонно, если не усилить собственный Rafuk identity.

**Рекомендация:** не «раскрашивать всё». Лучше добавить один-два уникальных брендовых мотива: Kufar-market signal language, distinctive empty states, more specific microcopy, stronger number hierarchy.

---

# Позитивные находки, которые стоит сохранить

- **Auth:** Telegram initData HMAC verification, max age, replay tracking signal, auth_bypass guarded from production.
- **CSRF/CORS:** Origin check + `X-Requested-With`, no `null` origin, CORS explicit origins.
- **CSP/frontend supply chain:** no `unsafe-inline`, vendored Telegram SDK with SRI, vendored Chart/fonts.
- **Data safety:** `safeUrl`, `safeKufarUrl`, `safeImageUrl`, image proxy path allowlist, object URL cleanup on detail modal close.
- **AI safety/privacy:** consent enforcement on backend AI endpoints, PII scrubber, prompt-injection pattern detection/logging, AI rate limits, AI audit failure metric.
- **Performance:** query dataset single-flight, distributed cache lock, Kufar client rate limiting/circuit breaker, virtual list, lazy charts/AI modules, service worker bypass for mutating endpoints.
- **Infra:** nginx rate/connection limits, compression disabled for selected PII endpoints, non-root Docker user, one-shot migration service before API, healthchecks, request IDs, metrics endpoint.
- **Tests:** broad pytest surface across auth, IDOR, consent, configs, migrations, image proxy, listings, workflow, scheduler, frontend structure/syntax.

# Проверки

- `uv run ruff check .` — PASS (`All checks passed!`).
- `uv run pytest --tb=short -q` — PASS: 839 passed, 1 skipped, 1 warning in 63.64s.

# Приоритет исправлений

1. `P1-SEC-INF-01` — настоящий Redis AUTH/command readiness для limiter.
2. `P1-PRIV-01` — scrub/hash AI audit query/title.
3. `P1-PRIV-02` — сделать AI provider consent text config-driven.
4. `P1-UX-PRIV-03` — ленивый AI consent gate только при AI intent.
5. `P1-ARCH-01` — начать резать god-файлы маленькими backward-compatible waves.
6. `P2-A11Y-01` + `P2-TEST-01` — закрыть long-press focus/listener bug и добавить browser smoke, чтобы не регрессировать.
7. `P2-BE-PERF-01` — убрать per-request user provisioning write amplification.
8. `P2-PRIV-04` — listing assistant history сделать opt-in/default less sensitive.
