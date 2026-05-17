"""Tests for api.services.risk_detector — marketplace risk detection."""

from __future__ import annotations

import pytest

from api.services.risk_detector import compute_risk_score, detect_risks


def _ad(**overrides: object) -> dict:
    base = {
        "ad_id": 100,
        "subject": "iPhone 15",
        "price_byn": 200_000,
        "body": "Отличный телефон",
        "company_ad": False,
        "images": [{"url": "https://example.com/photo.jpg"}],
    }
    base.update(overrides)
    return base


class TestDetectRisksNoRisks:
    def test_clean_ad_no_market_stats(self) -> None:
        risks = detect_risks(_ad())
        assert risks == []

    def test_clean_ad_with_market_stats(self) -> None:
        risks = detect_risks(_ad(), market_stats={"median": 2000})
        assert risks == []

    def test_empty_body(self) -> None:
        risks = detect_risks(_ad(body=""))
        assert risks == []

    def test_none_body_and_body_short(self) -> None:
        risks = detect_risks(_ad(body=None, body_short=None))
        assert risks == []


class TestDetectRisksTooCheap:
    def test_price_way_below_median(self) -> None:
        risks = detect_risks(_ad(price_byn=100_000), market_stats={"median": 2000})
        assert any(r["type"] == "too_cheap" for r in risks)

    def test_price_slightly_below_median_not_flagged(self) -> None:
        risks = detect_risks(_ad(price_byn=1_500_000), market_stats={"median": 2000})
        assert not any(r["type"] == "too_cheap" for r in risks)

    def test_price_above_median_not_flagged(self) -> None:
        risks = detect_risks(_ad(price_byn=3_000_000), market_stats={"median": 2000})
        assert not any(r["type"] == "too_cheap" for r in risks)

    def test_zero_price_not_flagged(self) -> None:
        risks = detect_risks(_ad(price_byn=0), market_stats={"median": 2000})
        assert not any(r["type"] == "too_cheap" for r in risks)

    def test_zero_median_not_flagged(self) -> None:
        risks = detect_risks(_ad(price_byn=100_000), market_stats={"median": 0})
        assert not any(r["type"] == "too_cheap" for r in risks)

    def test_negative_median_not_flagged(self) -> None:
        risks = detect_risks(_ad(price_byn=100_000), market_stats={"median": -100})
        assert not any(r["type"] == "too_cheap" for r in risks)

    def test_missing_median_key_not_flagged(self) -> None:
        risks = detect_risks(_ad(price_byn=100_000), market_stats={})
        assert not any(r["type"] == "too_cheap" for r in risks)

    def test_too_cheap_risk_is_high_level(self) -> None:
        risks = detect_risks(_ad(price_byn=100_000), market_stats={"median": 2000})
        tc = [r for r in risks if r["type"] == "too_cheap"]
        assert len(tc) == 1
        assert tc[0]["level"] == "high"


class TestDetectRisksSuspiciousDesc:
    @pytest.mark.parametrize(
        "word",
        [
            "предоплата",
            "на карту",
            "аванс",
            "задаток",
            "переведи деньги",
            "webmoney",
            "qiwi",
            "киви",
        ],
    )
    def test_hot_words_russian(self, word: str) -> None:
        risks = detect_risks(_ad(body=f"Нормальный текст {word} ещё текст"))
        assert any(r["type"] == "suspicious_desc" for r in risks)

    def test_suspicious_desc_is_medium_level(self) -> None:
        risks = detect_risks(_ad(body="предоплата"))
        sd = [r for r in risks if r["type"] == "suspicious_desc"]
        assert len(sd) == 1
        assert sd[0]["level"] == "medium"

    def test_body_short_used_when_body_missing(self) -> None:
        risks = detect_risks(_ad(body="", body_short="предоплата"))
        assert any(r["type"] == "suspicious_desc" for r in risks)

    def test_body_preferred_over_body_short(self) -> None:
        risks = detect_risks(_ad(body="предоплата", body_short="чистый текст"))
        assert any(r["type"] == "suspicious_desc" for r in risks)

    def test_clean_description_no_flag(self) -> None:
        risks = detect_risks(_ad(body="Продаю телефон в отличном состоянии"))
        assert not any(r["type"] == "suspicious_desc" for r in risks)

    def test_non_string_body_no_flag(self) -> None:
        risks = detect_risks(_ad(body=12345))
        assert not any(r["type"] == "suspicious_desc" for r in risks)

    def test_mixed_russian_english_no_false_positive(self) -> None:
        risks = detect_risks(_ad(body="Good condition iPhone, коробка и чек"))
        assert not any(r["type"] == "suspicious_desc" for r in risks)

    def test_mixed_languages_with_hot_word(self) -> None:
        risks = detect_risks(_ad(body="Great deal, предоплата required, call me"))
        assert any(r["type"] == "suspicious_desc" for r in risks)

    def test_very_long_description_with_hot_word(self) -> None:
        long_body = "Слово " * 10_000 + " предоплата " + " ещён " * 10_000
        risks = detect_risks(_ad(body=long_body))
        assert any(r["type"] == "suspicious_desc" for r in risks)


class TestDetectRisksDuplicateRemoved:
    """E-FIND-04: the seller_info/_check_duplicate path was dead code
    in production (no caller passed seller_info). Removed in the
    May-2026 audit. A real duplicate detector based on Listing
    history is tracked as a separate feature."""

    def test_seller_info_argument_no_longer_accepted(self) -> None:
        # Regression: callers passing the old kwarg now get a TypeError
        # so we don't silently accept-and-ignore the parameter.
        import pytest as _pytest
        with _pytest.raises(TypeError):
            detect_risks(_ad(), seller_info={"is_duplicate": True})  # type: ignore[call-arg]

    def test_no_duplicate_risk_emitted(self) -> None:
        risks = detect_risks(_ad())
        assert not any(r["type"] == "duplicate" for r in risks)


class TestDetectRisksMultiple:
    def test_too_cheap_and_suspicious_desc(self) -> None:
        risks = detect_risks(
            _ad(price_byn=100_000, body="предоплата на карту"),
            market_stats={"median": 2000},
        )
        types = {r["type"] for r in risks}
        assert "too_cheap" in types
        assert "suspicious_desc" in types

    def test_all_two_remaining_risks(self) -> None:
        # E-FIND-04: ``duplicate`` removed; the remaining priced risks
        # (too_cheap, suspicious_desc) still co-fire on the same ad.
        risks = detect_risks(
            _ad(price_byn=100_000, body="предоплата"),
            market_stats={"median": 2000},
        )
        types = {r["type"] for r in risks}
        assert "too_cheap" in types
        assert "suspicious_desc" in types


class TestComputeRiskScore:
    def test_no_risks_is_low(self) -> None:
        assert compute_risk_score([]) == "low"

    def test_medium_only(self) -> None:
        risks = [{"type": "suspicious_desc", "level": "medium", "message": "x"}]
        assert compute_risk_score(risks) == "medium"

    def test_high_present(self) -> None:
        risks = [
            {"type": "too_cheap", "level": "high", "message": "x"},
            {"type": "suspicious_desc", "level": "medium", "message": "y"},
        ]
        assert compute_risk_score(risks) == "high"

    def test_multiple_high(self) -> None:
        risks = [
            {"type": "too_cheap", "level": "high", "message": "x"},
            {"type": "duplicate", "level": "high", "message": "y"},
        ]
        assert compute_risk_score(risks) == "high"


class TestDetectRisksNoPhotos:
    """E-FIND-03: zero-photo listings flagged as medium risk."""

    def test_no_images_key_flagged(self) -> None:
        ad = _ad()
        del ad["images"]
        risks = detect_risks(ad)
        assert any(r["type"] == "no_photos" for r in risks)

    def test_empty_images_list_flagged(self) -> None:
        risks = detect_risks(_ad(images=[]))
        assert any(r["type"] == "no_photos" for r in risks)

    def test_with_images_not_flagged(self) -> None:
        risks = detect_risks(_ad(images=[{"url": "https://example.com/1.jpg"}]))
        assert not any(r["type"] == "no_photos" for r in risks)

    def test_image_count_nonzero_not_flagged(self) -> None:
        ad = _ad()
        del ad["images"]
        ad["image_count"] = 3
        risks = detect_risks(ad)
        assert not any(r["type"] == "no_photos" for r in risks)

    def test_no_photos_is_medium_level(self) -> None:
        risks = detect_risks(_ad(images=[]))
        np = [r for r in risks if r["type"] == "no_photos"]
        assert len(np) == 1
        assert np[0]["level"] == "medium"
