"""SCAN topology core — graph building, components, gaps, weights, layout."""

import pytest

from monitor.topology import (
    build_topology, components, gap_pairs, edge_width, ring_layout, MEDIC_ID,
)
from monitor.registry import NodeRegistry
from monitor.health_beacon import encode, decode

NOW = 1_000_000.0


def _beacon(rssi=-70):
    return decode(encode(uptime_s=100, heap_kb=140, wifi_rssi_dbm=rssi,
                         reset_reason=0, wifi_up=True, lora_up=True,
                         tcp_backbone_up=True, local_tcp_server_up=True,
                         wdt_armed=True, psram=True, fault=False,
                         board_id=0x3F, fw=(0, 6, 2)))


def _registry():
    r = NodeRegistry()
    r.register("aaaa", name="Northcote", lat=-37.770, lon=145.000)
    r.register("bbbb", name="Thornbury", lat=-37.756, lon=145.005)
    r.register("cccc", name="Coburg", lat=-37.744, lon=144.965)
    r.ingest("aaaa", _beacon(-70), now=NOW)
    r.ingest("bbbb", _beacon(-95), now=NOW)
    return r                          # cccc registered but never heard


def test_medic_is_a_node_and_heard_nodes_get_direct_edges():
    topo = build_topology(_registry(), paths=[], now=NOW)
    assert MEDIC_ID in topo.node_ids()
    keys = {e.key() for e in topo.edges}
    assert ("aaaa", MEDIC_ID) in keys and ("bbbb", MEDIC_ID) in keys
    assert not any("cccc" in k for k in keys)        # never heard -> no line


def test_path_via_reveals_node_to_node_link():
    paths = [{"hash": "cccc", "via": "aaaa", "hops": 2}]
    topo = build_topology(_registry(), paths, now=NOW)
    keys = {e.key() for e in topo.edges}
    assert ("aaaa", "cccc") in keys                  # the relay link
    e = next(e for e in topo.edges if e.key() == ("aaaa", "cccc"))
    assert e.kind == "relayed" and e.rssi is None


def test_measured_edge_beats_path_implied_duplicate():
    paths = [{"hash": "aaaa", "via": None, "hops": 1}]
    topo = build_topology(_registry(), paths, now=NOW)
    e = next(e for e in topo.edges if e.key() == ("aaaa", MEDIC_ID))
    assert e.rssi == -70                             # kept the measured one


def test_unknown_path_nodes_are_added_as_placeholder_nodes():
    paths = [{"hash": "dddd", "via": "aaaa", "hops": 3}]
    topo = build_topology(_registry(), paths, now=NOW)
    assert "dddd" in topo.node_ids()


def test_components_detect_a_split_mesh():
    topo = build_topology(_registry(), paths=[], now=NOW)
    comps = components(topo)
    # medic+aaaa+bbbb connected; cccc isolated
    sizes = sorted(len(c) for c in comps)
    assert sizes == [1, 3]


def test_gap_pairs_finds_close_unlinked_pairs_split_first():
    topo = build_topology(_registry(), paths=[], now=NOW)
    gaps = gap_pairs(topo, max_km=5.0)
    pairs = {tuple(sorted((g["a"], g["b"]))) for g in gaps}
    assert ("aaaa", "cccc") in pairs                 # close but no line
    assert gaps[0]["split"] is True                  # cross-component gaps first
    mid = next(g for g in gaps if tuple(sorted((g["a"], g["b"]))) == ("aaaa", "cccc"))
    assert mid["midpoint"] == pytest.approx(((-37.770 - 37.744) / 2, (145.000 + 144.965) / 2))


def test_edge_width_scales_with_signal():
    assert edge_width(None) == 1.0
    assert edge_width(-120) == 1.0
    assert edge_width(-70) == 4.0
    assert edge_width(-95) == pytest.approx(2.5)


def test_ring_layout_is_deterministic_and_centres_best_connected():
    topo = build_topology(_registry(), paths=[], now=NOW)
    pos = ring_layout(topo, 400, 400)
    assert pos == ring_layout(topo, 400, 400)        # deterministic
    assert pos[MEDIC_ID] == (200, 200)               # medic has highest degree
    for nid, (x, y) in pos.items():
        assert 0 <= x <= 400 and 0 <= y <= 400
