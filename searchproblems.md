# Search problems audit (Wave 113+)

This file enumerates the search/filter defects reported on `bad-app`
and the atomic waves that fix them. Companion document to `search.md`
(which covered Waves 107-112). Each defect lists the reproduction
command and the planned fix direction so future contributors can
verify the problem returns no longer.

## TL;DR for the impatient

The big one is **SEARCH-6**: Kufar's `prc=` query parameter is
expressed in *kopecks* (1 BYN = 100 kopecks), but
`api/services/kufar_filters.py::kufar_price_range` sends the value
the user typed (BYN) verbatim. So "max price = 2500 BYN" actually
asks Kufar for "max price = 25 BYN", which on a focused search like
`iPhone 14 Pro` + category `17010 (Мобильные телефоны)` returns 0
ads → the listings panel shows the empty state.

The other discrepancies (3166 vs 3169, 2235 vs 2238) are within the
~3-ad noise floor that comes from the 5-minute analytics cache plus
Kufar's real-time inventory churn. They're not freshness bugs — they
are the agreed cache TTL doing its job. We surface a force-refresh
path so the user can opt in to a fresh count when it matters.

## Reproduction baseline

API + bot are running locally on `127.0.0.1:8010` with
`AUTH_BYPASS=true`. Every diagnostic curl below uses
`--noproxy 127.0.0.1` because the host has an upstream proxy that
won't forward to localhost.

```bash
# Broad phone query, no filters
curl -s --noproxy 127.0.0.1 \
  "http://127.0.0.1:8010/api/v1/listings?query=iPhone+14+Pro&strict_search=true&limit=5"
# total=3167 dataset_count=135 (strict-filtered subset of 200 fetched ads)

# Phone category chip
curl -s --noproxy 127.0.0.1 \
  "http://127.0.0.1:8010/api/v1/price-stats?query=iPhone+14+Pro&strict_search=false"
# Мобильные телефоны count=2236
```

Kufar website at the same instant: 3169 / 2238. Three-ad gap on each
side — see SEARCH-7.

## Problems

### SEARCH-6 — Price filter sends BYN to Kufar's `prc=` param which expects kopecks

**Severity: critical — this is the "цена до" returns 0 results bug.**

Kufar's pricing across the API is consistent: ad responses report
`price_byn` as an integer in kopecks (so 1500 BYN ⇒ 150_000), and the
`prc` query parameter is interpreted with the same scale. But
`kufar_price_range`:

```python
def kufar_price_range(min_price, max_price):
    ...
    lower = max(0, math.floor(min_price)) if min_price is not None else 0
    upper = (
        max(0, math.ceil(max_price))
        if max_price is not None
        else KUFAR_OPEN_PRICE_MAX_BYN
    )
    return f"r:{lower},{upper}"
```

…sends the user-supplied BYN value verbatim. `max_price=2500` →
`prc=r:0,2500` → Kufar reads "0..25 BYN" → mostly cases and chargers,
zero phones. With a category filter active this drops to 0 results.

Direct Kufar verification:

```bash
# Kufar reads `prc=r:0,2500` as "0..25 BYN" (2500 kopecks = 25 BYN)
curl -s "https://api.kufar.by/search-api/v2/search/rendered-paginated?\
query=iPhone+14+Pro&size=3&cur=BYN&sort=lst.d&cat=17010&prc=r%3A0%2C2500"
# total=5 — three accessory ads under 25 BYN

# Same with kopecks (correct: 2500 BYN = 250000 kopecks)
curl -s "https://api.kufar.by/search-api/v2/search/rendered-paginated?\
query=iPhone+14+Pro&size=3&cur=BYN&sort=lst.d&cat=17010&prc=r%3A0%2C250000"
# total=1608 — actual phones under 2500 BYN
```

Through our API:

```bash
curl -s --noproxy 127.0.0.1 \
  "http://127.0.0.1:8010/api/v1/listings?query=iPhone+14+Pro&\
strict_search=true&max_price=2500&category=17010&limit=5"
# total=0 returned=0 dataset_count=0 — the failure the user saw
```

Once min/max price are converted to kopecks before being placed in
`prc`, the same query returns the expected ~1600 results, the strict
filter narrows it to a few hundred precise iPhone 14 Pro hits, and
the listings panel renders correctly.

Note that:

* The `KUFAR_OPEN_PRICE_MAX_BYN = 999_999_999` sentinel only "worked"
  by accident — Kufar reads it as 9,999,999.99 BYN, which is well
  above any realistic listing. After we convert user input to kopecks,
  we keep the same numeric sentinel (in kopecks) so the open-ended
  upper bound still acts as "no limit". The constant deserves a less
  misleading name (`KUFAR_OPEN_PRICE_MAX_KOPECKS`).
* `_matches_price` in `api/routers/listings.py` is correct already:
  it compares the user's BYN value against the BYN we get from
  `normalize_price_byn` (which divides the kopeck-encoded `price_byn`
  by 100). It's only the upstream Kufar query that is wrong.
* `tests/test_kufar_filters.py`, `tests/test_listings.py` and
  `tests/test_kufar_client.py` currently *codify* the buggy
  behaviour by asserting `r:500,1000` etc. They have to be updated
  in the same wave as the fix.

**Fix wave**: SEARCH-6 — Wave 113. Multiply `min_price`/`max_price`
by 100 inside `kufar_price_range` (and update the open-max sentinel
+ tests). No frontend or storage change needed.

### SEARCH-7 — App total can lag Kufar's website by a few ads

**Severity: minor / expected.**

User saw `iPhone 14 Pro` total = 3166 in the app vs. 3169 on
kufar.by, and the `Мобильные телефоны` chip = 2235 vs. 2238. The gap
is consistent at ~3 ads in both places.

Root cause:

* `price-stats` and `listings` cache responses for
  `cache_ttl_seconds = 300` (5 minutes). The chip totals fan-out
  (`fetch_category_totals`) cache also lives for 300 s. So a freshly
  loaded screen can be up to 5 minutes behind Kufar.
* Kufar's listing inventory churns by several ads/minute on a popular
  query like `iPhone 14 Pro` (sold, taken down, expired, edited).
* SEARCH-5 already added a `force_refresh` query parameter to the
  listings/price-stats path; we just don't expose a user-visible way
  to trigger it on the search results screen.

This isn't a correctness bug in the strict sense: the cached totals
were exactly what Kufar said *at the time the cache was populated*.
But the user can't tell the difference between "stale cache" and
"phantom missing 3 ads", so we should surface a refresh affordance.

**Fix direction (Wave 114, SEARCH-7)**:

* Add a small "Обновить" affordance next to the totals chip that
  triggers `search(target, { forceRefresh: true })`. The plumbing
  already exists end-to-end; only the button + binding is missing.
* Don't auto-refresh — that would defeat SEARCH-5's analytics cache
  protection. Refresh stays user-initiated.
* Don't display a "stale" warning — the deltas are tiny and doing so
  would draw attention to noise.

### SEARCH-8 — Client-side price filter compares user's BYN input to displayed-currency price

**Severity: medium — wrong filter results when currency ≠ BYN.**

`frontend/js/render_cards.js::matchesFilters`:

```javascript
const itemPrice = item.price != null ? Number(item.price) : null;
...
if (state.filters.minPrice != null && itemPrice != null && itemPrice < state.filters.minPrice) return false;
if (state.filters.maxPrice != null && itemPrice != null && itemPrice > state.filters.maxPrice) return false;
```

The filter inputs are labelled "Цена, BYN" so `state.filters.minPrice`/
`maxPrice` are always BYN, but `item.price` is in `state.misc.currency`
— the user's display currency, which can be USD/EUR/RUB. With the
default BYN currency this happens to agree with the filter, so the bug
is invisible until someone switches currency.

Today the only things that change `state.misc.currency` are tests
fixtures, but the field is wired everywhere (`buildCommonQuery` sends
`currency=` to every endpoint). It's a footgun waiting for a future
currency selector.

**Fix direction (Wave 115, SEARCH-8)**:

* The backend now applies the price filter authoritatively (Wave 108
  in `_filter_visible_ads` + Wave 113 sending it to Kufar correctly).
  The frontend pass is meant to be a defensive no-op for already-
  loaded payloads (per `search.md`).
* Compare against `item.price_byn` (always BYN) instead of `item.price`
  so the no-op truly is a no-op regardless of display currency.
* Keep `hasPriceRange && itemPrice == null → false` rule so
  "Договорная" (price=null) ads still drop when a price range is set.

### SEARCH-9 — Filter dropdown commit doesn't surface backend errors

**Severity: low — diagnostic improvement.**

When the filter Apply button kicks off `loadListings(true)` and the
backend returns an empty slice (which after SEARCH-6's fix is rare
but not impossible — e.g. extremely tight ranges), the user sees the
generic "Ничего не найдено / Попробуйте изменить запрос или снять
фильтры" empty state. There's no signal whether the empty came from
a backend filter, a dataset cap, or a strict-mode mismatch.

This is a cosmetic improvement, not a correctness fix; documenting
here so the wave plan picks it up after the higher-priority fixes
land.

**Fix direction (later)**:

* When `state.listings.total === 0` and any of the listing filters
  (price/condition/seller/region/category) is active, show an inline
  "Снять фильтры" link in the empty state.
* Don't auto-clear filters — preserving user intent matters more
  than always showing some result.

## Atomic wave plan

1. **Wave 113 — SEARCH-6 — kopecks fix.**
   Convert min/max to kopecks inside `kufar_price_range`; rename the
   open-max constant to reflect the unit; update the three test files
   that asserted the BYN-as-kopecks shape. Add a regression test that
   verifies `r:0,2500` is no longer produced for `max_price=2500`.

2. **Wave 114 — SEARCH-8 — client-side filter currency safety.**
   Switch the defensive client-side price filter to use
   `item.price_byn`, drop the obsolete BYN-vs-display-currency
   comparison. Add a frontend syntax/structure regression test if
   feasible.

3. **Wave 115 — SEARCH-7 — surface force-refresh on the totals chip.**
   Add a small refresh affordance + binding that calls
   `search(state.ui.activeView, { forceRefresh: true })`. No backend
   change. Add a frontend structure test that the new control exists.

4. **Wave 116 — SEARCH-9 — empty-state diagnostics.**
   Inline "Снять фильтры" CTA when filters are active and backend
   returned 0. Pure UX, no schema change.

Each wave: green `uv run pytest --tb=short -q`, atomic commit with
the audit IDs in the body, no formatter run (per AGENTS.md).
