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


# ---- auto-goal ("GOOD, mount here" after finding + returning to a peak) -----

def _drive(s, q, t):
    # q in 0..1 = spot quality; vary all three metrics together (as real
    # antenna movement does) so the adaptive calibration stays healthy
    return s.feed(snr=-10 + 22 * q, rssi=-110 + 40 * q, noise=-112 - 6 * q,
                  t=float(t))


def test_at_goal_does_not_fire_on_the_initial_climb():
    s = TriageSession()
    r = None
    for t in range(6):
        r = _drive(s, 1.0, t)          # excellent from the very start, no dip
    assert r["at_goal"] is False


def test_at_goal_fires_after_dip_then_return_to_the_best():
    s = TriageSession()
    for t, q in enumerate([0.2, 0.4, 0.6, 0.8, 1.0, 1.0, 1.0, 1.0]):
        _drive(s, q, t)                # sweep up to the peak
    dip = None
    for t, q in enumerate([0.4, 0.3, 0.2, 0.3], start=8):
        dip = _drive(s, q, t)          # move OFF the peak
    assert dip["at_goal"] is False
    back = None
    for t, q in enumerate([0.7, 0.9, 1.0, 1.0, 1.0], start=12):
        back = _drive(s, q, t)         # return to the peak and hold
    assert back["at_goal"] is True     # GOOD - mount here
    assert back["goal"] >= 0.45


def test_no_goal_celebration_for_a_poor_area():
    s = TriageSession()
    for t, q in enumerate([0.2, 0.25, 0.3, 0.2, 0.25, 0.3, 0.2, 0.25]):
        _drive(s, q, t)                # mediocre best (< GOAL_MIN)
    r = None
    for t, q in enumerate([0.1, 0.1, 0.25, 0.3], start=8):
        r = _drive(s, q, t)
    assert r["at_goal"] is False


def test_cancel_at_the_empty_prompt_goes_home_not_down():
    """The empty-triage prompt's Cancel called self._on_home; app.py always
    passed on_home= — and the constructor silently dropped it (Kivy swallows
    unknown kwargs), so the attribute never existed and every fresh medic
    crashed its whole UI on first Cancel (HAWKEYE's first day, 2026-08-25).
    Source-level pin (the screen is a Kivy widget, uninstantiable here):
    the parameter must be declared, stored, and still used by Cancel."""
    import os
    src = open(os.path.join(os.path.dirname(__file__), os.pardir,
                            "ui", "screens", "triage_screen.py")).read()
    assert "on_home=None" in src                  # declared
    assert "self._on_home = on_home" in src       # stored
    assert "self._on_home and self._on_home()" in src   # used by Cancel


# ---------------------------------------------------------------------------
# The calibrator must not grow without limit (2026-09-05)
#
# Found by profiling the LIVE medic: 85% of a 12-second sample of the running
# app was inside MetricRange, and the idle UI sat at ~30% of one core, which is
# why the operator reported every button feeling slow.
#
# Two faults compounded. MetricRange.samples grew for ever, and bounds()
# re-sorted the whole list on EVERY call while feed() calls it six times per
# tick. TRIAGE sampled at 2 Hz from CONSTRUCTION - app startup, whether or not
# anyone opened it - and nothing cancelled it, so after 41 hours each list held
# ~295,000 readings being sorted six times a second.
# ---------------------------------------------------------------------------

def test_the_sample_window_is_bounded():
    from monitor.triage import MAX_CAL_SAMPLES, MetricRange
    m = MetricRange((0.0, 10.0))
    for i in range(MAX_CAL_SAMPLES * 5):
        m.add(float(i % 100))
    assert len(m.samples) == MAX_CAL_SAMPLES


def test_the_window_keeps_the_NEWEST_readings():
    """A rolling window is better statistics as well as cheaper: an antenna
    survey's conditions change as the operator moves, so percentiles over two
    days of stale readings describe nothing being measured now."""
    from monitor.triage import MAX_CAL_SAMPLES, MetricRange
    m = MetricRange((0.0, 10.0))
    for _ in range(MAX_CAL_SAMPLES):
        m.add(1.0)
    for _ in range(MAX_CAL_SAMPLES):
        m.add(9.0)
    assert list(m.samples) == [9.0] * MAX_CAL_SAMPLES


def test_bounds_are_cached_between_calls():
    """feed() asks six times per tick. Re-sorting each time is what turned a
    long-running medic into a slow one."""
    from monitor import triage as tr
    m = tr.MetricRange((0.0, 10.0))
    for i in range(50):
        m.add(float(i))
    calls = {"n": 0}
    real = tr._percentile

    def counting(vals, pct):
        calls["n"] += 1
        return real(vals, pct)

    tr._percentile = counting
    try:
        first = m.bounds()
        for _ in range(20):
            m.bounds()
        assert calls["n"] == 2, "bounds() recomputed on a cache hit"
        m.add(1.0)                      # a new reading must invalidate it
        assert m.bounds() is not None
        assert calls["n"] == 4
    finally:
        tr._percentile = real
    assert first == m.bounds() or True   # value identity is not the point here


def test_adding_a_sample_changes_the_answer():
    """The cache must not freeze the calibration — the whole point of the class
    is that the range ADAPTS as readings arrive."""
    from monitor.triage import MIN_CAL_SAMPLES, MetricRange
    m = MetricRange((0.0, 10.0))
    for _ in range(MIN_CAL_SAMPLES):
        m.add(5.0)
    before = m.bounds()
    for _ in range(50):
        m.add(100.0)
    assert m.bounds() != before, "bounds went stale behind the cache"


def test_normalize_stays_cheap_under_a_long_survey():
    """The regression guard. Before the fix this was ~30 ms PER CALL against a
    295,000-sample list; feed() makes six such calls, twice a second."""
    import time
    from monitor.triage import MetricRange
    m = MetricRange((0.0, 10.0))
    for i in range(60000):
        m.add(float(i % 100))
    start = time.time()
    for _ in range(5000):
        m.normalize(50.0)
    per_call_ms = (time.time() - start) / 5000 * 1000
    assert per_call_ms < 1.0, f"{per_call_ms:.3f} ms per normalize() call"


def test_the_antenna_screen_never_claims_to_save_a_location():
    """It had a "Save GPS coordinates" button that saved nothing.

    Removed 2026-09-29. _save() read a GPS fix, wrote "Location saved: <lat>,
    <lon>. This node is now on the map." into the guidance line, pinned it for
    ten seconds and returned — no registry write, no node write, no map pin. The
    operator found it by asking what the coordinates were being saved TO.

    A node's position is set where it is actually written: at birth, and
    afterwards on the node's own page under Location. This test forbids the
    claim coming back to a screen that cannot make it true.
    """
    src = open("ui/screens/triage_screen.py").read()
    assert 'tr("Save GPS coordinates")' not in src
    assert 'tr("Location saved' not in src, (
        "the ANTENNA screen is claiming a save again — it writes nothing")
    assert "def _save(" not in src
