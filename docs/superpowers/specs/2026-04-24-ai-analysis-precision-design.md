# AI Analysis Precision Design

Date: 2026-04-24
Status: Draft for review

## Goal

Improve AI analysis quality for any listing, including fixed-price and negotiable-price listings, without making the feature materially slower.

The scope is the full Kufar marketplace, not only cars. Cars are used as the most sensitive example because regressions there are especially visible, but the design must improve analog selection and risk detection across all supported categories.

The analysis should work for both:
- a professional buyer who cares about configuration accuracy, seller risk, and market realism;
- an ordinary user who needs a clear recommendation, realistic analogs, and actionable red flags.

## Current Problems

The current AI pipeline degrades in two visible ways:

1. Similar listings are selected mostly by price proximity.
This causes weak analogs to be sent to AI even when they are not truly close in model, generation, configuration, or seller context.

2. Seller risk is underrepresented.
Signals such as `company_ad`, marketplace hot words, financing bait, reseller wording, and low-information sales listings are not consistently elevated into the final red flags.

This produces weaker reports:
- fewer truly comparable alternatives;
- weaker market context;
- missing red flags such as "автохаус / мутный дилер";
- less trustworthy fair price reasoning.

## Product Requirements

1. Prefer fewer but stronger analogs over padding the list with weak matches.
2. Preserve support for negotiable-price listings.
3. Keep photo and description analysis in place.
4. Keep the AI path single-pass: no second AI rerank request.
5. Reuse already-fetched search datasets as much as possible.
6. Make seller-risk and marketplace risk context visible when it is grounded in observable signals.

## Non-Goals

1. No universal perfect similarity engine for every category in one pass.
2. No extra multimodal calls for each candidate analog.
3. No expensive embedding search or external vector store.
4. No increase in UI complexity for the user during this iteration.

## Design Overview

The new pipeline is `precision-first`.

Instead of selecting analogs mainly by `price within +/-30%`, we will:

1. Build several candidate cohorts from fast search strategies.
2. Score candidates by structural similarity first.
3. Use price as one factor, not the main selector.
4. Keep only high-confidence analogs for AI.
5. Separately compute marketplace risk context and pass it to AI as grounded context.
6. Add a server-side fallback red flag when risk is high and based on explicit facts.

## Search Cohorts

The system already runs multiple search strategies. We will keep the same general cost profile, but use the results differently.

### Cohort Order

1. `strict + same category`
2. `broad + same category`
3. `broad + no category`

### Selection Rule

The target listing is searched across the cohorts. Once found, all available cohorts are still allowed as candidate sources, but they are ranked by trust:

1. candidates from `strict + same category`;
2. then `broad + same category`;
3. then `broad + no category`.

This preserves recall without allowing fallback cohorts to dominate stronger ones.

## Candidate Similarity Scoring

We introduce a deterministic `similarity_score` per candidate.

### Common Signals

For all categories:

1. Exact or near-exact token overlap with the normalized query.
2. High title similarity to the target title.
3. Parameter overlap from `ad_parameters`.
4. Same condition.
5. Same seller type gets a small boost; seller-type mismatch gets a small penalty.
6. Richer listing quality gets a small boost:
   - real description;
   - more than one photo;
   - recent listing.

### Auto-Specific Signals

For vehicles, these signals are high priority:

1. Generation / body markers: `4L`, `4M`, `B8`, `W212`, etc.
2. Year proximity.
3. Engine displacement.
4. Fuel type.
5. Transmission.
6. Body type / trim markers when available.
7. Mileage proximity as a ranking factor, not a hard filter.

Auto candidates that mismatch generation/body are heavily penalized.
This is the main fix for cases like `Audi Q7`, where a direct competitor of the same generation matters more than a random similarly priced Q7 variant.

### Other Category Signals

For non-auto goods, the scoring uses category-relevant fields where present:

1. phones: model family, memory, color, condition;
2. laptops: model, CPU family when visible, RAM, SSD, screen size;
3. headphones: generation, wireless/wired, case/box/originality hints;
4. TVs/monitors: size, resolution, panel traits;
5. consoles: generation, storage, bundle notes;
6. default fallback: title + parameter overlap + condition.

If a category does not yet have custom rules, it must still benefit from the common precision-first scoring instead of falling back to price-first behavior.

## Candidate Filtering

We intentionally do not always return 5 analogs.

### Rules

1. Hard reject the target listing itself.
2. Hard reject zero-price candidates as analogs for fair-price grounding unless no priced analogs exist.
3. Hard reject candidates below a minimum similarity threshold.
4. Keep a small rerank pool of at most 16 candidates.
5. Send only top high-confidence analogs to AI, up to 5 items total.
6. If only 2-3 strong analogs exist, keep only 2-3.

The product principle is honesty over completeness.

## Price Handling

Price remains useful, but it is no longer the main gate.

### Fixed-Price Listings

Price affects:

1. market position label;
2. fair price reasoning;
3. candidate ranking as a mild tie-breaker.

### Negotiable-Price Listings

Negotiable listings should not be treated as `0 BYN`.

For these listings:

1. candidate selection relies on similarity first, not price distance;
2. fair price is grounded by market distribution and priced analogs;
3. negotiation guidance is framed as entry-price logic, not as if the current price were zero.

## Marketplace Risk Context Layer

A marketplace-wide risk context is computed server-side before the AI call.

### Observable Signals

1. `company_ad == True`
2. seller type / dealer / shop / reseller label in parameters
3. hot words and risk words such as `кредит`, `лизинг`, `рассрочка`, `/мес`, `перекуп`, `автохаус`, `площадка`, `доставка`
4. financing-bait wording and aggressive sales wording
5. unusually thin factual description with heavy sales language
6. mismatch between polished reseller framing and low-information listing
7. suspicious phrases that reduce transparency, such as deflecting concrete condition details or hiding ownership context

### Output

The pipeline produces:

1. `risk_context_score`
2. `risk_context_flags`
3. `risk_context_summary`

This context is sent into AI explicitly so the model can mention dealer markup, reseller behavior, financing bait, opacity of history, and other marketplace risks when justified.

## Guardrails for Red Flags

We should not rely only on the model to mention marketplace risk context.

### Rule

If server-side risk context is high and based on explicit facts, and the AI output omits it, we inject one factual red flag after AI:

- example: `Продавец — автохаус/дилерская площадка; возможна наценка и слабая прозрачность истории обслуживания.`

This guardrail should be conservative and only fire on directly supported evidence.

## AI Context Changes

The AI request format remains single-pass, but the context becomes sharper.

We add:

1. marketplace risk context summary;
2. more precise analog list;
3. clearer explanation of why these analogs were selected;
4. stronger prompt bias toward explicit comparison with direct competitors.

The AI still analyzes:

1. target photos;
2. target description;
3. market stats;
4. selected analogs.

No extra AI reranking call is added.

## Performance Constraints

This design must remain close to current latency.

### Allowed

1. More deterministic scoring on already-loaded ads.
2. Small extra parsing of titles and parameters.
3. Slightly smarter cohort fusion.

### Not Allowed

1. Extra AI request per analysis.
2. Per-candidate image analysis.
3. Heavy external retrieval systems.

Expected cost increase should remain modest because the work is CPU-light and runs on a small in-memory candidate set.

## User-Facing Outcome

For an ordinary user:

1. fewer but more believable alternatives;
2. clearer explanation why the listing is good or bad;
3. visible marketplace risk context when relevant;
4. less noise from weak analogs.

For a professional buyer:

1. stronger grounding by generation/configuration;
2. better market realism;
3. better negotiation inputs;
4. fewer false comparisons.

## Implementation Plan Shape

Implementation should be split into focused steps:

1. Extract candidate normalization and similarity utilities.
2. Replace `_collect_similar_listings` with cohort-aware precision scoring.
3. Add marketplace risk-context extraction and post-AI red-flag guardrail.
4. Expand tests for auto, negotiable-price, and dealer-risk scenarios.
5. Validate response size and latency remain acceptable.

## Testing Requirements

Add tests for:

1. strict-search candidates outranking broad ones;
2. generation/body mismatch getting penalized for autos;
3. negotiable-price listing still getting useful analogs;
4. risk-context red flag appearing for autohaus/dealer/reseller/financing-bait cases;
5. weak analogs not padding the list to five;
6. no material regression in the number of search calls.

## Acceptance Criteria

The change is successful when:

1. the same target listing gets a tighter, more believable analog set;
2. autohaus/dealer/reseller/financing-bait risk is surfaced when justified;
3. negotiable-price listings still receive grounded fair-price analysis;
4. the number of analogs may be lower, but their quality is higher;
5. the end-to-end analysis remains roughly as responsive as today.
