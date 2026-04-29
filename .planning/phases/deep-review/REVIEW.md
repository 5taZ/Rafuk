---
phase: deep-review
reviewed: 2026-04-29T19:30:00Z
depth: deep
files_reviewed: 19
files_reviewed_list:
  - frontend/css/parts/ai.css
  - frontend/css/parts/brand.css
  - frontend/css/parts/layout.css
  - frontend/css/parts/modals.css
  - frontend/css/parts/pipeline.css
  - frontend/css/parts/states.css
  - frontend/css/parts/tokens.css
  - frontend/css/style.css
  - frontend/index.html
  - frontend/js/api_core.js
  - frontend/js/api_leads.js
  - frontend/js/api_listing_assistant.js
  - frontend/js/api_watchlist.js
  - frontend/js/app_actions.js
  - frontend/js/app_core.js
  - frontend/js/app_core_dom.js
  - frontend/js/render_card_builders.js
  - frontend/js/render_modals.js
  - frontend/js/virtual_list.js
findings:
  critical: 1
  warning: 6
  info: 5
  total: 12
status: issues_found
---

# Phase deep-review: Code Review Report (Frontend Layer)

**Reviewed:** 2026-04-29T19:30:00Z
**Depth:** deep
**Files Reviewed:** 19 (8 CSS, 1 HTML, 10 JS)
**Status:** issues_found

## Summary

Reviewed the complete frontend layer of the Kufar marketplace analytics Telegram Mini App: 8 CSS part files, the HTML entry point, and 10 JavaScript modules. The codebase demonstrates strong XSS hygiene overall -- user-controlled content consistently uses `textContent` and the `domEl()` helper rather than `innerHTML`, and `safeUrl()` validates all URL attributes. The module pattern with factory functions (`createXxx`) provides clean encapsulation and avoids global scope pollution.

One critical issue was found: `_showConfirmDialog` in `app_actions.js` uses `innerHTML` to build its dialog markup. While the current call sites only pass hardcoded strings (so there is no exploitable XSS vector today), the pattern is fragile -- any future caller passing user-controlled input would introduce an XSS vulnerability. Six warnings cover a global `keydown` listener that is never cleaned up (memory leak), `innerHTML` usage with hardcoded SVG strings that should use DOM construction for consistency, an `escapeHtml()` call on `textContent` assignment (redundant and semantically misleading), a raw `fetch` call that bypasses the standard timeout wrapper, O(n) insertion scan in the virtual list, and `console.warn` remaining in production code. Five info items cover duplicate CSS declarations, unused CSS variables, and minor code quality observations.

The previous review's 8 critical findings (CR-01 through CR-08 about `state.leads.find()` vs `state.leads.items.find()`) are all **fixed** in the current code -- every file correctly uses the `.items` accessor path.

## Critical Issues

### CR-01: innerHTML in _showConfirmDialog accepts string interpolation (XSS risk pattern)

**File:** `frontend/js/app_actions.js:564-570`
**Issue:** `_showConfirmDialog(title, message)` builds its dialog markup via `sheet.innerHTML` with template literals that interpolate `escapeHtml(title)` and `escapeHtml(message)`. The current escape is correct, but the pattern of using `innerHTML` with string interpolation creates a fragile API surface. If a future developer calls `_showConfirmDialog` with unescaped user input (or if `escapeHtml` is accidentally removed), it becomes a direct XSS vector. The rest of the codebase consistently avoids `innerHTML` with user data, making this an outlier. The dialog is appended to `document.body` and renders within the Telegram Mini App context, where XSS can exfiltrate the `initData` token.

All current call sites (`deleteAllLeads`, `deleteAllWatchlist`, `clearEvents`) pass hardcoded Russian strings, so there is no exploitable path today. The finding is rated critical because the *pattern* is dangerous, even though the current usage is safe.

**Fix:**
```javascript
function _showConfirmDialog(title, message) {
    return new Promise((resolve) => {
        const overlay = document.createElement("div");
        overlay.className = "detail-modal";
        overlay.style.cssText = "display:flex;align-items:center;justify-content:center;z-index:1000;";
        const sheet = document.createElement("div");
        sheet.className = "detail-sheet";
        sheet.style.cssText = "max-width:340px;width:90%;padding:20px;text-align:center;";

        const h3 = document.createElement("h3");
        h3.style.cssText = "margin:0 0 8px;font-size:17px;";
        h3.textContent = title;

        const p = document.createElement("p");
        p.style.cssText = "margin:0 0 20px;color:var(--text-secondary);font-size:14px;";
        p.textContent = message;

        const btnRow = document.createElement("div");
        btnRow.style.cssText = "display:flex;gap:10px;justify-content:center;";

        const cancelBtn = document.createElement("button");
        cancelBtn.textContent = "\u041E\u0442\u043C\u0435\u043D\u0430";
        cancelBtn.style.cssText = "flex:1;padding:10px;border-radius:10px;border:1px solid var(--border-color);background:var(--surface-color);color:var(--text-color);font-size:14px;";

        const confirmBtn = document.createElement("button");
        confirmBtn.textContent = "\u0423\u0434\u0430\u043B\u0438\u0442\u044C";
        confirmBtn.style.cssText = "flex:1;padding:10px;border-radius:10px;border:none;background:var(--danger-color,#e53935);color:#fff;font-size:14px;font-weight:600;";

        btnRow.append(cancelBtn, confirmBtn);
        sheet.append(h3, p, btnRow);
        overlay.appendChild(sheet);
        document.body.appendChild(overlay);
        document.body.classList.add("modal-open");

        function close(result) {
            document.body.classList.remove("modal-open");
            overlay.remove();
            resolve(result);
        }

        cancelBtn.addEventListener("click", () => close(false));
        confirmBtn.addEventListener("click", () => close(true));
        overlay.addEventListener("click", (e) => {
            if (e.target === overlay) close(false);
        });
    });
}
```

## Warnings

### WR-01: Global keydown listener in listing assistant never removed (memory leak)

**File:** `frontend/js/api_listing_assistant.js:828`
**Issue:** `document.addEventListener("keydown", (e) => { ... })` is registered at module creation time but the `destroy()` method at line 840-842 only removes the form's `submit` listener. The keydown listener for the Escape key remains attached to `document` for the lifetime of the page. If the listing assistant module were ever recreated (e.g., during a hot reload), listeners would accumulate. The anonymous function also prevents `removeEventListener` from working since there is no stored reference.

**Fix:**
```javascript
// Store the listener reference at the top of createListingAssistant:
const _escHandler = (e) => {
    if (e.key === "Escape" && !modal.hidden) {
        closeModal();
    }
};
document.addEventListener("keydown", _escHandler);

// In destroy():
return {
    destroy() {
        form.removeEventListener("submit", handleSubmit);
        document.removeEventListener("keydown", _escHandlers);
    },
};
```

### WR-02: innerHTML used for hardcoded SVG icons (pattern inconsistency)

**File:** `frontend/js/render_trackers.js` (multiple locations)
**Issue:** Tracker card rendering uses `innerHTML` to inject SVG icon strings (e.g., play, pause, trash, edit icons). While the SVG content is entirely hardcoded and not a security risk, it violates the project's own convention of using `domEl()` for DOM construction. If a developer later modifies the SVG strings to include dynamic attributes (e.g., a data attribute from listing data), it could introduce an injection point. The same pattern appears in `render_card_builders.js` for sparkline SVGs.

**Fix:** Use `domEl("svg", ...)` or `document.createElementNS("http://www.w3.org/2000/svg", "svg")` for SVG construction. At minimum, add a comment documenting that the innerHTML content is intentionally hardcoded:
```javascript
// SVG icons are hardcoded strings -- safe for innerHTML. Do NOT
// interpolate dynamic values into these templates.
trackerEl.querySelector(".tracker-icon").innerHTML = TRACKER_PLAY_SVG;
```

### WR-03: escapeHtml() called on textContent assignment (redundant and misleading)

**File:** `frontend/js/render_modals.js:193`
**Issue:** `msg.textContent = escapeHtml(rf.message || rf.type || "")` applies HTML entity encoding (`&amp;`, `&lt;`, etc.) to text that is then assigned via `textContent`. Since `textContent` does not parse HTML, the escaped entities will be rendered literally (e.g., the message `"Price < 100"` would display as `"Price &lt; 100"` on screen). This is semantically incorrect and suggests the developer intended to use `innerHTML` but switched to `textContent` without removing the escape call.

**Fix:**
```javascript
msg.textContent = rf.message || rf.type || "";
```

### WR-04: exportLeads uses raw fetch without standard timeout wrapper

**File:** `frontend/js/app_actions.js:410`
**Issue:** `exportLeads()` calls `fetch()` directly instead of using the project's `requestJson()` / `getJson()` wrapper from `api_core.js`. The standard wrappers include an `AbortController`-based timeout (default 30s). The raw `fetch` has no timeout, so if the API hangs (e.g., generating a large XLSX file), the download request will hang indefinitely with no user feedback. The blob download endpoint can legitimately take longer than normal API calls.

**Fix:**
```javascript
async function exportLeads(format = "csv") {
    const fmt = format === "xlsx" ? "xlsx" : "csv";
    try {
        const initData = window.Telegram?.WebApp?.initData;
        const headers = initData ? { "X-Telegram-Init-Data": initData } : {};
        const controller = new AbortController();
        const timeout = setTimeout(() => controller.abort(), 120_000); // 2 min for large exports
        const response = await fetch(`/api/v1/leads/export?format=${fmt}`, {
            headers,
            signal: controller.signal,
        });
        clearTimeout(timeout);
        // ... rest of handler
    }
}
```

### WR-05: Virtual list insertion scan is O(n) over all rendered items

**File:** `frontend/js/virtual_list.js:142-152`
**Issue:** When new items arrive (e.g., via `pushItems`), `_mergeRenderedItems` scans the entire `renderedMap` to find the insertion point. This is O(n) where n is the number of currently rendered items. For the tracker events use case, `renderedMap` can contain dozens to hundreds of entries. The scan iterates every entry to find the one with the smallest index greater than `minNewIdx`. This could be optimized to O(log n) using the sorted key property of the map, but since the rendered count is bounded by viewport size (typically 20-50 items), the practical impact is negligible. Flagged as a warning because the algorithm could become a problem if virtual list is reused for larger datasets.

**Fix:**
```javascript
// Since renderedMap keys (indices) are sorted, use binary search:
const keys = Array.from(renderedMap.keys());
let lo = 0, hi = keys.length;
while (lo < hi) {
    const mid = (lo + hi) >>> 1;
    if (keys[mid] > minNewIdx) hi = mid;
    else lo = mid + 1;
}
const insertBefore = lo < keys.length ? renderedMap.get(keys[lo]) : bottomSpacer;
```

### WR-06: console.warn left in production code path

**File:** `frontend/js/app_core.js:394`
**Issue:** `measureRender()` calls `console.warn(...)` when an operation exceeds the threshold (default 100ms). This is a development utility that will fire in production whenever any render takes longer than 100ms, which is plausible on low-end devices. While not a security or correctness issue, it pollutes the browser console and could leak performance information. The function is exported and used across the app.

**Fix:**
```javascript
function measureRender(name, thresholdMs = 100) {
    if (typeof import.meta !== "undefined" && import.meta.env?.PROD) return () => 0;
    const start = performance.now();
    return function () {
        const elapsed = performance.now() - start;
        if (elapsed > thresholdMs) {
            console.warn(`[perf] ${name} took ${elapsed.toFixed(1)}ms`);
        }
        return elapsed;
    };
}
// Or simply remove the function if it is not actively used for profiling.
```

## Info

### IN-01: Duplicate `color: var(--text)` declaration in `.logo-name`

**File:** `frontend/css/parts/tokens.css:425` and `tokens.css:427`
**Issue:** The `.logo-name` rule declares `color: var(--text)` twice -- once at line 425 and again at line 427 (after `letter-spacing`). The second declaration is redundant.
**Fix:** Remove the duplicate `color: var(--text)` at line 427.

### IN-02: CSS custom properties defined but never referenced

**File:** `frontend/css/parts/tokens.css` (multiple)
**Issue:** Several CSS custom properties defined in `:root` appear to have no references in the rest of the codebase (e.g., `--gradient-warm`, some animation tokens). While this is not harmful, it adds dead weight to the token system.
**Fix:** Audit `:root` variables against actual usage. Remove or mark deprecated any that are not referenced.

### IN-03: `domEl()` helper is not defined in any of the reviewed frontend JS files

**File:** `frontend/js/app_core_dom.js:209`
**Issue:** `domEl()` is called in `populateRegionSelectOptions()` but is not defined in `app_core_dom.js` or any other reviewed file. It is presumably defined in a shared utility or globally. This makes the dependency implicit rather than explicit, which could cause issues if the loading order changes.
**Fix:** Verify `domEl()` is defined before `app_core_dom.js` in the script loading order (confirmed: `index.html` loads scripts in the correct order). Consider adding a comment or making the dependency explicit.

### IN-04: `_vlIndex` expando property on DOM elements is fragile

**File:** `frontend/js/virtual_list.js:158`
**Issue:** The virtual list stores the item index directly on DOM elements via `el._vlIndex = idx`. While this works, expando properties on DOM elements are fragile -- they can be lost if the element is serialized/cloned, and they are a code smell that indicates the data model and view are not cleanly separated. A `WeakMap<Element, number>` would be more robust.
**Fix:** Consider using `WeakMap`:
```javascript
const indexMap = new WeakMap();
// Instead of el._vlIndex = idx:
indexMap.set(el, idx);
// Instead of el._vlIndex:
indexMap.get(el);
```

### IN-05: frontend/css/style.css imports partials via @import but also includes inline rules

**File:** `frontend/css/style.css`
**Issue:** The main stylesheet uses `@import` to load the `parts/` CSS files, but also contains inline rules. The `@import` statements are synchronous render-blocking requests. In production, these should be concatenated into a single file or loaded via `<link>` tags with `media` attributes for non-critical CSS. This is a build/optimization concern, not a bug.
**Fix:** For the current no-build-step architecture, this is acceptable. If a build step is added later, replace `@import` with concatenation.

---

_Reviewed: 2026-04-29T19:30:00Z_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: deep_
