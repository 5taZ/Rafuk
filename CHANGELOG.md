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

### Wave 13 — docs + CI quick win _(this commit)_

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

For the exact mapping of audit IDs → wave, the per-commit messages
list every ID they touched. Use `git log --grep="BE-M11"` (or any
audit ID) to find the wave that closed a particular item.

## Audit progress

As of Wave 12:

| Severity | Total | Closed | Remaining | Notes                                |
|----------|-------|--------|-----------|--------------------------------------|
| CRITICAL | 29    | 26     | 3         | All 3 are operational (HTTPS, secret rotation, dev `pkill`) |
| HIGH     | 54    | 50     | 4         | All 4 are ops/CI (CD, monitoring, backups, partitioning)    |
| MEDIUM   | 73    | 7      | 66        | Wave 13+ continues the sweep         |
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
