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
    r.register("aaaa", name="Wrenhill", lat=-37.770, lon=145.000)
    r.register("bbbb", name="Ironbark", lat=-37.756, lon=145.005)
    r.register("cccc", name="Saltbush", lat=-37.752, lon=144.965)
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
    assert mid["midpoint"] == pytest.approx(((-37.770 - 37.752) / 2, (145.000 + 144.965) / 2))


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


# --- edges are typed by transport; Wi-Fi never masquerades as LoRa ----------

def test_path_interface_names_type_the_edge():
    """rnpath's interface field says HOW the medic reaches a destination —
    the one honest source of edge transport. RNodeInterface = LoRa,
    AutoInterface = the LAN (Wi-Fi bucket), TCP* = internet."""
    from monitor.topology import transport_of
    assert transport_of("RNodeInterface[RNode LoRa Interface]") == "lora"
    assert transport_of("AutoInterface[LAN Interface]") == "wifi"
    assert transport_of("TCPClientInterface[backbone]") == "internet"
    assert transport_of("TCPServerInterface[srv]") == "internet"
    assert transport_of("LocalServerInterface[rns/default]") == "local"
    assert transport_of("") == "unknown"
    assert transport_of(None) == "unknown"


def test_wifi_rssi_never_dresses_a_lora_edge(monkeypatch):
    """The SCAN screen drew rec.signal_dbm() — the node's WI-FI RSSI — as
    edge strength on every direct edge, which is assumption dressed as data
    (telemetry audit, 2026-08-13; operator: 'bad news for placing nodes').
    A registry-derived direct edge with only Wi-Fi evidence is a WIFI edge;
    its rssi may be drawn only there. A path row over RNodeInterface types
    the same pair LoRa — and carries no faked strength."""
    from monitor.registry import NodeRegistry
    from monitor.topology import build_topology
    r = NodeRegistry()
    rec = r.register("aa" * 16, name="ttt")
    rec.mesh_hops = 1
    import monitor.registry as _reg
    monkeypatch.setattr(type(rec), "signal_dbm", lambda self: -61, raising=False)
    paths = [{"hash": "aa" * 16, "hops": 1,
              "interface": "RNodeInterface[RNode LoRa Interface]"}]
    topo = build_topology(r, paths, now=1000.0)
    edges = [e for e in topo.edges if e.kind == "direct"]
    assert edges, "expected a direct edge"
    for e in edges:
        if e.transport == "lora":
            assert e.rssi is None, "a Wi-Fi number was drawn on a LoRa edge"
        if e.rssi == -61:
            assert e.transport == "wifi"


def test_relayed_edges_are_honest_about_unknown_transport():
    from monitor.registry import NodeRegistry
    from monitor.topology import build_topology
    r = NodeRegistry()
    paths = [{"hash": "bb" * 16, "via": "cc" * 16, "hops": 3,
              "interface": "RNodeInterface[RNode LoRa Interface]"}]
    topo = build_topology(r, paths, now=1000.0)
    relayed = [e for e in topo.edges if e.kind == "relayed"]
    assert relayed and relayed[0].transport == "unknown", \
        "the far segment's transport is not observable from the medic's chair"


def test_link_segments_carry_their_transport():
    """The map draws LoRa as the standard view and everything else as a
    coloured overlay (operator, 2026-08-13) — so a segment must say which
    transport it evidences, and the caller must be able to filter."""
    from monitor.topology import Topology, TopoNode, TopoEdge
    from ui.screens.scan_screen import link_segments
    t = Topology(generated_at=0)
    t.nodes = [TopoNode(id="a", name="a", status="ok", lat=-37.8, lon=145.0),
               TopoNode(id="b", name="b", status="ok", lat=-37.9, lon=145.1),
               TopoNode(id="c", name="c", status="ok", lat=-37.7, lon=144.9)]
    t.edges = [TopoEdge("a", "b", transport="lora"),
               TopoEdge("a", "c", transport="wifi")]
    segs = link_segments(t)
    assert {s[4] for s in segs} == {"lora", "wifi"}
    only_lora = link_segments(t, transports={"lora"})
    assert len(only_lora) == 1 and only_lora[0][4] == "lora"


def test_scan_screen_offers_the_overlay_toggles():
    """Wi-Fi / Bluetooth / Internet views are slot switches; LoRa is the
    standard view with no off switch; non-LoRa lines draw in their own
    colours, and an edge's strength is only ever a measurement of its own
    transport."""
    from tests.srcutil import src
    text = src("ui/screens/scan_screen.py")
    assert "OnOffToggle" in text
    assert "LINK_COLOURS" in text
    for t in ("wifi", "internet", "bluetooth"):
        assert f'"{t}"' in text
    # LoRa must not be toggleable
    assert "lora_toggle" not in text.lower().replace("_", "")
