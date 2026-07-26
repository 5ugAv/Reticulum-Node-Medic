"""UPS Module 3S monitor — INA219 math, SoC curve, read path, and the
voltage-gated safe-shutdown guard (all without hardware)."""

from monitor.ups import (
    bus_voltage, current_ma, pack_percent, interpret, read_ups,
    UpsState, BatteryGuard, _REG_BUSVOLT, _REG_CURRENT)


# -- register + SoC math -----------------------------------------------------

def test_bus_voltage_decodes_4mv_steps():
    # 11.1 V -> (11100/4) << 3 = 22200
    assert abs(bus_voltage(22200) - 11.1) < 0.01


def test_current_is_signed():
    assert current_ma(1000) == 100.0                 # +100 mA (charging-ish)
    assert current_ma(65536 - 2000) == -200.0        # two's-complement negative


def test_pack_percent_endpoints_and_mid():
    assert pack_percent(3 * 3.00) == 0               # empty (3.0 V/cell)
    assert pack_percent(3 * 4.20) == 100             # full (4.2 V/cell)
    assert pack_percent(0) == 0
    mid = pack_percent(3 * 3.70)                      # ~3.7 V/cell
    assert 40 <= mid <= 55
    assert pack_percent(3 * 5.0) == 100              # clamps above the curve


def test_interpret_builds_state():
    st = interpret(22200, 1000)                      # 11.1 V, +100 mA
    assert st.present and abs(st.voltage - 11.1) < 0.01
    assert st.charging and not st.discharging


# -- read path ---------------------------------------------------------------

def test_read_ups_with_fake_reader():
    raw = {_REG_BUSVOLT: 22200, _REG_CURRENT: 65536 - 2000}   # 11.1 V, -200 mA
    st = read_ups(reader=lambda reg: raw[reg])
    assert st.present and st.percent > 0 and st.discharging


def test_read_ups_no_hardware_is_safe_noop():
    # _open_reader returns None with no smbus/bus -> present False, no exception
    st = read_ups(reader=None)              # CI has no I2C bus 1
    assert st.present is False and st.percent == 0


def test_reader_exception_is_swallowed():
    def boom(reg):
        raise IOError("bus error")
    assert read_ups(reader=boom).present is False


# -- the safe-shutdown guard -------------------------------------------------

def _st(v, charging=False):
    return UpsState(present=True, voltage=v, percent=pack_percent(v),
                    charging=charging)


def test_guard_ok_when_healthy():
    g = BatteryGuard()
    assert g.evaluate(_st(12.0)) == "ok"


def test_guard_warns_once_then_quiet():
    g = BatteryGuard()
    assert g.evaluate(_st(10.4)) == "warn"           # first cross of warn line
    assert g.evaluate(_st(10.3)) == "ok"             # doesn't nag every poll


def test_guard_shuts_down_only_after_confirmation():
    g = BatteryGuard(confirm=3)
    assert g.evaluate(_st(9.0)) == "ok"              # 1
    assert g.evaluate(_st(9.0)) == "ok"              # 2
    assert g.evaluate(_st(9.0)) == "shutdown"        # 3 -> act


def test_guard_never_shuts_down_while_charging():
    g = BatteryGuard(confirm=1)
    for _ in range(5):
        assert g.evaluate(_st(9.0, charging=True)) == "ok"


def test_guard_transient_sag_does_not_trigger():
    g = BatteryGuard(confirm=3)
    g.evaluate(_st(9.0)); g.evaluate(_st(9.0))       # 2 low reads (a flash burst)
    assert g.evaluate(_st(11.4)) == "ok"             # recovered -> counter resets
    assert g.evaluate(_st(9.0)) == "ok"              # needs 3 fresh lows again


def test_guard_ignores_absent_ups():
    g = BatteryGuard(confirm=1)
    assert g.evaluate(UpsState(present=False)) == "ok"
