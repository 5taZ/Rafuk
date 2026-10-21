from __future__ import annotations

from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from api.schemas import TrackerCreate
from api.services.aggregator import PriceStats
from api.services.listing_filters import match_condition_filter, warn_unknown_filter_value
from api.services.reseller_tools import (
    SCORING,
    analyze_query_text,
    compute_deal_score,
    config_keyword_matches,
    default_config_keyword,
    matches_tracker_filters,
)

_INDEX_HTML = Path(__file__).resolve().parent.parent / "frontend" / "index.html"


def test_analyze_query_text_extracts_spaced_ram_and_storage() -> None:
    insights = analyze_query_text("macbook air m1 8gb 256gb")
    assert insights.ram_gb == 8
    assert insights.storage_gb == 256
    assert insights.config_summary == "Air 8/256"


def test_analyze_query_text_handles_console_aliases() -> None:
    insights = analyze_query_text("пс5 слим 1 тб")
    assert insights.normalized_query == "ps5 slim 1024"
    assert insights.storage_gb == 1024
    assert insights.config_summary == "Slim 1024GB"


def _make_stats(median: float = 1000.0, **kw) -> PriceStats:
    return PriceStats(
        mean=kw.get("mean", median),
        median=median,
        count=kw.get("count", 20),
        q1=kw.get("q1", median * 0.9),
        q3=kw.get("q3", median * 1.1),
        min=kw.get("min", median * 0.7),
        max=kw.get("max", median * 1.4),
    )


def _make_ad(
    price_byn: float = 100000,
    subject: str = "iPhone 15 128GB",
    seller_type: str | None = "Частное лицо",
    company_ad: bool = False,
    list_time: str | None = None,
    condition: str | None = None,
    region_name: str | None = None,
) -> dict:
    params = []
    if seller_type:
        params.append({"p": "seller_type", "v": seller_type})
    if condition:
        params.append({"p": "condition", "v": condition})
    return {
        "subject": subject,
        "price_byn": price_byn,
        "company_ad": company_ad,
        "ad_parameters": params,
        "list_time": list_time,
    }


def test_compute_deal_score_verdict_good_price() -> None:
    ad = _make_ad(price_byn=80000)
    stats = _make_stats(median=1000.0)
    result = compute_deal_score(ad, query="iphone 15", market_stats=stats)
    assert result.verdict == "Хорошая цена"
    assert result.score > SCORING.base_score


def test_compute_deal_score_verdict_above_market() -> None:
    ad = _make_ad(price_byn=120000)
    stats = _make_stats(median=1000.0)
    result = compute_deal_score(ad, query="iphone 15", market_stats=stats)
    assert result.verdict == "Выше рынка"
    assert result.score < SCORING.base_score


def test_compute_deal_score_verdict_fair_market() -> None:
    ad = _make_ad(price_byn=101000)
    stats = _make_stats(median=1000.0)
    result = compute_deal_score(ad, query="iphone 15", market_stats=stats)
    assert result.verdict == "Средняя цена"


def test_compute_deal_score_verdict_below_market() -> None:
    ad = _make_ad(price_byn=97000)
    stats = _make_stats(median=1000.0)
    result = compute_deal_score(ad, query="iphone 15", market_stats=stats)
    assert result.verdict == "Ниже рынка"


def test_compute_deal_score_private_seller_bonus() -> None:
    ad_private = _make_ad(price_byn=100000, seller_type="Частное лицо")
    ad_shop = _make_ad(price_byn=100000, seller_type="Магазин")
    stats = _make_stats(median=1000.0)
    private_result = compute_deal_score(ad_private, query="iphone 15", market_stats=stats)
    shop_result = compute_deal_score(ad_shop, query="iphone 15", market_stats=stats)
    assert private_result.score > shop_result.score


def test_compute_deal_score_anomaly_penalty() -> None:
    from unittest.mock import patch

    ad = _make_ad(price_byn=100000)
    stats = _make_stats(median=1000.0)
    with patch(
        "api.services.reseller_tools.detect_anomaly_flags",
        return_value=["anomaly1", "anomaly2"],
    ):
        result = compute_deal_score(ad, query="iphone 15", market_stats=stats)
    assert "есть аномалии" in result.reasons
    assert result.score < SCORING.base_score


def test_compute_deal_score_score_clamped_0_100() -> None:
    ad = _make_ad(price_byn=100000)
    stats = _make_stats(median=1000.0)
    result = compute_deal_score(ad, query="iphone 15", market_stats=stats)
    assert 0.0 <= result.score <= 100.0


def test_compute_deal_score_reasons_deduped_and_capped() -> None:
    ad = _make_ad(price_byn=100000)
    stats = _make_stats(median=1000.0)
    result = compute_deal_score(ad, query="iphone 15", market_stats=stats)
    assert len(result.reasons) <= 4
    assert len(result.reasons) == len(set(result.reasons))


def test_matches_tracker_filters_no_filters_passes() -> None:
    ad = _make_ad(price_byn=100000)
    stats = _make_stats(median=1000.0)
    assert matches_tracker_filters(ad, market_stats=stats) is True


def test_matches_tracker_filters_max_price() -> None:
    ad = _make_ad(price_byn=120000)
    stats = _make_stats(median=1000.0)
    assert matches_tracker_filters(ad, market_stats=stats, max_price_byn=1100.0) is False
    assert matches_tracker_filters(ad, market_stats=stats, max_price_byn=1300.0) is True


def test_matches_tracker_filters_seller_type_shop() -> None:
    ad_shop = _make_ad(price_byn=100000, seller_type="Магазин", company_ad=True)
    ad_private = _make_ad(price_byn=100000, seller_type="Частное лицо")
    stats = _make_stats(median=1000.0)
    assert matches_tracker_filters(ad_shop, market_stats=stats, seller_type="shop") is True
    assert matches_tracker_filters(ad_private, market_stats=stats, seller_type="shop") is False


def test_matches_tracker_filters_seller_type_private() -> None:
    ad_private = _make_ad(price_byn=100000, seller_type="Частное лицо")
    ad_shop = _make_ad(price_byn=100000, seller_type="Магазин", company_ad=True)
    stats = _make_stats(median=1000.0)
    assert matches_tracker_filters(ad_private, market_stats=stats, seller_type="private") is True
    assert matches_tracker_filters(ad_shop, market_stats=stats, seller_type="private") is False


def test_matches_tracker_filters_condition() -> None:
    ad_new = _make_ad(price_byn=100000, condition="2")
    ad_used = _make_ad(price_byn=100000, condition="1")
    stats = _make_stats(median=1000.0)
    assert matches_tracker_filters(ad_new, market_stats=stats, condition="new") is True
    assert matches_tracker_filters(ad_used, market_stats=stats, condition="new") is False
    assert matches_tracker_filters(ad_used, market_stats=stats, condition="used") is True


def test_matches_tracker_filters_config_keyword() -> None:
    ad_match = _make_ad(price_byn=100000, subject="iPhone 15 Pro Max 256GB")
    ad_no_match = _make_ad(price_byn=100000, subject="Samsung Galaxy S25")
    stats = _make_stats(median=1000.0)
    assert (
        matches_tracker_filters(ad_match, market_stats=stats, config_keyword="iphone 15 pro")
        is True
    )
    assert (
        matches_tracker_filters(ad_no_match, market_stats=stats, config_keyword="iphone 15 pro")
        is False
    )


def test_matches_tracker_filters_combined_filters() -> None:
    ad = _make_ad(price_byn=90000, subject="iPhone 15 128GB", condition="1")
    stats = _make_stats(median=1000.0)
    assert (
        matches_tracker_filters(
            ad,
            market_stats=stats,
            max_price_byn=1000.0,
            condition="used",
            config_keyword="iphone 15",
        )
        is True
    )
    assert (
        matches_tracker_filters(
            ad,
            market_stats=stats,
            max_price_byn=800.0,
        )
        is False
    )


class TestWave2TrackerFilterMatching:
    """D-1 / D-2: matches_tracker_filters used to accept only numeric
    ``condition`` codes and exact-case ``region_name``. With the
    shared helpers in ``api.services.listing_filters`` it now mirrors
    the behaviour of ``/listings`` local filtering — Kufar's text
    labels and case variations no longer drop legitimate matches.
    """

    @staticmethod
    def _market_stats() -> PriceStats:
        return PriceStats(
            count=10, mean=100.0, median=100.0,
            q1=80.0, q3=120.0, min=50.0, max=200.0,
        )

    def test_condition_text_label_matches_new(self) -> None:
        # Kufar returned 'Новый' as a text label — the old map only
        # accepted '2' so the tracker silently dropped this match.
        ad = {
            "subject": "Новый телефон",
            "ad_parameters": [{"p": "condition", "v": "Новый"}],
            "price_byn": 50_000,  # 500 BYN
        }
        assert matches_tracker_filters(
            ad,
            market_stats=self._market_stats(),
            condition="new",
        )

    def test_condition_text_label_b_u_matches_used(self) -> None:
        ad = {
            "subject": "Б/у телефон",
            "ad_parameters": [{"p": "condition", "v": "Б/у"}],
            "price_byn": 50_000,
        }
        assert matches_tracker_filters(
            ad,
            market_stats=self._market_stats(),
            condition="used",
        )

    def test_condition_numeric_code_still_matches(self) -> None:
        # Regression: numeric codes must still work.
        ad = {
            "subject": "Phone",
            "ad_parameters": [{"p": "condition", "v": "2"}],
            "price_byn": 50_000,
        }
        assert matches_tracker_filters(
            ad,
            market_stats=self._market_stats(),
            condition="new",
        )

    def test_condition_mismatch_rejected(self) -> None:
        ad = {
            "subject": "Б/у",
            "ad_parameters": [{"p": "condition", "v": "1"}],
            "price_byn": 50_000,
        }
        assert not matches_tracker_filters(
            ad,
            market_stats=self._market_stats(),
            condition="new",
        )

    def test_region_match_is_case_insensitive(self) -> None:
        # Tracker stored canonical 'Минск'; Kufar returns 'минск'.
        # Old exact-match ``!=`` rejected the ad even though regions
        # are the same.
        ad = {
            "subject": "iPhone",
            "ad_parameters": [],
            "region_name": "минск",
            "price_byn": 50_000,
        }
        assert matches_tracker_filters(
            ad,
            market_stats=self._market_stats(),
            region_name="Минск",
        )

    def test_region_match_strips_whitespace(self) -> None:
        ad = {
            "subject": "Item",
            "region_name": "  Брест  ",
            "ad_parameters": [],
            "price_byn": 50_000,
        }
        assert matches_tracker_filters(
            ad,
            market_stats=self._market_stats(),
            region_name="Брест",
        )

    def test_region_falls_back_to_area(self) -> None:
        ad = {
            "subject": "Item",
            "area_name": "Гомельская обл.",
            "ad_parameters": [],
            "price_byn": 50_000,
        }
        assert matches_tracker_filters(
            ad,
            market_stats=self._market_stats(),
            region_name="гомельская обл.",
        )

    def test_region_mismatch_still_rejected(self) -> None:
        ad = {
            "subject": "Item",
            "region_name": "Витебск",
            "ad_parameters": [],
            "price_byn": 50_000,
        }
        assert not matches_tracker_filters(
            ad,
            market_stats=self._market_stats(),
            region_name="Минск",
        )



def test_compute_deal_score_negotiable_returns_dogovornaya_verdict() -> None:
    """B-09: negotiable ad gets verdict='Цена договорная', no discount adjustment."""
    ad = _make_ad(price_byn=0, seller_type="Другое", company_ad=True)
    ad["body"] = "Цена договорная"
    ad["subject"] = "Разное"
    stats = _make_stats(median=1000.0)
    result = compute_deal_score(ad, query="xyz123", market_stats=stats)
    assert result.verdict == "Цена договорная"
    # No discount adjustment applied — score starts at base_score.
    # Seller type "Другое" is neither private nor shop, so no seller bonus.
    # Query "xyz123" won't match subject "Разное" for profile bonus.
    assert result.score == SCORING.base_score


def test_matches_tracker_filters_negotiable_with_min_discount_returns_false() -> None:
    """B-09: negotiable ad cannot satisfy min_discount_percent filter."""
    ad = _make_ad(price_byn=0)
    ad["body"] = "Цена договорная"
    stats = _make_stats(median=1000.0)
    assert matches_tracker_filters(ad, market_stats=stats, min_discount_percent=5.0) is False


# ---------------------------------------------------------------------------
# RV-BE-03: the tracker form's "Продавец"/"Состояние" values must filter
# ---------------------------------------------------------------------------

# What each <option value> of the tracker forms means. If the Mini App
# changes these values, the test below fails instead of the filter
# silently turning into a no-op again.
_UI_SELLER_MEANING = {"Частное лицо": "private", "Магазин": "shop"}
_UI_CONDITION_MEANING = {"Новый": "new", "Б/у": "used"}


def _ui_option_values(*select_ids: str) -> set[str]:
    soup = BeautifulSoup(_INDEX_HTML.read_text(encoding="utf-8"), "html.parser")
    values: set[str] = set()
    for select_id in select_ids:
        select = soup.find("select", id=select_id)
        assert select is not None, select_id
        values |= {
            option.get("value", "") for option in select.find_all("option")
        } - {""}
    return values


def _kufar_ad(*, shop: bool, condition_code: str) -> dict:
    # Real Kufar ads carry the seller as ``company_ad`` and the condition
    # as a numeric code ("1" = б/у, "2" = новый).
    return {
        "subject": "iPhone 15 128GB",
        "price_byn": 100000,
        "company_ad": shop,
        "ad_parameters": [{"p": "condition", "v": condition_code}],
    }


_ADS = {
    ("private", "new"): _kufar_ad(shop=False, condition_code="2"),
    ("private", "used"): _kufar_ad(shop=False, condition_code="1"),
    ("shop", "new"): _kufar_ad(shop=True, condition_code="2"),
    ("shop", "used"): _kufar_ad(shop=True, condition_code="1"),
}


def test_ui_seller_and_condition_values_reach_matches_tracker_filters() -> None:
    """RV-BE-03: the tracker form posts "Частное лицо"/"Магазин" and
    "Новый"/"Б/у" verbatim; the matcher only knew "private"/"shop" and
    "new"/"used", so a "private + new" tracker alerted on used shop lots.
    Values are read from frontend/index.html, pushed through the create
    schema like the API does, and then matched."""
    seller_values = _ui_option_values("tracker-seller-select", "edit-seller-select")
    condition_values = _ui_option_values("tracker-condition-select", "edit-condition-select")
    assert seller_values == set(_UI_SELLER_MEANING)
    assert condition_values == set(_UI_CONDITION_MEANING)

    stats = _make_stats(median=1000.0)
    for seller_value in sorted(seller_values):
        for condition_value in sorted(condition_values):
            tracker = TrackerCreate(
                query="iphone 15", seller_type=seller_value, condition=condition_value,
            )
            # Stored as sent, so the form can preselect the option again.
            assert (tracker.seller_type, tracker.condition) == (seller_value, condition_value)
            wanted = (_UI_SELLER_MEANING[seller_value], _UI_CONDITION_MEANING[condition_value])
            matched = {
                key
                for key, ad in _ADS.items()
                if matches_tracker_filters(
                    ad,
                    market_stats=stats,
                    seller_type=tracker.seller_type,
                    condition=tracker.condition,
                )
            }
            assert matched == {wanted}, (seller_value, condition_value, matched)


@pytest.mark.parametrize(
    ("seller_type", "expected"),
    [
        ("private", "private"),
        ("Частное лицо", "private"),
        ("  частное   ЛИЦО ", "private"),
        ("SHOP", "shop"),
        ("магазин", "shop"),
    ],
)
def test_seller_filter_accepts_codes_and_labels_in_any_case(
    seller_type: str, expected: str,
) -> None:
    stats = _make_stats(median=1000.0)
    matched = {
        key[0]
        for key, ad in _ADS.items()
        if matches_tracker_filters(ad, market_stats=stats, seller_type=seller_type)
    }
    assert matched == {expected}


@pytest.mark.parametrize(
    ("condition", "expected"),
    [("new", "new"), ("Новый", "new"), (" НОВЫЙ ", "new"), ("used", "used"), ("б/у", "used")],
)
def test_condition_filter_accepts_codes_and_labels_in_any_case(
    condition: str, expected: str,
) -> None:
    matched = {key[1] for key, ad in _ADS.items() if match_condition_filter(ad, condition)}
    assert matched == {expected}


def test_condition_filter_label_matches_text_labelled_ads() -> None:
    ad_new = {"subject": "x", "ad_parameters": [{"p": "condition", "v": "Новый"}]}
    ad_used = {"subject": "x", "ad_parameters": [{"p": "condition", "v": "Б/у"}]}
    assert match_condition_filter(ad_new, "Новый") is True
    assert match_condition_filter(ad_used, "Новый") is False
    assert match_condition_filter(ad_used, "Б/у") is True


def test_unknown_filter_values_fail_closed_and_warn_once(caplog) -> None:
    """RV-BE-03: an unknown value used to disable the filter (everything
    matched). Legacy junk now matches nothing and is logged once."""
    import logging

    warn_unknown_filter_value.cache_clear()
    stats = _make_stats(median=1000.0)
    caplog.set_level(logging.WARNING, logger="api.services.listing_filters")
    for ad in _ADS.values():
        assert matches_tracker_filters(ad, market_stats=stats, seller_type="дилер") is False
        assert matches_tracker_filters(ad, market_stats=stats, condition="как новый") is False
        assert match_condition_filter(ad, "как новый") is False

    warnings = [rec.getMessage() for rec in caplog.records]
    assert sum("seller_type='дилер'" in msg for msg in warnings) == 1
    assert sum("condition='как новый'" in msg for msg in warnings) == 1


def test_blank_filters_mean_no_filter() -> None:
    stats = _make_stats(median=1000.0)
    for ad in _ADS.values():
        assert matches_tracker_filters(ad, market_stats=stats, seller_type="", condition="  ")


# ---------------------------------------------------------------------------
# RV-BE-04: config_keyword matches a token set, not a substring
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("title", "keyword"),
    [
        ("Samsung Galaxy S23 8/256", "samsung s23"),
        ("MacBook Air 13 M2", "macbook air m2"),
        ("iPhone 13 Pro Max 256GB", "pro max 256"),
        ("MacBook Air M1 8GB/256GB", "m1 8 256"),
        ("Galaxy S23 Samsung", "S23 SAMSUNG"),
    ],
)
def test_config_keyword_matches_tokens_in_any_order(title: str, keyword: str) -> None:
    """RV-BE-04: the old substring check rejected all of these."""
    assert config_keyword_matches(title, keyword) is True


@pytest.mark.parametrize(
    ("title", "keyword"),
    [
        ("Samsung Galaxy S22 8/256", "samsung s23"),
        ("MacBook Pro 14 M2", "macbook air m2"),
        ("iPhone 13 Pro 256GB", "pro max 256"),
    ],
)
def test_config_keyword_still_requires_every_token(title: str, keyword: str) -> None:
    assert config_keyword_matches(title, keyword) is False


def test_config_keyword_blank_means_no_filter() -> None:
    assert config_keyword_matches("Anything", None) is True
    assert config_keyword_matches("Anything", "") is True
    assert config_keyword_matches("Anything", " / ") is True


def test_legacy_default_config_keyword_no_longer_drops_reordered_titles() -> None:
    """Trackers created before RV-BE-04 still carry the whole normalised
    query as config_keyword; token matching makes those rows work too."""
    stats = _make_stats(median=1000.0)
    legacy_keyword = default_config_keyword("Samsung S23")
    ad = _make_ad(subject="Samsung Galaxy S23 8/256", price_byn=100000)
    assert matches_tracker_filters(ad, market_stats=stats, config_keyword=legacy_keyword)
    legacy_keyword = default_config_keyword("macbook air m2")
    ad = _make_ad(subject="MacBook Air 13 M2", price_byn=100000)
    assert matches_tracker_filters(ad, market_stats=stats, config_keyword=legacy_keyword)
