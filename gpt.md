# GPT deep-dive remediation log

Created: 2026-05-13
Scope: issues found in the GPT audit of the Kufar/Rafuk Telegram Mini App.

## Verification baseline

- `uv run ruff check .` passed before remediation.
- `uv run pytest --tb=short -q` passed before remediation: 700 passed, 1 skipped.
- Existing dirty state before remediation: `D mybad.md`.

## Problems to fix

### P1 — Security / privacy / compliance

1. **AI server consent gate does not require `pd_processing`.**
   - Frontend asks for `ai_analysis`, `cross_border`, and `pd_processing`.
   - Backend `_check_ai_consent()` currently requires only `ai_analysis` and `cross_border`.
   - Fix: require all three active current-version consents server-side and cover with tests.

2. **Client IP and rate-limit identity trust proxy headers too broadly.**
   - `CF-Connecting-IP` is accepted whenever present.
   - Nginx `$rate_limit_key` also trusts incoming `CF-Connecting-IP` directly.
   - SlowAPI public fallback uses `get_remote_address` instead of the shared trusted helper.
   - Fix: trust proxy headers only from Cloudflare/private proxy peers, normalize SlowAPI keying, and harden nginx direct-access behavior.

3. **Listing Assistant AI cache is deterministic and shared across users.**
   - Key is based on title/category/condition/price/notes/photo hashes but not user id.
   - Account deletion does not clear `ai_listing:*`.
   - Fix: make Listing Assistant cache user-scoped or avoid caching sensitive inputs; cover erasure behavior.

### P2 — Erasure / audit / operations

4. **AI audit log writes are best-effort without an observable failure counter.**
   - Requests continue when `_log_ai_audit()` fails.
   - Fix: keep UX best-effort but expose an in-process failure counter for tests/health/metrics follow-up.

5. **Account deletion can leave DLQ rows where only `telegram_user_id` matches.**
   - Export includes DLQ by `user_id OR telegram_user_id`.
   - Delete relies on cascade from nullable `user_id` only.
   - Fix: explicitly delete `TelegramNotificationDLQ.telegram_user_id == current user` during account deletion.

6. **Frontend sends `X-Frame-Options: SAMEORIGIN` while CSP allows Telegram frame ancestors.**
   - This can conflict with Telegram Web embedding in older WebViews.
   - Fix: remove XFO from frontend shell responses and keep frame protection via CSP `frame-ancestors`; keep API DENY/SAMEORIGIN only if appropriate.

### P2 — Frontend accessibility / responsive / performance

7. **Secondary tablists lack full keyboard semantics.**
   - Main view tabs have roving tabindex and Arrow/Home/End.
   - Items tabs and Listing Assistant tabs only handle clicks and `aria-selected`.
   - Fix: shared tablist keyboard helper, roving tabindex, `aria-controls`, and `aria-labelledby`.

8. **Focus trap snapshots focusable nodes only once.**
   - Dynamic Listing Assistant result/history content can change focusable elements after the modal opens.
   - Fix: query focusables live on each Tab keydown and restore focus on cleanup.

9. **Header touch targets are 36x36, below 44x44 guidance.**
   - Theme and privacy buttons are too small for mobile Telegram WebView.
   - Fix: keep visual size but provide 44x44 hit area/min-size.

10. **Frontend bundle and CSS remain large.**
    - `app_bundle.js` ~445 KB / 10.7k lines.
    - `style.css` ~222 KB / 9.9k lines.
    - Fix in this remediation cycle: avoid adding weight, keep lazy-loading intact, and document the next structural split; do not introduce a risky bundler migration inside security/a11y waves.

11. **`scrollSectionIntoView()` always smooth-scrolls.**
    - Ignores `prefers-reduced-motion` despite global CSS handling.
    - Fix: use instant/auto behavior when reduced motion is enabled.

### P3 — Docs / CI hygiene

12. **README test count is stale and internally inconsistent.**
    - Status says 661 tests; Tests section says 494; current suite is 700 passed, 1 skipped.
    - Fix: update README counts.

13. **CI push trigger excludes the current `bad-app` working branch.**
    - CI runs on push to `main`/`develop` and PR to `main` only.
    - Fix: add `bad-app` push trigger while the branch remains active.

## Atomic wave plan

### Wave 49 — AI consent and Listing Assistant cache privacy

- Require `pd_processing` in `_check_ai_consent()`.
- Add/update consent tests for missing `pd_processing`.
- Make Listing Assistant cache key user-scoped and ensure cache reads/writes use the scoped key.
- Add tests covering per-user cache isolation where practical.
- Run focused AI/consent tests and ruff.

### Wave 50 — Trusted client IP and rate limiter keying

- Harden `get_client_ip()` so `CF-Connecting-IP` is trusted only from trusted proxy peers.
- Add tests for spoofed CF header from direct clients and trusted proxy cases.
- Update SlowAPI limiter fallback to use the shared helper.
- Harden nginx map so direct clients cannot choose arbitrary `$rate_limit_key` via CF header.
- Run focused client IP / limiter tests and nginx config-adjacent checks where feasible.

### Wave 51 — Erasure and AI audit observability

- Explicitly delete DLQ rows by `telegram_user_id` during account deletion.
- Add deletion test for DLQ row with `user_id IS NULL`.
- Add observable AI audit failure counter and tests.
- Keep audit write best-effort unless a future policy requires fail-closed behavior.

### Wave 52 — Frontend a11y, reduced motion, and touch targets

- Make focus trap live-query focusable elements.
- Add shared tablist keyboard helper and wire items tabs + Listing Assistant tabs.
- Add missing tab ARIA relationships.
- Respect reduced motion in `scrollSectionIntoView()` and shortcut scrolls.
- Increase header touch hit areas to 44x44.
- Rebuild frontend bundle/CSS and bump static version if needed.

### Wave 53 — Ops/docs hygiene

- Resolve XFO/CSP Telegram embedding conflict in nginx.
- Update README test counts to the current suite.
- Add `bad-app` to CI push triggers.
- Keep large-bundle concern documented as a follow-up, because safe bundle decomposition is larger than an audit-remediation wave.

### Final verification

- `uv run ruff check .`
- `uv run pytest --tb=short -q`
- Any focused frontend/static rebuild checks required by touched files.
- `git status --short` to confirm only intended files changed plus the pre-existing `D mybad.md`.
