import pytest

from node_profile import NodeProfile
from transport.connection import EmulatedConnection
from diagnostics.system_health import SystemHealthCheck


def healthy_conn():
    return (
        EmulatedConnection()
        .rule("--output=pcent", code=0, stdout="Use%\n 45%")
        .rule("chronyc tracking", code=0,
              stdout="System time : 0.000034 seconds fast of NTP time")
        .rule("NTPSynchronized", code=0, stdout="yes")
        .rule("/etc/logrotate.conf", code=0, stdout="")
        .rule("^systemctl is-active log2ram", code=0, stdout="active")
        .rule("ps -eo stat", code=0, stdout="0")
        .rule("/dev/watchdog", code=0, stdout="")
        # extended checks 61-62, 74-77, 79-80
        .rule("which rnsd", code=0, stdout="/usr/local/bin/rnsd")
        .rule("systemctl cat rnsd", code=0,
              stdout="ExecStart=/usr/local/bin/rnsd")
        .rule("dmesg", code=0, stdout="[    0.000000] Booting Linux 6.1")
        .rule("get_throttled", code=0, stdout="throttled=0x0")
        .rule("swapon --show", code=0, stdout="")
        .rule("Timezone", code=0, stdout="Australia/Melbourne")
        .rule("manfid", code=0, stdout="0x000003")
        .rule("python3 --version", code=0, stdout="Python 3.14.6")
        .rule("pip3 --version", code=0, stdout="pip 23.0.1 from /x")
    )


def run(conn):
    return SystemHealthCheck(conn, NodeProfile()).run()


def names(issues):
    return {i.check_name for i in issues}


def sev(issues, name):
    return next(i.severity for i in issues if i.check_name == name)


def ins(conn, tup):
    conn.rules.insert(0, tup)
    return conn


def test_category_name():
    assert SystemHealthCheck(healthy_conn(), NodeProfile()).category_name == (
        "System health"
    )


def test_all_healthy_no_issues():
    assert run(healthy_conn()) == []


def test_disk_warning_over_80():
    conn = ins(healthy_conn(), ("--output=pcent", 0, "Use%\n 85%", ""))
    issues = run(conn)
    assert sev(issues, "disk_space") == "warning"


def test_disk_critical_over_90():
    conn = ins(healthy_conn(), ("--output=pcent", 0, "Use%\n 95%", ""))
    assert sev(run(conn), "disk_space") == "critical"


def test_clock_drift_detected():
    conn = ins(healthy_conn(), ("chronyc tracking", 0,
               "System time : 412.5 seconds slow of NTP time", ""))
    assert "clock_drift" in names(run(conn))


def test_ntp_not_synced():
    conn = ins(healthy_conn(), ("NTPSynchronized", 0, "no", ""))
    assert "ntp_sync" in names(run(conn))


def test_log_rotation_missing():
    conn = ins(healthy_conn(), ("/etc/logrotate.conf", 1, "", ""))
    assert "log_rotation" in names(run(conn))


def test_log2ram_inactive():
    conn = ins(healthy_conn(),
               ("^systemctl is-active log2ram", 3, "inactive", ""))
    assert "log2ram_active" in names(run(conn))


def test_zombie_processes_detected():
    conn = ins(healthy_conn(), ("ps -eo stat", 0, "9", ""))
    assert "zombie_processes" in names(run(conn))


def test_watchdog_missing():
    conn = ins(healthy_conn(), ("/dev/watchdog", 1, "", ""))
    assert "hardware_watchdog" in names(run(conn))


# ---- extended checks 61-62, 74-77, 79-80 ---------------------------------


def test_rnsd_unit_path_mismatch():
    conn = ins(healthy_conn(),
               ("systemctl cat rnsd", 0, "ExecStart=/opt/weird/rnsd", ""))
    assert "rnsd_unit_path" in names(run(conn))


def test_ext4_journal_corruption():
    conn = ins(healthy_conn(),
               ("dmesg", 0, "EXT4-fs error (device mmcblk0p2): journal aborted", ""))
    issues = run(conn)
    assert "ext4_journal_corruption" in names(issues)
    assert sev(issues, "ext4_journal_corruption") == "critical"


def test_ext4_unverified_when_dmesg_denied():
    conn = ins(healthy_conn(),
               ("dmesg", 1, "", "Operation not permitted"))
    issues = run(conn)
    assert sev(issues, "ext4_journal_corruption") == "info"


def test_undervoltage_currently_throttled_is_critical():
    conn = ins(healthy_conn(), ("get_throttled", 0, "throttled=0x50005", ""))
    issues = run(conn)
    assert sev(issues, "undervoltage") == "critical"


def test_undervoltage_historical_is_warning():
    conn = ins(healthy_conn(), ("get_throttled", 0, "throttled=0x50000", ""))
    issues = run(conn)
    assert sev(issues, "undervoltage") == "warning"


def test_no_undervoltage_when_zero():
    assert "undervoltage" not in names(run(healthy_conn()))


def test_swap_on_sd_detected():
    conn = ins(healthy_conn(),
               ("swapon --show", 0, "NAME TYPE SIZE USED\n/var/swap file 100M 0", ""))
    assert "swap_on_sd" in names(run(conn))


def test_timezone_utc_default_flagged():
    conn = ins(healthy_conn(), ("Timezone", 0, "Etc/UTC", ""))
    assert "timezone_set" in names(run(conn))


def test_sd_card_suspicious_manfid():
    conn = ins(healthy_conn(), ("manfid", 0, "0x0000ad", ""))
    assert "sd_card_suspicious" in names(run(conn))


def test_python_too_old():
    conn = ins(healthy_conn(), ("python3 --version", 0, "Python 3.7.3", ""))
    assert "python_version" in names(run(conn))


def test_pip_too_old():
    conn = ins(healthy_conn(), ("pip3 --version", 0, "pip 18.1 from /x", ""))
    assert "pip_version" in names(run(conn))


def test_fix_swap_disables_swap():
    conn = ins(healthy_conn(),
               ("swapon --show", 0, "/var/swap file 100M 0", ""))
    conn.rule("swapoff", code=0, stdout="")
    check = SystemHealthCheck(conn, NodeProfile())
    issue = next(i for i in check.run() if i.check_name == "swap_on_sd")
    fix = check.fix(issue)
    assert fix.success is True
