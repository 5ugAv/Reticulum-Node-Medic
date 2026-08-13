"""SYNAPSE phase 4 — the recommender: what to add, where, and how cheap.

The tier rule's cheap-under-uncertainty cases and the G5 mast-raise case are
pinned here as named tests: propagation is only ever recommended on CHECKED
grounds (unreachable users, a junction of confirmed branches, a peering
articulation, measured traffic); thin or manufactured evidence keeps the
cheap transport tier and says "could not check". A mast raise on an existing
node is a first-class recommendation that outranks buying a new node, and
its 10 m and its 6 dB are separate typed fields that cannot be confused.
"""

import math

from monitor.synapse_graph import (
    TIER_PROPAGATION,
    TIER_TRANSPORT,
    analyze,
    build_analysis_graph,
    hops_to_propagation,
)
from monitor.synapse_links import (
    DEFAULT_HOP_RANGE_KM,
    LinkObservation,
    LinkObservationStore,
    MAX_HOPS_TO_PROP,
    POSITION_EXACT,
    POSITION_FUZZED,
)
from monitor.synapse_range import estimate_range
from monitor.synapse_recommend import (
    AUTOPEER_MAXDEPTH,
    MAST_RAISE_GAIN_DB,
    MAST_RAISE_M,
    predicted_links,
    recommend,
    tier_for,
)
from monitor.topology import Topology, TopoNode, TopoEdge
from monitor.placement import suggest
from tests.srcutil import src

NOW = 1_000_000.0
KM_DEG = 1.0 / 111.0                       # ~1 km in latitude degrees


def _topo(nodes, edges):
    return Topology(
        nodes=[TopoNode(id=i, name=i, lat=lat, lon=lon) for i, lat, lon in nodes],
        edges=[TopoEdge(a, b, transport=t, rssi=r) for a, b, t, r in edges],
        generated_at=NOW)


def _roster(**types):
    return {nid: {"type": t} for nid, t in types.items()}


def _lora(a, b, rssi=None):
    return (a, b, "lora", rssi)


def _obs(a, b, at=NOW, a_pos=POSITION_EXACT, b_pos=POSITION_EXACT, km=None):
    return LinkObservation(heard_by=a, heard_from=b, observed_at=at,
                           distance_km=km, heard_by_position=a_pos,
                           heard_from_position=b_pos)


def _confirmed_store(*node_ids):
    """Bidirectional, repeated evidence for each node — a confirmed anchor."""
    store = LinkObservationStore()
    for n in node_ids:
        store.add(_obs(n, "far1"))
        store.add(_obs("far1", n, at=NOW + 1))
    return store


class _FlatEarth:
    """A terrain store whose ground is flat and low — short paths are clear."""
    def elevation(self, lat, lon):
        return 0.0


class _Wall:
    """A terrain store with a ridge everywhere except at the endpoints asked
    about first — any path longer than a few hundred metres hits it."""
    def elevation(self, lat, lon):
        return 100.0 if abs(lon - 0.005) < 0.004 else 0.0


# ---- the default answer is the cheap node ------------------------------------

def _gap_graph():
    # pp serves g1; g2 sits 2 km past g1 with no link — a coverage gap.
    topo = _topo([("pp", 0.0, 0.0), ("g1", 0.0, 0.02), ("g2", 0.0, 0.04)],
                 [_lora("pp", "g1", -70)])
    return build_analysis_graph(topo, roster=_roster(pp="pi_propagation"))


def test_the_default_tier_is_the_cheap_transport_node():
    recs = [r for r in recommend(_gap_graph()) if r.action == "new_node"]
    assert recs
    r = recs[0]
    assert r.tier == TIER_TRANSPORT and r.tier_checked is True
    assert "cheap" in r.cost_note.lower()
    assert r.lat is not None and r.lon is not None


# ---- tier rule (a): users beyond reach of any store-and-forward --------------

def test_tier_a_users_still_beyond_hop_reach_get_propagation():
    # A chain pp-n0-...-n8 (n8 is 9 hops out), then a stranded user 2 km past
    # n8. A relay there still leaves the user 11 hops from pp: > the limit,
    # so only a store-and-forward AT the spot serves them.
    chain = ["pp"] + [f"n{i}" for i in range(9)]
    nodes = [(n, None, None) for n in chain[:-1]] + [("n8", 0.0, 0.0),
                                                     ("u", 0.0, 0.018)]
    edges = [_lora(chain[i], chain[i + 1]) for i in range(len(chain) - 1)]
    topo = _topo(nodes, edges)
    graph = build_analysis_graph(topo, roster=_roster(pp="pi_propagation"))
    recs = [r for r in recommend(graph)
            if r.action == "new_node" and "u" in r.detail_affected()]
    assert recs
    assert recs[0].tier == TIER_PROPAGATION
    assert "store-and-forward" in recs[0].tier_why


def test_tier_a_does_not_fire_when_a_relay_genuinely_reaches():
    graph = _gap_graph()                       # anchor is 1 hop from pp
    recs = [r for r in recommend(graph) if r.action == "new_node"]
    assert all(r.tier == TIER_TRANSPORT for r in recs)


# ---- tier rule (b): junctions must be CONFIRMED ------------------------------

def _three_branch_graph():
    # Three separate located branches, all within reach of a central spot.
    topo = _topo([("x", 0.0, -0.02), ("y", 0.0, 0.02), ("z", 0.02, 0.0)], [])
    return build_analysis_graph(topo)


def test_tier_b_a_junction_of_three_confirmed_branches_earns_propagation():
    graph = _three_branch_graph()
    dist, _ = hops_to_propagation(graph)
    tier, why, checked = tier_for(["x", "y", "z"], graph, dist,
                                  store=_confirmed_store("x", "y", "z"))
    assert tier == TIER_PROPAGATION and checked is True
    assert "junction" in why and "confirmed" in why


def test_tier_b_a_manufactured_junction_stays_cheap_and_says_so():
    # Single one-way observations, one of them through a fuzzed pin: this
    # junction is manufactured. Cheap tier, could-not-check out loud.
    store = LinkObservationStore()
    store.add(_obs("x", "far1"))                              # one-way, once
    store.add(_obs("y", "far1"))
    store.add(_obs("z", "far1", a_pos=POSITION_FUZZED))       # and fuzzed
    graph = _three_branch_graph()
    dist, _ = hops_to_propagation(graph)
    tier, why, checked = tier_for(["x", "y", "z"], graph, dist, store=store)
    assert tier == TIER_TRANSPORT and checked is False
    assert "could not check" in why.lower()


def test_tier_b_no_store_at_all_never_silently_upgrades():
    graph = _three_branch_graph()
    dist, _ = hops_to_propagation(graph)
    tier, why, checked = tier_for(["x", "y", "z"], graph, dist, store=None)
    assert tier == TIER_TRANSPORT and checked is False
    assert "could not check" in why.lower()


# ---- tier rule (c): the propagation peering path -----------------------------

def test_tier_c_removing_a_peering_articulation_earns_propagation():
    # Three propagation nodes on a long relay chain: p1-p2 and p2-p3 are 4
    # transport hops apart (they autopeer, <= ~6 hops) but p1-p3 is 8 (they
    # cannot). p2 is the only peering path between p1 and p3. A candidate
    # reaching p1 and p3 directly gives the store network a second spine.
    chain = ["p1", "r1", "r2", "r3", "p2", "r4", "r5", "r6", "p3"]
    topo = _topo([("p1", 0.0, 0.0), ("p3", 0.0, 0.04)]
                 + [(n, None, None) for n in chain
                    if n not in ("p1", "p3")],
                 [_lora(chain[i], chain[i + 1])
                  for i in range(len(chain) - 1)])
    graph = build_analysis_graph(
        topo, roster=_roster(p1="pi_propagation", p2="pi_propagation",
                             p3="pi_propagation"))
    dist, _ = hops_to_propagation(graph)
    tier, why, checked = tier_for(["p1", "p3"], graph, dist)
    assert tier == TIER_PROPAGATION and checked is True
    assert "peering" in why.lower()


def test_the_autopeer_depth_is_named_and_documented():
    assert AUTOPEER_MAXDEPTH == 6
    text = src("monitor/synapse_recommend.py")
    assert "autopeer" in text.lower()


# ---- tier rule (d): traffic only when measured -------------------------------

def test_tier_d_measured_saturation_earns_propagation():
    graph = _gap_graph()
    dist, _ = hops_to_propagation(graph)
    tier, why, checked = tier_for(["g1"], graph, dist, traffic={"g1": 0.92})
    assert tier == TIER_PROPAGATION and checked is True
    assert "load" in why.lower()


def test_tier_d_is_inert_with_zero_traffic_data():
    graph = _gap_graph()
    dist, _ = hops_to_propagation(graph)
    tier, why, checked = tier_for(["g1"], graph, dist, traffic=None)
    assert tier == TIER_TRANSPORT
    assert "assum" not in why.lower()          # never "assumed high"


# ---- G5: the mast raise ------------------------------------------------------

def _marginal_graph():
    topo = _topo([("pp", 0.0, 0.0), ("wk", 0.0, 0.02)],
                 [_lora("pp", "wk", -112)])
    return build_analysis_graph(topo, roster=_roster(pp="pi_propagation"))


def test_g5_metres_and_decibels_are_separate_typed_fields():
    raise_recs = [r for r in recommend(_marginal_graph())
                  if r.action == "raise_antenna"]
    assert raise_recs
    r = raise_recs[0]
    assert r.node in ("pp", "wk") and r.lat is None
    assert r.height_m == MAST_RAISE_M == 10.0
    assert r.predicted_gain_db == MAST_RAISE_GAIN_DB == 6.0
    assert r.height_m != r.predicted_gain_db   # unconfusable by construction
    assert "10 m" in r.summary and "6 dB" in r.summary


def test_g5_the_mast_raise_outranks_buying_a_new_node():
    recs = recommend(_marginal_graph())
    kinds = [r.action for r in recs
             if "marginal" in " ".join(r.resolves).lower()
             or r.action == "raise_antenna"]
    assert kinds and kinds[0] == "raise_antenna"
    relay = next((r for r in recs if r.action == "new_node"
                  and set(r.detail_affected()) >= {"pp", "wk"}), None)
    assert relay is not None
    alt = relay.alternative_actions[0]
    assert alt.height_m == 10.0 and alt.predicted_gain_db == 6.0


# ---- the output shape: confidence first, end to end --------------------------

def test_every_number_names_its_source_default_case():
    recs = [r for r in recommend(_gap_graph()) if r.action == "new_node"]
    r = recs[0]
    assert r.resolves and r.cost_note
    assert r.predicted_links
    for link in r.predicted_links:
        assert link.km > 0 and link.margin_km is not None
        assert link.source == "default"
        assert link.confidence in ("none", "low", "medium", "high")
    assert "resolved_points" in r.score_parts
    assert "cost_penalty" in r.score_parts


def test_a_measured_range_names_its_sample_count():
    store = LinkObservationStore()
    for i, km in enumerate([1.0, 2.0, 3.0, 4.0, 5.0]):
        store.add(LinkObservation(heard_by=f"a{i}", heard_from=f"b{i}",
                                  observed_at=NOW, distance_km=km))
    est = estimate_range(store)
    links, _ = predicted_links(_gap_graph(), 0.0, 0.03, est)
    assert links and all(l.source == "measured n=5" for l in links)


def test_a_link_past_the_measured_range_is_labelled_extrapolated():
    store = LinkObservationStore()
    for i, (km, rssi) in enumerate([(1.0, -60), (2.0, -61), (3.0, -61),
                                    (4.0, -62), (5.0, -63)]):
        store.add(LinkObservation(heard_by=f"a{i}", heard_from=f"b{i}",
                                  observed_at=NOW, distance_km=km,
                                  rssi_dbm=rssi))
    est = estimate_range(store)                # range 4.0, projected 7.5
    topo = _topo([("far", 0.0, 6.0 * KM_DEG)], [])
    graph = build_analysis_graph(topo)
    links, _ = predicted_links(graph, 0.0, 0.0, est)
    assert links and links[0].source == "extrapolated"
    assert links[0].viable is False            # never counted as a sure link


# ---- terrain: consulted where cached, honestly absent otherwise --------------

def test_cold_start_centroid_respects_cached_terrain():
    topo = _topo([("u1", 0.0, 0.0), ("u2", 0.0, 0.01)], [])
    graph = build_analysis_graph(topo)
    blocked = [r for r in recommend(graph, terrain_store=_Wall())
               if r.action == "new_node"]
    assert blocked and any("ground" in c.lower() or "terrain" in c.lower()
                           for c in blocked[0].cautions)
    silent = [r for r in recommend(graph) if r.action == "new_node"]
    assert silent and not any("terrain" in c.lower() or "ground" in c.lower()
                              for c in silent[0].cautions)   # fail-open


def test_cold_start_recommends_one_propagation_node():
    topo = _topo([("u1", 0.0, 0.0), ("u2", 0.0, 0.01)], [])
    graph = build_analysis_graph(topo)
    recs = [r for r in recommend(graph) if r.action == "new_node"]
    assert len(recs) == 1
    assert recs[0].tier == TIER_PROPAGATION
    assert "store-and-forward" in recs[0].tier_why


# ---- ranking: severity first, then cost --------------------------------------

def test_ranking_is_severity_first_then_cost():
    # An orphaned user (severity 1) and a marginal link (severity 4): the
    # orphan fix leads, and within the marginal fix the mast beats the node.
    topo = _topo([("pp", 0.0, 0.0), ("wk", 0.0, 0.02), ("lost", 0.0, 0.06),
                  ("nr", 0.0, 0.04)],
                 [_lora("pp", "wk", -112), _lora("pp", "nr", -70)])
    graph = build_analysis_graph(topo, roster=_roster(pp="pi_propagation"))
    recs = recommend(graph)
    assert recs[0].action == "new_node"
    assert "lost" in recs[0].detail_affected()
    raise_i = next(i for i, r in enumerate(recs)
                   if r.action == "raise_antenna")
    relay_i = next(i for i, r in enumerate(recs)
                   if r.action == "new_node"
                   and any("marginal" in s for s in r.resolves))
    assert raise_i < relay_i


# ---- placement.suggest() is now an adapter over this engine ------------------

def test_scan_suggest_sees_through_a_wifi_only_edge():
    # Two located nodes 2 km apart, "linked" only by wifi (same building's
    # network, not radio). The old transport-blind gap_pairs stayed silent;
    # the adapter must suggest the midpoint relay.
    topo = Topology(
        nodes=[TopoNode(id="aaaa", name="A", lat=0.0, lon=0.0),
               TopoNode(id="bbbb", name="B", lat=0.0, lon=0.018)],
        edges=[TopoEdge("aaaa", "bbbb", rssi=-50, transport="wifi")],
        generated_at=NOW)
    sugs = suggest(topo)
    assert sugs and sugs[0].kind == "fill_gap"
    assert math.isclose(sugs[0].lon, 0.009)


def test_scan_gap_window_comes_from_the_range_spine():
    # 8 km apart, no links anywhere: outside the old 3 km fallback, inside
    # 2x the spine's 5 km default. The spine is the one range answer now.
    topo = Topology(
        nodes=[TopoNode(id="aaaa", name="A", lat=0.0, lon=0.0),
               TopoNode(id="bbbb", name="B", lat=0.0, lon=8.0 * KM_DEG)],
        edges=[], generated_at=NOW)
    sugs = suggest(topo)
    assert sugs and sugs[0].kind == "fill_gap"
    assert 2.0 * DEFAULT_HOP_RANGE_KM == 10.0


def test_scan_suggest_keeps_its_public_signature():
    topo = Topology(nodes=[], edges=[], generated_at=NOW)
    assert suggest(topo) == []
    assert suggest(topo, None, None) == []     # positional, as callers use it
