# Deep Dive Review V3 — Post-Fix Audit Report

**Date:** 2026-05-09
**Scope:** Full-stack audit after commits `95818a0`, `f6cfcfa`, `7b7da9b`
**Method:** 5 parallel subagent audits (security, backend, database, frontend, infrastructure)

---

## Executive Summary

Of the ~45 issues identified in V1, **~30 are confirmed FIXED** across 3 commit waves. This V3 audit found **5 P0-equivalent remaining, 22 P1, 28 P2, 19 P3** issues across all domains. No critical (P0) showstoppers remain — the app is functional and reasonably secure for a local Docker + Cloudflare tunnel setup.

### Score Card

| Domain | Fixed | Remaining P1 | Remaining P2 | Remaining P3 |
|--------|-------|-------------|-------------|-------------|
| Security | 14 | 5 | 7 | 7 |
| Backend Logic | 12 | 4 | 8 | 6 |
| Database | 6 | 3 | 6 | 6 |
| Frontend | 3 | 4 | 15 | 9 |
| Infrastructure | 3 | 3 | 5 | 3 |
| **Total** | **~38** | **19** | **41** | **31** |

---

## CONFIRMED FIXED (Previous Waves)

### Security (14 fixed)
- CSRF middleware (Origin check on POST/PATCH/DELETE) — `api/main.py:171-210`
- Prompt injection sanitization in AI tools — `api/services/ai_service.py:393-426`
- Debug mode protection (ENV=production) — `api/dependencies.py:69`
- Cloudflare IP detection (CF-Connecting-IP priority) — `api/routers/consent.py:61-84`
- CORS whitelist (web.telegram.org) — `api/main.py:109-114`
- CSP meta-tag — `nginx/default.conf:17`
- telegram_init_data_max_age 3600→7200 — `api/config.py:44`
- Redis URL password masking — `api/limiter.py:55-63`
- Rate limiting on /leads, /watchlist, /trackers — 36 endpoints
- AI timeout read=45s — `api/services/ai_service.py:730`
- Security headers middleware — `api/main.py:148-155`
- Non-root Docker user — `Dockerfile:13,21`
- Bot token as SecretStr — `api/config.py:17`
- `.env` in .gitignore — `.gitignore:5`

### Backend Logic (12 fixed)
- exc_info=True in global exception handler — `api/main.py`
- Currency service graceful fallback
- Pagination (limit/offset) on listings endpoint
- AI timeout optimization with asyncio.wait_for
- Image proxy asyncio.Lock
- AI separate connection pool (Limits(20,10))
- Cache-Control middleware per path
- Redis health check with in-memory fallback
- User auto-provision middleware + ensure_user_exists
- AI audit log (best-effort)
- AI consent gate before AI endpoints
- Bulk INSERT snapshots for watchlist refresh

### Database (6 fixed)
- PK/FK unified to BigInteger (users.id + all referencing FKs)
- Partial indexes synced (idx_trackers_user_active_partial)
- Index on last_checked_at
- DESC in tracker_events index
- Timezone for updated_at on query_listing_states
- Bulk INSERT for LeadItemPriceSnapshot

### Frontend (3 fixed)
- virtual_list.js dead code removed
- CSP meta-tag added to index.html
- Google Fonts inline event handler removed

### Infrastructure (3 fixed)
- Cloudflared service in docker-compose
- Health endpoint checks DB + Redis
- .env.example with all variables documented

---

## REMAINING ISSUES

### SECURITY

#### P1 — High

| # | Issue | File | Description |
|---|-------|------|-------------|
| S-P1-1 | CORS `allow_credentials` not explicit | `api/main.py:125-130` | Omitting `allow_credentials=False` risks credential leak if cookie auth is added later |
| S-P1-2 | CORS origin `"null"` in allowlist | `api/main.py:114` | Enables sandbox-origin bypass; needed for Telegram iOS but opens attack surface |
| S-P1-3 | No rate limiting on consent/account endpoints | `api/routers/consent.py:87,134,215,256,313` | All 5 consent endpoints lack `@limiter.limit`; enables table flooding and DoS |
| S-P1-4 | Redis exposed on port 6380 without auth | `docker-compose.yml:85-86` | No `requirepass`; any host process can read/write cached data |
| S-P1-5 | Health endpoint leaks internal state | `api/routers/health.py:15-38` | `/health` returns DB and Redis status to unauthenticated requests |

#### P2 — Medium

| # | Issue | File | Description |
|---|-------|------|-------------|
| S-P2-1 | Missing HSTS header | `api/main.py:148-155` | No Strict-Transport-Security; MITM can strip HTTPS client-side |
| S-P2-2 | Debug mode check relies on URL string matching | `api/config.py:46-61` | `localhost` substring match can misfire on remote DB hostnames |
| S-P2-3 | Error message reflects user input | `api/routers/consent.py:98` | `f"Неизвестный тип: {consent_type}"` reflects path param in error response |
| S-P2-4 | Export tokens not HMAC-signed, no TTL enforcement | `api/routers/ai_analysis.py:1281-1301` | In-memory store accumulates if pruning doesn't run; process restart breaks URLs |
| S-P2-5 | CSP uses `'unsafe-eval'` and `'unsafe-inline'` | `nginx/default.conf:17` | Significantly weakens XSS protection |
| S-P2-6 | Auto-provision errors silently swallowed | `api/main.py:213-226` | `except Exception` hides DB failures without logging |
| S-P2-7 | No DELETE /account confirmation mechanism | `api/routers/consent.py:256-310` | Irreversible cascade delete requires only Telegram init_data |

#### P3 — Low

| # | Issue | File | Description |
|---|-------|------|-------------|
| S-P3-1 | Cloudflared is opt-in (profiles: tunnel) | `docker-compose.yml:105` | `docker compose up` without `--profile tunnel` runs without TLS |
| S-P3-2 | Frontend served over HTTP (port 8081) | `docker-compose.yml:73-74` | Local nginx is plain HTTP |
| S-P3-3 | Dockerfile installs unused `nodejs` | `Dockerfile:9` | Unnecessary attack surface (~100MB bloat) |
| S-P3-4 | Secrets passed as plain env vars | `docker-compose.yml:6,33,51` | Visible via `docker inspect` |
| S-P3-5 | Dependencies use `>=` ranges, not pinned | `pyproject.toml:7-24` | Compromised upstream release would be auto-installed |
| S-P3-6 | X-Frame-Options DENY vs SAMEORIGIN mismatch | `api/main.py:153` vs `nginx/default.conf:15` | Confusing but actually correct by intent |
| S-P3-7 | Rate limiter degrades to per-process in-memory | `api/limiter.py:88-95` | Multi-worker = N× limit bypass |

#### NEW Security Issues

| # | Severity | Issue | File | Description |
|---|----------|-------|------|-------------|
| S-N1 | P1 | Missing rate limit on /account/export | `api/routers/consent.py:313-497` | 6+ sequential DB queries without rate limit = DoS vector |
| S-N2 | P1 | Missing rate limit on DELETE /account | `api/routers/consent.py:256-310` | Repeated cascade deletes cause DB load spikes |
| S-N3 | P2 | AI task IDs not tied to requesting user | `api/routers/ai_analysis.py:1203` | `GET /task/{task_id}` doesn't verify ownership; any user can read results |
| S-N4 | P2 | Export tokens not tied to requesting user | `api/routers/ai_analysis.py:1290-1301` | Any authenticated user with a token can download report |

---

### BACKEND LOGIC

#### P1 — High

| # | Issue | File | Description |
|---|-------|------|-------------|
| B-P1-1 | `auto_provision_user` races with DB commit | `api/main.py:213-226` | Middleware runs after `call_next`; first request can hit FK violation |
| B-P1-2 | In-memory task/export stores leak across workers | `api/routers/ai_analysis.py:78-79` | `_tasks`/`_exports` dicts are process-local; shadow store checked before Redis |
| B-P1-3 | `_transcoded_cache_lock` created at module import | `api/routers/image_proxy.py:81` | `asyncio.Lock()` before event loop starts = wrong loop binding |
| B-P1-4 | `DealExpenseUpdate.amount_byn` allows negative/zero | `api/schemas.py:517` | No `Field(gt=0)` on update schema; corrupts ROI calculations |

#### P2 — Medium

| # | Issue | File | Description |
|---|-------|------|-------------|
| B-P2-1 | `export_leads` loads ALL leads without pagination | `api/routers/export.py:193-198` | No LIMIT cap; xlsx build blocks event loop synchronously |
| B-P2-2 | Bare `except Exception: pass` in account deletion | `api/routers/consent.py:299-300,307-308` | Cache/AI errors silently swallowed during deletion |
| B-P2-3 | `create_tracker` commits inside savepoint incorrectly | `api/routers/trackers.py:168-206` | `session.commit()` inside `begin_nested()` commits outer transaction too |
| B-P2-4 | `_build_xlsx_workbook` blocks event loop | `api/routers/export.py:98-152` | openpyxl is synchronous; should use `asyncio.to_thread` |
| B-P2-5 | `update_expense` allows zero/negative amount | `api/routers/expenses.py:113` | PATCH sets `amount_byn` without validation |
| B-P2-6 | Redis INCR race on initial key creation | `api/services/cache.py:173-178` | SET NX + INCR gap can give one free request |
| B-P2-7 | Currency service only extracts USD rate | `api/services/currency_service.py:80-89` | EUR/RUB prices return unchanged BYN value |
| B-P2-8 | CSRF middleware rebuilds allowed set per request | `api/main.py:172-210` | `get_settings()` called on every request; should cache |

#### P3 — Low

| # | Issue | File | Description |
|---|-------|------|-------------|
| B-P3-1 | Health check uses fallback engine | `api/routers/health.py:15-38` | Could mask startup failures |
| B-P3-2 | `LeadRead` has mutable default for computed fields | `api/schemas.py:396-398` | Inconsistent with rest of schemas |
| B-P3-3 | `get_lead_analytics` loads all ORM leads | `api/routers/analytics.py:127-138` | Should use SQL aggregation instead |
| B-P3-4 | `image_proxy._http_client` not thread-safe | `api/routers/image_proxy.py:177` | Race on concurrent client creation |
| B-P3-5 | `_limiter` does blocking socket connect at import | `api/limiter.py:49` | Blocks event loop during startup |
| B-P3-6 | `_run_analysis` is 560 lines (SRP violation) | `api/routers/ai_analysis.py:615-1157` | God function; hard to test |

#### NEW Backend Issues

| # | Severity | Issue | File | Description |
|---|----------|-------|------|-------------|
| B-N1 | P1 | Unbounded parallel Kufar queries in refresh | `api/routers/workflow.py:588-599,713-724` | `asyncio.gather` with no concurrency cap; 50 watchlist items = 20+ simultaneous chains |
| B-N2 | P2 | `update_lead` auto-transitions to "sold" without reverse guard | `api/routers/workflow.py:248-249` | Setting `sold_price_byn=None` leaves status "sold" with stale `sold_at` |
| B-N3 | P2 | Deferred imports inside endpoint functions | `api/routers/consent.py:102,157,231,268,304` | Import resolution on every request; module structure smell |
| B-N4 | P2 | AI price-advice doesn't validate `advice` field | `api/routers/ai_tools.py:233` | Hallucinated values pass through unvalidated |
| B-N5 | P2 | `_coerce_pricing` uses `# type: ignore[arg-type]` | `api/routers/ai_listing_assistant.py:86-101` | Suppresses type mismatch instead of fixing it |
| B-N6 | P3 | `delete_all_leads` doesn't cascade-delete `LeadReminder` | `api/routers/workflow.py:256-291` | Orphaned reminders after bulk delete (FK CASCADE handles it at DB level) |

---

### DATABASE

#### P1 — High

| # | Issue | File | Description |
|---|-------|------|-------------|
| D-P1-1 | Duplicate indexes (index=True + explicit Index) | `api/models.py` (12 instances) | Wastes disk + write amplification on telegram_user_id, user_id, lead_id, etc. |
| D-P1-2 | Missing `DateTime(timezone=True)` on `LeadItem.updated_at` | `api/models.py:337-341` | Stores naive timestamps; inconsistent with all other columns |
| D-P1-3 | `LeadReminder.lead` uses `backref` not explicit relationship | `api/models.py:479` | Implicit `reminders` attr on LeadItem; no cascade="all, delete-orphan" |

#### P2 — Medium

| # | Issue | File | Description |
|---|-------|------|-------------|
| D-P2-1 | No unique constraint on SavedSearch(user_id, query, strict_mode) | `api/models.py:559-604` | Unlimited duplicate entries possible |
| D-P2-2 | Missing CheckConstraint on DealExpense.expense_type | `api/models.py:418` | Invalid expense types can be inserted |
| D-P2-3 | Export loads all user data into memory (N+1 pattern) | `api/routers/consent.py:330-497` | 7 sequential ORM queries; should use streaming |
| D-P2-4 | `get_lead_analytics` loads full ORM objects | `api/routers/analytics.py:127-138` | Should use column-level select for financial calculations |
| D-P2-5 | Missing composite index on UserConsent(user_id, consent_type) | `api/models.py:487-524` | Hot path (every AI request); current indexes don't cover the query |
| D-P2-6 | Missing index for due-reminder polling | `api/models.py:482-484` | `idx_reminders_due` not optimized for `sent=false AND remind_at <= now()` |

#### P3 — Low

| # | Issue | File | Description |
|---|-------|------|-------------|
| D-P3-1 | `idx_trackers_active` redundant with composite | `api/models.py:173-178` | Single-column covered by prefix of `(active, paused)` |
| D-P3-2 | `idx_lead_items_status` redundant with composite | `api/models.py:361-363` | Almost all queries filter by user_id first |
| D-P3-3 | `onupdate=func.now()` doesn't fire for bulk updates | `api/models.py:226-231,337-341` | ORM-only; bulk `update()` won't bump timestamp |
| D-P3-4 | QuerySnapshot/QueryListingState have no user_id | `api/models.py:189-238` | By design (shared analytics), but no DB-level access control |
| D-P3-5 | Alembic downgrade unsafe for users.id type change | `migrations/versions/6040ea4a0fd6:91-94` | Will truncate if id > INT_MAX |
| D-P3-6 | No `updated_at` on Tracker model | `api/models.py:134-179` | Uses TimestampMixin (created_at only); no modification tracking |

---

### FRONTEND

#### P1 — High

| # | Issue | File | Description |
|---|-------|------|-------------|
| F-P1-1 | CSP `script-src` allows `'unsafe-inline'` | `index.html:7` | Defeats most XSS protection |
| F-P1-2 | Virtual scrolling disabled for all lists | `render_cards.js:360,690-692` | 200+ items = all DOM nodes created eagerly |
| F-P1-3 | `aria-live="polite"` on virtual list viewport | `virtual_list.js:58` | Announces every DOM mutation to screen readers during scroll |
| F-P1-4 | `signal` undefined in card builder helpers | `render_card_builders.js:393,404,461` | Event listeners on inputs never cleaned up; memory leak |

#### P2 — Medium

| # | Issue | File | Description |
|---|-------|------|-------------|
| F-P2-1 | `_buildSkeletonCard` uses `innerHTML` | `render_cards.js:45` | Inconsistent with codebase convention (domEl/domAppend) |
| F-P2-2 | `renderAll()` called excessively | Multiple files | Re-renders all panels even unchanged ones |
| F-P2-3 | `innerHTML` for hardcoded SVGs in empty states | `render_core.js:280` | Safe today but fragile pattern |
| F-P2-4 | Pull-to-refresh transforms document.documentElement | `dom_helpers.js:505` | Forces full-page layout recalculation on every touchmove |
| F-P2-5 | Image preloads kept in `_preloadCache` | `api_events.js:87,838-841` | Ghost Image objects never cleared until navigation |
| F-P2-6 | `sortedKeys` rebuilt on every render | `virtual_list.js:145` | O(n log n) per insert; should use sorted-insert |
| F-P2-7 | Skip link target is `<span tabindex="-1">` | `index.html:75` | `<main>` element is more semantic |
| F-P2-8 | No visible focus indicators on custom buttons | CSS | `.listing-btn`, `.wl-btn`, `.lead-btn` may lack `:focus-visible` |
| F-P2-9 | Long-press menu not keyboard accessible | `dom_helpers.js:975-1053` | Touch-only; no keyboard equivalent (Shift+F10) |
| F-P2-10 | No `Telegram.WebApp.ready()` call visible | JS files | Loading placeholder may show longer than needed |
| F-P2-11 | No `Telegram.WebApp.expand()` on load | JS files | Mini App may render in collapsed state |
| F-P2-12 | Single monolithic CSS file (~2600 lines) | `css/style.css` | No source map, no section markers |
| F-P2-13 | CSS custom properties not fully leveraged | CSS | Hardcoded colors may not adapt to all Telegram themes |
| F-P2-14 | Swipe gesture disabled on cards | `render_card_builders.js:720-726` | Telegram-native pattern lost due to visual bug |
| F-P2-15 | Pull-to-refresh conflicts with search input | `dom_helpers.js:461-472` | Touch target not excluded from gesture detection |

#### P3 — Low

| # | Issue | File | Description |
|---|-------|------|-------------|
| F-P3-1 | No JS bundler/module system | `index.html:36-56` | 22 separate `<script defer>` tags; no tree-shaking |
| F-P3-2 | `getVirtualListRenderedCount` and `hasVirtualList` dead code | `virtual_list.js:249-260` | Never called |
| F-P3-3 | `document.write` in PDF export | `api_ai.js:1293` | Modern alternative: `URL.createObjectURL(new Blob(...))` |
| F-P3-4 | Error bar uses `aria-live="assertive"` | `index.html:211` | Disruptive for frequent network errors |
| F-P3-5 | Haptic feedback not using shared wrapper | `api_events.js` (11 locations) | Repeated `try { _tgHaptic()?.impactOccurred?.("light") } catch` pattern |
| F-P3-6 | No offline indicator | — | No visual feedback when viewing stale cached data |
| F-P3-7 | Double-tap zoom not suppressed on carousel images | CSS | Missing `touch-action: manipulation` |
| F-P3-8 | `_escXml` duplicates `escapeHtml` | `api_ai.js:1299-1306` | DRY violation |
| F-P3-9 | Filter dropdown `setTimeout` hack | `api_events.js:363-367` | Race on rapid toggle; should track timeout ID |

---

### INFRASTRUCTURE

#### P1 — High

| # | Issue | File | Description |
|---|-------|------|-------------|
| I-P1-1 | Dockerfile broken layer cache | `Dockerfile:15-16` | `COPY . .` before `uv sync`; any code change reinstalls all deps |
| I-P1-2 | `cloudflared` uses `:latest` tag | `docker-compose.yml:96` | Non-reproducible; breaking upgrades on pull |
| I-P1-3 | Redis has no persistence volume | `docker-compose.yml:83-93` | All cached data lost on `docker compose down` |

#### P2 — Medium

| # | Issue | File | Description |
|---|-------|------|-------------|
| I-P2-1 | No logging configuration for containers | `docker-compose.yml` | Unbounded `json-file` log growth |
| I-P2-2 | `nodejs` installed but unused | `Dockerfile:9` | ~100MB image bloat |
| I-P2-3 | `.dockerignore` missing entries | `.dockerignore` | `.github/`, `*.md`, `LICENSE` leak into image |
| I-P2-4 | No uvicorn access log configuration | `Dockerfile:26` | No structured log format |
| I-P2-5 | API healthcheck `start_period` too short | `docker-compose.yml:24` | 10s may not be enough for cold start |

#### P3 — Low

| # | Issue | File | Description |
|---|-------|------|-------------|
| I-P3-1 | Empty `CLOUDFLARE_TUNNEL_TOKEN` in .env | `.env:29` | Confusing UX; cloudflared starts and immediately fails |
| I-P3-2 | Redis port exposed to host | `docker-compose.yml:86` | Unnecessary for Docker network communication |
| I-P3-3 | Bot/scheduler services have no healthchecks | `docker-compose.yml:30-63` | Cannot detect silent crashes |

#### NEW Infrastructure Issues

| # | Severity | Issue | File | Description |
|---|----------|-------|------|-------------|
| I-N1 | P2 | CI builds Docker image but never tests it | `.github/workflows/ci.yml:106-113` | `push: false` with no smoke test; build verification is syntax-only |

---

## TOP 10 PRIORITY FIXES

These are the highest-impact, lowest-effort fixes to tackle next:

| # | Issue | Effort | Impact |
|---|-------|--------|--------|
| 1 | Add `@limiter.limit` to all 5 consent/account endpoints | 5 min | Prevents DoS on account/export/delete |
| 2 | Fix `DealExpenseUpdate.amount_byn` validation (`Field(gt=0)`) | 2 min | Prevents corrupt ROI calculations |
| 3 | Lazy-init `asyncio.Lock()` in image_proxy.py | 5 min | Prevents wrong-loop RuntimeError |
| 4 | Remove `DateTime(timezone=True)` gap on `LeadItem.updated_at` | 10 min | Schema consistency |
| 5 | Fix Dockerfile layer cache (COPY pyproject.toml first) | 5 min | Saves 30-60s per rebuild |
| 6 | Add Redis persistence volume in docker-compose | 5 min | Prevents state loss on restart |
| 7 | Pin cloudflared image tag | 2 min | Reproducible builds |
| 8 | Remove duplicate indexes (12 instances) | 15 min | Reduces write amplification |
| 9 | Add concurrency cap to watchlist/leads refresh | 10 min | Prevents Kufar API exhaustion |
| 10 | AI task/export ownership verification | 15 min | Prevents cross-user data access |

---

## OUT OF SCOPE (Per User Requirements)

The following are acknowledged but excluded per project constraints:

- Production deployment (nginx hardening, resource limits, restart policies)
- Prometheus monitoring / observability stack
- CSS/JS bundling / build pipeline
- Secrets rotation / git history cleanup (needs `git filter-repo`)
- Type checking in CI (`mypy`/`pyright`)

---

## STATISTICS

| Metric | V1 | V2 | V3 |
|--------|----|----|-----|
| Total issues found | ~45 | ~40 | 91 |
| P0 Critical | 8 | 2 | 0 |
| P1 High | 22 | 8 | 19 |
| P2 Medium | 20+ | 20+ | 41 |
| P3 Low | — | 10+ | 31 |
| Issues fixed since V1 | — | ~6 | ~38 |
| Fix rate | — | 13% | 84% |
