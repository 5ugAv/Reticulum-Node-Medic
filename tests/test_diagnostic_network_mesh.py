import pytest

from node_profile import NodeProfile
from transport.connection import EmulatedConnection
from diagnostics.network_mesh import NetworkMeshCheck


def healthy_conn():
    return (
        EmulatedConnection()
        .rule("rnpath -t", code=0, stdout="a1b2c3 via d4e5f6  [2 hops]")
        .rule("journalctl -u rnsd", code=0,
              stdout="Sending announce for e5f6a1")
        .rule("rnstatus", code=0,
              stdout="Transport enabled\n3 paths known\nChannel load: 12%")
        .rule("--loop", code=0, stdout="loop ok")
        .rule("rnping", code=0, stdout="Valid reply received, 3 hops")
        .rule("rnprobe", code=0, stdout="announce heard")
        .rule("storage/identity", code=0, stdout="")
    )


def run(conn):
    return NetworkMeshCheck(conn, NodeProfile()).run()


def names(issues):
    return {i.check_name for i in issues}


def ins(conn, tup):
    conn.rules.insert(0, tup)
    return conn


def test_category_name():
    assert NetworkMeshCheck(healthy_conn(), NodeProfile()).category_name == (
        "Network & mesh"
    )


def test_all_healthy_no_issues():
    assert run(healthy_conn()) == []


def test_no_peers_heard():
    conn = ins(healthy_conn(), ("rnpath -t", 0, "", ""))
    assert "peers_heard" in names(run(conn))


def test_no_announces_sending():
    conn = ins(healthy_conn(), ("journalctl -u rnsd", 0, "nothing here", ""))
    assert "announces_sending" in names(run(conn))


def test_path_table_empty():
    conn = ins(healthy_conn(), ("rnstatus", 0, "Transport enabled\n0 paths known", ""))
    assert "path_table_populated" in names(run(conn))


def test_channel_congested():
    conn = ins(healthy_conn(), ("rnstatus", 0,
               "Transport enabled\n3 paths known\nChannel load: 88%", ""))
    assert "channel_congestion" in names(run(conn))


def test_l1_loopback_fails():
    conn = ins(healthy_conn(), ("--loop", 1, "", ""))
    assert "loopback_l1" in names(run(conn))


def test_l2_mesh_ping_fails():
    conn = ins(healthy_conn(), ("rnping", 1, "No reply", ""))
    assert "mesh_ping_l2" in names(run(conn))


def test_l3_announce_not_heard():
    conn = ins(healthy_conn(), ("rnprobe", 1, "", ""))
    assert "announce_heard_l3" in names(run(conn))


def test_identity_missing():
    conn = ins(healthy_conn(), ("storage/identity", 1, "", ""))
    issues = run(conn)
    assert "reticulum_identity" in names(issues)
    assert next(i for i in issues if i.check_name == "reticulum_identity").severity == (
        "critical"
    )


def test_three_level_ping_present():
    # confirm all three ping levels exist as distinct checks
    conn = EmulatedConnection(default_code=1, default_stdout="")
    n = names(run(conn))
    assert {"loopback_l1", "mesh_ping_l2", "announce_heard_l3"} <= n
