from __future__ import annotations

import time
from datetime import UTC, datetime

import pytest

from api.services import aggregator
from api.services.aggregator import compute_price_stats
from api.services.market_signals import duplicate_counts, region_label
from api.services.reseller_tools import (
    NO_PRICE_VERDICT,
    analyze_query_text,
    compute_deal_score,
    config_keyword_matches,
    default_config_keyword,
    matches_tracker_filters,
)

FROZEN_NOW = datetime(2026, 4, 1, 12, tzinfo=UTC)
MARKET = compute_price_stats([1520.0 + index * 10 for index in range(10)])


@pytest.fixture
def frozen_now(monkeypatch: pytest.MonkeyPatch) -> datetime:
    monkeypatch.setattr(aggregator, "utc_now", lambda: FROZEN_NOW)
    return FROZEN_NOW


def kufar_ad(**overrides) -> dict:
    ad = {
        "ad_id": 1,
        "subject": "iPhone 13 128GB",
        "price_byn": 150000,
        "list_time": "2026-04-01T10:00:00Z",
        "region_id": 7,
        "company_ad": False,
        "ad_parameters": [
            {"p": "condition", "pl": "Состояние", "v": "1", "vl": "Б/у"},
            {"p": "region", "pl": "Область", "v": "7", "vl": "Минск"},
        ],
    }
    ad.update(overrides)
    return ad


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


@pytest.mark.parametrize(
    ("title", "keyword"),
    [
        ("Samsung Galaxy S23 8/256", "samsung s23"),
        ("Apple iPhone 13 Midnight 128GB", "iphone 13 128gb"),
        ("MacBook Air 13 M1 8/256", "macbook air m1"),
        ("128GB iPhone 13", "iphone 13 128"),
    ],
)
def test_config_keyword_matches_is_token_based(title: str, keyword: str) -> None:
    assert config_keyword_matches(title, keyword) is True


def test_config_keyword_matches_requires_every_token() -> None:
    assert config_keyword_matches("iPhone 13 256GB", "iphone 13 128") is False
    assert config_keyword_matches("iPhone 13", None) is True
    assert config_keyword_matches("iPhone 13", "") is True
    assert default_config_keyword("iPhone 13 128GB") == "iphone 13 128"


def test_tracker_filters_compare_codes_and_labels() -> None:
    ad = kufar_ad()
    assert matches_tracker_filters(ad, market_stats=MARKET, condition="Б/у") is True
    assert matches_tracker_filters(ad, market_stats=MARKET, condition="used") is True
    assert matches_tracker_filters(ad, market_stats=MARKET, condition="Новый") is False
    assert matches_tracker_filters(ad, market_stats=MARKET, seller_type="Частное лицо") is True
    assert matches_tracker_filters(ad, market_stats=MARKET, seller_type="Магазин") is False
    assert matches_tracker_filters(ad, market_stats=MARKET, region_name="минск") is True
    assert matches_tracker_filters(ad, market_stats=MARKET, region_name="Регион 7") is True
    assert matches_tracker_filters(ad, market_stats=MARKET, region_name="Брест") is False


def test_tracker_filters_keep_ads_without_filter_data() -> None:
    ad = {"ad_id": 1, "subject": "iPhone 13", "price_byn": 150000}
    assert (
        matches_tracker_filters(
            ad,
            market_stats=MARKET,
            condition="Б/у",
            seller_type="Частное лицо",
            region_name="Минск",
        )
        is True
    )


def test_region_filter_by_name_keeps_ads_with_only_region_id() -> None:
    ad = {"ad_id": 1, "subject": "iPhone 13", "price_byn": 150000, "region_id": 6}
    assert matches_tracker_filters(ad, market_stats=MARKET, region_name="Минск") is True
    assert matches_tracker_filters(ad, market_stats=MARKET, region_name="Регион 6") is True
    assert matches_tracker_filters(ad, market_stats=MARKET, region_name="Регион 7") is False


def test_tracker_filters_match_legacy_label_values() -> None:
    ad = kufar_ad(
        company_ad=None,
        ad_parameters=[
            {"p": "condition", "v": "Новый"},
            {"p": "seller_type", "v": "Магазин"},
        ],
    )
    assert matches_tracker_filters(ad, market_stats=MARKET, condition="Новый") is True
    assert matches_tracker_filters(ad, market_stats=MARKET, seller_type="Магазин") is True
    assert matches_tracker_filters(ad, market_stats=MARKET, seller_type="Частное лицо") is False


def test_tracker_filters_reject_placeholder_prices() -> None:
    ad = kufar_ad(price_byn=100)
    assert matches_tracker_filters(ad, market_stats=MARKET, min_discount_percent=10) is False
    cheap = kufar_ad(price_byn=130000)
    assert matches_tracker_filters(cheap, market_stats=MARKET, min_discount_percent=10) is True


def test_region_label_reads_ad_parameters() -> None:
    assert region_label(kufar_ad()) == "Минск"
    assert region_label({"region_id": "6"}) == "Регион 6"
    assert region_label({"region_id": "abc"}) is None


def test_compute_deal_score_is_deterministic_with_frozen_time(frozen_now: datetime) -> None:
    del frozen_now
    score = compute_deal_score(kufar_ad(), query="iphone 13 128", market_stats=MARKET)
    assert score.score == 95.2
    assert score.verdict == "Забирать"
    assert score.reasons[:3] == ["-4% к медиане", "частник", "свежий лот"]


def test_compute_deal_score_ignores_future_list_time(frozen_now: datetime) -> None:
    del frozen_now
    fresh = compute_deal_score(kufar_ad(), query="iphone", market_stats=MARKET)
    future = compute_deal_score(
        kufar_ad(list_time="2026-05-01T10:00:00Z"), query="iphone", market_stats=MARKET
    )
    assert "свежий лот" in fresh.reasons
    assert "свежий лот" not in future.reasons
    assert future.score == fresh.score - 12


def test_compute_deal_score_without_price_is_neutral(frozen_now: datetime) -> None:
    del frozen_now
    score = compute_deal_score(kufar_ad(price_byn=None), query="iphone", market_stats=MARKET)
    assert score.score == 0.0
    assert score.verdict == NO_PRICE_VERDICT
    assert score.reasons == ["нет цены"]


def test_compute_deal_score_caps_placeholder_prices(frozen_now: datetime) -> None:
    del frozen_now
    score = compute_deal_score(kufar_ad(price_byn=100), query="iphone", market_stats=MARKET)
    assert score.verdict == "Мимо"
    assert score.score <= 40
    assert "цена-заглушка" in score.reasons


def _duplicate_ads(**extra) -> list[dict]:
    return [
        {"ad_id": ad_id, "subject": "iPhone 13 128GB", "price_byn": price, "region_id": 6, **extra}
        for ad_id, price in ((1, 150000), (2, 155000), (3, 160000))
    ]


def test_duplicate_counts_groups_similar_prices() -> None:
    assert duplicate_counts(_duplicate_ads()) == {1: 2, 2: 2, 3: 2}
    ads = _duplicate_ads()
    ads[2]["price_byn"] = 200000
    assert duplicate_counts(ads) == {1: 1, 2: 1}


def test_duplicate_counts_separates_seller_accounts() -> None:
    ads = _duplicate_ads()
    for index, ad in enumerate(ads):
        ad["account_id"] = f"seller-{index}"
    assert duplicate_counts(ads) == {}
    same_seller = _duplicate_ads(account_id="seller-1")
    assert duplicate_counts(same_seller) == {1: 2, 2: 2, 3: 2}


def test_duplicate_counts_skips_malformed_ads() -> None:
    ads = [*_duplicate_ads(), {"ad_id": None, "subject": "iPhone 13 128GB"}, {"ad_id": "x"}]
    assert duplicate_counts(ads) == {1: 2, 2: 2, 3: 2}


def test_duplicate_counts_scales_to_large_groups() -> None:
    ads = [
        {"ad_id": index + 1, "subject": "iPhone 13 128GB", "price_byn": 100000 + index * 10}
        for index in range(20000)
    ]
    started = time.perf_counter()
    counts = duplicate_counts(ads)
    assert time.perf_counter() - started < 5
    assert len(counts) == len(ads)
