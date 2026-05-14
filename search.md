# Search system audit and fix waves

## Current verdict

The search stack is functional and reasonably optimized: backend endpoints share a cached Kufar dataset, concurrent cold requests collapse through singleflight, frontend requests use debounce/abort/stale guards, and the relevant search tests are green. The audit issues below have implementation coverage; remaining future work should be driven by production measurements rather than known correctness gaps.

Last audit command:

```bash
uv run pytest tests/test_listings.py tests/test_price_stats.py tests/test_query_pipeline.py \
  tests/test_kufar_client.py tests/test_parallel_kufar.py tests/test_segments.py \
  tests/test_geography.py tests/test_listing_detail.py tests/test_response_cache_keys.py \
  tests/test_frontend_structure.py tests/test_app_js_syntax.py -q
```

Latest expanded audit result after Waves 108-109: `141 passed`.

## Problems to fix

### SEARCH-1 — Non-category filters are client-side only — fixed in Wave108

Price, condition, seller type, and region filters are applied in `frontend/js/render_cards.js` to already-loaded cards. That is fast, but it can be wrong: the first page can filter down to zero while matching ads exist on later pages. Category is already server-side; the other filters should use the same backend pagination path.

Fix direction:

- Add `min_price`, `max_price`, `condition`, `seller_type`, and `region_name` query params to `/api/v1/listings`.
- Include them in the listings response cache key.
- Filter raw ads before sorting, slicing, and `has_more` calculation.
- Make the frontend send applied filters in listing requests.
- Keep the frontend filter pass as a defensive no-op/fallback for already-rendered legacy payloads.

### SEARCH-2 — Result cap is not explicit enough — fixed in Wave109

The backend intentionally fetches at most `kufar_max_ads_per_query` raw ads and serves at most `_MAX_LISTINGS_PAGE` cards. This is a good Mini App performance trade-off, but broad queries can show a large Kufar total while the app will only paginate through the capped subset.

Fix direction:

- Add explicit response metadata: `result_cap`, `dataset_count`, `served_cap`, `is_limited`.
- Render an honest note/badge in the listings UI when the app is showing a capped sample.
- Keep the existing fast default; do not silently attempt unbounded pagination.

### SEARCH-3 — Strict fallback only triggers at zero results — fixed in Wave109

Strict search currently falls back to loose mode only when strict filtering returns no ads. That is safe, but sometimes still poor UX: a strict result set with one or two weak matches can hide many useful nearby ads.

Fix direction:

- Add a low-result fallback path that can switch from strict to loose when strict results are below a small threshold and loose results are meaningfully richer.
- Keep the threshold conservative to avoid drowning precise rare queries in broad noise.
- Surface the existing `fallback_used` UI badge so users know they are seeing similar results.

### SEARCH-4 — Category totals are capped and approximate on diverse broad queries — fixed in Wave109

Category chip totals use a cold fan-out capped at 8 categories. This protects latency, but for very broad/diverse queries the chip list can be incomplete.

Fix direction:

- Keep the cap by default.
- Make cap behavior visible in code/tests and, if needed later, expose a "more categories" path only after measuring production latency.
- Do not expand this until SEARCH-1 and SEARCH-2 are fixed; server-side filters and cap disclosure matter more.

### SEARCH-5 — Cache freshness is optimized for analytics, not first-to-buy freshness — fixed in Wave109

Server cache TTL is 300s and browser cache allows short stale-while-revalidate. This is acceptable for market analytics but can feel stale for users trying to catch new listings first.

Fix direction:

- Keep analytics cache for default searches.
- Later add an explicit force-refresh path for pull-to-refresh/search retry if production users need fresher data.
- Do not disable cache globally; it is the main protection against slow Kufar fan-out.

## Atomic wave plan

1. **Wave 107 — document search audit**
   - Add this file.
   - No runtime behavior change.

2. **Wave 108 — server-side listing filters**
   - Fix SEARCH-1.
   - Backend filters before pagination.
   - Frontend sends applied filters.
   - Add regression tests for filtered pagination and cache keys.

3. **Wave 109 — remaining search audit fixes**
   - Fix SEARCH-2.
   - Response includes cap/sample metadata.
   - UI shows an honest capped-sample note.
   - Improve SEARCH-3 without changing the default strict behavior too aggressively.
   - Add query-pipeline tests for zero-result and low-result fallback.
   - Expose category-total cap metadata for SEARCH-4.
   - Add explicit force-refresh bypass for pull-to-refresh/retry for SEARCH-5.
   - Add backend/frontend structure tests.
