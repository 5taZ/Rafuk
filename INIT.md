# INIT.md — quick onboarding for the next AI session

> **Read this file first.** It captures the current state of the
> Rafuks/Kufar Mini App project, what was just done, what's pending,
> and the gotchas that will bite you if you skip them. Companion to
> `CLAUDE.md` (more granular conventions / architecture detail).

---

## 1. What this project is

**Rafuks** — a Telegram Mini App for Belarusian marketplace
analytics on **kufar.by**. The app fetches Kufar's public JSON API,
computes price stats (median, IQR, segments), tracks queries for
new listings & price drops, and runs an AI assistant for buyers
(deal analysis) and sellers (listing draft generator).

Users open it inside Telegram. The UI is a single-page vanilla JS
app served by Nginx. Backend is FastAPI + PostgreSQL + Redis.

**Working branch right now:** `bad-app` (off `main`). All recent
fixes since 2026-04 sit here.

---

## 2. Stack & versions

- **Python 3.12** with `uv` package manager (`uv sync --extra dev`,
  `uv run …`)
- **FastAPI** (async) + **SQLAlchemy 2.x async** (asyncpg) +
  **Alembic** for migrations
- **PostgreSQL 16** on `localhost:5433` (Docker maps `5432→5433`)
- **Redis 7** on `localhost:6380` (Docker maps `6379→6380`)
- **aiogram 3** for the Telegram bot (`/app`, `/start`, callbacks)
- **APScheduler** for the tracker collector loop
- **Vanilla JS, no build step** — module pattern with factory
  functions (`createXxx(context)`)
- **Nginx 1.27 alpine** serves frontend on `:8081`, proxies `/api/`
  to backend on `:8010`
- **Cloudflared tunnel** for exposing `:8081` to Telegram during dev
- **AI**: OpenAI-compatible API. Currently `AI_MODEL=gemini-2.5-flash`
  in `.env` (the code default is `google/gemma-4-31B-it` — that's
  the fallback path; the live deployment uses Gemini 2.5 Flash).
  Code branches on `_is_gemini` / `_is_gemma_legacy`.

---

## 3. Run / test / lint

```bash
# Infra
docker compose up -d redis            # postgres runs as a host
                                      # container `marketplace_postgres`
                                      # on :5433 (already up usually)

# Backend + bot + scheduler + frontend (one terminal each, or:)
./start-local.sh                      # everything local
./start-local.sh --with-tunnel        # adds cloudflared tunnel

# Stop
./stop-local.sh

# Migrations
uv run alembic -c migrations/alembic.ini upgrade head
uv run alembic -c migrations/alembic.ini heads

# Tests (151 currently passing)
uv run pytest
uv run pytest tests/test_workflow_api.py -k watchlist

# Lint
uv run ruff check .
uv run ruff check --fix .
```

`API_BASE_URL=http://127.0.0.1:8010`, frontend at
`http://127.0.0.1:8081`.

---

## 4. Architecture cheat-sheet

```
Frontend (Vanilla JS, Nginx :8081)
    ▼ fetch('/api/...', X-Telegram-Init-Data header)
Nginx → /api/ → FastAPI (:8010)
    ▼
KufarClient (api.kufar.by/search-rendered-paginated)
    ▼
aggregator → query_pipeline → routers → response
    ▼
Postgres (history snapshots, leads, trackers, saved searches)
Redis (Kufar response cache, AI cache; falls back to MemoryCache)
```

### Three long-running processes

1. **API** — `uv run uvicorn api.main:app --port 8010`
2. **Bot** — `uv run python -m bot.main` (aiogram)
3. **Scheduler** — `uv run python -m scheduler.collector`
   (APScheduler tracker loop)

### Frontend module map

`app.js` → `createAppCore()` (state, DOM, formatters)
       → `createAppRenderers()` (composition hub)
       → `createAppActions()` (API calls + event binding)

Key JS files (all factories):
- `api_core.js` — fetch primitives, Telegram init-data header
- `api_listings.js` — search orchestrator (`search()` is the entry
  point for ALL user-driven queries)
- `api_watchlist.js` — watchlist proxy over leads (see §6.1)
- `api_leads.js` — deal/lead CRUD
- `api_events.js` — DOM event binding (huge file, watch it)
- `api_ai.js` — AI analysis modal
- `api_listing_assistant.js` — seller-side listing draft modal
- `render_card_builders.js` — `buildListingNode()` etc.
- `render_cards.js` — listings/deals/watchlist render dispatchers

---

## 5. What was JUST done (last sprint)

The branch `bad-app` accumulates these fixes since the merge
work started. **Read commit messages for full context.**

```
5e845d7 Doc fix: Gemma → Gemini 2.5 Flash in comments
12c32a3 Fix +78539% bug + collapse paraphrase duplicates in AI
a757a12 Listings pill: show Kufar's true total, not the render cap
c9ef6ce Categories: chip count = listings count, family expansion
cee10e5 Mirror kufar.by sidebar: real per-category totals
747eab8 Filters: client-side category filter + reset on every search
b15f0bb AI helper card: drop "История" shortcut
aff02f3 UI cleanup: drop empty "0 сделок", tracker events refresh
8038083 Fix watchlist↔leads transitions: race-safe + idempotent
c4a07bf Merge watchlist_items into lead_items (status='watching')
6e9fdd4 Watchlist mutations refresh unified Мои объявления list
a088bf6 Fix Мои объявления: 50/50 tabs, drop importance, dedupe
cb2d1ba Initial UI-only merge of Watchlist + Покупки
```

### High-impact changes you must remember

- **`watchlist_items` table no longer exists.** Its rows live in
  `lead_items` with `status='watching'`. Migration:
  `migrations/versions/20260427_0001_merge_watchlist_into_leads.py`.
  `WatchlistItem` model is gone. `/api/v1/watchlist/*` endpoints
  remain as a *proxy layer* over `LeadItem` for frontend
  back-compat — see `api/routers/workflow.py`.
- **`upsert_lead`** now race-safe (SAVEPOINT + IntegrityError
  retry) and refuses to demote an existing active/closed lead to
  `watching` from a watchlist add (returns 409 instead).
- **`DELETE /watchlist/{id}` is idempotent** (204 even if the row
  was already promoted/deleted).
- **`promoteWatchlistToLead`** is now `PATCH /leads/{id} {status:
  "new"}` instead of POST + DELETE — same row, no race.
- **Category filter overhaul** — chip counts mirror what kufar.by's
  sidebar shows (one parallel `cat=<id>&size=200` request per chip,
  see `query_pipeline.fetch_category_totals` and
  `KUFAR_CATEGORY_FAMILY` map). Listings pill = Kufar raw total
  (broad) or post-filter count (cat-scoped, with fallback to Kufar
  total when paginated cap is hit).
- **Filter reset semantics**: `search(target, { keepFilters })` —
  default `false`. Every text-input/Enter/recent-search resets. Only
  the dropdown's "Применить" button passes `keepFilters: true`.
- **AI dedupe**: server-side paraphrase collapse for
  `condition.notes`, `watch_out`, `red_flags`,
  `meeting_checklist`, `negotiation_tips`, `selling_points`,
  `photo_tips`, `negotiation_playbook`, `quick_condition.notes`.
  Cross-field rule: anything in `condition.notes` is removed from
  `watch_out`/`red_flags`. See `dedupe_analysis_payload` in
  `api/services/ai_service.py`.

---

## 6. What's pending (user's roadmap)

User originally listed three cleanup tasks ("Делаем все по
порядку"):

1. ✅ **Full DB migration**: watchlist → leads. Done in commit
   `c4a07bf`, hardened in `8038083`.
2. ✅ **Unified card builder for watch + lead.** Single
   `buildItemCard(item, { mode })` in
   `frontend/js/render_card_builders.js`. Shared helpers extracted
   for missing banner, price-delta pill, profit/potential block,
   market badge. `buildLeadNode` / `buildWatchlistNode` are now
   thin one-line wrappers over the unified builder so existing
   callsites keep working without churn.
3. ✅ **Drag-to-promote / swipe actions.** `makeSwipeable(card, …)`
   in `frontend/js/dom_helpers.js` is a generic helper that wraps
   any card in a swipe track, drags it with rubber-band
   resistance, fires haptic feedback on commit, and short-circuits
   under `prefers-reduced-motion`. Watching cards (mode='watching',
   not missing) get left=delete / right=promote-to-Покупки wired
   in by default. Lead cards stay tap-only because their middle
   row holds buy/sold price inputs that conflict with horizontal
   pans.

---

## 7. Gotchas & tribal knowledge

### Kufar API quirks
- Endpoint: `https://api.kufar.by/search-api/v2/search/rendered-paginated`
- Param names: `cur` (not `currency`), `cat`, `rgn`, `cnd`, `size`,
  `sort`. **No `otype` since 2026** — seller_type filtering is
  client-side.
- `cat=<id>` queries silently widen Kufar's matching rules: e.g.
  query="Audi Q7 4L 2015" + cat=2010 returns Kufar `total=11` but
  only 3 ads survive our `apply_search_mode`. We trust the post-
  filter count for cat-scoped pills, fall back to Kufar total only
  when paginated cap (≥200) is hit.
- Prices come in **kopecks × 100** (i.e. 78639 BYN appears as
  7,863,900 in raw response). `query_pipeline._normalize_response_ads`
  divides by 100 if the heuristic detects kopeck-scale numbers.
- Pagination: 25 pages × 200 ads = 5000 cap (`kufar_max_ads_per_query`).

### Currency
- All DB prices in **BYN** (`Float` or `Numeric(10,2)`).
- API returns whatever `currency` param the user picked, conversion
  via `currency_service.get_rates()`.
- Frontend has `state.usdRateByn` for client-side toggle.

### Telegram auth
- `X-Telegram-Init-Data` header → `api/middleware/telegram_auth.py`
  HMAC-verifies with `BOT_TOKEN`.
- **Debug mode**: in `.env` set `debug=true` and the auth
  middleware lets requests through with `user_id=0` (only useful
  for local curl). CORS also opens up for `localhost:8081`.

### AI service
- **Gemini 2.5 Flash** (current production model) honours
  `response_format: {"type":"json_object"}` — the code uses it via
  `_is_gemini` branch in `ai_service.py`.
- **Gemma 4 (legacy fallback)** does NOT — using
  `response_format` produces empty `content`. The code reads from
  `reasoning` field as fallback. `max_tokens` must be ≥2200 for
  analysis prompts.
- Two-call analysis: `analyze_listing_parallel` runs Call A (price
  & market) and Call B (condition & risks) staggered by 1s to avoid
  Together AI 429 rate limits, then merges. Final dedupe step
  collapses paraphrase duplicates across all list fields.
- `nginx/default.conf` has `proxy_read_timeout: 300s` — AI requests
  often take 30-60s. Don't lower it.

### Modal & scroll lock
- `body.modal-open` locks page scroll.
- Modal content scrolls inside `.detail-sheet-content` /
  `.ai-modal-body` via flex + `overflow-y: auto;
  -webkit-overflow-scrolling: touch`.
- `[hidden] { display: none !important; }` is REQUIRED in CSS for
  any flex/grid container that uses HTML `hidden` — flex/grid
  silently overrides it otherwise.

### Frontend factory pattern pitfalls
- `createApiActions` destructures from `context`. If a function
  isn't returned by `createAppRenderers`, it's `undefined` in
  downstream modules even if it's defined.
- `context._hooks` is the cross-module bridge for renderers to call
  each other. E.g. `render_core.js` calls
  `context._hooks.renderChart()`.

---

## 8. Frontend state shape (the important bits)

```js
state = {
  query: "",                     // current text in search input
  category: null | number,       // active cat=X filter (or null)
  pendingCategory: null,         // dropdown's pending value
  categories: [],                // [{ id, label, count }, ...] for chips
  stats: { ... },                // /price-stats response
  listings: [],                  // /listings response.listings
  listingsTotal: 0,              // /listings response.total (Kufar
                                 // raw for broad, post-filter for cat)
  leads: [],                     // /leads response (excludes watching)
  watchlist: [],                 // /watchlist response (= LeadItem
                                 // status='watching' under the hood)
  trackerEvents: [],             // tracker_events
  // … many more in app_core.js
}
```

`render_cards.js` reads from `state.*` exclusively — never mutate
state inside a render function.

---

## 9. Files that will likely matter

```
api/
  config.py                    pydantic-settings env loader
  models.py                    SQLAlchemy ORM (LeadItem now holds
                               watchlist columns)
  schemas.py                   Pydantic IO models
  routers/
    workflow.py                /api/v1/leads + /api/v1/watchlist
                               (watchlist routes proxy to LeadItem)
    listings.py                /api/v1/listings — pill total logic
    price_stats.py             /api/v1/price-stats — categories
                               enrichment via fetch_category_totals
    ai_analysis.py             /api/v1/ai/analyze + quick-condition
  services/
    aggregator.py              price stats, search filtering,
                               apply_search_mode
    query_pipeline.py          QueryDataset, fetch_category_totals,
                               KUFAR_CATEGORY_FAMILY map
    ai_service.py              AIService class (analyze, generate,
                               quick_condition) + dedupe helpers
                               (dedupe_analysis_payload etc.)
    workflow_store.py          ensure_user, upsert_lead (race-safe)
    kufar_client.py            HTTP client for api.kufar.by

migrations/
  alembic.ini
  versions/
    20260427_0001_merge_watchlist_into_leads.py     ← critical recent

frontend/
  index.html
  css/style.css                ~8000 lines, search before adding
  js/                           see §4 module map

tests/
  test_workflow_api.py         leads & watchlist CRUD smoke
  test_listings.py             includes price_vs_median stability
                               test (don't break this — it covers
                               the +78539% regression)
  test_ai_analysis.py          AI service mocked; 34 tests
  ... 151 tests total
```

---

## 10. Conventions to respect

- **Ruff**: `E/W/F/I/N/UP/B/SIM/TCH`, line length 99, isort
  known-first-party `[api, bot, scheduler]`. `UP017` is ignored
  (`datetime.UTC` doesn't exist on the `datetime` class — use
  `from datetime import UTC, datetime` and `datetime.now(UTC)`).
- **Async everywhere**: SQLAlchemy async sessions, httpx async,
  aiogram 3.
- **No frontend build step.** Don't add bundlers. Vanilla JS only.
- **XSS prevention**: `escapeHtml()` for text, `safeUrl()` for
  href/src (blocks `javascript:` / `data:`).
- **Currency**: BYN in DB always.
- **Don't add comments unless asked** — that's a project rule from
  `CLAUDE.md`. (Doc strings on new helpers are fine.)
- **Dont add documentation files describing your changes** — but
  this `INIT.md` is an exception, it's an onboarding handover.

---

## 11. How to verify your work

Before saying "done":
1. `uv run ruff check .` clean.
2. `uv run pytest` — 151+ passing (don't break existing tests).
3. For backend changes affecting Kufar: hit `/api/v1/health`,
   `/api/v1/price-stats?query=…`, `/api/v1/listings?query=…`
   with curl. The dev API listens on `127.0.0.1:8010`. Use
   `--noproxy '*'` with curl when there's a proxy in `.env`.
4. For frontend: open the Mini App via Telegram tunnel
   (`./start-local.sh --with-tunnel`) and walk the touched flow.
5. Commit messages: imperative mood, "why" not "what". Use the
   project's `git commit -m "$(cat <<'EOF' …` heredoc pattern with
   the Devin co-author trailer.

---

## 12. Common debug recipes

- **"Внутренняя ошибка сервера"** — check `tail -50 /tmp/rafuk-api.log`
  for the FastAPI traceback.
- **MissingGreenlet on a Pydantic field** — you forgot
  `await session.refresh(obj)` after `session.commit()`. The ORM
  attribute lazy-loads in a sync context and explodes.
- **Kufar 422 with `details: "translation not found for X"`** —
  param name typo. Their API only knows `cur`, `cat`, `rgn`, `cnd`,
  `size`, `sort`, `cursor`. No `otype`, no `aggregations`, no
  `currency` (the long form).
- **`unique constraint chk_lead_items_status` violation** — you
  added a status the DB constraint doesn't know about. Check
  `migrations/versions/20260427_0001_merge_watchlist_into_leads.py`
  for the allowed enum and add a migration if you need a new state.
- **Frontend tab counter stale** — make sure you call both
  `loadLeads()` and `loadWatchlist()` in parallel after a mutation,
  the surfaces share the same row but are queried separately.

---

**TL;DR for the impatient**: read commits `c4a07bf` →
`a757a12` to see the recent direction. The DB merge is done; the
two pending milestones are (a) one shared card builder for both
surfaces, (b) swipe-to-promote gesture on watching cards. Don't
bring back `watchlist_items` table.
