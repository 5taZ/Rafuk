from __future__ import annotations

import pytest

from api.services.aggregator import (
    PriceStats,
    _precompute_cluster_stats_sync,  # M9: test sync impl directly
    _remove_outliers,
    apply_search_mode,
    build_query_key,
    compute_category_price_stats,
    compute_price_stats,
    compute_price_vs_median,
    compute_price_vs_reference,
    compute_segments,
    detect_price_type,
    extract_category_distribution,
    extract_prices,
    extract_search_refinements,
    filter_ads_for_accessory_category,
    filter_deal_ads,
    is_strict_match,
    normalize_price_byn,
    normalize_search_text,
    sort_listings,
)


def test_extract_prices_filters_zero_and_anomalies(sample_ads: list[dict[str, object]]) -> None:
    prices = extract_prices(sample_ads)
    assert 0.0 not in prices
    assert 1500000.0 not in prices  # anomaly filtered (15M BYN, above MAX_PRICE_BYN)
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
    result = compute_price_vs_median({"price_byn": 2500}, 20.0)
    assert result == pytest.approx(25.0)


def test_sort_listings_newest_first(sample_ads: list[dict[str, object]]) -> None:
    result = sort_listings(sample_ads[:4], "newest", 20.0)
    assert result[0]["ad_id"] == 2


def test_sort_listings_near_median(sample_ads: list[dict[str, object]]) -> None:
    result = sort_listings(sample_ads[:4], "near_median", 21.0)
    assert result[0]["ad_id"] in (1, 2)


def test_filter_deal_ads_respects_discount_threshold(sample_ads: list[dict[str, object]]) -> None:
    result = filter_deal_ads(sample_ads[:4], 22.0, 10.0)
    assert [item["ad_id"] for item in result] == [4]


def test_filter_deal_ads_supports_discount_range(sample_ads: list[dict[str, object]]) -> None:
    # Sample ads have prices [1800, 2000, 2200, 2500] which after kopecks
    # normalization become BYN [18, 20, 22, 25]; median is 21. So ad #1
    # (price 20) sits ~4.8% below the median, ad #4 (price 18) ~14.3%.
    # A 3-6% window must capture only ad #1.
    result = filter_deal_ads(sample_ads[:4], 22.0, 3.0, 6.0)
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


def test_is_strict_match_binds_model_number_to_family_word() -> None:
    # SEARCH-18: private/used listings often name the OS version, which
    # used to satisfy the model number by plain token containment.
    query = "iphone 18 pro"
    for title in (
        "iPhone 15 pro (18 IOS)",
        "iPhone 12 Pro 128Gb Graphite (18 ios)",
        "iPhone 15 Pro 128gb Black Titanium iOS 18",
        "iPhone 16 pro 256 esim ios 18.7",
    ):
        assert is_strict_match(title, query) is False, title
    for title in (
        "iPhone 18 Pro Активированный 100%",
        "Обмен iPhone 18 pro бургунди на 256 гб",
        "Apple Iphone 18 Pro Burgundy",
        "Айфон 18 Pro 256",
        "Чехол для iPhone 17/18 Pro",
    ):
        assert is_strict_match(title, query) is True, title
    assert is_strict_match("Audi A6 C7 2.0 TDI 2015", "audi a6 2015") is True
    assert is_strict_match("MacBook Pro 16 M3 (14 cores)", "macbook pro 14 m3") is False
    assert is_strict_match("MacBook Pro 14 M3", "macbook pro 14 m3") is True


@pytest.mark.parametrize(("text", "expected"), [
    # SEARCH-19: Cyrillic variants after a model number or family word.
    ("Айфон 15 про", "iphone 15 pro"),
    ("iPhone 15 про мах", "iphone 15 pro max"),
    ("айфон15промакс", "iphone 15 pro max"),
    ("macbook эйр m2", "macbook air m2"),
    ("Про машину 15 лет", "про машину 15 лет"),
    ("BMW X5 макс комплектация", "bmw x5 макс комплектация"),
    ("iphone 13 128 плюс чехол", "iphone 13 128 плюс чехол"),
    # SEARCH-19: apostrophes, glued model codes, the Plus suffix.
    ("Levi's 501", "levis 501"),
    ("Джинсы Левис 501", "джинсы levis 501"),
    ("RX580 8GB", "rx 580 8"),
    ("RTX 3060Ti", "rtx 3060 ti"),
    ("1660super", "1660 super"),
    ("s24ultra", "s24 ultra"),
    ("Samsung Galaxy S24+", "samsung galaxy s24 plus"),
    ("Redmi Note 13 Pro+", "redmi note 13 pro plus"),
    ("iPhone 8+", "iphone 8 plus"),
    ("GTA 5 18+", "gta 5 18"),
    ("Sapphire Nitro+ RX 580", "sapphire nitro rx 580"),
    ("8+128gb", "8 128"),
    # SEARCH-19: whole-word aliases; "обычная" is "fat" only next to a console.
    ("поколение кабриолет попсокет", "поколение кабриолет попсокет"),
    ("Куртка обычная", "куртка обычная"),
    ("ps5 обычная", "ps5 fat"),
    ("PlayStation 5 Slim, обычная", "ps5 slim обычная"),
    ("ps5 + ps plus", "ps5 psplus"),
    ("пс5", "ps5"),
])
def test_normalize_search_text_spellings(text: str, expected: str) -> None:
    assert normalize_search_text(text) == expected


def test_strict_match_handles_spelling_variants() -> None:
    # SEARCH-19: live Kufar titles that strict search used to drop or let in.
    assert is_strict_match("iPhone 15 Pro 256Gb Blue", "айфон 15 про") is True
    assert is_strict_match("Айфон 15 про мах реплика", "айфон 15 про") is False
    assert is_strict_match("Джинсы Levi's 501 original", "джинсы levis 501") is True
    assert is_strict_match("RX580  8 гб", "rx 580 8gb") is True
    assert is_strict_match("Видеокарта RTX 3060Ti", "rtx 3060") is False
    assert is_strict_match("Samsung Galaxy S24+", "samsung s24") is False
    assert is_strict_match("Ps5 slim + гарантия + ps plus 9 месяцев", "ps5 slim") is True
    assert is_strict_match("Куртка зимняя обычная", "куртка зимняя") is True


def test_strict_match_ignores_variant_words_in_apparel() -> None:
    # SEARCH-19: "Air", "Max", "Lite" are sneaker lines, not device variants.
    title = "Кроссовки Nike Air Max 95, размер 41-42"
    assert is_strict_match(title, "кроссовки nike 42") is False
    for category_id in (19020, 8100, 12020):
        assert is_strict_match(title, "кроссовки nike 42", category_id=category_id) is True
    assert is_strict_match("Samsung S24 Ultra", "samsung s24", category_id=17010) is False
    ads = [
        {"ad_id": 1, "subject": title, "category": "19020"},
        {"ad_id": 2, "subject": "Кроссовки Nike 42", "category": "19020"},
        {"ad_id": 3, "subject": "Кроссовки Nike T-Lite XI 42", "category": "8100"},
    ]
    assert [ad["ad_id"] for ad in apply_search_mode(ads, "кроссовки nike 42", True)] == [1, 2, 3]


def test_whole_word_aliases_keep_generation_and_accessory_words() -> None:
    # SEARCH-19: "поко" → "poco" inside "поколение" hid the generation from
    # the watchlist market, "пс" → "ps" inside "попсокет" hid the AI type.
    from api.services.ai_marketplace import _accessory_type_similarity_bonus
    from api.services.watchlist_market import _generation

    assert _generation("Volkswagen Golf 4 поколение") == "4"
    assert _accessory_type_similarity_bonus(
        "phone_accessory", normalize_search_text("Попсокет iPhone 14"),
        normalize_search_text("попсокет для iphone 14"),
    ) > 0


def test_query_key_keeps_its_original_normalization() -> None:
    # SEARCH-19: keys are persisted; a changed key re-alerts a tracker's page.
    assert build_query_key("айфон 15 про", True) == "strict::iphone 15 про"
    assert build_query_key("Levi's 501", False, 19010) == "broad::cat:19010::levi s 501"
    assert build_query_key("ps5 обычная", True) == "strict::ps5 fat"


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
    assert build_query_key("iphone 15 256", False, 17010) == "broad::cat:17010::iphone 15 256"


def test_normalize_search_text_supports_reseller_aliases() -> None:
    assert normalize_search_text("айфон 15 про макс 256гб") == "iphone 15 pro max 256"
    assert normalize_search_text("пс5 слим 1 тб") == "ps5 slim 1024"


def test_extract_category_distribution_single_category() -> None:
    param = {"p": "category", "v": "2010", "vl": "Легковые авто"}
    ads = [
        {"category": "2010", "ad_parameters": [param]},
        {"category": "2010", "ad_parameters": [param]},
    ]
    result = extract_category_distribution(ads)
    assert len(result) == 1
    assert result[0]["id"] == 2010
    assert result[0]["label"] == "Легковые авто"
    assert result[0]["count"] == 2


def test_extract_category_distribution_multiple_categories() -> None:
    p_auto = {"p": "category", "v": "2010", "vl": "Легковые авто"}
    p_parts = {"p": "category", "v": "2040", "vl": "Запчасти"}
    ads = [
        {"category": "2010", "ad_parameters": [p_auto]},
        {"category": "2040", "ad_parameters": [p_parts]},
        {"category": "2040", "ad_parameters": [p_parts]},
        {"category": "2040", "ad_parameters": [p_parts]},
    ]
    result = extract_category_distribution(ads)
    assert len(result) == 2
    assert result[0]["id"] == 2040  # sorted by count desc
    assert result[0]["count"] == 3
    assert result[1]["id"] == 2010
    assert result[1]["count"] == 1


def test_extract_category_distribution_no_category_field() -> None:
    ads = [
        {"ad_id": 1, "ad_parameters": []},
        {"category": "2010", "ad_parameters": [{"p": "category", "v": "2010", "vl": "Cars"}]},
    ]
    result = extract_category_distribution(ads)
    assert len(result) == 1
    assert result[0]["count"] == 1


def test_compute_price_vs_reference_prefers_category_median() -> None:
    ads = [
        {"ad_id": 1, "category": "2010", "price_byn": 30000},
        {"ad_id": 2, "category": "2010", "price_byn": 40000},
        {"ad_id": 3, "category": "2010", "price_byn": 41000},
        {"ad_id": 4, "category": "2040", "price_byn": 100},
        {"ad_id": 5, "category": "2040", "price_byn": 120},
        {"ad_id": 6, "category": "2040", "price_byn": 141},
    ]
    market_stats = compute_price_stats(extract_prices(ads))
    category_stats = compute_category_price_stats(ads)

    result = compute_price_vs_reference(ads[-1], market_stats, category_stats)

    assert result == pytest.approx(17.5)


def test_filter_deal_ads_uses_category_reference_when_available() -> None:
    ads = [
        {"ad_id": 1, "category": "2010", "price_byn": 30000},
        {"ad_id": 2, "category": "2010", "price_byn": 40000},
        {"ad_id": 3, "category": "2010", "price_byn": 41000},
        {"ad_id": 4, "category": "2040", "price_byn": 100},
        {"ad_id": 5, "category": "2040", "price_byn": 120},
        {"ad_id": 6, "category": "2040", "price_byn": 141},
        {"ad_id": 7, "category": "2040", "price_byn": 90},
    ]
    market_stats = compute_price_stats(extract_prices(ads))
    category_stats = compute_category_price_stats(ads)

    result = filter_deal_ads(
        ads,
        market_stats.median,
        10.0,
        market_stats=market_stats,
        category_price_stats=category_stats,
    )

    assert [item["ad_id"] for item in result] == [1, 7]


def test_resolve_reference_falls_back_to_query_when_category_too_small() -> None:
    """A 1-of-1 category must fall back to the global query median —
    otherwise we'd get a useless reference (median == price → delta 0%).
    """
    from api.services.aggregator import resolve_price_reference

    ads = [
        {"ad_id": 1, "category": "2010", "price_byn": 100000},
        {"ad_id": 2, "category": "2010", "price_byn": 110000},
        {"ad_id": 3, "category": "2010", "price_byn": 120000},
        {"ad_id": 4, "category": "2010", "price_byn": 105000},
        # Lone outlier in its own category — too few for a useful median
        {"ad_id": 99, "category": "9999", "price_byn": 50000},
    ]
    market_stats = compute_price_stats(extract_prices(ads))
    category_stats = compute_category_price_stats(ads)

    main_ref = resolve_price_reference(ads[0], market_stats, category_stats)
    lone_ref = resolve_price_reference(ads[-1], market_stats, category_stats)

    assert main_ref.scope == "category"
    assert lone_ref.scope == "query"  # fallback because category count == 1


def test_iphone_14_scenario_uses_phone_category_not_global() -> None:
    """User's reported case: query "iPhone 14" mixes phones and accessories.
    Phone deviation should be vs the phone median, not the global one
    that's pulled down by the cheaper accessories.
    """
    from api.services.aggregator import compute_price_vs_reference

    ads = [
        # Phones (~1500-1700 BYN, all in category 17010)
        {"ad_id": 1, "category": 17010, "price_byn": 160000},
        {"ad_id": 2, "category": 17010, "price_byn": 175000},
        {"ad_id": 3, "category": 17010, "price_byn": 155000},
        {"ad_id": 4, "category": 17010, "price_byn": 165000},
        {"ad_id": 5, "category": 17010, "price_byn": 170000},
        # Accessories (5-12 BYN, in category 17030) — would crash the global median
        {"ad_id": 10, "category": 17030, "price_byn": 500},
        {"ad_id": 11, "category": 17030, "price_byn": 800},
        {"ad_id": 12, "category": 17030, "price_byn": 1200},
    ]
    market_stats = compute_price_stats(extract_prices(ads))
    category_stats = compute_category_price_stats(ads)

    # An iPhone at 1700 BYN against the global median (~95) would look
    # like +1700% above market, which is nonsense. With per-category
    # logic it should be ~+3% (vs phone median of 1650).
    delta = compute_price_vs_reference(ads[4], market_stats, category_stats)
    assert -10.0 < delta < 10.0, (
        f"iPhone deviation should be small vs phone median, got {delta:+.1f}%"
    )


def test_product_cluster_groups_vehicle_engines_in_mixed_parts_query() -> None:
    ads = [
        {"ad_id": 1, "subject": "Volkswagen Polo 1.9 SDI мотор", "price_byn": 196000},
        {"ad_id": 2, "subject": "Двигатель VW Polo 1.4 бензин", "price_byn": 180000},
        {"ad_id": 3, "subject": "Мотор Фольксваген Поло 1.6", "price_byn": 210000},
        {"ad_id": 4, "subject": "Volkswagen Polo поворотники", "price_byn": 1956},
        {"ad_id": 5, "subject": "Зеркало наружнее левое Volkswagen Polo", "price_byn": 4191},
    ]

    clusters = _precompute_cluster_stats_sync(ads, query="Volkswagen Polo")

    assert clusters[1] is not None
    assert clusters[1].count == 3
    assert clusters[1].median == pytest.approx(1960.0)
    assert clusters[4] is None
    assert _precompute_cluster_stats_sync(
        ads, query="Volkswagen Polo", target_ids={1, 4},
    ) == {1: clusters[1], 4: None}


def test_extract_search_refinements_returns_top_recurring_tokens() -> None:
    """Subjects share "Pro" (5x) and "256" (4x) which aren't in the query —
    those should bubble up. "Айфон" maps via alias to "iphone" which IS in
    the query, so it's excluded.
    """
    ads = [
        {"subject": "iPhone 13 Pro 256GB"},
        {"subject": "Айфон 13 Pro Max 256"},
        {"subject": "iPhone 13 Pro 128GB Серый"},
        {"subject": "iPhone 13 Pro Max 256GB"},
        {"subject": "iPhone 13 Pro 256GB Идеал"},
        {"subject": "iPhone 13 mini 64"},
        {"subject": "iPhone 13 256GB"},
    ]
    refinements = extract_search_refinements(ads, "iphone 13", min_support=2)
    assert "pro" in refinements
    # UX-P2-08: storage is presented with units, not as an unexplained number.
    assert "256 ГБ" in refinements
    assert "iphone" not in refinements
    assert "13" not in refinements


def test_extract_search_refinements_drops_stopwords_and_short_tokens() -> None:
    ads = [
        {"subject": "iPhone 13 на запчасти Минск идеал"},
        {"subject": "iPhone 13 на запчасти Минск торг"},
        {"subject": "iPhone 13 на запчасти Минск"},
        {"subject": "iPhone 13 на запчасти оригинал"},
        {"subject": "iPhone 13 на запчасти Минск"},
    ]
    refinements = extract_search_refinements(ads, "iphone 13", min_support=2)
    assert "запчасти" in refinements
    # Stop-words and city names are filtered
    assert "на" not in refinements
    assert "минск" not in refinements
    assert "идеал" not in refinements
    assert "оригинал" not in refinements


def test_refinements_keep_colour_phrases_and_only_label_proven_storage() -> None:
    ads = [{"subject": "iPhone 14 Pro 256GB Deep Purple 2026"}] * 4
    refinements = extract_search_refinements(ads, "iphone 14 pro")
    assert "deep purple" in refinements
    assert "deep" not in refinements
    assert "purple" not in refinements
    assert "256 ГБ" in refinements
    assert "2026" not in refinements
    assert "256 ГБ" not in extract_search_refinements(ads, "iphone 14 pro 256")


def test_extract_search_refinements_returns_empty_for_unique_titles() -> None:
    """No token appears in enough listings to clear the support floor."""
    ads = [
        {"subject": "iPhone 13 Pro"},
        {"subject": "iPhone 13 Max"},
        {"subject": "iPhone 13 mini"},
        {"subject": "iPhone 13 SE"},
    ]
    assert extract_search_refinements(ads, "iphone 13", min_support=3) == []


def test_extract_search_refinements_handles_missing_subjects() -> None:
    ads = [
        {"subject": ""},
        {"ad_id": 1},
        {"subject": "iPhone 13 Pro Max"},
        {"subject": "iPhone 13 Pro Max"},
        {"subject": "iPhone 13 Pro Max"},
    ]
    refinements = extract_search_refinements(ads, "iphone 13", min_support=2)
    assert "pro" in refinements
    assert "max" in refinements


def test_extract_search_refinements_caps_at_limit() -> None:
    ads = [
        {"subject": f"item alpha bravo charlie delta echo foxtrot {i}"}
        for i in range(10)
    ]
    refinements = extract_search_refinements(ads, "item", limit=3, min_support=2)
    assert len(refinements) == 3


# ── detect_price_type & normalize_price_byn ──────────────────────────────
#
# Kufar returns price=0 for both "договорная" (price unknown) and
# "бесплатно" (giveaway). detect_price_type discriminates the two by
# inspecting ad text. The default is "negotiable" — only contextual
# giveaway phrases escalate to "free", because mis-labelling a
# negotiable listing as free poisons the market median.


class TestDetectPriceTypePositives:
    """Ads that genuinely give the item away — must be 'free'."""

    @pytest.mark.parametrize(
        "ad",
        [
            {"subject": "Стол", "body": "Отдам бесплатно в хорошие руки"},
            {"subject": "Коробки", "body": "Забери даром"},
            {"subject": "Хлам", "body": "Безвозмездно отдадим коробки"},
            {"subject": "Бесплатно стол самовывоз", "body": ""},
            {"subject": "Доска", "body": "Отдам даром"},
            {"subject": "ДАРОМ ШКАФ", "body": ""},
            {"subject": "Кресло", "body": "Возьмите бесплатно"},
            {"subject": "Холодильник", "body": "Забирайте бесплатно сегодня"},
            {"subject": "Кніга", "body": "Аддам бясплатна"},  # Belarusian
            {"subject": "Стиралка", "body_short": "Отдам бесплатно нерабочую"},
        ],
    )
    def test_giveaway_phrases_detected_as_free(self, ad: dict) -> None:
        assert detect_price_type(ad) == "free"


class TestDetectPriceTypeFalsePositiveProtection:
    """Ads that look free-ish but aren't — must stay 'negotiable'."""

    @pytest.mark.parametrize(
        ("name", "ad"),
        [
            ("free assembly service", {"subject": "Шкаф", "body": "Сборка бесплатно"}),
            ("free demo", {"subject": "Велосипед", "body": "Покажу бесплатно перед покупкой"}),
            ("free shipping en", {"subject": "Headphones", "body": "Free shipping worldwide"}),
            ("ne nuzhen", {"subject": "Велосипед", "body": "Велосипед не нужен мне"}),
            ("ne nuzhny", {"subject": "Диски", "body": "Эти диски не нужны"}),
            ("zaberite alone", {"subject": "Стол", "body": "Заберите завтра вечером"}),
            ("free in middle", {"subject": "Товар", "body": "Покажу товар бесплатно"}),
            ("explicit negation", {"subject": "Машина", "body": "Не бесплатно, цена 500"}),
            ("negation za", {"subject": "Машина", "body": "Не за бесплатно отдам"}),
            ("free delivery in title", {"subject": "Телефон бесплатная доставка", "body": ""}),
            ("free demo in title", {"subject": "Стол бесплатно осмотр", "body": ""}),
            ("free install in title", {"subject": "Кондиционер бесплатно установка", "body": ""}),
            ("plain", {"subject": "Стол", "body": "Торг уместен"}),
            ("substring 'бесплатная'", {"subject": "Телефон", "body": "Бесплатная доставка"}),
        ],
    )
    def test_no_false_positive(self, name: str, ad: dict) -> None:
        assert detect_price_type(ad) == "negotiable", name


class TestNormalizePriceBynSemantic:
    """Verify the contract: None=excluded from metrics, 0.0=included as 0."""

    def test_zero_with_negotiable_text_returns_none(self) -> None:
        # Negotiable -> None -> excluded from median/mean
        ad = {"price_byn": 0, "subject": "Велосипед", "body": "Торг уместен"}
        assert normalize_price_byn(0, ad) is None

    def test_zero_with_free_text_returns_zero(self) -> None:
        # Free -> 0.0 -> included in median/mean as 0
        ad = {"price_byn": 0, "subject": "Стол", "body": "Отдам бесплатно"}
        assert normalize_price_byn(0, ad) == 0.0

    def test_zero_without_ad_returns_none(self) -> None:
        # Without ad context, can't know -> safer is None (negotiable)
        assert normalize_price_byn(0) is None

    def test_positive_price_unaffected_by_ad_text(self) -> None:
        # Real price 100 BYN, body mentions "бесплатно" — must NOT be misread
        ad = {"price_byn": 10000, "subject": "Велосипед", "body": "Доставка бесплатно"}
        assert normalize_price_byn(10000, ad) == 100.0


class TestExtractPricesSemantic:
    """Verify metrics excluded negotiable, include free as 0."""

    def test_negotiable_excluded_from_metrics(self) -> None:
        ads = [
            {"price_byn": 100000, "subject": "A", "body": ""},        # 1000 BYN
            {"price_byn": 0, "subject": "B", "body": "Торг"},         # negotiable -> excluded
            {"price_byn": 200000, "subject": "C", "body": ""},        # 2000 BYN
        ]
        prices = extract_prices(ads)
        assert prices == [1000.0, 2000.0]

    def test_free_included_as_zero(self) -> None:
        ads = [
            {"price_byn": 100000, "subject": "A", "body": ""},          # 1000 BYN
            {"price_byn": 0, "subject": "Free", "body": "Отдам даром"},  # free -> 0.0
            {"price_byn": 200000, "subject": "C", "body": ""},          # 2000 BYN
        ]
        prices = extract_prices(ads)
        assert prices == [1000.0, 0.0, 2000.0]

    def test_free_drives_discount_to_minus_100(self) -> None:
        # "Скидка %" is computed by compute_price_vs_median.
        # A free item (price=0) vs median=1000 should be -100%.
        free_ad = {"price_byn": 0, "subject": "Стол", "body": "Отдам бесплатно"}
        delta = compute_price_vs_median(free_ad, median=1000.0)
        assert delta == -100.0

    def test_negotiable_has_zero_delta_not_minus_100(self) -> None:
        # B-09: Negotiable items return None — they have no meaningful price delta.
        negotiable_ad = {"price_byn": 0, "subject": "Стол", "body": "Торг"}
        delta = compute_price_vs_median(negotiable_ad, median=1000.0)
        assert delta is None


class TestWave1FreeListingConsistency:
    """B-01..B-04: free listings (price=0 + giveaway text) must show
    up consistently across /price-stats, /segments, accessory filter
    and the IQR-zero outlier fallback. Negotiable listings (price=0
    without giveaway text) must not be confused with free in priced
    sorts."""

    @staticmethod
    def _free(subject: str = "Стол") -> dict:
        return {"price_byn": 0, "subject": subject, "body": "Отдам бесплатно"}

    @staticmethod
    def _negotiable(subject: str = "Шкаф") -> dict:
        return {"price_byn": 0, "subject": subject, "body": "Цена договорная"}

    @staticmethod
    def _priced(price_byn: int, subject: str = "Item") -> dict:
        return {"price_byn": price_byn, "subject": subject, "body": ""}

    # ----- B-01 ----------------------------------------------------

    def test_compute_segments_includes_free_listings(self) -> None:
        # /price-stats counts free as 0.0 (extract_prices); /segments
        # used to drop them because the helper called normalize_price_byn
        # without ``ad``. Both endpoints now agree.
        ads = [
            {**self._free("Бесплатный диван"), "company_ad": False,
             "ad_parameters": [{"p": "condition", "v": "1"}]},
            {**self._priced(150_00, "Б/у диван"), "company_ad": False,
             "ad_parameters": [{"p": "condition", "v": "1"}]},
        ]
        result = compute_segments(ads)
        assert result["used_private"]["count"] == 2
        # 150_00 kopecks → 150.0 BYN; median of [0.0, 150.0] = 75.0.
        assert result["used_private"]["median"] == pytest.approx(75.0)

    # ----- B-02 ----------------------------------------------------

    def test_filter_ads_for_accessory_category_keeps_free(self) -> None:
        # Free phone case under the accessory cap must survive the
        # filter; previously normalize_price_byn(...) without ``ad``
        # returned None and the giveaway listing was dropped.
        ads = [
            self._free("Чехол iPhone бесплатно"),
            self._priced(20_00, "Чехол iPhone"),  # 20 BYN
            self._priced(2_000_00, "iPhone 14 Pro"),  # 2000 BYN — parent product
        ]
        # ``phone_accessory`` cap is 150 BYN — see ACCESSORY_PRICE_CAPS.
        filtered = filter_ads_for_accessory_category(ads, "phone_accessory")
        # free + 20 BYN case should be kept; the 2000 BYN phone dropped.
        subjects = {ad["subject"] for ad in filtered}
        assert "Чехол iPhone бесплатно" in subjects
        assert "iPhone 14 Pro" not in subjects

    # ----- B-03 ----------------------------------------------------

    def test_sort_listings_price_asc_pushes_negotiable_to_tail(self) -> None:
        ads = [
            self._negotiable("Negotiable A"),
            self._priced(500_00, "Cheap 5 BYN"),
            self._free("Free 0 BYN"),
            self._priced(100_000_00, "Expensive 1000 BYN"),
        ]
        ordered = sort_listings(ads, "price_asc", median=10.0)
        subjects = [ad["subject"] for ad in ordered]
        # Free (0) and 5 BYN cheap come first; expensive next; negotiable last.
        assert subjects[-1] == "Negotiable A"
        # Free should out-rank the 5 BYN cheap (0 < 5).
        assert subjects[0] == "Free 0 BYN"

    def test_sort_listings_price_desc_keeps_negotiable_at_tail(self) -> None:
        ads = [
            self._negotiable("Negotiable A"),
            self._priced(500_00, "Cheap"),
            self._priced(100_000_00, "Expensive"),
        ]
        ordered = sort_listings(ads, "price_desc", median=10.0)
        subjects = [ad["subject"] for ad in ordered]
        # Expensive first, cheap second, negotiable still last.
        assert subjects == ["Expensive", "Cheap", "Negotiable A"]

    def test_sort_listings_near_median_excludes_negotiable_first(self) -> None:
        ads = [
            self._negotiable("Negotiable"),
            self._priced(1_000_00, "Right at median"),  # 10 BYN
            self._priced(2_000_00, "20 BYN"),
        ]
        ordered = sort_listings(ads, "near_median", median=10.0)
        subjects = [ad["subject"] for ad in ordered]
        # The exact-match should win; negotiable must not surface
        # ahead of priced listings just because (0.0 - median) is small.
        assert subjects[0] == "Right at median"
        assert subjects[-1] == "Negotiable"

    # ----- B-04 ----------------------------------------------------

    def test_remove_outliers_iqr_zero_fallback_keeps_free(self) -> None:
        # 8 prices, all 100 BYN, plus a free 0.0 → IQR=0, median=100.
        # Old fallback band [20, 500] dropped 0.0; now it survives.
        prices = [0.0] + [100.0] * 8
        kept = _remove_outliers(prices)
        assert 0.0 in kept
        assert kept.count(100.0) == 8



def test_sort_listings_cheap_does_not_treat_zero_delta_as_inf() -> None:
    """B-09 follow-up: an ad priced exactly at median (delta=0.0) must NOT
    sort to the tail with negotiable ads. 0.0 is falsy but is a valid delta."""
    # Prices in kopecks. Median of [800, 1000, 1200] = 1000 BYN (after /100).
    cheap_ad = {
        "ad_id": 1, "subject": "Cheap", "price_byn": 80000,
        "ad_link": "x", "list_time": "",
    }
    fair_ad = {
        "ad_id": 2, "subject": "Fair", "price_byn": 100000,
        "ad_link": "x", "list_time": "",
    }
    expensive_ad = {
        "ad_id": 3, "subject": "Expensive", "price_byn": 120000,
        "ad_link": "x", "list_time": "",
    }
    negotiable_ad = {
        "ad_id": 4, "subject": "Negotiable", "price_byn": 0,
        "ad_link": "x", "list_time": "",
    }

    ordered = sort_listings(
        [fair_ad, cheap_ad, negotiable_ad, expensive_ad],
        "cheap",
        median=1000.0,
    )
    ids = [ad["ad_id"] for ad in ordered]
    # cheap < fair < expensive < negotiable (inf)
    assert ids.index(1) < ids.index(2) < ids.index(4)
    # Fair (delta=0.0) must NOT be at the tail with negotiable.
    assert ids[-1] == 4


def test_filter_deal_ads_excludes_negotiable() -> None:
    """B-09: negotiable ads (delta=None) must not match a discount filter."""
    priced_ad = {"ad_id": 1, "price_byn": 80000, "subject": "Cheap", "body": ""}
    at_median_ad = {"ad_id": 3, "price_byn": 100000, "subject": "Fair", "body": ""}
    negotiable_ad = {"ad_id": 2, "price_byn": 0, "subject": "Шкаф", "body": "Цена договорная"}
    # Median of [800, 1000] = 900 BYN; priced_ad is 800 → ~-11% discount.
    result = filter_deal_ads([priced_ad, at_median_ad, negotiable_ad], 900.0, 5.0)
    assert [ad["ad_id"] for ad in result] == [1]


def test_compute_price_stats_small_sample_unreliable() -> None:
    """LOGIC-NEW-4: fewer than 3 prices yields reliable=False with q1=q3=median."""
    stats_one = compute_price_stats([500.0])
    assert stats_one.reliable is False
    assert stats_one.q1 == stats_one.median
    assert stats_one.q3 == stats_one.median

    stats_two = compute_price_stats([400.0, 600.0])
    assert stats_two.reliable is False
    assert stats_two.q1 == stats_two.median
    assert stats_two.q3 == stats_two.median

    # 3+ prices should be reliable
    stats_three = compute_price_stats([100.0, 200.0, 300.0])
    assert stats_three.reliable is True
