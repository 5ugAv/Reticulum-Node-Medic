from monitor.health_beacon import encode, decode
from monitor.registry import NodeRegistry
from monitor.formatting import beacon_lines

HASH = "11223344556677889900aabbccddeeff"
NOW = 1_000_000.0


def _reg_with_beacon(**over):
    kw = dict(uptime_s=36, heap_kb=140, wifi_rssi_dbm=-62, reset_reason=0,
              wifi_up=True, lora_up=True, tcp_backbone_up=True,
              local_tcp_server_up=True, wdt_armed=True, psram=True, fault=False,
              board_id=0x3F, fw=(0, 6, 2))
    kw.update(over)
    r = NodeRegistry()
    r.ingest(HASH, decode(encode(**kw)), NOW)
    return r.get(HASH)


def test_beacon_lines_no_beacon():
    r = NodeRegistry()
    rec = r.register(HASH, name="TRUTH")
    lines = beacon_lines(rec)
    assert lines == ["No health beacon received yet."]


def test_beacon_lines_report_key_fields():
    lines = beacon_lines(_reg_with_beacon())
    joined = "\n".join(lines)
    assert "Firmware: 0.6.2" in joined
    assert "Heltec32 V4" in joined
    assert "WiFi: up (-62 dBm)" in joined
    assert "LoRa: up" in joined
    assert "Watchdog: armed" in joined
    assert "Fault: no" in joined


def test_beacon_lines_wifi_down_hides_rssi():
    lines = beacon_lines(_reg_with_beacon(wifi_up=False, wifi_rssi_dbm=0))
    joined = "\n".join(lines)
    assert "WiFi: down" in joined
    assert "dBm" not in joined.split("LoRa")[0]  # no rssi shown for down wifi


def test_beacon_lines_flag_fault_and_unarmed_watchdog():
    lines = beacon_lines(_reg_with_beacon(fault=True, wdt_armed=False))
    joined = "\n".join(lines)
    assert "Fault: YES" in joined
    assert "NOT armed" in joined


# --- how long ago, in units a person can act on ----------------------------

def test_over_a_day_reads_in_days():
    """Operator, 2026-08-10, on the VITALS list: "instead of saying SEEN 210
    hours ago or 268 hours ago — that's hard to work out how many days that
    is". 268h is ELEVEN DAYS down, and nobody reads that off the number without
    stopping to divide. The solar grace period that decides whether a quiet node
    is a fault is counted in days, so the display should be too."""
    from monitor.formatting import format_age
    assert format_age(268.0) == "11d 4h"
    assert format_age(210.0) == "8d 18h"


def test_under_a_day_keeps_hours():
    """"3.2h" is already the right size of thought — no change wanted there."""
    from monitor.formatting import format_age
    assert format_age(0.1) == "0.1h"
    assert format_age(23.9) == "23.9h"


def test_a_whole_number_of_days_says_so_plainly():
    from monitor.formatting import format_age
    assert format_age(24.0) == "1d"
    assert format_age(72.0) == "3d"
    # and rounding must never produce "1d 24h"
    assert format_age(47.6) == "2d"


def test_nonsense_and_negatives_do_not_crash_the_row():
    """This runs in the VITALS list for every node; a bad value must degrade,
    not take the screen down."""
    from monitor.formatting import format_age
    assert format_age(None) == "?"
    assert format_age("x") == "?"
    assert format_age(-5) == "0.0h"
