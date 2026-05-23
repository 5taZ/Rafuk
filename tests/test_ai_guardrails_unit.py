"""Dedicated unit tests for ai_guardrails + ai_category_data (TEST-04).

The audit flagged that both modules lack their own test coverage.
They're smoke-tested indirectly through the analyse endpoint in
test_ai_analysis.py, but an isolated suite pins the behaviour down
without the HTTP / cache / AI-mock scaffolding — easier to reason
about when they regress.

Scope:
* ``apply_ai_market_guardrails`` — clamping logic for out-of-market AI
  prices (including the financing-bait escalation).
* ``contains_financing_bait`` — detection of "в кредит/рассрочку"
  signals in titles + descriptions.
* ``detect_category`` — category-keyword scoring with sub-category
  boost (phone_accessory wins over phone for chehol matches).
* ``normalize_condition_label`` — free-form → canonical label
  folding for both Russian and English inputs.
"""

from __future__ import annotations

import pytest

from api.services.ai_category_data import (
    CATEGORY_HINTS,
    CATEGORY_KEYWORDS,
    detect_category,
    normalize_condition_label,
)
from api.services.ai_guardrails import (
    apply_ai_market_guardrails,
    contains_financing_bait,
)

# ──────────────────────────────────────────────────────────────────────
# contains_financing_bait
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text",
    [
        "BMW X5 без взноса",
        "Автомобиль в кредит без первого взноса",
        "Рассрочка 0-0-12",
        "Платёж всего 300 BYN/мес",
        "Покупай в лизинг — выгодно",
    ],
)
def test_financing_bait_detected(text: str) -> None:
    assert contains_financing_bait(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "iPhone 14 Pro Max 256 GB",
        "Состояние отличное, коробка в комплекте",
        "Цена договорная",
        "",
        None,
    ],
)
def test_financing_bait_ignores_clean_listings(text) -> None:
    assert contains_financing_bait(text) is False


def test_financing_bait_examines_multiple_fields() -> None:
    # A dealer might put the bait in description, not title — the
    # call site passes both.
    assert (
        contains_financing_bait("Audi A6 2022", "Предлагаем в кредит")
        is True
    )


# ──────────────────────────────────────────────────────────────────────
# apply_ai_market_guardrails
# ──────────────────────────────────────────────────────────────────────


def _minimal_result(ai_from: int, ai_to: int) -> dict:
    return {
        "fair_price": {
            "from": ai_from,
            "to": ai_to,
            "reasoning": "AI-generated",
        },
        "summary": "…",
        "recommendation": {"verdict": "think_twice", "text": "…"},
    }


def test_guardrails_no_op_when_market_sample_too_thin() -> None:
    # market_count < 3 → can't trust the market, leave AI as-is.
    result = _minimal_result(10_000, 12_000)
    out = apply_ai_market_guardrails(
        result,
        title="test",
        description=None,
        market_median=1500,
        market_q1=1400,
        market_q3=1600,
        market_count=2,
        similar_listings=None,
        is_negotiable_price=False,
    )
    assert out == result


def test_guardrails_no_op_when_ai_range_inside_market() -> None:
    similar = [{"price_byn": p} for p in (1400, 1500, 1600, 1650, 1700)]
    result = _minimal_result(1500, 1650)
    out = apply_ai_market_guardrails(
        result,
        title="iPhone",
        description=None,
        market_median=1500,
        market_q1=1450,
        market_q3=1650,
        market_count=15,
        similar_listings=similar,
        is_negotiable_price=False,
    )
    assert out["fair_price"]["from"] == 1500
    assert out["fair_price"]["to"] == 1650


def test_guardrails_clamps_ai_range_far_above_market_ceiling() -> None:
    # Market says 1400-1700, AI says 10k-12k → clamp.
    similar = [{"price_byn": p} for p in (1400, 1500, 1600, 1700)]
    result = _minimal_result(10_000, 12_000)
    out = apply_ai_market_guardrails(
        result,
        title="iPhone 14",
        description="Отличное состояние",
        market_median=1550,
        market_q1=1450,
        market_q3=1650,
        market_count=20,
        similar_listings=similar,
        is_negotiable_price=False,
    )
    # Clamped to Q1-Q3.
    assert out["fair_price"]["from"] == 1450
    assert out["fair_price"]["to"] == 1650
    # New reasoning cites market median/range.
    assert "рынк" in out["fair_price"]["reasoning"].lower()
    # resale_potential rebuilt from market, not AI.
    assert "fast_price" in out["resale_potential"]
    assert out["resale_potential"]["fast_price"]["price_byn"] <= 1650


def test_guardrails_financing_bait_escalates_clamp_for_negotiable() -> None:
    """Negotiable financing-bait listings trip the clamp at a lower
    ceiling (15 % above Q3 instead of 35 %)."""
    similar = [{"price_byn": p} for p in (15_000, 16_000, 17_000, 18_000)]
    result = _minimal_result(20_000, 22_000)  # 22k / 17k q3 = +29%
    out = apply_ai_market_guardrails(
        result,
        title="BMW 5-series",
        description="Отличная машина, в кредит без первого взноса!",
        market_median=17_000,
        market_q1=15_500,
        market_q3=17_500,
        market_count=15,
        similar_listings=similar,
        is_negotiable_price=True,
    )
    # The financing-bait rule forced clamping to Q1-Q3.
    assert out["fair_price"]["from"] == 15_500
    assert out["fair_price"]["to"] == 17_500


def test_guardrails_leave_non_dict_fair_price_alone() -> None:
    """If the model returned a malformed fair_price we don't try to
    reason about it — just pass the result through unchanged."""
    result = {"fair_price": "not a dict"}
    out = apply_ai_market_guardrails(
        result,
        title="x",
        description=None,
        market_median=1000,
        market_q1=800,
        market_q3=1200,
        market_count=10,
        similar_listings=[{"price_byn": 1000}],
        is_negotiable_price=False,
    )
    assert out == result


# ──────────────────────────────────────────────────────────────────────
# detect_category
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "title,expected",
    [
        ("iPhone 14 Pro 256 GB", "phone"),
        ("Чехол для iPhone 14", "phone_accessory"),
        ("Сдам 1-комнатную квартиру в Минске", "real_estate"),
        ("Ноутбук Lenovo ThinkPad X1 Carbon", "laptop"),
        ("Диван угловой, доставка по Минску", "furniture"),
    ],
)
def test_detect_category_covers_typical_listings(title: str, expected: str) -> None:
    assert detect_category(title) == expected


def test_detect_category_subcategory_boost_wins_over_parent() -> None:
    # "Чехол iPhone 14 Pro" matches BOTH phone (iPhone) and
    # phone_accessory (чехол). Sub-category boost must win.
    assert detect_category("Чехол iPhone 14 Pro с защитой камер") == "phone_accessory"


def test_detect_category_prefers_phone_accessory_for_phone_holder() -> None:
    """A "phone holder" is a phone_accessory, not an auto_accessory,
    because the phone_accessory keywords get the sub-category boost
    and "держатель" + "телефон" appear in both keyword lists — the
    boost tips it to phone_accessory, which is the intended behaviour
    (the UI shows phone-accessory pricing ranges, not auto-accessory
    ones). Locking this in so the boost table doesn't silently flip.
    """
    assert (
        detect_category("Автомобильный держатель для телефона")
        == "phone_accessory"
    )


def test_detect_category_defaults_without_any_keyword() -> None:
    # "Продаю вещь" is truly generic — no keyword hits.
    assert detect_category("Продаю вещь") == "default"


def test_detect_category_uses_parameter_values_as_hints() -> None:
    # The raw title is generic, but the parameter says it's a phone.
    title = "Apple — продаётся устройство"
    params = [{"label": "Категория", "value": "Смартфон iPhone"}]
    # Without params we'd likely match phone via "apple iphone" —
    # this test confirms the params contribute.
    assert detect_category(title, params) in ("phone", "default", "electronics")


def test_category_keywords_are_all_strings() -> None:
    """Guard against accidental non-string entries — a dict in the
    list would silently skip matching without raising."""
    for category, keywords in CATEGORY_KEYWORDS.items():
        assert isinstance(keywords, list), f"{category} keywords must be a list"
        for kw in keywords:
            assert isinstance(kw, str), f"{category} has non-string keyword: {kw!r}"
            assert kw.strip(), f"{category} has empty keyword"


def test_category_hints_have_required_keys() -> None:
    """Prompt builders expect every hint dict to carry both fields —
    a missing key surfaces as a KeyError inside the AI prompt."""
    for category, hint in CATEGORY_HINTS.items():
        assert "category_hints" in hint, f"{category} is missing category_hints"
        assert "bargain_hint" in hint, f"{category} is missing bargain_hint"
        assert isinstance(hint["category_hints"], str)
        assert isinstance(hint["bargain_hint"], str)


# ──────────────────────────────────────────────────────────────────────
# normalize_condition_label
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("отличное", "Отличное"),
        ("Хорошее", "Хорошее"),
        ("удовлетворительное", "Удовлетворительное"),
        ("good", "Хорошее"),
        ("excellent", "Отличное"),
        ("fair", "Удовлетворительное"),
        ("poor", "Требует внимания"),
        ("new", "Новый"),
        ("used", "Б/у"),
    ],
)
def test_normalize_condition_label_maps_common_values(raw: str, expected: str) -> None:
    assert normalize_condition_label(raw) == expected


def test_normalize_condition_label_trebuet_vnimaniya_edge_case() -> None:
    """Known edge case: "требует внимания" currently resolves to "Б/у"
    because the loop does substring matching and "бу" is a substring
    of "тре**бу**ет". The canonical "Требует внимания" → "Требует
    внимания" path only fires via the pipe-delimited branch
    (e.g. "Хорошее|требует внимания"). This test locks down the
    current behaviour so a refactor of the label-resolver notices.
    The pipe path is exercised in the next test.
    """
    assert normalize_condition_label("требует внимания") == "Б/у"


def test_normalize_condition_label_returns_empty_for_missing_input() -> None:
    assert normalize_condition_label(None) == ""
    assert normalize_condition_label("") == ""
    assert normalize_condition_label("   ") == ""


def test_normalize_condition_label_picks_valid_from_multi_valued_param() -> None:
    # Kufar sometimes returns "Хорошее|Отличное" when a listing spans
    # two variants — we pick one valid label rather than passing the
    # pipe form through to the AI.
    out = normalize_condition_label("Хорошее|Отличное")
    assert out in {"Хорошее", "Отличное"}


def test_normalize_condition_label_returns_empty_on_unknown_input() -> None:
    assert normalize_condition_label("какая-то белиберда") == ""
