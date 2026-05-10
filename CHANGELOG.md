# Changelog

All notable changes to this project are tracked in this file. Entries
are organised as **waves** — atomic, themed commits — following the
audit document `DEEP_DIVE_REVIEW_COMPREHENSIVE.md` (gitignored). Each
wave is one git commit; audit IDs (`SEC-Cn`, `BE-Hn`, `FE-Mn`, …) link
each fix to the original entry in the audit so future readers can grep
both directions.

The format is loosely based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) but waves
replace the standard `Added/Changed/Fixed` buckets because most fixes
cut across all three.

## Unreleased

### Wave 15 — backend data integrity + UX _(this commit)_

* **BE-M2** — `GET /leads/{id}/expenses` now paginates via `limit`
  (1-500, default 100) and `offset` (≥0) query params. Previously
  unbounded; a power-user with hundreds of expense rows would
  return >256 KB of JSON in one shot. Default page size is plenty
  for the detail view; the cap is a backstop against runaway
  fetches.
* **BE-M3** — `DELETE /api/v1/account` requires a JSON body
  ``{"confirmation": "<typed>"}``. Server validates that the typed
  value matches the user's Telegram `first_name` (case-insensitive,
  stripped) **or** the string of their numeric Telegram id. Any
  other value returns 400; missing body returns 422 from FastAPI.
  The frontend modal was upgraded from a simple "Удалить?" confirm
  to a typed-input dialog that disables the confirm button until
  the input matches — the dialog is UX, not the security boundary.
  Three new tests cover the matching, missing, and mismatched
  cases.
* **BE-M16** — `POST /trackers` now takes a row-level FOR UPDATE
  lock on the user row before the count + insert dance, so two
  concurrent tracker-creates for the same user no longer both
  pass the per-user limit check. Also dropped the broken
  ``await session.rollback()`` that previously lived inside a
  ``session.begin_nested()`` block — it was rolling back the
  *outer* transaction and was redundant with the savepoint
  context manager's automatic rollback. The savepoint itself was
  also dropped because the FOR UPDATE lock is enough; we now
  surface IntegrityError from a normal commit as a 409.
* Side: `core.deleteJson(url, payload?)` in `frontend/js/api_core.js`
  now optionally carries a JSON body so the BE-M3 confirmation can
  travel on a DELETE. Backward compatible — existing call sites
  that omit `payload` keep the historical no-body behaviour.
* Removed dead `_showConfirmDialog` (only consumer was the old
  `deleteAccount`; replaced by `_showTypedConfirmDialog`).

### Verified non-bugs / closed-by-prior-wave (no code change)

* **BE-M7** — `history_service.py` nested transaction rollback is
  already correct: `async with session.begin_nested()` rolls back
  the savepoint automatically on exception, and the inline comment
  at line 91 explicitly documents that no manual `session.rollback`
  is needed (it would kill the outer transaction). Wave 8's
  cleanup left this in good shape.
* **BE-M9** — `ensure_user` / `ensure_user_exists` already use
  PostgreSQL ``INSERT ... ON CONFLICT DO NOTHING`` for atomicity
  (closed in Wave 5b under BE-H3). The audit was tracking the
  same root cause from a slightly different angle.
* **BE-M14** — `routers/listing_detail.py` already caches the full
  response payload (median, market_stats, liquidity, risk, …)
  under a query-shaped key at line 95 with `cache_ttl_seconds`
  TTL. Per-listing `compute_category_price_stats` only runs on
  cache miss; on a hit the whole object is returned without any
  computation. No additional intermediate cache layer needed.

Verification: 500 passed (+3 from Wave 14's 497), 1 skipped
(Postgres migration round-trip), no regressions. ruff F clean.

### Wave 14 — test sweep `58f609d`

* **TEST-M2** — replaced loose ``status_code in (200, 503)`` and
  ``in (400, 422)`` assertions with deterministic single-value
  checks. ``test_health.py`` was papering over a 503 branch that
  the lifespan fallback (RedisCache → MemoryCache) makes
  unreachable in tests; ``test_export.py`` was hedging on a 400
  branch that Wave 12 (BE-M12) removed entirely.
* **TEST-M6** — new ``tests/test_migrations.py`` adds three static
  Alembic chain checks (single head, walkable base→head, unique
  revision IDs) that run everywhere, plus a Postgres-gated
  round-trip smoke test (``head → -1 → head``) that catches missing
  or inverse-incorrect ``downgrade()`` implementations. Static
  checks add ~0.3s to local runs; the round-trip smoke is skipped
  unless ``TEST_DATABASE_URL`` points at Postgres.
* **TEST-M1 verified non-bug** — the IDOR test
  ``test_user_b_cannot_delete_user_a_watchlist_item`` deliberately
  asserts 204 (not 404) because the endpoint is intentionally
  idempotent: a 404 response would leak which watchlist IDs exist
  across all users via timing/status diffing. The docstring
  already explains this; no change needed. The audit entry was
  incorrect.
* **TEST-M3 verified non-bug** — the audit's complaint about
  ``asyncio.sleep(0.01)`` doesn't match the current code: the only
  short sleeps live inside fake handlers in
  ``test_parallel_kufar.py`` (0.02s) and ``test_listings.py``
  (0.05s) and are *inducing* concurrency, not waiting on it. The
  rate-limit timing test in ``test_kufar_client.py`` measures real
  delay and is correct as written.
* **TEST-M5 deferred** — adding a graceful-shutdown harness needs
  ``PERF-M4`` (graceful SIGTERM handling) wired into the app code
  first. Lined up for Wave 15.
* Side note: silenced an Alembic 1.18+ deprecation warning by
  adding ``path_separator = os`` to ``migrations/alembic.ini``.

Verification: 497 passed (+3 from Wave 13), 1 skipped (Postgres
round-trip smoke), no regressions.

### Wave 13 — docs + CI quick win `db55f04`

* **INF-M1** — added a `security` job in CI running `pip-audit`
  against `uv export --no-dev` output. Found and fixed a real CVE
  on the way: bumped `mako` 1.3.10 → 1.3.12 (CVE-2026-44307,
  transitive via `alembic`).
* **INF-M2** — `proxy-server.mjs` no longer hardcodes
  `/home/staz/Downloads/myProjetctKufar/frontend`; resolves the
  frontend dir relative to the script via `import.meta.url`. Ports
  also overridable via env.
* **INF-M3** — docker-compose `api` service now waits on a one-shot
  `migrate` service that runs `alembic upgrade head` before any
  long-running container starts. Bot and scheduler still wait on
  `api: service_healthy`, so the dependency chain is migrate → api
  → bot/scheduler. A failed migration halts the whole stack instead
  of letting api come up against a half-migrated schema.
* **INF-M4** — CI `lint` / `security` / `test` jobs run in parallel
  (no `needs:` chain between them); `docker-build` still gates on
  all three succeeding. A transient ruff failure no longer hides
  test regressions until the next run.
* **F401 sweep in tests + migrations** — ruff bumped to 0.15.9 in
  the lock file caught 14 unused imports across `tests/` and one
  migration; cleaned up via `ruff check --fix`. None of them
  affected behaviour.
* **CI ruff format check disabled** — the project uses a compact,
  trailing-comma single-line style that the ruff formatter would
  explode into multi-line. Documented in `AGENTS.md` so future
  contributors don't try to bulk-reformat.
* `README.md` — was empty, now has project overview, quick start,
  architecture map, and pointers to the other docs.
* `AGENTS.md` — formal index of conventions, commands, project
  layout, and the wave-per-commit cadence. Used by both human
  contributors and AI agents working on this repo.
* `CHANGELOG.md` — this file. Backfilled with waves 0–12.

### Wave 12 — backend dead code + type-safety sweep `e603bd8`

* **BE-M10** — un-marking a sold deal set
  `lead.status = "active"`, a value not in `LeadStatusEnum`. Now
  reverts to `LeadStatusEnum.bought`.
* **BE-M11** — price-drop comparison in `history_service` mixed
  `Decimal` (from Postgres `Numeric(12,2)`) with `float` (from
  `normalize_price_byn`) — silent on SQLite, `TypeError` on
  Postgres. Coerced both sides to `Decimal`.
* **BE-M12** — removed unreachable `Literal["csv","xlsx"]`
  re-validation in `routers/export.py`; FastAPI enforces the
  literal before the handler runs.
* **BE-M15** — replaced substring host check
  (`"localhost" in db_url`) with proper URL parsing + `ipaddress`
  loopback detection. Closes false-positive cases like
  `db.localhost.attacker.com` and DSN passwords containing
  `sqlitebackup`.
* **F401/F821 sweep** (generalises **BE-L3**) — 5 unused imports
  across `api/dependencies.py`, `api/routers/analytics.py`,
  `api/services/history_service.py`, `api/services/workflow_store.py`,
  `scheduler/collector.py`; plus `Any` imported into `api/main.py`
  for an annotation that previously only worked under
  `from __future__ import annotations`.
* **BE-M1** — verified closed by Wave 10
  (`periodic_prune_shadow_stores` now actually prunes, was a no-op
  inline loop in the original god-file).
* **BE-L4** — rejected as a false positive: `deal_workflow.py` is a
  pure-utility module (liquidity scoring, flip estimates),
  `routers/workflow.py` is HTTP CRUD. Different concerns.

## Wave-by-wave history

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
| 15   | _this_  | backend data integrity + UX (pagination, typed delete confirm, FOR UPDATE) |

For the exact mapping of audit IDs → wave, the per-commit messages
list every ID they touched. Use `git log --grep="BE-M11"` (or any
audit ID) to find the wave that closed a particular item.

## Audit progress

As of Wave 15:

| Severity | Total | Closed | Remaining | Notes                                |
|----------|-------|--------|-----------|--------------------------------------|
| CRITICAL | 29    | 26     | 3         | All 3 are operational (HTTPS, secret rotation, dev `pkill`) |
| HIGH     | 54    | 50     | 4         | All 4 are ops/CI (CD, monitoring, backups, partitioning)    |
| MEDIUM   | 73    | 19     | 54        | Wave 16+ continues the sweep         |
| LOW      | 30    | 1      | 29        | Mostly polish (docs, dead imports)   |

## Conventions

* **One atomic commit per wave**, message names every audit ID it
  closed. See [`AGENTS.md`](./AGENTS.md) for the detailed rules.
* **Comments use audit IDs as anchors** — `# BE-M11: ...` lets
  future readers grep both the audit entry and the fix.
* **`.env`, `DEEP_DIVE_REVIEW_COMPREHENSIVE.md`, `notmyfault.md`**
  are gitignored and stay that way.
* **494 tests must stay green.** A wave that breaks the suite is a
  rolled-back wave.

[Keep a Changelog]: https://keepachangelog.com/en/1.1.0/
