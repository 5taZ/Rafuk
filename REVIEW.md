# Code Review -- Rafuks Project

## Summary

Deep review of 8 changed files across backend (Python/FastAPI) and frontend (vanilla JS) layers. Of the 13 findings from the previous debug scan, 2 are confirmed Critical, 4 are confirmed as Warnings, 2 are confirmed as Low/Info, and 5 are false positives. Additionally, 3 new findings were discovered during this review pass. The most impactful issues are: (1) `asyncio.gather` in `parallel_kufar.py` without `return_exceptions=True`, which crashes the entire fan-out on a single Kufar failure, and (2) inconsistent `DateTime(timezone=True)` in `TimestampMixin`, which silently drops timezone information on `Tracker` and `LeadItem` timestamps.

## Findings

### [CRITICAL] CR-01: `asyncio.gather` without `return_exceptions=True` causes cascading failures

**File:** `api/services/parallel_kufar.py:21`
**Status:** CONFIRMED
**Description:** Both `parallel_search` and `parallel_search_all` call `asyncio.gather()` without `return_exceptions=True`. If any single Kufar API call in the batch fails (network timeout, rate limit, HTTP error), the entire `gather` raises an exception and all results from successful parallel calls are discarded. Since this module is used by segment and category-total endpoints, a single transient failure in one search strategy kills the response for all strategies.
**Impact:** Segment price breakdowns return empty or 500 errors whenever any one condition/broad query times out. In the AI analysis pipeline (`ai_analysis.py:445-448`), the same pattern is used correctly with `return_exceptions=True`, showing the project is aware of the pattern but missed it here.
**Fix:**
```python
# parallel_kufar.py line 21
return list(await asyncio.gather(
    *(bounded_search(task) for task in tasks),
    return_exceptions=True,
))
```
Note: Callers (`load_segment_datasets`, `fetch_category_totals`) already handle `Exception` instances in the result list, so this change is backward-compatible.

---

### [CRITICAL] CR-02: `TimestampMixin.created_at` uses `DateTime` without `timezone=True`

**File:** `api/models.py:93`
**Status:** CONFIRMED
**Description:** `TimestampMixin.created_at` is declared as `mapped_column(nullable=False, server_default=func.now())` without specifying `DateTime(timezone=True)`. This means PostgreSQL stores it as `timestamp without time zone`, discarding timezone information. Meanwhile, `User.created_at` (line 46), `TrackerEvent.created_at` (line 254), and `Tracker.last_checked_at` (line 145) all use `DateTime(timezone=True)`. The `Tracker`, `LeadItem`, and `QuerySnapshot` models inherit this inconsistency through `TimestampMixin`.
**Impact:** When `func.now()` returns a timezone-aware datetime (which it does with PostgreSQL's `CURRENT_TIMESTAMP`), PostgreSQL silently truncates the timezone or stores it as UTC-implicit. Comparisons between tz-aware and tz-naive datetimes in Python raise `TypeError`, and scheduler timestamp comparisons (e.g., `tracker.last_checked_at` vs `datetime.now(UTC)`) will break or produce incorrect results when mixing tz-aware and tz-naive values.
**Fix:**
```python
class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
```
A database migration is required to alter the column type from `timestamp` to `timestamptz` on existing tables.

---

### [HIGH] WR-01: `primary_event` IndexError when `created_events` is empty

**File:** `scheduler/collector.py:560`
**Status:** CONFIRMED
**Description:** At line 560, `primary_event = created_events[0] if created_events else None` correctly guards against empty list. However, at lines 564-565, `primary_event.query` and `primary_event.link` are accessed without the `primary_event is not None` check that was added at line 567 (`if primary_event is not None`). The keyboard construction at line 562-567 correctly guards with `if primary_event is not None else None`, so the actual code is safe. **Re-reading the code more carefully: the ternary at line 560 correctly sets `primary_event` to `None` when empty, and line 562 guards with `if primary_event is not None`.** This is actually correctly handled.
**Re-assessment:** FALSE POSITIVE. The code at line 560 correctly handles the empty case with `primary_event = created_events[0] if created_events else None`, and the keyboard construction at lines 561-569 only accesses `primary_event.query` and `primary_event.link` inside the `if primary_event is not None` branch.

---

### [HIGH] WR-02: `asyncio.gather` in `parallel_kufar.py` without error isolation (duplicate of CR-01)

See CR-01 above.

---

### [HIGH] WR-03: Account deletion not atomic across Redis and PostgreSQL

**File:** `api/routers/consent.py:204-255`
**Status:** CONFIRMED
**Description:** The `delete_account` endpoint first deletes the user from PostgreSQL (line 230-231, `await session.commit()`), then clears Redis/AI caches in a best-effort `try/except` block (lines 234-253). If the Redis cache deletion fails (network error, Redis down), the PostgreSQL data is already committed and gone, but rate-limit counters (`ai_rate:<user_id>`) remain in Redis. If the user re-registers with the same Telegram ID, they could inherit the old rate-limit counter and be immediately rate-limited.
**Impact:** Low in practice -- the rate-limit counter expires after 1 hour (TTL=3600), and re-registration creates a new user. But the principle of atomicity is violated, and the audit log shows a successful deletion when some data remnants exist.
**Fix:** Reverse the order: clear caches first, then delete from PostgreSQL. If cache clearing fails, the user can retry deletion.

---

### [HIGH] WR-04: Auth error details leaked to client

**File:** `api/dependencies.py:78-82`
**Status:** CONFIRMED
**Description:** When `verify_telegram_init_data` raises `ValueError`, the exception message is passed directly to the client as `detail=str(exc)`. Error messages include "Invalid Telegram initData signature" and "initData is too old", which reveal the authentication mechanism's internal validation logic to potential attackers.
**Impact:** Information disclosure. An attacker can enumerate valid vs invalid signatures and determine the age validation window. This is standard practice in auth libraries to give the frontend useful error messages, but it aids attackers in understanding the auth flow.
**Fix:**
```python
except ValueError as exc:
    logger.warning("Telegram auth failed: %s", exc)
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid Telegram authentication",
    ) from exc
```

---

### [HIGH] WR-05: Flip estimates use `market_stats` instead of `reference.stats`

**File:** `api/services/listing_mapper.py:174`
**Status:** CONFIRMED
**Description:** In `build_listing_detail`, `deal_score` correctly uses `reference.stats` (line 138), which resolves to category-level stats when available. But `flip_estimates` at line 174 uses the raw `market_stats` parameter (query-level stats). When a category-specific price reference is active, the deal score and anomaly flags use the category median, but flip estimates use the broader query median. This inconsistency means a listing could be flagged as a "deal" relative to its category but show flip estimates based on the broader market, producing contradictory advice.
**Impact:** Confusing UX where deal score says "Хорошая сделка" based on category median but flip estimates show lower margins based on broader market. Same issue exists in `build_listing_item` at line 237.
**Fix:**
```python
# listing_mapper.py line 174
flip_estimates=compute_flip_estimates(ad, reference.stats),
# listing_mapper.py line 237
flip_estimates=compute_flip_estimates(ad, reference.stats),
```

---

### [MEDIUM] WR-06: No `session.rollback()` in scheduler -- partial state committed after errors

**File:** `scheduler/collector.py:406-609`
**Status:** CONFIRMED (but mitigated)
**Description:** The `check_trackers` function uses a single session for all query groups. When a query group fails (caught at line 600), the exception is logged but the session continues. The flush at line 599 commits successful groups incrementally, and the final `session.commit()` at line 609 commits everything including any dirty state from a failed group that wasn't rolled back.
**Impact:** If an exception occurs after `session.flush()` (line 557) but before the per-group `session.flush()` (line 599), partially constructed `TrackerEvent` objects could be committed without their corresponding Telegram notification being sent. However, the `async with session_factory() as session:` context manager does issue a rollback on the session when no explicit commit happens (the session exits without `commit()`), so this is partially mitigated. The real risk is that the code catches `_TRACKER_QUERY_ERRORS` at line 600 and continues to the next group, accumulating dirty state.
**Fix:** Add `await session.rollback()` inside the except block at line 600, or use savepoints (`begin_nested()`) for each query group.

---

### [LOW] IN-01: Semaphore bound to event loop at creation time

**File:** `api/routers/image_proxy.py:67-71`
**Status:** CONFIRMED (low severity)
**Description:** `_get_transcode_semaphore()` creates the `asyncio.Semaphore` lazily on first call. In Python 3.10, `Semaphore.__init__` binds to the running loop via `get_running_loop()`. If the first request happens during testing with a mock loop, or during uvicorn `--reload` where the loop is recreated, the semaphore could reference a stale loop. In Python 3.12+, this is mitigated because the loop binding is deferred.
**Impact:** Low. Production uvicorn uses a single event loop for the process lifetime. Only affects test scenarios or rapid reload cycles.
**Fix:** No action needed for production. Tests already call `_clear_cache_for_tests()` which resets the semaphore via `_http_client = None`.

---

### [LOW] IN-02: Stale futures across event loop restarts

**File:** `api/services/query_pipeline.py:155`
**Status:** CONFIRMED (low severity)
**Description:** `_inflight_dataset_futures` is a module-level dict of `asyncio.Future` objects. If the event loop is recreated (uvicorn `--reload`), futures from the old loop remain in the dict. The check `inflight.done()` at line 234 would work correctly (the old-loop future would never be "done"), but `await inflight` at line 237 would raise `RuntimeError: Task attached to a different loop`.
**Impact:** Low. The `finally` block at line 263 cleans up futures. Production uvicorn does not restart the loop. The only scenario is rapid reload during development.
**Fix:** Add a loop identity check:
```python
if inflight is not None and not inflight.done():
    if inflight.get_loop() is asyncio.get_running_loop():
        response = await inflight
    else:
        _inflight_dataset_futures.pop(sf_key, None)
```

---

### [FALSE POSITIVE] FP-01: `cache.py` `incr()` assigns expired TTL

**File:** `api/services/cache.py:69-89`
**Original Finding:** `incr()` method assigns expired TTL to new counters, making them vanish immediately.
**Status:** FALSE POSITIVE
**Explanation:** Reading lines 84-86 carefully:
```python
new_expires = (
    expires_at if entry else (time.monotonic() + ttl if ttl else 0.0)
)
```
When `entry` exists and the key has expired (lines 74-75 delete it and set `count = 1`), the code reaches line 84 with the deleted entry. At this point, `entry` was deleted from storage, so `entry` still references the old tuple (Python doesn't null it). However, the condition uses `entry` (the local variable) not a re-lookup. Since the `del self._storage[key]` at line 75 removes it from storage but `entry` is still truthy (a tuple), the ternary takes the `expires_at` branch, which is the OLD expired timestamp. This IS a bug.

**Re-assessment:** On closer reading, this IS actually a real bug. When a key exists but is expired, line 75 deletes it from storage. Then `entry` is still truthy (the local variable holds the old tuple), so line 84 takes the `expires_at` branch, preserving the already-expired timestamp. The counter is immediately re-stored with the old expired TTL.

**UPGRADED TO WARNING WR-07:**

---

### [HIGH] WR-07: `MemoryCache.incr()` preserves expired TTL for re-created counters

**File:** `api/services/cache.py:84-86`
**Status:** CONFIRMED
**Description:** When a key exists but has expired, `incr()` deletes the key from storage (line 75) and starts a new counter at 1. However, the TTL calculation at line 84 uses the `entry` local variable (which is still truthy) to select `expires_at` from the OLD expired entry. The new counter is stored with the already-passed expiration time, making it immediately expired on the next read.
**Impact:** Rate-limit counters (`ai_rate:<user_id>`) in `MemoryCache` (used when Redis is unavailable) can silently vanish, allowing users to bypass rate limits entirely. The AI rate limiter at `ai_analysis.py:245` calls `cache.incr(key, ttl=3600)` which is affected.
**Fix:**
```python
async def incr(self, key: str, ttl: int | None = None) -> int:
    entry = self._storage.get(key)
    if entry is not None:
        value, expires_at = entry
        if expires_at > 0 and time.monotonic() >= expires_at:
            del self._storage[key]
            count = 1
            # Use fresh TTL, not the expired one
            new_expires = time.monotonic() + ttl if ttl else 0.0
        else:
            try:
                count = int(value) + 1
            except (TypeError, ValueError):
                count = 1
            new_expires = expires_at
    else:
        count = 1
        new_expires = time.monotonic() + ttl if ttl else 0.0
    self._storage[key] = (str(count), new_expires)
    self._storage.move_to_end(key)
    return count
```

---

### [FALSE POSITIVE] FP-02: `history_service.py` missing `begin_nested()` for concurrent inserts

**File:** `api/services/history_service.py:204-217`
**Original Finding:** Missing `begin_nested()` for concurrent inserts.
**Status:** FALSE POSITIVE
**Explanation:** The `sync_query_listing_states` function at lines 180-244 performs sequential inserts within a single session. There is no concurrent access pattern -- all `session.add()` calls are in a single-threaded `for` loop. The function is called from the scheduler which uses one session per tick. No concurrent inserts are happening that would require a savepoint.

---

### [FALSE POSITIVE] FP-03: `ai_analysis.py` TimeoutError fallback references potentially undefined variables

**File:** `api/routers/ai_analysis.py:832-932`
**Original Finding:** TimeoutError fallback references potentially undefined variables.
**Status:** FALSE POSITIVE
**Explanation:** The `TimeoutError` handler at line 832 references `similar`, `price_byn`, `is_negotiable_price`, `median`, `q1`, `q3`, `risk_context`, `photo_condition_label`, `photo_condition_notes`, `title`, `parameters`, and `payload`. All of these are defined in the `try` block BEFORE the `TimeoutError` can occur (the timeout wraps the AI call at line 694). The variables are populated during the market data loading phase (lines 412-585), which completes successfully before the AI call that could timeout. Python's `try/except` does not reset variables defined in the `try` block.

---

### [FALSE POSITIVE] FP-04: `buildEmptyState` sets innerHTML with potentially untrusted icon parameter

**File:** `frontend/js/render_core.js:279`
**Original Finding:** `buildEmptyState` sets innerHTML with potentially untrusted icon parameter.
**Status:** FALSE POSITIVE
**Explanation:** Line 271 uses `const iconHtml = _EMPTY_STATE_ICONS[icon] || "";` which performs a dictionary lookup against `_EMPTY_STATE_ICONS`, a hardcoded object with SVG string values (lines 242-253). The `icon` parameter is used only as a lookup key -- if not found, the fallback is empty string `""`. All callers in `render_trackers.js` and `render_cards.js` pass hardcoded string literals (`"trackers"`, `"events"`, `"watchlist"`, `"leads"`, `"search"`). No user-controlled data reaches the `icon` parameter.

---

### [FALSE POSITIVE] FP-05: `query_pipeline.py` stale futures across event loop restarts

**File:** `api/services/query_pipeline.py:155`
**Original Finding:** Stale futures across event loop restarts.
**Status:** FALSE POSITIVE (upgraded to LOW IN-02 above)

---

## New Findings

### [HIGH] NEW-01: X-Forwarded-For IP spoofing in consent audit trail

**File:** `api/routers/consent.py:133-139`
**Status:** CONFIRMED
**Description:** The consent grant endpoint captures the client IP for the audit trail. When the `X-Forwarded-For` header is present, it takes the LAST entry (line 139: `client_ip = parts[-1]`). The comment claims "Last entry is set by our reverse proxy", but this assumes the nginx proxy is the only source of the header. An attacker can inject a forged `X-Forwarded-For` header with a comma-separated list where the last entry is their chosen IP. Since nginx APPENDS to the existing header (creating `forged-ip, real-ip`), the code takes the last entry which is the real client IP -- so this is actually correct for the append pattern. However, if nginx is configured to REPLACE the header (common with `proxy_set_header X-Forwarded-For $remote_addr`), the code works correctly. If nginx passes through the client's header unchanged, the last entry is the real IP (nginx appends), but the first entries are attacker-controlled.
**Impact:** The code takes `parts[-1]`, which is the last entry. Since nginx appends the real IP, the last entry IS the real client IP. This is actually the correct pattern. **FALSE POSITIVE on re-analysis.** The code is safe because it trusts the rightmost entry, which is set by the trusted proxy.

**Re-assessment:** FALSE POSITIVE. The `parts[-1]` pattern is the standard way to get the real IP when behind a reverse proxy that appends.

---

### [MEDIUM] NEW-02: Missing `XSS_STYLE_EXPRESSION_RE` in export HTML sanitization

**File:** `api/routers/ai_analysis.py:1064-1065`
**Status:** CONFIRMED
**Description:** The export HTML sanitizer removes `@import` and `url()` from style content, but does not strip CSS `expression()` directives. In legacy browsers (IE), `expression()` allows JavaScript execution from CSS. While modern browsers don't support this, the sanitizer should be comprehensive for defense-in-depth. More importantly, the `_XSS_STYLE_URL_RE` replacement replaces `url(...)` with the literal string `none`, but does not handle `url()` with escaped characters or `url()` followed by `expression()`.
**Impact:** Very low in practice. The CSP header on the export route (`script-src 'none'`) blocks JavaScript execution, and modern browsers don't support CSS expressions. The sanitizer is defense-in-depth for when CSP is relaxed.
**Fix:** Add an expression regex:
```python
_XSS_STYLE_EXPRESSION_RE = _re.compile(r"expression\s*\([^)]*\)", flags=_re.IGNORECASE)
# Add to _sanitize_export_html:
sanitized = _XSS_STYLE_EXPRESSION_RE.sub("", sanitized)
```

---

### [HIGH] NEW-03: Export report HTML stored in-memory without size bounding

**File:** `api/routers/ai_analysis.py:1096-1103`
**Status:** CONFIRMED
**Description:** The `create_export_report` endpoint stores up to 300KB of HTML per export in the `_exports` dict (module-level dict). Pruning happens via `_prune_old_exports()` which removes expired entries. However, pruning only runs when a new export is created (line 1096) or periodically via `periodic_prune_shadow_stores` (every 5 minutes). Between prunes, a burst of export requests from different users could accumulate significant memory. With a 300KB limit per export and, say, 1000 concurrent users, this could reach ~300MB.
**Impact:** Memory pressure under load. The `_export_ttl` of 900 seconds (15 minutes) limits how long entries persist, but a burst could still cause issues.
**Fix:** Add a maximum capacity check:
```python
_MAX_EXPORTS = 500
@router.post("/export-report")
async def create_export_report(...):
    if len(_exports) >= _MAX_EXPORTS:
        raise HTTPException(status_code=503, detail="Too many pending exports, try again later")
    ...
```

---

### [MEDIUM] NEW-04: `MemoryCache.incr()` is not atomic under concurrent access

**File:** `api/services/cache.py:69-89`
**Status:** CONFIRMED
**Description:** The `MemoryCache.incr()` method performs a read-modify-write sequence without any locking. In an async context, two concurrent coroutines calling `incr()` on the same key could both read the same value, increment it, and write back, resulting in a lost increment. While Python's GIL prevents true parallel execution of bytecode, `await` points between the read and write could allow interleaving.
**Impact:** Rate-limit counters could undercount, allowing more requests than the configured limit. However, since the GIL prevents true parallelism and the read-modify-write is a single synchronous block (no `await` between get and set), this is practically safe. The only risk is if `int(value)` raises an exception (caught at line 80-81), which would allow a brief window.
**Fix:** No practical fix needed. The synchronous nature of the operation within a single event loop iteration makes this safe. Document the non-atomic nature for future maintainers.

---

## Statistics

- **Total findings:** 15
- **Critical:** 2 (CR-01, CR-02)
- **High/Warning:** 5 (WR-03, WR-04, WR-05, WR-06, WR-07)
- **Medium:** 2 (NEW-02, NEW-04)
- **Low/Info:** 2 (IN-01, IN-02)
- **Additional note:** 1 (NEW-03, memory bounding)
- **False positives from debug scan:** 4 (FP-02, FP-03, FP-04, NEW-01 re-analysis)
- **Correctly handled (not a bug):** 1 (WR-01 / FP re-assessment)

### Priority fix order:
1. CR-01: `return_exceptions=True` in `parallel_kufar.py` -- 1-line fix, high impact
2. WR-07: `MemoryCache.incr()` expired TTL bug -- allows rate-limit bypass
3. WR-05: Flip estimates using wrong stats reference -- user-facing inconsistency
4. CR-02: `TimestampMixin` timezone inconsistency -- requires migration
5. WR-04: Auth error details leaked -- security hardening
6. NEW-03: Export memory bounding -- operational safety

---

_Reviewed: 2026-04-28T12:00:00Z_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: deep_
