# Codebase Concerns

**Analysis Date:** 2026-04-22

## Tech Debt

**Price columns use `Float` instead of `Numeric/Decimal` in database models:**
- Issue: All price fields (`price_byn`, `last_price_byn`, `initial_price_byn`, `current_price_byn`, `last_seen_price_byn`) use `Float` columns which are subject to IEEE 754 floating-point rounding errors. Only `buy_price_byn` and `sold_price_byn` on `LeadItem` and `amount_byn` on `DealExpense` use `Numeric(10, 2)`.
- Files: `api/models.py` lines 123, 154, 249, 293, 324, 378-379
- Impact: Accumulated rounding errors in financial calculations (profit, ROI, price delta). Two users could see slightly different prices for the same listing depending on calculation path.
- Fix approach: Migrate price columns to `Numeric(10, 2)` via Alembic migration. Update `normalize_price_byn()` to return `Decimal` or round consistently. Most critical for `current_price_byn`, `initial_price_byn` on `WatchlistItem` and `price_byn` on `LeadItem`.

**No structured logging or log level configuration in API service:**
- Issue: The API service relies on `uvicorn`'s logging configuration. Only the scheduler (`scheduler/collector.py` line 39) calls `logging.basicConfig()`. The API has no log level config, no structured log format, and no request-ID tracking.
- Files: `api/main.py`, `api/services/*.py`
- Impact: Difficult to debug production issues, trace requests across services, or filter logs by severity.
- Fix approach: Add a `logging.config.dictConfig()` setup in `api/main.py` with JSON-structured output, request-ID middleware, and configurable log level via `LOG_LEVEL` env var.

**Swallowed exceptions in AI analysis anomaly/deal score computation:**
- Issue: Two `except Exception: pass` blocks silently discard errors during anomaly flag and deal score computation in the AI analysis router. If these computations fail, the AI analysis proceeds with empty data and no error indication.
- Files: `api/routers/ai_analysis.py` lines 309-310, 316-317
- Impact: Silent data loss. AI analysis could return misleading results if anomaly detection or deal scoring fails.
- Fix approach: Replace bare `pass` with `logger.warning()` calls at minimum. Consider propagating the error as a partial-result warning in the AI response.

**`_normalize_response_ads` heuristic for price unit detection:**
- Issue: The `query_pipeline.py` function detects whether Kufar API returned prices in BYN (direct) or kopecks using a heuristic based on median value ranges. This is fragile and depends on market price distributions remaining within expected bounds.
- Files: `api/services/query_pipeline.py` lines 47-85
- Impact: If Kufar changes API behavior or a query returns unusual price distributions, prices could be double-converted or left as kopecks.
- Fix approach: Add explicit API version or price-unit indicator detection. At minimum, log a warning when the heuristic triggers.

**Singleton AI service with no lifecycle management:**
- Issue: `AIService` uses a module-level singleton pattern (`get_ai_service()` in `api/services/ai_service.py` line 868). The httpx client is lazily created and only closed in the app lifespan handler by reaching into a private attribute (`ai._httpx_client`).
- Files: `api/services/ai_service.py` lines 864-872, `api/main.py` line 73
- Impact: If the singleton is accessed before the app starts or after shutdown, it could use a closed client. The private attribute access in `lifespan` is fragile.
- Fix approach: Register the AI service on `app.state` during lifespan, similar to cache and currency_service. Use a proper async context manager or shutdown hook.

## Known Bugs

**ruff UP017 false positive with `datetime.UTC`:**
- Symptoms: `ruff` suggests replacing `timezone.utc` with `datetime.UTC`, but `datetime.UTC` does not exist as a class attribute on the `datetime` type (it is `datetime.timezone.UTC` or just imported as `UTC` from `datetime`).
- Files: All files using `from datetime import UTC` (e.g., `scheduler/collector.py`, `api/services/workflow_store.py`, `api/routers/workflow.py`)
- Trigger: Running `ruff check .`
- Workaround: Project uses `from datetime import UTC` (Python 3.11+ re-export) and ignores UP017. This is correct.

**Gemma 4 reasoning token consumption limits output budget:**
- Symptoms: AI analysis returns truncated or empty JSON when the model's internal reasoning consumes most of the `max_tokens` budget.
- Files: `api/services/ai_service.py` lines 400-408
- Trigger: Complex listing analysis with many alternatives, long descriptions, or detailed category hints.
- Workaround: `max_tokens` set to 2800 and `_parse_json()` extracts JSON from the `reasoning` field as fallback. The `response_format` parameter is intentionally omitted because it causes empty `content` with Gemma 4.

**CSS `display: flex/grid` overrides HTML `hidden` attribute:**
- Symptoms: Elements using both `display: flex/grid` and the `hidden` attribute remain visible because CSS display property takes precedence over the browser's default `[hidden] { display: none }`.
- Files: `frontend/css/style.css` (7 specific override rules for `[hidden]`)
- Trigger: Any new element that uses both flex/grid layout and `hidden`.
- Workaround: Each affected element has a specific `[hidden]` override rule. New elements need the same pattern.

## Security Considerations

**Debug mode bypasses all Telegram authentication:**
- Risk: When `debug=true` in `.env`, all requests without `X-Telegram-Init-Data` header receive `user_id=0`. Any endpoint scoped to `user_id=0` returns aggregate data from all debug-mode users. CORS is also widened to allow localhost.
- Files: `api/dependencies.py` lines 51-54, `api/config.py` line 29
- Current mitigation: Debug mode requires explicit `.env` configuration. Production should never have `debug=true`.
- Recommendations: Add a startup warning log when debug mode is active. Consider requiring a separate `DEBUG_SECRET` header even in debug mode to prevent accidental exposure.

**Nginx CORS header uses wildcard origin:**
- Risk: The `add_header Access-Control-Allow-Origin *` in the Nginx config allows any origin to make requests to the API proxy endpoint. This bypasses the FastAPI CORS middleware's origin restriction.
- Files: `nginx/default.conf` line 25
- Current mitigation: Telegram auth (`X-Telegram-Init-Data` HMAC verification) protects data access.
- Recommendations: Replace the wildcard with the configured `MINI_APP_URL` origin. Pass it as an environment variable to the Nginx container.

**Rate limiter parses Telegram initData on every limited request:**
- Risk: The rate limiter (`api/limiter.py` lines 24-36) re-verifies the Telegram initData HMAC signature for every request to extract the user ID for rate-limit keying. This is redundant computation and creates a side channel: if `verify_telegram_init_data` raises, the limiter silently falls back to IP-based limiting.
- Files: `api/limiter.py` lines 28-35
- Current mitigation: Exception is caught and falls back to IP.
- Recommendations: Parse user_id from request.state if the dependency already ran, or extract from the header without full HMAC verification (just extract the user JSON from the parsed query string).

**No input sanitization on `query` parameter for logging:**
- Risk: User-supplied query strings are logged verbatim in multiple places. While this is valuable for debugging, multi-line or control-character queries could pollute logs.
- Files: `api/routers/ai_analysis.py` line 199, `scheduler/collector.py` line 332
- Current mitigation: Query length is capped at 255 chars by `api/validators.py`.
- Recommendations: Consider truncating queries in log output to 80 chars and stripping newlines.

## Performance Bottlenecks

**Opportunity board makes N parallel Kufar API requests:**
- Problem: `GET /saved-searches/opportunity-board` calls `_load_saved_search_opportunities` for each saved search (up to 8), each creating a new `KufarClient` that calls `search_all_ads` (potentially fetching up to 25 pages). This can trigger 200+ HTTP requests to Kufar.
- Files: `api/routers/saved_searches.py` lines 298-308
- Cause: Each saved search requires a fresh Kufar API query. No shared caching between searches.
- Improvement path: Cache Kufar responses per query with a short TTL (already partially done via `load_query_dataset`). Reuse a single `KufarClient` instance across all searches in the opportunity board request.

**AI analysis makes two sequential Kufar API calls on fallback:**
- Problem: `/ai/analyze` first tries strict search, then falls back to non-strict if the target ad is not found. Each call creates a new `KufarClient` and fetches potentially many pages.
- Files: `api/routers/ai_analysis.py` lines 200-225
- Cause: Strict search may exclude the target ad itself if the title doesn't match strictly.
- Improvement path: Consider a single non-strict search followed by client-side strict filtering for similar listings, keeping the target ad always present. Or fetch the target ad by ID directly from Kufar if such an endpoint exists.

**Watchlist/leads refresh makes one Kufar query per unique query group:**
- Problem: Refreshing watchlist or leads groups items by query and makes a separate Kufar API call per group. A user with many distinct queries triggers many API calls.
- Files: `api/routers/workflow.py` lines 404-443, 499-524
- Cause: No deduplication of queries across different users or request batches.
- Improvement path: Batch refresh requests and share Kufar responses across users for the same query within a time window.

**Frontend `style.css` is 6053 lines in a single file:**
- Problem: The entire CSS is in one monolithic file with no code-splitting, no build step, and no minification.
- Files: `frontend/css/style.css`
- Cause: No CSS preprocessor or build pipeline. Dark/light theming via CSS custom properties increases file size.
- Improvement path: Split CSS into per-module files (cards, modals, trackers, charts) and consider a simple build step for concatenation and minification. This would also improve cache granularity.

## Fragile Areas

**AI JSON parsing with multi-strategy fallback:**
- Files: `api/services/ai_service.py` lines 817-861 (`_parse_json` method)
- Why fragile: Three parsing strategies (direct parse, balanced-brace extraction, greedy regex) handle edge cases from different AI models and reasoning chains. Changes to the AI model or API provider could break the expected output format. The greedy regex (`\{.*\}` with `re.DOTALL`) could match incorrect JSON boundaries in reasoning text.
- Safe modification: When changing AI model or provider, test `_parse_json` against actual outputs. Consider adding a contract test with known inputs/outputs. When adding new parsing strategies, add them before the greedy regex.
- Test coverage: No dedicated unit tests for `_parse_json` edge cases found.

**Scheduler tracker check runs sequentially per query group:**
- Files: `scheduler/collector.py` lines 300-408
- Why fragile: All tracker query groups are processed sequentially in a single database session. An error in one query group is caught and logged but uses a shared session that is committed at the end. If a previous query's ORM state is dirty when an exception occurs, the commit at line 402 could fail or commit partial state.
- Safe modification: The current error handling logs and continues, which is reasonable. However, changes to ORM operations within the loop should be tested with failure scenarios. Consider using `session.flush()` after each query group to isolate failures.

**Frontend module communication via shared context object:**
- Files: `frontend/js/app_renderers.js`, `frontend/js/app_actions.js`
- Why fragile: All renderer and action modules share a flat `context` object with destructured imports. Adding a new module requires passing through all needed functions. As noted in CLAUDE.md, `createAppRenderers` must both destructure AND return functions like `safeUrl` in its public API, otherwise downstream modules get `undefined`.
- Safe modification: When adding new cross-module dependencies, trace the full chain from creation (in `app_core.js`) through `app_renderers.js` to the consuming module. Test that all destructured values are defined after module initialization.

**`load_query_dataset` creates and destroys a KufarClient per call:**
- Files: `api/services/query_pipeline.py` lines 119-150
- Why fragile: Every call to `load_query_dataset` instantiates a new `KufarClient` (which creates an `httpx.AsyncClient`) and closes it in a `finally` block. This means each API request that queries Kufar creates a fresh HTTP client with no connection reuse.
- Safe modification: If modifying to share a client, be careful about the `_enforce_delay` rate limiter which uses instance state (`_last_request_time`). Shared clients need per-request delay tracking.

## Scaling Limits

**Kufar API rate limiting:**
- Current capacity: Configurable via `KUFAR_REQUEST_DELAY` (default 1.0s) and `KUFAR_PARALLEL_SEMAPHORE` (default 2-3)
- Limit: Kufar is a third-party API with undocumented rate limits. The app uses a fixed delay between requests. With many concurrent users triggering searches, the semaphore may cause request queuing and timeouts.
- Scaling path: Implement request batching, shared result caching across users for the same query, and adaptive rate limiting based on Kufar's 429 responses.

**Database connection pool under load:**
- Current capacity: `db_pool_size=10`, `db_max_overflow=20` (30 total connections)
- Limit: The scheduler, API, and bot share the same PostgreSQL instance. Each service creates its own engine with the same pool settings. Under load (many simultaneous API requests + scheduler queries), the total connection count could exceed PostgreSQL's `max_connections`.
- Scaling path: Configure pool size per service (API needs more, scheduler needs fewer). Use PgBouncer for connection pooling. Monitor `pool_timeout` metrics.

**Memory cache bounded at 500 entries:**
- Current capacity: `MemoryCache.MAX_ENTRIES = 500` with LRU eviction
- Limit: If Redis is unavailable and the app falls back to `MemoryCache`, each cached API response (potentially hundreds of KB for listing data) consumes heap memory. 500 entries of large listing responses could consume significant RAM.
- Scaling path: Add memory-based eviction in addition to count-based. Monitor cache hit rate. Alert when falling back to memory cache.

## Dependencies at Risk

**Together AI (Gemma 4 27B IT):**
- Risk: Single AI provider. Model-specific workarounds (no `response_format`, reasoning token handling, `_parse_json` multi-strategy) tightly couple the service to Gemma 4's behavior. Provider outages or model deprecation would break all AI features.
- Impact: AI analysis, quick condition check, and all AI-powered features become unavailable.
- Migration plan: Abstract the AI provider behind a protocol that supports multiple backends. Test with alternative models/providers (OpenAI, DeepSeek, local models). The `_parse_json` fallback already handles varied output formats.

**Kufar API (undocumented third-party):**
- Risk: The app depends on Kufar's search-rendered-paginated endpoint with no official API contract. Changes to response structure, field names, or price units could break listing parsing silently.
- Impact: All listing data, price analytics, tracker notifications, and deal scoring become incorrect or unavailable.
- Migration plan: Add response schema validation with explicit error logging when unexpected structures are encountered. The `_normalize_response_ads` heuristic is a partial mitigation. Add integration tests that validate against actual API responses periodically.

**slowapi rate limiter:**
- Risk: Uses in-memory storage by default (no Redis-backed storage configured). Rate limit state is lost on server restart and not shared across workers.
- Impact: Rate limits are per-process, not per-user across the service. A restart resets all limits.
- Migration plan: Configure slowapi to use the existing Redis instance as storage backend. This is a one-line change in `api/limiter.py`.

## Missing Critical Features

**No pagination for tracker events:**
- Problem: Tracker events are loaded in bulk per tracker. With active trackers generating events every check interval, the events list can grow large. The frontend uses virtual scrolling (`frontend/js/virtual_list.js`) to mitigate rendering cost, but the API still returns all events in a single response.
- Files: `api/routers/trackers.py`
- Blocks: Scalability for users with many active trackers over extended periods.

**No user-facing error recovery for AI service outages:**
- Problem: When the AI service is down, rate-limited, or the Together AI balance is exhausted, users see generic error messages. There is no retry mechanism or queue for pending analyses.
- Files: `api/routers/ai_analysis.py` lines 357-381
- Blocks: User experience during AI service disruptions.

## Test Coverage Gaps

**No tests for AI service JSON parsing edge cases:**
- What's not tested: `_parse_json()` in `api/services/ai_service.py` with various AI model outputs (truncated JSON, reasoning chains, markdown-wrapped JSON, nested braces in text).
- Files: `api/services/ai_service.py` lines 817-861
- Risk: Model updates or provider changes could silently break AI response parsing.
- Priority: High

**No integration tests for scheduler collector:**
- What's not tested: The full `check_trackers()` flow including tracker grouping, Kufar API fetching, event persistence, and user notification. Tests exist for individual components (`test_bot_tracker.py`) but not the end-to-end scheduler loop.
- Files: `scheduler/collector.py` lines 253-408
- Risk: Changes to the scheduler could break notification delivery without being caught by tests.
- Priority: Medium

**No tests for watchlist/leads refresh Kufar integration:**
- What's not tested: The refresh endpoints in `api/routers/workflow.py` that query Kufar to check listing availability. These are the most complex API endpoints with multiple Kufar calls and state mutations.
- Files: `api/routers/workflow.py` lines 385-527
- Risk: Kufar API changes could break refresh logic silently.
- Priority: Medium

**No load/stress testing for concurrent Kufar API access:**
- What's not tested: Behavior when multiple users trigger searches simultaneously, exceeding the semaphore or rate limit.
- Files: `api/services/kufar_client.py`
- Risk: Connection pool exhaustion, request queuing, or cascading timeouts under load.
- Priority: Low

**No tests for CSS `[hidden]` override consistency:**
- What's not tested: That all elements using `hidden` attribute have corresponding `[hidden]` CSS overrides in `style.css`.
- Files: `frontend/css/style.css`
- Risk: New elements with `hidden` attribute in flex/grid containers may not hide correctly.
- Priority: Low

---

*Concerns audit: 2026-04-22*
