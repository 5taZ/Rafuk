# Small Issues — pending after the May-2026 audit

Continuation of `logicissues.md` for the LOW / INFO findings that were
**not** closed in waves 1–6 (commits `b9c86cf` → `10ca670`). The
critical/MEDIUM set is done; everything below is polish, dead-code
removal, observability touch-ups, or feature gaps that need a product
call before any code change.

* Pytest baseline at hand-off: **933 passed, 1 skipped**.
* Ruff: clean.
* Branch: `bad-app`. Latest commit: `10ca670 fix: E-FIND-02/04/05/08 …`.

## Convention

Same as `logicissues.md` — each fix should land as one atomic
commit, message body listing every finding ID it closes, audit-ID
inline comments where the change is non-obvious. Tests must stay
green; ruff stays clean.

---

## Wave 7 — Contract polish + currency fallback (4 findings)

Theme: small contract papercuts and the EUR/RUB fallback. All four
land in one wave; estimated 30–60 min of work.

### A-3 [LOW] · `get_trackers` lacks pagination

* **File:** `api/routers/trackers.py:95-150`
* **Evidence:** Endpoint returns all active trackers + per-tracker
  stats with no `limit`/`offset`. Bounded today by
  `max_trackers_per_user=50`, but inconsistent with every other list
  endpoint in the codebase.
* **Patch:**
  ```python
  limit: int = Query(default=50, ge=1, le=100),
  offset: int = Query(default=0, ge=0),
  ```
  Apply to the `select(Tracker)` query, return only the requested
  page. Update `TrackerEventStats` join accordingly.
* **Tests:** add a case in `tests/test_trackers_api.py` that creates
  3 trackers, requests `?limit=2&offset=1`, and asserts only the
  middle one comes back.

### A-4 [LOW] · `_serialize_watchlist` passes `user_id` to `WatchlistRead`

* **File:** `api/routers/workflow.py:207`
* **Evidence:** `WatchlistRead(user_id=item.user_id, ...)`. Pydantic
  v2 silently drops extras today; would break under
  `extra='forbid'`.
* **Patch:** drop the `user_id=` kwarg from the constructor call.
* **Tests:** none required — it's removing a no-op field.

### A-6 [LOW] · `ConsentGrantRequest.consent_type` unvalidated at schema

* **File:** `api/schemas.py:612`
* **Evidence:** `consent_type: str` — handler validates against
  `VALID_CONSENT_TYPES` but OpenAPI/422 doesn't reflect this.
* **Patch:**
  ```python
  consent_type: Literal["ai_analysis", "pd_processing", "cross_border"]
  ```
  (or a `StrEnum`).
* **Tests:** ensure existing
  `test_grant_consent_rejects_invalid_type` still returns 422 (it
  should — Pydantic catches it now instead of the handler).

### B-07 [LOW → MEDIUM in audit, demoted to LOW for here] · Currency fallback only covers USD

* **File:** `api/services/currency_service.py:52-53`
* **Evidence:** `rates = {"USD": DEFAULT_USD_RATE}`. EUR/RUB get
  `None` from `convert_from_byn`, but the response still labels the
  amount `"EUR"`/`"RUB"`.
* **Patch:** Two options:
  1. Add fallback rates: `{"USD": 3.0, "EUR": 3.3, "RUB": 0.033}`.
     Caveat: ranges drift quickly; document as approximate.
  2. Cleaner — when the rate is missing in fallback mode, return
     `None`/omit the converted figure rather than mislabel BYN as
     EUR. Choose based on whether the frontend can render
     "конвертация недоступна" gracefully.
* **Tests:** add a case in `tests/test_currency_service.py` that
  forces NBRB-down and asserts EUR/RUB neither inherit the BYN
  number nor crash.

---

## Wave 8 — Observability, dead-code, feature gaps (~12 findings)

Theme: documentation, dead-code removal, small feature wins,
deferred semantic fixes. Estimated 1.5–2 hours total.

### B-05 / D-3 [LOW] · `/geography` `share_percent` does not sum to 100

* **File:** `api/routers/geography.py:82,95`
* **Evidence:** `total_analyzed = max(1, dataset.price_stats.count)`
  is post-global-outlier; per-region counts are post-per-region-outlier.
  Plus ads with no `region_id` skip grouping.
* **Patch:** change denominator to
  `total_for_share = sum(s.count for s in region_stats_list)` for
  the percent calc. Either keep `total_analyzed` for the total
  display field, or document the gap inline.
* **Tests:** parametrise `tests/test_geography.py` with a dataset
  that triggers per-region outlier removal differently from global,
  assert sum of `share_percent` ≈ 100 ± rounding.

### B-06 [LOW] · `QuerySnapshot` does not store q1/q3

* **File:** `api/models.py:214-216`, `api/services/history_service.py:108-112`
* **Evidence:** Snapshot persists only `mean/median/min/max`; q1/q3
  computed by `compute_price_stats` are discarded.
* **Patch:** new migration adding `q1_byn`/`q3_byn` columns
  (`Numeric(12,2)`, nullable, no backfill — table is rolling).
  Update `upsert_query_snapshot` to write them, expose in
  `PriceHistoryPoint` schema. Consumer-side: `/price-history`
  endpoint can render the fair-price band.
* **Tests:** add to `tests/test_history_service.py`,
  `tests/test_price_history.py`.

### B-08 [LOW] · `_normalize_response_ads` heuristic edge case

* **File:** `api/services/query_pipeline.py:107-112`
* **Evidence:** Heuristic flags `all divisible by 100 AND any > 1000`
  as kopecks. A test fixture with BYN-prices `[1200, 1500, 2000]`
  would be misclassified. Production responses always include
  `price_usd` so the earlier check short-circuits — test-only.
* **Patch:** Document the constraint in the docstring; in test
  fixtures set `price_usd` explicitly.
* **Tests:** none new — the constraint is a docstring update.

### B-09 [LOW] · `compute_price_vs_median` returns 0.0 for negotiable

* **File:** `api/services/aggregator.py:709-710`
* **Evidence:** `if price_byn is None: return 0.0`. Semantically
  `0.0` means "equal to median", but `extract_prices` excludes
  negotiable from the dataset, so `compute_price_vs_median` lands
  in this branch only for "this ad is negotiable". Six callers
  across four files — see `logicissues.md` Wave 1 — silently treat
  negotiable as on-market.
* **Patch:** change return type to `float | None`. Update callers:
  * `aggregator.filter_deal_ads:843` → `if delta is None or delta >= 0: continue`
  * `aggregator.sort_listings:` "cheap" path — bucket-keyed already
    after Wave 1, just propagate `None` through the decorate step.
  * `scheduler/collector.py:624` — `if delta_pct is None: discount_pct = 0.0`
  * `scheduler/collector.py:957, 1009` — already guard `delta < 0` (pre-audit)
  * `reseller_tools.compute_deal_score:282` — when `delta is None`
    use `verdict = "Цена договорная"` and skip score adjustment.
  * `reseller_tools.matches_tracker_filters:388` — `delta is None
    → return False` (negotiable cannot match a discount filter).
  * `listing_mapper._price_context:123, 130` — propagate `None` to
    the card so the UI shows "—" instead of "0%".
* **Tests:** rewrite the existing
  `tests/test_aggregator.py::test_negotiable_has_zero_delta_not_minus_100`
  to assert `is None`. Add coverage for each call site.

### B-10 [LOW] · `analyzed_count` vs `total_results` semantic mismatch

* **File:** `api/services/history_service.py:109-110`,
  `api/schemas.py` `PriceHistoryPoint`
* **Evidence:** `total_results` is Kufar's API total, `analyzed_count`
  is post-outlier sample. Both shipped to frontend without
  clarification.
* **Patch:** add `fetched_count` field (raw pre-outlier sample) and
  document the three numbers in the schema docstring. Frontend can
  then show "X analysed of Y fetched (Kufar reports Z total)".
* **Tests:** schema-level + one snapshot creation test.

### C-03 [LOW] · README mentions restock detection that is not implemented

* **File:** `README.md` "Detects market signals" bullet
* **Evidence:** README lists "restock spikes"; collector marks ads
  `active=True` on reappearance but emits no event.
* **Patch (recommended):** remove the "restock spikes" mention
  from README. Implementing the feature is a separate, larger ask
  (needs a new `event_type='restock'`, CHECK migration, message
  template, frontend filter chip). Track as a backlog item.
* **Tests:** none — README change.

### C-09 [LOW] · `Tracker.exclude_duplicates` is dead

* **File:** `api/models.py` Tracker, SavedSearch
* **Evidence:** Field exists; never read in collector,
  reseller_tools, or query_pipeline.
* **Patch:** add an inline comment marking the field as unused,
  pointing at the (deferred) duplicate-detection feature. Removing
  the column requires a destructive migration and is not worth it.
* **Tests:** none — comment-only.

### C-10 [LOW] · `_MAX_EVENTS_PER_TYPE=10` vs notification cap=3

* **File:** `scheduler/collector.py`
* **Evidence:** Events table stores 10 per type per tick; tracker
  Telegram messages cap at 3. UI may show 10 in feed but user got 3.
* **Patch:** add a code comment near both caps stating the
  rationale (events = audit trail, notifications = UX) and
  cross-referencing each other. The pre-audit batch already added
  an INFO log for cap-overflow; this is the static documentation
  side.
* **Tests:** none — comment-only.

### E-FIND-01 [LOW] · No projected ROI from `target_resale_byn`

* **File:** `api/routers/workflow.py`, `api/routers/analytics.py`
* **Evidence:** `actual_profit` requires `sold_price_byn`. There is
  no `projected_profit_byn` for watching/bought leads using
  `target_resale_byn`.
* **Patch:** add to `_serialize_lead_read` (introduced in Wave 6):
  ```python
  if lead.sold_price_byn is None and lead.target_resale_byn:
      basis = (lead.buy_price_byn or 0) + total_expenses
      out.projected_profit_byn = round(float(lead.target_resale_byn) - basis, 2)
  ```
  Add `projected_profit_byn: float | None = None` to `LeadRead`.
  Mirror in dashboard aggregate.
* **Tests:** add to `tests/test_workflow_api.py` + `test_analytics_api.py`.

### E-FIND-03 [LOW] · `risk_detector` ignores zero-photo signal

* **File:** `api/services/risk_detector.py`
* **Evidence:** Photo count not in `detect_risks`; only the
  liquidity scorer uses it.
* **Patch:** new `_check_no_photos` helper:
  ```python
  if not (ad.get("images") or ad.get("image_count")):
      risks.append({"type": "no_photos", "level": "medium",
                    "message": "Объявление без фото"})
  ```
  Hook into `detect_risks` after the existing checks.
* **Tests:** add to `tests/test_risk_detector.py` — both ad with
  photos and without.

### E-FIND-06 [LOW] · `watching → bought` blocked

* **File:** `api/routers/workflow.py` `_VALID_LEAD_STATUS_TRANSITIONS`
* **Evidence:** Watching cannot promote directly to bought; user
  must transit reviewing/in_progress first.
* **Patch (decision: allow):** add `"bought"` to the allowed-set
  for `"watching"`. Wave 6 already stamps `bought_at` on the
  transition, so the timestamp lands correctly.
* **Tests:** there is currently a test that asserts the transition
  IS blocked (`test_lead_status_state_machine_blocks_invalid_transitions`).
  Remove the watching→bought assertion (keep watching→sold blocked
  — sold needs an explicit purchase price first).

### E-FIND-07 [LOW] · Fallback resale below purchase

* **File:** `api/services/ai_marketplace.py` `_fallback_resale_potential`
* **Evidence:** When `fast_price >= purchase`, the fallback recomputes
  `fast_price = purchase * 0.92`, advertising a loss when the market
  may genuinely sit above purchase.
* **Patch:** floor `fast_price` at `max(purchase, computed_fast)` so
  the fallback never claims a loss when the data does not support it.
* **Tests:** unit test with purchase well below market median; assert
  the fast tier is ≥ purchase.

### E-FIND-09 [LOW] · `buy_price=NULL` with `sold` inflates ROI

* **File:** `api/routers/analytics.py`, `api/routers/export.py`
* **Evidence:** `buy_price = lead.buy_price_byn or 0.0`; if user
  sold without recording purchase price, `actual_profit = sold_price
  − expenses` and ROI is reported as if free goods.
* **Patch:** when `buy_price_byn is None AND sold_price_byn is not None`:
  * return `actual_profit=None`, `roi_percent=None`,
  * set `incomplete_cost_basis: bool = True` on `LeadRead` so frontend
    can flag.
  * Skip the lead from average ROI / total profit aggregates.
* **Tests:** create a lead with `sold_price_byn=1000`,
  `buy_price_byn=None`; assert `actual_profit is None`,
  `incomplete_cost_basis is True`.

---

## Deferred from `logicissues.md` (NOT in Wave 7/8)

These were marked deferred earlier and are intentionally left alone
unless a product call upgrades them:

* **A-5** `/ai/price-advice` cache shared across users — needs explicit
  documentation that it's intentional, possibly a TTL adjustment.
* **A-7** `delete_all_*` race window — DB CASCADE handles the orphan
  case, the explicit pre-deletes are belt-and-braces.
* **A-8** `/health/ready` exposes rate-limiter state — load-balancer
  probes need this; if concerned, lock down via nginx.
* **A-9** `image_proxy` path-depth not capped — Kufar CDN bounds it
  functionally; add a `len(path) > 256` guard if a future incident
  warrants it.
* **C-01** `TrackerEvent` duplicated across query groups — needs
  a redesign of the event-table primary identity (`(user_id, ad_id,
  event_type)` plus per-tracker fan-out via a join table).
* **C-02** No DB UNIQUE on `tracker_events` — code dedup window is
  24h; a UNIQUE here would block legitimate re-emission past 24h.
* **C-06** Partial Kufar response detection — the
  "completely empty" guard is in place; flagging "5 of expected 50"
  needs heuristics that risk false positives.
* **D-4** `region_label` may surface district name as region —
  Kufar's `region_name` presence is high in practice; touching the
  fallback risks regressing the genuinely-no-region_name case.
* **D-5..D-12** INFO findings from Track D — graceful fallbacks,
  category-data behaviour, listing-mapper edge cases. Currently
  correct; documented for future reference.

---

## Suggested wave order if reopening in a new session

1. **Wave 7** (single commit, 4 findings) — A-3, A-4, A-6, B-07.
   Tight scope, easy regressions, leaves the contract surface
   coherent.
2. **Wave 8a — observability** (one commit) — B-05/D-3, B-06,
   B-08, B-10, C-09, C-10. Doc + small migration. ~30 min.
3. **Wave 8b — semantic** (one commit) — B-09. Cross-cutting; do
   alone so the diff stays reviewable. ~45 min.
4. **Wave 8c — risk + ROI** (one commit) — E-FIND-01, E-FIND-03,
   E-FIND-09. Backend semantics + new schema fields. ~45 min.
5. **Wave 8d — UX** (one commit) — E-FIND-06 (allow watching →
   bought), E-FIND-07 (resale clamp), C-03 (README). ~20 min.

Each wave: change → tests → ruff → pytest → commit, same as Waves
1–6. Re-read `AGENTS.md` if anything is unclear about the conventions.
