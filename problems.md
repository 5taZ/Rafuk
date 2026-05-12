# Problems and Atomic Fix Waves

Дата аудита: 2026-05-12  
База: ветка `bad-app`, HEAD `5feab6c` (`fix(wave31): refine listing modal actions`)  
Статус качества на момент аудита:

- `uv run pytest --tb=short -q` — `661 passed, 1 skipped`
- `uv run ruff check .` — passed
- dependency audit — known vulnerabilities не найдено

## Правила для волн

1. Одна волна = один атомарный commit с одной темой.
2. Commit subject продолжает текущий стиль: `fix(waveNN): <short reason>`.
3. Commit body должен перечислять закрытые problem IDs, например `Closes PROB-002`.
4. После runtime-изменений запускать минимум:
   - `uv run pytest --tb=short -q`
   - `uv run ruff check .`
5. После изменений `frontend/js` или `frontend/css`:
   - пересобрать bundle/stylesheet при необходимости;
   - выполнить `scripts/bump_static_version.sh`.
6. Не смешивать ops-документацию, schema/migration, frontend UX и backend validation в одном commit.
7. Не трогать пользовательские deferred policy items без явного решения: secret rotation, external backup target, monitoring provider, secret store.

## Problem backlog

### PROB-001 — AI privacy/consent copy не совпадает с runtime-конфигурацией

**Severity:** P1  
**Area:** privacy, compliance, frontend copy, config/docs

Frontend consent/privacy говорит, что AI-анализ выполняется `Google Gemini (США)`, но backend/docs/default env описывают Together API-compatible Gemini:

- `frontend/index.html` — consent modal/privacy policy: `Google Gemini (США)`.
- `README.md` — Together-compatible Gemini.
- `api/config.py` и `docker-compose.yml` — default `AI_BASE_URL=https://api.together.xyz/v1`, `AI_MODEL=gemini-2.5-flash`.
- `.env.example` — `AI_MODEL=gemini-3-flash`, что не совпадает с config/compose.

**Impact:** пользователь соглашается на формулировку обработки, которая может не соответствовать реальному processor/subprocessor/model. Это риск доверия и compliance.

**Target state:**

- Один canonical provider/subprocessor/model/region во всех местах.
- `frontend/index.html`, privacy policy, README, `.env.example`, `api/config.py`, `docker-compose.yml` согласованы.
- Если содержание обработки меняется существенно — bump `CURRENT_POLICY_VERSION` и re-consent path.

**Verification:**

- Текстовый grep по `Google Gemini`, `Together`, `AI_MODEL`, `CURRENT_POLICY_VERSION`.
- Tests/lint.
- Ручная проверка consent modal copy.

---

### PROB-002 — Link length contract не выровнен между schemas и DB

**Severity:** P1  
**Area:** backend schemas, DB model/migration, tests

`LeadItem.link` уже `String(2048)`, но `LeadCreate.link` всё ещё `max_length=512`. `TrackerEvent.link` остаётся `String(512)`.

**Impact:** длинные Kufar URL с tracking/recommender params могут быть отвергнуты API до БД или падать при записи tracker events.

**Target state:**

- Все listing/link поля, которые хранят Kufar ad URL, имеют единый лимит 2048.
- DB migration расширяет `tracker_events.link` до 2048.
- Pydantic schemas принимают 2048 там, где DB принимает 2048.
- Regression tests покрывают длинный URL для lead create и tracker event creation.

**Verification:**

- Alembic migration upgrade на test DB.
- Tests/lint.

---

### PROB-003 — Tracker update contract содержит мёртвую ветку `query`

**Severity:** P2  
**Area:** backend API contract

`TrackerUpdate` не содержит `query`, но `update_tracker()` проверяет `"query" in update_data` и пересчитывает `config_keyword`.

**Impact:** либо query update задуман, но недоступен; либо stale code создаёт ложный контракт и усложняет сопровождение.

**Target state:**

Выбрать один вариант:

1. Разрешить update `query` у tracker и покрыть тестами пересчёт `config_keyword`.
2. Запретить update `query` явно и удалить мёртвую ветку.

**Recommendation:** вариант 2, если frontend не умеет редактировать query; вариант 1 только если это реальное UX-требование.

**Verification:**

- Tests на `PATCH /trackers/{id}`.
- Lint.

---

### PROB-004 — Tracker/lead schemas имеют gaps в validation bounds/max_length

**Severity:** P2  
**Area:** backend validation, API hardening

Tracker create/update поля `min_discount_percent`, `max_price_byn`, `seller_type`, `condition`, `region_name`, `config_keyword`, alert thresholds не имеют bounds/max_length, хотя DB columns имеют Numeric/String limits. `LeadUpdate.notes` не имеет `max_length=512`, хотя create и DB ограничены.

**Impact:** мусорные/отрицательные/слишком длинные значения могут доходить до DB, создавать 500/Integrity errors или некорректные фильтры.

**Target state:**

- Pydantic bounds отражают DB и продуктовые ограничения.
- `LeadUpdate.notes` ограничен 512.
- Tests на отрицательные цены/скидки, слишком длинные strings, oversized notes.

**Verification:**

- Schema validation tests.
- Existing API tests.
- Lint.

---

### PROB-005 — `WatchlistRead.price_history` использует mutable default

**Severity:** P3  
**Area:** backend schema hygiene

`price_history: list[PriceSnapshotPoint] = []` лучше заменить на `Field(default_factory=list)`.

**Impact:** в Pydantic v2 это обычно не shared-list bug, но стиль отличается от остальных schemas и может привести к путанице.

**Target state:**

- `price_history: list[PriceSnapshotPoint] = Field(default_factory=list)`.
- Minimal schema regression test, если рядом есть подходящий тест.

**Verification:** tests/lint.

---

### PROB-006 — Skip-link ведёт в пустой `<main>`

**Severity:** P2  
**Area:** frontend accessibility

`<a href="#main-content">` есть, но `<main id="main-content">` пустой; основной UI находится рядом, не внутри landmark.

**Impact:** keyboard/screen reader пользователи переходят к пустому landmark вместо основного содержимого.

**Target state:**

- Реальный основной контент находится внутри `<main id="main-content">`, либо skip-link указывает на настоящий контейнер основного контента.
- Не ломается layout Telegram Mini App.

**Verification:**

- DOM/static test или HTML parse test.
- Manual keyboard check: Tab → skip link → Enter фокусирует meaningful content.

---

### PROB-007 — Chart.js preload противоречит lazy-load

**Severity:** P2  
**Area:** frontend performance

`index.html` preloads Chart.js в `<head>`, но `render_charts.js` lazy-loads Chart.js при первом paint графика.

**Impact:** пользователи, которые не открывают графики, могут платить network cost за Chart.js.

**Target state:**

- Убрать unconditional preload.
- Опционально добавить intent-based prefetch при раскрытии chart panel/overview.
- Комментарии в HTML/JS согласованы с реальным поведением.

**Verification:**

- Static grep: нет `<link rel="preload" as="script" ...chart.js...>` в head.
- Manual/network check при открытии app без chart panel.
- Frontend JS syntax check / existing checks.

---

### PROB-008 — Listing Assistant localStorage history хранит чувствительный AI output

**Severity:** P2  
**Area:** privacy, frontend UX

AI Listing Assistant хранит историю в `localStorage` до 30 дней: input title, draft price, condition, extra notes, photos_count и full AI output. Есть opt-out и clear, но server-side erasure это не очищает.

**Impact:** localStorage доступен любому JS в origin при XSS и не покрывается серверным account deletion.

**Target state:**

- Privacy copy явно говорит, что история AI Assistant хранится локально в браузере/Telegram WebView.
- Пользователь понимает TTL, opt-out и clear behavior.
- Рассмотреть уменьшение persisted payload или default-off, если UX позволяет.

**Verification:**

- Manual modal check.
- Static grep for localStorage history copy.
- Tests/lint if JS tests exist; otherwise syntax check.

---

### PROB-009 — Query singleflight работает только in-process при `WORKERS=4`

**Severity:** P2  
**Area:** backend performance/reliability

`query_pipeline.py` прямо отмечает, что singleflight ломается при нескольких uvicorn workers. Dockerfile default — `WORKERS=4`.

**Impact:** cold search всё ещё может дублировать Kufar pagination между workers, нагружая upstream и увеличивая latency.

**Target state:**

Выбрать один вариант:

1. Реализовать Redis-based distributed singleflight/lock для dataset fetch.
2. Оставить in-process singleflight как осознанный trade-off, но добавить метрики upstream fetch/cache miss и документацию scaling boundary.

**Recommendation:** для текущего single-host compose сначала вариант 2 + метрики; Redis distributed lock делать отдельной wave, если upstream duplication реально виден.

**Verification:**

- Tests на cache miss/singleflight behavior.
- Metrics/manual logs для upstream fetch count.

---

### PROB-010 — Metrics per-process/in-memory недостаточны для multi-worker production diagnostics

**Severity:** P2  
**Area:** observability

Metrics хранятся в in-memory counters/summaries каждого процесса. При `WORKERS=4` они не агрегируются и теряются при restart.

**Impact:** `/metrics` полезен для локальной видимости, но ограничен для production диагностики.

**Target state:**

Выбрать один вариант:

1. Prometheus multiprocess mode / external metrics backend.
2. Явно документировать limitation и добавить worker label/process info.
3. Минимальный Redis-backed aggregation для ключевых counters.

**Recommendation:** вариант 2 как quick hardening; полноценный backend — после выбора monitoring stack.

**Verification:**

- `/metrics` smoke test.
- Tests for auth protection remain green.

---

### PROB-011 — initData replay telemetry не использует Cloudflare-aware IP helper

**Severity:** P2  
**Area:** security telemetry

`get_telegram_user()` использует `request.client.host`, а Cloudflare-aware `_get_client_ip()` существует только в consent router.

**Impact:** replay telemetry может писать proxy/container IP вместо реального клиента в Cloudflare deployment.

**Target state:**

- Вынести IP extraction helper в общий модуль.
- Consent router и auth dependency используют один helper.
- Tests покрывают `CF-Connecting-IP`, trusted `X-Forwarded-For`, fallback.

**Verification:** targeted tests + full tests/lint.

---

### PROB-012 — Backup script не оформлен как production backup/restore процесс

**Severity:** P1  
**Area:** ops/reliability

`scripts/backup.sh` есть и не светит DB password в command line, но нет расписания, off-host target, restore drill, alerting.

**Impact:** recovery зависит от ручного запуска и дисциплины.

**Target state:**

- Выбран backup target/retention policy.
- Есть scheduled execution outside app container.
- Есть documented restore drill.
- Есть alerting на failed backup.

**Decision needed:** external backup target и способ запуска требуют решения пользователя.

**Verification:**

- Dry-run backup.
- Restore drill на test DB.
- Проверка retention cleanup.

---

### PROB-013 — Крупные shipped/source файлы ухудшают maintainability

**Severity:** P3  
**Area:** maintainability, frontend/backend code organization

Текущие размеры:

- `frontend/js/app_bundle.js` — ~10,780 lines / ~438 KB raw.
- `frontend/css/style.css` — ~9,919 lines / ~217 KB raw.
- `frontend/vendor/telegram-web-app.js` — ~114 KB raw.
- `scheduler/collector.py` — ~1,681 lines.
- `api/services/ai_service.py` — ~1,489 lines.

**Impact:** выше стоимость ревью, regression risk, сложнее локализовать изменения.

**Target state:**

- Не делать один большой refactor.
- Делать opportunistic extraction только рядом с функциональными изменениями.
- Отдельные split waves для `scheduler/collector.py` и `ai_service.py`, если потребуется.

**Verification:** tests/lint after each split.

---

## Atomic fix waves

Нумерация продолжает историю commits: последняя волна — `wave31`.

### Wave 32 — Align AI privacy contract

**Commit:** `fix(wave32): align ai privacy contract`  
**Closes:** PROB-001, part of PROB-008

**Scope:**

- Выбрать canonical AI provider/subprocessor/model/region.
- Синхронизировать:
  - `frontend/index.html` consent modal + privacy policy;
  - `README.md`;
  - `.env.example`;
  - `api/config.py`;
  - `docker-compose.yml`.
- Если меняется policy meaning — bump `CURRENT_POLICY_VERSION`.
- Добавить/обновить tests around consent version if needed.

**Out of scope:** localStorage implementation changes, backup/monitoring.

**Verification:**

- `rg "Google Gemini|Together|AI_MODEL|CURRENT_POLICY_VERSION" ...`
- `uv run pytest --tb=short -q`
- `uv run ruff check .`

**Decision gate:** нужен выбор: оставить Together-compatible Gemini как canonical или реально перейти на Google Gemini wording/provider.

---

### Wave 33 — Fix listing link length contract

**Commit:** `fix(wave33): align kufar link length limits`  
**Closes:** PROB-002

**Scope:**

- `LeadCreate.link` → `max_length=2048`.
- `TrackerEvent.link` model/migration → `String(2048)`.
- Проверить другие URL fields на тот же contract.
- Добавить tests на URL длиной >512 и <=2048.

**Out of scope:** unrelated validation bounds.

**Verification:**

- Alembic migration upgrade.
- `uv run pytest --tb=short -q`
- `uv run ruff check .`

---

### Wave 34 — Tighten tracker and lead validation

**Commit:** `fix(wave34): tighten tracker and lead schemas`  
**Closes:** PROB-003, PROB-004, PROB-005

**Scope:**

- Решить `TrackerUpdate.query`:
  - preferred: удалить мёртвую ветку, если UI не редактирует query;
  - или добавить query update fully with tests.
- Добавить bounds/max_length для tracker filters/thresholds.
- `LeadUpdate.notes` → `max_length=512`.
- `WatchlistRead.price_history` → `Field(default_factory=list)`.
- Tests на validation errors.

**Out of scope:** DB link migration, frontend.

**Verification:**

- `uv run pytest --tb=short -q`
- `uv run ruff check .`

---

### Wave 35 — Repair frontend a11y landmark and chart loading

**Commit:** `fix(wave35): repair frontend landmark and chart loading`  
**Closes:** PROB-006, PROB-007

**Scope:**

- Исправить empty `<main>`/skip-link landmark.
- Убрать unconditional Chart.js preload.
- При необходимости добавить intent-based prefetch, но только если это не усложняет UX.
- Синхронизировать comments в HTML/JS.
- После frontend asset changes — rebuild/bump cache tags.

**Out of scope:** AI Assistant localStorage privacy copy.

**Verification:**

- HTML/static test or manual keyboard check.
- `node --check frontend/js/app_bundle.js` after rebuild.
- `scripts/bump_static_version.sh`
- `uv run pytest --tb=short -q`
- `uv run ruff check .`

---

### Wave 36 — Clarify AI Assistant local history privacy

**Commit:** `fix(wave36): clarify listing assistant local history`  
**Closes:** PROB-008

**Scope:**

- Add concise UI/privacy copy: history is stored locally in this browser/Telegram WebView for 30 days.
- Ensure opt-out and clear controls are discoverable.
- Optional: reduce persisted output fields if product UX allows.

**Out of scope:** AI provider consent wording already handled by Wave 32.

**Verification:**

- Manual modal check.
- JS syntax check/bundle rebuild if JS changes.
- `scripts/bump_static_version.sh` if frontend assets changed.
- Tests/lint.

---

### Wave 37 — Share Cloudflare-aware client IP helper

**Commit:** `fix(wave37): reuse trusted client ip helper`  
**Closes:** PROB-011

**Scope:**

- Move `_get_client_ip`, `_is_valid_ip`, `_is_cloudflare_ip` to shared module, e.g. `api/services/client_ip.py` or `api/utils/client_ip.py`.
- Consent router imports shared helper.
- `get_telegram_user()` uses shared helper for initData replay telemetry.
- Tests for CF-Connecting-IP, trusted XFF, fallback.

**Out of scope:** metrics/singleflight.

**Verification:**

- Targeted tests.
- `uv run pytest --tb=short -q`
- `uv run ruff check .`

---

### Wave 38 — Document and expose multi-worker observability boundaries

**Commit:** `fix(wave38): expose multi worker observability limits`  
**Closes:** PROB-009, PROB-010 partially

**Scope:**

- Add explicit docs/comments for in-process query singleflight with `WORKERS=4`.
- Add lightweight metrics/counters for dataset cache miss/upstream fetch if feasible without external backend.
- Add `/metrics` process/worker identity label if safe and low-cardinality.
- Document that metrics are per-process unless external monitoring is selected.

**Out of scope:** Redis distributed lock, Prometheus multiprocess, external monitoring provider.

**Verification:**

- Metrics unit tests/smoke tests.
- `uv run pytest --tb=short -q`
- `uv run ruff check .`

**Decision gate:** if the goal is to fully close PROB-009/010 rather than document boundaries, schedule Wave 39/40 below.

---

### Wave 39 — Optional Redis distributed singleflight

**Commit:** `fix(wave39): coordinate cold dataset fetches across workers`  
**Closes:** PROB-009 fully

**Scope:**

- Implement Redis lock/sentinel for dataset fetch ownership.
- Followers wait/poll bounded by timeout, then fall back safely.
- Preserve existing in-process singleflight as fast path.
- Tests with fake Redis/cache backend.

**Out of scope:** metrics backend replacement.

**Verification:**

- Concurrency tests.
- Failure-mode tests: lock owner crashes, timeout, Redis unavailable.
- Full tests/lint.

**When to do:** only if Wave 38 metrics/logs show duplicate cold fetches are material or production scaling requires it.

---

### Wave 40 — Optional production metrics backend

**Commit:** `fix(wave40): make metrics production aware`  
**Closes:** PROB-010 fully

**Scope:**

- Choose one:
  - Prometheus multiprocess mode;
  - OpenTelemetry/StatsD;
  - Redis-backed aggregate counters.
- Keep `/metrics` auth behavior.
- Add tests around render/auth and multi-worker assumptions.

**Out of scope:** backup scheduling.

**Decision gate:** requires choosing monitoring stack.

---

### Wave 41 — Backup and restore runbook/process

**Commit:** `fix(wave41): define database backup process`  
**Closes:** PROB-012

**Scope:**

- Choose backup target and schedule outside app container.
- Add runbook for backup and restore drill.
- Add non-destructive dry-run command/documentation.
- Keep `scripts/backup.sh` safe: no password in argv, retention documented.

**Out of scope:** secret rotation, Docker Secrets/Vault unless separately approved.

**Decision gate:** requires user choice of off-host target/provider.

**Verification:**

- Dry-run backup.
- Restore to test DB.
- Retention cleanup check.

---

### Wave 42 — Opportunistic maintainability splits

**Commit:** `refactor(wave42): split oversized service seams`  
**Closes:** PROB-013

**Scope:**

- Do not split everything in one commit.
- If needed, split into sub-waves:
  - `wave42a`: `api/services/ai_service.py` seam extraction.
  - `wave42b`: `scheduler/collector.py` seam extraction.
  - `wave42c`: frontend source/bundle build hygiene.
- Preserve backward-compatible re-exports where imports exist.

**Out of scope:** behavior changes.

**Verification:**

- Full tests/lint after each sub-wave.
- No frontend behavior change unless covered by manual check.

---

## Recommended execution path

Минимальный путь, который закрывает все non-decision проблемы:

1. Wave 32 — AI privacy contract.
2. Wave 33 — link length contract.
3. Wave 34 — schema validation cleanup.
4. Wave 35 — frontend landmark + Chart.js loading.
5. Wave 36 — localStorage history privacy.
6. Wave 37 — shared client IP helper.
7. Wave 38 — document/expose multi-worker metrics/singleflight limits.

Проблемы, которые требуют решения пользователя перед полным закрытием:

1. PROB-009 — достаточно ли documented boundary + metrics, или нужен Redis distributed singleflight?
2. PROB-010 — нужен ли полноценный production metrics backend, и какой?
3. PROB-012 — куда и как делать off-host backups?
4. PROB-013 — делать ли refactor-only waves сейчас или оставлять opportunistic.

Если цель — закрыть абсолютно всё без deferred items, добавить после Wave 38:

8. Wave 39 — Redis distributed singleflight.
9. Wave 40 — production metrics backend.
10. Wave 41 — backup/restore process.
11. Wave 42a/42b/42c — maintainability splits.
