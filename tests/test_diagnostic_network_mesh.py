import pytest

from node_profile import NodeProfile
from transport.connection import EmulatedConnection
from diagnostics.network_mesh import NetworkMeshCheck


import json

# Real JSON schemas captured from RNS 1.3.7 on a LIVE node (rnsd + RNode up).


def rnstatus_json(status=True, chload=0.07):
    return json.dumps({"interfaces": [
        {"name": "AutoInterface[Default Interface]", "type": "AutoInterface",
         "status": True},
        {"name": "RNodeInterface[RNode Interface]", "type": "RNodeInterface",
         "status": status, "channel_load_short": chload,
         "channel_load_long": chload, "airtime_short": 0.0,
         "noise_floor": -94, "cpu_temp": 42, "battery_percent": 100,
         "interference": None},
    ]})


# a real remote path (non-local interface) -> peers heard + table populated
RNPATH_JSON = json.dumps([
    {"hash": "ad272c7106cd9d86bbf1cf550f2610d8",
     "via": "5463bddfb8b41e0159c1b867e9981f36", "hops": 1,
     "expires": 1784171781,
     "interface": "TCPInterface[everywhere/192.168.1.187:4242]"},
])


def healthy_conn():
    return (
        EmulatedConnection()
        .rule("rnpath -t --json", code=0, stdout=RNPATH_JSON)
        .rule("journalctl -u rnsd", code=0,
              stdout="Sending announce for e5f6a1")
        .rule("rnstatus --json", code=0, stdout=rnstatus_json())
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
    # only a local destination (no remote peer heard)
    local_only = json.dumps([
        {"hash": "de04", "via": "de04", "hops": 0, "expires": 1,
         "interface": "LocalInterface[rns/default]"}])
    conn = ins(healthy_conn(), ("rnpath -t --json", 0, local_only, ""))
    assert "peers_heard" in names(run(conn))


def test_no_announces_sending():
    conn = ins(healthy_conn(), ("journalctl -u rnsd", 0, "nothing here", ""))
    assert "announces_sending" in names(run(conn))


def test_path_table_empty():
    conn = ins(healthy_conn(), ("rnpath -t --json", 0, "[]", ""))
    assert "path_table_populated" in names(run(conn))


def test_channel_congested():
    # channel_load_short is a 0.0-1.0 fraction -> 0.88 == 88% congested
    conn = ins(healthy_conn(), ("rnstatus --json", 0, rnstatus_json(chload=0.88), ""))
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
