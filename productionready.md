# Production-Ready Audit — Wave Plan

Snapshot of remaining issues found during the wave131 deep-dive review.
The first wave (wave131) committed the cleanup of stale local notes;
this file lists the 20 atomic fixes that close the audit out.

Each issue gets its **own** wave commit. Format:

```
fix(waveN): <short description>

Body — what changed and why. Closes <ID>.

Generated with [Devin](https://cli.devin.ai/docs)
Co-Authored-By: Devin ...
```

Severity legend:
- **P1** — real bug, fix this wave.
- **P2** — should be fixed, low chance of immediate exploitation.
- **P3** — hardening, defence-in-depth, ergonomics.

Status legend: `pending` → `in_progress` → `done`.

---

## P1 — Critical

### PR-01 — image_proxy DoS via unbounded upstream body
- **Severity:** P1 (security/perf)
- **Wave:** 132
- **File:** `api/routers/image_proxy.py`
- **Problem:** `client.get(upstream_url)` (line ~320) loads the full
  upstream JPEG body into memory **before** the
  `len(upstream.content) > image_proxy_max_bytes` check on line ~327.
  An authenticated abuser can target any URL under
  `rms.kufar.by/v1/gallery/...` whose response is large; the worker
  buffers the whole body, then 413-rejects. Fast-path attack: open
  many concurrent image requests for known-large gallery paths.
  `ai_images.py` already does this correctly via `client.stream()`.
- **Fix:** switch to `client.stream("GET", upstream_url)`, abort early
  on `Content-Length` header > limit, and accumulate bytes via
  `aiter_bytes()` with a running cap — same pattern already used in
  `api/services/ai_images.py:80-97`.
- **Status:** done

### PR-02 — AI endpoints lack edge rate-limiting
- **Severity:** P1 (security)
- **Wave:** 133
- **Files:**
  - `api/routers/ai_analysis.py` (`/ai/analyze`, `/ai/task/{task_id}`)
  - `api/routers/ai_listing_assistant.py` (`/ai/listing-assistant`)
  - `api/routers/ai_tools.py` (`/ai/negotiate`, `/ai/price-advice`)
- **Problem:** none of the AI router endpoints carry a
  `@limiter.limit(...)` decorator. The internal `_check_ai_*` quota
  guard is per-user but lives behind the dependency stack; the
  polling endpoint `/ai/task/{task_id}` has no quota at all and an
  abusive client can DoS it cheaply.
- **Fix:** add appropriate slowapi limits — e.g. `60/minute` for
  `/ai/task/{task_id}` polling, `10/minute` for the heavy
  endpoints (analyze / listing-assistant / negotiate / price-advice).
- **Status:** done

### PR-03 — `/ai/negotiate` cache key crosses users
- **Severity:** P1 (privacy)
- **Wave:** 134
- **File:** `api/routers/ai_tools.py:113`
- **Problem:** `cache_key = f"ai_negotiate:{ad_id}:{my_offer}:{asking}"`
  has no `user_id`. Two users supplying the same ad+offer+asking get
  the same cached AI response. Listing-assistant and AI task store
  already namespace by user (e.g. `ai_listing:u{uid}:...`).
- **Fix:** include `user_id` in the cache key (mirror the existing
  `_listing_assistant_cache_key` shape, or wrap via
  `digest_cache_key("ai_negotiate", {..., "u": user_id})`).
- **Status:** done

---

## P2 — High

### PR-04 — XLSX export not protected against formula injection
- **Severity:** P2 (security)
- **Wave:** 135
- **File:** `api/routers/export.py`
- **Problem:** `_csv_safe()` prefixes leading `=`, `+`, `-`, `@` only
  for the CSV writer path. The XLSX path (`_build_xlsx_workbook` →
  `ws.append(_row_for_lead(...))`) writes raw values, so a Kufar
  title or user-typed note starting with `=` is interpreted as an
  Excel formula on open.
- **Fix:** apply `_csv_safe` to the same free-text columns
  (`query`, `title`, `link`, `status`, `source`, `notes`) before
  `ws.append` in `_build_xlsx_workbook`.
- **Status:** done

### PR-05 — `_provisioned_users` only evicts at the cap
- **Severity:** P2 (perf, mild memory)
- **Wave:** 136
- **File:** `api/main.py:391-406`
- **Problem:** `_provisioned_users` only sweeps expired entries when
  the dict crosses 4096 entries. On a long-running worker with high
  user churn the dict pins ~4096 entries' worth of timestamps and
  only ever sheds the expired ones at the cap.
- **Fix:** sweep expired entries on every `_should_auto_provision`
  call (cheap dict comprehension over the live entries) so steady-
  state size tracks active users instead of growing to the cap.
- **Status:** done

### PR-06 — `MemoryCache.incr()` skips `MAX_ENTRIES` eviction
- **Severity:** P2 (perf, fallback path)
- **Wave:** 137
- **File:** `api/services/cache.py:85-107`
- **Problem:** `set()` enforces `MAX_ENTRIES = 5000` via a
  `popitem(last=False)` loop, but `incr()` writes into `_storage`
  without that loop. Whenever the API runs on the MemoryCache
  fallback (Redis unreachable), counters can grow without bound.
- **Fix:** mirror the eviction loop from `set()` inside `incr()`.
- **Status:** done

### PR-07 — Image-proxy per-user semaphore evictable mid-request
- **Severity:** P2 (rate-limit bypass under load)
- **Wave:** 138
- **File:** `api/routers/image_proxy.py:92-100`
- **Problem:** `_get_user_semaphore` evicts the front half of the dict
  when 1024 user keys are present, including users that currently
  hold an `acquire()` slot. A second request from the same user then
  builds a *new* semaphore and bypasses the per-user concurrency cap.
- **Fix:** evict by recency / age rather than insertion order, or
  guard eviction so an entry currently held by a request is not
  dropped (e.g. skip semaphores with `_value < limit`).
- **Status:** done

### PR-08 — Bot deep-link `start_param` is unvalidated
- **Severity:** P2 (URL injection / open-redirect-shape)
- **Wave:** 139
- **File:** `bot/handlers/start.py:14-33`
- **Problem:** `command.args` is passed straight into the Mini-App URL
  via `f"{url}{sep}start_param={deep_param}"`. A malicious
  `/start a%23%2Fother` could rewrite the fragment / sneak in URL
  characters. Telegram Mini Apps run in a controlled WebView so the
  exploit ceiling is low, but it's free to validate.
- **Fix:** allowlist `deep_param` against the same map already used
  in `view_labels` (`tracking|deals|monitoring|...`) and
  url-encode whatever survives.
- **Status:** done

### PR-09 — `reminders.py` GET has no pagination
- **Severity:** P2 (perf / DoS-shaped)
- **Wave:** 140
- **File:** `api/routers/reminders.py:67-101`
- **Problem:** `get_reminders` returns the full list with no
  `limit`/`offset`. A user with thousands of reminders gets a
  multi-MB JSON response per call.
- **Fix:** add `limit: int = Query(default=100, ge=1, le=500)` and
  `offset: int = Query(default=0, ge=0)`, mirror the pattern from
  `expenses.py:get_expenses`.
- **Status:** done

---

## P3 — Hardening

### PR-10 — `ai_privacy.py` swallows Redis init error silently
- **Severity:** P3 (observability)
- **Wave:** 141
- **File:** `api/services/ai_privacy.py:66-71`
- **Problem:** `except Exception: cache = MemoryCache()` falls back
  silently when `RedisCache.from_url(...)` raises. Operators get no
  signal that the in-memory fallback kicked in for the privacy /
  account-deletion path.
- **Fix:** add `logger.warning("Redis init failed in clear_user_ai_data,
  falling back to MemoryCache", exc_info=True)` before the fallback.
- **Status:** done

### PR-11 — `track_init_data_use` first-write race
- **Severity:** P3 (observability)
- **Wave:** 142
- **File:** `api/services/session_security.py:94-103`
- **Problem:** Two concurrent first-time requests from different IPs
  for the same initData both observe `existing is None` and both
  call `set_json`. Last writer wins; the first IP is lost from the
  observability trail.
- **Fix:** if cache backend exposes an SETNX-style primitive, use it;
  otherwise widen the existing block to detect "we just lost the
  race" via a re-read and emit the same WARNING the mismatch path
  uses today.
- **Status:** done

### PR-12 — `_task_locks` race on FIFO eviction
- **Severity:** P3 (rare race under heavy load)
- **Wave:** 143
- **File:** `api/services/ai_task_store.py:148-162`
- **Problem:** When `_task_locks` crosses 1024 entries the front half
  is evicted including locks for tasks currently being updated. A
  parallel `_update_task` for an evicted task creates a fresh lock
  and serialisation breaks for that task.
- **Fix:** skip evicting entries whose `Lock` is currently `locked()`
  during the cleanup pass.
- **Status:** done

### PR-13 — Reminder accepts `remind_at` in the past
- **Severity:** P3 (logic)
- **Wave:** 144 (no-op — already implemented)
- **File:** `api/schemas.py` — `ReminderCreate.validate_remind_at`
- **Problem:** No validation that `remind_at` is in the future. A
  past timestamp is silently accepted; the next scheduler tick
  treats it as already-due and fires the notification immediately,
  which is rarely what the user meant.
- **Fix:** validate at the Pydantic schema level — reject `remind_at`
  more than 60 seconds in the past with a clear 422 error.
- **Status:** done before this audit cycle. The
  ``ReminderCreate.validate_remind_at`` field validator already
  enforces all three conditions: timezone-aware, strictly future,
  and within ``REMINDER_MAX_HORIZON_DAYS``. Regression tests
  ``test_create_reminder_rejects_past_remind_at`` and siblings
  in ``tests/test_reminders.py`` keep the contract honest.
  Wave 144 is the documentation update only — no code changed.

### PR-14 — Postgres password defaults to `kufar`
- **Severity:** P3 (config)
- **Wave:** 145
- **File:** `docker-compose.yml:319-321`
- **Problem:** `POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-kufar}` —
  if the deployer forgets to put the variable in `.env` they get a
  Postgres instance accepting `kufar/kufar`.
- **Fix:** drop the default so `POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}`
  fails fast on `docker compose up` without an explicit value. A
  comment near the line directs the operator to `.env.example`.
- **Status:** done

### PR-15 — Telegram themeParams written to CSS without validation
- **Severity:** P3 (defence-in-depth)
- **Wave:** 146
- **File:** `frontend/js/app.js:218-231`
- **Problem:** `setProperty('--tg-theme-bg-color', tp.bg_color)` trusts
  Telegram's themeParams blob. `setProperty` ignores most invalid CSS
  but leniently accepts e.g. `red; --custom: ...`. Telegram is the
  controlled source so the risk is small, but free to harden.
- **Fix:** validate each value matches a strict CSS color regex
  (`/^#[0-9a-fA-F]{3,8}$/`) before calling `setProperty`; fall back
  to leaving the variable unset if validation fails.
- **Status:** done

### PR-16 — `analytics.py` bot reply uses unbounded `search_query`
- **Severity:** P3 (input length)
- **Wave:** 147
- **File:** `bot/handlers/analytics.py:111-117`
- **Problem:** `search_query` is `html_escape`-d but never length-
  capped. A 4 KB query plus the boilerplate exceeds Telegram's 4096-
  char message limit and the send fails with a noisy stack trace.
- **Fix:** truncate `search_query` to ~120 chars with a `…` suffix
  before formatting the response, mirroring the `lead.title[:120]`
  pattern used elsewhere.
- **Status:** done

### PR-17 — `ensure_user_exists` Postgres-only `ON CONFLICT`
- **Severity:** P3 (test/dev portability)
- **Wave:** 148
- **File:** `api/dependencies.py:202-227`
- **Problem:** `pg_insert(...).on_conflict_do_nothing(...)` is the
  Postgres `ON CONFLICT` form; SQLite (used by the test suite via
  `aiosqlite`) accepts the equivalent `INSERT OR IGNORE` only. Today
  every test path that needs auto-provision goes through
  `auth_bypass=True` which short-circuits before this function, but a
  future test removing that escape hatch would crash on the unsupported
  syntax.
- **Fix:** branch on `bind.dialect.name` — emit `pg_insert` on
  Postgres and `sqlite_insert` on SQLite. Both support
  `on_conflict_do_nothing`.
- **Status:** done

### PR-18 — Redis port published in production-shaped compose
- **Severity:** P3 (config / hardening)
- **Wave:** 149
- **File:** `docker-compose.yml:264-266`
- **Problem:** `127.0.0.1:6380:6379` ships a debug-only port mapping
  that the inline comment marks as "Remove in production". Easy to
  miss when copying the file to a real host.
- **Fix:** gate the published port behind a `local-debug` profile so
  it's opt-in (`docker compose --profile local-debug up`), matching
  the pattern already used for `postgres` and `cloudflared`.
- **Status:** done

### PR-19 — nginx CORS regex accepts every `*.telegram.org`
- **Severity:** P3 (security)
- **Wave:** 150
- **File:** `nginx/default.conf:237-242`
- **Problem:** `^https://[a-z0-9.-]+\.telegram\.org$` allows any
  subdomain. Telegram owns the apex so the exploitation requires a
  subdomain takeover, but tightening to the three real Mini-App
  embedders (`web.telegram.org`, `webk.telegram.org`,
  `webz.telegram.org`) is free.
- **Fix:** swap the regex for an explicit map of allowed origins.
- **Status:** done

### PR-20 — `MemoryCache` fallback notification rate-limit consistency
- **Severity:** P3 (operational)
- **Wave:** 151
- **File:** `api/services/cache.py`
- **Problem:** `MemoryCache.incr()` resets to 1 when the entry is
  malformed (line ~99-100) without re-emitting any TTL. Combined
  with PR-06, the eviction-loop fix makes this trivially correct;
  but the bare-minimum log line on the `(TypeError, ValueError)`
  branch would help operators find corrupt-state Redis blips.
- **Fix:** add a `logger.debug` (not warning — corrupt counter is
  expected after Redis restart) on the fallback path describing
  which key reset to 1.
- **Status:** done

---

## Verification gate

For each wave:
1. Apply the smallest possible diff that closes the issue.
2. Run `uv run pytest --tb=short -q` — must stay green.
3. Run `uv run ruff check .` — must stay clean.
4. Commit atomically with the message format above.

If a wave breaks tests, fix the test or roll back the fix; never commit
a red suite.

After all 20 fixes land, this file is deleted in a final wave so the
working tree carries no leftover audit notes (same pattern as the
wave131 cleanup that started this cycle).
