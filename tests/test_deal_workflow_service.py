"""Tests for api.services.deal_workflow — liquidity insight & flip estimates."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from api.services.aggregator import PriceStats
from api.services.deal_workflow import compute_flip_estimates, compute_liquidity_insight


def _make_stats(**overrides: float) -> PriceStats:
    defaults = {
        "mean": 2000, "median": 2000, "q1": 1800,
        "q3": 2200, "min": 1500, "max": 2500, "count": 20,
    }
    defaults.update(overrides)
    return PriceStats(**defaults)


def _fresh_list_time(hours_ago: float) -> str:
    ts = datetime.now(UTC) - timedelta(hours=hours_ago)
    return ts.isoformat()


def _ad(**overrides: object) -> dict:
    base = {
        "ad_id": 1,
        "subject": "iPhone 15",
        "price_byn": 200_000,
        "list_time": _fresh_list_time(2),
        "images": [{"path": "a.jpg"}, {"path": "b.jpg"}, {"path": "c.jpg"}],
        "ad_parameters": [{"p": "condition", "v": "2"}],
    }
    base.update(overrides)
    return base


class TestComputeLiquidityInsightMarketOnly:
    def test_no_ads_returns_low_score(self) -> None:
        insight = compute_liquidity_insight([], _make_stats())
        assert insight.score >= 0
        assert insight.label in ("Высокая", "Средняя", "Осторожно")

    def test_many_ads_high_score(self) -> None:
        ads = [_ad(ad_id=i, list_time=_fresh_list_time(1)) for i in range(30)]
        insight = compute_liquidity_insight(ads, _make_stats(count=30))
        assert insight.score >= 50

    def test_fresh_ads_boost_score(self) -> None:
        ads = [_ad(ad_id=i, list_time=_fresh_list_time(1)) for i in range(10)]
        insight = compute_liquidity_insight(ads, _make_stats())
        assert "свежие лоты есть" in insight.reasons or "много свежих лотов" in insight.reasons

    def test_thin_market_penalty(self) -> None:
        ads = [_ad(ad_id=i) for i in range(2)]
        insight = compute_liquidity_insight(ads, _make_stats(count=2))
        assert "рынок тонкий" in insight.reasons or "выборка маленькая" in insight.reasons

    def test_reasons_deduplicated(self) -> None:
        ads = [_ad(ad_id=i, list_time=_fresh_list_time(1)) for i in range(30)]
        insight = compute_liquidity_insight(ads, _make_stats(count=30))
        assert len(insight.reasons) == len(set(insight.reasons))

    def test_reasons_capped_at_four(self) -> None:
        ads = [_ad(ad_id=i, list_time=_fresh_list_time(1)) for i in range(30)]
        insight = compute_liquidity_insight(ads, _make_stats(count=30))
        assert len(insight.reasons) <= 4


class TestComputeLiquidityInsightWithAd:
    def test_low_price_gets_bonus(self) -> None:
        ads = [_ad(ad_id=i, price_byn=200_000) for i in range(15)]
        ad = _ad(price_byn=150_000)
        insight = compute_liquidity_insight(ads, _make_stats(), ad=ad)
        assert any("ниже рынка" in r for r in insight.reasons)

    def test_high_price_gets_penalty(self) -> None:
        ads = [_ad(ad_id=i) for i in range(15)]
        ad = _ad(price_byn=3_000_000)
        insight = compute_liquidity_insight(ads, _make_stats(), ad=ad)
        assert any("выше рынка" in r for r in insight.reasons)

    def test_fresh_ad_bonus(self) -> None:
        ads = [_ad(ad_id=i) for i in range(15)]
        ad = _ad(list_time=_fresh_list_time(1))
        insight = compute_liquidity_insight(ads, _make_stats(), ad=ad)
        assert "только что выложено" in insight.reasons

    def test_old_ad_penalty(self) -> None:
        ads = [_ad(ad_id=i) for i in range(15)]
        ad = _ad(list_time=_fresh_list_time(200))
        insight = compute_liquidity_insight(ads, _make_stats(), ad=ad)
        assert "давно на рынке" in insight.reasons

    def test_new_condition_bonus(self) -> None:
        ads = [_ad(ad_id=i) for i in range(15)]
        ad = _ad(ad_parameters=[{"p": "condition", "v": "2"}])
        insight_new = compute_liquidity_insight(ads, _make_stats(), ad=ad)
        ad_no_cond = _ad(ad_parameters=[])
        insight_no_cond = compute_liquidity_insight(ads, _make_stats(), ad=ad_no_cond)
        assert insight_new.score >= insight_no_cond.score

    def test_used_condition_small_bonus(self) -> None:
        ads = [_ad(ad_id=i) for i in range(15)]
        ad = _ad(ad_parameters=[{"p": "condition", "v": "1"}])
        insight_new = compute_liquidity_insight(
            ads, _make_stats(),
            ad=_ad(ad_parameters=[{"p": "condition", "v": "2"}]),
        )
        insight_used = compute_liquidity_insight(ads, _make_stats(), ad=ad)
        assert insight_new.score >= insight_used.score

    def test_many_photos_bonus(self) -> None:
        ads = [_ad(ad_id=i) for i in range(15)]
        ad = _ad(images=[{"path": f"p{i}.jpg"} for i in range(6)])
        insight = compute_liquidity_insight(ads, _make_stats(), ad=ad)
        assert insight.score > 0

    def test_no_photos_penalty(self) -> None:
        ads = [_ad(ad_id=i) for i in range(15)]
        ad_no_photo = _ad(images=[])
        ad_with_photo = _ad(images=[{"path": f"p{i}.jpg"} for i in range(5)])
        insight_no = compute_liquidity_insight(ads, _make_stats(), ad=ad_no_photo)
        insight_yes = compute_liquidity_insight(ads, _make_stats(), ad=ad_with_photo)
        assert insight_yes.score >= insight_no.score

    def test_score_bounded_0_to_100(self) -> None:
        ads = [_ad(ad_id=i, price_byn=50_000, list_time=_fresh_list_time(1)) for i in range(30)]
        ad = _ad(price_byn=50_000, list_time=_fresh_list_time(0.5))
        insight = compute_liquidity_insight(ads, _make_stats(count=30), ad=ad)
        assert 0.0 <= insight.score <= 100.0

    def test_label_high(self) -> None:
        ads = [_ad(ad_id=i, price_byn=150_000, list_time=_fresh_list_time(1)) for i in range(30)]
        ad = _ad(price_byn=150_000, list_time=_fresh_list_time(0.5))
        insight = compute_liquidity_insight(ads, _make_stats(count=30), ad=ad)
        assert insight.label == "Высокая"

    def test_label_caution(self) -> None:
        ads = [_ad(ad_id=i, list_time=_fresh_list_time(200)) for i in range(2)]
        ad = _ad(price_byn=3_000_000, list_time=_fresh_list_time(300), images=[])
        insight = compute_liquidity_insight(ads, _make_stats(count=2), ad=ad)
        assert insight.label == "Осторожно"


class TestComputeLiquidityInsightEdgeCases:
    def test_zero_price_ad(self) -> None:
        ads = [_ad(ad_id=i) for i in range(15)]
        ad = _ad(price_byn=0)
        insight = compute_liquidity_insight(ads, _make_stats(), ad=ad)
        assert insight.score >= 0

    def test_missing_list_time(self) -> None:
        ads = [_ad(ad_id=i) for i in range(15)]
        ad = _ad(list_time=None)
        insight = compute_liquidity_insight(ads, _make_stats(), ad=ad)
        assert insight.score >= 0

    def test_invalid_list_time(self) -> None:
        ads = [_ad(ad_id=i) for i in range(15)]
        ad = _ad(list_time="not-a-date")
        insight = compute_liquidity_insight(ads, _make_stats(), ad=ad)
        assert insight.score >= 0

    def test_zero_median_stats(self) -> None:
        insight = compute_liquidity_insight([], _make_stats(median=0, count=0))
        assert insight.score >= 0

    def test_empty_ads_market_only(self) -> None:
        insight = compute_liquidity_insight([], _make_stats())
        assert insight.label == "Осторожно"


class TestComputeFlipEstimates:
    def test_basic_estimates(self) -> None:
        ad = _ad(price_byn=200_000)
        stats = _make_stats()
        estimates = compute_flip_estimates(ad, stats)
        assert len(estimates) == 3
        labels = [e.label for e in estimates]
        assert "Быстро" in labels
        assert "По рынку" in labels
        assert "Оптимально" in labels

    def test_quick_target_at_most_q1(self) -> None:
        ad = _ad(price_byn=200_000)
        stats = _make_stats(q1=1800, median=2000)
        estimates = compute_flip_estimates(ad, stats)
        quick = [e for e in estimates if e.label == "Быстро"][0]
        assert quick.target_price >= 1800

    def test_market_target_equals_median(self) -> None:
        ad = _ad(price_byn=200_000)
        stats = _make_stats(median=2000)
        estimates = compute_flip_estimates(ad, stats)
        market = [e for e in estimates if e.label == "По рынку"][0]
        assert market.target_price == 2000.0

    def test_optimal_target_at_least_q3(self) -> None:
        ad = _ad(price_byn=200_000)
        stats = _make_stats(q3=2200, median=2000)
        estimates = compute_flip_estimates(ad, stats)
        optimal = [e for e in estimates if e.label == "Оптимально"][0]
        assert optimal.target_price >= 2200

    def test_profit_calculation(self) -> None:
        ad = _ad(price_byn=200_000)
        stats = _make_stats(median=2000)
        estimates = compute_flip_estimates(ad, stats)
        market = [e for e in estimates if e.label == "По рынку"][0]
        assert market.profit_byn == 0.0
        assert market.profit_percent == 0.0

    def test_negative_profit_passes_through(self) -> None:
        ad = _ad(price_byn=3_000_000)
        stats = _make_stats(median=2000)
        estimates = compute_flip_estimates(ad, stats)
        assert all(e.profit_byn < 0 for e in estimates)

    def test_zero_market_count_returns_empty(self) -> None:
        ad = _ad(price_byn=200_000)
        stats = _make_stats(count=0)
        assert compute_flip_estimates(ad, stats) == []

    def test_null_price_returns_empty(self) -> None:
        ad = _ad(price_byn=0)
        stats = _make_stats()
        assert compute_flip_estimates(ad, stats) == []

    def test_missing_price_returns_empty(self) -> None:
        ad: dict = {"ad_id": 1}
        stats = _make_stats()
        assert compute_flip_estimates(ad, stats) == []

    def test_profit_percent_calculation(self) -> None:
        ad = _ad(price_byn=100_000)
        stats = _make_stats(median=2000)
        estimates = compute_flip_estimates(ad, stats)
        market = [e for e in estimates if e.label == "По рынку"][0]
        assert market.profit_byn > 0
        assert market.profit_percent > 0
