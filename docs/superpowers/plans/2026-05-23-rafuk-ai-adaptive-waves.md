# Rafuk AI Adaptive Waves Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Rafuk AI adapt its analysis and seller assistant to any Kufar.by product and to the user's actual intent, while preserving the current response volume.

**Architecture:** Keep the existing Gemini/OpenAI-compatible pipeline and JSON schemas. Add small, test-covered guidance layers around it: category profiles, intent profiles, quality metrics, and post-response quality checks. Do not reduce prompt sections or `max_tokens`; optimize by routing cheaper models where quality risk is low and by measuring real token/cost data.

**Tech Stack:** FastAPI, Pydantic, vanilla JS frontend, pytest, ruff, Prometheus text metrics.

---

### Wave AI-00: Commit Current AI Foundation

**Files:**
- Modify: `api/services/ai_marketplace.py`
- Modify: `api/services/ai_prompts.py`
- Modify: `api/services/ai_service.py`
- Modify: `api/services/ai_costs.py`
- Modify: `api/metrics.py`
- Modify: `api/config.py`
- Modify: `api/routers/ai_analysis.py`
- Modify: `api/routers/ai_listing_assistant.py`
- Modify: `tests/test_ai_analysis.py`
- Modify: `tests/test_ai_error_paths.py`
- Modify: `tests/test_metrics.py`

- [ ] **Step 1: Verify tests**

Run:

```bash
uv run pytest --tb=short -q
uv run ruff check .
```

Expected: full pytest and ruff pass.

- [ ] **Step 2: Commit**

Commit only the AI foundation files. Leave unrelated markdown drafts and unrelated frontend polish out unless committed in their own wave.

### Wave AI-01: Category Intelligence Profiles

**Files:**
- Create: `api/services/ai_category_profiles.py`
- Modify: `api/services/ai_service.py`
- Test: `tests/test_ai_analysis.py`

- [ ] **Step 1: Write failing tests**

Add tests that build listing context for:

```python
def test_ai_context_includes_plant_specific_guidance() -> None:
    service = AIService()
    context = service._build_listing_context(
        title="Монстера в горшке",
        description="Большое комнатное растение",
        price_byn=45,
        is_negotiable_price=False,
        condition="Хорошее",
        parameters=[],
        market_median=50,
        market_count=8,
    )
    assert "вредители" in context.lower()
    assert "imei" not in context.lower()


def test_listing_assistant_context_includes_clothing_specific_guidance() -> None:
    service = AIService()
    context = service._build_listing_assistant_context_for_tests(
        title="Пальто женское шерстяное",
        condition="Б/у",
        is_negotiable=False,
        draft_price_byn=120,
        extra_notes=None,
        market_median=130,
        market_q1=100,
        market_q3=160,
        market_min=80,
        market_max=200,
        market_count=12,
        similar_listings=[],
        category_hint=None,
        category_bargain_hint=None,
    )
    assert "размер" in context.lower()
    assert "состав ткани" in context.lower()
```

- [ ] **Step 2: Run RED**

Run:

```bash
uv run pytest tests/test_ai_analysis.py::test_ai_context_includes_plant_specific_guidance tests/test_ai_analysis.py::test_listing_assistant_context_includes_clothing_specific_guidance -q
```

Expected: tests fail because category profiles and test helper do not exist yet.

- [ ] **Step 3: Implement profiles**

Create `ai_category_profiles.py` with `AI_CATEGORY_PROFILES` and `guidance_for_title(title, parameters)`.
Include at least: plants, books, clothing, furniture, baby goods, tools, pets, real estate, electronics, auto.

- [ ] **Step 4: Inject profiles**

Append profile guidance to buyer analysis context and seller assistant context.

- [ ] **Step 5: Run GREEN and commit**

Run:

```bash
uv run pytest tests/test_ai_analysis.py::test_ai_context_includes_plant_specific_guidance tests/test_ai_analysis.py::test_listing_assistant_context_includes_clothing_specific_guidance -q
uv run pytest tests/test_ai_analysis.py tests/test_ai_error_paths.py -q
uv run ruff check api/services/ai_category_profiles.py api/services/ai_service.py tests/test_ai_analysis.py
```

Commit as `Wave AI-01: add adaptive category guidance`.

### Wave AI-02: User Intent Profiles

**Files:**
- Modify: `api/schemas.py`
- Modify: `api/routers/ai_analysis.py`
- Modify: `api/routers/ai_listing_assistant.py`
- Modify: `api/services/ai_service.py`
- Test: `tests/test_ai_analysis.py`
- Test: `tests/test_schema_validation.py`

- [ ] **Step 1: Write failing tests**

Add optional fields:

```python
AIAnalysisRequest(..., user_goal="safe_buy")
AIListingAssistantRequest(..., seller_goal="sell_fast")
```

Expected context snippets:
- `safe_buy`: prioritise fraud, meeting checklist, documents, hidden defects.
- `resale`: prioritise entry price, margin, liquidity.
- `sell_fast`: clearer low-friction price and practical photos.
- `maximize_price`: stronger positioning, patient price, negotiation floor.

- [ ] **Step 2: Run RED**

Run targeted schema/context tests and confirm they fail.

- [ ] **Step 3: Implement intent guidance**

Keep defaults backward-compatible. Existing clients that omit the fields must get the current behavior.

- [ ] **Step 4: Run GREEN and commit**

Run targeted tests, `tests/test_ai_analysis.py`, `tests/test_schema_validation.py`, and ruff. Commit as `Wave AI-02: adapt AI to user intent`.

### Wave AI-03: Quality Feedback and Outcome Metrics

**Files:**
- Modify: `api/metrics.py`
- Modify: `api/routers/ai_analysis.py`
- Modify: `api/routers/ai_listing_assistant.py`
- Modify: `api/schemas.py`
- Test: `tests/test_metrics.py`
- Test: `tests/test_ai_analysis.py`

- [ ] **Step 1: Write failing tests**

Add a small feedback endpoint or event helper that records:
- endpoint (`analyze`, `listing_assistant`)
- rating (`helpful`, `not_helpful`)
- reason (`too_generic`, `wrong_category`, `bad_price`, `good`)

- [ ] **Step 2: Implement metrics**

Expose Prometheus counter `kufar_ai_feedback_total{endpoint,rating,reason}`.

- [ ] **Step 3: Run tests and commit**

Run targeted tests and ruff. Commit as `Wave AI-03: add AI quality feedback metrics`.

### Wave AI-04: Anti-Generic Quality Guard

**Files:**
- Create: `api/services/ai_quality.py`
- Modify: `api/services/ai_marketplace.py`
- Modify: `api/routers/ai_listing_assistant.py`
- Test: `tests/test_ai_analysis.py`

- [ ] **Step 1: Write failing tests**

Add tests that reject or improve empty/generic advice:

```python
assert is_generic_ai_advice("Обратите внимание на состояние товара")
assert not is_generic_ai_advice("Проверь корни и листья на вредителей")
```

- [ ] **Step 2: Implement checks**

Add lightweight phrase/action-density helpers. Use them only to repair fallback/generated fragments, not to shrink valid model responses.

- [ ] **Step 3: Run tests and commit**

Run targeted tests, AI tests, and ruff. Commit as `Wave AI-04: guard AI advice against generic output`.

### Wave AI-05: Frontend Intent and Feedback Controls

**Files:**
- Modify: `frontend/index.html`
- Modify: `frontend/js/api_ai.js`
- Modify: `frontend/js/api_listing_assistant.js`
- Modify: `frontend/js/api_ai_render.js`
- Modify: `frontend/css/parts/layout.css`
- Rebuild: `frontend/js/app_bundle.js`
- Run: `scripts/bump_static_version.sh`
- Test: `tests/test_frontend_structure.py`
- Test: `tests/test_app_js_syntax.py`

- [ ] **Step 1: Write failing tests**

Assert frontend sends optional intent fields and exposes feedback controls without reducing displayed sections.

- [ ] **Step 2: Implement UI**

Add compact controls:
- Buyer analysis: `Для себя`, `Максимум безопасности`, `Перепродажа`.
- Listing assistant: `Продать быстрее`, `Продать дороже`, `Баланс`.

- [ ] **Step 3: Rebuild, test, commit**

Run frontend tests, full pytest, ruff, bundle rebuild, static version bump. Commit as `Wave AI-05: expose adaptive AI controls`.
