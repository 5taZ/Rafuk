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

### Wave 24 — PERF-M3 complete (parallel tracker notifications) _(this commit)_

Closes the deeper half of **PERF-M3** that Wave 23 explicitly
deferred: the tracker-check loop in `scheduler/collector.py`
interleaved per-tracker DB writes with up to 8 Telegram sends
per tracker across four code paths (new listings, price drops,
trend reversals, threshold alerts). With 300 trackers × ~3
notifications × ~50 ms RTT that's ~45 s of head-of-line
blocking per tick — and since the DB transaction stays open
the whole time, a slow Telegram actively holds row locks
against the API/bot processes.

* **PERF-M3 (tracker portion)** — refactored
  `_check_trackers_inner` from "DB work + `await notify_user`"
  interleaved inside a savepoint-per-query-group to a clean
  two-phase pipeline:

    1. **Collect phase** (inside the savepoints): every place
       that used to call `await notify_user(...)` now appends
       a `_TrackerNotifyJob(telegram_user_id, internal_user_id,
       message, reply_markup)` to a per-tick
       `pending_notifications` list. No Telegram I/O, no
       blocking — the tick commits the DB changes as fast as
       Postgres can take them.
    2. **Dispatch phase** (after `session.commit()`): the new
       `_dispatch_tracker_notifications` helper groups jobs by
       `telegram_user_id`, then schedules up to
       `_TRACKER_USER_CONCURRENCY = 5` **user-batches** in
       parallel via `asyncio.gather`. Within each user's batch
       sends stay **serial** because Telegram rate-limits
       individual chats to ~1 msg/sec. On the first `blocked`
       outcome for a user, the rest of that user's queue is
       dropped and the `internal_user_id` is collected for
       a single bulk `UPDATE Tracker SET active=False WHERE
       user_id IN (...)` at the end of the dispatch.

* **Bonus correctness improvements** (all fall out naturally
  from the refactor):

    * The old inline `break` on "user blocked" fired per-loop
      (new_listings, price_drops), so a blocked user could
      still generate up to 4 failed send attempts per tracker.
      The new design drops ALL remaining jobs for that user
      across every tracker they own after the first block —
      strictly fewer wasted Telegram calls.
    * `total_notified` now counts only genuine `"sent"`
      outcomes, matching the accounting established in Wave
      23's reminder refactor (the old path counted `"retry"`
      as notified, which was misleading).
    * Per-blocked-user tracker deactivation used to emit one
      UPDATE per failed send; now it's one UPDATE per tick per
      set of blocked users.

* **Tests**: 4 new focused tests in `test_scheduler_collector.py`
  exercising the dispatch helper directly — empty job list is a
  no-op (doesn't even open a session); happy-path sends all
  jobs and leaves trackers active; blocked user short-circuits
  remaining jobs AND deactivates both of their trackers in one
  bulk UPDATE while a sibling user's send still succeeds;
  `TelegramRetryAfter` counts as "not sent" without deactivating
  the tracker. 507 passed, 1 skipped (was 503 + 1).

* Expected wall-clock: 45 s → ~9 s at 300 trackers. The DB
  transaction commits before any Telegram I/O, so a Telegram
  outage no longer holds row locks.

### Wave 23 — PERF-M3 partial (parallel lead reminders) + handoff refresh

The highest-user-impact remaining MEDIUM item after Wave 22 was
**PERF-M3** (scheduler Telegram notifications fire serially).
This wave tackles the simpler half — the lead-reminder loop —
and refreshes `claude.md` with the post-Wave-22 state.

The deeper half of PERF-M3 (the tracker-check loop that
interleaves per-tracker DB writes with notifications across
six code paths) is still deferred; it needs a real phase
split between DB work and notifications, plus per-user
serialisation to respect Telegram's chat-rate limits. That's
a dedicated wave's worth of careful work.

* **PERF-M3 (reminders portion)** — `check_reminders` in
  `scheduler/collector.py` used to be a sequential
  ``for reminder in due_reminders: await notify_user(...)``
  loop. With 50 due reminders × ~50 ms Telegram RTT that's
  ~2.5 s of head-of-line blocking per tick. Refactored into
  a 3-phase pipeline:

    1. **Build phase** — single DB read, then per-row job
       materialisation (message, keyboard). Reminders with no
       lead or no Telegram user are marked ``sent=True``
       immediately because retry would never help.
    2. **Send phase** — `asyncio.gather` the prepared messages
       through the new `_send_message_classified` helper with
       a bounded `asyncio.Semaphore(_REMINDER_SEND_CONCURRENCY
       = 5)`. The helper is pure I/O and does NOT touch the
       SQLAlchemy session, which is what makes the parallelism
       safe (AsyncSession isn't concurrency-safe).
    3. **Apply phase** — one sequential pass writes outcomes
       back: `sent=True` for success, accumulate blocked
       `user_id`s, then issue a single bulk
       ``UPDATE Tracker SET active=False WHERE user_id IN
       (blocked)`` instead of one UPDATE per blocked user.
       Single `session.commit()` at the end.

  Expected wall-clock at 50 reminders: 2.5 s → ~500 ms (5×).
  Telegram's ~30 msg/sec global limit stays well within reach
  because each in-flight send is to a different user.

* **`_send_message_classified` helper** — extracted the
  classification logic (sent / blocked / retry) from
  `notify_user` into a pure-I/O function. `notify_user` now
  delegates to it, so the existing 7 tests for `notify_user`
  (forbidden, not-found, unauthorized, retry-after, generic
  API error, etc.) still exercise the same branches via
  delegation. Single source of truth for "how does this
  Telegram error map to a caller-visible outcome".

* Three new tests in `tests/test_scheduler_collector.py` cover
  the parallel path end-to-end:
  `test_check_reminders_happy_path_marks_all_sent`,
  `test_check_reminders_blocked_user_deactivates_trackers`,
  `test_check_reminders_retry_error_leaves_reminder_pending`.

* **`claude.md` refreshed** — the session-handoff file now
  reflects the Waves 12–22 completion state. Updated:
  head revision (`20260510_0006`), compose services (now
  five, including the Wave-13 `migrate` one-shot), test count
  (500 → 503 with this wave's additions), per-wave table
  through `ddeb8ca`, "what's still open" bucket down from 27
  to 26 MEDIUMs, and the "suggested next moves" list that
  points the next AI at PERF-M3's larger half,
  `api_ai.js` split, `ai_audit_log` partitioning, and the
  ops-side HIGHs.

Verification:
* `ruff check . --select F` clean.
* `pytest` 503 passed (+3 from Wave 22's 500), 1 skipped
  (Postgres migration round-trip), no regressions.
* Existing 7 `notify_user` tests still pass — confirms the
  delegation refactor didn't change any branch's behaviour.

### Wave 22 — a11y + UX polish `ddeb8ca`

Five UX/FE-M items investigated; three landed real fixes, two
verified as already-correct.

* **UX-M2** (`frontend/js/dom_helpers.js` +
  `frontend/js/app_actions.js`): `openModalAnimated` now applies
  `inert` to every direct child of `<body>` other than the
  modal itself, restored on close. Same treatment extended by
  hand to the three manual modal paths (consent gate, privacy
  text, typed-delete-confirm) so all 10 modal surfaces share
  the same a11y posture. The `inert` attribute is the modern
  one-shot replacement for the `tabindex=-1 + aria-hidden=true`
  dance — Telegram WebView (Blink/WebKit) has supported it
  since 2022. The restore step runs **before** focus is moved
  back to `_previousFocus` so the restored target isn't itself
  sitting in an inert subtree.

* **FE-M11** (`frontend/js/render_core.js`): added
  pause-on-hover/focus to the toast component. `pointerenter`
  and `focusin` clear the dismiss timer; `pointerleave` and
  `focusout` reschedule it for whatever time was left when the
  pause started. A user who hovers a 3 s toast 1 s in and lets
  go after 5 s still gets the remaining 2 s to read the
  message. Floor of 400 ms on the resumed timer prevents a
  fast hover-out from cutting the message before the user can
  finish reading it.

* **FE-M8** (`frontend/offline.html` + `frontend/sw.js`): added
  a self-contained offline fallback page. The Service Worker
  pre-caches it during `install` (the only resource it
  pre-caches; everything else still fills lazily). When
  `networkFirst` for a navigation request misses both the
  network and the cached SPA shell, it now serves
  `offline.html` instead of throwing — the browser's native
  "no internet" page is no longer the user's last line of
  defence. Bumped `CACHE_VERSION` to `rafuk-cache-v7` so older
  installs eviction-cycle into v7 cleanly.

### Verified non-bugs (no code change)

* **UX-M1** — Wave 6 (`b390a08`) already added
  `role="dialog" aria-modal="true" aria-labelledby="…"` to
  every modal in `frontend/index.html` (7 surfaces) and
  `aria-hidden="true"` to every decorative SVG, skeleton, and
  spacer. Spot-checked: charts (`render_charts.js`),
  card-builder placeholders (`render_card_builders.js`),
  virtual-list spacers (`virtual_list.js`), AI warning icon
  (`api_ai.js`), tracker-status spinner (`render_trackers.js`).
  The only `setAttribute("aria-hidden")` calls in the bundle
  are intentional, and the audit's complaint about missing
  ARIA labels on interactive elements doesn't match the
  current DOM.

* **UX-H1** — focus trap is wired into every modal open path:
  `openModalAnimated` (used by listing detail, expenses,
  AI analysis, AI listing assistant, edit-tracker — installs
  `trapFocus` + saves `_previousFocus` for restore) and the
  three manual paths (consent / privacy / typed-confirm)
  which all install `trapFocus(modal)` next to their
  `modal.hidden = false`. The audit was tracking a slice of
  reality from before Wave 6's a11y wave.

Also: bumped frontend cache-busting tags
(`bump_static_version.sh`).

Verification:
* `ruff check . --select F` clean.
* `pytest` 500 passed, 1 skipped, no regressions.

### Wave 21 — frontend code quality `016efd2`

Seven FE/UX-M items investigated; two real cleanups landed, four
verified as already-correct (audit was over-eager), one
intentionally deferred to a dedicated wave.

* **FE-M12** — stripped 5 stale `console.log` debug
  breadcrumbs from `frontend/js/api_ai.js`. They were the only
  remaining `console.log` calls anywhere in the production
  bundle; `console.error`/`console.warn` calls (kept) all
  surface real failure modes (render errors, tracker save
  failures, etc.) so they stay where they are.
* **FE-M14 partial** — extracted the duplicated 30-second
  in-flight-mutation cleanup magic number into a single
  `INFLIGHT_GUARD_MS` constant in `frontend/js/dom_helpers.js`.
  Both `app_actions.js` (cross-pipeline ad-mutation guard) and
  `api_watchlist.js` (per-row guard) now reference it, so a
  click in the leads surface and a click in the watchlist
  surface for the same ad can't race past each other due to a
  drift between the two timeouts. Other "magic" numbers in the
  bundle are mostly animation timings (140/180/240/320 ms) that
  carry product meaning — they stay inline next to the CSS
  transition they're paired with.

### Verified non-bugs / closed-by-prior-wave (no code change)

* **FE-M4** — the audit complained about a circular dependency
  between `api_events.js` and `app_actions.js`. There isn't one:
  `api_events.js` is loaded first (per `index.html` script
  order) and exposes `createApiEvents(context)` as a global;
  `app_actions.js` then populates a `context` object with all
  action functions and invokes `createApiEvents(context)` once.
  Events read action references off the shared `context` at
  runtime — classic dependency-injection, not a cycle.
* **FE-M13** — the audit said the `virtual_list.js` recycle pool
  "grows without bound". It doesn't: `renderedMap` is the only
  pool, and `renderVisibleItems` removes any entry whose index
  scrolls outside the viewport range (`renderedMap.delete(idx)`
  + `el.remove()`) on every scroll tick. Worst-case size is
  `(visible_end - visible_start) + 2 × bufferSize`, bounded by
  the container height divided by item height. `setItems` also
  clears the map outright when data changes.
* **FE-L6** — Service Worker API cache-busting was already
  closed by Wave 20: `CACHE_VERSION` bumps drop both static and
  runtime caches on activate, plus the new
  `RUNTIME_CACHE_MAX_AGE_SECONDS` (FE-M9) bounds how long any
  individual entry can pin the user to stale data.

### Deferred (intentionally, with reasoning)

* **FE-M5** (`!important` migration) — 31 declarations across
  `tokens.css` (8), `pipeline.css` (6), `modals.css` (11),
  `brand.css` (4), `states.css` (2). Spot-checked: most are
  intentional fights against either Telegram WebApp's inline
  styles (`body`, `[data-theme]` overrides, modal overlays) or
  the global `[hidden]` attribute (`display:none !important`
  beats flex/grid display values that downstream rules want).
  The pre-reduce-motion media query also relies on `!important`
  to override component-level animation rules. Each removal
  needs paired visual review; the bulk-strip a future wave
  can do is small.
* **UX-M8** (`api_ai.js` 1325-line split) — the natural split
  points are 4 modules (modal lifecycle, analysis loop, result
  rendering, PDF export). All four share the same closure-
  scoped state (`_lastAiData`, progress refs, cancellation
  signals) so the split needs careful closure-rewiring through
  a new `context` shape. Not in this wave's scope; the
  service-side `api/services/ai_service.py` split (Wave 18)
  did the equivalent backend work and the AI flow on FE has
  near-zero automated coverage, so a dedicated wave with a
  manual smoke pass is the safer cadence.

Also: bumped frontend cache-busting tags (`bump_static_version.sh`).

Verification:
* `ruff check . --select F` clean.
* `pytest` 500 passed, 1 skipped, no regressions.

### Wave 20 — frontend performance `b444ff1`

Six FE-M items investigated; three were already closed by earlier
waves (no code change needed), three got fixed here.

* **FE-M2 verified-closed** — lazy-loading the AI module bundle
  was already wired up via `ensureAiLoaded` /
  `context._loadScript` in `frontend/js/app_actions.js`. Neither
  `js/api_ai.js` nor `js/api_listing_assistant.js` is referenced
  from `index.html`; both load on demand the first time the user
  triggers an AI flow. Removed `frontend/js/lazy_ai.js`, an
  older incomplete prototype of the same pattern that was never
  wired into `index.html` — pure dead code.
* **FE-M3 verified-closed** — every card thumbnail built by
  `buildMediaNode` (the single render path for listing /
  watchlist / lead cards) already sets
  `loading="lazy" decoding="async"` plus an explicit
  width/height layout box and an optional `fetchpriority="low"`.
  Below-the-fold images don't fetch until they scroll into view.
* **FE-M10** — added an `error`-event fallback to
  `buildMediaNode` so when a Kufar thumbnail 404s (the site
  occasionally garbage-collects URLs while the listing is still
  indexed) the `<img>` is replaced with the same `<div>`
  placeholder we'd render for a missing URL, preserving the
  reserved layout box. `{once: true}` ensures a flaky network
  can't trigger a swap → re-fetch → error loop.
* **FE-M9** — `staleWhileRevalidate` in `frontend/sw.js` now
  enforces a 1-hour `RUNTIME_CACHE_MAX_AGE_SECONDS`. Without
  this, a user who left the app open all morning could keep
  reading analytics from before lunch — the background refetch
  fires but the user is already acting on the stale data.
  Implementation reads the cached response's `Date` header
  (Starlette/FastAPI sets it on every reply); a missing header
  is conservatively treated as "fresh" so we don't regress the
  pre-FE-M9 behaviour for the rare case the header is absent.
  Bumped `CACHE_VERSION` to `rafuk-cache-v6` so activation drops
  any v5 entries that pre-date the age check.
* **FE-M1 partial** — added
  `<link rel="preload" as="style" href="css/style.css?…">` ahead
  of the existing render-blocking stylesheet link. The browser
  now starts fetching the 54 KB CSS bundle during HTML parse,
  in parallel with the Google-Fonts request, so the subsequent
  `<link rel="stylesheet">` resolves from the in-flight preload
  entry. Saves one round-trip on the critical-path CSS.
  Stopped short of the
  `media="print" onload="this.media='all'"` async-load trick:
  it requires an inline event handler that the current
  `script-src` CSP (no `'unsafe-inline'`) blocks, and
  bundle-splitting introduces FOUC risk that needs UX
  validation across modal/pipeline surfaces — punted to a
  follow-up wave with proper testing.

Also: bumped frontend cache-busting tags via
`scripts/bump_static_version.sh` (rewrote 24 asset refs).

Verification:
* `ruff check . --select F` clean.
* `pytest` 500 passed, 1 skipped (Postgres migration round-trip),
  no regressions.

### Wave 19 — extract `ai_marketplace.py` lexicon to JSON `be5939f`

Second half of the god-file sweep (first half: Wave 18 for
`ai_service.py`). The audit called out ~200 lines of inline
Russian/English token dictionaries at the top of
`ai_marketplace.py` as "pure data edited by product, not
engineering". Same pattern Wave 5d established for
`category_guidance.json`: move the data to a JSON file under
`api/services/data/` and materialise it at import time into the
exact same runtime structures (frozenset / tuple) the scorer
expected.

* New `api/services/data/marketplace_lexicon.json` holds nine
  token groups (234 lines):
    - `ignored_param_keys` (4 entries)
    - `stop_tokens` (13)
    - `fuel_words` / `transmission_words` / `hot_word_groups`
    - `auto_part_stems` (26)
    - `color_words` (24)
    - `auto_brand_tokens` (23)
    - `category_generic_tokens` (6 categories)
    - `accessory_type_tokens` (8 accessory types)
* `api/services/ai_marketplace.py` shrank from 1824 → 1644 lines
  (−180). The loader (`_load_marketplace_lexicon`) runs once at
  import and builds the `_IGNORED_PARAM_KEYS` / `_STOP_TOKENS` /
  `_FUEL_WORDS` / `_TRANS_WORDS` / `_HOT_WORD_GROUPS` /
  `_AUTO_PART_STEMS` / `_COLOR_WORDS` / `_AUTO_BRAND_TOKENS` /
  `_CATEGORY_GENERIC_TOKENS` / `_ACCESSORY_TYPE_TOKENS` constants
  that the similarity scorer already consumed — no downstream
  call sites changed. Regex patterns and scoring weights stay
  inline because they encode behaviour, not data.
* The duplicate `_DATA_DIR = Path(__file__).parent / "data"` that
  existed twice in the file (once for the lexicon introduced
  in this wave and once for the Wave-5d guidance loader) was
  consolidated to a single top-of-module definition.

Verified at runtime that all 10 structures survive the JSON
round-trip with the same element types and membership
(`frozenset("audi" in _AUTO_BRAND_TOKENS) → True`, etc.), and
that `_CATEGORY_WATCH_OUT` / `_CATEGORY_CHECKLIST` from Wave 5d
still load correctly against the same `_DATA_DIR`.

Verification:
* `ruff check . --select F` clean.
* `pytest` 500 passed, 1 skipped, no regressions.

### Wave 18 — split `ai_service.py` god-file `3b6a526`

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
| 18   | 3b6a526 | split `ai_service.py` — prompts/sanitize/dedupe into sibling modules    |
| 19   | be5939f | extract `ai_marketplace.py` lexicon to JSON (BE-M17)                    |
| 20   | b444ff1 | frontend performance (image onerror, SW max-age, CSS preload)            |
| 21   | 016efd2 | frontend code quality (strip console.log, INFLIGHT_GUARD_MS dedupe)     |
| 22   | ddeb8ca | a11y + UX polish (inert siblings, toast pause-on-hover, offline page)   |
| 23   | _this_  | PERF-M3 partial — parallel lead-reminder sends + handoff refresh        |

For the exact mapping of audit IDs → wave, the per-commit messages
list every ID they touched. Use `git log --grep="BE-M11"` (or any
audit ID) to find the wave that closed a particular item.

## Audit progress

As of Wave 23:

| Severity | Total | Closed | Remaining | Notes                                |
|----------|-------|--------|-----------|--------------------------------------|
| CRITICAL | 29    | 26     | 3         | All 3 are operational (HTTPS, secret rotation, dev `pkill`) |
| HIGH     | 54    | 51     | 3         | Rest are ops/CI (CD, monitoring, off-host backups)           |
| MEDIUM   | 73    | 47     | 26        | PERF-M3 reminders portion closed; tracker-loop half deferred |
| LOW      | 30    | 2      | 28        | FE-L6 closed via Wave 20 SW changes  |

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
