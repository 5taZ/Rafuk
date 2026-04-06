from __future__ import annotations

import pytest

from api.services.aggregator import (
    PriceStats,
    apply_search_mode,
    build_query_key,
    compute_price_stats,
    compute_price_vs_median,
    compute_segments,
    extract_prices,
    filter_deal_ads,
    is_strict_match,
    normalize_search_text,
    sort_listings,
)


def test_extract_prices_filters_zero_and_anomalies(sample_ads: list[dict[str, object]]) -> None:
    prices = extract_prices(sample_ads)
    assert 0.0 not in prices
    assert 150000.0 not in prices
    assert len(prices) == 4


def test_compute_price_stats_basic() -> None:
    stats = compute_price_stats([1000.0, 2000.0, 3000.0, 4000.0, 5000.0])
    assert isinstance(stats, PriceStats)
    assert stats.count == 5
    assert stats.mean == pytest.approx(3000.0)
    assert stats.median == pytest.approx(3000.0)
    assert stats.min == 1000.0
    assert stats.max == 5000.0


def test_compute_price_stats_empty_returns_zeros() -> None:
    stats = compute_price_stats([])
    assert stats.count == 0
    assert stats.mean == 0.0


def test_compute_price_vs_median_above() -> None:
    result = compute_price_vs_median({"price_byn": 250000}, 2000.0)
    assert result == pytest.approx(25.0)


def test_sort_listings_newest_first(sample_ads: list[dict[str, object]]) -> None:
    result = sort_listings(sample_ads[:4], "newest", 2000.0)
    assert result[0]["ad_id"] == 2


def test_sort_listings_near_median(sample_ads: list[dict[str, object]]) -> None:
    result = sort_listings(sample_ads[:4], "near_median", 2100.0)
    assert result[0]["ad_id"] in (1, 2)


def test_filter_deal_ads_respects_discount_threshold(sample_ads: list[dict[str, object]]) -> None:
    result = filter_deal_ads(sample_ads[:4], 2200.0, 10.0)
    assert [item["ad_id"] for item in result] == [4]


def test_filter_deal_ads_supports_discount_range(sample_ads: list[dict[str, object]]) -> None:
    result = filter_deal_ads(sample_ads[:4], 2200.0, 5.0, 10.0)
    assert [item["ad_id"] for item in result] == [1]


def test_compute_segments_groups_correctly(sample_ads: list[dict[str, object]]) -> None:
    segments = compute_segments(sample_ads)
    assert "new_private" in segments
    assert "used_shop" in segments
    assert "new_shop" in segments
    assert "used_private" in segments
    assert segments["new_private"]["count"] == 1


def test_is_strict_match_rejects_extra_variant_tokens() -> None:
    assert is_strict_match("iPhone 15 256GB", "iphone 15 256") is True
    assert is_strict_match("iPhone 15 Pro 256GB", "iphone 15 256") is False
    assert is_strict_match("iPhone 15 Pro Max 256GB", "iphone 15 pro") is False


def test_apply_search_mode_filters_ads_in_strict_mode() -> None:
    ads = [
        {"ad_id": 1, "subject": "iPhone 15 256GB"},
        {"ad_id": 2, "subject": "iPhone 15 Pro 256GB"},
    ]
    assert [item["ad_id"] for item in apply_search_mode(ads, "iphone 15 256", True)] == [1]
    assert [item["ad_id"] for item in apply_search_mode(ads, "iphone 15 256", False)] == [1, 2]


def test_build_query_key_separates_modes() -> None:
    assert build_query_key("iphone 15 256", False) == "broad::iphone 15 256"
    assert build_query_key("iphone 15 256", True) == "strict::iphone 15 256"


def test_normalize_search_text_supports_reseller_aliases() -> None:
    assert normalize_search_text("айфон 15 про макс 256гб") == "iphone 15 pro max 256"
    assert normalize_search_text("пс5 слим 1 тб") == "ps5 slim 1024"
