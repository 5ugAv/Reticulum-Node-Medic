import pytest

from node_profile import NodeProfile
from transport.connection import EmulatedConnection
from diagnostics.network_mesh import NetworkMeshCheck


# Real formats captured from RNS 1.3.7 on a live instance.
RNPATH_OK = ("<ad272c7106cd9d86bbf1cf550f2610d8> is 1 hop  away via "
             "<5463bddfb8b41e0159c1b867e9981f36> on "
             "TCPInterface[everywhere/192.168.1.187:4242] "
             "expires 2026-07-12 12:43:33")


def rnstatus_text(status="Up", chload="12.0"):
    return (" RNodeInterface[RNode Interface]\n"
            f"    Status    : {status}\n"
            "    Airtime   : 0.0% (15s), 0.0% (1h)\n"
            f"    Ch. Load  : {chload}% (15s), 8.0% (1h)\n"
            " Transport Instance <840d52d57dc44bee758945945251451f> running")


def healthy_conn():
    return (
        EmulatedConnection()
        .rule("rnpath -t", code=0, stdout=RNPATH_OK)
        .rule("journalctl -u rnsd", code=0,
              stdout="Sending announce for e5f6a1")
        .rule("rnstatus", code=0, stdout=rnstatus_text())
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
    # no "is N hop away" entries in rnpath -t
    conn = ins(healthy_conn(), ("rnpath -t", 0, "", ""))
    assert "path_table_populated" in names(run(conn))


def test_channel_congested():
    conn = ins(healthy_conn(), ("rnstatus", 0, rnstatus_text(chload="88.0"), ""))
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
