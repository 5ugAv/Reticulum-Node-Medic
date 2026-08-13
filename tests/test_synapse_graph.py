"""SYNAPSE phase 3 — deficiency detection that never invents what it didn't check.

The break-lens corpus cases are pinned as named tests: R1-R8 (cold start,
hop-limit boundary, Wilson bounds, per-component articulation, parallel-path
fixes, could-not-check marginal links, inert saturation, occupancy-weighted
gaps) and S1-S3 (performance bounds that an O(V^3) implementation cannot meet
by construction). Placement analysis is LoRa-first: wifi/internet/bluetooth
edges are context, never placement signal (operator, 2026-08-13).
"""

import math
import os
import random
import time

from monitor.health_beacon import encode, decode
from monitor.registry import NodeRegistry
from monitor.synapse_graph import (
    TIER_PROPAGATION,
    TIER_TRANSPORT,
    TIER_USER,
    analyze,
    articulation_points,
    build_analysis_graph,
    hops_to_propagation,
    path_reliability,
    tier_of,
    wilson_bounds,
    wilson_lower,
)
from monitor.synapse_links import MAX_HOPS_TO_PROP, MIN_RELIABILITY
from monitor.topology import Topology, TopoNode, TopoEdge, MEDIC_ID

NOW = 1_000_000.0
#: Generous CI allowance on the wall-clock budgets; the bound still exists.
PERF_MULT = float(os.environ.get("MEDIC_PERF_MULT", "5"))


def _topo(nodes, edges):
    return Topology(
        nodes=[TopoNode(id=i, name=i, lat=lat, lon=lon,
                        is_medic=(i == MEDIC_ID))
               for i, lat, lon in nodes],
        edges=[TopoEdge(a, b, transport=t) for a, b, t in edges],
        generated_at=NOW)


def _roster(**types):
    return {nid: {"type": t} for nid, t in types.items()}


def _lora_chain(names):
    return [(names[i], names[i + 1], "lora") for i in range(len(names) - 1)]


def _beacon():
    return decode(encode(uptime_s=100, heap_kb=140, wifi_rssi_dbm=-70,
                         reset_reason=0, wifi_up=True, lora_up=True,
                         tcp_backbone_up=True, local_tcp_server_up=True,
                         wdt_armed=True, psram=True, fault=False,
                         board_id=0x3F, fw=(0, 6, 2)))


def _kinds(defs, kind):
    return [d for d in defs if d.kind == kind]


# ---- tiers: evidence in, honesty out -----------------------------------------

def test_tier_from_the_kin_roster_is_checked():
    roster = _roster(pp="pi_propagation", tn="rtnode2400", pi="pi")
    assert tier_of("pp", roster=roster)[0:2] == (TIER_PROPAGATION, True)
    assert tier_of("tn", roster=roster)[0:2] == (TIER_TRANSPORT, True)
    assert tier_of("pi", roster=roster)[0:2] == (TIER_TRANSPORT, True)


def test_tier_from_registry_evidence_is_checked():
    reg = NodeRegistry()
    rec = reg.register("dddd", name="Prop")
    rec.node_type = "pi_propagation"          # set only from a self-report
    reg.register("eeee", name="Rt")
    reg.ingest("eeee", _beacon(), now=NOW)    # spoke the beacon protocol
    assert tier_of("dddd", registry=reg)[0:2] == (TIER_PROPAGATION, True)
    assert tier_of("eeee", registry=reg)[0:2] == (TIER_TRANSPORT, True)


def test_a_registry_default_is_not_evidence_of_tier():
    # register() types unknown nodes "rtnode2400" by default, and that default
    # has mislabelled real hardware before (kin_roster.type_for_cert,
    # 2026-08-10). No beacon, no roster -> user-only, could not check.
    reg = NodeRegistry()
    reg.register("ffff", name="Bare")
    tier, checked, basis = tier_of("ffff", registry=reg)
    assert tier == TIER_USER and checked is False
    assert "could not check" in basis


# ---- LoRa-first: wifi is context, never placement signal ---------------------

def test_a_wifi_only_link_does_not_count_for_placement():
    topo = _topo([("pp", 0.0, 0.0), ("wa", 0.0, 0.01), ("lb", 0.0, 0.02)],
                 [("pp", "wa", "wifi"), ("pp", "lb", "lora")])
    graph = build_analysis_graph(topo, roster=_roster(pp="pi_propagation"))
    assert "wa" not in graph.adj.get("pp", set())      # wifi: context only
    assert "lb" in graph.adj["pp"]
    assert len(graph.context_edges) == 1
    dist, _ = hops_to_propagation(graph)
    assert "wa" not in dist                            # orphan by LoRa
    orphans = _kinds(analyze(graph), "orphan_risk")
    assert any("wa" in d.nodes for d in orphans)


# ---- R1: zero-propagation cold start -----------------------------------------

def test_r1_cold_start_is_one_calm_recommendation_not_n_alarms():
    topo = _topo([("u1", 10.0, 10.0), ("u2", 10.0, 14.0), ("u3", 10.0, 10.0)],
                 _lora_chain(["u1", "u2", "u3"]))
    graph = build_analysis_graph(topo)
    defs = analyze(graph, occupancy={"u1": 3, "u2": 1})
    assert _kinds(defs, "orphan_risk") == []           # not N alarms
    cold = _kinds(defs, "no_propagation")
    assert len(cold) == 1 and cold[0].severity == 1
    # weighted centroid: u3 has no occupancy figure -> counts as 1 person
    lat, lon = cold[0].detail["centroid"]
    assert math.isclose(lat, 10.0)
    assert math.isclose(lon, (10.0 * 3 + 14.0 * 1 + 10.0 * 1) / 5)
    assert "first propagation node" in cold[0].recommendation


def test_r1_cold_start_with_no_locations_admits_it():
    topo = _topo([("u1", None, None), ("u2", None, None)],
                 _lora_chain(["u1", "u2"]))
    cold = _kinds(analyze(build_analysis_graph(topo)), "no_propagation")
    assert len(cold) == 1 and cold[0].checked is False
    assert cold[0].detail.get("centroid") is None


# ---- R2: the hop-limit boundary ----------------------------------------------

def _chain_graph(extra_hops):
    names = ["pp"] + [f"n{i}" for i in range(MAX_HOPS_TO_PROP + extra_hops)]
    topo = _topo([(n, None, None) for n in names], _lora_chain(names))
    return build_analysis_graph(topo, roster=_roster(pp="pi_propagation")), \
        names[-1]


def test_r2_exactly_at_the_hop_limit_passes_but_is_surfaced_as_marginal():
    graph, last = _chain_graph(0)                      # farthest = exactly 10
    defs = _kinds(analyze(graph), "prop_distance")
    violations = [d for d in defs if not d.detail.get("at_limit")]
    marginal = [d for d in defs if d.detail.get("at_limit")]
    assert violations == []
    assert len(marginal) == 1 and last in marginal[0].nodes


def test_r2_one_hop_beyond_the_limit_is_a_violation():
    graph, last = _chain_graph(1)                      # farthest = 11
    defs = _kinds(analyze(graph), "prop_distance")
    violations = [d for d in defs if not d.detail.get("at_limit")]
    assert len(violations) == 1 and last in violations[0].nodes
    assert violations[0].severity == 2


# ---- R3: ten out of ten is not certainty -------------------------------------

def test_r3_wilson_lower_bound_of_a_perfect_score():
    lo = wilson_lower(10, 10)
    assert 0.70 < lo < 0.75                            # NOT 1.0
    lo2, hi2 = wilson_bounds(10, 10)
    assert lo2 == lo and hi2 == 1.0


def test_r3_path_reliability_is_a_range_and_admits_unmeasured_hops():
    full = path_reliability([(10, 10), (10, 10)])
    assert full["checked"] is True
    assert math.isclose(full["low"], wilson_lower(10, 10) ** 2)
    assert full["high"] == 1.0
    thin = path_reliability([(10, 10), None])
    assert thin["checked"] is False and thin["low"] is None
    assert thin["unmeasured_hops"] == 1
    assert thin["high"] == 1.0                         # an upper bound only


def test_r3_a_measured_bad_hop_is_a_reliability_violation():
    topo = _topo([("pp", None, None), ("x", None, None)],
                 _lora_chain(["pp", "x"]))
    graph = build_analysis_graph(topo, roster=_roster(pp="pi_propagation"))
    defs = analyze(graph, reliability_samples={("pp", "x"): (5, 10)})
    rel = [d for d in _kinds(defs, "prop_distance")
           if "reliability_high" in d.detail]
    assert len(rel) == 1
    assert rel[0].detail["reliability_high"] < MIN_RELIABILITY


# ---- R4 + R5: articulation points, fixed by addition -------------------------

def test_r4_articulation_points_are_found_in_every_component():
    adj = {"p1": {"a"}, "a": {"p1", "b"}, "b": {"a"},
           "c": {"d"}, "d": {"c", "e"}, "e": {"d"}}
    assert articulation_points(adj) == {"a", "d"}


def test_r4_analysis_reports_cut_nodes_on_a_disconnected_graph():
    topo = _topo([(n, None, None) for n in "pabqcd"],
                 _lora_chain(["p", "a", "b"]) + _lora_chain(["q", "c", "d"]))
    graph = build_analysis_graph(
        topo, roster=_roster(p="pi_propagation", q="pi_propagation"))
    cuts = {d.nodes[0] for d in _kinds(analyze(graph), "single_point")}
    assert cuts == {"a", "c"}


def test_r5_the_fix_is_a_parallel_path_never_a_replacement():
    topo = _topo([(n, None, None) for n in "pab"], _lora_chain(["p", "a", "b"]))
    graph = build_analysis_graph(topo, roster=_roster(p="pi_propagation"))
    cut = _kinds(analyze(graph), "single_point")[0]
    assert "parallel" in cut.recommendation.lower()
    assert "replac" not in cut.recommendation.lower()


def test_the_medic_is_never_reported_as_a_single_point():
    # Every observed topology is medic-centric, so the medic is trivially a
    # cut vertex of the PICTURE — an artefact of where the observer stood,
    # not a fact about the mesh. And the medic leaves; it is not infrastructure.
    topo = _topo([(MEDIC_ID, None, None), ("pp", None, None),
                  ("u1", None, None), ("u2", None, None)],
                 [(MEDIC_ID, "pp", "lora"), (MEDIC_ID, "u1", "lora"),
                  (MEDIC_ID, "u2", "lora")])
    graph = build_analysis_graph(topo, roster=_roster(pp="pi_propagation"))
    assert _kinds(analyze(graph), "single_point") == []


# ---- R6: marginal links — measured or admitted, never fabricated -------------

def test_r6_a_measured_weak_link_is_marginal_and_an_unmeasured_one_is_honest():
    topo = Topology(
        nodes=[TopoNode(id=n, name=n) for n in ("pp", "wk", "uk")],
        edges=[TopoEdge("pp", "wk", rssi=-112, transport="lora"),
               TopoEdge("pp", "uk", transport="lora")],
        generated_at=NOW)
    graph = build_analysis_graph(topo, roster=_roster(pp="pi_propagation"))
    defs = _kinds(analyze(graph), "marginal_link")
    measured = [d for d in defs if d.checked]
    unchecked = [d for d in defs if not d.checked]
    assert len(measured) == 1 and set(measured[0].nodes) == {"pp", "wk"}
    assert len(unchecked) == 1                          # ONE calm line
    assert "could not check" in unchecked[0].summary.lower()
    assert unchecked[0].detail["unmeasured_links"] == 1


def test_r6_nothing_is_flagged_when_every_measured_link_is_healthy():
    topo = Topology(
        nodes=[TopoNode(id=n, name=n) for n in ("pp", "ok")],
        edges=[TopoEdge("pp", "ok", rssi=-80, transport="lora")],
        generated_at=NOW)
    graph = build_analysis_graph(topo, roster=_roster(pp="pi_propagation"))
    assert _kinds(analyze(graph), "marginal_link") == []


# ---- R7: saturation is inert without traffic data ----------------------------

def test_r7_no_traffic_data_means_no_saturation_findings_at_all():
    topo = _topo([("pp", None, None), ("x", None, None)],
                 _lora_chain(["pp", "x"]))
    graph = build_analysis_graph(topo, roster=_roster(pp="pi_propagation"))
    defs = analyze(graph)                               # no traffic given
    assert _kinds(defs, "saturation") == []
    assert not any("assum" in d.summary.lower() for d in defs)


def test_r7_real_traffic_data_wakes_saturation_up():
    topo = _topo([("pp", None, None), ("x", None, None)],
                 _lora_chain(["pp", "x"]))
    graph = build_analysis_graph(topo, roster=_roster(pp="pi_propagation"))
    sat = _kinds(analyze(graph, traffic={"x": 0.92}), "saturation")
    assert len(sat) == 1 and sat[0].nodes == ["x"] and sat[0].severity == 6
    assert _kinds(analyze(graph, traffic={"x": 0.30}), "saturation") == []


# ---- R8: coverage gaps weighted by occupancy ---------------------------------

def test_r8_gaps_rank_by_occupancy_where_known_and_admit_where_not():
    topo = _topo([("pp", 0.0, 0.0),
                  ("g1", 0.10, 0.0), ("g2", 0.13, 0.0),    # ~3.3 km gap
                  ("g3", 0.40, 0.0), ("g4", 0.43, 0.0)],   # ~3.3 km gap
                 [("pp", "g1", "lora"), ("pp", "g3", "lora")])
    graph = build_analysis_graph(topo, roster=_roster(pp="pi_propagation"))
    gaps = _kinds(analyze(graph, occupancy={"g3": 6, "g4": 4}),
                  "coverage_gap")
    pairs = [set(d.nodes) for d in gaps]
    assert {"g3", "g4"} in pairs and {"g1", "g2"} in pairs
    assert pairs[0] == {"g3", "g4"}                     # 10 people beat 2
    weighted = gaps[0]
    assert weighted.detail["weight"] == 10
    unknown = next(d for d in gaps if set(d.nodes) == {"g1", "g2"})
    assert unknown.detail["occupancy_known"] is False


# ---- ordering: the handover's severity ranking, exactly ----------------------

def test_findings_come_out_in_severity_order():
    topo = _topo([("pp", 0.0, 0.0), ("g1", 0.10, 0.0), ("g2", 0.13, 0.0),
                  ("lone", 5.0, 5.0)],
                 [("pp", "g1", "lora"), ("pp", "g2", "lora"),
                  ("g1", "g2", "lora")])
    graph = build_analysis_graph(topo, roster=_roster(pp="pi_propagation"))
    defs = analyze(graph, traffic={"pp": 0.95})
    sevs = [d.severity for d in defs]
    assert sevs == sorted(sevs)
    assert any(d.kind == "orphan_risk" for d in defs)   # lone is stranded


# ---- S1-S3: performance bounds (an O(V^3) all-pairs pass cannot meet these) --

def _perf_topology(n_nodes, n_edges, seed=7):
    rng = random.Random(seed)
    names = [f"x{i}" for i in range(n_nodes)]
    nodes = []
    for i, n in enumerate(names):
        if i % 2 == 0:                                  # half are located
            nodes.append((n, rng.uniform(-38.5, -36.5),
                          rng.uniform(144.0, 146.0)))
        else:
            nodes.append((n, None, None))
    edges = _lora_chain(names)                          # connected backbone
    while len(edges) < n_edges:
        a, b = rng.sample(names, 2)
        edges.append((a, b, "lora"))
    roster = _roster(**{names[i]: "pi_propagation"
                        for i in range(0, n_nodes, 100)})
    return _topo(nodes, edges), roster


def test_s1_full_analysis_of_500_nodes_2000_edges_is_under_a_second():
    topo, roster = _perf_topology(500, 2000)
    graph = build_analysis_graph(topo, roster=roster)
    t0 = time.perf_counter()
    analyze(graph, traffic={f"x{i}": 0.8 for i in range(0, 500, 10)})
    assert time.perf_counter() - t0 < 1.0 * PERF_MULT


def test_s2_multi_source_bfs_on_500_nodes_is_under_100ms():
    # O(V+E). A Floyd-Warshall would run ~500^3 inner steps here and cannot
    # pass this bound in Python by construction.
    topo, roster = _perf_topology(500, 2000)
    graph = build_analysis_graph(topo, roster=roster)
    t0 = time.perf_counter()
    hops_to_propagation(graph)
    assert time.perf_counter() - t0 < 0.1 * PERF_MULT


def test_s3_doubling_the_mesh_stays_within_a_linear_budget():
    topo, roster = _perf_topology(1000, 4000)
    graph = build_analysis_graph(topo, roster=roster)
    t0 = time.perf_counter()
    analyze(graph)
    assert time.perf_counter() - t0 < 2.0 * PERF_MULT
