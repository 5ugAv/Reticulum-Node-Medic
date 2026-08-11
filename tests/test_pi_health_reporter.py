import pytest

from monitor.pi_health_reporter import (
    PiHealthInputs,
    collect_pi_health,
    build_beacon_bytes,
    parse_proc_uptime,
    parse_meminfo_free_kb,
    disk_used_percent,
    DISK_FAULT_PCT,
    FREE_KB_CAP,
)
from monitor.health_beacon import (
    decode,
    beacon_status,
    BOARD_PI_PROPAGATION,
    FORMAT_VERSION_V2,
)


def healthy_solar(**over):
    kw = dict(
        uptime_s=86400, free_ram_kb=512000, disk_used_pct=41,
        net_up=True, radio_up=True, rns_transport_up=True,
        battery_mv=12400, battery_pct=88, on_battery=True, on_solar=True,
        charging=True, lora_snr_db=8, lora_rssi_dbm=-88,
    )
    kw.update(over)
    return PiHealthInputs(**kw)


def test_collect_produces_v2_pi_beacon():
    b = collect_pi_health(healthy_solar())
    assert b.format_version == FORMAT_VERSION_V2
    assert b.board_id == BOARD_PI_PROPAGATION
    assert b.board_label == "RPi propagation"
    assert b.has_power_telemetry and b.has_link_telemetry


def test_wire_roundtrips_through_the_medic_decoder():
    # the whole point: the medic decodes a Pi beacon with the same codec
    raw = build_beacon_bytes(healthy_solar())
    assert len(raw) == 20
    b = decode(raw)
    assert b.board_label == "RPi propagation"
    assert b.battery_pct == 88
    assert b.on_solar and b.charging
    assert b.lora_snr_db == 8 and b.lora_rssi_dbm == -88


def test_healthy_solar_node_is_ok():
    assert beacon_status(collect_pi_health(healthy_solar())) == "ok"


def test_radio_down_is_alert():
    # a propagation node whose RNode is down is useless -> red
    assert beacon_status(collect_pi_health(healthy_solar(radio_up=False))) == "alert"


def test_full_disk_is_fault_alert():
    b = collect_pi_health(healthy_solar(disk_used_pct=DISK_FAULT_PCT))
    assert b.fault is True
    assert beacon_status(b) == "alert"


def test_discharging_flat_battery_is_alert():
    b = collect_pi_health(healthy_solar(
        battery_pct=7, charging=False, on_solar=True, on_battery=True))
    assert beacon_status(b) == "alert"


def test_free_ram_is_capped_to_uint16():
    b = collect_pi_health(healthy_solar(free_ram_kb=4_000_000))
    assert b.free_heap_kb == FREE_KB_CAP


def test_no_ups_reports_no_battery():
    b = collect_pi_health(healthy_solar(
        battery_mv=None, battery_pct=None, on_battery=False, charging=False))
    assert b.has_power_telemetry is False
    assert b.battery_label == "not reported"


def test_healthy_node_does_not_spuriously_warn_on_watchdog():
    # wdt_armed is forced True for a Pi so the 'watchdog not armed' rule never
    # fires; a clean node stays green.
    b = collect_pi_health(healthy_solar())
    assert b.wdt_armed is True
    assert beacon_status(b) == "ok"


# ---- /proc + df parsers ---------------------------------------------------


def test_parse_proc_uptime():
    assert parse_proc_uptime("12345.67 98765.43") == 12345
    assert parse_proc_uptime("") == 0
    assert parse_proc_uptime("garbage") == 0


def test_parse_meminfo_prefers_available():
    text = "MemTotal:  8000000 kB\nMemFree:  1000000 kB\nMemAvailable:  6000000 kB\n"
    assert parse_meminfo_free_kb(text) == 6000000


def test_parse_meminfo_falls_back_to_free():
    text = "MemTotal:  8000000 kB\nMemFree:  1000000 kB\n"
    assert parse_meminfo_free_kb(text) == 1000000


def test_parse_meminfo_empty_is_zero():
    assert parse_meminfo_free_kb("") == 0


def test_disk_used_percent():
    assert disk_used_percent(41, 100) == 41
    assert disk_used_percent(0, 0) == 0
    assert disk_used_percent(200, 100) == 100


# --- every announce must carry a beacon, not just the deliberate ones -------

def test_the_reporter_attaches_a_beacon_to_automatic_announces_too():
    """SkyFinger, 2026-08-11. The medic KNEW the node's health identity — so
    announces were plainly arriving — and had never once stored a beacon. It sat
    in VITALS as LoRa-only for days, reading as a broken node. The node was
    fine.

    A destination announces two ways. The reporter's own loop attaches the
    beacon; but RNS ALSO re-announces a destination by itself whenever someone
    requests a path to it, and that automatic announce carries the destination's
    DEFAULT app_data — which was nothing. The medic probes paths constantly, so
    the announces it actually received were overwhelmingly the empty ones.

    set_default_app_data takes a callable, evaluated at announce time, so an
    automatic re-announce carries readings from that moment rather than a stale
    snapshot from boot. Verified against the installed RNS: Destination.announce
    reads default_app_data and calls it when callable.
    """
    from tests.srcutil import func_source
    src = func_source("monitor/pi_health_reporter.py", "serve")
    assert "set_default_app_data(current_beacon)" in src, \
        "automatic re-announces would carry no health at all"
    # the callable, NOT a snapshot: a beacon frozen at boot would report an
    # uptime of seconds forever, which is worse than silence because it looks live
    assert "set_default_app_data(current_beacon())" not in src


def test_rns_really_honours_a_callable_default_app_data():
    """The fix rests on a library behaviour, so check the library rather than
    the documentation — this project has been bitten before by a call that
    returned rc=0 and did nothing."""
    RNS = pytest.importorskip("RNS")
    import inspect
    src = inspect.getsource(RNS.Destination.announce)
    assert "default_app_data" in src
    assert "callable(" in src
