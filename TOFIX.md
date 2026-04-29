# Rafuks — Issues to Fix

This document lists all remaining issues found during the comprehensive code review on 2026-04-29.
Already-fixed issues are at the bottom for reference. Each unfixed issue includes file paths, line numbers,
root cause analysis, and specific remediation instructions.

**Project**: Rafuks — Telegram Mini App for marketplace analytics on kufar.by
**Stack**: FastAPI + SQLAlchemy async + PostgreSQL + Redis + vanilla JS + Telegram WebApp
**Commands**: `uv run pytest` (tests), `uv run ruff check .` (lint), `uv run alembic -c migrations/alembic.ini upgrade head` (migrations)

---

## CRITICAL (unfixed)

### C1. Float used for monetary values — silent rounding errors

**Files**: `api/models.py` — multiple columns across `Tracker`, `TrackerEvent`, `QuerySnapshot`, `QueryListingState`, `LeadItem`, `LeadItemPriceSnapshot`

**Problem**: All `price_byn` columns use SQLAlchemy `Float` (IEEE 754 double precision). Values like `12.10 BYN` are stored as `12.099999999999999`. This causes:
- Silent accumulation errors in profit/ROI calculations
- Incorrect price-drop detection (the `0.5` epsilon in `api/services/history_service.py:224` is itself an acknowledgment of float imprecision)
- Inconsistent comparisons between `LeadItem.buy_price_byn` (which correctly uses `Numeric(10, 2)`) and `LeadItem.price_byn` (which uses `Float`)

**Affected columns** (all use `Float`, should be `Numeric(12, 2)`):
- `TrackerFiltersMixin.min_discount_percent` — `models.py:111`
- `TrackerFiltersMixin.max_price_byn` — `models.py:112`
- `Tracker.last_seen_price_byn` — `models.py:143`
- `QuerySnapshot.mean_byn`, `median_byn`, `min_byn`, `max_byn` — `models.py:184-187`
- `QueryListingState.last_price_byn` — `models.py:203`
- `TrackerEvent.price_byn`, `delta_byn` — `models.py:247-248`
- `LeadItem.price_byn` — `models.py:278`
- `LeadItem.target_resale_byn` — `models.py:281`
- `LeadItem.initial_price_byn`, `market_median_byn` — `models.py:306-307`
- `LeadItemPriceSnapshot.price_byn` — `models.py:357`

**Already correct** (for reference): `LeadItem.buy_price_byn` and `LeadItem.sold_price_byn` use `Numeric(10, 2)`.

**Fix**:
1. Change all `Float` price columns to `Numeric(12, 2)` in `api/models.py`. Import `Numeric` from `sqlalchemy`.
2. Create an Alembic migration: `ALTER COLUMN ... TYPE NUMERIC(12, 2)` for each affected column. PostgreSQL can cast `float → numeric` automatically.
3. Update `api/services/aggregator.py:150-165` (`normalize_price_byn`) — the return type changes from `float` to `Decimal`. All callers that do `float(price)` will need updating.
4. Update `api/services/reseller_tools.py` — `compute_deal_score` and related functions compare prices; ensure they work with `Decimal`.
5. Update `scheduler/collector.py` — price comparison in `sync_query_listing_states` and `detect_price_drops`.
6. Run all tests after.

**Note**: This is a significant refactor. The `Numeric` type returns `Decimal` from SQLAlchemy, which is not JSON-serializable by default. Add `from decimal import Decimal` and use `float(x)` at serialization boundaries (Pydantic schemas, JSON responses).

---

### C2. No CHECK constraint or enum on `LeadItem.status` in ORM definition

**File**: `api/models.py`, class `LeadItem` (lines ~270-336)

**Problem**: The `status` column is `String(32)` with no `CheckConstraint` in the model's `__table_args__`. The database has `chk_lead_items_status` (added in migration `20260407_0008`, updated in `59ad46ea83ba`) but the ORM doesn't know about it. Any arbitrary string can be inserted from application code. `alembic autogenerate` won't recreate or verify this constraint.

**Current legal values** (from migration `59ad46ea83ba`): `'new', 'reviewing', 'in_progress', 'negotiating', 'deferred', 'closed', 'abandoned', 'bought', 'sold'`, plus `'watching', 'researching', 'skipped'` from the watchlist merge migration.

**Fix**:
1. Define a `StrEnum`:
```python
import enum

class LeadStatus(str, enum.Enum):
    NEW = "new"
    WATCHING = "watching"
    RESEARCHING = "researching"
    IN_PROGRESS = "in_progress"
    NEGOTIATING = "negotiating"
    BOUGHT = "bought"
    SOLD = "sold"
    SKIPPED = "skipped"
    DEFERRED = "deferred"
    CLOSED = "closed"
    ABANDONED = "abandoned"
```
2. Add `CheckConstraint` to `LeadItem.__table_args__`.
3. Change the column type to use the enum for type safety.
4. Validate incoming status values in the Pydantic schemas (`api/schemas.py`).
5. Create a centralized `transition_lead_status(current, target)` function in `api/services/deal_workflow.py` that validates legal transitions (e.g., can't go `new → sold` without `bought`).

---

### C3. `sync_query_listing_states` has TOCTOU race condition

**File**: `api/services/history_service.py`, lines ~180-244

**Problem**: The function loads all existing `QueryListingState` rows for a query, then creates new ones for ads not in the set. When two concurrent scheduler ticks process the same query, both SELECTs return the same existing set, both attempt to INSERT the same `(query, ad_id)` pair, and one fails with a unique constraint violation on `uq_query_listing_state`. The exception is unhandled and rolls back the entire batch.

This is the same pattern that was already fixed in `upsert_query_snapshot` (using `begin_nested() + IntegrityError`), but `sync_query_listing_states` wasn't fixed.

**Fix**: Use PostgreSQL upsert via SQLAlchemy's `insert().on_conflict_do_update()`:
```python
from sqlalchemy.dialects.postgresql import insert as pg_insert

# For each new listing state:
stmt = pg_insert(QueryListingState).values(
    query=query, ad_id=ad_id, ...
).on_conflict_do_update(
    constraint="uq_query_listing_state",
    set_={"title": title, "link": link, "active": True, ...}
)
await session.execute(stmt)
```

Alternatively, wrap individual INSERTs in `begin_nested()` savepoints with `IntegrityError` handling (same pattern as `upsert_query_snapshot`).

---

## HIGH (unfixed)

### H1. Public analytics endpoints enable Kufar API amplification attacks

**Files**:
- `api/routers/listings.py` (line 42-245)
- `api/routers/listing_detail.py` (line 28-85)
- `api/routers/price_stats.py`
- `api/routers/segments.py`
- `api/routers/geography.py`
- `api/routers/price_history.py`

**Problem**: These endpoints do not depend on `get_telegram_user` — they are fully public. Each request triggers multiple Kufar API calls (up to 25 pages × 200 ads = 5000 ads per query). An attacker can script arbitrary requests, causing the server to proxy massive volumes to `api.kufar.by`, potentially getting the server's IP banned.

The rate limiter (`api/limiter.py`) helps but is per-IP and can be bypassed with distributed requests.

**Fix**: Add `Depends(get_telegram_user)` to all analytics endpoints, OR implement a lightweight API key/token mechanism for non-Telegram clients. At minimum, add Telegram auth to:
- `GET /api/v1/listings`
- `GET /api/v1/listings/{ad_id}`
- `GET /api/v1/price-stats`
- `GET /api/v1/segments`
- `GET /api/v1/geography`
- `GET /api/v1/price-history`

**Note**: This is a product decision — if public access to analytics is intentional, consider adding a stricter rate limit specifically for these endpoints (e.g., 20 req/min instead of 60).

---

### H2. AI export report stores user-generated HTML server-side (stored XSS risk)

**File**: `api/routers/ai_analysis.py`, lines 1081-1137

**Problem**: The `/api/v1/ai/export-report` endpoint accepts arbitrary HTML from the client, applies regex-based sanitization (`_sanitize_export_html`), and stores it in memory. The regex sanitization is incomplete — edge cases like `\x00` between `<` and tag name, or `<svg><foreignobject><body onload=...>` can bypass it.

The CSP header (`script-src 'none'`) is the primary defense and is solid. But if CSP is ever relaxed, stored XSS is possible.

**Fix**: Replace `_sanitize_export_html` with a proper HTML sanitizer library:
```bash
uv add nh3
```
```python
import nh3

def _sanitize_export_html(html: str) -> str:
    return nh3.clean(
        html,
        tags={
            "div", "span", "p", "h1", "h2", "h3", "h4", "h5", "h6",
            "table", "thead", "tbody", "tr", "th", "td",
            "ul", "ol", "li", "strong", "em", "b", "i", "u",
            "br", "hr", "img", "a", "blockquote", "code", "pre",
        },
        attributes={
            "*": {"class", "style"},
            "img": {"src", "alt", "width", "height"},
            "a": {"href", "target"},
            "td": {"colspan", "rowspan"},
            "th": {"colspan", "rowspan"},
        },
        clean_content_tags={"script", "style"},
    )
```

---

### H3. `initData` replay window is 24 hours

**File**: `api/middleware/telegram_auth.py`, line 23

**Problem**: `max_age_seconds` defaults to 86400 (24 hours). A captured initData string is valid for a full day. If an attacker intercepts the `X-Telegram-Init-Data` header (public WiFi, browser DevTools, log exposure), they can impersonate the user for 24 hours.

**Fix**: Change `max_age_seconds` from 86400 to 300 (5 minutes):
```python
# In verify_telegram_init_data function:
if auth_date_diff > 300:  # was 86400
    raise ValueError("initData expired")
```

Or make it configurable via an env var `TELEGRAM_INIT_DATA_MAX_AGE_SECONDS` with default 300.

---

### H4. Rate limiter silently falls back to per-process memory when Redis is down

**File**: `api/limiter.py`, lines 71-75

**Problem**: When Redis is unreachable, the rate limiter falls back to in-memory storage. In a multi-worker deployment (gunicorn with N uvicorn workers), each worker enforces its own rate limits independently, effectively multiplying the allowed rate by N.

**Fix**:
1. Log the fallback at ERROR level (currently WARNING).
2. Add a health check flag that reports degraded rate limiting.
3. Optionally, reject requests with a 503 when Redis is down instead of silently degrading:
```python
# In the rate limiter middleware:
if storage.backend == "memory":
    logger.error("Rate limiter using in-memory fallback — limits not shared across workers")
```

---

### H5. `_inflight_dataset_futures` dict is unbounded

**File**: `api/services/query_pipeline.py`, lines ~155, 240-263

**Problem**: The module-level `_inflight_dataset_futures` dict grows with every unique query. The `finally` block cleans up entries, but if the process runs for a long time with many unique queries, the dict can accumulate stale entries between cleanup cycles. There is no periodic sweep or size limit.

**Fix**: Add a size limit:
```python
import logging

_MAX_INFLIGHT = 500

# In load_query_dataset, before creating a new future:
if len(_inflight_dataset_futures) > _MAX_INFLIGHT:
    logger.warning("Purging %d stale inflight futures", len(_inflight_dataset_futures))
    _inflight_dataset_futures.clear()
```

---

### H6. `KufarClient._get_client` is not thread-safe

**File**: `api/services/kufar_client.py`, lines 39-42

**Problem**: Two concurrent calls to `_get_client()` can both see `self._http_client is None` and each create a new `httpx.AsyncClient`. The old client (whichever one "loses" the race) is never closed, leaking the connection pool.

The same issue exists in:
- `api/services/currency_service.py:20-23` (`CurrencyService._get_client`)
- `api/services/ai_service.py:661-669` (`AIService._get_client`)

**Fix**: Use `asyncio.Lock` to guard client creation:
```python
def __init__(self, settings):
    self._settings = settings
    self._http_client: httpx.AsyncClient | None = None
    self._client_lock = asyncio.Lock()

async def _get_client(self) -> httpx.AsyncClient:
    if self._http_client is None or self._http_client.is_closed:
        async with self._client_lock:
            if self._http_client is None or self._http_client.is_closed:
                self._http_client = httpx.AsyncClient(timeout=self._settings.kufar_timeout)
    return self._http_client
```

---

## MEDIUM (unfixed)

### M1. `TimestampMixin` uses `DateTime` without `timezone=True`

**File**: `api/models.py`, line ~93

**Problem**: `TimestampMixin.created_at` is `DateTime` (no timezone), while other datetime columns in the same models use `DateTime(timezone=True)`. In PostgreSQL, `timestamp without time zone` and `timestamp with time zone` are different types. Mixing them prevents index usage in comparisons and can produce wrong results if the server timezone changes.

**Fix**:
1. Change `TimestampMixin.created_at` to `DateTime(timezone=True)`.
2. Create Alembic migration: `ALTER TABLE trackers ALTER COLUMN created_at TYPE TIMESTAMPTZ USING created_at AT TIME ZONE 'UTC'` (same for `lead_items`).

---

### M2. `onupdate=func.now()` does not fire on bulk UPDATEs

**Files**:
- `api/models.py` — `QueryListingState.updated_at` (lines ~213-217)
- `api/models.py` — `LeadItem.updated_at` (lines ~316-320)

**Problem**: SQLAlchemy's `onupdate` only fires for ORM-level `session.add()` / `session.flush()`. Bulk `session.execute(update(...))` statements do NOT trigger it. If any bulk UPDATE is used, `updated_at` stays stale.

**Fix**: Explicitly set `updated_at = func.now()` in all bulk UPDATE statements. Example:
```python
await session.execute(
    update(LeadItem)
    .where(LeadItem.id == lead_id)
    .values(status=new_status, updated_at=func.now())
)
```

---

### M3. `deleteAccount` uses `confirm()` — not available in Telegram WebView

**File**: `frontend/js/app_actions.js`, line ~534

**Problem**: `confirm("Все ваши данные будут безвозвратно удалены. Продолжить?")` may not work in Telegram's WebView. If it doesn't render, the function either always proceeds (dangerous for a destructive operation) or always returns (broken feature).

**Fix**: Replace with a custom in-app confirmation dialog, similar to the existing "Clear events" / "Clear all leads" double-confirm pattern. Create a modal with two-step confirmation (type "DELETE" to confirm).

---

### M4. Virtual list rebuilds all DOM nodes on every scroll

**File**: `frontend/js/virtual_list.js`, lines 67-121

**Problem**: `renderVisibleItems` calls `domClear(viewport)` and rebuilds all visible items from scratch on every scroll (throttled by rAF). For tracker events with complex cards, this creates significant GC pressure and re-attaches event listeners on each render.

**Fix**: Implement a recycling approach where only entering/exiting items are created/destroyed. Items that remain visible are left untouched. Key approach:
1. Track which items are currently rendered.
2. On update, diff the new visible range against the current one.
3. Remove items that scrolled out, add items that scrolled in.
4. Reposition existing items that moved.

---

### M5. `localStorage` operations lack quota protection

**File**: `frontend/js/app_core.js`, lines 277-294

**Problem**: `saveRecentSearches` uses try/catch for `localStorage.setItem` but silently ignores `QuotaExceededError`. In Telegram WebView, localStorage quotas can be very restrictive. Once the quota is hit, saves silently fail on every search and recent searches are lost on next app launch.

**Fix**: On `QuotaExceededError`, prune the oldest entries and retry:
```javascript
function saveRecentSearches(searches) {
    try {
        localStorage.setItem(RECENT_KEY, JSON.stringify(searches));
    } catch (e) {
        if (e.name === "QuotaExceededError" && searches.length > 1) {
            saveRecentSearches(searches.slice(0, Math.ceil(searches.length / 2)));
        }
    }
}
```

---

### M6. `loadWatchlist` and `loadLeads` fire redundant `loadAnalytics`

**File**: `frontend/js/api_leads.js`, line 56

**Problem**: Both `loadLeads` and `loadWatchlist` call `void loadAnalytics()` at the end. When called in parallel, two `loadAnalytics` requests race. The stale-request guard prevents the wrong response, but the redundant network request is wasteful.

**Fix**: Deduplicate by checking `state.analytics.loading` before firing:
```javascript
if (!state.analytics.loading) {
    void loadAnalytics();
}
```

---

### M7. `double-click` prevention Set has no timeout

**Files**:
- `frontend/js/app_actions.js`, lines 235-305 (`_inflightAdMutations`)
- `frontend/js/api_watchlist.js` (`_inflightAd`, `_inflightWatchId`)

**Problem**: If a request hangs indefinitely (server down without closing connection), the ad_id stays in the Set forever, permanently blocking that ad from being added to leads/watchlist.

**Fix**: Auto-remove from the set after a timeout:
```javascript
_inflightAdMutations.add(adId);
setTimeout(() => _inflightAdMutations.delete(adId), 30000); // 30s safety net
```

---

### M8. Bot engine initialization race condition

**File**: `bot/database.py`, lines 13-18

**Problem**: `_get_init_lock()` checks and sets a module-level global without synchronization. In an async context, two coroutines can both observe `_init_lock is None` and each create their own lock. Also, `get_bot_engine()` (lines 21-26) creates the engine without any lock.

**Fix**: Initialize the lock at module load time:
```python
_init_lock: asyncio.Lock | None = None

def _get_init_lock() -> asyncio.Lock:
    global _init_lock
    if _init_lock is None:
        _init_lock = asyncio.Lock()
    return _init_lock
```
Or use `functools.lru_cache`:
```python
@functools.lru_cache(maxsize=1)
def _get_init_lock() -> asyncio.Lock:
    return asyncio.Lock()
```

---

### M9. Broad exception catching in scheduler hides bugs

**File**: `scheduler/collector.py`, lines 58-66

**Problem**: `_TRACKER_QUERY_ERRORS` includes `TypeError` and `ValueError`, which are very broad. A programming bug (e.g., passing wrong arguments) would be silently caught and logged as a "query processing error" rather than surfacing as a real bug.

**Fix**: Narrow the caught exceptions to database/HTTP/network errors:
```python
_TRACKER_QUERY_ERRORS: tuple[type[Exception], ...] = (
    httpx.HTTPError,
    KufarAPIError,
    sqlalchemy.exc.DBAPIError,
    sqlalchemy.exc.OperationalError,
    asyncio.TimeoutError,
)
```
Let `TypeError` and `ValueError` propagate so they surface bugs during development.

---

## Already Fixed (for reference)

These issues were fixed during the review session on 2026-04-29:

| # | File | Fix |
|---|------|-----|
| F1 | `api/services/parallel_kufar.py` | Silent exception swallowing → logging + preserve list length with empty responses |
| F2 | `tests/test_parallel_kufar.py` | Updated test for new non-throwing behavior + added partial failure test |
| F3 | `migrations/versions/20260429_0001_*.py` | Added `trend_reversal` to CHECK constraint |
| F4 | `api/models.py` | Added `alert_price_threshold` and `alert_discount_percent` to `TrackerFiltersMixin` |
| F5 | `frontend/js/app_actions.js` | `new Promise(async ...)` → plain `async function` |
| F6 | `frontend/js/app_actions.js` | Consent modal: `AbortController` for listener cleanup |
| F7 | `api/services/history_service.py` | `upsert_query_snapshot`: `begin_nested()` + `IntegrityError` handling |
| F8 | `api/services/cache.py` | Redis `INCR + EXPIRE` → atomic `SET NX EX` + `INCR` |
| F9 | `frontend/js/api_core.js` | `requestJson`: linked external + internal `AbortController` |
