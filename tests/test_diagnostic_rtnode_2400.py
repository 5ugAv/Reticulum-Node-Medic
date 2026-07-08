"""Type B (RTNode-2400) diagnostics — driven by the serial [HealthBeacon] line.

The RTNode-2400 has NO text console (confirmed by the firmware side). On serial
it emits a KISS binary stream plus passive human-readable log output, including
a beacon line:

    [HealthBeacon] announce dst=<32hex> data=<28hex 14-byte payload>

so these diagnostics regex that line, decode the payload with the existing
health_beacon codec, and derive checks from it — reusing the locked wire
contract instead of a bespoke parser. The boot log (also passive) is scanned
for FATAL.
"""

import pytest

from node_profile import NodeProfile
from transport.connection import EmulatedConnection
from monitor.health_beacon import encode
from diagnostics.rtnode_2400 import RTNode2400Check, CAPTURE_COMMAND

REAL_HW_LINE = (
    "[HealthBeacon] announce dst=eabdd142596bcae888242ec1b172d566 "
    "data=010000002400c7cc053b3f000602")


def make_data(**over):
    kw = dict(
        uptime_s=36, heap_kb=199, wifi_rssi_dbm=-52, reset_reason=0,
        wifi_up=True, lora_up=True, tcp_backbone_up=True,
        local_tcp_server_up=True, wdt_armed=True, psram=True, fault=False,
        board_id=0x3F, fw=(0, 6, 2),
    )
    kw.update(over)
    return encode(**kw).hex()


def beacon_line(**over):
    return (f"[HealthBeacon] announce dst=eabdd142596bcae888242ec1b172d566 "
            f"data={make_data(**over)}")


def serial_log(beacon=True, fatal=False, **over):
    lines = ["Booting RTNode-2400 (5ugAv)", "RNS transport up", "mem_free: 210000"]
    if fatal:
        lines.append("FATAL: interface name collision backbone==local")
    if beacon:
        lines.append(beacon_line(**over))
    return "\n".join(lines)


def conn(log=None, **over):
    return EmulatedConnection().rule(
        CAPTURE_COMMAND, code=0, stdout=log if log is not None else serial_log(**over))


def run(c, prof=None):
    return RTNode2400Check(c, prof or NodeProfile()).run()


def names(issues):
    return {i.check_name for i in issues}


def sev(issues, name):
    return next(i.severity for i in issues if i.check_name == name)


def test_category_name():
    assert RTNode2400Check(conn(), NodeProfile()).category_name == "RTNode-2400"


def test_healthy_beacon_no_issues():
    assert run(conn()) == []


def test_no_beacon_line_flags_beacon_received():
    issues = run(conn(beacon=False))
    assert "beacon_received" in names(issues)
    assert sev(issues, "beacon_received") == "critical"


def test_no_beacon_skips_beacon_derived_checks():
    # with no beacon, only log-based checks run (beacon_received, boot_fatal)
    assert names(run(conn(beacon=False))) == {"beacon_received"}


def test_boot_fatal_from_passive_log():
    issues = run(conn(fatal=True))
    assert "boot_fatal" in names(issues)
    assert sev(issues, "boot_fatal") == "critical"


def test_boot_fatal_catches_real_watchdog_reboot_line():
    # the real heap-floor reboot line from the firmware (not the word "FATAL")
    log = ("Booting RTNode-2400\n"
           "[WATCHDOG] CRITICAL: Free heap 31000 < 40000 — REBOOTING\n"
           + beacon_line())
    assert "boot_fatal" in names(run(conn(log=log)))


def test_healthy_watchdog_line_not_flagged():
    # the periodic (non-critical) watchdog line must NOT trip boot_fatal
    log = ("Booting RTNode-2400\n"
           "[WATCHDOG] WiFi.status()=3 heap=180000 min_heap=140000\n"
           + beacon_line())
    assert "boot_fatal" not in names(run(conn(log=log)))


def test_lora_down_is_critical():
    issues = run(conn(lora_up=False))
    assert sev(issues, "lora_link") == "critical"


def test_fault_bit_is_critical():
    issues = run(conn(fault=True))
    assert sev(issues, "heap_fault") == "critical"


def test_heap_low_is_warning():
    issues = run(conn(heap_kb=20))
    assert sev(issues, "heap_low") == "warning"


def test_watchdog_not_armed_is_warning():
    assert "watchdog_armed" in names(run(conn(wdt_armed=False)))


def test_wifi_down_is_warning():
    assert "wifi_link" in names(run(conn(wifi_up=False)))


def test_wifi_rssi_warning_and_critical():
    assert sev(run(conn(wifi_rssi_dbm=-80)), "wifi_rssi") == "warning"
    assert sev(run(conn(wifi_rssi_dbm=-90)), "wifi_rssi") == "critical"


def test_wifi_rssi_not_checked_when_wifi_down():
    # a weak/zero RSSI while WiFi is down must not raise a signal issue
    assert "wifi_rssi" not in names(run(conn(wifi_up=False, wifi_rssi_dbm=0)))


def test_tcp_backbone_down_is_info():
    issues = run(conn(tcp_backbone_up=False))
    assert sev(issues, "tcp_backbone") == "info"


def test_abnormal_reset_panic_is_warning():
    # reset_reason 1 = panic
    assert "abnormal_reset" in names(run(conn(reset_reason=1)))


def test_other_reset_is_not_flagged():
    # reset_reason 5 = other (as in the real capture) is benign
    assert "abnormal_reset" not in names(run(conn(reset_reason=5)))


def test_v3_board_psram_note_is_info():
    issues = run(conn(board_id=0x3A))  # Heltec32 V3
    assert sev(issues, "psram_v3_note") == "info"


def test_unknown_board_flagged_info():
    issues = run(conn(board_id=0x99))
    assert sev(issues, "board_identified") == "info"


def test_real_hardware_line_parses():
    # the genuine capture: everything up except tcp_backbone (=> one info)
    issues = run(conn(log=serial_log() and REAL_HW_LINE))
    n = names(issues)
    # real vector has tcp_backbone False -> exactly the info issue, nothing worse
    assert n == {"tcp_backbone"}
    assert sev(issues, "tcp_backbone") == "info"


def test_garbled_beacon_hex_is_treated_as_no_beacon():
    bad = "[HealthBeacon] announce dst=abcd data=zznothex"
    assert "beacon_received" in names(run(conn(log=bad)))
