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

### SEARCH-10 — Empty result hides the entire listings UI — fixed in Wave 118

**Severity: high — bricks the recovery path the user reported.**

When a search produced zero results, `renderListings` set
`elements.listingsSection.hidden = !hasContent && !hasData` —
both halves were false (no items, total=0), so the whole
`#listings-section` collapsed: filter button gone, total badge
gone, SEARCH-7 refresh button gone, **SEARCH-9 "Снять фильтры"
CTA we built in Wave 117 was rendered into a hidden parent and
the user never saw it.** All they got was a "white tail" below
the summary strip and zeros in the stats grid.

The overview side compounded the confusion: the summary's signal
text fell through to "Выборка маленькая, смотрите объявления и
сравнивайте вручную" because `analyzedCount < 5` matched a 0-row
sample, implying analytics still applied to something.

**Fix shipped (Wave 118)**:

* `renderListings` keeps `#listings-section` visible whenever a
  query is active, regardless of `hasContent`/`hasData`. The
  empty-state node carries the filter button by virtue of being
  inside the section header (header isn't conditionally rendered),
  so the user can always reach the filter dropdown.
* `renderListingsCollection` now composes the empty-state hint
  from a query-aware primary line ("По запросу «iPhone fjksldj»
  ничего не найдено") plus a filter-aware second sentence
  ("Возможно, фильтры слишком узкие — попробуйте снять часть"
  vs. "Попробуйте изменить запрос или сделать его короче"). The
  SEARCH-9 "Снять фильтры" CTA is preserved; we just stopped
  hiding the parent it lives in.
* The total badge falls back to "0 объявлений" instead of a bare
  "0" pill so the unit stays consistent with the populated case.
* `renderSummary` adds an explicit `totalResults === 0 &&
  analyzedCount === 0` branch with copy that points to the
  «Объявления» tab, so overview readers don't get misled by the
  "small sample" hint into thinking the analytics still apply.

### SEARCH-9 — Filter-narrowed empty state has no escape hatch — fixed in Wave 117

**Severity: low — diagnostic / UX improvement.**

When the filter Apply button kicks off `loadListings(true)` and the
backend returns an empty slice (which after SEARCH-6's fix is rare
but not impossible — e.g. extremely tight ranges), the user used to
see only the generic "Ничего не найдено / Попробуйте изменить
запрос или снять фильтры" empty state. They had to find the filter
dropdown, open it, and clear each field by hand to escape.

Wave 117 wires `actions.clearListingFilters` so the listings empty
state shows a "Снять фильтры" CTA whenever any of category /
condition / seller / price range / region is applied. Click drops
every applied filter (current + pending) and re-runs the broad
search via `search(activeView, { keepFilters: true })`. The CTA
disappears as soon as filters are off, so the unfiltered "0
results" case still reads as "wrong query" rather than "wrong
filters".

We deliberately do **not** auto-clear filters — preserving user
intent matters more than always showing some result.

## Atomic wave plan (delivered)

| Wave | Audit ID | Done |
|------|----------|------|
| 113  | (audit doc) | docs(wave113): record search filter problems audit |
| 114  | SEARCH-6 | fix(wave114): convert price filter to kopecks before Kufar call |
| 115  | SEARCH-8 | fix(wave115): keep client-side price filter pinned to BYN |
| 116  | SEARCH-7 | fix(wave116): surface force-refresh on totals to escape 5-min cache lag |
| 117  | SEARCH-9 | fix(wave117): offer "Снять фильтры" CTA from filter-narrowed empty state |
| 118  | SEARCH-10 | fix(wave118): keep listings section + filter UI visible on zero-result queries |

Each wave: green `uv run pytest --tb=short -q`, atomic commit with
the audit IDs in the body, no formatter run (per AGENTS.md).
