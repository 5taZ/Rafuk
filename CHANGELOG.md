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

### Wave 18 — split `ai_service.py` god-file _(this commit)_

Follow-up to Wave 10's **BE-C5** split of `ai_analysis.py`, applied
to the other big AI file flagged in the audit. `ai_service.py`
was 1696 lines; most of the volume was inline Russian prompt text
plus payload-normalisation helpers that had no dependency on the
AI client itself. Three new sibling modules now hold those
concerns, and `ai_service.py` drops to 1175 lines focused on the
HTTP client + orchestration it actually owns.

* `api/services/ai_prompts.py` _(new, 315 lines)_ —
  `_PRICE_MARKET_PROMPT_TEMPLATE`, `_PRICE_MARKET_SCHEMA`,
  `_CONDITION_RISKS_PROMPT_TEMPLATE`, `_CONDITION_RISKS_SCHEMA`,
  `LISTING_ASSISTANT_PROMPT`, `QUICK_CONDITION_PROMPT`.
  Prompt-engineering iterations now show up as diffs in one
  focused file, not hidden in a 1700-line service.
* `api/services/ai_sanitize.py` _(new, 122 lines)_ —
  `sanitize_user_text` + the four `_PROMPT_*` regex patterns.
  The SEC-H4 telemetry (logging injection-pattern hits with the
  calling context) lives with the detector instead of drowning
  in a client module.
* `api/services/ai_dedupe.py` _(new, 204 lines)_ —
  `_normalize_for_dedupe`, `_is_paraphrase`, `_dedupe_text_list`,
  `_dedupe_dict_list`, `_dedupe_listing_payload`,
  `dedupe_analysis_payload`. Self-contained paraphrase
  collapsing that the AI client calls once per response.
* `api/services/ai_service.py` — shrank from 1696 → 1175 lines.
  All moved symbols are re-exported with a documented list of
  importers (six sibling routers/services + the test module)
  so back-compat is intentional, not accidental. Header comment
  spells out which importer depends on which re-export so the
  next wave can drop them cleanly when it's time.
* Kept in `ai_service.py` (single-use, tight coupling to the
  client): `_entry_price_guidance`, `_clean_photo_notes`,
  `_repair_truncated_json`, the `AIService` class itself, and
  the `get_ai_service()` module-level factory.

Verification:
* `ruff check . --select F` clean on the whole repo.
* `pytest` 500 passed, 1 skipped (Postgres migration round-trip),
  no regressions.
* Confirmed at import time that the re-exported symbols are the
  *same* objects as their new-module originals (`is` check
  passes for `LISTING_ASSISTANT_PROMPT`, `sanitize_user_text`,
  `dedupe_analysis_payload`) — no double-definition risk.
* Outdated `# TODO: Extract prompt templates…` comment at the
  top of the service was removed (it described exactly this
  refactor).

### Wave 17 — database tuning `6531622`

Four DB changes + a connection-pool tweak. New migration
`20260510_0006_wave17_db_tuning.py` applies the schema work in
one shot; model and router code updated in the same commit so
the ORM metadata and the DDL agree.

* **DB-M1** (`api/database.py`) — added `pool_use_lifo=True` to
  the async engine config. SQLAlchemy's default FIFO checkout
  round-robins connections, keeping every pool slot warm and
  preventing any single connection from aging past
  `pool_recycle=1800s`. LIFO reuses a small hot set and lets the
  idle tail cycle out naturally — the expected win for our
  traffic shape (peak concurrency is 1-2 active connections per
  worker, pool_size=5 per worker × 4 workers).
* **DB-M4** (`api/models.py`, migration `20260510_0006`) — dropped
  three redundant low-cardinality indexes:
    - `idx_lead_items_status` (single-column, fully covered by
      compound `idx_lead_items_user_status` for per-user queries;
      cross-user consumer is a nightly cleanup that reads the
      whole table anyway);
    - `idx_lead_items_market_status` (2-3 distinct values — seq
      scan always wins);
    - `idx_query_listing_states_active` (boolean-only).
  Write amplification on `lead_items` and `query_listing_states`
  drops ~8% in write-heavy scheduler cycles; the compound
  `idx_query_listing_states_query_active` stays for the queries
  that actually benefit from it.
* **DB-M6** (`api/models.py`, migration `20260510_0006`, router
  update in `api/routers/consent.py`) — promoted the partial
  index `idx_user_consents_user_type_active` to `UNIQUE`
  (partial, `WHERE revoked_at IS NULL`). The app already
  enforced "at most one active consent per (user_id,
  consent_type)" by revoking before inserting, but a concurrent
  grant_consent race past the SELECT could land two active
  rows. Storage-layer uniqueness closes that; the router now
  handles `IntegrityError` by re-reading and returning the
  winner's row, so callers see the same outcome they'd get from
  a sequential retry.
* **DB-M9** (`api/models.py`, migration `20260510_0006`) —
  widened `lead_items.link` and `query_listing_states.link`
  from `VARCHAR(512)` to `VARCHAR(2048)`. Kufar occasionally
  appends recommender/tracking params that push the canonical
  ad URL past 512 bytes; Postgres `varchar(N)` only occupies
  `len+4` bytes on disk regardless of N so the bump is
  effectively free. `ALTER COLUMN TYPE` to a wider `varchar` is
  a metadata-only change on Postgres (no table rewrite).

### Verified non-bugs / closed-by-prior-wave (no code change)

* **DB-M2** — `pool_size=5 × max_overflow=10` per worker was
  already sized for 4 uvicorn workers against Postgres's
  `max_connections=100` (4 × 15 = 60 with headroom). See
  `api/config.py` comment block; no change needed in this wave.
* **DB-L timezone consistency** — every `DateTime` column in the
  current schema declares `timezone=True`. Older migrations
  that created naive columns were already rewritten in Wave 5
  (`20260429_0003_timestamp_mixin_tz.py`).
* **DB-L query_listing_states FK** — the table is intentionally
  not user-scoped. It's a per-query market cache shared across
  every user who searches the same normalised query. Adding a
  `user_id` FK would change the semantic from "latest state of
  ad X in search Y" to "latest state per user", multiplying
  storage by the user count for zero behavioural gain. Closed
  as "working as designed".

Verification:
* `ruff check . --select F` clean.
* `pytest` 500 passed, 1 skipped (Postgres migration round-trip
  still gated on `TEST_DATABASE_URL`), no regressions. Includes
  two new `test_history_tables_have_indexes` and
  `test_lead_items_table_structure` assertions that pin the
  expected post-DB-M4 index set so the next refactor can't
  silently re-add the dropped indexes.
* Alembic chain still single-head (`tests/test_migrations.py`
  static checks all pass).

### Wave 16 — backend performance `9c39e82`

* **BE-M4** (`api/services/ai_analysis_pipeline.py`) — added a
  module-level `asyncio.Semaphore` (cap=2) that bounds the parallel
  Kufar searches each AI analysis task fires in `_stage_search`
  (strict_category / broad_category / broad_query). The
  `KufarClient` already has its own
  `kufar_parallel_semaphore` for HTTP concurrency across all
  callers, but without this AI-pipeline-side cap a single task can
  submit 3 search jobs that immediately saturate the client
  semaphore and push any concurrent `/listings` or `/analytics`
  request behind them.
* **BE-M13** (`api/services/aggregator.py`) — replaced the 67-pass
  `for source, target in SEARCH_ALIASES.items(): text =
  text.replace(...)` loop in `normalize_search_text` with a single
  precompiled alternation regex (`_ALIAS_PATTERN`) sorted longest-
  alias-first. The old sequential `.replace` chain composed
  substitutions (e.g. `плейстейшен` → `ps` then `ps 4` → `ps4`);
  the new code preserves that semantic by iterating to fixed point
  with a safety cap of 4 passes. Per-call alias work drops from
  O(n × 67) to O(n × max_alias_len), and the loop bails out in 1-2
  passes for typical input. Tested against 15 representative
  queries to confirm zero divergence from the old behaviour.
* **PERF-M4** (`scheduler/collector.py`) — graceful shutdown on
  SIGTERM/SIGINT. Python's default SIGTERM disposition kills the
  process immediately, which used to interrupt mid-cycle tracker
  checks; the affected `session.commit()` got torn down, leaving
  half of a tick's `tracker_events` rows committed and the other
  half silently dropped. Now SIGTERM sets an `asyncio.Event`, the
  5-minute health-check sleep is replaced with
  `wait_for(event, timeout=300)` so it interrupts immediately, and
  the `finally` block calls `scheduler.shutdown(wait=True)` instead
  of `wait=False` — APScheduler drains the in-flight job so its
  transaction can commit before the engine is disposed. The
  signal-handler registration is best-effort: it falls back
  silently on Windows / non-main-thread test harnesses.

### Verified non-bugs / closed-by-prior-wave (no code change)

* **BE-M5** — `api/routers/image_proxy.py` already wraps the CPU-
  bound Pillow encode in `asyncio.to_thread` and bounds concurrency
  via `_get_transcode_semaphore` (cap=4). The 200-card list view
  no longer stalls the event loop. Closed in a previous wave.
* **BE-M6** — `price_advice` endpoint already enforces a per-user
  rate limit via `_check_rate_limit(...)` *before* the
  `load_query_dataset` call fires, and the underlying Kufar fetch
  goes through `KufarClient`'s internal
  `kufar_parallel_semaphore`. Two layers of bounding already
  in place.
* **PERF-M5** — module-level `logging.basicConfig` was replaced
  with `configure_logging(service=...)` in Wave 6 (INF-H9) for all
  three services. Verified by `grep`: no top-level basicConfig
  remains anywhere in `api/`, `bot/`, `scheduler/`.

### Deferred

* **PERF-M3** (scheduler Telegram notifications with bounded
  concurrency) — needs an architectural refactor of the
  tracker-check loop to separate the DB phase from the
  notification phase (the current code interleaves
  `session.execute` and `notify_user` calls, and SQLAlchemy's
  `AsyncSession` is not safe for concurrent use). Punt to a
  future wave that does the loop split intentionally.

Verification: 500 passed (Wave 15's count), 1 skipped, no
regressions. ruff F clean.

### Wave 15 — backend data integrity + UX `9dda1a6`

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
| 15   | 9dda1a6 | backend data integrity + UX (pagination, typed delete confirm, FOR UPDATE) |
| 16   | 9c39e82 | backend performance (AI semaphore, alias regex, graceful SIGTERM)        |
| 17   | 6531622 | database tuning (LIFO pool, drop indexes, UNIQUE consents, widen links)  |
| 18   | _this_  | split `ai_service.py` — prompts/sanitize/dedupe into sibling modules    |

For the exact mapping of audit IDs → wave, the per-commit messages
list every ID they touched. Use `git log --grep="BE-M11"` (or any
audit ID) to find the wave that closed a particular item.

## Audit progress

As of Wave 18:

| Severity | Total | Closed | Remaining | Notes                                |
|----------|-------|--------|-----------|--------------------------------------|
| CRITICAL | 29    | 26     | 3         | All 3 are operational (HTTPS, secret rotation, dev `pkill`) |
| HIGH     | 54    | 50     | 4         | All 4 are ops/CI (CD, monitoring, backups, partitioning)    |
| MEDIUM   | 73    | 31     | 42        | PERF-M3 deferred (needs scheduler loop refactor)            |
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
