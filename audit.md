# Rafuk/Kufar Analytics deep-dive audit

Date: 2026-05-13  
Scope: backend/security/ops/performance/frontend/design/accessibility review.

## Verification baseline

Last read-only audit checks:

- `uv run ruff check .` — passed
- `uv run pytest --tb=short -q` — `782 passed, 1 skipped, 1 warning`
- `uv run alembic -c migrations/alembic.ini current` — `20260513_0015 (head)`
- `node --check frontend/js/app_bundle.js` — passed
- `npx --yes impeccable --json --fast frontend/index.html frontend/js` — `[]`

## Executive summary

No P0/blocking issues were found. The remaining work is mostly hardening,
accessibility, privacy cleanup, and maintainability:

- Backend auth, CSRF/CORS, Telegram `initData`, IDOR scoping, exports, AI consent,
  and image SSRF defenses are generally strong.
- The most important remaining frontend security item is URL allowlisting:
  `safeUrl()` currently accepts arbitrary `http(s)` URLs and protocol-relative
  `//host` paths.
- Accessibility is good overall, but the Listing Assistant file upload is not
  keyboard-accessible and the edit-tracker modal contains a nested-label pattern.
- Local AI Listing Assistant history is disclosed and bounded, but account deletion
  should also clear local `rafuk:*` data where the browser permits it.
- The image proxy backend is well-hardened, but the frontend never opts into it.
- The tracker scheduler still performs external Kufar I/O while a DB savepoint is
  open.
- Large monolith files remain and should be split only where the split directly
  supports the active fixes.

## Positive findings to preserve

- Production-like deployments fail closed if the rate limiter has degraded to
  in-memory fallback.
- Telegram auth validates signed `initData`, supports a separate internal service
  token path, applies blacklist checks, and tracks replay/IP mismatch signals.
- CORS/CSRF rejects `null` origin and requires `X-Requested-With` on state-changing
  browser requests.
- IDOR coverage exists for leads, trackers, expenses, watchlist, and tracker events.
- CSV export prefixes formula-like cells and caps export size.
- Account export/deletion paths are bounded and clear per-user AI/cache state.
- AI image fetch validates HTTPS Kufar hosts, redirects, content type, and byte size.
- Frontend CSP is strict, Telegram SDK is vendored with SRI, and render paths mostly
  use DOM APIs/text nodes rather than raw HTML.
- Focus traps, inert sibling handling, roving tablists, and focus restore are already
  implemented for key modals and tabs.

## Atomic waves

### Wave 1 — AUDIT-DOC: persist audit and implementation plan

Status: completed in wave76

Deliverables:

- Add this `audit.md`.
- Commit only the audit document; do not stage unrelated deleted local notes.

Verification:

- `git diff -- audit.md`

### Wave 2 — FE-URL-HARDEN: strict URL validators

Status: completed in wave77

Problem:

- `safeUrl()` accepts any `http://`, `https://`, and any string starting with `/`,
  including protocol-relative external URLs.
- Several “Kufar” links/images trust backend or AI-provided URLs.

Deliverables:

- Replace prefix-based URL validation with `URL` parsing.
- Add separate helpers for:
  - `safeUrl()` / same-origin relative URLs
  - `safeKufarUrl()` for Kufar listing links
  - `safeImageUrl()` for allowed image hosts/paths
- Update Kufar-branded links and image call sites to use the stricter helper.
- Add frontend/static tests covering `//evil.example`, `javascript:`, non-Kufar
  external URLs, valid same-origin paths, valid Kufar links, and valid Kufar images.
- Rebuild `frontend/js/app_bundle.js` and bump static cache tags if frontend assets
  changed.

Verification:

- Relevant frontend tests or static test script
- `node --check frontend/js/app_bundle.js`
- `uv run pytest --tb=short -q`

### Wave 3 — FE-A11Y-FORMS: keyboard upload and valid labels

Status: completed in wave78

Problem:

- Listing Assistant photo upload uses a visible label wrapping a hidden file input,
  which is not keyboard-focusable as the upload trigger.
- Edit-tracker “Строгий режим” has a nested `<label>` pattern.

Deliverables:

- Make the photo upload trigger keyboard-accessible using a real button or a
  focusable input/label pattern.
- Remove nested labels in the edit-tracker modal while preserving the same visual
  layout and toggle behavior.
- Rebuild frontend bundle and bump static tags.

Verification:

- `node --check frontend/js/app_bundle.js`
- `uv run pytest --tb=short -q`

### Wave 4 — FE-CONSENT-PRIVACY: consent status and local data cleanup

Status: completed

Problem:

- Frontend `checkAiConsent()` currently treats any status-request error as success.
- Account deletion reloads after server deletion but does not attempt to clear
  local `rafuk:*`/recent-search data first.

Deliverables:

- Make consent precheck fail closed outside local/debug contexts, with a clear toast.
- Clear local Rafuk keys on successful account deletion before reload.
- Keep backend consent checks as the security boundary.
- Rebuild frontend bundle and bump static tags.

Verification:

- `node --check frontend/js/app_bundle.js`
- `uv run pytest --tb=short -q`

### Wave 5 — IMG-PROXY-POLICY: align image proxy behavior

Status: completed

Problem:

- Backend image proxy is authenticated/rate-limited and hardened, but frontend calls
  `optimizedImage(..., { width })` without `useProxy`, so proxy is effectively idle.

Deliverables:

- Decide in code which surfaces should use proxy.
- Prefer using proxy for detail/gallery or large images where privacy/bandwidth
  benefits outweigh transcode cost; keep direct CDN for small list thumbnails if
  first-paint speed is the chosen policy.
- Update code comments/tests to make the policy explicit.
- Rebuild frontend bundle and bump static tags if frontend assets changed.

Verification:

- `node --check frontend/js/app_bundle.js`
- Image proxy tests or existing pytest coverage
- `uv run pytest --tb=short -q`

### Wave 6 — SCHED-TX-SPLIT: remove external I/O from DB savepoint

Status: pending

Problem:

- Tracker cycle performs `client.search()` while inside `session.begin_nested()`,
  extending DB transaction/savepoint lifetime across external Kufar network I/O.

Deliverables:

- Fetch Kufar payload before opening the per-query savepoint.
- Keep only DB mutation/detection inside the savepoint.
- Preserve per-query error isolation and notification dispatch-after-commit behavior.

Verification:

- Scheduler-focused tests
- `uv run pytest --tb=short -q`

### Wave 7 — MAINT-SPLIT: targeted monolith reduction

Status: pending

Problem:

- Large files remain above 500 LOC, especially scheduler, AI services, frontend CSS,
  and frontend JS helpers.

Deliverables:

- Do not bulk-reformat or split for its own sake.
- Extract only seams touched by prior waves into focused helper modules if doing so
  reduces review risk.
- Preserve backward-compatible exports where needed.
- If no safe scoped split remains after waves 2-6, mark this as deferred with the
  exact candidate files and rationale in this file rather than performing a risky
  cosmetic split.

Verification:

- Relevant tests for any extracted seam
- `uv run ruff check .`
- `uv run pytest --tb=short -q`

## Final acceptance criteria

- Every wave above is either committed as closed or explicitly marked deferred with
  a concrete reason.
- No unrelated local deletions or secrets are staged.
- Full final verification passes:
  - `uv run ruff check .`
  - `uv run pytest --tb=short -q`
  - `uv run alembic -c migrations/alembic.ini current`
  - `node --check frontend/js/app_bundle.js`
