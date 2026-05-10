# Project handoff for the next AI

> Drop this file when you start helping with the Kufar Analytics
> project. It captures the state of the codebase as of Wave 25.6
> (FE-M5 closed — the `!important` cleanup is finished). Waves 0–25.6
> are reflected here; what's still genuinely worth doing is listed
> below. The companion audit document is
> `DEEP_DIVE_REVIEW_COMPREHENSIVE.md` (gitignored) — it lists 186
> issues at four severities (29 CRITICAL / 54 HIGH / 73 MEDIUM /
> 30 LOW). Numbers in this file refer to those audit IDs.
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
  notifications via the two-phase dispatch pattern (Wave 23/24).
* **`frontend/`** — vanilla JS + plain CSS Mini App (no bundler,
  no JSX). Assets are served by nginx, see `nginx/default.conf`.
* **`migrations/`** — Alembic; head revision is `20260510_0006`.

**Deployment shape:** five Docker services (migrate one-shot
+ api + bot + scheduler + frontend) plus Redis 7 and Cloudflare
Tunnel. See `docker-compose.yml`.

**Test suite:** 509 tests, all green (one skipped — the
Postgres-gated round-trip smoke). Runner is `pytest`. Local
dev uses SQLite via `aiosqlite`; CI/PG via env override. Static
Alembic-chain checks (single head, walkable, unique IDs) plus
the Postgres-gated round-trip (skipped without
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

# bump cache-busting tags after touching frontend/js, frontend/css,
# frontend/sw.js, frontend/offline.html, frontend/index.html.
# Note: app_actions.js maintains its OWN hand-coded ?v= tags for the
# lazy-loaded AI modules — bump those by hand after running the script.
scripts/bump_static_version.sh

# regenerate the single-file style.css from frontend/css/parts/*.css
# (run after editing any partial — parts/ is the source of truth, the
# bundle is checked in for atomic-rollout reasons)
uv run python scripts/rebuild_css.py

# alembic head revision
uv run alembic -c migrations/alembic.ini current

# local dev (Redis + frontend container + cloudflared tunnel)
./start-local.sh --with-tunnel
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
  Sub-waves (e.g. 25.1, 25.2, 25.3) exist for hot-fixes that
  belong in the same "story" but each get their own commit.
* **Comments use audit IDs as anchors.** When you fix an issue,
  reference the ID inline: `# FE-H7: defence in depth on top of
  the existing Origin check`. Future readers can grep for the
  ID and find both the audit entry and the fix.
* **Backward-compat re-exports.** When a module is split (e.g.
  `api_ai.js` in Wave 25, `ai_service.py` in Wave 18,
  `ai_analysis.py` in Wave 10), keep the old import surface
  working via re-exports or factory orchestration rather than
  rewriting every caller. Documented re-exports / public APIs
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
* **`closeModalAnimated()` is the ONLY modal-close path.** Every
  modal close MUST go through it — direct `modalEl.hidden =
  true` skips scroll-lock release, focus-trap cleanup, and the
  inert-siblings restore, freezing the UI. Wave 25.5 added a
  static regression test that scans `api_events.js` for any
  `xxxModal.hidden = true` pattern to catch this class of bug.

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
3. **509 tests must stay green.** If a refactor breaks tests,
   either (a) update the tests with audit-ID comments explaining
   why, or (b) roll back the refactor. Do not commit with a red
   suite.
4. **`.env`, `DEEP_DIVE_REVIEW_COMPREHENSIVE.md` and `notmyfault.md`
   are in `.gitignore`** — never commit them, even if you edit
   them locally.

---

## 5. What's been fixed (Waves 0–25.6)

29 of 29 **CRITICAL** items are either closed in code or
explicitly deferred per user policy (secret rotation, HTTPS via
Cloudflare). 51 of 54 **HIGH** items closed; the remaining 3 are
operational (CD pipeline, monitoring, off-host backups). 50 of 73
**MEDIUM** items closed; the remaining 23 are mostly long-tail
ergonomic improvements that don't materially affect users
(internal refactors, design polish, JS-build-system / TS adoption).

| Wave  | Commit  | Theme                                                       |
|-------|---------|-------------------------------------------------------------|
| 0     | 80271c9 | `.gitignore` + redact secrets                              |
| 1     | 2e0692a | CORS/CSRF, Redis auth, AUTH_BYPASS, CSP, limiter           |
| 2     | a4beeae | Race-safe singleflight, shadow stores, breaker             |
| 4     | c0e6372 | Resource limits, safe stop, O(n²) cluster stats            |
| 5a    | 1d2fc8d | initData replay, blacklist, prompt-injection log           |
| 5b    | 7790a66 | Atomic upsert, transactions, Lua rate-limit                |
| 5c    | 156a4b9 | Multi-worker, bounded pools, perf hardening                |
| 5d    | 8959d36 | Extract guidance JSON, ai_audit_log cleanup                |
| 6     | b390a08 | a11y + CSRF X-Requested-With + structured logging          |
| 7     | 2741f77 | Frontend request cancellation + signal cleanup             |
| 8     | dec973d | DB CHECK + audit retention wiring                          |
| 9     | 63a9e20 | Account deletion really clears every namespace             |
| 10    | e06c1b3 | Split `ai_analysis.py` god-file into 4 services            |
| 11    | eeba8d9 | CSP frame-ancestors / cache-busting / front image          |
| 12    | e603bd8 | MEDIUM/LOW dead code + type-safety sweep                   |
| 13    | db55f04 | docs + CI security/parallelisation + compose migrate       |
| 14    | 58f609d | test sweep — Alembic DAG checks + tightened assertions     |
| 15    | 9dda1a6 | backend data integrity + UX (pagination, typed delete, FOR UPDATE) |
| 16    | 9c39e82 | backend perf (AI semaphore, alias regex, graceful SIGTERM) |
| 17    | 6531622 | DB tuning (LIFO pool, drop indexes, UNIQUE consents, widen links) |
| 18    | 3b6a526 | split `ai_service.py` god-file into prompts/sanitize/dedupe |
| 19    | be5939f | extract `ai_marketplace.py` lexicon to JSON                |
| 20    | b444ff1 | frontend perf (image onerror, SW max-age, CSS preload)     |
| 21    | 016efd2 | frontend code quality (strip console.log, INFLIGHT_GUARD_MS) |
| 22    | ddeb8ca | a11y + UX polish (inert siblings, toast pause, offline page) |
| 23    | 0653a6d | scheduler — parallel lead-reminder sends (PERF-M3 partial) |
| 24    | cd8fa23 | scheduler — parallel tracker notifications (PERF-M3 complete) |
| 25    | 6db6ddf | frontend — split `api_ai.js` (1318 lines) into 4 modules (UX-M8) |
| 25.1  | e7a9bbe | nginx — defer host.docker.internal DNS (BROKEN, superseded) |
| 25.2  | 91bfac2 | nginx — revert 25.1 + correct fix via build.extra_hosts    |
| 25.3  | a037e2f | a11y — fix modal `inert` regression (Wave 22 walk-down)   |
| 25.4  | 1de3cf9 | css — FE-M5 partial (drop 3 dup body backgrounds, annotate keepers) |
| 25.5  | 9064985 | frontend — fix Listing Assistant Esc freeze (broken close path) |
| 25.6  | 6f5f244 | css — FE-M5 complete (drop 13 dead-weight `!important`)   |

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

### HIGH — partially done

* **DB-H3** `ai_audit_log` partitioning. Today we just have a
  nightly `cleanup_ai_audit_log` (Wave 8). Real fix is
  `PARTITION BY RANGE (created_at)` with monthly children, but
  that's a non-trivial migration with a backfill — postponed.

### MEDIUM — what's left after Waves 12–25.6

After 12 themed sweeps + the 25.x sub-wave run, the remaining 23
MEDIUM items split into two buckets:

**Genuinely-impactful, deferred (0):** — all closed.

* ~~**PERF-M3**~~ scheduler notification fan-out — closed by
  Waves 23 (lead reminders) + 24 (tracker notifications). Both
  now use a two-phase "collect during DB pass, dispatch after
  commit with bounded concurrency" pattern. Wall-clock at
  scale: 45 s → ~9 s per tick.
* ~~**UX-M8**~~ — closed by Wave 25. `api_ai.js` was split from
  a 1318-line god-file into 4 modules (`api_ai_modal.js`,
  `api_ai_render.js`, `api_ai_pdf.js`, plus the orchestrator
  `api_ai.js` at ~224 lines). Cross-module state via a tiny
  `aiCtx` object. Public API unchanged.
* ~~**FE-M5**~~ — closed by Waves 25.4 + 25.6. The audit's 31
  `!important` declarations are now down to 14, all of them
  W3C-canonical (`prefers-reduced-motion`, `[hidden]`,
  `[x-cloak]`) or Telegram-WebApp defensive (`body { background,
  color }`, `body.modal-open { overflow }` — beat the inline
  styles `app_core.js` and Telegram's BottomSheet set on body).
  Each kept declaration is annotated inline with WHY it must
  stay.

**Internal refactor / low user impact (~5):**

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

**Long tail (~18):** mostly operational/CI, design polish,
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
  collector.py                     APScheduler jobs (~1600 lines after Waves 23+24).
                                   Wave 23 added _send_message_classified helper
                                   and parallel check_reminders (Semaphore=5).
                                   Wave 24 added _TrackerNotifyJob +
                                   _dispatch_tracker_notifications: collect during
                                   the DB pass, dispatch after commit with bounded
                                   per-user concurrency (cap=5). Per-user serial,
                                   across-user parallel, single bulk Tracker
                                   deactivation on blocked-user discovery.

frontend/
  index.html                       Mini-App shell (FE-M1 preload tag, 7 modal surfaces).
                                   Modals live inside <div id="app-root">, NOT
                                   as direct body children — this matters for
                                   the inert-walk algorithm in dom_helpers.js.
  offline.html                     Wave 22: SW fallback page
  sw.js                            CACHE_VERSION rafuk-cache-v7, max-age, offline pre-cache
  js/
    api_core.js                    fetch wrapper, X-Requested-With, deleteJson(url, payload?)
    api_listings.js                search() + abort hooks
    api_events.js                  event binders, debounce, global Esc handler
                                   (Wave 25.5: LA branch removed — listing-assistant
                                    has its own keydown handler)
    api_*.js                       per-domain
    api_ai.js                      AI flow orchestrator (~224 lines after Wave 25).
                                   Owns the loadAIAnalysis polling loop,
                                   _showAIError glue, aiCtx state, PDF button binding.
    api_ai_modal.js                Wave 25: modal lifecycle + progress UI
                                   (~285 lines). Encapsulates _progressFrame,
                                   _aiProgress, LOADING_STEPS, STAGE_LABELS.
                                   closeAIModal lives HERE — bumps aiCtx.pollSession
                                   to cancel an in-flight poll.
    api_ai_render.js               Wave 25: DOM-build helpers + renderAIModalResult
                                   (~526 lines). Owns SECTION_IDS.
    api_ai_pdf.js                  Wave 25: printable HTML / PDF export
                                   (~388 lines). Reads aiCtx.lastData on click.
    api_listing_assistant.js       Has its OWN Esc keydown handler that calls
                                   closeModal() correctly (Wave 25.5 fixed the
                                   conflict with the global Esc handler).
    app.js                         analyticsApp() bootstrap
    app_actions.js                 cross-module action object + typed-delete-confirm modal.
                                   ensureAiLoaded() now Promise.all's 5 scripts
                                   (3 AI sub-modules + orchestrator + listing
                                   assistant) — share one ?v= stamp.
    app_core.js                    Theme handling. document.body.style.backgroundColor
                                   is set HERE on theme switch — this is what
                                   the kept-!important on body in tokens.css beats.
    render_*.js                    pure-DOM renderers (incl. toast pause-on-hover)
    render_modals.js               closeDetailModal, closeExpensesModal — both
                                   route through closeModalAnimated().
    render_card_builders.js        buildMediaNode (loading=lazy + onerror fallback)
    dom_helpers.js                 trapFocus, openModalAnimated, closeModalAnimated,
                                   _applyInertToSiblings (Wave 25.3 walk-down
                                   algorithm — walks <body> → modal, inerting
                                   siblings at each level, lifting inert from
                                   path elements for nested modals),
                                   _restoreInertSiblings (un-inerts newly-inerted +
                                   re-inerts lifted),
                                   INFLIGHT_GUARD_MS constant.
    virtual_list.js                bounded recycle pool
  css/
    parts/tokens.css               Design tokens (Wave 6 contrast bumps).
                                   8 !important after FE-M5: [hidden], [x-cloak],
                                   reduced-motion (4), body { bg, color }.
                                   Each annotated inline with rationale.
    parts/modals.css               4 !important after FE-M5: body.modal-open
                                   overflow + 3 reduced-motion. Annotated.
    parts/pipeline.css             0 !important after Wave 25.6.
    parts/brand.css                1 !important after FE-M5: reduced-motion
                                   modal animation.
    parts/states.css               1 !important: .recent-strip[hidden] overrides
                                   the global [hidden] for animation purposes.
    style.css                      Bundled stylesheet (~9.5K lines, ~54K).
                                   Source of truth lives in parts/ —
                                   run scripts/rebuild_css.py after edits.

nginx/
  default.conf                     /api/ proxy_pass uses literal host.docker.internal
                                   (Wave 25.2: NOT a variable + resolver — that
                                   ignored extra_hosts in runtime). Build-time
                                   nginx -t works because docker-compose.yml
                                   build.extra_hosts injects a stub.

docker-compose.yml                 build.extra_hosts on frontend service (Wave 25.2),
                                   runtime extra_hosts: host-gateway on all services.

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
  test_scheduler_collector.py      Waves 23 + 24: reminder retry test +
                                   4 dispatch helper tests (empty no-op,
                                   sends-all, blocked-skips-rest + bulk
                                   deactivate, retry-doesn't-count).
  test_app_js_syntax.py            Static JS checks. New behavioural tests:
                                   - test_inert_walk_handles_nested_modals
                                     (Wave 25.3: runs the actual JS against a
                                     DOM mock via Node, asserts flat/nested/
                                     stacked modal cases)
                                   - test_no_modal_close_bypasses_close_modal_animated
                                     (Wave 25.5: scans api_events.js for any
                                     xxxModal.hidden = true pattern)
                                   - test_close_ai_modal_cancels_polling
                                     (rewritten in Wave 25 — looks in
                                     api_ai_modal.js now, asserts aiCtx.pollSession)
  ...                              509 total
```

---

## 8. Suggested next moves for the next AI

The themed sweeps are done. The big-impact MEDIUM items
(`PERF-M3`, `UX-M8`, `FE-M5`) are all closed. What's left is:

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

3. **Long-tail MEDIUM / LOW** — about 18 MEDIUM and 28 LOW
   items remain, mostly ergonomic. Pick whichever one has
   real user-visible impact or aligns with whatever else the
   user is touching. Some candidates:
   - `BE-M8` `_AC` analysis-context refactor (internal only,
     low risk).
   - Long-tail integration tests for the FE AI flow (currently
     near-zero automated coverage despite Wave 25's split).
   - Design-polish items if the user opens a UI direction.

Whatever you pick: stay in the wave-per-commit cadence, mark
each fix with its audit ID, and ping the user when something
crosses an "ops decision" line (e.g. introducing a new
external service, secret rotation, or a destructive migration).

---

## 9. Recent operational gotchas (Wave 25.x lessons)

These bit us during the Wave 25.x sub-wave run — worth knowing
so you don't repeat them:

* **`extra_hosts` doesn't exist at image-build time.** Wave 25.1
  tried to "fix" `RUN nginx -t` failing on `host.docker.internal`
  by switching nginx to a variable `proxy_pass` + `resolver
  127.0.0.11`. That made the build pass but **broke runtime**
  because Docker's embedded DNS at 127.0.0.11 only resolves
  Docker-network service names — NOT `/etc/hosts` entries from
  `extra_hosts`. Symptom: every `/api/*` request 502'd. Wave
  25.2 reverted and used `build.extra_hosts` in compose to
  inject the stub at build time while leaving runtime to use
  the real literal lookup via libc + `/etc/hosts`. Lesson:
  understand which DNS path nginx actually takes (literal →
  libc → /etc/hosts vs variable → resolver → external DNS).
* **`inert` propagates to descendants.** Wave 22 added
  `_applyInertToSiblings` that assumed modals were direct
  children of `<body>`. They aren't — they live inside
  `<div class="app" id="app-root">`. The old loop inerted
  `#app-root`, and `inert` inherited down to the modal itself,
  freezing scroll/clicks INSIDE the modal. Wave 25.3 rewrote
  the algorithm to walk DOWN from body to the modal, inerting
  siblings at each level and lifting inert from the path
  itself (the modal + its ancestors). Static behaviour test
  added.
* **Every modal close must go through `closeModalAnimated`.**
  Wave 25.5 fixed the listing-assistant Esc handler. The bug:
  global Esc handler had `laModal.hidden = true` direct
  shortcut. This bypassed `unlockBodyScroll`,
  `_focusTrapCleanup`, and `_restoreInertSiblings`. Symptom:
  whole UI froze (page can't scroll, nothing on the page is
  clickable). Static regression test added — scans
  api_events.js for any `xxxModal.hidden = true` pattern.
* **Bundle order matters for `!important` cleanup.** FE-M5
  part 2 (Wave 25.6) found that `.consent-modal-content`'s
  `!important` was dead weight because `.modal-content`
  (pipeline.css, loads later) was already winning by source
  order with the same value. Always trace the actual cascade
  in the BUNDLED `style.css`, not the partial in isolation.
* **`scripts/bump_static_version.sh` only rewrites
  `frontend/index.html`.** Hand-coded `?v=` tags in
  `app_actions.js` (for the lazy-loaded AI sub-modules) need
  to be updated separately — do it manually with sed after
  running the bump script.
