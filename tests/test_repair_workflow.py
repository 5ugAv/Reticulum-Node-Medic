import pytest

from node_profile import NodeProfile
from transport.connection import EmulatedConnection
from diagnostics.radio_firmware import LATEST_FIRMWARE
from workflows.repair import (
    RepairWorkflow,
    RepairSession,
    CategoryResult,
    ProgressEvent,
)

GOOD_INFO = "\n".join([
    "[Device] RNode",
    f"Firmware version: {LATEST_FIRMWARE}",
    "Firmware hash: 0badc0ffee",
    "Frequency: 915.125 MHz",
    "Bandwidth: 125.0 KHz",
    "TX power: 17 dBm",
    "Spreading factor: 9",
    "Coding rate: 5",
])

EXPECTED_ORDER = [
    "Power & hardware",
    "Reticulum software",
    "Radio & firmware",
    "System health",
    "Network & mesh",
    "Client connectivity",
]


def full_healthy_conn():
    return (
        EmulatedConnection()
        .rule("^systemctl is-active rnsd", 0, "active")
        .rule("^systemctl is-active lxmd", 0, "active")
        .rule("^systemctl is-active log2ram", 0, "active")
        .rule("^systemctl is-active meshtastic-bridge", 0, "active")
        .rule("^systemctl is-enabled rnsd", 0, "enabled")
        .rule("^systemctl is-enabled lxmd", 0, "enabled")
        .rule("^id -nG", 0, "pi dialout gpio")
        .rule("enable_transport = Yes", 0, "enable_transport = Yes")
        .rule("RNodeInterface", 0, "type = RNodeInterface")
        .rule("rnstatus", 0,
              "Interface Status: Up\n3 paths known\nChannel load: 12%")
        .rule("^which rnsd", 0, "/usr/local/bin/rnsd")
        .rule("--info", 0, GOOD_INFO)
        .rule("--loop", 0, "loop ok")
        .rule("--version", 0, "RNode 1.80")
        .rule("thermal_zone0/temp", 0, "45000")
        .rule("dmesg", 0, "[    0.000000] Booting Linux 6.1")
        .rule(".rtt_wtest", 0, "")
        .rule("MemAvailable", 0, "MemAvailable: 512000 kB")
        .rule("/proc/uptime", 0, "123456 60")
        .rule("--output=pcent", 0, "Use%\n 45%")
        .rule("chronyc tracking", 0, "System time : 0.0001 seconds fast")
        .rule("NTPSynchronized", 0, "yes")
        .rule("ps -eo stat", 0, "0")
        .rule("rnpath -t", 0, "a1 via b2 [2 hops]")
        .rule("journalctl -u rnsd", 0, "Sending announce for a1")
        .rule("rnping", 0, "Valid reply received")
        .rule("rnprobe", 0, "announce heard")
        .rule("^test -c", 0, "")
        .rule("^test -f", 0, "")
        .rule("^test -d", 0, "")
        .rule(":4242", 0, "LISTEN")
        .rule("pgrep -f lxmd", 0, "4321")
        .rule(":8000", 0, "LISTEN")
        # reticulum_software extended checks (50-53, 63, 78, 82-85)
        .rule("journalctl -u rnsd", 0, "Sending announce for a1")
        .rule("journalctl -u lxmd", 0, "LXMF router up")
        .rule("getfacl", 0, "user:pi:rw")
        .rule("systemctl cat rnsd", 0,
              "ExecStartPre=/bin/sleep 5\nExecStart=/usr/local/bin/rnsd")
        .rule("systemctl cat lxmd", 0,
              "After=rnsd.service\nWants=rnsd.service\n"
              "ExecStart=/usr/local/bin/lxmd --service")
        .rule("grep -c", 0, "0")
        .rule("37428", 0, 'LISTEN *:37428 users:(("rnsd",pid=1))')
        .rule("storage/identity", 0, "600")
        .rule("stat -c %a ~/.reticulum", 0, "700")
        .rule("announce_interval", 1, "")
    )


def test_healthy_run_has_no_issues():
    wf = RepairWorkflow(full_healthy_conn(), NodeProfile())
    session = wf.run()
    assert isinstance(session, RepairSession)
    assert session.all_issues == []


def test_categories_run_in_defined_order():
    wf = RepairWorkflow(full_healthy_conn(), NodeProfile())
    session = wf.run()
    assert [c.category for c in session.categories] == EXPECTED_ORDER


def test_category_result_passed_flag():
    wf = RepairWorkflow(full_healthy_conn(), NodeProfile())
    session = wf.run()
    assert all(isinstance(c, CategoryResult) and c.passed for c in session.categories)


def test_progress_events_fire():
    events = []
    wf = RepairWorkflow(full_healthy_conn(), NodeProfile())
    wf.run(on_progress=events.append)
    types = [e.type for e in events]
    assert types.count("category_start") == 6
    assert types.count("category_done") == 6
    assert types.count("run_complete") == 1
    # per-check events fire for _check-based checks
    assert "check_start" in types
    assert "check_done" in types
    # run_complete carries the session
    complete = next(e for e in events if e.type == "run_complete")
    assert isinstance(complete.session, RepairSession)


def broken_conn():
    conn = full_healthy_conn()
    # critical: rnsd down; warning: rnsd not enabled; info: just rebooted
    conn.rules.insert(0, ("^systemctl is-active rnsd", 3, "inactive", ""))
    conn.rules.insert(0, ("^systemctl is-enabled rnsd", 1, "disabled", ""))
    conn.rules.insert(0, ("/proc/uptime", 0, "60 30", ""))
    return conn


def test_dynamic_severity_checks_stream_as_progress():
    # cpu_temperature / disk_space / undervoltage used to build Issue directly
    # and never emitted progress events; now they route through _check and
    # stream live like every other check.
    events = []
    RepairWorkflow(full_healthy_conn(), NodeProfile()).run(on_progress=events.append)
    streamed = {e.check_name for e in events if e.type == "check_done"}
    assert {"cpu_temperature", "disk_space", "undervoltage"} <= streamed


def test_all_issues_sorted_critical_first():
    wf = RepairWorkflow(broken_conn(), NodeProfile())
    session = wf.run()
    ranks = [i.severity_rank for i in session.all_issues]
    assert ranks == sorted(ranks)
    assert session.all_issues[0].severity == "critical"
    # info issue is last
    assert session.all_issues[-1].severity == "info"


def test_auto_fixable_subset():
    wf = RepairWorkflow(broken_conn(), NodeProfile())
    session = wf.run()
    fixable = session.auto_fixable_issues
    assert all(i.auto_fixable for i in fixable)
    assert {i.check_name for i in fixable} <= {i.check_name for i in session.all_issues}
    assert "rnsd_running" in {i.check_name for i in fixable}


def test_fix_one_applies_single_fix():
    conn = broken_conn()
    conn.rule("^systemctl start rnsd", 0, "")
    wf = RepairWorkflow(conn, NodeProfile())
    session = wf.run()
    issue = next(i for i in session.all_issues if i.check_name == "rnsd_running")
    fix = wf.fix_one(issue)
    assert fix.success is True
    assert any("systemctl start rnsd" in c for c in conn.history)


def test_fix_all_applies_all_autofixable():
    conn = broken_conn()
    conn.rule("^systemctl start rnsd", 0, "")
    conn.rule("^systemctl enable rnsd", 0, "")
    wf = RepairWorkflow(conn, NodeProfile())
    wf.run()
    fixes = wf.fix_all()
    assert len(fixes) >= 2
    assert all(f.success for f in fixes if f.issue.check_name in
               {"rnsd_running", "rnsd_enabled"})


def test_fix_all_fires_progress():
    conn = broken_conn()
    conn.rule("^systemctl start rnsd", 0, "")
    conn.rule("^systemctl enable rnsd", 0, "")
    wf = RepairWorkflow(conn, NodeProfile())
    wf.run()
    events = []
    wf.fix_all(on_progress=events.append)
    assert len(events) >= 1
