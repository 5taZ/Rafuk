---
phase: deep-review
reviewed: 2026-04-28T12:00:00Z
depth: deep
files_reviewed: 35
files_reviewed_list:
  - api/config.py
  - api/main.py
  - api/routers/ai_analysis.py
  - api/routers/ai_listing_assistant.py
  - api/routers/ai_tools.py
  - api/routers/consent.py
  - api/routers/image_proxy.py
  - api/routers/workflow.py
  - api/services/ai_service.py
  - api/services/ai_category_data.py
  - api/services/cache.py
  - frontend/js/app_core.js
  - frontend/js/app.js
  - frontend/js/app_actions.js
  - frontend/js/app_renderers.js
  - frontend/js/api_core.js
  - frontend/js/api_events.js
  - frontend/js/api_leads.js
  - frontend/js/api_listings.js
  - frontend/js/api_trackers.js
  - frontend/js/api_watchlist.js
  - frontend/js/api_ai.js
  - frontend/js/api_listing_assistant.js
  - frontend/js/render_core.js
  - frontend/js/render_cards.js
  - frontend/js/render_card_builders.js
  - frontend/js/render_charts.js
  - frontend/js/render_modals.js
  - frontend/js/render_trackers.js
  - frontend/js/render_views.js
  - frontend/js/dom_helpers.js
  - frontend/css/style.css
  - tests/test_ai_analysis.py
  - tests/test_app_js_syntax.py
  - tests/test_config.py
findings:
  critical: 8
  warning: 3
  info: 2
  total: 13
status: issues_found
---

# Deep Code Review Report

**Reviewed:** 2026-04-28
**Depth:** deep
**Files Reviewed:** 35
**Status:** issues_found

## Summary

Full deep review of 35 files across backend (Python/FastAPI) and frontend (vanilla JS) after a state-refactoring commit that restructured flat arrays into nested sub-objects. The backend is solid: auth is correctly applied on all sensitive endpoints, ORM cascade deletes are properly configured, XSS sanitization is thorough, and input validation is comprehensive. Tests are well-structured with good coverage of edge cases (outlier prices, prompt injection, stale response guards).

The critical findings are all in the **frontend JavaScript** and all share the same root cause: after the state refactoring that changed `state.leads` from a flat array to `state.leads = { items: [], ... }`, multiple files still call array methods (`.find()`, `.some()`, `.filter()`, `.length`) directly on the object instead of on `.items`. These will cause `TypeError: state.leads.find is not a function` crashes at runtime.

## Critical Issues

### CR-01: `state.leads.find()` instead of `state.leads.items.find()` in revertLeadStage

**File:** `frontend/js/api_leads.js:247`
**Issue:** After the state refactoring, `state.leads` is an object `{ items: [], filter: ..., _requestId: 0 }`, not an array. Calling `.find()` on it throws `TypeError: state.leads.find is not a function`. This function runs every time a user clicks the "revert" button on a sold lead card.
**Fix:**
```javascript
// Line 247: change
const leadInState = state.leads.find((l) => l.id === leadId);
// to
const leadInState = state.leads.items.find((l) => l.id === leadId);
```

### CR-02: `state.leads.find()` instead of `state.leads.items.find()` in markLeadAsSold

**File:** `frontend/js/api_leads.js:320`
**Issue:** Same pattern as CR-01. `markLeadAsSold` calls `.find()` on the leads object instead of the `.items` array. This runs when the user confirms a lead as sold via the "Done" button on a sold lead card.
**Fix:**
```javascript
// Line 320: change
const leadInState = state.leads.find((l) => l.id === lead.id);
// to
const leadInState = state.leads.items.find((l) => l.id === lead.id);
```

### CR-03: `state.leads.some()` instead of `state.leads.items.some()` in addLeadFromListing

**File:** `frontend/js/app_actions.js:256`
**Issue:** `addLeadFromListing` calls `.some()` on `state.leads` (an object), not `state.leads.items` (the array). This function runs every time a user clicks "Add to purchases" from a listing card, long-press menu, or tracker event. The crash prevents adding any lead.
**Fix:**
```javascript
// Line 256: change
const alreadyInLeads = state.leads.some(
    (l) => l.ad_id === item.ad_id && ACTIVE_LEAD_STATUSES.has(l.status),
);
// to
const alreadyInLeads = state.leads.items.some(
    (l) => l.ad_id === item.ad_id && ACTIVE_LEAD_STATUSES.has(l.status),
);
```

### CR-04: `state.watchlist.find()` and `.filter()` instead of `state.watchlist.items.*`

**File:** `frontend/js/app_actions.js:268,279`
**Issue:** Two calls in `addLeadFromListing` use the old flat-array pattern. Line 268 calls `.find()` on `state.watchlist`, and line 279 replaces the entire `state.watchlist` object with a plain array via `.filter()`. The `.find()` call crashes. The `.filter()` assignment on line 279 is worse: it replaces the entire refactored `state.watchlist = { items: [], filter: ..., _requestId: 0 }` with a plain array, destroying `_requestId`, `filter`, and `itemsFilter` -- which will cascade-crash every subsequent watchlist operation.
**Fix:**
```javascript
// Line 268: change
const watchingItem = state.watchlist.find((w) => w.ad_id === item.ad_id);
// to
const watchingItem = state.watchlist.items.find((w) => w.ad_id === item.ad_id);

// Line 279: change
state.watchlist = state.watchlist.filter((w) => w.id !== watchingItem.id);
// to
state.watchlist.items = state.watchlist.items.filter((w) => w.id !== watchingItem.id);
```

### CR-05: `deleteHistoryDeal` replaces `state.leads` with a plain array

**File:** `frontend/js/app_actions.js:315`
**Issue:** `state.leads = state.leads.filter(...)` replaces the entire refactored leads object `{ items: [], filter: ..., _requestId: 0, itemsFilter: ... }` with a bare array. This destroys `_requestId` (stale-response guard), `filter`, and `itemsFilter`. Every subsequent leads operation will crash or behave incorrectly. Even if `.filter` didn't throw (it will -- objects don't have `.filter`), the assignment would break the state structure.
**Fix:**
```javascript
// Line 315: change
state.leads = state.leads.filter((l) => l.id !== leadId);
// to
state.leads.items = state.leads.items.filter((l) => l.id !== leadId);
```

### CR-06: `state.trackers.find()` instead of `state.trackers.items.find()` in openEditTracker

**File:** `frontend/js/api_trackers.js:204`
**Issue:** `openEditTracker` calls `.find()` on the trackers object instead of `trackers.items`. This runs when the user clicks "Edit" on a tracker card.
**Fix:**
```javascript
// Line 204: change
const tracker = state.trackers.find((t) => t.id === trackerId);
// to
const tracker = state.trackers.items.find((t) => t.id === trackerId);
```

### CR-07: `state.trackers.length` instead of `state.trackers.items.length` in renderTrackers

**File:** `frontend/js/render_trackers.js:93`
**Issue:** The empty-state check uses `state.trackers.length` on the refactored object. Since objects don't have a `.length` property, this evaluates to `undefined`, and `!undefined` is `true`. The result: **the tracker list always shows the empty state even when trackers exist.** Compare with `render_views.js:34` which correctly uses `state.trackers.items.length`.
**Fix:**
```javascript
// Line 93: change
if (!state.trackers.length) {
// to
if (!state.trackers.items.length) {
```

### CR-08: `state.trackers.find()` instead of `state.trackers.items.find()` in renderTrackerEvents

**File:** `frontend/js/render_trackers.js:335`
**Issue:** When a tracker-scoped filter is active and the filtered list is empty, `renderTrackerEvents` calls `.find()` on `state.trackers` to look up the tracker's query for the empty-state message. This crashes when the user filters events by tracker and there are no events.
**Fix:**
```javascript
// Line 335: change
const tracker = state.trackers.find((t) => t.id === state.trackers.eventFilterTrackerId);
// to
const tracker = state.trackers.items.find((t) => t.id === state.trackers.eventFilterTrackerId);
```

## Warnings

### WR-01: Existing tests do not cover state accessor paths

**File:** `tests/test_app_js_syntax.py`
**Issue:** The static tests verify `_requestId` guards and inflight mutation patterns but do not assert that array method calls target `.items` rather than the parent object. A simple regex check like `assert /state\.leads\.find\\(/ not in text` (for the bad pattern) would have caught all 8 critical findings before merge. The tests at lines 76-83 verify `state.leads._requestId` exists but never check that `.find()` or `.some()` calls use the `.items` accessor.
**Fix:** Add a test that greps for bare `state.leads.find(`, `state.leads.some(`, `state.leads.filter(`, `state.watchlist.find(`, `state.watchlist.filter(`, `state.trackers.find(`, and `state.trackers.length` -- all of which are now invalid after the refactoring. Example:
```python
def test_state_array_methods_target_items_not_parent():
    for module in JS_MODULES:
        text = module.read_text(encoding="utf-8")
        for bad in (
            "state.leads.find(",
            "state.leads.some(",
            "state.leads.filter(",
            "state.watchlist.find(",
            "state.watchlist.filter(",
            "state.trackers.find(",
            "state.trackers.length",
        ):
            assert bad not in text, f"{module}: bare `{bad}` must use `.items` accessor"
```

### WR-02: SVG sanitization regex may miss some SVG event handlers

**File:** `frontend/js/dom_helpers.js`
**Issue:** The SVG sanitization checks for `<script` and `on\w+=` patterns. The `\b` word boundary before `on` and the `\s*=` after it are good, but SVG supports namespaced event attributes like `xlink:href` and more obscure handlers. The check rejects `<script` and `on\w+=` which covers the common attack surface. The SVG strings used in the long-press menu and tracker action buttons are all hardcoded string literals in the source code with no user-controlled input, so the practical risk is negligible. However, if the sanitization function is ever reused for user-provided SVG, the pattern should be hardened.
**Fix:** This is low risk given current usage. If the function is ever used for user input, consider using a DOM parser approach: parse the SVG string into a document, enumerate all attributes, and strip any that aren't on an explicit allowlist.

### WR-03: Cache key collision risk in negotiate endpoint

**File:** `frontend/js/render_trackers.js` (tracker action icon SVG at line 167)
**Issue:** The `innerHTML` assignment at line 167 in `render_trackers.js` sets SVG content from the hardcoded string literals defined on lines 152-158. While these are safe because they are static source-code constants, this is the same pattern as `buildIconButton` in `render_card_builders.js` (line 175-201) which passes SVG through `attachLongPress`. If any future change allows user-controlled strings into these SVG icon slots, it would be an XSS vector. Currently safe but worth noting the pattern.
**Fix:** No action needed for current code. Document that `iconHtml` parameters must always receive hardcoded SVG string literals.

## Info

### IN-01: Debug tokens and placeholder values in test fixtures

**File:** `tests/test_ai_analysis.py:34`
**Issue:** `fake_telegram_user()` returns `user_id=123456` with `raw={}`. The tests in `test_config.py:13` use `BOT_TOKEN: "7123456789:AAFtesttoken"`. These are clearly test fixtures, not real credentials, but the token format matches a real Telegram bot token pattern. This is standard practice for test fixtures and poses no risk since they are never used against the real Telegram API.

### IN-02: Duplicated CSS between style.css and parts/ directory

**File:** `frontend/css/style.css` vs `frontend/css/parts/brand.css`, `frontend/css/parts/layout.css`
**Issue:** The `color-mix()` usage in `style.css` (lines 1093-9208) appears to duplicate many of the same rules found in `frontend/css/parts/brand.css` and `frontend/css/parts/layout.css`. The test file uses `rglob("*.css")` to concatenate all CSS, so duplication doesn't cause test failures, but it may cause specificity conflicts at runtime if both the monolithic `style.css` and the partials are loaded. Based on the test comment at line 18 ("style.css is now a thin @import loader"), this duplication may be intentional during a migration from monolithic to partial CSS. Not a bug, but worth tracking for cleanup.

---

_Reviewed: 2026-04-28_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: deep_
