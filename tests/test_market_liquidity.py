from __future__ import annotations

from datetime import UTC, datetime, timedelta

from api.services.market_liquidity import estimate_liquidity
from api.services.watchlist_market import build_market_comparables, compare_listing_market

NOW = datetime(2026, 11, 5, 12, tzinfo=UTC)


def _phone(ad_id: int, **overrides: object) -> dict:
    return {
        "ad_id": ad_id,
        "subject": "iPhone 13 128 GB",
        "category": "17010",
        "price_byn": 60_000,
        "list_time": NOW.isoformat(),
        "ad_parameters": [{"p": "condition", "vl": "Б/у"}],
        **overrides,
    }


def test_market_rates_each_price_against_current_peers_without_duplicates() -> None:
    cheap = _phone(1, price_byn=50_000)
    expensive = _phone(2, price_byn=75_000)
    peers = [
        cheap, expensive, _phone(3), _phone(4), _phone(4),
        _phone(5, subject="iPhone 13 256 GB", price_byn=30_000),
        _phone(6, ad_parameters=[{"p": "condition", "vl": "Новый"}], price_byn=30_000),
    ]
    estimates = {
        str(ad["ad_id"]): estimate_liquidity(ad, build_market_comparables(ad, peers), now=NOW)
        for ad in (cheap, expensive)
    }
    assert estimates["1"].label == "Высокая"
    assert estimates["2"].label == "Низкая"
    assert estimates["1"].sample_size == estimates["2"].sample_size == 3
    assert estimates["1"].reasons[0] == "Цена на 17% ниже медианы аналогов."


def test_older_supply_lowers_estimate_at_market_price() -> None:
    ad = _phone(1)
    old = (NOW - timedelta(days=20)).isoformat()
    peers = [_phone(2, list_time=old), _phone(3, list_time=old), _phone(4)]
    insight = estimate_liquidity(ad, build_market_comparables(ad, peers), now=NOW)
    assert insight.label == "Низкая"
    assert "У 2 из 3 аналогов дата в выдаче старше 14 дней." in insight.reasons


def test_sparse_or_unpriced_listings_show_why_an_estimate_is_unavailable() -> None:
    ad = _phone(1)
    negotiable = _phone(2, price_byn=0)
    peers = [ad, negotiable, _phone(3)]
    estimates = {
        str(target["ad_id"]): estimate_liquidity(
            target, build_market_comparables(target, peers), now=NOW,
        ) for target in (ad, negotiable)
    }
    assert estimates["1"].label == "Недостаточно аналогов"
    assert estimates["1"].sample_size == 1
    assert estimates["2"].label == "Недостаточно данных"


def test_polo_uses_the_same_year_range_and_peers_as_its_price_badge() -> None:
    def polo(ad_id: int, year: int, price: int) -> dict:
        return {
            "ad_id": ad_id, "subject": "Volkswagen Polo", "category": 2010,
            "price_byn": price * 100, "list_time": NOW.isoformat(),
            "ad_parameters": [
                {"p": "regdate", "v": str(year)}, {"p": "condition", "vl": "Б/у"},
            ],
        }

    target = polo(1, 2012, 19_500)
    peers = [polo(ad_id, 2010 + ad_id % 5, 23_000) for ad_id in range(2, 35)]
    peers.append(polo(35, 2017, 30_000))
    comparison = compare_listing_market(target, peers)
    insight = estimate_liquidity(target, build_market_comparables(target, peers), now=NOW)
    assert comparison.comparable_count == insight.sample_size == 33
    assert comparison.median_byn == 23_000
    assert insight.label == "Высокая"
    assert insight.reasons[0] == "Цена на 15% ниже медианы аналогов."
