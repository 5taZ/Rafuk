from __future__ import annotations

from types import SimpleNamespace

from api.services.aggregator import PriceStats
from api.services.reseller_tools import (
    SCORING,
    analyze_query_text,
    compute_deal_score,
    matches_tracker_filters,
)


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
    with patch("api.services.reseller_tools.detect_anomaly_flags", return_value=["anomaly1", "anomaly2"]):
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
    assert matches_tracker_filters(ad_match, market_stats=stats, config_keyword="iphone 15 pro") is True
    assert matches_tracker_filters(ad_no_match, market_stats=stats, config_keyword="iphone 15 pro") is False


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
