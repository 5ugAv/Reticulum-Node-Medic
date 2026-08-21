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
# kept replaying its cached announce. seen_and_echo is THE one composer both
# the StatBar strip and node detail render from — weaker evidence must LOOK
# weaker, and must vanish once the node itself speaks.

def test_seen_and_echo_appends_a_fresher_echo():
    from monitor.formatting import seen_and_echo
    seen, tag = seen_and_echo({"last_seen_hours": 3.1,
                               "last_direct_hours": 3.1,
                               "last_echo_hours": 0.2})
    assert seen == "SEEN 3.1h"
    assert tag == "echo 12m"


def test_seen_without_echo_has_no_tag():
    from monitor.formatting import seen_and_echo
    assert seen_and_echo({"last_seen_hours": 3.1}) == ("SEEN 3.1h", None)
    assert seen_and_echo({"last_seen_hours": 3.1,
                          "last_echo_hours": None})[1] is None


def test_echo_gates_on_the_last_direct_word_not_last_seen():
    """The gate is the node's last DIRECT word: ingest_mesh can bump last_seen
    from a path row's learned-time — a route, weaker evidence — and gating on
    that hid the tag exactly when it mattered."""
    from monitor.formatting import seen_and_echo
    # a direct word fresher than the echo silences it
    assert seen_and_echo({"last_seen_hours": 5.0, "last_direct_hours": 0.1,
                          "last_echo_hours": 1.0})[1] is None
    # exactly as fresh is not FRESHER — still silenced
    assert seen_and_echo({"last_seen_hours": 5.0, "last_direct_hours": 1.0,
                          "last_echo_hours": 1.0})[1] is None
    # mesh-bumped last_seen looks fresh, but the last direct word is old:
    # the echo IS the freshest direct-ish evidence, so it shows
    seen, tag = seen_and_echo({"last_seen_hours": 0.1,
                               "last_direct_hours": 5.0,
                               "last_echo_hours": 1.0})
    assert seen == "SEEN 0.1h"
    assert tag == "echo 1.0h"
    # no direct word on record at all -> nothing outranks the echo
    assert seen_and_echo({"last_seen_hours": 3.0,
                          "last_echo_hours": 0.2})[1] == "echo 12m"


def test_ninety_second_echo_reads_in_minutes():
    """The canonical replay of 2026-08-21 arrived 90 s after the unplug;
    through format_age that is "echo 0.0h" — a tag that says nothing. Below
    the hour the echo speaks minutes, floored at 1m (an echo on record is
    never "0m ago"). format_age itself keeps the operator's coarser scale."""
    from monitor.formatting import format_age_fine
    assert format_age_fine(90 / 3600) == "2m"
    assert format_age_fine(20 / 3600) == "1m"     # floor, never "0m"
    assert format_age_fine(0.0) == "1m"
    assert format_age_fine(0.5) == "30m"
    assert format_age_fine(1.0) == "1.0h"         # back on format_age's scale
    assert format_age_fine(26.0) == "1d 2h"
    assert format_age_fine(None) == "?"
    assert format_age_fine(-0.2) == "1m"          # clamped, like format_age


def test_sub_resolution_echo_is_dropped():
    """A rendered echo age identical to the rendered SEEN age is a difference
    below display resolution — "SEEN 3.1h · echo 3.1h" claims nothing."""
    from monitor.formatting import seen_and_echo
    assert seen_and_echo({"last_seen_hours": 3.14, "last_direct_hours": 10.0,
                          "last_echo_hours": 3.11})[1] is None


def test_composer_is_none_safe():
    from monitor.formatting import seen_and_echo
    assert seen_and_echo({}) == ("SEEN ?", None)
