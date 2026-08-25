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


def test_trips_only_after_two_hot_laps():
    g = DualSupplyGuard()
    assert g.evaluate(True, 5.0) is None
    assert g.evaluate(True, 5.0) == "danger"


def test_single_glitch_does_not_trip():
    g = DualSupplyGuard()
    assert g.evaluate(True, 5.0) is None
    assert g.evaluate(True, 0.0) is None      # glitch over — counter resets
    assert g.evaluate(True, 5.0) is None      # must debounce again


def test_clear_fires_once_when_cable_pulled():
    g = DualSupplyGuard()
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
