import pytest

from node_profile import NodeProfile
from transport.connection import EmulatedConnection
from diagnostics.reticulum_software import ReticulumSoftwareCheck


def healthy_conn():
    """An EmulatedConnection where every reticulum_software check passes."""
    return (
        EmulatedConnection()
        .rule("^systemctl is-active rnsd", code=0, stdout="active")
        .rule("^systemctl is-active lxmd", code=0, stdout="active")
        .rule("^systemctl is-enabled rnsd", code=0, stdout="enabled")
        .rule("^systemctl is-enabled lxmd", code=0, stdout="enabled")
        .rule("^id -nG", code=0, stdout="pi adm dialout sudo gpio")
        .rule("enable_transport = Yes", code=0, stdout="enable_transport = Yes")
        .rule("RNodeInterface", code=0, stdout="  type = RNodeInterface")
        .rule("^rnstatus", code=0, stdout="Interface  RNode LoRa\n  Status : Up")
        .rule("^test -c", code=0)
        .rule("^test -f", code=0)
        .rule("^which rnsd", code=0, stdout="/usr/local/bin/rnsd")
        # extended checks 50-53, 63, 78, 82-85
        .rule("journalctl -u rnsd", code=0, stdout="Sending announce for a1")
        .rule("getfacl", code=0, stdout="user::rw\nuser:pi:rw\ngroup::rw")
        .rule("systemctl cat rnsd", code=0,
              stdout="ExecStartPre=/bin/sleep 5\nExecStart=/usr/local/bin/rnsd")
        .rule("systemctl cat lxmd", code=0,
              stdout="ExecStart=/usr/local/bin/lxmd --service")
        .rule("journalctl -u lxmd", code=0, stdout="LXMF router up")
        .rule("grep -c", code=0, stdout="0")
        .rule("37428", code=0, stdout='LISTEN 0 0 *:37428 users:(("rnsd",pid=1))')
        .rule("storage/identity", code=0, stdout="600")
        .rule("stat -c %a ~/.reticulum", code=0, stdout="700")
        .rule("announce_interval", code=1, stdout="")
    )


def broken(*failing_rules):
    """Healthy connection with failing rule(s) prepended so they win."""
    conn = EmulatedConnection()
    for pattern, code, out in failing_rules:
        conn.rule(pattern, code=code, stdout=out)
    # append the healthy rules after, so the failing ones match first
    healthy = healthy_conn()
    conn.rules.extend(healthy.rules)
    return conn


def run(conn):
    return ReticulumSoftwareCheck(conn, NodeProfile()).run()


def names(issues):
    return {i.check_name for i in issues}


def test_category_name():
    assert ReticulumSoftwareCheck(healthy_conn(), NodeProfile()).category_name == (
        "Reticulum software"
    )


def test_all_healthy_no_issues():
    assert run(healthy_conn()) == []


def test_rnsd_not_running():
    conn = broken(("^systemctl is-active rnsd", 3, "inactive"))
    issues = run(conn)
    assert "rnsd_running" in names(issues)
    issue = next(i for i in issues if i.check_name == "rnsd_running")
    assert issue.severity == "critical"


def test_rnsd_not_enabled():
    conn = broken(("^systemctl is-enabled rnsd", 1, "disabled"))
    assert "rnsd_enabled" in names(run(conn))


def test_lxmd_not_running():
    conn = broken(("^systemctl is-active lxmd", 3, "inactive"))
    assert "lxmd_running" in names(run(conn))


def test_lxmd_not_enabled():
    conn = broken(("^systemctl is-enabled lxmd", 1, "disabled"))
    assert "lxmd_enabled" in names(run(conn))


def test_serial_port_permission_missing_dialout():
    conn = broken(("^id -nG", 0, "pi adm sudo gpio"))
    assert "serial_port_permission" in names(run(conn))


def test_transport_mode_disabled():
    conn = broken(("enable_transport = Yes", 1, ""))
    assert "transport_mode_enabled" in names(run(conn))


def test_radio_interface_down():
    conn = broken(("^rnstatus", 0, "Interface RNode\n  Status : Down"))
    assert "radio_interface_up" in names(run(conn))


def test_serial_port_missing():
    conn = broken(("^test -c", 1, ""))
    assert "serial_port_exists" in names(run(conn))


def test_config_missing():
    conn = broken(("^test -f", 1, ""))
    assert "config_present" in names(run(conn))


def test_rnode_interface_not_configured():
    conn = broken(("RNodeInterface", 1, ""))
    assert "rnode_interface_configured" in names(run(conn))


def test_reticulum_not_installed():
    conn = broken(("^which rnsd", 1, ""))
    assert "reticulum_installed" in names(run(conn))


# ---- fixes ---------------------------------------------------------------


def test_fix_start_rnsd_runs_systemctl_start():
    conn = broken(("^systemctl is-active rnsd", 3, "inactive"))
    # allow the start command to succeed
    conn.rule("^systemctl start rnsd", code=0, stdout="")
    check = ReticulumSoftwareCheck(conn, NodeProfile())
    issue = next(i for i in check.run() if i.check_name == "rnsd_running")
    fix = check.fix(issue)
    assert fix.success is True
    assert any("systemctl start rnsd" in c for c in conn.history)


def test_fix_add_dialout_runs_usermod():
    conn = broken(("^id -nG", 0, "pi adm sudo gpio"))
    conn.rule("^sudo usermod", code=0, stdout="")
    check = ReticulumSoftwareCheck(conn, NodeProfile())
    issue = next(
        i for i in check.run() if i.check_name == "serial_port_permission"
    )
    fix = check.fix(issue)
    assert fix.success is True
    assert any("usermod" in c and "dialout" in c for c in conn.history)


def test_run_produces_original_issues_when_everything_broken():
    # every command fails -> at least the 11 original checks should fire
    conn = EmulatedConnection(default_code=1, default_stdout="")
    issues = run(conn)
    original = {
        "rnsd_running", "rnsd_enabled", "lxmd_running", "lxmd_enabled",
        "serial_port_permission", "transport_mode_enabled",
        "radio_interface_up", "serial_port_exists", "config_present",
        "rnode_interface_configured", "reticulum_installed",
    }
    assert original <= names(issues)


# ---- extended checks 50-53, 63, 78, 82-85 --------------------------------


def test_warm_boot_param_mismatch():
    conn = broken(("journalctl -u rnsd", 0,
                   "Radio state mismatch: bandwidth mismatch detected"))
    assert "warm_boot_param_mismatch" in names(run(conn))


def test_serial_acl_missing():
    # an ACL with no rw grant at all
    conn = broken(("getfacl", 0, "user::r--\ngroup::r--\nother::---"))
    assert "serial_acl" in names(run(conn))


def test_serial_acl_stricter_than_substring():
    # rw present for owner/other but NOT reachable by our user 'pi' -> flag
    # (the old "rw in acl" check would have passed this — a false negative)
    conn = broken(("getfacl", 0,
                   "# owner: root\nuser::rw-\ngroup::r--\nother::rw-"))
    assert "serial_acl" in names(run(conn))


def test_serial_acl_ok_via_dialout_group():
    conn = broken(("getfacl", 0,
                   "# owner: root\n# group: dialout\nuser::rw-\n"
                   "group:dialout:rw-\nother::---"))
    assert "serial_acl" not in names(run(conn))


def test_serial_acl_ok_via_named_user_entry():
    conn = broken(("getfacl", 0,
                   "# owner: root\nuser::rw-\nuser:pi:rw-\ngroup::r--"))
    assert "serial_acl" not in names(run(conn))


def test_rnsd_startup_race():
    conn = broken(("systemctl cat rnsd", 0, "ExecStart=/usr/local/bin/rnsd"))
    assert "rnsd_startup_race" in names(run(conn))


def test_lxmd_service_flag_missing():
    conn = broken(("systemctl cat lxmd", 0, "ExecStart=/usr/local/bin/lxmd"))
    assert "lxmd_service_flag" in names(run(conn))


def test_shared_instance_cascade():
    conn = broken(("journalctl -u lxmd", 0,
                   "No shared instance; Reticulum will attempt to bring up"))
    assert "shared_instance_cascade" in names(run(conn))


def test_config_crlf_line_endings():
    conn = broken(("grep -c", 0, "12"))
    assert "config_line_endings" in names(run(conn))


def test_shared_instance_port_conflict():
    conn = broken(("37428", 0,
                   'LISTEN 0 0 *:37428 users:(("python3",pid=999))'))
    assert "shared_instance_port_conflict" in names(run(conn))


def test_port_conflict_not_flagged_when_owner_unidentifiable():
    # unprivileged `ss` shows the listener but no process (no pid=/users:) —
    # we must NOT raise a false-positive critical conflict.
    conn = broken(("37428", 0, "LISTEN 0 0 *:37428 *:*"))
    assert "shared_instance_port_conflict" not in names(run(conn))


def test_identity_permissions_wrong():
    conn = broken(("storage/identity", 0, "644"))
    assert "identity_permissions" in names(run(conn))


def test_config_dir_permissions_wrong():
    conn = broken(("stat -c %a ~/.reticulum", 0, "755"))
    assert "config_dir_permissions" in names(run(conn))


def test_announce_interval_too_aggressive():
    conn = broken(("announce_interval", 0, "announce_interval = 30"))
    assert "announce_interval" in names(run(conn))


def test_fix_line_endings_runs_sed():
    conn = broken(("grep -c", 0, "12"))
    conn.rule("sed -i 's/\\r//'", code=0, stdout="")
    check = ReticulumSoftwareCheck(conn, NodeProfile())
    issue = next(i for i in check.run() if i.check_name == "config_line_endings")
    fix = check.fix(issue)
    assert fix.success is True


def test_fix_identity_permissions_runs_chmod():
    conn = broken(("storage/identity", 0, "644"))
    conn.rule("chmod 600", code=0, stdout="")
    check = ReticulumSoftwareCheck(conn, NodeProfile())
    issue = next(i for i in check.run() if i.check_name == "identity_permissions")
    fix = check.fix(issue)
    assert fix.success is True
    assert any("chmod 600" in c for c in conn.history)
