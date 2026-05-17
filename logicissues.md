# Logic Audit — 2026-05-17

Single source of truth for the May-2026 deep logic audit of Kufar
Analytics. Findings come from a five-track parallel pass:

* Track A — endpoints, contracts, IDOR
* Track B — metric math (median, mean, q1/q3, currency)
* Track C — scheduler, autotrack, dedup, DLQ
* Track D — categories, filters, segmentation
* Track E — liquidity, deal pipeline, marketplace AI

Severity tally on first pass: **0 HIGH · 16 MEDIUM · 26 LOW · 8 INFO**.
All findings are catalogued below with file:line, evidence, root cause,
proposed patch, and the wave that closes them.

Each wave is one atomic commit. Pytest stays green and `ruff check .`
clean before each commit per AGENTS.md. Audit IDs are referenced inline
in code where the fix is non-obvious.

---

## Wave plan

| Wave | Closes | Theme |
|------|--------|-------|
| 1 | B-01, B-02, B-03, B-04, B-09 | Metric consistency for free vs negotiable listings |
| 2 | D-1, D-2 | Tracker-matching condition/region normalization |
| 3 | A-1, A-2 | `update_lead`/`update_expense` PATCH contract |
| 4 | C-04, C-07 | Scheduler hardening — DLQ retry constant + auto-pause on persistent errors |
| 5 | C-08 | Scheduler respects `revoke_consent` |
| 6 | E-FIND-02, E-FIND-04, E-FIND-05, E-FIND-08 | Liquidity correctness (bought_at, dup-detection, price_drop %, flip+expenses) |
| 7 | A-3, A-4, A-6, B-07 | API contract polish + currency fallback |
| 8 | B-05, B-06, B-08, B-10, C-03, C-09, C-10, D-3, E-FIND-01, E-FIND-03, E-FIND-06, E-FIND-07, E-FIND-09 | Documentation, dead-code removal, observability touchups |

INFO-class findings (D-5..D-12, A-5, A-7, A-8, A-9) are documented but
**deferred** unless they are upgraded to a real bug — see the
"Deferred" section at the end.

---

## Track A — Endpoints

### A-1 [MEDIUM] · `update_lead` ignores `notes`

* **File:** `api/routers/workflow.py:361-403`
* **Evidence:** `LeadUpdate` schema declares `notes: str | None`; handler
  iterates `payload.model_fields_set` for `status`, `target_resale_byn`,
  `buy_price_byn`, `sold_price_byn` but never `notes`.
* **Why:** Schema/handler contract mismatch — frontend can send `notes`,
  the API silently drops it.
* **Patch:** Add `if "notes" in payload.model_fields_set: lead.notes = payload.notes`.
* **Wave:** 3

### A-2 [MEDIUM] · `update_expense` cannot null fields

* **File:** `api/routers/expenses.py:125`
* **Evidence:** `if payload.notes is not None: expense.notes = payload.notes`.
  Same `is not None` guard for `expense_date`.
* **Why:** A client sending `{"notes": null}` cannot clear notes —
  `None` skips the assignment. Should be `model_fields_set` like
  `update_lead` does for the working fields.
* **Patch:** Switch `notes` and `expense_date` to `model_fields_set`.
  `amount_byn` keeps the `is not None` guard because Pydantic enforces
  `gt=0` and clearing it would violate the DB CHECK.
* **Wave:** 3

### A-3 [MEDIUM] · `get_trackers` lacks pagination

* **File:** `api/routers/trackers.py:95-150`
* **Evidence:** Endpoint returns all active trackers + per-tracker stats
  with no `limit`/`offset`. Inconsistent with every other list endpoint
  in the codebase.
* **Why:** Bounded today by `max_trackers_per_user=50`, but if the limit
  is ever raised the enrichment loop runs unbounded.
* **Patch:** Add `limit: int = Query(default=50, ge=1, le=100)` and
  `offset: int = Query(default=0, ge=0)`.
* **Wave:** 7

### A-4 [LOW] · `_serialize_watchlist` passes `user_id` to `WatchlistRead`

* **File:** `api/routers/workflow.py:207`
* **Evidence:** `WatchlistRead(user_id=item.user_id, ...)`. Schema has
  no `user_id` field; Pydantic v2 silently drops extras today, would
  break with `extra='forbid'`.
* **Wave:** 7

### A-5 [LOW] · `/ai/price-advice` cache shared across users

* **File:** `api/routers/ai_tools.py:87-95`
* **Evidence:** Cache key has no `user_id`; comment says "shared across
  users" intentionally. Inconsistent with `/ai/negotiate` which was
  fixed for cross-user data leak (PR-03).
* **Why:** Response is genuinely market-only (no PII), so risk is low,
  but documentation gap.
* **Wave:** Deferred — document intentional sharing if confirmed.

### A-6 [LOW] · `ConsentGrantRequest.consent_type` unvalidated

* **File:** `api/schemas.py:612`
* **Evidence:** `consent_type: str` — handler validates against
  `VALID_CONSENT_TYPES` but OpenAPI/422 doesn't reflect this.
* **Patch:** `consent_type: Literal["ai_analysis", "pd_processing", "cross_border"]`.
* **Wave:** 7

### A-7 [LOW] · `delete_all_*` race with concurrent create

* **File:** `api/routers/workflow.py:410-435, 490-520`
* **Evidence:** Subquery selects IDs, then deletes children +
  parents. Concurrent insert between subquery and delete could orphan.
* **Why:** Mitigated by DB CASCADE; race window is tiny.
* **Wave:** Deferred — rely on CASCADE.

### A-8 [LOW] · Health endpoints leak operational state

* **File:** `api/routers/health.py:18-67`
* **Evidence:** `/health/ready` returns `"rate_limiter": "degraded"`
  unauthenticated.
* **Wave:** Deferred — health endpoints are intentionally probe-friendly.

### A-9 [LOW] · `image_proxy` regex allows deep paths

* **File:** `api/routers/image_proxy.py:48`
* **Evidence:** `^[A-Za-z0-9_\-/]+\.(?:jpe?g|JPE?G)$` — no length cap.
* **Wave:** Deferred — Kufar CDN 404s; functional risk is low.

---

## Track B — Metrics

### B-01 [MEDIUM] · `compute_segments` drops free listings

* **File:** `api/services/aggregator.py:1075`
* **Evidence:** `price_byn = normalize_price_byn(ad.get("price_byn"))`
  — second positional `ad` argument missing.
* **Why:** Without `ad`, the helper cannot detect "free" semantics
  from text and returns `None` for `price_byn=0`. Free listings
  silently disappear from segment counts even though `extract_prices`
  (used by `/price-stats`, `/geography`) includes them.
* **Patch:** Pass `ad`: `normalize_price_byn(ad.get("price_byn"), ad)`.
* **Wave:** 1

### B-02 [LOW] · `filter_ads_for_accessory_category` drops free listings

* **File:** `api/services/aggregator.py:798`
* **Evidence:** `price = normalize_price_byn(ad.get("price_byn"))`.
* **Why:** Same as B-01 — free accessories vanish from filtered list
  even though their price (0) ≤ cap.
* **Patch:** Pass `ad`.
* **Wave:** 1

### B-03 [LOW] · `sort_listings` cannot distinguish free from negotiable

* **File:** `api/services/aggregator.py:872, 881, 885, 891`
* **Evidence:** `normalize_price_byn(ad.get("price_byn")) or 0.0`.
* **Why:** Both free and negotiable collapse to `0.0`; `price_asc`
  surfaces them as "cheapest" indistinguishably. Negotiable listings
  (price unknown) should not sort as cheapest.
* **Patch:** Pass `ad`; map negotiable → `float('inf')` for ascending
  sort, leave free as `0.0`.
* **Wave:** 1

### B-04 [MEDIUM] · IQR-zero outlier fallback excludes free listings

* **File:** `api/services/aggregator.py:670-672`
* **Evidence:**
  ```
  if iqr <= 0:
      med = statistics.median(sorted_prices)
      if med > 0:
          return [p for p in prices if 0.2 * med <= p <= 5.0 * med]
  ```
* **Why:** When IQR=0 with ≥8 listings, the fallback band excludes
  `0.0`; contradicts `extract_prices` which intentionally keeps free
  listings.
* **Patch:** Allow `p == 0.0` to bypass the band.
* **Wave:** 1

### B-05 [LOW] · `/geography` `share_percent` does not sum to 100

* **File:** `api/routers/geography.py:82, 95`
* **Evidence:** `total_analyzed = max(1, dataset.price_stats.count)`
  is post-global-outlier; per-region counts are post-per-region-outlier.
  Plus ads with no `region_id` skip grouping.
* **Patch:** Use `total_for_share = sum(s.count for s in region_stats_list)`
  for the percent denominator, or document the gap.
* **Wave:** 8

### B-06 [LOW] · `QuerySnapshot` does not store q1/q3

* **File:** `api/models.py:214-216`, `api/services/history_service.py:108-112`
* **Evidence:** Snapshot only persists `mean_byn`, `median_byn`,
  `min_byn`, `max_byn`; q1/q3 are computed by `compute_price_stats`
  but discarded.
* **Why:** History endpoint cannot show the "fair-price band" trend
  even though the live endpoint does.
* **Patch:** Add columns + migration; backfill is unnecessary because
  the snapshot table is rolling.
* **Wave:** 8

### B-07 [MEDIUM] · Currency fallback only covers USD

* **File:** `api/services/currency_service.py:52-53`
* **Evidence:** `rates = {"USD": DEFAULT_USD_RATE}`.
* **Why:** When NBRB is down, `convert_from_byn` for EUR/RUB returns
  the BYN amount unchanged but the response still says
  `currency: "EUR"`. User reads BYN values labeled as EUR.
* **Patch:** Add fallback rates for EUR (~3.3) and RUB (~0.033). If
  the rate is missing AND we are in fallback mode, return `None` so
  the response can omit the converted figure.
* **Wave:** 7

### B-08 [LOW] · `_normalize_response_ads` heuristic edge case

* **File:** `api/services/query_pipeline.py:107-112`
* **Evidence:** Heuristic flags `all divisible by 100 AND any > 1000`
  as kopecks. A test fixture with BYN-prices [1200, 1500, 2000] would
  be misclassified.
* **Why:** Production responses always include `price_usd` so the
  earlier check short-circuits. Test-only.
* **Patch:** Document the constraint in docstring; require test
  fixtures to set `price_usd`.
* **Wave:** 8

### B-09 [LOW] · `compute_price_vs_median` returns 0.0 for negotiable

* **File:** `api/services/aggregator.py:709-710`
* **Evidence:** `if price_byn is None: return 0.0`.
* **Why:** Semantically `0.0` means "equal to median". Callers that
  don't separately check `price_type == 'negotiable'` will silently
  treat negotiable as on-market.
* **Patch:** Return `None`; update return type to `float | None` and
  fix the two production callers (`listing_mapper`, `aggregator`).
* **Wave:** 1

### B-10 [LOW] · `analyzed_count` vs `total_results` semantic mismatch

* **File:** `api/services/history_service.py:109-110`
* **Evidence:** `total_results` is Kufar's API total, `analyzed_count`
  is post-outlier sample. Both shipped to frontend without clarification.
* **Patch:** Document; consider adding `fetched_count` for the raw
  pre-outlier size.
* **Wave:** 8

---

## Track C — Scheduler / Autotrack

### C-01 [MEDIUM] · `TrackerEvent` duplicated across query groups

* **File:** `scheduler/collector.py` (`persist_tracker_events`)
* **Evidence:** `seen_by_tracker` is per-tracker; cross-tracker
  duplicates persist as separate event rows.
* **Why:** Notifications are deduped (now via `dedup_key` after the
  pre-audit batch), but the DB still grows duplicated events.
* **Wave:** Deferred — needs broader event-table redesign; cross-tracker
  notifications are already deduped, this only inflates analytics
  storage. Document.

### C-02 [LOW] · No DB unique on `tracker_events`

* **File:** `api/models.py` TrackerEvent
* **Evidence:** Dedup is code-only via `_recent_event_keys` (24h).
* **Wave:** Deferred — code dedup covers the intended window; adding
  a UNIQUE here interferes with legitimate re-emission after 24h.

### C-03 [LOW] · README mentions restock detection that is not implemented

* **File:** `README.md` + `scheduler/collector.py`
* **Evidence:** README lists "restock spikes" as a market signal; the
  collector marks ads `active=True` on reappearance but emits no event.
* **Patch:** Remove the README mention OR add the `event_type='restock'`
  pipeline. README correction first; pipeline is a separate feature.
* **Wave:** 8 (README correction)

### C-04 [MEDIUM] · No auto-pause on persistent Kufar errors

* **File:** `scheduler/collector.py` (`_check_trackers_inner`)
* **Evidence:** Per-query-group `_TRACKER_QUERY_ERRORS` are caught and
  the tick continues; the failing tracker is re-tried each cycle.
  `Tracker.pause_reason` exists in the model but is never set
  automatically.
* **Patch:** Track consecutive query-group failures per tracker; after
  N (default 5) ticks, set `paused=True`, `paused_at=now()`,
  `pause_reason='persistent_kufar_error'`. Reset counter on success.
* **Wave:** 4

### C-05 — false positive (strict_mode is correctly applied upstream)

### C-06 [LOW] · Partial Kufar response not detected as outage

* **File:** `scheduler/collector.py` (`sync_query_listing_states`)
* **Evidence:** Outage guard fires only on completely empty response
  (`if not ads and prior_snapshots_had_ads`).
* **Wave:** Deferred — shape of "partial" response is hard to detect
  reliably; Kufar pagination already enforces 200 cap.

### C-07 [MEDIUM] · `_DLQ_MAX_RETRIES` code/comment/DB drift

* **File:** `scheduler/collector.py` constant + `api/models.py`
  TelegramNotificationDLQ comment
* **Evidence:** Code constant is `5`; DB CHECK constraint allows up
  to 10; `models.py` comment says "10 in code".
* **Patch:** Keep code at 5 (current behavior); fix the model comment;
  surface the constant to the cleanup path so the threshold matches.
* **Wave:** 4

### C-08 [MEDIUM] · `revoke_consent` does not stop tracker notifications

* **File:** `api/routers/consent.py` `revoke_consent` + scheduler
* **Evidence:** Revocation deletes AI artifacts, deactivates AI
  consent flags; tracker rows untouched. Scheduler keeps sending
  Telegram alerts.
* **Why:** Whether price-tracking counts as "personal data processing"
  under №99-З is a product/legal call, but the conservative move is
  to pause user trackers on revoke and require explicit re-opt-in.
* **Patch:** In `revoke_consent`, when consent_type is `pd_processing`
  (the broadest one), set `paused=True` + `pause_reason='consent_revoked'`
  on all of the user's trackers. Re-grant un-pauses.
* **Wave:** 5

### C-09 [LOW] · `Tracker.exclude_duplicates` is dead

* **File:** `api/models.py` Tracker, `api/models.py` SavedSearch
* **Evidence:** Field exists; never read in collector, reseller_tools,
  or query_pipeline.
* **Patch:** Either implement (broader change) or document as deferred.
  Removing the column is a destructive migration — leave the column
  and add an inline comment.
* **Wave:** 8 (comment only)

### C-10 [LOW] · `_MAX_EVENTS_PER_TYPE=10` vs notification cap=3

* **File:** `scheduler/collector.py`
* **Evidence:** Events table stores 10 per type per tick; tracker
  Telegram messages cap at 3. UI shows 10 in feed but user got 3.
* **Patch:** Document the difference (covered already by the
  pre-audit batch's overflow log); add a code comment near both caps.
* **Wave:** 8

### C-11 — false positive (per-search dedup is correct)

---

## Track D — Categories / Filters

### D-1 [MEDIUM] · `matches_tracker_filters` cannot match text condition labels

* **File:** `api/services/reseller_tools.py:372-377`
* **Evidence:**
  ```
  condition_map = {"new": "2", "used": "1"}
  expected = condition_map.get(condition, condition)
  if ad_condition != expected: return False
  ```
* **Why:** Kufar returns `"Новый"`/`"Б/у"` text labels in
  `ad_parameters`. `routers/listings.py` `_matches_condition` already
  handles both formats via set-comparison; the tracker matcher does
  not, so trackers with a condition filter silently drop matches.
* **Patch:** Extract a shared helper (`api/services/aggregator.py`
  has `condition_map`; new helper `match_condition_filter` in a
  shared module). Use the same set-based comparison as listings.
* **Wave:** 2

### D-2 [MEDIUM] · `matches_tracker_filters` region exact-match without normalization

* **File:** `api/services/reseller_tools.py:379-382`
* **Evidence:** Direct `ad_region != region_name` without casefold or
  whitespace collapse.
* **Patch:** Use the same `_normalized_filter_text` from listings
  router (move to shared module).
* **Wave:** 2

### D-3 [LOW] · `/geography` share_percent doesn't sum to 100

(See B-05 — same finding from a different angle.)

### D-4 [LOW] · `region_label()` may surface district name

* **File:** `api/routers/geography.py:89`
* **Evidence:** `region_label(ad)` falls back to `area_name`/
  `locality_name`; first ad in group decides label.
* **Wave:** Deferred — Kufar's actual region_name presence is high in
  practice; touching the fallback risks regressing the case where
  region_name is genuinely absent.

### D-5 — D-12: INFO findings (graceful fallbacks, no action)

These are documented for future reference: condition-NULL ads excluded
from segments, cross-category snapshot pollution, ambiguous area
names with silent fallback, missing geo-radius feature, `detect_category`
default fallback (correct), price normalization heuristic constraint
(test-only), listing_mapper edge cases (correct), double-mapping
condition (harmless).

---

## Track E — Liquidity

### E-FIND-01 [LOW] · No projected ROI from `target_resale_byn`

* **File:** `api/routers/workflow.py`, `api/routers/analytics.py`
* **Evidence:** `actual_profit` requires `sold_price_byn`. There is no
  `projected_profit` field for watching/bought leads using `target_resale_byn`.
* **Patch:** Add `projected_profit_byn` to per-lead read & dashboard
  aggregate. Frontend can then surface "ожидаемая прибыль" before sale.
* **Wave:** 8

### E-FIND-02 [MEDIUM] · No `bought_at` timestamp → hold time uncomputable

* **File:** `api/models.py` LeadItem
* **Evidence:** Only `created_at` and `sold_at` exist. README markets
  "hold time" as a perekupshchik metric; there is no place to compute
  it from.
* **Patch:** Migration to add nullable `bought_at: TIMESTAMPTZ`;
  populate on the `* → bought` state transition; expose `hold_time_days`
  in `LeadItemRead`.
* **Wave:** 6

### E-FIND-03 [LOW] · `risk_detector` ignores zero-photo signal

* **File:** `api/services/risk_detector.py`
* **Evidence:** Photo count not in `detect_risks`; only the liquidity
  scorer handles it.
* **Patch:** Add a `no_photos` risk flag.
* **Wave:** 8

### E-FIND-04 [MEDIUM] · Duplicate detection is dead code

* **File:** `api/services/risk_detector.py` `_check_duplicate` +
  callers in `api/services/listing_mapper.py:310, 440` and
  `api/routers/listing_detail.py:167`
* **Evidence:** `_check_duplicate` reads `seller_info["is_duplicate"]`,
  but every caller passes `seller_info=None`. The function never
  fires in production, despite passing tests that call it directly.
* **Patch:** Two options — (a) remove the function, (b) actually
  detect duplicates from `Listing` history (same title + price +
  region within 24h). The second is a real feature; for now we go
  with option (a) and document it: kill the function, kill the test,
  remove the unused parameter from `detect_risks`.
* **Wave:** 6

### E-FIND-05 [MEDIUM] · `price_drop` threshold is absolute

* **File:** `scheduler/collector.py` (price_drop branch)
* **Evidence:** Drop fires when `last_dec - price_dec >= 0.5` BYN,
  no percent floor.
* **Patch:** Make it `max(0.5 BYN, last_price * 0.005)` — i.e.
  the larger of an absolute floor and a 0.5% relative threshold —
  so a 5000 BYN listing needs a 25 BYN drop instead of any 50 cent
  fluctuation.
* **Wave:** 6

### E-FIND-06 [LOW] · `watching → bought` blocked

* **File:** `api/routers/workflow.py` `_VALID_LEAD_STATUS_TRANSITIONS`
* **Evidence:** Direct watch → bought blocked; user must go through
  reviewing/in_progress.
* **Patch:** Either allow `watching → bought` (one less click) or
  document as intentional. Decision: **allow it.**
* **Wave:** 8

### E-FIND-07 [LOW] · Fallback resale below purchase

* **File:** `api/services/ai_marketplace.py` `_fallback_resale_potential`
* **Evidence:** When `fast_price >= purchase`, the fallback recomputes
  `fast_price = purchase * 0.92`, advertising a loss on resale even
  when the market is genuinely above purchase.
* **Patch:** Floor `fast_price` at `max(purchase, fallback_fast)` so
  the fallback never claims a loss when the data does not support it.
* **Wave:** 8

### E-FIND-08 [MEDIUM] · `compute_flip_estimates` ignores expenses

* **File:** `api/services/deal_workflow.py` `compute_flip_estimates`
* **Evidence:** `profit = target_price - ad.price_byn`; expenses
  not subtracted.
* **Patch:** Accept optional `expenses_byn: float = 0.0` parameter;
  subtract from each tier's profit. Caller in `routers/listing_detail.py`
  computes the lead's expense sum where available.
* **Wave:** 6

### E-FIND-09 [LOW] · `buy_price=NULL` with `sold` inflates ROI

* **File:** `api/routers/analytics.py`, `api/routers/export.py`
* **Evidence:** `buy_price = lead.buy_price_byn or 0.0`; if user sold
  without recording purchase price, `actual_profit = sold_price -
  expenses` and ROI is reported as if free goods.
* **Patch:** When `buy_price_byn is None` AND `sold_price_byn is not None`,
  return `actual_profit=None`, `roi_percent=None`, set
  `incomplete_cost_basis=True` so frontend can flag.
* **Wave:** 8

---

## Deferred (intentional or out-of-scope)

* **A-5** documentation of intentional cache sharing — needs product call.
* **A-7** `delete_all_*` race — DB CASCADE handles the orphan case.
* **A-8** health endpoint info — load-balancer probes need this.
* **A-9** image_proxy regex — Kufar CDN bounds it functionally.
* **C-01** TrackerEvent duplication — needs schema redesign.
* **C-02** UNIQUE on tracker_events — interferes with 24h re-emission.
* **C-06** partial Kufar response detection — heuristic noise risk.
* **D-4** region_label fallback — risks regressing the no-region_name case.

---

## Audit-ID convention

When a fix is non-obvious it carries a comment like:

```python
# B-04: IQR-zero fallback used to drop free listings (price_byn = 0.0).
# Allow them through explicitly so /segments and /price-stats agree.
```

`git log --grep="B-04"` resolves a finding back to the wave that
closed it.
