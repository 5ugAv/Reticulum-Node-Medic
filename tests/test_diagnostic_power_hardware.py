import pytest

from node_profile import NodeProfile
from transport.connection import EmulatedConnection
from diagnostics.power_hardware import PowerHardwareCheck


def healthy_conn():
    return (
        EmulatedConnection()
        .rule("thermal_zone0/temp", code=0, stdout="45000")
        .rule("fan1_input", code=0, stdout="3200")
        .rule("BAT0/capacity", code=0, stdout="95")
        .rule("dmesg", code=1, stdout="")  # grep no-match -> healthy
        .rule(".rtt_wtest", code=0, stdout="")
        .rule("MemAvailable", code=0, stdout="MemAvailable:   512000 kB")
        .rule("/proc/uptime", code=0, stdout="123456.7 65432.1")
    )


def profile(**flags):
    p = NodeProfile()
    for k, v in flags.items():
        setattr(p, k, v)
    return p


def run(conn, prof=None):
    return PowerHardwareCheck(conn, prof or NodeProfile()).run()


def names(issues):
    return {i.check_name for i in issues}


def sev(issues, name):
    return next(i.severity for i in issues if i.check_name == name)


def test_category_name():
    assert PowerHardwareCheck(healthy_conn(), NodeProfile()).category_name == (
        "Power & hardware"
    )


def test_all_healthy_no_issues():
    prof = profile(has_cooling_fan=True, has_battery_bank=True)
    assert run(healthy_conn(), prof) == []


def test_cpu_temp_warning_over_70():
    conn = healthy_conn()
    conn.rules.insert(0, ("thermal_zone0/temp", 0, "75000", ""))
    issues = run(conn)
    assert "cpu_temperature" in names(issues)
    assert sev(issues, "cpu_temperature") == "warning"


def test_cpu_temp_critical_over_80():
    conn = healthy_conn()
    conn.rules.insert(0, ("thermal_zone0/temp", 0, "85000", ""))
    issues = run(conn)
    assert sev(issues, "cpu_temperature") == "critical"


def test_cpu_temp_ok_below_70():
    assert "cpu_temperature" not in names(run(healthy_conn()))


def test_fan_checked_only_when_present():
    # fan stopped, but no fan hardware on profile -> not checked
    conn = healthy_conn()
    conn.rules.insert(0, ("fan1_input", 0, "0", ""))
    assert "cooling_fan" not in names(run(conn, profile()))
    # with fan hardware, a stopped fan is an issue
    assert "cooling_fan" in names(run(conn, profile(has_cooling_fan=True)))


def test_battery_warning_at_20_or_below():
    conn = healthy_conn()
    conn.rules.insert(0, ("BAT0/capacity", 0, "18", ""))
    issues = run(conn, profile(has_battery_bank=True))
    assert sev(issues, "battery_level") == "warning"


def test_battery_critical_at_10_or_below():
    conn = healthy_conn()
    conn.rules.insert(0, ("BAT0/capacity", 0, "8", ""))
    issues = run(conn, profile(has_battery_bank=True))
    assert sev(issues, "battery_level") == "critical"


def test_battery_not_checked_without_bank():
    conn = healthy_conn()
    conn.rules.insert(0, ("BAT0/capacity", 0, "5", ""))
    assert "battery_level" not in names(run(conn, profile()))


def test_sd_card_errors_detected():
    conn = healthy_conn()
    conn.rules.insert(0, ("dmesg", 0, "mmc0: error -110 whilst initialising", ""))
    assert "sd_card_health" in names(run(conn))


def test_filesystem_readonly_detected():
    conn = healthy_conn()
    conn.rules.insert(0, (".rtt_wtest", 1, "Read-only file system", ""))
    assert "filesystem_integrity" in names(run(conn))


def test_low_memory_detected():
    conn = healthy_conn()
    conn.rules.insert(0, ("MemAvailable", 0, "MemAvailable:   40000 kB", ""))
    assert "available_memory" in names(run(conn))


def test_recent_uptime_is_info():
    conn = healthy_conn()
    conn.rules.insert(0, ("/proc/uptime", 0, "120.5 60.0", ""))
    issues = run(conn)
    assert sev(issues, "uptime") == "info"
