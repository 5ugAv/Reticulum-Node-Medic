import pytest

from node_profile import NodeProfile
from transport.connection import EmulatedConnection
from diagnostics.network_mesh import NetworkMeshCheck


import json

# Real JSON schemas captured from RNS 1.3.7 on a LIVE node (rnsd + RNode up).


def rnstatus_json(status=True, chload=0.07, announce_freq=0.05):
    # outgoing_announce_frequency is a real rnstatus --json field (confirmed on
    # a live link); > 0 means this node is announcing onto the mesh.
    return json.dumps({"interfaces": [
        {"name": "AutoInterface[Default Interface]", "type": "AutoInterface",
         "status": True},
        {"name": "RNodeInterface[RNode Interface]", "type": "RNodeInterface",
         "status": status, "channel_load_short": chload,
         "channel_load_long": chload, "airtime_short": 0.0,
         "noise_floor": -94, "cpu_temp": 42, "battery_percent": 100,
         "outgoing_announce_frequency": announce_freq, "interference": None},
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
    # no outgoing announces on any interface AND no announce lines in the rnsd
    # JOURNAL (systemd rnsd has no logfile) -> flag
    conn = ins(healthy_conn(),
               ("rnstatus --json", 0, rnstatus_json(announce_freq=0), ""))
    conn.rules.insert(0, ("journalctl -u rnsd", 0, "rnsd interface up", ""))
    assert "announces_sending" in names(run(conn))


def test_path_table_empty():
    conn = ins(healthy_conn(), ("rnpath -t --json", 0, "[]", ""))
    assert "path_table_populated" in names(run(conn))


def test_channel_congested():
    # channel_load_short is a PERCENT (0-100): 85.0 == 85% -> congested
    conn = ins(healthy_conn(), ("rnstatus --json", 0, rnstatus_json(chload=85.0), ""))
    assert "channel_congestion" in names(run(conn))


def test_channel_not_congested_on_normal_percent_load():
    # a real busy-but-fine node reads e.g. 18.66% -> must NOT flag (the old
    # fraction logic read this as 1866% congested)
    conn = ins(healthy_conn(), ("rnstatus --json", 0, rnstatus_json(chload=18.66), ""))
    assert "channel_congestion" not in names(run(conn))


def test_l1_interface_down_flagged():
    # L1 is now a real signal: rnsd reports the RNode interface status=False when
    # the radio won't come up (param mismatch, lost port, unsupported params).
    conn = ins(healthy_conn(),
               ("rnstatus --json", 0, rnstatus_json(status=False), ""))
    assert "loopback_l1" in names(run(conn))


def test_l2_mesh_ping_fails():
    conn = ins(healthy_conn(), ("rnping", 1, "No reply", ""))
    assert "mesh_ping_l2" in names(run(conn))


def test_l3_no_announces_heard_when_radio_up():
    # radio up but nothing heard from the mesh: no incoming announces + empty
    # path table (the old check probed a placeholder "mesh-test" destination).
    conn = ins(healthy_conn(), ("rnpath -t --json", 0, "[]", ""))
    assert "announce_heard_l3" in names(run(conn))


def test_identity_missing():
    # both the client (storage/identity) and transport (transport_identity)
    # paths must be absent for a real "no identity".
    conn = healthy_conn()
    conn.rules.insert(0, ("storage/transport_identity", 1, "", ""))
    conn.rules.insert(0, ("storage/identity", 1, "", ""))
    issues = run(conn)
    assert "reticulum_identity" in names(issues)
    assert next(i for i in issues if i.check_name == "reticulum_identity").severity == (
        "critical"
    )


def test_three_level_checks_fire_on_their_real_conditions():
    # each level is a real signal now, firing under its own condition
    assert "loopback_l1" in names(run(               # L1: radio interface down
        ins(healthy_conn(),
            ("rnstatus --json", 0, rnstatus_json(status=False), ""))))
    assert "mesh_ping_l2" in names(run(              # L2: known peer, no reply
        ins(healthy_conn(), ("rnping", 1, "No reply", ""))))
    assert "announce_heard_l3" in names(run(         # L3: up but nothing heard
        ins(healthy_conn(), ("rnpath -t --json", 0, "[]", ""))))


def test_interface_down_surfaces_journal_reason():
    # a down interface pulls its cause from the rnsd journal (not a logfile)
    conn = healthy_conn()
    conn.rules.insert(0, ("journalctl -u rnsd", 0,
                          "[Error] Radio state mismatch\n"
                          "[Error] Aborting RNode startup", ""))
    conn.rules.insert(0, ("rnstatus --json", 0, rnstatus_json(status=False), ""))
    issue = next(i for i in run(conn) if i.check_name == "loopback_l1")
    assert "Aborting RNode startup" in issue.description


def test_announces_fallback_reads_journal_not_logfile():
    conn = ins(healthy_conn(),
               ("rnstatus --json", 0, rnstatus_json(announce_freq=0), ""))
    run(conn)
    assert any("journalctl -u rnsd" in c for c in conn.history)
    assert not any(".reticulum/logfile" in c for c in conn.history)


def test_identity_accepted_at_transport_path():
    # a transport node has only storage/transport_identity, no storage/identity
    conn = healthy_conn()
    conn.rules.insert(0, ("storage/transport_identity", 0, "", ""))   # present
    conn.rules.insert(0, ("storage/identity", 1, "", ""))             # absent
    assert "reticulum_identity" not in names(run(conn))


def test_l2_not_double_reported_when_radio_down():
    # radio down -> L1 owns it; L2 must not also fire (can't ping without a radio)
    conn = healthy_conn()
    conn.rules.insert(0, ("rnping", 1, "No reply", ""))
    conn.rules.insert(0, ("rnstatus --json", 0, rnstatus_json(status=False), ""))
    n = names(run(conn))
    assert "loopback_l1" in n
    assert "mesh_ping_l2" not in n


def test_mesh_ping_uses_real_peer_hash_not_placeholder():
    conn = healthy_conn()
    run(conn)
    assert any("rnping ad272c7106cd9d86bbf1cf550f2610d8" in c
               for c in conn.history)
    assert not any("mesh-test" in c for c in conn.history)
