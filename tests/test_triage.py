"""Triage scoring engine — pure, no hardware/UI."""

import pytest

from monitor.triage import (
    composite_score, score_to_ring, guidance_text, thermal_color,
    MetricRange, TriageCalibration, TriageSession,
    MIN_CAL_SAMPLES, SNR_DEFAULT,
)


# ---- composite score ------------------------------------------------------

def test_best_inputs_score_near_one_and_bullseye():
    score, usable = composite_score(snr=12, rssi=-70, noise=-118)
    assert score == pytest.approx(1.0, abs=1e-6)
    assert usable is True
    assert score_to_ring(score) == "bullseye"


def test_worst_inputs_score_near_zero_and_freezing():
    score, usable = composite_score(snr=-12.5, rssi=-118, noise=-95)
    assert score == pytest.approx(0.0, abs=1e-6)
    assert score_to_ring(score) == "freezing"


def test_below_decode_floor_is_unusable_and_capped():
    # relatively-ok noise/margin, but SNR below the SF9 floor -> cannot decode
    score, usable = composite_score(snr=-15, rssi=-90, noise=-118)
    assert usable is False
    assert score <= 0.15


def test_snr_dominates_the_weighting():
    # same margin/noise, only SNR differs -> the better-SNR spot must score higher
    hi, _ = composite_score(snr=10, rssi=-90, noise=-110)
    lo, _ = composite_score(snr=-5, rssi=-90, noise=-110)
    assert hi > lo


# ---- ring mapping ---------------------------------------------------------

@pytest.mark.parametrize("score,ring", [
    (0.90, "bullseye"), (0.85, "bullseye"),
    (0.70, "warm"), (0.65, "warm"),
    (0.50, "warming"), (0.45, "warming"),
    (0.30, "cold"), (0.25, "cold"),
    (0.10, "freezing"), (0.0, "freezing"),
])
def test_score_to_ring_boundaries(score, ring):
    assert score_to_ring(score) == ring


# ---- adaptive calibration -------------------------------------------------

def test_metric_range_uses_default_until_enough_samples():
    m = MetricRange(SNR_DEFAULT)
    for _ in range(MIN_CAL_SAMPLES - 1):
        m.add(5.0)
    assert m.bounds() == SNR_DEFAULT


def test_metric_range_learns_observed_range():
    m = MetricRange(SNR_DEFAULT)
    for v in [2, 3, 3, 4, 4, 5, 5, 6, 6, 7]:      # observed 2..7, tight
        m.add(v)
    lo, hi = m.bounds()
    assert lo < hi and 2 <= lo <= 4 and 5 <= hi <= 7   # percentile range, not default
    # a value at the top of the observed range now normalises high
    assert m.normalize(7) > 0.8
    assert m.normalize(2) < 0.2


def test_noise_metric_inverts_lower_is_better():
    cal = TriageCalibration()
    assert cal.noise.normalize(-118) > cal.noise.normalize(-95)   # quieter = better


# ---- guidance -------------------------------------------------------------

def test_guidance_prioritises_locked_then_colder_then_ring():
    assert "Locked" in guidance_text("bullseye", locked=True)
    assert "Colder" in guidance_text("warm", colder=True)
    assert "decode floor" in guidance_text("warm", usable=False)
    assert "Warm" in guidance_text("warm")


def test_guidance_is_emoji_free_for_the_field_pi():
    # the field Pi has no emoji font — every guidance string must be plain ASCII
    for ring in ("freezing", "cold", "warming", "warm", "bullseye"):
        assert guidance_text(ring).isascii()
    assert guidance_text("warm", locked=True).isascii()
    assert guidance_text("warm", colder=True).isascii()
    assert guidance_text("warm", usable=False).isascii()


def test_thermal_ramp_runs_cold_to_hot():
    assert thermal_color(0.0) == thermal_color(-1.0)      # clamps low
    assert thermal_color(1.0) == thermal_color(2.0)       # clamps high
    # the ramp warms: the red channel rises from cold to hot
    assert thermal_color(0.9)[0] > thermal_color(0.1)[0]
    # every output is a valid 0..1 rgb triple
    for t in (0.0, 0.3, 0.6, 1.0):
        c = thermal_color(t)
        assert len(c) == 3 and all(0.0 <= x <= 1.0 for x in c)


# ---- session: debounce / best / lock / direction --------------------------

def _best(session, t):
    return session.feed(snr=12, rssi=-70, noise=-118, t=t)


def test_session_tracks_best_reading():
    s = TriageSession()
    s.feed(snr=-5, rssi=-100, noise=-105, t=0.0)     # mediocre
    for t in (1.0, 2.0, 3.0, 4.0):
        _best(s, t)                                   # settle the window to the best spot
    assert s.best_reading["snr"] == 12
    assert s.best_score > 0.9        # smoothed reading (best-stable, not a raw spike)


def test_session_debounce_averages_the_window():
    s = TriageSession()
    r0 = s.feed(snr=12, rssi=-70, noise=-118, t=0.0)   # ~1.0
    r1 = s.feed(snr=-12.5, rssi=-118, noise=-95, t=1.0)  # ~0.0
    # second reading is the mean of the 3s window, not the raw 0.0
    assert 0.2 < r1["score"] < 0.8
    assert r0["score"] > r1["score"]


def test_session_locks_after_stable_bullseye_hold():
    s = TriageSession()
    assert _best(s, 0.0)["locked"] is False
    assert _best(s, 1.0)["locked"] is False
    assert _best(s, 2.0)["locked"] is False
    assert _best(s, 3.0)["locked"] is True         # 3s stable in the bullseye
    assert "Locked" in _best(s, 3.0)["guidance"]


def test_session_detects_colder_after_overshoot():
    s = TriageSession()
    for t in range(0, 5):
        _best(s, float(t))                          # settle hot
    cold = s.feed(snr=-6, rssi=-105, noise=-100, t=8.0)  # much worse, >3s later
    assert cold["colder"] is True
    assert "Colder" in cold["guidance"]


def test_dot_radius_is_one_minus_score():
    s = TriageSession()
    r = _best(s, 0.0)
    assert r["dot_radius"] == pytest.approx(1.0 - r["score"], abs=1e-9)


def test_snapshot_exposes_per_metric_normalised_values():
    s = TriageSession()
    r = s.feed(snr=12, rssi=-70, noise=-118, t=0.0)      # best-possible inputs
    m = r["metrics"]
    assert set(m) == {"snr", "margin", "noise"}
    assert all(0.0 <= v <= 1.0 for v in m.values())
    assert m["snr"] == pytest.approx(1.0, abs=1e-6)      # 12 dB = top of default range
    bad = s.feed(snr=-12.5, rssi=-118, noise=-95, t=1.0)
    assert bad["metrics"]["snr"] == pytest.approx(0.0, abs=1e-6)
    assert bad["metrics"]["noise"] < 0.2                  # noisy floor scores low
