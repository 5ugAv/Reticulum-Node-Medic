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
    # Since the operator's layout note (2026-08-13, on the glass): the three
    # overlays are BUTTONS on a second header row aligned under Links /
    # Terrain / Recenter — grey text off, lane colour on.
    assert "_toggle_overlay" in text and "_paint_overlay_btns" in text
    assert "LINK_COLOURS" in text
    for t in ("wifi", "internet", "bluetooth"):
        assert f'"{t}"' in text
    # LoRa must not be toggleable
    assert "lora_toggle" not in text.lower().replace("_", "")


# --- a deleted node must not be resurrected by the path table ---------------

def test_forgotten_hashes_do_not_rise_from_the_path_table():
    """RNS's path table outlives a node by SEVEN DAYS — the project's oldest
    scar. Delete a node in VITALS and the map would re-create it as an
    anonymous ghost from rnpath rows (operator, 2026-08-13: 'make sure the
    maps dont keep old stale nodes'). A cached path is not a sighting."""
    from monitor.registry import NodeRegistry
    from monitor.topology import build_topology
    r = NodeRegistry()
    paths = [{"hash": "dd" * 16, "via": "ee" * 16, "hops": 2,
              "interface": "RNodeInterface[x]"}]
    topo = build_topology(r, paths, now=1000.0, exclude={"dd" * 16})
    ids = {n.id for n in topo.nodes}
    assert ("dd" * 16) not in ids, "the ghost came back from the path table"
    assert not any(("dd" * 16) in (e.a, e.b) for e in topo.edges)


def test_a_reborn_node_returns_because_it_was_heard_not_remembered():
    """Exclusion suppresses path-table memory only. A node the registry has
    heard ANEW (a real announce after the delete) is alive — it returns,
    tombstone or not."""
    from monitor.registry import NodeRegistry
    from monitor.topology import build_topology
    r = NodeRegistry()
    r.register("dd" * 16, name="EVERYWHERE")
    paths = [{"hash": "dd" * 16, "hops": 1, "interface": "RNodeInterface[x]"}]
    topo = build_topology(r, paths, now=1000.0, exclude={"dd" * 16})
    assert ("dd" * 16) in {n.id for n in topo.nodes}


# -- v3 self-location + LoRa edge strength (2026-08-27) ----------------------

def _beacon_v3(lat, lng, lora_rssi=-88):
    return decode(encode(uptime_s=50, heap_kb=100, wifi_rssi_dbm=0,
                         reset_reason=0, wifi_up=False, lora_up=True,
                         tcp_backbone_up=False, local_tcp_server_up=False,
                         wdt_armed=False, psram=False, fault=False,
                         board_id=0x3C, lat=lat, lng=lng, position_sats=9,
                         lora_rssi_dbm=lora_rssi))


def test_self_reported_position_outranks_the_birth_stamp():
    r = _registry()
    # aaaa moves and says so: its own GPS claim must place it, not the stamp
    r.ingest("aaaa", _beacon_v3(-37.512345, 145.523456), now=NOW)
    topo = build_topology(r, paths=[], now=NOW)
    n = next(n for n in topo.nodes if n.id == "aaaa")
    assert n.self_located is True
    assert n.lat == pytest.approx(-37.512345, abs=1e-5)
    assert n.lon == pytest.approx(145.523456, abs=1e-5)
    # the never-self-located node keeps its stamp, unflagged
    m = next(n for n in topo.nodes if n.id == "bbbb")
    assert m.self_located is False and m.lat == pytest.approx(-37.756)


def test_nodes_own_lora_rssi_becomes_a_typed_lora_edge():
    r = _registry()
    r.ingest("aaaa", _beacon_v3(-37.512345, 145.523456, lora_rssi=-92), now=NOW)
    topo = build_topology(r, paths=[], now=NOW)
    lora = [e for e in topo.edges
            if e.transport == "lora" and "aaaa" in (e.a, e.b)]
    assert len(lora) == 1 and lora[0].rssi == -92
    assert lora[0].kind == "direct" and MEDIC_ID in (lora[0].a, lora[0].b)


def test_located_nodes_includes_a_self_located_never_stamped_node():
    # the T114 gap (2026-08-27 on-glass): detail page showed its position,
    # the MAP had no dot — located_nodes only read the birth stamp.
    r = NodeRegistry()
    r.register("dddd", name="Walkabout")            # no stamped location
    r.ingest("dddd", _beacon_v3(-37.512345, 145.523456), now=NOW)
    dots = r.located_nodes(NOW)
    walker = [d for d in dots if d["name"] == "Walkabout"]
    assert walker and walker[0]["self_located"] is True
    assert walker[0]["lat"] == pytest.approx(-37.512345, abs=1e-5)


def test_located_nodes_prefers_the_nodes_own_claim_over_the_stamp():
    r = _registry()
    r.ingest("aaaa", _beacon_v3(-37.512345, 145.523456), now=NOW)
    dots = r.located_nodes(NOW)
    d = next(x for x in dots if x["name"] == "Wrenhill")
    assert d["lat"] == pytest.approx(-37.512345, abs=1e-5)
    assert d["self_located"] is True
    # the stamped-only node is untouched and unflagged
    d2 = next(x for x in dots if x["name"] == "Ironbark")
    assert d2["self_located"] is False


# --- one device, one node -------------------------------------------------
# Operator, 2026-09-09, looking at a map with FIVE dots stacked on one Pi:
# "we can't have one node during five duplicate registries. We need to fold
# them into a single node, and the paths need to act the same as well.
# Otherwise the map's gonna get really messy."
#
# A machine announces many destinations — a Pi's transport, its LXMF
# propagation aspect, anything minted after the paperwork — and each arrived
# as its own row. The fold key was already there and unused: the kin roster
# records which physical device every entry belongs to, and set_kin_roster
# stamps it on the record as device_id.

def _multi_aspect_registry():
    """One Pi announcing three destinations, plus an unrelated node."""
    r = NodeRegistry()
    for h in ("a1", "a2", "a3"):
        r.register(h, name="ELSEWHERE")
        r.nodes[h].device_id = "dev-pi"
    # only one aspect carries the coordinates, as on the real medic
    r.nodes["a2"].lat, r.nodes["a2"].lon = -37.512, 145.523
    r.register("zzzz", name="Faraway", lat=-37.700, lon=145.100)
    return r


def test_a_devices_aspects_collapse_to_one_node():
    topo = build_topology(_multi_aspect_registry(), paths=[], now=NOW)
    named = [n for n in topo.nodes if n.name == "ELSEWHERE"]
    assert len(named) == 1, f"one Pi must be ONE node, got {len(named)}"


def test_the_fold_keeps_the_coordinates_a_silent_sibling_lacks():
    """Only one aspect had a location. Folding must not blank the node."""
    topo = build_topology(_multi_aspect_registry(), paths=[], now=NOW)
    n = next(n for n in topo.nodes if n.name == "ELSEWHERE")
    assert (round(n.lat, 3), round(n.lon, 3)) == (-37.512, 145.523)


def test_an_unrelated_node_is_not_swept_into_the_fold():
    topo = build_topology(_multi_aspect_registry(), paths=[], now=NOW)
    assert any(n.name == "Faraway" for n in topo.nodes)
    assert len({n.id for n in topo.nodes if n.name in ("ELSEWHERE", "Faraway")}) == 2


def test_a_path_via_any_aspect_links_the_NODE():
    """"the paths need to act the same" — a link reached via one aspect must
    land on the folded node, not hang off a duplicate dot."""
    r = _multi_aspect_registry()
    r.register("bbbb", name="Ironbark", lat=-37.756, lon=145.005)
    topo = build_topology(r, paths=[{"hash": "bbbb", "via": "a3", "hops": 2}],
                          now=NOW)
    ids = {n.id for n in topo.nodes}
    assert "a3" not in ids, "the aspect must not survive as its own node"
    assert any({e.a, e.b} == {"dev-pi", "bbbb"} for e in topo.edges), \
        "the relayed link must attach to the device, not the aspect"


def test_two_aspects_of_one_device_never_link_to_each_other():
    """A path from one of a node's own aspects to another is not a mesh hop."""
    r = _multi_aspect_registry()
    topo = build_topology(r, paths=[{"hash": "a1", "via": "a2", "hops": 2}],
                          now=NOW)
    assert not [e for e in topo.edges if e.a == e.b], "no self-link"


def test_a_node_with_no_device_id_stands_for_itself():
    """Folding must never merge on anything weaker than the recorded device —
    two nodes a keeper happened to name the same are still two nodes."""
    r = NodeRegistry()
    r.register("p1", name="Twin", lat=-37.7, lon=145.0)
    r.register("p2", name="Twin", lat=-37.8, lon=145.1)
    topo = build_topology(r, paths=[], now=NOW)
    assert len([n for n in topo.nodes if n.name == "Twin"]) == 2
