"""Battery runtime estimator — honest, data-gated extrapolation."""

import pytest

from monitor.battery import estimate_runtime, UNCERTAINTY
from monitor.history import HistoryPoint

DAY = 86400.0
NOW = 100 * DAY


def _pts(values, step_days=1.0):
    """Battery percentages, oldest first, ending at NOW."""
    n = len(values)
    return [HistoryPoint(t=NOW - (n - 1 - i) * step_days * DAY, battery_pct=v)
            for i, v in enumerate(values)]


def test_no_battery_data_means_no_estimate():
    assert estimate_runtime([HistoryPoint(t=NOW, rssi=-80)], NOW) is None
    assert estimate_runtime([], NOW) is None


def test_too_short_a_trend_means_no_estimate():
    assert estimate_runtime(_pts([80, 79], step_days=0.5), NOW) is None


def test_declining_battery_extrapolates_days_to_red():
    est = estimate_runtime(_pts([90, 87, 84, 81, 78, 75]), NOW)   # -3%/day
    assert est["kind"] == "days"
    # (75 - 10) / 3 ≈ 21.7 days
    assert est["days"] == pytest.approx(21.7, abs=0.5)
    assert "days remaining" in est["text"]
    assert est["note"] == UNCERTAINTY


def test_solar_holding_charge_reads_indefinite():
    est = estimate_runtime(_pts([80, 81, 80, 79, 80, 81]), NOW)
    assert est["kind"] == "indefinite"
    assert "Solar maintaining charge" in est["text"]


def test_already_below_red_reads_zero_days():
    est = estimate_runtime(_pts([20, 17, 14, 11, 8]), NOW)
    assert est["kind"] == "days" and est["days"] == 0.0
