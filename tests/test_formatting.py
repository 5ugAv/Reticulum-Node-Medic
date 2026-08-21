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


# --- the SEEN line, and the muted echo tag ---------------------------------
# The display end of "a replayed announce is not a sighting": on 2026-08-21 a
# powered-off, battery-less board's row stayed green for hours because rnsd
# kept replaying its cached announce. seen_line/echo_annotation are the pure
# helpers the StatBar and node detail render from — weaker evidence must LOOK
# weaker, and must vanish once the node itself speaks.

def test_seen_line_appends_a_fresher_echo():
    from monitor.formatting import seen_line
    row = {"last_seen_hours": 3.1, "last_echo_hours": 0.2}
    assert seen_line(row) == "SEEN 3.1h · echo 0.2h"


def test_seen_line_without_echo_is_just_seen():
    from monitor.formatting import seen_line
    assert seen_line({"last_seen_hours": 3.1}) == "SEEN 3.1h"
    assert seen_line({"last_seen_hours": 3.1,
                      "last_echo_hours": None}) == "SEEN 3.1h"


def test_echo_staler_than_the_sighting_never_shows():
    """Once the node itself has spoken, the old replay is noise — and an echo
    exactly as old as the sighting is not 'fresher' either."""
    from monitor.formatting import echo_annotation, seen_line
    assert echo_annotation(0.2, 3.1) is None
    assert echo_annotation(1.0, 1.0) is None
    assert seen_line({"last_seen_hours": 0.2,
                      "last_echo_hours": 3.1}) == "SEEN 0.2h"


def test_echo_annotation_is_none_safe():
    from monitor.formatting import echo_annotation
    assert echo_annotation(None, 0.2) is None
    assert echo_annotation(3.1, None) is None
    assert echo_annotation(None, None) is None


def test_echo_tag_speaks_days_like_seen_does():
    """The echo age goes through format_age too — the units lesson of
    2026-08-10 applies to every figure on the strip."""
    from monitor.formatting import echo_annotation
    assert echo_annotation(300.0, 26.0) == "echo 1d 2h"
