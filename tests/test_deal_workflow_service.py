"""Tests for api.services.deal_workflow — flip estimates."""

from __future__ import annotations

from api.services.aggregator import PriceStats
from api.services.deal_workflow import compute_flip_estimates


def _make_stats(**overrides: float) -> PriceStats:
    defaults = {
        "mean": 2000, "median": 2000, "q1": 1800,
        "q3": 2200, "min": 1500, "max": 2500, "count": 20,
    }
    defaults.update(overrides)
    return PriceStats(**defaults)


def _ad(**overrides: object) -> dict:
    return {"price_byn": 200_000, **overrides}


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

    def test_high_byn_median_is_a_real_market(self) -> None:
        """BE-02: a 150 000 BYN median (flats, cars) used to raise."""
        ad = _ad(price_byn=14_000_000)  # 140 000 BYN in kopecks
        stats = _make_stats(
            median=150_000, q1=140_000, q3=160_000, min=120_000, max=200_000, mean=150_000,
        )
        market = [e for e in compute_flip_estimates(ad, stats) if e.label == "По рынку"][0]
        assert market.profit_byn == 10_000

    def test_kopeck_median_degrades_to_no_estimates(self) -> None:
        stats = _make_stats(median=50_000_000)
        assert compute_flip_estimates(_ad(), stats) == []

    def test_profit_percent_calculation(self) -> None:
        ad = _ad(price_byn=100_000)
        stats = _make_stats(median=2000)
        estimates = compute_flip_estimates(ad, stats)
        market = [e for e in estimates if e.label == "По рынку"][0]
        assert market.profit_byn > 0
        assert market.profit_percent > 0


class TestComputeFlipEstimatesExpenses:
    """E-FIND-08: ``expenses_byn`` shifts every tier's profit_byn by
    the same amount (negatively) so the projection becomes net
    instead of gross. ``profit_percent`` denominator stays at the
    listing price for cross-user comparability.
    """

    def test_default_expenses_zero_matches_legacy_behaviour(self) -> None:
        ad = _ad(price_byn=200_000)
        stats = _make_stats(median=2000)
        without = compute_flip_estimates(ad, stats)
        with_zero = compute_flip_estimates(ad, stats, expenses_byn=0.0)
        assert [(e.label, e.profit_byn) for e in without] == [
            (e.label, e.profit_byn) for e in with_zero
        ]

    def test_expenses_subtract_from_each_tier(self) -> None:
        ad = _ad(price_byn=200_000)  # 2000 BYN listing price
        stats = _make_stats(q1=1800, median=2000, q3=2200)
        gross = {e.label: e.profit_byn for e in compute_flip_estimates(ad, stats)}
        net = {
            e.label: e.profit_byn
            for e in compute_flip_estimates(ad, stats, expenses_byn=150)
        }
        for label, gross_profit in gross.items():
            assert net[label] == round(gross_profit - 150, 2)

    def test_expenses_can_flip_market_target_to_negative(self) -> None:
        ad = _ad(price_byn=200_000)
        stats = _make_stats(median=2000)
        # Listing at median (zero gross) + 100 BYN of fix-up costs
        # → net flip of −100 BYN at the market tier.
        net = [
            e for e in compute_flip_estimates(ad, stats, expenses_byn=100)
            if e.label == "По рынку"
        ][0]
        assert net.profit_byn == -100.0

    def test_negative_expenses_clamped_to_zero(self) -> None:
        # Defensive: we never trust user input to be non-negative.
        ad = _ad(price_byn=200_000)
        stats = _make_stats(median=2000)
        expected = compute_flip_estimates(ad, stats, expenses_byn=0.0)
        actual = compute_flip_estimates(ad, stats, expenses_byn=-99.0)
        assert [e.profit_byn for e in actual] == [e.profit_byn for e in expected]
