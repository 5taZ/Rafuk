"""Fixes from the AI modules review (docs/AI_REVIEW.md, AI-REVIEW-3.x)."""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import httpx
import pytest

from api.schemas import AIAnalysisRequest
from api.services import ai_analysis_pipeline as pipeline
from api.services.ai_prompts import (
    _PRICE_MARKET_PROMPT_TEMPLATE,
    _PRICE_MARKET_SCHEMA,
    LISTING_ASSISTANT_PROMPT,
)
from api.services.ai_service import AIService
from api.services.ai_task_store import _get_task, _set_task
from api.services.cache import MemoryCache
from api.services.watchlist_market import MARKET_SEARCH_OPTIONS, NO_COMPARABLE_PRICES
from tests.test_ai_pipeline_contract import USER_ID, FakeAI, _ad, _dataset, _run_task

# ── AI-REVIEW-3.1: complete answers are actually shared ──────────────────


async def test_complete_answer_is_cached_for_the_configured_ttl(monkeypatch) -> None:
    settings = SimpleNamespace(ai_photo_precheck_enabled=False, ai_result_cache_ttl=1800)
    result, cache, payload = await _run_task(monkeypatch, FakeAI(), settings=settings)

    assert result["source"] == "ai"
    _, expires_at = cache._storage[pipeline._analysis_cache_key(payload)]
    # The old TTL was "60 s minus the analysis time", usually <= 0.
    assert expires_at - time.monotonic() > 1700


async def test_zero_ttl_turns_the_shared_cache_off(monkeypatch) -> None:
    settings = SimpleNamespace(ai_photo_precheck_enabled=False, ai_result_cache_ttl=0)
    _, cache, payload = await _run_task(monkeypatch, FakeAI(), settings=settings)

    assert pipeline._analysis_cache_key(payload) not in cache._storage


def test_analysis_cache_key_ignores_query_case_and_spacing() -> None:
    plain = AIAnalysisRequest(ad_id=7, query="iphone 13 128")
    noisy = AIAnalysisRequest(ad_id=7, query="  iPhone   13 128 ")
    other = AIAnalysisRequest(ad_id=7, query="iphone 13 256")

    assert pipeline._analysis_cache_key(plain) == pipeline._analysis_cache_key(noisy)
    assert pipeline._analysis_cache_key(plain) != pipeline._analysis_cache_key(other)


async def _run_with_loader(monkeypatch, ai: FakeAI, loader) -> dict:
    cache = MemoryCache()
    target = _ad(1, "iPhone 14 128GB", 1590)
    monkeypatch.setattr(pipeline, "get_ai_service", lambda: ai)
    monkeypatch.setattr(pipeline, "load_query_dataset", loader)
    payload = AIAnalysisRequest(ad_id=target["ad_id"], query="iphone 14")
    await _set_task(cache, "review-task", {
        "status": "pending", "progress": 0, "result": None, "error": None,
        "_telegram_user_id": USER_ID,
    })
    await pipeline._run_analysis(
        "review-task", payload, SimpleNamespace(ai_photo_precheck_enabled=False), cache, None,
        user_id=USER_ID,
    )
    task = await _get_task(cache, "review-task", user_id=USER_ID)
    assert task["status"] == "done", task
    return task["result"]


# ── AI-REVIEW-3.4: a failed market lookup falls back to the cohorts ──────


async def test_failed_market_lookup_uses_the_cohort_searches(monkeypatch) -> None:
    target = _ad(1, "iPhone 14 128GB", 1590)

    async def loader(**kwargs):
        if kwargs.get("search_kwargs") == MARKET_SEARCH_OPTIONS:
            raise httpx.ConnectError("market search down")
        return _dataset(target)

    ai = FakeAI()
    result = await _run_with_loader(monkeypatch, ai, loader)

    assert ai.calls[0]["market_median"] is not None
    assert result["market_context"] != NO_COMPARABLE_PRICES
    assert result["fair_price"] is not None


# ── AI-REVIEW-3.3 / 3.7: no wasted price call, photos only for Call B ────


def _recording_service(monkeypatch, answers: dict) -> tuple[AIService, list[tuple[str, object]]]:
    service = AIService()
    calls: list[tuple[str, object]] = []

    async def fake_chat(*, operation: str, content, **kwargs):
        del kwargs
        calls.append((operation, content))
        answer = answers[operation]
        if isinstance(answer, BaseException):
            raise answer
        return dict(answer)

    async def fake_fetch(_url):
        return {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,AA=="}}

    monkeypatch.setattr(service, "_chat", fake_chat)
    monkeypatch.setattr(service, "_fetch_image_b64", fake_fetch)
    return service, calls


_B_ANSWER = {"summary": "ok", "recommendation": {"verdict": "think_twice", "text": "x"}}
_A_ANSWER = {"market_context": "Цена 1500 BYN ниже медианы 1590 BYN."}


async def _analyze(service: AIService, *, median: float | None, images: bool = True) -> dict:
    return await service.analyze_listing_parallel(
        title="iPhone 14", description="Обычное состояние", price_byn=1500, condition="Б/у",
        parameters=[], market_median=median, market_count=24 if median else 0,
        image_urls=["https://rms.kufar.by/v1/gallery/a.jpg"] if images else None,
    )


async def test_no_median_skips_the_price_call(monkeypatch) -> None:
    service, calls = _recording_service(monkeypatch, {
        "analysis_price_market": _A_ANSWER, "analysis_condition_risks": _B_ANSWER,
    })
    merged = await _analyze(service, median=None)

    assert [operation for operation, _ in calls] == ["analysis_condition_risks"]
    assert merged["_failed_sections"] == []  # skipped, not failed: no "partial" label
    assert merged["summary"] == "ok"


async def test_no_median_and_failed_condition_call_raises(monkeypatch) -> None:
    service, _ = _recording_service(monkeypatch, {
        "analysis_price_market": _A_ANSWER,
        "analysis_condition_risks": RuntimeError("503 from provider"),
    })
    with pytest.raises(RuntimeError, match="503"):
        await _analyze(service, median=None)


async def test_with_median_photos_go_only_to_the_condition_call(monkeypatch) -> None:
    service, calls = _recording_service(monkeypatch, {
        "analysis_price_market": _A_ANSWER, "analysis_condition_risks": _B_ANSWER,
    })
    merged = await _analyze(service, median=1590)
    content = dict(calls)

    assert isinstance(content["analysis_price_market"], str)
    assert isinstance(content["analysis_condition_risks"], list)
    assert any(part.get("type") == "image_url" for part in content["analysis_condition_risks"])
    assert merged["_images_analyzed"] == 1
    assert merged["market_context"] == _A_ANSWER["market_context"]


async def test_no_median_rate_limit_retries_condition_text_only(monkeypatch) -> None:
    service = AIService()
    contents: list[object] = []

    async def fake_chat(*, operation: str, content, **kwargs):
        del kwargs
        assert operation == "analysis_condition_risks"
        contents.append(content)
        if len(contents) == 1:
            raise RuntimeError("429 RATE_LIMITED")
        return dict(_B_ANSWER)

    async def fake_fetch(_url):
        return {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,AA=="}}

    monkeypatch.setattr(service, "_chat", fake_chat)
    monkeypatch.setattr(service, "_fetch_image_b64", fake_fetch)
    merged = await _analyze(service, median=None)

    assert [type(c) for c in contents] == [list, str]
    assert merged["_images_analyzed"] == 0


def test_price_prompt_no_longer_mentions_attached_photos() -> None:
    assert "Если приложены фотографии" not in _PRICE_MARKET_PROMPT_TEMPLATE
    assert "condition.notes" not in _PRICE_MARKET_PROMPT_TEMPLATE


# ── AI-REVIEW-3.5: the model is not asked for fields the backend replaces ─


def test_price_schema_no_longer_asks_for_best_pick() -> None:
    from api.services.ai_service import _PRICE_MARKET_KEYS

    assert "best_pick" not in _PRICE_MARKET_SCHEMA
    assert "best_pick" not in _PRICE_MARKET_PROMPT_TEMPLATE
    assert "best_pick" not in _PRICE_MARKET_KEYS


def test_listing_prompt_drops_market_summary_and_numbers_competitors() -> None:
    assert '"market_summary"' not in LISTING_ASSISTANT_PROMPT
    assert '"n": 2' in LISTING_ASSISTANT_PROMPT
    assert '"pricing": null' in LISTING_ASSISTANT_PROMPT


def _competitors() -> list[dict]:
    return [
        {"title": f"iPhone 13 вариант {i}", "price_byn": 1300 + 50 * i,
         "image_url": f"https://rms.kufar.by/v1/gallery/{i}.jpg",
         "link": f"https://www.kufar.by/item/{100 + i}"}
        for i in range(1, 8)
    ]


def test_listing_context_numbers_the_competitors() -> None:
    context = AIService._build_listing_assistant_context(
        None, title="iPhone 13", condition=None, is_negotiable=False, draft_price_byn=None,
        extra_notes=None, market_median=1400, market_q1=1350, market_q3=1500,
        market_min=1300, market_max=1650, market_count=7, similar_listings=_competitors(),
        category_hint=None, category_bargain_hint=None,
    )

    assert "1. iPhone 13 вариант 1 (1350 BYN)" in context
    assert "6. iPhone 13 вариант 6" in context
    assert "7. iPhone 13 вариант 7" not in context  # only the first six are shown


def test_competitors_by_number_come_from_the_dataset() -> None:
    from api.routers.ai_listing_assistant import _coerce_competitors

    raw = [
        {"n": 2, "advantage": "У нас дешевле"},
        {"n": "2", "advantage": "повтор"},
        {"n": 7, "advantage": "номер не показан модели"},
        {"n": True, "advantage": "не номер"},
        {"n": 5, "advantage": "Комплект полнее"},
    ]
    result = _coerce_competitors(raw, dataset_competitors=_competitors())

    assert [(c.title, c.price_byn, c.advantage) for c in result] == [
        ("iPhone 13 вариант 2", 1400.0, "У нас дешевле"),
        ("iPhone 13 вариант 5", 1550.0, "Комплект полнее"),
    ]
    assert result[0].link == "https://www.kufar.by/item/102"
    assert result[0].image_url == "https://rms.kufar.by/v1/gallery/2.jpg"


# ── AI-REVIEW-3.2: provider slots, bounded queue, budget under the watchdog ─


async def test_full_queue_ends_in_a_labelled_fallback(monkeypatch) -> None:
    monkeypatch.setattr(pipeline, "_ANALYSIS_QUEUE_TIMEOUT", 0.05)
    target = _ad(1, "iPhone 14 128GB", 1590)
    searches: list[dict] = []

    async def loader(**kwargs):
        searches.append(kwargs)
        return _dataset(target)

    sem = pipeline._get_analysis_run_semaphore()
    for _ in range(pipeline._ANALYSIS_RUN_LIMIT):
        await sem.acquire()
    ai = FakeAI()
    try:
        result = await _run_with_loader(monkeypatch, ai, loader)
    finally:
        for _ in range(pipeline._ANALYSIS_RUN_LIMIT):
            sem.release()

    assert searches  # the Kufar stage ran without a provider slot
    assert ai.calls == []
    assert result["source"] == "fallback"
    assert "занят другими анализами" in result["_ai_warning"]


async def test_three_analyses_reach_the_provider_at_once(monkeypatch) -> None:
    target = _ad(1, "iPhone 14 128GB", 1590)
    running = 0
    peak = 0
    release = asyncio.Event()

    class SlowAI(FakeAI):
        async def analyze_listing_parallel(self, **kwargs) -> dict:
            nonlocal running, peak
            running += 1
            peak = max(peak, running)
            if running == pipeline._ANALYSIS_RUN_LIMIT:
                release.set()
            await asyncio.wait_for(release.wait(), timeout=5)
            running -= 1
            return await super().analyze_listing_parallel(**kwargs)

    async def loader(**kwargs):
        del kwargs
        return _dataset(target)

    ai = SlowAI()
    monkeypatch.setattr(pipeline, "get_ai_service", lambda: ai)
    monkeypatch.setattr(pipeline, "load_query_dataset", loader)
    cache = MemoryCache()
    payload = AIAnalysisRequest(ad_id=1, query="iphone 14")
    ids = [f"t{i}" for i in range(3)]
    for task_id in ids:
        await _set_task(cache, task_id, {"status": "pending", "_telegram_user_id": USER_ID})
    await asyncio.gather(*(
        pipeline._run_analysis(
            task_id, payload, SimpleNamespace(ai_photo_precheck_enabled=False), cache, None,
            user_id=USER_ID,
        )
        for task_id in ids
    ))

    assert peak == 3
    for task_id in ids:
        assert (await _get_task(cache, task_id, user_id=USER_ID))["status"] == "done"


def test_ai_stage_timeout_fits_inside_the_watchdog() -> None:
    from api.services.ai_task_store import _BG_TASK_TIMEOUT

    fresh = SimpleNamespace(analysis_timeout=150, started_at=time.monotonic())
    late = SimpleNamespace(analysis_timeout=150, started_at=time.monotonic() - 120)
    very_late = SimpleNamespace(analysis_timeout=150, started_at=time.monotonic() - 210)
    tiny = SimpleNamespace(analysis_timeout=0.1, started_at=time.monotonic() - 210)

    assert pipeline._ai_stage_timeout(fresh) == 150
    assert 94 <= pipeline._ai_stage_timeout(late) <= 95
    assert pipeline._ai_stage_timeout(very_late) == pipeline._MIN_AI_STAGE_SECONDS
    assert pipeline._ai_stage_timeout(tiny) == 0.1
    # queue wait + a full AI stage still ends before the watchdog fires
    assert pipeline._PIPELINE_BUDGET_SECONDS < _BG_TASK_TIMEOUT


# ── AI-REVIEW-3.6 / 3.9: provider-shaped requests, priced usage ──────────

_MESSAGES = [{"role": "system", "content": "JSON"}, {"role": "user", "content": "x"}]


def test_openai_reasoning_body_has_no_max_tokens_or_temperature() -> None:
    body = AIService()._request_body(
        "gpt-6-luna", _MESSAGES, max_tokens=800, reasoning_effort="low",
    )

    assert body["max_completion_tokens"] == 800
    assert "max_tokens" not in body and "temperature" not in body
    assert body["reasoning_effort"] == "low"
    assert body["response_format"] == {"type": "json_object"}


def test_openai_detection_covers_routed_names_and_skips_others() -> None:
    from api.services.ai_service import _is_openai_reasoning_model

    assert _is_openai_reasoning_model("openai/gpt-6-luna")
    assert _is_openai_reasoning_model("GPT-5.4-mini")
    assert not _is_openai_reasoning_model("gemini-2.5-flash")
    assert not _is_openai_reasoning_model("google/gemma-3n-E4B-it")


def test_gemini_body_keeps_its_shape() -> None:
    body = AIService()._request_body(
        "gemini-2.5-flash", _MESSAGES, max_tokens=3200, reasoning_effort=None,
    )

    assert body["max_tokens"] == 3200 and body["temperature"] == 0.2
    assert body["reasoning_effort"] == "medium"
    assert body["response_format"] == {"type": "json_object"}


def test_generic_provider_body_has_no_json_mode() -> None:
    service = AIService()
    service._base_url = "https://api.together.xyz/v1"
    body = service._request_body(
        "google/gemma-3n-E4B-it", _MESSAGES, max_tokens=1200, reasoning_effort="low",
    )

    assert body["max_tokens"] == 1200
    assert "reasoning_effort" not in body and "response_format" not in body


async def test_chat_sets_reasoning_effort_per_operation(monkeypatch) -> None:
    service = AIService()
    service._model = service._analysis_model = "gpt-6-luna"
    service._chat_min_interval_seconds = 0
    sent: list[dict] = []

    async def fake_post(client, url, *, headers, json):
        del client, url, headers
        sent.append(json)
        return httpx.Response(200, json={
            "choices": [{"message": {"content": '{"ok": true}'}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        })

    monkeypatch.setattr(service, "_post_with_retry", fake_post)
    await service.chat_json(system="JSON", content="x")
    await service._chat(system="JSON", content="x", operation="analysis_condition_risks")

    assert [body["reasoning_effort"] for body in sent] == ["low", "medium"]
    assert all("max_tokens" not in body for body in sent)


def test_luna_usage_is_priced_with_cached_input() -> None:
    from api.services.ai_costs import estimate_ai_cost_usd, extract_ai_usage

    usage = extract_ai_usage({"usage": {
        "prompt_tokens": 9000, "completion_tokens": 3500, "total_tokens": 12500,
        "prompt_tokens_details": {"cached_tokens": 2000},
        "completion_tokens_details": {"reasoning_tokens": 1800},
    }})

    assert usage.cached_prompt_tokens == 2000
    expected = (7000 * 0.10 + 2000 * 0.01 + 3500 * 0.50) / 1_000_000
    assert abs(estimate_ai_cost_usd("gpt-6-luna", usage) - expected) < 1e-12
    # Models without a published cached rate bill cached input as input.
    gemini = (9000 * 0.30 + 3500 * 2.50) / 1_000_000
    assert abs(estimate_ai_cost_usd("gemini-2.5-flash", usage) - gemini) < 1e-12


# ── AI-REVIEW-3.8: no paid call (or quota unit) for a fixed "neutral" ────


def test_price_advice_without_market_spends_no_quota(monkeypatch) -> None:
    from fastapi.testclient import TestClient

    import api.services.query_pipeline as query_pipeline
    from api.dependencies import get_telegram_user
    from api.main import create_app
    from api.routers import ai_tools
    from tests.test_ai_tools import FakeAIChatService, _noop_async, fake_telegram_user

    fake_ai = FakeAIChatService()
    charges: list[str] = []

    async def empty_market(**kwargs):
        del kwargs
        return SimpleNamespace(price_stats=SimpleNamespace(median=0.0, count=0, q1=0.0, q3=0.0))

    async def charge(request, user_id, *, endpoint="default"):
        del request, user_id
        charges.append(endpoint)

    monkeypatch.setattr(ai_tools, "_check_ai_available", lambda: fake_ai)
    monkeypatch.setattr(ai_tools, "_check_ai_consent", _noop_async)
    monkeypatch.setattr(ai_tools, "_check_ai_entitlement", _noop_async)
    monkeypatch.setattr(ai_tools, "_check_rate_limit", charge)
    monkeypatch.setattr(ai_tools, "_log_ai_audit", _noop_async)
    monkeypatch.setattr(query_pipeline, "load_query_dataset", empty_market)
    app = create_app()
    app.dependency_overrides[get_telegram_user] = fake_telegram_user

    with TestClient(app) as client:
        resp = client.post(
            "/api/v1/ai/price-advice", json={"query": "редкий товар", "current_price_byn": 100},
        )

    assert resp.status_code == 200
    assert resp.json()["advice"] == "neutral"
    assert charges == [] and fake_ai.calls == []


# ── AI-REVIEW-3.12: small correctness cleanups ───────────────────────────


async def test_quota_refusal_without_a_full_window_is_a_429_not_a_500() -> None:
    from fastapi import HTTPException

    from api.services.ai_quota import consume_ai_quota

    class RacyStore:
        async def rolling_quota(self, key, **kwargs):
            del key, kwargs
            return 2, 3, time.time() + 600, time.time() + 6000, False

    with pytest.raises(HTTPException) as exc_info:
        await consume_ai_quota(
            RacyStore(), telegram_user_id=1, short_limit=5, weekly_limit=50, request_id="r",
        )

    assert exc_info.value.status_code == 429
    assert exc_info.value.detail["window"] == "5h"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ('Ответ: {"a": {"b": 1}} конец', {"a": {"b": 1}}),
        ('```json\n{"x": 1}\n```', {"x": 1}),
        ('{"fair_price": {"from": 1, "to": 2}, "summ', {"fair_price": {"from": 1, "to": 2}}),
    ],
)
def test_parse_json_still_handles_wrapped_and_truncated_answers(text: str, expected: dict) -> None:
    assert AIService._parse_json(text) == expected


def test_listing_assistant_shares_the_ai_result_ttl() -> None:
    from api.routers import ai_listing_assistant

    assert ai_listing_assistant._ai_result_cache_ttl(SimpleNamespace()) == 1800
    assert ai_listing_assistant._ai_result_cache_ttl(
        SimpleNamespace(ai_result_cache_ttl=600)
    ) == 600
