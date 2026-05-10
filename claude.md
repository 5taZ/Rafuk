# Project handoff for the next AI

> Drop this file when you start helping with the Kufar Analytics
> project. It captures the state of the codebase as of commit
> `eeba8d9` (Wave 11), what we've already fixed, what's still open,
> and the conventions used so far. The companion audit document is
> `DEEP_DIVE_REVIEW_COMPREHENSIVE.md` — it lists 186 issues at four
> severities (29 CRITICAL / 54 HIGH / 73 MEDIUM / 30 LOW). Numbers
> in this file refer to the IDs from that document.

---

## 1. What this project is

Kufar Analytics: a Telegram Mini App + Telegram bot that scrapes
the Belarusian classifieds site **Kufar** for listings, runs
analytics + AI-assisted decisioning over them, and surfaces
"deal-finder" workflows to the end user.

**Components:**

* **`api/`** — FastAPI backend, talks to PostgreSQL + Redis.
* **`bot/`** — Aiogram 3 Telegram bot (long-polling).
* **`scheduler/collector.py`** — APScheduler process that scrapes
  Kufar on a schedule, persists snapshots, fans out
  notifications.
* **`frontend/`** — vanilla JS + plain CSS Mini App (no bundler,
  no JSX). Assets are served by nginx, see `nginx/default.conf`.
* **`migrations/`** — Alembic; head revision is `20260510_0005`.

**Deployment shape:** four Docker services (api / bot / scheduler
/ frontend) plus Redis 7 and Cloudflare Tunnel. See
`docker-compose.yml`.

**Test suite:** 494 tests, all green. Runner is `pytest`. Local
dev uses SQLite via `aiosqlite`; CI/PG via env override.

---

## 2. How to run things

```bash
# activate venv
source .venv/bin/activate

# tests (must stay green before committing)
pytest --tb=short

# single file
pytest tests/test_consent.py -x --tb=short

# bump cache-busting tags after touching frontend/js or frontend/css
scripts/bump_static_version.sh

# alembic head revision
alembic -c migrations/alembic.ini history --verbose | head -25
```

Tests rely on a custom `_CSRFTestClient` shim in
`tests/conftest.py` that auto-injects `Origin` and
`X-Requested-With` headers on every request — needed because of
the CSRF middleware (FE-H7). Async tests that use httpx
`AsyncClient` directly (e.g. `tests/test_consent.py`) must set
both headers manually.

---

## 3. Conventions established by previous waves

* **One atomic commit per wave.** Commit messages explain *why*
  the change was needed (with the audit ID, e.g. "BE-H8: 2-query
  pattern for leads+expenses → JOIN"), not just what it does.
* **Comments use audit IDs as anchors.** When you fix an issue,
  reference the ID inline: `# FE-H7: defence in depth on top of
  the existing Origin check`. Future readers can grep for the
  ID and find both the audit entry and the fix.
* **Backward-compat re-exports.** When a module is split (e.g.
  `api/routers/ai_analysis.py` in Wave 10), keep the old import
  surface working via re-exports rather than rewriting every
  caller. Documented re-exports include a comment listing every
  importer so you know what would break if you remove them.
* **`gh-flavoured commit trailer`** — every commit ends with:

  ```
  Generated with [Devin](https://cli.devin.ai/docs)

  Co-Authored-By: Devin <158243242+devin-ai-integration[bot]@users.noreply.github.com>
  ```

* **Code style:** Compact, dense, idiomatic. Avoid excessive
  try/except. Comments explain *why*, not *what*.

---

## 4. Hard constraints from the user

1. **DO NOT rotate secrets.** The user has accepted the risk that
   the bot token, AI API key, DB credentials and proxy creds in
   `.env` are unrotated even though they were briefly exposed in
   `DEEP_DIVE_REVIEW_COMPREHENSIVE.md` before redaction. `.env`
   is gitignored and the user is fine with that being the only
   line of defence.
2. **Local Redis on port 6380 has no password.** `REDIS_URL`
   stays plaintext for the host; `DOCKER_REDIS_URL` includes the
   password via `REDIS_PASSWORD` env var.
3. **494 tests must stay green.** If a refactor breaks tests,
   either (a) update the tests with audit-ID comments explaining
   why, or (b) roll back the refactor. Do not commit with a red
   suite.
4. **`.env`, `DEEP_DIVE_REVIEW_COMPREHENSIVE.md` and `notmyfault.md`
   are in `.gitignore`** — never commit them, even if you edit
   them locally.

---

## 5. What's already been fixed (Waves 0–11)

29 of 29 **CRITICAL** items are either closed in code or
explicitly deferred per user policy (secret rotation, HTTPS via
Cloudflare). 50 of 54 **HIGH** items closed; the remaining 4 are
operational (CI/CD, monitoring, backups) plus one partial
(DB-H3 ai_audit_log partitioning — cleanup function works, true
range-partitioning not done).

| Wave | Commit  | Theme                                              |
|------|---------|----------------------------------------------------|
| 0    | 80271c9 | .gitignore + redact secrets                        |
| 1    | 2e0692a | CORS/CSRF, Redis auth, AUTH_BYPASS, CSP, limiter   |
| 2    | a4beeae | Race-safe singleflight, shadow stores, breaker     |
| 4    | c0e6372 | Resource limits, safe stop, O(n²) cluster stats    |
| 5a   | 1d2fc8d | initData replay, blacklist, prompt-injection log   |
| 5b   | 7790a66 | Atomic upsert, transactions, Lua rate-limit        |
| 5c   | 156a4b9 | Multi-worker, bounded pools, perf hardening        |
| 5d   | 8959d36 | Extract guidance JSON, ai_audit_log cleanup        |
| 6    | b390a08 | a11y + CSRF X-Requested-With + structured logging  |
| 7    | 2741f77 | Frontend request cancellation + signal cleanup     |
| 8    | dec973d | DB CHECK + audit retention wiring                  |
| 9    | 63a9e20 | Account deletion really clears every namespace     |
| 10   | e06c1b3 | Split ai_analysis.py god-file into 4 services      |
| 11   | eeba8d9 | CSP frame-ancestors / cache-busting / front image  |

For the *exact* mapping of audit IDs → wave, see the commit
messages — each one names every ID it touched.

---

## 6. What's still open (in priority order)

### CRITICAL — operational, no code change planned

* **SEC-C1** secret rotation — *user policy: skip*.
* **INF-C2** HTTPS/TLS — *cloudflared already proxies, deeper TLS
  is a deployment concern*.
* **INF-C3** `pkill -f` in `scripts/` — *local dev scripts only,
  not used in production*.

### HIGH — operational

* **SEC-H2** Docker Secrets instead of `environment:` — needs a
  Compose Spec migration + secret-store decision (Vault?
  Doppler?). Not yet started.
* **INF-H1** PostgreSQL `pg_dump` cron — needs an off-host backup
  target.
* **INF-H2** Monitoring (Prometheus / UptimeRobot / similar).
* **INF-H3** Push Docker image to a registry from CI.
* **INF-H4** CD pipeline (`deploy.yml` GitHub Action).

### HIGH — partially done

* **DB-H3** `ai_audit_log` partitioning. Today we just have a
  nightly `cleanup_ai_audit_log` (Wave 8). Real fix is
  `PARTITION BY RANGE (created_at)` with monthly children, but
  that's a non-trivial migration with a backfill — postponed.

### MEDIUM (73) — biggest remaining buckets

The full breakdown is in `DEEP_DIVE_REVIEW_COMPREHENSIVE.md`
sections "🟡 MEDIUM — Address in Next Sprint". Highlights:

**Backend (18):**

* `api/routers/ai_analysis.py:periodic_prune_shadow_stores` —
  the *outer* loop is fine after the Wave 10 split, but the
  audit also flagged dead code in the same area; sweep when
  touching the AI services again.
* `api/routers/expenses.py:66-89` — `get_expenses` has no
  pagination. Today the table is small but a power-user with
  hundreds of leads would feel it.
* `api/routers/consent.py:260-313` — account deletion has no
  user-confirmation step. UX call: ask before adding a "type
  your username to confirm" flow.
* `api/services/ai_analysis_pipeline.py:359-437` — parallel
  `search` calls inside the AI pipeline have no semaphore. Let's
  bound them to keep us out of Kufar's rate-limit jail.
* `api/routers/image_proxy.py:147-177` — CPU-bound Pillow encode
  inside the request loop. Move to a thread pool / cache aggressively.
* `api/services/history_service.py:80-86` — nested transaction
  rollback is incomplete on error paths.
* `api/services/ai_marketplace.py` — still ~1400 lines of mostly
  static dicts; Wave 5d extracted *guidance*, not the rest.
* `api/services/ai_service.py` — ~1800 lines, inline prompts.
  Same kind of split BE-C5 did for ai_analysis.py.
* Misc: `workflow.py:254-259` (status `"active"` not in
  `LeadStatusEnum`), `history_service.py:254-256` (Decimal vs
  float), `export.py:168-172` (dead code), `aggregator.py:166-175`
  (O(n*m) alias map), `listing_detail.py:75-76` (no cache),
  `config.py:59-66` (CIDR check incomplete), `trackers.py:141-207`
  (no optimistic locking).

**Frontend (14):**

* `frontend/index.html:8` — CSS bundle is ~54K, served
  synchronously. Could split critical CSS or use `media="print"
  onload="this.media='all'"` trick.
* `frontend/index.html:150-175` — AI modules load eagerly.
  Lazy-import on first AI interaction.
* `frontend/js/render_card_builders.js:30-45` — images are
  eager-loaded on first render. `loading="lazy"` on the `<img>`
  tag would defer below-the-fold thumbnails.
* `api_events.js ↔ app_actions.js` — circular dependency.
* `frontend/css/*` — 12+ `!important` in CSS. Slowly migrate
  away when we touch each component.
* `frontend/js/*` — many `console.log` left in production code.
* `frontend/js/virtual_list.js` — pool grows without bound.
* `frontend/js/sw.js` — cache version is manual; if forgotten,
  users pin stale assets (related to INF-H8 cache-busting we just
  scripted).

**Database (9):**

* No `pool_use_lifo=True` — connections age and recycle more
  often.
* Missing index `idx_lead_items_user_created` was added in Wave 5;
  audit flagged earlier rev. Verify this is current.
* `lead_items.url` `String(512)` — may not be enough for some
  Kufar URL params.

**Tests (6):**

* `tests/test_idor.py:test_user_b_cannot_delete_user_a_watchlist_item`
  expects 204 instead of 404 — likely buggy assertion.
* Some `assert resp.status_code in (200, 503)` patterns — too
  loose, hides regressions.
* Several `asyncio.sleep(0.01)` for "wait for semaphore" — flaky.
* No integration tests against real PG, no migration tests.

**Performance MEDIUM (6):**

* Singleflight is in-process — multi-worker setups still cache-miss.
* Scheduler sends Telegram notifications sequentially — 300
  notifications = 60s of head-of-line blocking. Parallelise with
  bounded concurrency.
* No graceful SIGTERM handling — DB transactions can be killed.

### LOW (30) — polish

* Permissions-Policy headers added in Wave 11 (some sub-bullets
  closed).
* `README.md` is empty.
* No `CHANGELOG.md`.
* Various dead imports (`ai_export.py`), duplicated code
  (`deal_workflow.py` ↔ `workflow.py`).

---

## 7. Where things live (quick map)

```
api/
  main.py                          FastAPI app factory + middlewares
  config.py                        Settings (pydantic)
  models.py                        SQLAlchemy ORM
  schemas.py                       Pydantic request/response
  middleware/telegram_auth.py      HMAC verify of initData
  routers/
    ai_analysis.py                 ← Wave 10: thin router + re-exports
    ai_listing_assistant.py        Listing assistant endpoint
    ai_tools.py                    Negotiate / price advice
    consent.py                     Law-91-Z consent / erasure
    workflow.py                    Leads/watchlist CRUD + refresh
    listings.py / analytics.py / etc.
  services/
    ai_audit.py                    ← Wave 10
    ai_guards.py                   ← Wave 10 (consent / rate / coerce)
    ai_privacy.py                  ← Wave 10 (clear_user_ai_data)
    ai_shadow_store.py             ← Wave 10
    ai_analysis_pipeline.py        AI pipeline (still ~1500 lines)
    ai_service.py                  AI client wrapper (~1800 lines)
    ai_marketplace.py              Static dicts (~1400 lines)
    cache.py                       Redis + MemoryCache backends
    kufar_client.py                Kufar HTTP client
    parallel_kufar.py              Parallel Kufar fanout helper
    workflow_store.py              ensure_user, leads CRUD
    session_security.py            initData replay tracking + blacklist
    aggregator.py                  Price stats / detect_price_type
    history_service.py             Trend reversal etc.
  healthcheck.py                   Wave 6: /health/{live,ready}
  logging_config.py                Wave 6: JSON structured logs
  data/category_guidance.json      Wave 5d: extracted guidance

bot/
  main.py                          Aiogram bot entry-point
  api_client.py                    Bot's HTTP client (singleton)
  handlers/{start,analytics,callbacks}.py

scheduler/
  collector.py                     APScheduler jobs (~1300 lines)

frontend/
  index.html                       Mini-App shell
  js/
    api_core.js                    fetch wrapper, X-Requested-With
    api_listings.js                search() + abort hooks
    api_events.js                  event binders, debounce
    api_*.js                       per-domain
    app.js                         analyticsApp() bootstrap
    app_actions.js                 cross-module action object
    render_*.js                    pure-DOM renderers
    dom_helpers.js                 trapFocus, longpress, ptr, …
  css/
    parts/tokens.css               Design tokens (Wave 6 contrast bumps)
    style.css                      Main stylesheet (~9K lines)

migrations/
  versions/
    20260510_0005_deal_expenses_check.py     ← Wave 8 head
    20260510_0004_ai_audit_cleanup.py        ← Wave 5d
    20260510_0003_add_price_type.py          ← price-type fix
    20260509_*.py                            ← DB hardening
```

---

## 8. Suggested next moves for the next AI

Pick one of:

1. **Backend MEDIUM cluster** — ai_marketplace / ai_service splits
   following the BE-C5 pattern. Keep the late-bound import trick
   for tests (`from api.routers import ai_analysis as _aa`).
2. **Frontend MEDIUM cluster** — lazy-load AI modules, swap eager
   image loads for `loading="lazy"`, dedupe escapeHtml
   (verify it's still single after Wave 7), strip `console.log`s.
3. **DB-H3 partitioning** — the only HIGH that's only partially
   addressed. Plan: monthly partitions on `ai_audit_log.created_at`,
   a 24-month default rolling window, default partition for
   safety. Migration needs `pg_partman` or hand-rolled DDL +
   backfill.
4. **Tests sweep** — fix the `expects 204 instead of 404` bug,
   tighten loose `in (200, 503)` assertions, add an integration
   harness.

Whatever you pick: stay in the wave-per-commit cadence, mark
each fix with its audit ID, and ping the user when something
crosses an "ops decision" line (e.g. introducing a new
external service, secret rotation, or a destructive migration).
