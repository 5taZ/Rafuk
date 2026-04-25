"""Tests for fair-price band classification + scope-aware delta logic.

These lock in the symmetric thresholds so we don't accidentally drift
back to the asymmetric ranges that biased "fair" toward "below market".
"""

from __future__ import annotations

import pytest

from api.services.market_signals import (
    FAIR_BAND_ABOVE,
    FAIR_BAND_BELOW,
    FAIR_BAND_DEEP_DISCOUNT,
    FAIR_BAND_DEEP_OVERPRICED,
    fair_price_band,
    fair_price_label,
)


@pytest.mark.parametrize(
    ("delta", "expected_band", "expected_label"),
    [
        (-30.0, "deep_discount", "Сильно ниже рынка"),
        (-25.0, "below_market", "Ниже рынка"),
        (-15.0, "below_market", "Ниже рынка"),
        (-9.9, "fair", "По рынку"),
        (0.0, "fair", "По рынку"),
        (5.0, "fair", "По рынку"),
        (9.9, "fair", "По рынку"),
        (10.0, "fair", "По рынку"),  # boundary — inclusive on the upper end
        (10.1, "above_market", "Выше рынка"),
        (20.0, "above_market", "Выше рынка"),
        (25.0, "above_market", "Выше рынка"),
        (30.0, "high", "Сильно выше рынка"),
        (50.0, "high", "Сильно выше рынка"),
    ],
)
def test_fair_price_band_thresholds(
    delta: float,
    expected_band: str,
    expected_label: str,
) -> None:
    band = fair_price_band(delta)
    assert band == expected_band
    assert fair_price_label(band) == expected_label


def test_fair_price_band_handles_none() -> None:
    assert fair_price_band(None) is None
    assert fair_price_label(None) is None


def test_fair_band_thresholds_are_symmetric() -> None:
    """The 'fair' window must be balanced — a -10% deal and a +10% deal
    should both be 'По рынку', otherwise the badge unfairly punishes
    sellers asking just slightly above median.
    """
    assert FAIR_BAND_BELOW == -FAIR_BAND_ABOVE
    assert FAIR_BAND_DEEP_DISCOUNT == -FAIR_BAND_DEEP_OVERPRICED
