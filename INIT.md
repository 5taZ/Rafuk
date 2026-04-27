# INIT.md — Rafuks project onboarding

> **Read this first.** Current state, what works, what's broken,
> architecture, conventions. Companion to `CLAUDE.md` (granular
> code-level detail).

---

## 1. What this project is

**Rafuks** — Telegram Mini App for Belarusian marketplace analytics
on **kufar.by**. Fetches Kufar's public JSON API, computes price
stats (median, IQR, segments, geography), tracks queries for new
listings & price drops, runs an AI assistant for buyers (deal
analysis) and sellers (listing draft generator), and manages a deal
pipeline (leads, watchlist, expenses, contacts).

Users open it inside Telegram. The UI is a single-page vanilla JS
app served by Nginx. Backend is FastAPI + PostgreSQL + Redis.

**Working branch:** `bad-app` (off `main`). All work since 2026-04
sits here.

---

## 2. Stack & versions

| Layer | Tech | Notes |
|-------|------|-------|
| Runtime | Python 3.12, `uv` package manager | `uv sync --extra dev` |
| Backend | FastAPI (async) + SQLAlchemy 2.x async (asyncpg) | Alembic migrations |
| DB | PostgreSQL 16 on `:5433` | Docker maps 5432→5433 |
| Cache | Redis 7 on `:6380` | Docker maps 6379→6380; falls back to MemoryCache |
| Bot | aiogram 3 | `/app`, `/start`, tracker notification callbacks |
| Scheduler | APScheduler | Tracker collector loop every N minutes |
| Frontend | Vanilla JS, no build step | Module pattern, factory functions |
| Server | Nginx 1.27 alpine on `:8081` | Proxies `/api/` → backend `:8010` |
| Tunnel | Cloudflared | Exposes `:8081` to Telegram during dev |
| AI | OpenAI-compatible API (Together AI) | Gemini 2.5 Flash (production), Gemma 4 (legacy fallback) |
| CSS | 7 partials → `style.css` via `scripts/rebuild_css.py` | ~8900 lines total |

---

## 3. Run / test / lint

```bash
# Infra (Postgres usually runs as host container on :5433)
docker compose up -d redis

# All services local
./start-local.sh
./start-local.sh --with-tunnel    # adds cloudflared tunnel

# Stop
./stop-local.sh

# Migrations
uv run alembic -c migrations/alembic.ini upgrade head

# Tests (215 currently passing)
uv run pytest
uv run pytest tests/test_specific.py -k "test_name"

# Lint
uv run ruff check .
uv run ruff check --fix .

# JS syntax check
node --check frontend/js/dom_helpers.js

# CSS rebuild (after editing any partial)
uv run python scripts/rebuild_css.py

# Cache-bust: bump ?v= in index.html for all CSS/JS references
# e.g. ?v=20260427-v2 → ?v=20260427-v3
```

`API_BASE_URL=http://127.0.0.1:8010`, frontend at
`http://127.0.0.1:8081`.

---

## 4. Architecture

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
   (APScheduler tracker loop, checks every `ALERT_CHECK_INTERVAL` min)

### Frontend module map (~11,400 lines JS)

```
app.js                          entry point (138 lines)
  → createAppCore()             state, DOM cache, formatters (414 lines)
  → createAppRenderers()        composition hub (291 lines)
      render_core.js            toast, error, skeletons, panels (562 lines)
      render_card_builders.js   listing/deal/watchlist/opportunity cards (839 lines)
      render_cards.js           render dispatchers (689 lines)
      render_views.js           view switching, inputs (409 lines)
      render_modals.js          detail modal, expenses modal (341 lines)
      render_charts.js          distribution, history, profit charts (548 lines)
      render_trackers.js        tracker cards, events, virtual scroll (599 lines)
  → createAppActions()          API calls + event binding (526 lines)
      api_core.js               HTTP primitives, Telegram headers (127 lines)
      api_listings.js           search, listings, detail, segments, etc (558 lines)
      api_trackers.js           tracker CRUD, event loading (283 lines)
      api_events.js             all DOM event binding (1015 lines)
      api_watchlist.js          watchlist CRUD (357 lines)
      api_leads.js              lead/deal CRUD (383 lines)
      api_ai.js                 AI analysis modal with progress (1192 lines)
      api_listing_assistant.js  seller-side listing draft (736 lines)

dom_helpers.js                 shared utilities: escapeHtml, safeUrl,
                               attachPinchZoom, attachLongPress,
                               makeSwipeable, showLongPressMenu (943 lines)
virtual_list.js                virtual scrolling for tracker events (209 lines)
app_core_dom.js                DOM element cache (218 lines)
```

### CSS partials (~8,900 lines)

| Partial | Lines | Scope |
|---------|-------|-------|
| `tokens.css` | 720 | CSS custom properties, theme tokens, motion, spacing |
| `brand.css` | 1,720 | Cards, badges, AI assistant, listing assistant, buttons |
| `layout.css` | 1,927 | Grid, forms, status badges, action buttons, tabs |
| `modals.css` | 1,237 | Detail sheet, expenses modal, toasts, badges |
| `pipeline.css` | 1,683 | Deal pipeline, lead cards, tracker cards, events |
| `states.css` | 687 | Skeletons, loading, hidden, filter dropdown, AI block |
| `ai.css` | 910 | AI analysis modal layout, verdict, tiers, playbook |

---

## 5. Data model

### SQLAlchemy models (`api/models.py`)

| Model | Purpose |
|-------|---------|
| `User` | Telegram user (auto-created on first request) |
| `Tracker` | Saved query with filter config, alert thresholds |
| `TrackerEvent` | new_listing / price_drop events per tracker |
| `SavedSearch` | Saved search groups with filter config |
| `QuerySnapshot` | Periodic price stats snapshot per query |
| `QueryListingState` | Individual listing lifecycle per query |
| `LeadItem` | Deal pipeline items (status: new → researching → watching → negotiating → bought → reselling → sold) |
| `LeadItemPriceSnapshot` | Per-item price history for sparklines |
| `DealExpense` | Expense tracking per lead (delivery, repair, other) |
| `Contact` | Seller/buyer contacts per lead |

**Key:** `watchlist_items` table no longer exists. Watchlist rows
live in `lead_items` with `status='watching'`. Migration:
`20260427_0001_merge_watchlist_into_leads.py`. `/api/v1/watchlist/*`
endpoints remain as a proxy layer over `LeadItem`.

### Allowed lead statuses (DB constraint)

```
new → researching → watching → negotiating → bought → reselling → sold
```

`upsert_lead` refuses to demote an existing active/closed lead to
`watching` (returns 409). `DELETE /watchlist/{id}` is idempotent
(204 even if already promoted/deleted).

---

## 6. API surface

### Routers (`api/routers/`)

| Router | Endpoints | Auth |
|--------|-----------|------|
| `listings.py` | `/listings`, search, cheap deals | Public + user |
| `listing_detail.py` | `/listings/{ad_id}` | Public + user |
| `price_stats.py` | `/price-stats` | Public |
| `price_history.py` | `/price-history` | Public |
| `segments.py` | `/segments` | Public |
| `geography.py` | `/geography` | Public |
| `analytics.py` | `/analytics` | Public |
| `ai_analysis.py` | `/ai/analyze`, `/ai/quick-condition` | User, rate-limited |
| `trackers.py` | Tracker CRUD | User |
| `workflow.py` | `/leads`, `/watchlist` CRUD + transitions | User |
| `expenses.py` | `/expenses` CRUD | User |
| `contacts.py` | `/contacts` CRUD | User |
| `export.py` | `/export/leads` (Excel) | User |
| `currency.py` | `/currency-rates` | Public |
| `image_proxy.py` | `/img/{path}` (WebP/AVIF) | Public |
| `health.py` | `/health`, `/health/ready` | Public |
| `saved_searches.py` | `/saved-searches` | User |
| `risks.py` | `/risks/{ad_id}` | Public + user |

### Services (`api/services/`)

| Service | Purpose |
|---------|---------|
| `kufar_client.py` | HTTP client for Kufar API with retry/backoff, rate limiting |
| `aggregator.py` | Price stats computation, search mode filtering, query key building |
| `query_pipeline.py` | `QueryDataset`, `load_query_dataset()`, `fetch_category_totals()`, `KUFAR_CATEGORY_FAMILY` map |
| `ai_service.py` | `AIService` class: analyze, generate, quick_condition + dedupe helpers |
| `ai_guardrails.py` | Input/output validation for AI calls |
| `ai_listing_guardrails.py` | Validation for listing assistant |
| `ai_marketplace.py` | Marketplace-specific AI prompt construction |
| `cache.py` | `RedisCache` + `MemoryCache` (OrderedDict with TTL + LRU) |
| `currency_service.py` | BYN↔USD conversion |
| `deal_workflow.py` | Lead/watchlist CRUD, status transitions, liquidity scoring |
| `history_service.py` | Query snapshot upsert, listing state sync, price change detection |
| `listing_mapper.py` | Raw Kufar ad dicts → `ListingItem`/`ListingDetailResponse` schemas |
| `market_signals.py` | Market signal computation for opportunity board |
| `parallel_kufar.py` | Parallel Kufar fetch with semaphore |
| `reseller_tools.py` | Flip estimates, duplicate detection, tracker filter matching, deal score/verdict |
| `risk_detector.py` | Listing risk assessment |
| `workflow_store.py` | `ensure_user`, `upsert_lead` (race-safe with SAVEPOINT retry) |

---

## 7. Frontend views & interactions

### Views (6 tabs)

| Tab | ID | Content |
|-----|----|---------|
| Обзор | `overview-view` | Summary strip, price distribution chart, segments |
| Объявления | `ads-view` | Listing cards, filter dropdown, category chips |
| Отслеживание | `tracking-view` | Tracker cards, tracker events with virtual scroll |
| Выгодно | `cheap-view` | Cheap deals (discount-filtered listings) |
| Мониторинг | `monitoring-view` | Watchlist cards with price sparklines |
| Покупки | `deals-view` | Deal pipeline, profit dashboard, hero stats |

### Key interactions

- **Tap on listing card** → opens detail modal (`.listing-top` click handler)
- **Long-press on listing card** → bottom-sheet menu (В покупки / В избранное / Открыть на Kufar)
- **Pinch-zoom on detail photos** → `attachPinchZoom()` with anchor-point model
- **Swipe between photos** → opacity-crossfade (not live drag)
- **Filter dropdown** → keyframe animation open/close (no max-height thrashing)
- **Double-tap on photo** → toggle 1×/2× zoom centered on tap point
- **Swipe on watching cards** → left=delete, right=promote-to-Покупки (via `makeSwipeable`)

---

## 8. What's done (recent sprint, `bad-app` branch)

```
b43dae6 Rewrite pinch-zoom, tap card for detail, kill blue tint on listings
6e3dd06 UI depth: Geist font, accent-secondary for AI, fix pinch-zoom lag
a6a47ef UI/UX refresh: typography scale, kill glassmorphism, micro-interactions, polish
f7c835d Flatten visual nesting: remove box-in-box-in-box across all views
a566262 Remove comparison feature from Обзор and entire codebase
11a866c Trackers: fix new-tracker invisible-until-refresh + remove alert feature
220a628 Photo swipe: drop live drag, use simple opacity-crossfade
4e20a38 Photo swipe: rAF-coalesced touchmove + GPU layer hint
e0582ea Photo modal: scoped finger-follow swipe + sturdier snapshot persist
6736d10 Fix view-flash on discount preset clicks
6d9a65e Search perf overhaul: 6s "iphone 13" → ~1s wall-clock
91623bc Fix tabs render crashes + 2x2 layout on narrow screens
1892e17 Singleflight Kufar fetch + fold "Выгодно" into "Объявления"
c618ca0 Strip pipeline funnel + fix watchlist visual + share Kufar dataset cache
90ddece Search perf: collapse CSS waterfall, preconnect Kufar, lower fetch cap
cd4c17e Fast-path images + bypass SW cache for /leads & /watchlist
27530f8 Paginate /listings + display-aware thumbnail sizing
210bf48 Image proxy perf: thread-pool transcode, semaphore, LRU, keep-alive
3952b13 Modularize style.css: 7 topical partials behind a thin @import shell
5132847 N+1 fix: bulk-load last snapshot prices in watchlist refresh
553889b ROI / win-rate dashboard: server-side aggregation + funnel
41f1bd1 Tracker hard alerts: price threshold + discount threshold
12497b3 WebP/AVIF image proxy for Kufar JPEG thumbnails
4221f2b Side-by-side comparison: metric matrix + winner highlight
139f281 Tracker trend-reversal alerts: "цена опять растёт после падения"
5266e4c Smart re-search suggestions: top-N tokens from result set as chips
8649774 Excel export for leads + richer empty states on tracker surfaces
c4bcfb4 Service worker: stale-while-revalidate for read-only API + offline shell
512ac82 Long-press menu: bottom-sheet quick actions on listing cards
15556b9 Watchlist sparkline: per-row price history + inline SVG trend
c4a07bf Merge watchlist_items into lead_items (status='watching')
```

### High-impact changes to remember

- **`watchlist_items` table gone.** Rows in `lead_items` with
  `status='watching'`. `/api/v1/watchlist/*` is a proxy layer.
- **Comparison feature removed.** `api/routers/compare.py` deleted,
  frontend cleaned up (-1389 lines).
- **Category filter overhaul.** Chip counts mirror kufar.by sidebar
  (parallel `cat=<id>&size=200` per chip). Listings pill = Kufar
  raw total (broad) or post-filter count (cat-scoped).
- **Filter reset semantics.** `search(target, { keepFilters })` —
  default `false`. Only "Применить" passes `keepFilters: true`.
- **AI dedupe.** Server-side paraphrase collapse for all list
  fields. Cross-field: `condition.notes` removed from
  `watch_out`/`red_flags`.
- **Pinch-zoom rewritten.** Anchor-point model with
  `transformOrigin: 0 0`. No `getBoundingClientRect` on touchmove.
  `baseRect` snapshotted once on touchstart.
- **Tap card = detail.** Click on `.listing-top` opens detail.
  "Подробнее" button removed. 3 action buttons: В покупки,
  В избранное, Kufar.
- **Blue tint killed on listings.** All accent color-mix tints
  removed from listing cards, buttons, price, hover, thumb border.
  Buttons are neutral transparent+border style.

---

## 9. Known issues & rough edges

### Backend

- **Kufar API has no `otype` param since 2026.** Seller type
  filtering is client-side only. If Kufar adds it back, the
  client-side filter becomes redundant.
- **Kufar kopeck heuristic.** Prices sometimes come in kopecks×100
  (7,863,900 for 78639 BYN). `_normalize_response_ads` divides by
  100 if it detects kopeck-scale numbers — fragile but works in
  practice.
- **AI model branching.** `_is_gemini` / `_is_gemma_legacy` in
  `ai_service.py`. Gemini 2.5 Flash supports `response_format`;
  Gemma 4 does not. If a third model is added, this branching
  needs refactoring.
- **AI analysis timeout.** Can take 30-90s. Nginx
  `proxy_read_timeout: 300s` must not be lowered.
- **No pagination on `/leads` or `/watchlist`.** Returns all items.
  Will be a problem if a user has hundreds of leads.
- **`kufar_max_ads_per_query = 1500`.** Lowered from 5000 for perf.
  Statistically indistinguishable median, but rare edge cases
  (very broad queries) may miss long-tail listings.

### Frontend

- **`api_events.js` is 1015 lines.** Largest JS file. Handles all
  DOM event binding. Hard to navigate. Could benefit from splitting
  by view, but the delegation pattern makes it tricky.
- **`api_ai.js` is 1192 lines.** Second largest. AI modal with
  progress animation, polling, template rendering. Complex state
  machine.
- **No TypeScript.** Vanilla JS with no type checking. Factory
  pattern makes cross-module dependencies implicit — if
  `createAppRenderers` doesn't return a function, downstream
  modules get `undefined` silently.
- **CSS is ~8,900 lines.** 7 partials help, but `brand.css` alone
  is 1,720 lines. Some redundancy between partials.
- **Service worker cache version.** Must be bumped manually in
  `sw.js` when deploying new builds. If forgotten, users see stale
  content for up to 24h.
- **`?v=` cache-bust param.** Must be bumped in `index.html` for
  every CSS/JS reference when any file changes. Easy to forget.
- **No responsive breakpoints for tablets.** Layout is mobile-first
  (Telegram Mini App) but breaks awkwardly on wider screens.

### Infrastructure

- **Postgres runs as a host container** (not in docker-compose).
  `marketplace_postgres` on `:5433`. If it's not running, the API
  won't start.
- **No CI/CD.** Manual deploy via docker-compose on a VPS.
- **No automated browser tests.** Only Python unit/integration
  tests + JS syntax checks. No Playwright/Cypress for the frontend.
- **Cloudflared tunnel required for Telegram.** Without it, the
  Mini App can't load (Telegram requires HTTPS).

---

## 10. Frontend state shape

```js
state = {
  query: "",                     // current text in search input
  strictSearch: false,           // strict mode toggle
  category: null | number,       // active cat=X filter
  categories: [],                 // [{ id, label, count }] for chips
  condition: "",                  // filter: "", "new", "used"
  sellerType: "",                 // filter: "", "private", "shop"
  minPrice: null, maxPrice: null, // price range filter
  regionName: "",                 // region filter
  pendingCategory/Condition/etc,  // dropdown pending values
  filterDropdownOpen: false,
  searchRequestId: 0,            // stale-response guard
  sort: "newest",
  discountFromPercent: 10,
  discountToPercent: 30,
  loading: false,
  error: null,
  stats: null,                   // /price-stats response
  listings: [],                  // /listings response
  listingsTotal: 0,
  listingsHasMore: false,
  dealListings: [],              // cheap deals
  segments: null,                // /segments response
  geography: [],                 // /geography response
  history: [],                   // /price-history response
  leads: [],                     // /leads response (excludes watching)
  leadFilter: "all",
  watchlist: [],                 // /watchlist response (= LeadItem watching)
  trackerEvents: [],
  // Monotonic request IDs for stale-response guards:
  _leadsRequestId: 0,
  _watchlistRequestId: 0,
  _detailRequestId: 0,
  _historyRequestId: 0,
  _listingsRequestId: 0,
  _dealsRequestId: 0,
  // ... more in app_core.js
}
```

`render_*.js` reads from `state.*` exclusively — never mutate
state inside a render function.

---

## 11. CSS design system

### Token architecture (`tokens.css`)

```
--font: "Geist", sans-serif
--font-mono: "JetBrains Mono", monospace

--text-xs/sm/base/lg/xl/2xl/3xl/display  (11px → 32px scale)
--space-1..5, --r-sm/md/lg/xl

--t-fast/mid/slow  (120/200/350ms)
--easing-standard/deceleration

Colors (dark/light themes via [data-theme]):
  --bg, --bg-card, --bg-elevated, --bg-hover, --bg-surface-tint
  --text, --text-muted, --text-dim, --text-secondary
  --accent (#3b82f6 dark / #2563eb light) — primary UI
  --accent-secondary (#8b5cf6 / #7c3aed) — AI features
  --green, --red, --amber, --violet, --cyan
  --border, --border-hover
```

### Key design decisions

- **Primary accent (blue)** — tabs, primary buttons, search focus,
  stat highlights, active states
- **Accent-secondary (violet)** — AI features only: AI button,
  AI sheet, AI sections, la-* components, AI history
- **No accent tint on listing cards** — pure bg-card, neutral
  borders, no blue color-mix anywhere on listings
- **Listing action buttons** — neutral transparent+border (not
  filled blue). Kufar link uses `listing-btn--kufar` (same style)
- **Cards** — `var(--bg-card)` + `var(--border)`, no accent tint
- **Skeleton shimmer** — `@keyframes skeleton-shimmer` gradient
- **Micro-interactions** — card hover translateY(-2px), chip hover
  translateY(-1px), all using `--t-fast` + `--easing-standard`

---

## 12. Pinch-zoom implementation notes

The zoom uses an **anchor-point model** (rewritten from scratch):

```
transform: translate3d(tx, ty, 0) scale(s)
transform-origin: 0 0
```

Core invariant: the image point `(px, py)` in image-local coords
stays at viewport position `(vx, vy)`:

```
tx = vx - px * s
ty = vy - py * s
```

- `viewportToImage(vx, vy)` converts viewport → image-local
- On pinch-start: anchor `(px, py)` = pinch center in image-local
- On touchmove: `tx = cvx - pinchAnchorPx * nextS` — anchor
  tracks the moving pinch center
- `baseRect` snapshotted once on touchstart (temporarily removes
  transform to get natural size). No `getBoundingClientRect` on
  touchmove.
- `clampTranslate()` uses `window.innerWidth/Height` + `baseRect`
  dimensions. Requires 40px margin visible on each side.
- `will-change: transform` added when zoomed, removed at scale=1
- Double-tap toggles 1×↔2×, zooming toward tap point

---

## 13. Kufar API quirks

- Endpoint: `https://api.kufar.by/search-api/v2/search/rendered-paginated`
- Params: `cur` (not `currency`), `cat`, `rgn`, `cnd`, `size`,
  `sort`, `cursor`. **No `otype`** — seller_type is client-side.
- `cat=<id>` widens Kufar's matching rules. Post-filter count may
  differ significantly from Kufar `total`.
- Prices in **kopecks × 100** (heuristic normalization).
- Pagination: 25 pages × 200 ads = 5000 cap, but
  `kufar_max_ads_per_query = 1500` (3× faster, same median quality).
- Image base URL: `https://rms.kufar.by/v1/gallery/`

---

## 14. AI service details

- **Gemini 2.5 Flash** (production): supports
  `response_format: {"type":"json_object"}`, uses it via
  `_is_gemini` branch.
- **Gemma 4** (legacy fallback): does NOT support `response_format`
  (causes empty `content`). Reads from `reasoning` field as
  fallback. `max_tokens` ≥ 2200.
- **Two-call analysis**: `analyze_listing_parallel` runs Call A
  (price & market) and Call B (condition & risks) staggered by 1s
  to avoid 429 rate limits, then merges + dedupes.
- **Dedupe**: `dedupe_analysis_payload` collapses paraphrase
  duplicates across all list fields. Cross-field rule: anything in
  `condition.notes` is removed from `watch_out`/`red_flags`.
- **Rate limit**: `ai_hourly_limit = 10` per user.
- **Cache**: AI results cached for `ai_cache_hours = 1` hour.
- **Timeout**: `ai_analysis_timeout = 150s`,
  `ai_quick_condition_timeout = 45s`.

---

## 15. Configuration (`api/config.py`)

| Env var | Default | Notes |
|---------|---------|-------|
| `BOT_TOKEN` | required | Telegram bot token + HMAC key |
| `DATABASE_URL` | required | asyncpg connection string |
| `REDIS_URL` | required | Redis connection string |
| `API_BASE_URL` | required | Public API URL |
| `MINI_APP_URL` | required | Public frontend URL |
| `KUFAR_REQUEST_DELAY` | 1.0 | Delay between Kufar API calls |
| `KUFAR_PARALLEL_SEMAPHORE` | 2 | Max parallel Kufar requests |
| `KUFAR_TIMEOUT` | 15.0 | Kufar HTTP timeout |
| `KUFAR_MAX_ADS_PER_QUERY` | 1500 | Pagination cap |
| `ALERT_CHECK_INTERVAL` | 30 | Scheduler check interval (min) |
| `CACHE_TTL_SECONDS` | 300 | Redis cache TTL |
| `AUTO_REMOVE_MISSING_DAYS` | 7 | Auto-remove missing listings |
| `MAX_TRACKERS_PER_USER` | 50 | Tracker limit |
| `DEBUG` | false | Bypasses Telegram auth, opens CORS |
| `DB_POOL_SIZE` | 10 | SQLAlchemy pool size |
| `DB_MAX_OVERFLOW` | 20 | SQLAlchemy max overflow |
| `AI_API_KEY` | None | Together AI API key |
| `AI_BASE_URL` | `https://api.together.xyz/v1` | OpenAI-compatible base |
| `AI_MODEL` | `google/gemma-4-31B-it` | Model ID |
| `AI_MAX_IMAGES` | 3 | Max images per analysis |
| `AI_CACHE_HOURS` | 1 | AI result cache duration |
| `AI_HOURLY_LIMIT` | 10 | Rate limit per user |
| `AI_PROXY_URL` | None | HTTP proxy for AI calls |
| `AI_ANALYSIS_TIMEOUT` | 150 | Full analysis timeout (s) |
| `AI_QUICK_CONDITION_TIMEOUT` | 45 | Quick condition timeout (s) |

---

## 16. Migrations

21 migrations in `migrations/versions/`. Key ones:

| Migration | Purpose |
|-----------|---------|
| `20260405_0001` | Create trackers |
| `20260406_0002` | History and price tracking |
| `20260406_0006` | Workflow items (leads + watchlist) |
| `20260407_0009` | Users table + FK constraints |
| `20260407_0010` | Expenses, contacts, sold fields |
| `20260427_0001` | **Merge watchlist into leads** (critical) |
| `20260428_0001` | Lead item price snapshots |
| `20260428_0002` | Tracker alert thresholds |

---

## 17. Conventions

- **Ruff**: `E/W/F/I/N/UP/B/SIM/TCH`, line length 99, isort
  known-first-party `[api, bot, scheduler]`. `UP017` ignored
  (`datetime.UTC` doesn't exist on the `datetime` class — use
  `from datetime import UTC`).
- **Async everywhere**: SQLAlchemy async sessions, httpx async,
  aiogram 3.
- **No frontend build step.** Don't add bundlers. Vanilla JS only.
- **XSS prevention**: `escapeHtml()` for text, `safeUrl()` for
  href/src (blocks `javascript:` / `data:`).
- **Currency**: BYN in DB always.
- **No comments unless asked.** Docstrings on new helpers are fine.
- **Hex colors only inside theme blocks** (tokens.css dark/light).
  Use CSS vars elsewhere.
- **CSS rebuild**: `uv run python scripts/rebuild_css.py` after
  editing any partial.
- **`?v=` cache-bust**: must be bumped in `index.html` for all
  CSS/JS references after any change.

---

## 18. How to verify your work

1. `uv run ruff check .` — clean.
2. `uv run pytest` — 215 passing.
3. `node --check frontend/js/<changed-file>.js` — syntax OK.
4. Backend: `curl http://127.0.0.1:8010/api/v1/health`.
5. Frontend: open via Telegram tunnel (`./start-local.sh --with-tunnel`).
6. CSS: `uv run python scripts/rebuild_css.py` + bump `?v=`.

---

## 19. Debug recipes

- **"Внутренняя ошибка сервера"** — `tail -50 /tmp/rafuk-api.log`
- **MissingGreenlet** — forgot `await session.refresh(obj)` after
  `session.commit()`.
- **Kufar 422 "translation not found"** — param name typo. Only
  `cur`, `cat`, `rgn`, `cnd`, `size`, `sort`, `cursor` are valid.
- **`unique constraint chk_lead_items_status`** — you added a
  status the DB constraint doesn't know. Check migration
  `20260427_0001` for the allowed enum.
- **Frontend tab counter stale** — call both `loadLeads()` and
  `loadWatchlist()` after a mutation.
- **AI returns empty content** — you used `response_format` with
  Gemma 4. Only Gemini supports it.
- **Pinch-zoom jumps on start** — check that `transformOrigin` is
  `"0 0"` and anchor point is computed from `viewportToImage()`.
- **CSS changes not showing** — rebuild CSS + bump `?v=`.
