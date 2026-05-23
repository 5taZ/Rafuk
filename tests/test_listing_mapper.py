"""Tests for api.services.listing_mapper — listing mapping logic."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from api.services.aggregator import (
    PriceStats,
    compute_category_price_stats,
    compute_price_stats,
    extract_prices,
)
from api.services.currency_service import CurrencyService
from api.services.listing_mapper import (
    _parameter_value,
    _stringify_value,
    build_listing_item,
    collect_fields,
    extract_seller_rating,
    first_image_url,
    image_url,
)


def _make_stats(**overrides: float) -> PriceStats:
    defaults = {
        "mean": 2000, "median": 2000, "q1": 1800,
        "q3": 2200, "min": 1500, "max": 2500, "count": 20,
    }
    defaults.update(overrides)
    return PriceStats(**defaults)


def _currency_service() -> CurrencyService:
    svc = MagicMock(spec=CurrencyService)
    svc.convert_from_byn.side_effect = lambda amt, cur, rates: round(amt, 2)
    return svc


def _ad(**overrides: object) -> dict:
    base = {
        "ad_id": 42,
        "subject": "iPhone 15 128GB",
        "price_byn": 200_000,
        "ad_link": "https://kufar.by/item/42",
        "list_time": "2026-04-01T10:00:00",
        "region_id": 6,
        "company_ad": False,
        "body": "Отличный телефон",
        "ad_parameters": [
            {"p": "condition", "v": "Новый", "pl": "Состояние"},
        ],
        "account_parameters": [],
        "images": [],
    }
    base.update(overrides)
    return base


RATES = {"USD": 3.0}


class TestStringifyValue:
    @pytest.mark.parametrize("empty", [None, "", [], {}])
    def test_empty_returns_none(self, empty: object) -> None:
        assert _stringify_value(empty) is None

    def test_bool_true(self) -> None:
        assert _stringify_value(True) == "Да"

    def test_bool_false(self) -> None:
        assert _stringify_value(False) == "Нет"

    def test_list_joins(self) -> None:
        assert _stringify_value(["a", "b"]) == "a, b"

    def test_list_filters_empty(self) -> None:
        assert _stringify_value(["a", "", "b"]) == "a, b"

    def test_list_all_empty_returns_none(self) -> None:
        assert _stringify_value(["", " "]) is None

    def test_string_strips(self) -> None:
        assert _stringify_value("  hello  ") == "hello"

    def test_number_converted(self) -> None:
        assert _stringify_value(42) == "42"


class TestParameterValue:
    def test_vl_preferred(self) -> None:
        assert _parameter_value({"vl": "Видимый", "v": "raw"}) == "Видимый"

    def test_falls_back_to_v(self) -> None:
        assert _parameter_value({"v": "raw"}) == "raw"

    def test_both_empty(self) -> None:
        assert _parameter_value({"vl": "", "v": ""}) is None


class TestImageUrl:
    def test_valid_path(self) -> None:
        url = image_url({"path": "abc/123.jpg"})
        assert url == "https://rms.kufar.by/v1/gallery/abc/123.jpg"

    def test_empty_path(self) -> None:
        assert image_url({"path": ""}) is None

    def test_none_path(self) -> None:
        assert image_url({"path": None}) is None

    def test_non_string_path(self) -> None:
        assert image_url({"path": 123}) is None


class TestFirstImageUrl:
    def test_first_valid_image(self) -> None:
        ad = {"images": [{"path": ""}, {"path": "pic.jpg"}]}
        assert first_image_url(ad) == "https://rms.kufar.by/v1/gallery/pic.jpg"

    def test_no_images(self) -> None:
        assert first_image_url({}) is None

    def test_empty_images(self) -> None:
        assert first_image_url({"images": []}) is None


class TestCollectFields:
    def test_basic_fields(self) -> None:
        items = [
            {"p": "condition", "pl": "Состояние", "vl": "Новый"},
        ]
        fields = collect_fields(items)
        assert len(fields) == 1
        assert fields[0].label == "Состояние"
        assert fields[0].value == "Новый"

    def test_ignored_keys(self) -> None:
        items = [
            {"p": "users_synonyms", "pl": "Синонимы", "vl": "test"},
            {"p": "condition", "pl": "Состояние", "vl": "Б/у"},
        ]
        fields = collect_fields(items, ignored={"users_synonyms"})
        assert len(fields) == 1

    def test_missing_label_skipped(self) -> None:
        items = [{"p": "x", "vl": "val"}]
        assert collect_fields(items) == []

    def test_missing_value_skipped(self) -> None:
        items = [{"p": "x", "pl": "Label"}]
        assert collect_fields(items) == []


class TestExtractSellerRating:
    def test_from_account_parameters(self) -> None:
        ad = {"account_parameters": [{"p": "seller_rating", "v": "4.7"}]}
        assert extract_seller_rating(ad) == 4.7

    def test_from_top_level(self) -> None:
        ad = {"seller_rating": 4.5, "account_parameters": []}
        assert extract_seller_rating(ad) == 4.5

    def test_no_rating(self) -> None:
        assert extract_seller_rating({"account_parameters": []}) is None

    def test_invalid_value_skipped(self) -> None:
        ad = {"account_parameters": [{"p": "seller_rating", "v": "abc"}]}
        assert extract_seller_rating(ad) is None


class TestBuildListingItem:
    def test_basic_mapping(self) -> None:
        stats = _make_stats()
        item = build_listing_item(
            _ad(),
            query="iphone 15",
            currency="BYN",
            rates=RATES,
            currency_service=_currency_service(),
            median_byn=2000.0,
            market_stats=stats,
        )
        assert item.ad_id == 42
        assert item.title == "iPhone 15 128GB"
        assert item.price == 2000.0
        assert item.currency == "BYN"
        assert item.link == "https://kufar.by/item/42"
        assert item.company_ad is False

    def test_company_ad_flag(self) -> None:
        item = build_listing_item(
            _ad(company_ad=True),
            query="x",
            currency="BYN",
            rates=RATES,
            currency_service=_currency_service(),
            median_byn=2000.0,
            market_stats=_make_stats(),
        )
        assert item.company_ad is True
        assert item.seller_type == "shop"

    def test_private_seller_type(self) -> None:
        item = build_listing_item(
            _ad(company_ad=False),
            query="x",
            currency="BYN",
            rates=RATES,
            currency_service=_currency_service(),
            median_byn=2000.0,
            market_stats=_make_stats(),
        )
        assert item.seller_type == "private"

    def test_seller_type_from_param(self) -> None:
        ad = _ad(ad_parameters=[{"p": "seller_type", "v": "shop"}])
        item = build_listing_item(
            ad,
            query="x",
            currency="BYN",
            rates=RATES,
            currency_service=_currency_service(),
            median_byn=2000.0,
            market_stats=_make_stats(),
        )
        assert item.seller_type == "shop"

    def test_missing_fields(self) -> None:
        ad: dict = {"ad_id": 99}
        item = build_listing_item(
            ad,
            query="x",
            currency="BYN",
            rates=RATES,
            currency_service=_currency_service(),
            median_byn=2000.0,
            market_stats=_make_stats(),
        )
        assert item.ad_id == 99
        assert item.title == ""
        # No price_byn field at all -> None (negotiable: empty ad text has no free keywords)
        assert item.price is None
        assert item.price_type == "negotiable"

    def test_zero_price(self) -> None:
        item = build_listing_item(
            _ad(price_byn=0),
            query="x",
            currency="BYN",
            rates=RATES,
            currency_service=_currency_service(),
            median_byn=2000.0,
            market_stats=_make_stats(),
        )
        # price_byn=0 with no free keywords in ad text -> negotiable -> None
        assert item.price is None
        assert item.price_type == "negotiable"

    def test_zero_price_with_free_keyword(self) -> None:
        ad = _ad(price_byn=0)
        ad["body"] = "Отдам бесплатно в хорошие руки"
        item = build_listing_item(
            ad,
            query="x",
            currency="BYN",
            rates=RATES,
            currency_service=_currency_service(),
            median_byn=2000.0,
            market_stats=_make_stats(),
        )
        # "бесплатно" keyword -> free -> price 0.0
        assert item.price == 0.0
        assert item.price_type == "free"
        # Free vs market median is -100% (full discount), surfaced for UI
        assert item.price_vs_median == -100.0

    def test_negotiable_suppresses_price_delta(self) -> None:
        # The metric function returns 0.0 for negotiable (no signal),
        # but the UI would render that as "≈ по рынку" — falsely
        # implying a fair price. Listing must surface price_vs_median
        # as None so the delta badge is hidden.
        ad = _ad(price_byn=0)
        ad["body"] = "Цена обсуждается, торг уместен"
        item = build_listing_item(
            ad,
            query="x",
            currency="BYN",
            rates=RATES,
            currency_service=_currency_service(),
            median_byn=2000.0,
            market_stats=_make_stats(),
        )
        assert item.price is None
        assert item.price_type == "negotiable"
        assert item.price_vs_median is None

    def test_with_thumbnail(self) -> None:
        ad = _ad(images=[{"path": "thumb.jpg"}])
        item = build_listing_item(
            ad,
            query="x",
            currency="BYN",
            rates=RATES,
            currency_service=_currency_service(),
            median_byn=2000.0,
            market_stats=_make_stats(),
        )
        assert item.thumbnail == "https://rms.kufar.by/v1/gallery/thumb.jpg"

    def test_with_cluster_cache(self) -> None:
        cluster = _make_stats(median=1900, q1=1700, q3=2100)
        item = build_listing_item(
            _ad(),
            query="x",
            currency="BYN",
            rates=RATES,
            currency_service=_currency_service(),
            median_byn=2000.0,
            market_stats=_make_stats(),
            cluster_cache={42: cluster},
        )
        assert item.ad_id == 42

    def test_mixed_parts_reference_suppresses_noisy_motor_delta(self) -> None:
        part_param = {"p": "category", "v": "2040", "vl": "Запчасти"}
        ads = [
            _ad(
                subject="Volkswagen Polo 1.9 SDI мотор",
                price_byn=196000,
                category="2040",
                ad_parameters=[part_param],
            ),
            _ad(
                ad_id=43,
                subject="Volkswagen Polo поворотники",
                price_byn=1956,
                category="2040",
                ad_parameters=[part_param],
            ),
            _ad(
                ad_id=44,
                subject="Зеркало наружнее левое Volkswagen Polo",
                price_byn=4191,
                category="2040",
                ad_parameters=[part_param],
            ),
            _ad(
                ad_id=45,
                subject="Крышка багажника Volkswagen Polo",
                price_byn=16800,
                category="2040",
                ad_parameters=[part_param],
            ),
        ]
        item = build_listing_item(
            ads[0],
            query="Volkswagen Polo",
            currency="BYN",
            rates=RATES,
            currency_service=_currency_service(),
            median_byn=100.0,
            market_stats=compute_price_stats(extract_prices(ads)),
            category_price_stats=compute_category_price_stats(ads),
            cluster_cache={42: None},
        )

        assert item.price_vs_median is None
        assert item.fair_price_label is None
        assert item.deal_verdict is None
        assert item.anomaly_labels == []

    def test_similar_cluster_keeps_motor_delta_when_comparable_ads_exist(self) -> None:
        part_param = {"p": "category", "v": "2040", "vl": "Запчасти"}
        ad = _ad(
            subject="Volkswagen Polo 1.9 SDI мотор",
            price_byn=196000,
            category="2040",
            ad_parameters=[part_param],
        )
        item = build_listing_item(
            ad,
            query="Volkswagen Polo",
            currency="BYN",
            rates=RATES,
            currency_service=_currency_service(),
            median_byn=100.0,
            market_stats=_make_stats(median=100, q1=40, q3=160),
            category_price_stats={2040: _make_stats(median=100, q1=40, q3=160)},
            cluster_cache={42: _make_stats(median=1960, q1=1800, q3=2100, count=3)},
        )

        assert item.price_vs_median == 0.0
        assert item.price_reference_scope == "similar"
        assert item.price_reference_label == "Похожие объявления"
        assert item.fair_price_label == "По рынку"
        assert item.deal_verdict == "Средняя цена"

    def test_cluster_cache_miss(self) -> None:
        item = build_listing_item(
            _ad(),
            query="x",
            currency="BYN",
            rates=RATES,
            currency_service=_currency_service(),
            median_byn=2000.0,
            market_stats=_make_stats(),
            cluster_cache={},
        )
        assert item.ad_id == 42

    def test_risk_factors_populated(self) -> None:
        item = build_listing_item(
            _ad(price_byn=100_000, body="предоплата"),
            query="x",
            currency="BYN",
            rates=RATES,
            currency_service=_currency_service(),
            median_byn=2000.0,
            market_stats=_make_stats(),
        )
        assert len(item.risk_factors) >= 1

    def test_flip_estimates_populated(self) -> None:
        item = build_listing_item(
            _ad(),
            query="x",
            currency="BYN",
            rates=RATES,
            currency_service=_currency_service(),
            median_byn=2000.0,
            market_stats=_make_stats(),
        )
        assert len(item.flip_estimates) == 3

    def test_flip_estimates_empty_on_zero_market_count(self) -> None:
        item = build_listing_item(
            _ad(),
            query="x",
            currency="BYN",
            rates=RATES,
            currency_service=_currency_service(),
            median_byn=2000.0,
            market_stats=_make_stats(count=0),
        )
        assert item.flip_estimates == []
