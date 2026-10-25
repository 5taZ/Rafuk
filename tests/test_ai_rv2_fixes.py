"""Regression tests for the RV-2 AI review findings (AI2-*, AI-11/12/13)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from api.services.ai_images import compress_image, fetch_image_b64
from api.services.ai_listing_guardrails import normalize_listing_pricing
from api.services.ai_marketplace import (
    MarketplaceRiskContext,
    RiskFloor,
    _price_anchor_discount,
    choose_best_alternative,
    complete_analysis_sections,
    enforce_risk_floor,
)
from api.services.ai_quality import filter_specific_advice
from api.services.ai_sanitize import (
    clean_model_text,
    sanitize_user_text,
    scrub_pii,
    strip_html_tags,
)
from api.services.ai_service import AIService, _repair_truncated_json
from api.services.ai_task_store import _get_task, _task_cache_key, _update_task
from api.services.cache import MemoryCache

# ── AI2-01 / AI-11: model output parsing ──────────────────────────────


def test_repair_closes_array_of_objects_innermost_first() -> None:
    truncated = (
        '{"summary": "Нормальный вариант", "watch_out": '
        '[{"point": "Корпус", "why": "изн'
    )
    result = _repair_truncated_json(truncated)
    assert result["summary"] == "Нормальный вариант"
    assert result["watch_out"] == [{"point": "Корпус", "why": "изн"}]


def test_repair_drops_trailing_comma() -> None:
    result = _repair_truncated_json('{"negotiation_tips": ["Скинь 50 BYN",')
    assert result == {"negotiation_tips": ["Скинь 50 BYN"]}


@pytest.mark.parametrize("text", ["модель ответила прозой", '{"a": tru'])
def test_repair_raises_instead_of_inventing_a_summary(text: str) -> None:
    with pytest.raises(ValueError):
        _repair_truncated_json(text)


@pytest.mark.parametrize("text", ["[1, 2]", '"text"', "42", "null", "[]"])
def test_parse_json_rejects_non_objects(text: str) -> None:
    with pytest.raises(ValueError):
        AIService._parse_json(text)


def _parallel_service(monkeypatch, answers: dict) -> AIService:
    service = AIService()

    async def fake_chat(*, operation: str, **kwargs):
        del kwargs
        answer = answers[operation]
        if isinstance(answer, BaseException):
            raise answer
        return answer

    monkeypatch.setattr(service, "_chat", fake_chat)
    return service


async def _analyze(service: AIService) -> dict:
    return await service.analyze_listing_parallel(
        title="iPhone 14",
        description="Обычное состояние",
        price_byn=1500,
        condition="Б/у",
        parameters=[],
        market_median=1590,
        market_count=24,
    )


async def test_one_call_cannot_null_out_the_other_calls_sections(monkeypatch) -> None:
    fair = {"from": 1500, "to": 1650, "reasoning": "по аналогам"}
    service = _parallel_service(monkeypatch, {
        "analysis_price_market": {"fair_price": fair, "summary": "не из этой схемы"},
        "analysis_condition_risks": {
            "fair_price": None,
            "summary": "Цена около медианы.",
            "recommendation": {"verdict": "think_twice", "text": "x"},
        },
    })
    merged = await _analyze(service)

    assert merged["fair_price"] == fair
    assert merged["summary"] == "Цена около медианы."
    assert merged["_failed_sections"] == []


async def test_unparseable_call_marks_its_section_failed(monkeypatch) -> None:
    service = _parallel_service(monkeypatch, {
        "analysis_price_market": {"fair_price": {"from": 1500, "to": 1650}},
        "analysis_condition_risks": ValueError("unparseable model output"),
    })
    merged = await _analyze(service)

    assert merged["_failed_sections"] == ["condition_risks"]
    assert merged["summary"] == ""


async def test_call_without_any_schema_key_counts_as_failed(monkeypatch) -> None:
    service = _parallel_service(monkeypatch, {
        "analysis_price_market": {"unexpected": "shape"},
        "analysis_condition_risks": {"summary": "ok"},
    })
    merged = await _analyze(service)
    assert merged["_failed_sections"] == ["price_market"]


async def test_both_calls_without_schema_keys_raise(monkeypatch) -> None:
    service = _parallel_service(monkeypatch, {
        "analysis_price_market": {"x": 1},
        "analysis_condition_risks": {"y": 2},
    })
    with pytest.raises(ValueError):
        await _analyze(service)


def test_alternatives_prompt_has_no_similarity_score_as_deal_rating() -> None:
    # AI2-10: the similarity score read like "bad deal, 31/100".
    context = AIService._build_listing_context(
        AIService.__new__(AIService),
        title="iPhone 14",
        description=None,
        price_byn=1500,
        is_negotiable_price=False,
        condition=None,
        parameters=[],
        market_median=1590,
        market_count=24,
        similar_listings=[{
            "title": "iPhone 14 128GB", "price_byn": 1580, "price_delta_byn": 80,
            "deal_score": 31.0, "seller_type": "private",
        }],
    )
    assert "31/100" not in context


# ── AI2-02 / AI2-14: model prose clean-up ─────────────────────────────


def test_specific_advice_keeps_price_ranges_and_plain_words() -> None:
    tips = filter_specific_advice(
        [
            "Торгуйся в диапазоне 1200-1300 BYN",
            "Проверь, нет ли jailbreak на iPhone и IMEI в коробке",
        ],
        limit=5,
        max_len=200,
    )
    assert tips[0] == "Торгуйся в диапазоне 1200-1300 BYN"
    assert "jailbreak" in tips[1]


def test_clean_model_text_still_masks_contacts_and_caps_length() -> None:
    cleaned = clean_model_text("Звони  +375 29 123 45 67\nили t.me/seller", 200)
    assert "123 45 67" not in cleaned and "t.me" not in cleaned
    assert "\n" not in cleaned
    assert clean_model_text("a" * 50, 10) == "a" * 10


def test_strip_html_keeps_comparison_signs_in_prose() -> None:
    text = "цена <30% ниже, пробег >200 тыс."
    assert strip_html_tags(text) == text
    assert strip_html_tags("a <b>b</b> <img src=x onerror=y> c") == "a b  c"


# ── AI-13 / AI2-12: PII masking ───────────────────────────────────────


@pytest.mark.parametrize(
    "phone",
    [
        "+375 (29) 123-45-67",
        "+375(29)1234567",
        "8 (029) 123-45-67",
        "8(029)123 45 67",
        "(029) 123-45-67",
        "+375 29 123 45 67",
    ],
)
def test_belarus_phone_formats_are_masked(phone: str) -> None:
    for keep_ranges in (False, True):
        cleaned, hits = scrub_pii(f"Звоните {phone} вечером", keep_numeric_ranges=keep_ranges)
        assert cleaned == "Звоните [phone] вечером", (phone, keep_ranges)
        assert hits == 1


@pytest.mark.parametrize(
    "text",
    [
        "отдам за 1200-1500 BYN",
        "пробег 2019 150000 км",
        "Цена 750 BYN, торг до 700",
    ],
)
def test_seller_text_keeps_prices_and_mileage(text: str) -> None:
    assert sanitize_user_text(text) == text


def test_seller_text_still_masks_phones() -> None:
    cleaned = sanitize_user_text("пиши 8 (029) 123-45-67 или +375291234567")
    assert cleaned == "пиши [phone] или [phone]"


# ── AI2-03: best pick ─────────────────────────────────────────────────


def _alternatives() -> list[dict]:
    return [
        {"ad_id": 101, "title": "A", "price_byn": 1400.0, "deal_score": 40.0},
        {"ad_id": 102, "title": "B", "price_byn": 1650.0, "deal_score": 30.0},
    ]


def test_best_alternative_accepts_string_ad_id_from_model() -> None:
    decision = choose_best_alternative(
        _alternatives(), target_price=1500, is_negotiable_price=False, ai_best_pick_ad_id="102",
    )
    assert decision.item["ad_id"] == 102
    assert decision.reason == ""


@pytest.mark.parametrize("bad_id", [None, "", "abc", True, 0, 999])
def test_best_alternative_falls_back_with_own_reason(bad_id) -> None:
    decision = choose_best_alternative(
        _alternatives(), target_price=1500, is_negotiable_price=False, ai_best_pick_ad_id=bad_id,
    )
    assert decision.item is not None
    assert decision.reason


# ── AI2-06: condition section ─────────────────────────────────────────


def _complete(condition: dict) -> dict:
    return complete_analysis_sections(
        result={"condition": condition},
        title="iPhone 14",
        parameters=[],
        price_byn=1500,
        market_median=1590,
        best_alternative=None,
        risk_context=MarketplaceRiskContext(summary="", flags=[], score=0.0, hot_words=[]),
        photo_condition_label=None,
        photo_condition_notes=[],
        is_negotiable_price=False,
        red_flags=[],
    )["condition"]


def test_condition_notes_string_is_not_split_into_characters() -> None:
    condition = _complete({"label": "Хорошее", "notes": "Царапина на крышке"})
    assert condition["notes"] == ["Царапина на крышке"]


@pytest.mark.parametrize("raw,expected", [("высокая", 0.64), ("85%", 0.85), (0.9, 0.9)])
def test_condition_confidence_is_coerced_not_raised(raw, expected) -> None:
    condition = _complete({"label": "Хорошее", "notes": ["Корпус целый"], "confidence": raw})
    assert condition["confidence"] == expected


# ── AI2-09: listing tiers ─────────────────────────────────────────────


def test_tier_reordering_with_a_missing_tier_stays_monotonic() -> None:
    out = normalize_listing_pricing(
        {"market": {"price_byn": 1500}, "patient": {"price_byn": 1200}},
        market_median=None, market_q1=None, market_q3=None,
        market_min=None, market_max=None, market_count=0,
    )
    assert out["fast"]["price_byn"] == 1200
    assert out["market"]["price_byn"] == 1500
    assert out["patient"]["price_byn"] == 0


# ── AI2-11: fallback discount ─────────────────────────────────────────


def test_fallback_discount_never_exceeds_fifteen_percent() -> None:
    amount, _ = _price_anchor_discount(
        price_byn=30, market_median=40, best_alternative=None, is_negotiable_price=False,
    )
    assert amount is not None and amount <= 30 * 0.15
    amount, _ = _price_anchor_discount(
        price_byn=2000, market_median=2500, best_alternative=None, is_negotiable_price=False,
    )
    assert amount == 60


# ── AI2-13: images ────────────────────────────────────────────────────


def _png_bytes() -> bytes:
    from io import BytesIO

    from PIL import Image

    buf = BytesIO()
    Image.new("RGBA", (4, 4), (255, 0, 0, 255)).save(buf, format="PNG")
    return buf.getvalue()


async def test_image_payload_is_labelled_jpeg_after_compression(monkeypatch) -> None:
    import api.services.ai_images as ai_images

    async def fake_bytes(url, get_client):
        del url, get_client
        return _png_bytes()

    monkeypatch.setattr(ai_images, "fetch_image_bytes", fake_bytes)
    img = await fetch_image_b64("https://rms.kufar.by/v1/gallery/x.png", None)
    assert img["image_url"]["url"].startswith("data:image/jpeg;base64,")


async def test_undecodable_image_is_dropped_not_sent_raw(monkeypatch) -> None:
    import api.services.ai_images as ai_images

    async def fake_bytes(url, get_client):
        del url, get_client
        return b"<svg xmlns='http://www.w3.org/2000/svg'/>"

    monkeypatch.setattr(ai_images, "fetch_image_bytes", fake_bytes)
    assert await fetch_image_b64("https://rms.kufar.by/v1/gallery/x.svg", None) is None


def test_decompression_bomb_is_caught(monkeypatch) -> None:
    from PIL import Image

    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 2)
    assert compress_image(_png_bytes()) is None


# ── AI2-18: the model's own high scam risk ────────────────────────────


def test_model_high_scam_risk_blocks_worth_it() -> None:
    result, _ = enforce_risk_floor(
        {
            "recommendation": {"verdict": "worth_it", "text": "Бери"},
            "scam_analysis": {"risk_level": "high"},
        },
        floor=RiskFloor(),
        red_flags=[],
        title="iPhone 14",
        price_byn=1500,
        market_median=1590,
        best_alternative=None,
        is_negotiable_price=False,
    )
    assert result["recommendation"]["verdict"] == "think_twice"
    assert result["scam_analysis"]["risk_level"] == "high"


def test_low_scam_risk_without_floor_is_untouched() -> None:
    original = {"recommendation": {"verdict": "worth_it", "text": "Бери"},
                "scam_analysis": {"risk_level": "low"}}
    result, _ = enforce_risk_floor(
        original, floor=RiskFloor(), red_flags=[], title="x", price_byn=1,
        market_median=None, best_alternative=None, is_negotiable_price=False,
    )
    assert result is original


# ── AI2-07: recreated tasks stay user-scoped ──────────────────────────


async def test_recreated_task_lands_under_user_scoped_key() -> None:
    cache = MemoryCache()
    await _update_task(cache, "gone", user_id=4242, status="done", result={"x": 1})

    assert await cache.get_json(_task_cache_key("gone")) is None
    stored = await cache.get_json(_task_cache_key("gone", user_id=4242))
    assert stored["_telegram_user_id"] == 4242
    assert (await _get_task(cache, "gone", user_id=4242))["status"] == "done"


# ── AI2-15: price advice grounding ────────────────────────────────────


def test_price_advice_does_not_promote_suspiciously_cheap_listing() -> None:
    from api.routers.ai_tools import _ground_price_advice

    stats = SimpleNamespace(median=1000.0, q1=900.0, q3=1100.0, count=10)
    advice, reasoning, confidence = _ground_price_advice(
        advice="wait", reasoning="", confidence=0.8, current_price_byn=500, stats=stats,
    )
    assert advice == "neutral"
    assert "подозрительно" in reasoning
    assert confidence <= 0.5

    advice, _, _ = _ground_price_advice(
        advice="wait", reasoning="", confidence=0.8, current_price_byn=880, stats=stats,
    )
    assert advice == "buy_now"
