import pytest

from node_profile import NodeProfile
from transport.connection import EmulatedConnection
from diagnostics.client_connectivity import ClientConnectivityCheck


def healthy_conn():
    return (
        EmulatedConnection()
        .rule(":4242", code=0, stdout="LISTEN 0 0 *:4242")
        .rule("pgrep -f lxmd", code=0, stdout="4321")
        .rule(":8000", code=0, stdout="LISTEN 0 0 *:8000")
        .rule("storage/lxmf", code=0, stdout="")
        .rule("^systemctl is-active meshtastic-bridge", code=0, stdout="active")
        .rule("/dev/ttyACM0", code=0, stdout="")
        # extended checks 54-56, 81
        .rule("journalctl -u lxmd", code=0, stdout="LXMF router up, nominal")
        .rule("systemctl cat lxmd", code=0,
              stdout="After=rnsd.service\nWants=rnsd.service\nExecStart=/x --service")
    )


def full_profile():
    p = NodeProfile()
    p.has_meshchat_client = True
    p.has_sideband_client = True
    p.has_meshtastic_client = True
    p.has_meshtastic_bridge = True
    return p


def run(conn, prof=None):
    return ClientConnectivityCheck(conn, prof or NodeProfile()).run()


def names(issues):
    return {i.check_name for i in issues}


def ins(conn, tup):
    conn.rules.insert(0, tup)
    return conn


def test_category_name():
    assert ClientConnectivityCheck(healthy_conn(), NodeProfile()).category_name == (
        "Client connectivity"
    )


def test_all_healthy_full_profile_no_issues():
    assert run(healthy_conn(), full_profile()) == []


def test_bare_profile_only_runs_ungated_checks():
    # with no client flags, only checks 44 & 45 run
    issues = run(healthy_conn(), NodeProfile())
    assert issues == []
    # break both ungated to confirm exactly those two are present
    conn = healthy_conn()
    ins(conn, (":4242", 1, "", ""))
    ins(conn, ("pgrep -f lxmd", 1, "", ""))
    assert names(run(conn, NodeProfile())) == {
        "tcp_interface_listening", "lxmf_delivery_running"}


def test_tcp_interface_not_listening():
    conn = ins(healthy_conn(), (":4242", 1, "", ""))
    assert "tcp_interface_listening" in names(run(conn))


def test_lxmf_delivery_not_running():
    conn = ins(healthy_conn(), ("pgrep -f lxmd", 1, "", ""))
    assert "lxmf_delivery_running" in names(run(conn))


def test_meshchat_gated_on_flag():
    conn = ins(healthy_conn(), (":8000", 1, "", ""))
    # no meshchat flag -> not checked
    assert "meshchat_tcp" not in names(run(conn, NodeProfile()))
    # with flag -> checked
    p = NodeProfile()
    p.has_meshchat_client = True
    assert "meshchat_tcp" in names(run(conn, p))


def test_lxmf_storage_gated_on_sideband_or_columba():
    conn = ins(healthy_conn(), ("storage/lxmf", 1, "", ""))
    assert "lxmf_storage_dir" not in names(run(conn, NodeProfile()))
    p = NodeProfile()
    p.has_columba_client = True
    assert "lxmf_storage_dir" in names(run(conn, p))


def test_meshtastic_bridge_gated_on_client_flag():
    conn = ins(healthy_conn(),
               ("^systemctl is-active meshtastic-bridge", 3, "inactive", ""))
    assert "meshtastic_bridge_running" not in names(run(conn, NodeProfile()))
    p = NodeProfile()
    p.has_meshtastic_client = True
    assert "meshtastic_bridge_running" in names(run(conn, p))


def test_meshtastic_board_gated_on_bridge_flag():
    conn = ins(healthy_conn(), ("/dev/ttyACM0", 1, "", ""))
    assert "meshtastic_board_connected" not in names(run(conn, NodeProfile()))
    p = NodeProfile()
    p.has_meshtastic_bridge = True
    assert "meshtastic_board_connected" in names(run(conn, p))


# ---- extended checks 54-56, 81 -------------------------------------------


def test_lxmd_store_full():
    conn = ins(healthy_conn(),
               ("journalctl -u lxmd", 0, "LXMF store full, dropping", ""))
    assert "lxmd_store_full" in names(run(conn))


def test_lxmd_statistics_timeout():
    conn = ins(healthy_conn(),
               ("journalctl -u lxmd", 0, "statistics timeout after 30s", ""))
    assert "lxmd_statistics_timeout" in names(run(conn))


def test_lxmd_peer_limit():
    conn = ins(healthy_conn(),
               ("journalctl -u lxmd", 0, "peer limit reached, refusing", ""))
    assert "lxmd_peer_limit" in names(run(conn))


def test_lxmd_after_rnsd_missing():
    conn = ins(healthy_conn(),
               ("systemctl cat lxmd", 0, "ExecStart=/x --service", ""))
    assert "lxmd_after_rnsd" in names(run(conn))


def test_fix_lxmd_after_rnsd_adds_dependency():
    conn = ins(healthy_conn(),
               ("systemctl cat lxmd", 0, "ExecStart=/x --service", ""))
    conn.rule("after.conf", code=0, stdout="")
    check = ClientConnectivityCheck(conn, NodeProfile())
    issue = next(i for i in check.run() if i.check_name == "lxmd_after_rnsd")
    fix = check.fix(issue)
    assert fix.success is True
