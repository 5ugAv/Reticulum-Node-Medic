"""Dual-supply tripwire: a HAT-powered medic must scream when USB-C power
arrives (two supplies on one rail, 2026-08-25 ruling). Pure logic only —
the PMIC read is exercised via its parser."""

from monitor.dual_supply import (EXT5V_DANGER_V, GRACE_SECONDS,
                                 DualSupplyGuard, parse_ext5v)


def test_parses_live_pmic_output():
    # exact shape captured live on Medic 1 (2026-08-25)
    assert parse_ext5v(" EXT5V_V volt(24)=5.01830000V") == 5.0183


def test_parser_never_raises_on_garbage():
    for junk in ("", "error", "volt=", "EXT5V_V volt(24)=notanumber"):
        assert parse_ext5v(junk) is None


def _armed_guard():
    """A guard that has WITNESSED the baseline: one low read arms it —
    the redesigned contract (2026-08-30, the HAWKEYE backfeed false
    positive: hot-from-birth must never trip)."""
    g = DualSupplyGuard()
    assert g.evaluate(True, 0.05) is None      # the witnessed low = armed
    return g


def test_trips_only_after_two_hot_laps():
    g = _armed_guard()
    assert g.evaluate(True, 5.0) is None
    assert g.evaluate(True, 5.0) == "danger"


def test_hot_from_birth_never_trips_the_backfeed_case():
    # HAWKEYE 2026-08-30: HAT backfeeds the sense line, EXT5V reads ~5V
    # with an EMPTY socket from the first read — the old guard shut a
    # healthy medic down in a 15s loop. Never again.
    g = DualSupplyGuard()
    for _ in range(50):
        assert g.evaluate(True, 5.0) is None
    assert g.armed is False
    assert "never read low" in g.why_inert()


def test_arms_then_catches_a_real_arrival():
    g = DualSupplyGuard()
    g.evaluate(True, 0.02)                     # empty socket, sane hardware
    assert g.armed and g.why_inert() is None
    g.evaluate(True, 0.02)
    assert g.evaluate(True, 5.02) is None      # arrival, debounce lap 1
    assert g.evaluate(True, 5.02) == "danger"  # confirmed


def test_single_glitch_does_not_trip():
    g = _armed_guard()
    assert g.evaluate(True, 5.0) is None
    assert g.evaluate(True, 0.0) is None      # glitch over — counter resets
    assert g.evaluate(True, 5.0) is None      # must debounce again


def test_clear_fires_once_when_cable_pulled():
    g = _armed_guard()
    g.evaluate(True, 5.0); g.evaluate(True, 5.0)
    assert g.evaluate(True, 0.1) == "clear"
    assert g.evaluate(True, 0.1) is None


def test_inert_without_hat():
    g = DualSupplyGuard()
    for _ in range(5):
        assert g.evaluate(False, 5.2) is None


def test_unreadable_pmic_never_trips():
    g = DualSupplyGuard()
    for _ in range(5):
        assert g.evaluate(True, None) is None


def test_threshold_is_between_float_and_supply():
    # a floating input reads near 0; a real supply ~5 V
    assert 1.0 < EXT5V_DANGER_V < 4.5
    assert GRACE_SECONDS >= 10     # a human needs time to reach the cable
