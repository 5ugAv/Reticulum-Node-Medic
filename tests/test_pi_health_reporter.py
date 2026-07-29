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
