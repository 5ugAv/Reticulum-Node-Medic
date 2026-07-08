import pytest

from node_profile import NodeProfile
from transport.connection import EmulatedConnection
from diagnostics.rtnode_2400 import RTNode2400Check

HEALTHY_STATUS = "\n".join([
    "Firmware: 5ugAv/RTNode-2400 v0.9",
    "Flash: 16MB",
    "PSRAM: enabled",
    "Heap free: 140000",
    "Heap min: 90000",
    "Watchdog: armed",
    "WiFi RSSI: -62 dBm",
    "Interfaces: backbone, local (unique)",
    "Last flash: ok",
    "Antenna: ok",
])


def healthy_conn(status=HEALTHY_STATUS, bootlog="Boot OK\nRNode interface up"):
    return (
        EmulatedConnection()
        .rule("rtnode --status", code=0, stdout=status)
        .rule("rtnode --bootlog", code=0, stdout=bootlog)
        .rule("rtnode --check-interfaces", code=0, stdout="OK")
    )


def run(conn, prof=None):
    return RTNode2400Check(conn, prof or NodeProfile()).run()


def names(issues):
    return {i.check_name for i in issues}


def sev(issues, name):
    return next(i.severity for i in issues if i.check_name == name)


def ins(conn, tup):
    conn.rules.insert(0, tup)
    return conn


def test_category_name():
    assert RTNode2400Check(healthy_conn(), NodeProfile()).category_name == (
        "RTNode-2400"
    )


def test_healthy_v4_no_issues():
    assert run(healthy_conn()) == []


def test_v3_psram_flagged_as_info_not_fault():
    status = HEALTHY_STATUS.replace("Flash: 16MB", "Flash: 8MB")
    issues = run(healthy_conn(status=status))
    assert "psram_v3_limited" in names(issues)
    assert sev(issues, "psram_v3_limited") == "info"


def test_board_variant_unknown_flagged():
    status = HEALTHY_STATUS.replace("Flash: 16MB", "Flash: ???")
    assert "board_variant" in names(run(healthy_conn(status=status)))


def test_heap_leak_detected():
    status = HEALTHY_STATUS.replace("Heap min: 90000", "Heap min: 5000")
    assert "heap_trend" in names(run(healthy_conn(status=status)))


def test_watchdog_not_armed():
    status = HEALTHY_STATUS.replace("Watchdog: armed", "Watchdog: disabled")
    assert "watchdog_armed" in names(run(healthy_conn(status=status)))


def test_boot_fatal_scan():
    issues = run(healthy_conn(bootlog="FATAL: interface name collision"))
    assert "boot_fatal" in names(issues)
    assert sev(issues, "boot_fatal") == "critical"


def test_wifi_rssi_warning():
    status = HEALTHY_STATUS.replace("WiFi RSSI: -62 dBm", "WiFi RSSI: -80 dBm")
    issues = run(healthy_conn(status=status))
    assert sev(issues, "wifi_rssi") == "warning"


def test_wifi_rssi_critical():
    status = HEALTHY_STATUS.replace("WiFi RSSI: -62 dBm", "WiFi RSSI: -90 dBm")
    issues = run(healthy_conn(status=status))
    assert sev(issues, "wifi_rssi") == "critical"


def test_fork_verification_upstream_flagged():
    status = HEALTHY_STATUS.replace(
        "Firmware: 5ugAv/RTNode-2400 v0.9", "Firmware: upstream/microReticulum")
    assert "fork_verification" in names(run(healthy_conn(status=status)))


def test_interface_name_collision():
    conn = ins(healthy_conn(),
               ("rtnode --check-interfaces", 0, "COLLISION: backbone==local", ""))
    assert "interface_collision" in names(run(conn))


def test_reflash_failure_detected():
    status = HEALTHY_STATUS.replace("Last flash: ok", "Last flash: failed")
    assert "reflash_failure" in names(run(healthy_conn(status=status)))


def test_first_boot_errors_benign_on_fork():
    # ERROR noise present but running the fork -> not flagged
    issues = run(healthy_conn(bootlog="ERROR harmless first-boot noise\nBoot OK"))
    assert "first_boot_errors" not in names(issues)


def test_first_boot_errors_flagged_on_upstream():
    status = HEALTHY_STATUS.replace(
        "Firmware: 5ugAv/RTNode-2400 v0.9", "Firmware: upstream/microReticulum")
    issues = run(healthy_conn(status=status,
                              bootlog="ERROR first-boot noise\nBoot OK"))
    assert "first_boot_errors" in names(issues)


def test_wifi_antenna_compressed():
    status = HEALTHY_STATUS.replace("Antenna: ok", "Antenna: compressed")
    assert "wifi_antenna_compressed" in names(run(healthy_conn(status=status)))


def test_reflash_fix_retries_lower_baud():
    status = HEALTHY_STATUS.replace("Last flash: ok", "Last flash: failed")
    conn = healthy_conn(status=status)
    conn.rule("--baud 115200", code=0, stdout="flash ok")
    check = RTNode2400Check(conn, NodeProfile())
    issue = next(i for i in check.run() if i.check_name == "reflash_failure")
    fix = check.fix(issue)
    assert fix.success is True
