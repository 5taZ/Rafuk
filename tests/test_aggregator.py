from __future__ import annotations

from datetime import UTC, datetime

import pytest

from api.services import aggregator
from api.services.aggregator import (
    QUERY_KEY_MAX_LENGTH,
    PriceStats,
    apply_search_mode,
    build_query_key,
    coerce_ad_id,
    compute_price_stats,
    compute_price_vs_median,
    compute_segments,
    extract_prices,
    filter_deal_ads,
    get_param,
    is_placeholder_price,
    is_strict_match,
    listing_age_hours,
    normalize_search_text,
    parse_list_time,
    response_cache_key,
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
    # Distances to 2100: ad 1 and ad 2 tie at 100 (stable order), then 4 (300) and 3 (400);
    # the unpriced ad 5 always goes last.
    result = sort_listings(sample_ads[:5], "near_median", 2100.0)
    assert [item["ad_id"] for item in result] == [1, 2, 4, 3, 5]
    result = sort_listings(sample_ads[:4], "near_median", 2190.0)
    assert [item["ad_id"] for item in result] == [2, 1, 3, 4]


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


def test_build_query_key_fits_column_for_long_queries() -> None:
    long_query = "айфон " * 60
    key = build_query_key(long_query, True)
    assert len(key) <= QUERY_KEY_MAX_LENGTH
    assert key.startswith("strict::iphone iphone")
    assert key == build_query_key(long_query, True)
    assert key != build_query_key(long_query + " pro", True)
    assert len(build_query_key("promax " * 50, False)) <= QUERY_KEY_MAX_LENGTH


def test_build_query_key_keeps_short_keys_unchanged() -> None:
    query = "x" * 240
    assert build_query_key(query, False) == f"broad::{query}"


def test_response_cache_key_normalizes_query() -> None:
    assert response_cache_key("listings", "  iPhone 15 ", False, "BYN") == response_cache_key(
        "listings", "iphone 15", False, "BYN"
    )
    assert response_cache_key("listings", "iphone", False, "BYN") != response_cache_key(
        "listings", "iphone", True, "BYN"
    )


def test_aliases_apply_on_word_boundaries_only() -> None:
    assert normalize_search_text("Эпсон необычный") == "эпсон необычный"
    assert normalize_search_text("iPhone 13 обычный 128gb") == "iphone 13 обычный 128"
    assert normalize_search_text("PS4 обычная 500gb") == "ps4 fat 500"
    assert normalize_search_text("айфон 15 про") == "iphone 15 pro"


def test_strict_mode_keeps_generic_words_and_russian_pro() -> None:
    assert is_strict_match("iPhone 13 обычный 128gb", "iphone 13") is True
    assert is_strict_match("iPhone 15 Pro", "iphone 15 про") is True
    assert is_strict_match("Sony PS4 обычная", "ps4 slim") is False


def test_strict_mode_ignores_token_order() -> None:
    assert is_strict_match("Apple MacBook Air 13 M2", "macbook air m2 13") is True
    assert is_strict_match("MacBook M2 Air", "macbook air m2") is True
    assert is_strict_match("MacBook Pro M2 Air", "macbook air m2") is False


def test_compute_price_stats_drops_placeholder_prices() -> None:
    prices = [1520.0 + index * 10 for index in range(10)] + [1.0]
    stats = compute_price_stats(prices)
    assert stats.count == 10
    assert stats.min == 1520.0
    assert stats.mean == pytest.approx(1565.0)


def test_compute_price_stats_keeps_small_samples_intact() -> None:
    stats = compute_price_stats([1.0, 1500.0, 1600.0])
    assert stats.count == 3
    assert stats.min == 1.0


def test_filter_deal_ads_never_returns_placeholder_prices() -> None:
    prices = [1520.0 + index * 10 for index in range(10)] + [1.0]
    ads = [{"ad_id": index, "price_byn": int(price * 100)} for index, price in enumerate(prices)]
    stats = compute_price_stats(prices)
    result = filter_deal_ads(ads, stats.median, 1.0)
    assert 10 not in [item["ad_id"] for item in result]
    assert filter_deal_ads([{"ad_id": 1, "price_byn": 100}], 1565.0, 10.0) == []


def test_sort_listings_puts_unpriced_lots_last() -> None:
    ads = [
        {"ad_id": 1, "price_byn": None},
        {"ad_id": 2, "price_byn": 200000},
        {"ad_id": 3, "price_byn": 150000},
    ]
    assert [item["ad_id"] for item in sort_listings(ads, "price_asc", 1800.0)] == [3, 2, 1]
    assert [item["ad_id"] for item in sort_listings(ads, "price_desc", 1800.0)] == [2, 3, 1]
    assert [item["ad_id"] for item in sort_listings(ads, "cheap", 1800.0)] == [3, 2, 1]
    assert [item["ad_id"] for item in sort_listings(ads, "near_median", 1800.0)][-1] == 1


def test_sort_listings_newest_handles_null_and_mixed_offsets() -> None:
    ads = [
        {"ad_id": 1, "list_time": None},
        {"ad_id": 2, "list_time": "2026-10-11T10:00:00+03:00"},
        {"ad_id": 3, "list_time": "2026-10-11T09:00:00Z"},
        {"ad_id": 4, "list_time": 1760000000},
        {"ad_id": 5, "list_time": "2026-10-11T09:00:00.500Z"},
    ]
    assert [item["ad_id"] for item in sort_listings(ads, "newest", 0.0)] == [5, 3, 2, 1, 4]


def test_list_time_helpers_reject_bad_and_future_values(monkeypatch) -> None:
    monkeypatch.setattr(aggregator, "utc_now", lambda: datetime(2026, 4, 1, 12, tzinfo=UTC))
    assert parse_list_time(None) is None
    assert parse_list_time("not a date") is None
    assert parse_list_time("2026-04-01T10:00:00") == datetime(2026, 4, 1, 10, tzinfo=UTC)
    assert listing_age_hours("2026-04-01T10:00:00Z") == pytest.approx(2.0)
    assert listing_age_hours("2026-04-02T12:00:00Z") is None
    assert listing_age_hours("2026-04-01T12:05:00Z") == 0.0


def test_coerce_ad_id_handles_malformed_values() -> None:
    assert coerce_ad_id(12) == 12
    assert coerce_ad_id("12") == 12
    assert coerce_ad_id(12.0) == 12
    assert coerce_ad_id(None) is None
    assert coerce_ad_id("abc") is None
    assert coerce_ad_id(True) is None
    assert coerce_ad_id(-3) is None


def test_get_param_survives_malformed_parameters() -> None:
    assert get_param({"ad_parameters": None}, "condition") is None
    assert get_param({"ad_parameters": ["oops", {"p": "condition", "v": 1}]}, "condition") == "1"
    assert get_param({"ad_parameters": [{"p": "condition", "vl": "Б/у"}]}, "condition") == "Б/у"


def test_compute_segments_understands_kufar_codes() -> None:
    ads = [
        {
            "price_byn": 200000,
            "company_ad": False,
            "ad_parameters": [{"p": "condition", "v": "2", "vl": "Новый"}],
        },
        {
            "price_byn": 150000,
            "company_ad": True,
            "ad_parameters": [{"p": "condition", "v": "1", "vl": "Б/у"}],
        },
    ]
    segments = compute_segments(ads)
    assert segments["new_private"]["count"] == 1
    assert segments["used_shop"]["count"] == 1


def test_concatenated_playstation_generation_is_recognized() -> None:
    assert normalize_search_text("Sony PlayStation5 825GB") == "sony ps5 825"
    assert normalize_search_text("Плейстейшен4 slim") == "ps4 slim"
    assert is_strict_match("Sony PlayStation5 825GB", "ps5") is True
    assert is_strict_match("Playstation4 Slim 500gb", "ps4 slim") is True
    assert is_strict_match("Sony PlayStation5", "playstation 5") is True
    assert build_query_key("PlayStation5", False) == build_query_key("ps5", False) == "broad::ps5"


def test_normalization_keeps_yo_and_belarusian_letters() -> None:
    assert normalize_search_text("Чёрный") == "черный"
    assert normalize_search_text("Ёлка") == "елка"
    assert normalize_search_text("Мінск, Ўзор") == "минск узор"
    assert is_strict_match("Елка искусственная", "ёлка") is True
    assert is_strict_match("iPhone 13 черный", "iphone 13 чёрный") is True
    assert build_query_key("ёлка", True) == build_query_key("елка", True) == "strict::елка"


def test_plus_suffix_is_a_variant_in_strict_mode() -> None:
    assert normalize_search_text("Galaxy S23+ 256") == "galaxy s23 plus 256"
    assert is_strict_match("Galaxy S23+ 256", "galaxy s23") is False
    assert is_strict_match("Galaxy S23+ 256", "galaxy s23+") is True
    assert is_strict_match("Galaxy S23 Plus", "galaxy s23+") is True
    assert is_strict_match("Galaxy Note 10+", "galaxy note 10") is False
    # A "+" between digits is a RAM+storage config, not the plus model.
    assert normalize_search_text("Redmi Note 12 8+256") == "redmi note 12 8 256"
    assert is_strict_match("Redmi Note 12 8+256", "redmi note 12") is True


def test_response_cache_key_keeps_raw_query_differences() -> None:
    def key(query: str) -> str:
        return response_cache_key("listings", query, False, "BYN")

    assert key("Galaxy S23+") != key("Galaxy S23")
    assert key("айфон 13") != key("iphone 13")
    assert key("iphone 13 128gb") != key("iphone 13 128")
    assert key("  Galaxy   S23+ ") == key("galaxy s23+")
    # Parts are encoded unambiguously, so a ":" inside the query cannot fake another part.
    assert response_cache_key("listings", "x:a", False, "b") != response_cache_key(
        "listings", "x", False, "a:b"
    )


def test_placeholder_cutoff_matches_the_stats() -> None:
    prices = [1.0, 1.0, 1.0, 1.0, 22.0, 100.0, 100.0, 100.0, 100.0]
    stats = compute_price_stats(prices)
    # 22 BYN is below a quarter of the median of the real prices: it is dropped from the
    # stats and every placeholder check agrees.
    assert stats.count == 4
    assert stats.min == 100.0
    assert stats.low_cutoff == 25.0
    for price in prices:
        assert is_placeholder_price(price, stats) is (price < stats.min)
    ads = [
        {"ad_id": index + 1, "price_byn": int(price * 100)} for index, price in enumerate(prices)
    ]
    assert filter_deal_ads(ads, stats.median, 1.0, low_cutoff=stats.low_cutoff) == []
    # The cutoff is internal: it never leaks into serialized stats.
    assert "low_cutoff" not in stats.model_dump()


def test_placeholder_cutoff_is_zero_for_small_samples() -> None:
    stats = compute_price_stats([1.0, 1500.0, 1600.0])
    assert stats.low_cutoff == 0.0
    assert is_placeholder_price(1.0, stats) is False
    # Hand-built stats without a cutoff fall back to a share of the median.
    manual = PriceStats(mean=100, median=100, q1=100, q3=100, min=100, max=100, count=5)
    assert is_placeholder_price(20.0, manual) is True
    assert is_placeholder_price(30.0, manual) is False


def test_compute_price_stats_can_skip_placeholder_dropping() -> None:
    prices = [1.0] + [1000.0] * 5
    assert compute_price_stats(prices).count == 5
    raw = compute_price_stats(prices, drop_placeholders=False)
    assert raw.count == 6
    assert raw.min == 1.0
