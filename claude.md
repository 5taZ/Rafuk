# Project handoff for the next AI

> Drop this file when you start helping with the Kufar Analytics
> project. It captures the state of the codebase as of Wave 25
> (UX-M8 complete — `api_ai.js` split into 4 modules). What's
> been fixed across Waves 0–25 is reflected here, what's still
> genuinely worth doing is listed below. The companion audit
> document is `DEEP_DIVE_REVIEW_COMPREHENSIVE.md` (gitignored) — it
> lists 186 issues at four severities (29 CRITICAL / 54 HIGH /
> 73 MEDIUM / 30 LOW). Numbers in this file refer to those audit IDs.
>
> The full per-wave change log lives in `CHANGELOG.md` — that's the
> authoritative reference for "what changed when". This file is the
> shorter mental-model handoff.

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
* **`migrations/`** — Alembic; head revision is `20260510_0006`.

**Deployment shape:** five Docker services (migrate one-shot
+ api + bot + scheduler + frontend) plus Redis 7 and Cloudflare
Tunnel. See `docker-compose.yml`.

**Test suite:** 500 tests, all green. Runner is `pytest`. Local
dev uses SQLite via `aiosqlite`; CI/PG via env override. Static
Alembic-chain checks (single head, walkable, unique IDs) plus a
Postgres-gated round-trip smoke (skipped without
`TEST_DATABASE_URL`).

---

## 2. How to run things

```bash
# install / activate venv
uv sync --extra dev

# tests (must stay green before committing)
uv run pytest --tb=short

# single file
uv run pytest tests/test_consent.py -x --tb=short

# linter (CI matches this — ruff format is intentionally NOT enforced)
uv run ruff check .

# bump cache-busting tags after touching frontend/js, frontend/css, frontend/sw.js, frontend/offline.html, frontend/index.html
scripts/bump_static_version.sh

# alembic head revision
uv run alembic -c migrations/alembic.ini current
```

CSRF-required tests use the `_CSRFTestClient` shim in
`tests/conftest.py` — it auto-injects `Origin` and
`X-Requested-With`. Async tests using `httpx.AsyncClient` directly
(e.g. `tests/test_consent.py`) must set both headers manually.

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
  `api/services/ai_service.py` in Wave 18, `ai_analysis.py` in
  Wave 10), keep the old import surface working via re-exports
  rather than rewriting every caller. Documented re-exports
  include a comment listing every importer so you know what
  would break if you remove them.
* **`gh-flavoured commit trailer`** — every commit ends with:

  ```
  Generated with [Devin](https://cli.devin.ai/docs)

  Co-Authored-By: Devin <158243242+devin-ai-integration[bot]@users.noreply.github.com>
  ```

* **Code style:** Compact, dense, idiomatic. Avoid excessive
  try/except. Comments explain *why*, not *what*. `ruff format`
  is intentionally NOT enforced — see AGENTS.md.

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
3. **500 tests must stay green.** If a refactor breaks tests,
   either (a) update the tests with audit-ID comments explaining
   why, or (b) roll back the refactor. Do not commit with a red
   suite.
4. **`.env`, `DEEP_DIVE_REVIEW_COMPREHENSIVE.md` and `notmyfault.md`
   are in `.gitignore`** — never commit them, even if you edit
   them locally.

---

## 5. What's been fixed (Waves 0–22)

29 of 29 **CRITICAL** items are either closed in code or
explicitly deferred per user policy (secret rotation, HTTPS via
Cloudflare). 51 of 54 **HIGH** items closed; the remaining 3 are
operational (CD pipeline, monitoring, off-host backups). 46 of 73
**MEDIUM** items closed; the remaining 27 are mostly
deliberately-deferred (god-file splits with risk, `!important` CSS
migration that needs visual review, scheduler loop architectural
refactor) plus a long tail of ergonomic improvements that don't
affect users.

| Wave | Commit  | Theme                                                       |
|------|---------|-------------------------------------------------------------|
| 0    | 80271c9 | `.gitignore` + redact secrets                              |
| 1    | 2e0692a | CORS/CSRF, Redis auth, AUTH_BYPASS, CSP, limiter           |
| 2    | a4beeae | Race-safe singleflight, shadow stores, breaker             |
| 4    | c0e6372 | Resource limits, safe stop, O(n²) cluster stats            |
| 5a   | 1d2fc8d | initData replay, blacklist, prompt-injection log           |
| 5b   | 7790a66 | Atomic upsert, transactions, Lua rate-limit                |
| 5c   | 156a4b9 | Multi-worker, bounded pools, perf hardening                |
| 5d   | 8959d36 | Extract guidance JSON, ai_audit_log cleanup                |
| 6    | b390a08 | a11y + CSRF X-Requested-With + structured logging          |
| 7    | 2741f77 | Frontend request cancellation + signal cleanup             |
| 8    | dec973d | DB CHECK + audit retention wiring                          |
| 9    | 63a9e20 | Account deletion really clears every namespace             |
| 10   | e06c1b3 | Split `ai_analysis.py` god-file into 4 services            |
| 11   | eeba8d9 | CSP frame-ancestors / cache-busting / front image          |
| 12   | e603bd8 | MEDIUM/LOW dead code + type-safety sweep                   |
| 13   | db55f04 | docs + CI security/parallelisation + compose migrate       |
| 14   | 58f609d | test sweep — Alembic DAG checks + tightened assertions     |
| 15   | 9dda1a6 | backend data integrity + UX (pagination, typed delete, FOR UPDATE) |
| 16   | 9c39e82 | backend perf (AI semaphore, alias regex, graceful SIGTERM) |
| 17   | 6531622 | DB tuning (LIFO pool, drop indexes, UNIQUE consents, widen links) |
| 18   | 3b6a526 | split `ai_service.py` god-file into prompts/sanitize/dedupe |
| 19   | be5939f | extract `ai_marketplace.py` lexicon to JSON                |
| 20   | b444ff1 | frontend perf (image onerror, SW max-age, CSS preload)     |
| 21   | 016efd2 | frontend code quality (strip console.log, INFLIGHT_GUARD_MS) |
| 22   | ddeb8ca | a11y + UX polish (inert siblings, toast pause, offline page) |

For the *exact* mapping of audit IDs → wave, see `CHANGELOG.md`
or the per-commit messages — each one names every ID it touched.
`git log --grep="BE-M11"` finds the wave that closed any given
audit item.

---

## 6. What's still open

### CRITICAL — operational, no code change planned

* **SEC-C1** secret rotation — *user policy: skip*.
* **INF-C2** HTTPS/TLS — *cloudflared already proxies, deeper TLS
  is a deployment concern*.
* **INF-C3** `pkill -f` in `scripts/` — *local dev scripts only,
  not used in production; the script already prefers PID files*.

### HIGH — operational, decision pending

* **SEC-H2** Docker Secrets instead of `environment:` — needs a
  Compose Spec migration + secret-store decision (Vault?
  Doppler?). Not yet started.
* **INF-H1** PostgreSQL `pg_dump` cron — needs an off-host backup
  target.
* **INF-H2** Monitoring (Prometheus / UptimeRobot / similar).
* **INF-H3** Push Docker image to a registry from CI.
* **INF-H4** CD pipeline (`deploy.yml` GitHub Action).

(One HIGH closed indirectly: UX-H1 verified-via-Wave-22 a11y posture.)

### HIGH — partially done

* **DB-H3** `ai_audit_log` partitioning. Today we just have a
  nightly `cleanup_ai_audit_log` (Wave 8). Real fix is
  `PARTITION BY RANGE (created_at)` with monthly children, but
  that's a non-trivial migration with a backfill — postponed.

### MEDIUM — what's left after Waves 12–22

After 11 themed sweeps, the remaining 27 MEDIUM items split into
three buckets:

**Genuinely-impactful, deferred for scope or risk reasons (1):**

* **FE-M5** — 31 `!important` CSS declarations across
  `tokens.css`, `pipeline.css`, `modals.css`, `brand.css`,
  `states.css`. Most fight Telegram-WebApp inline styles or the
  global `[hidden]` attribute. Each removal needs paired visual
  review, so a bulk strip isn't safe.

**Internal refactor / low user impact (a handful):**

* **BE-M8** — `_AC` analysis-context class is a god-object with
  ~20 mutable attributes. Internal-only; works fine as is.
* **DB-M5** — historical 4 migrations modify the same CHECK on
  `tracker_events`. Cosmetic — the chain is well-formed, just
  noisy.
* **UX-M3** — no TypeScript / JSDoc type annotations on the
  vanilla-JS frontend. Major rework.
* **UX-M6** — no JS build system. Same scope as adding TS.
* **DESIGN-M4** — Telegram initData (1-2 KB) sent on every API
  call. Inherent to the auth model; would need a
  short-lived-token redesign.

**Long tail (~20):** mostly operational/CI, design polish,
migration cleanup, integration test gaps. See
`DEEP_DIVE_REVIEW_COMPREHENSIVE.md` (gitignored) for the full
list and `CHANGELOG.md` "Audit progress" for the running tally.

### LOW (28 of 30 still open)

`README.md` is no longer empty (Wave 13). Most remaining LOW
items are dead-import polish, design micro-tweaks, and a few
Redis-encryption-at-rest / `Permissions-Policy` extras that are
configuration-side, not code.

---

## 7. Where things live (quick map)

```
api/
  main.py                          FastAPI app factory + middlewares
  config.py                        Settings (pydantic) + _is_local_database_url
  models.py                        SQLAlchemy ORM
  schemas.py                       Pydantic request/response (incl. AccountDeletionConfirmation)
  database.py                      get_engine — pool_use_lifo=True (Wave 17)
  middleware/telegram_auth.py      HMAC verify of initData
  routers/
    ai_analysis.py                 Wave 10: thin router + re-exports
    ai_listing_assistant.py        Listing assistant endpoint
    ai_tools.py                    Negotiate / price advice
    consent.py                     Law-91-Z consent / erasure (BE-M3 typed-delete + DB-M6 IntegrityError handler)
    workflow.py                    Leads/watchlist CRUD + refresh
    expenses.py                    Pagination via limit/offset (BE-M2)
    trackers.py                    create_tracker with FOR UPDATE on User row (BE-M16)
    listings.py / analytics.py / etc.
  services/
    ai_audit.py                    Wave 10
    ai_guards.py                   Wave 10 (consent / rate / coerce)
    ai_privacy.py                  Wave 10 (clear_user_ai_data)
    ai_shadow_store.py             Wave 10
    ai_analysis_pipeline.py        AI pipeline (still ~900 lines)
    ai_service.py                  AI client (1175 lines after Wave 18)
    ai_prompts.py                  Wave 18: 6 prompt templates
    ai_sanitize.py                 Wave 18: sanitize_user_text + injection regex
    ai_dedupe.py                   Wave 18: paraphrase collapsing
    ai_marketplace.py              1644 lines after Wave 19 (was 1824)
    cache.py                       Redis + MemoryCache backends
    kufar_client.py                Kufar HTTP client
    parallel_kufar.py              Parallel Kufar fanout helper
    workflow_store.py              ensure_user, leads CRUD
    session_security.py            initData replay tracking + blacklist
    aggregator.py                  Wave 16: SEARCH_ALIASES → precompiled regex
    history_service.py             Wave 12: Decimal coercion in price-drop detection
  data/category_guidance.json      Wave 5d: extracted guidance
  services/data/
    category_guidance.json         (moved here in earlier wave)
    marketplace_lexicon.json       Wave 19: 10 token groups
  healthcheck.py                   Wave 6: /health/{live,ready}
  logging_config.py                Wave 6: JSON structured logs

bot/
  main.py                          Aiogram bot entry-point
  api_client.py                    Bot's HTTP client (singleton)
  handlers/{start,analytics,callbacks}.py

scheduler/
  collector.py                     APScheduler jobs (~1300 lines, PERF-M4 SIGTERM handler in main())

frontend/
  index.html                       Mini-App shell (FE-M1 preload tag, 7 modal surfaces)
  offline.html                     Wave 22: SW fallback page
  sw.js                            CACHE_VERSION rafuk-cache-v7, max-age, offline pre-cache
  js/
    api_core.js                    fetch wrapper, X-Requested-With, deleteJson(url, payload?)
    api_listings.js                search() + abort hooks
    api_events.js                  event binders, debounce
    api_*.js                       per-domain
    api_ai.js                      AI modal flow (still 1325 lines — UX-M8 deferred)
    app.js                         analyticsApp() bootstrap
    app_actions.js                 cross-module action object + typed-delete-confirm modal
    render_*.js                    pure-DOM renderers (incl. toast pause-on-hover)
    render_card_builders.js        buildMediaNode (loading=lazy + onerror fallback)
    dom_helpers.js                 trapFocus, openModalAnimated (with inert siblings),
                                   INFLIGHT_GUARD_MS constant
    virtual_list.js                bounded recycle pool
  css/
    parts/tokens.css               Design tokens (Wave 6 contrast bumps)
    style.css                      Bundled stylesheet (~9.5K lines, ~54K)

migrations/
  versions/
    20260510_0006_wave17_db_tuning.py        ← head, current
    20260510_0005_deal_expenses_check.py     ← Wave 8
    20260510_0004_ai_audit_cleanup.py        ← Wave 5d
    20260510_0003_add_price_type.py          ← price-type fix
    20260509_*.py                            ← DB hardening

tests/
  test_migrations.py               Wave 14: Alembic chain checks + Postgres round-trip
  conftest.py                      _CSRFTestClient shim, env setup
  test_consent.py                  4 BE-M3 tests for typed account deletion
  test_models.py                   Pin post-DB-M4 index set
  ...                              500 total
```

---

## 8. Suggested next moves for the next AI

The themed sweeps are done. What's left is bigger / more deliberate:

1. **DB-H3 (`ai_audit_log` partitioning)** — only HIGH still
   actually open. Plan: monthly partitions on `created_at`,
   24-month default rolling window, default partition for
   safety. Migration needs `pg_partman` or hand-rolled DDL +
   backfill. The Wave 8 cleanup function buys time but the
   real fix is partitioning.

2. **Operational HIGHs (INF-H1/2/3/4 + SEC-H2)** — these need
   ops-side decisions (backup target, monitoring stack, secret
   store, deployment target) before code can land. Worth
   surfacing to the user when one of these blockers comes up
   organically.

Whatever you pick: stay in the wave-per-commit cadence, mark
each fix with its audit ID, and ping the user when something
crosses an "ops decision" line (e.g. introducing a new
external service, secret rotation, or a destructive migration).
