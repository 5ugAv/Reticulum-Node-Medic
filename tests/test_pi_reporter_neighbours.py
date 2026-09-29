"""A Pi node reports who it hears — v4 only when it has something to say."""
import json

from monitor import health_beacon as hb
from monitor.pi_health_reporter import (PiHealthInputs, build_beacon_bytes,
                                        collect_pi_health, gather_neighbours)

BASE = dict(uptime_s=100, free_ram_kb=200_000, disk_used_pct=10, net_up=True,
            radio_up=True, rns_transport_up=True)
A = "5a11001100000000000000000000000b"
B = "5a120012000000000000000000000012"


def test_a_node_that_hears_nobody_keeps_sending_v2():
    """Ten bytes on every beacon forever to say 'nobody' is airtime we do not
    spend. The medic reads v2 as 'not reported', never as 'isolated'."""
    raw = build_beacon_bytes(PiHealthInputs(**BASE))
    assert raw[0] == hb.FORMAT_VERSION_V2
    assert hb.decode(raw).neighbours == []


def test_a_node_with_neighbours_sends_v4_and_the_medic_reads_them():
    raw = build_beacon_bytes(PiHealthInputs(neighbours=[(A, None, 30), (B, None, 700)], **BASE))
    assert raw[0] == hb.FORMAT_VERSION_V4
    got = hb.decode(raw).neighbours
    assert [n["short_hash"] for n in got] == [0xc627, 0xf7b0]
    assert got[0]["snr_db"] is None, "the path table carries no SNR; none is claimed"
    assert got[0]["age_s"] == 60 and got[1]["age_s"] == 900, "bucket upper bounds"


def test_gather_reads_one_hop_rows_from_the_nodes_own_path_table():
    now = 1_000_000.0
    table = [{"hash": A, "hops": 1, "timestamp": now - 45},
             {"hash": B, "hops": 2, "timestamp": now - 10},      # via someone: not ours
             {"hash": "zz", "hops": 1, "timestamp": now - 5},   # junk hash
             {"hash": "5a0a000a" + "0" * 24, "hops": 1, "timestamp": 0}]  # no time
    got = gather_neighbours(now=now, run=lambda cmd: json.dumps(table))
    assert got == [(A, None, 45.0)]


def test_gather_is_empty_on_any_failure():
    assert gather_neighbours(run=lambda cmd: "not json") == []
    assert gather_neighbours(run=lambda cmd: (_ for _ in ()).throw(OSError())) == []
    assert gather_neighbours(run=lambda cmd: "") == []
