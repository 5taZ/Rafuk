from __future__ import annotations

from datetime import UTC, datetime, timedelta

from api.models import QuerySnapshot
from api.services.history_service import detect_trend_reversal_up


def _snap(median: float, when: datetime) -> QuerySnapshot:
    return QuerySnapshot(
        query="iphone",
        snapshot_at=when,
        total_results=10,
        analyzed_count=10,
        mean_byn=median,
        median_byn=median,
        min_byn=median * 0.5,
        max_byn=median * 2.0,
    )


def _build_series(values: list[float], *, base: datetime) -> list[QuerySnapshot]:
    """Build one snapshot per day (UTC midnight) with the given medians.

    The list is ordered oldest→newest; index `i` corresponds to day `base+i`.
    """
    return [_snap(v, base + timedelta(days=i)) for i, v in enumerate(values)]


def test_detect_trend_reversal_up_classic_v_shape() -> None:
    """Price falls 1000→700 over 5 days, then rebounds to 770 today —
    decline 30 %, rebound ~10 % → fires.
    """
    base = datetime(2026, 4, 1, tzinfo=UTC)
    snaps = _build_series([1000, 900, 800, 750, 700, 770], base=base)
    signal = detect_trend_reversal_up(snaps)
    assert signal is not None
    assert signal.today_byn == 770.0
    assert signal.low_byn == 700.0
    assert signal.pre_high_byn == 1000.0
    assert signal.decline_pct == 30.0
    assert signal.rebound_pct == 10.0


def test_detect_trend_reversal_up_returns_none_when_still_falling() -> None:
    base = datetime(2026, 4, 1, tzinfo=UTC)
    snaps = _build_series([1000, 950, 900, 850, 800, 780], base=base)
    assert detect_trend_reversal_up(snaps) is None


def test_detect_trend_reversal_up_returns_none_for_steady_rise() -> None:
    base = datetime(2026, 4, 1, tzinfo=UTC)
    snaps = _build_series([800, 850, 900, 950, 1000, 1050], base=base)
    assert detect_trend_reversal_up(snaps) is None


def test_detect_trend_reversal_up_requires_min_days() -> None:
    base = datetime(2026, 4, 1, tzinfo=UTC)
    snaps = _build_series([1000, 800, 850], base=base)
    assert detect_trend_reversal_up(snaps) is None


def test_detect_trend_reversal_up_skips_tiny_rebound_below_threshold() -> None:
    base = datetime(2026, 4, 1, tzinfo=UTC)
    snaps = _build_series([1000, 900, 800, 750, 700, 710], base=base)
    assert detect_trend_reversal_up(snaps) is None


def test_detect_trend_reversal_up_skips_tiny_decline_below_threshold() -> None:
    base = datetime(2026, 4, 1, tzinfo=UTC)
    snaps = _build_series([1000, 995, 990, 988, 985, 1015], base=base)
    assert detect_trend_reversal_up(snaps) is None


def test_detect_trend_reversal_up_collapses_intraday_snapshots() -> None:
    """6 days of data, but day 0 has 3 hourly snapshots — function should
    collapse them via median per day.
    """
    base = datetime(2026, 4, 1, tzinfo=UTC)
    snaps: list[QuerySnapshot] = []
    snaps.append(_snap(995, base))
    snaps.append(_snap(1000, base + timedelta(hours=6)))
    snaps.append(_snap(1005, base + timedelta(hours=12)))
    snaps += _build_series([900, 800, 750, 700, 770], base=base + timedelta(days=1))
    signal = detect_trend_reversal_up(snaps)
    assert signal is not None
    assert signal.pre_high_byn == 1000.0  # median of 995/1000/1005


def test_detect_trend_reversal_up_ignores_zero_and_negative_medians() -> None:
    base = datetime(2026, 4, 1, tzinfo=UTC)
    snaps = _build_series([1000, 0, 800, 750, 700, 770], base=base)
    # The zero-day bucket is dropped → 5 valid days remain → still fires
    signal = detect_trend_reversal_up(snaps)
    assert signal is not None


def test_detect_trend_reversal_up_low_must_be_recent() -> None:
    """If the recent low is older than 3 buckets back, that's not a
    reversal anymore — it's an established new uptrend.
    """
    base = datetime(2026, 4, 1, tzinfo=UTC)
    snaps = _build_series([1000, 700, 750, 800, 850, 900], base=base)
    assert detect_trend_reversal_up(snaps) is None


def test_detect_trend_reversal_up_returns_none_for_empty() -> None:
    assert detect_trend_reversal_up([]) is None
