"""SYNAPSE phase 3 — the analysis graph, and what is actually wrong with the mesh.

Takes the SCAN topology (monitor.topology), the tier evidence the medic already
holds (kin roster + registry), and the phase-2 range spine, and produces a
severity-ranked list of deficiencies. Two standing rules shape everything:

* **LoRa-first.** Placement analysis runs over lora / unknown / local edges
  only. Wi-Fi, Bluetooth and internet links are context — Wi-Fi range means
  "same building" and internet reach negates infill — so they never count as
  placement signal (operator, 2026-08-13, docs/SYNAPSE_DECISIONS.md). They are
  kept on the graph as ``context_edges`` so nothing pretends they don't exist.
* **Never invent what was not checked.** A node with no tier evidence is
  user-only *and says "could not check"*. A link with no signal or success
  measurement is not declared marginal or healthy — it is counted in one calm
  could-not-check line. Saturation without traffic data emits nothing at all,
  never "assumed high". Ten successes out of ten is a Wilson lower bound of
  ~0.72, not certainty.

Detection is ordered exactly as the handover ranks the harms:

1. orphan risk (multi-source BFS from every propagation node, O(V+E)),
2. propagation distance (> MAX_HOPS_TO_PROP; exactly-at-limit passes but is
   surfaced as marginal) and path reliability p^n on Wilson bounds,
3. single points of failure (Tarjan articulation points, per component),
4. marginal links (within FADE_MARGIN_DB of the floor, or measured success
   below MIN_RELIABILITY),
5. coverage gaps (close unlinked pairs, weighted by occupancy where known),
6. saturation (only when traffic data exists).

With zero propagation nodes the analysis does not fire N orphan alarms; it
makes ONE aggregate recommendation — the first propagation node, at the
people-weighted centroid of the known nodes.

This phase detects; it does not generate or score candidate sites (phase 4),
and it draws nothing (UI later). Pure data + arithmetic; no third-party
imports. All bounds are engineering judgement (see monitor.synapse_links)
except the graph algorithms, which are just graph algorithms.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Set, Tuple

from monitor.channel_util import utilisation_label
from monitor.synapse_links import (
    DEFAULT_HOP_RANGE_KM,
    FADE_MARGIN_DB,
    MAX_HOPS_TO_PROP,
    MIN_RELIABILITY,
    link_distance_km,
)
from monitor.synapse_range import RSSI_FLOOR_DBM
from monitor.topology import Topology

__all__ = [
    "TIER_PROPAGATION", "TIER_TRANSPORT", "TIER_USER",
    "PLACEMENT_TRANSPORTS", "SEVERITY",
    "MeshNode", "AnalysisGraph", "Deficiency",
    "tier_of", "build_analysis_graph", "hops_to_propagation",
    "articulation_points", "wilson_bounds", "wilson_lower",
    "path_reliability", "analyze",
]

# ---- tiers -------------------------------------------------------------------

TIER_PROPAGATION = "propagation"     # Pi + RNode running rnsd + lxmd
TIER_TRANSPORT = "transport"         # routes for others (RTNode-2400, plain Pi)
TIER_USER = "user_only"              # or nothing checkable says otherwise

#: Edge transports that count as placement signal. Everything else (wifi /
#: internet / bluetooth) is context: real, drawn, and irrelevant to where a
#: LoRa node should stand.
PLACEMENT_TRANSPORTS = ("lora", "unknown", "local")

#: The handover's harm ranking, pinned. no_propagation shares rank 1 with
#: orphan risk because it is the same harm — people the mail cannot reach.
SEVERITY = {
    "no_propagation": 1,
    "orphan_risk": 1,
    "prop_distance": 2,
    "single_point": 3,
    "marginal_link": 4,
    "coverage_gap": 5,
    "saturation": 6,
}

#: Roster/registry node_type -> tier. "pi" routes (it runs rnsd) but does not
#: run lxmd, so it is transport, not propagation.
_TYPE_TIERS = {
    "pi_propagation": TIER_PROPAGATION,
    "rtnode2400": TIER_TRANSPORT,
    "pi": TIER_TRANSPORT,
}


def tier_of(node_id: str, registry=None, roster: Optional[dict] = None
            ) -> Tuple[str, bool, str]:
    """(tier, checked, basis) for a node, from evidence only.

    The kin roster outranks everything — it is the operator's own record,
    written at birth. A registry record counts only where its node_type came
    in through an evidence door: ``pi_propagation`` is set solely from a
    node's self-report, and ``rtnode2400`` counts only alongside a beacon or
    HTTP status (register()'s bare default has mislabelled real hardware
    before — kin_roster.type_for_cert, 2026-08-10). Anything else is
    user-only with "could not check" said out loud."""
    entry = (roster or {}).get(node_id)
    if entry and entry.get("type") in _TYPE_TIERS:
        return _TYPE_TIERS[entry["type"]], True, "kin roster"
    rec = registry.get(node_id) if registry is not None else None
    if rec is not None:
        if rec.node_type == "pi_propagation":
            return TIER_PROPAGATION, True, "node self-reported as propagation"
        if rec.node_type in _TYPE_TIERS and (rec.latest_beacon is not None
                                             or rec.latest_http is not None):
            return _TYPE_TIERS[rec.node_type], True, \
                "spoke the beacon/status protocol"
    return TIER_USER, False, "could not check — no roster entry, no protocol"


# ---- the analysis graph ------------------------------------------------------

@dataclass
class MeshNode:
    id: str
    name: str
    tier: str
    tier_checked: bool
    tier_basis: str
    lat: Optional[float] = None
    lon: Optional[float] = None
    is_medic: bool = False
    #: kin | kindred | neighbour | unknown — who this node belongs to. Modify
    #: advice (mast raises) names kin and kindred ONLY: a neighbour's
    #: position is fuzzed by design, so where their antenna actually stands
    #: is not the medic's to claim (operator, 2026-08-13).
    provenance: str = "unknown"


@dataclass
class AnalysisGraph:
    nodes: Dict[str, MeshNode] = field(default_factory=dict)
    #: Placement adjacency — lora/unknown/local edges only.
    adj: Dict[str, Set[str]] = field(default_factory=dict)
    placement_edges: list = field(default_factory=list)
    #: Real links that are not placement signal (wifi/internet/bluetooth).
    context_edges: list = field(default_factory=list)

    def propagation_ids(self) -> List[str]:
        return [i for i, n in self.nodes.items()
                if n.tier == TIER_PROPAGATION]


def build_analysis_graph(topo: Topology, registry=None,
                         roster: Optional[dict] = None) -> AnalysisGraph:
    """The placement-analysis view of a SCAN topology: every node tiered from
    evidence, LoRa-signal edges wired for analysis, everything else kept as
    context. The medic is on the graph but tiered user-only — it is transient
    by definition and must never anchor an analysis."""
    graph = AnalysisGraph()
    for tn in topo.nodes:
        if tn.is_medic:
            tier, checked, basis = (TIER_USER, True,
                                    "the medic is transient — never an anchor")
        else:
            tier, checked, basis = tier_of(tn.id, registry=registry,
                                           roster=roster)
        prov = "unknown"
        rec = registry.nodes.get(tn.id) if registry is not None else None
        if rec is not None:
            try:
                prov = rec.provenance
            except Exception:                                      # noqa: BLE001
                prov = "unknown"
        if prov == "unknown" and roster and tn.id in roster:
            prov = "kin"                 # the roster IS the list of own nodes
        graph.nodes[tn.id] = MeshNode(
            id=tn.id, name=tn.name, tier=tier, tier_checked=checked,
            tier_basis=basis, lat=tn.lat, lon=tn.lon, is_medic=tn.is_medic,
            provenance=prov)
        graph.adj[tn.id] = set()
    for e in topo.edges:
        if e.a not in graph.nodes or e.b not in graph.nodes:
            continue
        if e.transport in PLACEMENT_TRANSPORTS:
            graph.placement_edges.append(e)
            graph.adj[e.a].add(e.b)
            graph.adj[e.b].add(e.a)
        else:
            graph.context_edges.append(e)
    return graph


# ---- graph algorithms (each O(V+E), and that is load-bearing) ----------------

def hops_to_propagation(graph: AnalysisGraph
                        ) -> Tuple[Dict[str, int], Dict[str, Optional[str]]]:
    """Multi-source BFS from every propagation node at once: (hops, parent)
    per reachable node. One pass over the graph — never all-pairs."""
    dist: Dict[str, int] = {}
    parent: Dict[str, Optional[str]] = {}
    dq: deque = deque()
    for pid in graph.propagation_ids():
        dist[pid] = 0
        parent[pid] = None
        dq.append(pid)
    while dq:
        u = dq.popleft()
        for v in graph.adj.get(u, ()):
            if v not in dist:
                dist[v] = dist[u] + 1
                parent[v] = u
                dq.append(v)
    return dist, parent


def articulation_points(adj: Dict[str, Set[str]]) -> Set[str]:
    """Tarjan's articulation points, iterative (field graphs can be chains
    hundreds deep — no recursion limit), run per component so a split mesh
    still has every cut vertex of every island found."""
    index: Dict[str, int] = {}
    low: Dict[str, int] = {}
    cuts: Set[str] = set()
    counter = 0
    for root in adj:
        if root in index:
            continue
        index[root] = low[root] = counter
        counter += 1
        root_children = 0
        stack = [(root, None, iter(adj[root]))]
        while stack:
            node, parent, it = stack[-1]
            descended = False
            for child in it:
                if child == parent:
                    continue
                if child in index:
                    low[node] = min(low[node], index[child])
                else:
                    index[child] = low[child] = counter
                    counter += 1
                    stack.append((child, node, iter(adj[child])))
                    descended = True
                    break
            if descended:
                continue
            stack.pop()
            if stack:
                pnode = stack[-1][0]
                low[pnode] = min(low[pnode], low[node])
                if pnode == root:
                    root_children += 1
                elif low[node] >= index[pnode]:
                    cuts.add(pnode)
        if root_children >= 2:
            cuts.add(root)
    return cuts


# ---- reliability: Wilson bounds, because n is small --------------------------

def wilson_bounds(successes: int, trials: int, z: float = 1.96
                  ) -> Tuple[float, float]:
    """The Wilson score interval for a success rate. Ten of ten is a lower
    bound near 0.72, not p = 1.0 — with a handful of trials the raw ratio
    flatters, and a mesh planned on flattery strands people."""
    if trials <= 0:
        raise ValueError("wilson_bounds of no trials")
    p = successes / trials
    z2 = z * z
    denom = 1.0 + z2 / trials
    centre = p + z2 / (2 * trials)
    radius = z * math.sqrt(p * (1 - p) / trials + z2 / (4 * trials * trials))
    return ((centre - radius) / denom,
            min(1.0, (centre + radius) / denom))


def wilson_lower(successes: int, trials: int, z: float = 1.96) -> float:
    return wilson_bounds(successes, trials, z)[0]


def path_reliability(hop_samples: Iterable[Optional[Tuple[int, int]]]) -> dict:
    """p^n over a path, as a stated RANGE, honest about unmeasured hops.

    Each measured hop contributes its Wilson interval. Because every factor
    is <= 1, the product of the measured hops' upper bounds is a valid upper
    bound for the WHOLE path even when hops are unmeasured — an unmeasured
    hop can only make things worse. The lower bound exists only when every
    hop was measured; otherwise it is None, never a guess."""
    low, high = 1.0, 1.0
    measured = unmeasured = 0
    for s in hop_samples:
        if not s or s[1] <= 0:
            unmeasured += 1
            continue
        lo, hi = wilson_bounds(s[0], s[1])
        low *= lo
        high *= hi
        measured += 1
    checked = unmeasured == 0
    return {"low": low if checked else None, "high": high,
            "measured_hops": measured, "unmeasured_hops": unmeasured,
            "checked": checked}


# ---- deficiencies ------------------------------------------------------------

@dataclass
class Deficiency:
    kind: str                        # a SEVERITY key
    severity: int
    summary: str                     # plain English, bare fact first
    nodes: List[str] = field(default_factory=list)
    detail: dict = field(default_factory=dict)
    checked: bool = True             # False = "could not check", not "is fine"
    recommendation: str = ""


def _name(graph: AnalysisGraph, nid: str) -> str:
    n = graph.nodes.get(nid)
    return n.name if n and n.name else nid


def _cold_start(graph: AnalysisGraph, occupancy: dict) -> Deficiency:
    """Zero propagation nodes: ONE calm aggregate recommendation, not one
    alarm per node — every node is 'orphaned' for the same single reason."""
    located = [n for n in graph.nodes.values()
               if not n.is_medic and n.lat is not None and n.lon is not None]
    if not located:
        return Deficiency(
            kind="no_propagation", severity=SEVERITY["no_propagation"],
            summary="This mesh has no propagation node - no one's messages "
                    "are stored while they are offline.",
            checked=False,
            detail={"centroid": None},
            recommendation="Add the first propagation node. Could not suggest "
                           "where - no node locations are known.")
    weights = [(n, float(occupancy.get(n.id, 1))) for n in located]
    total = sum(w for _, w in weights)
    lat = sum(n.lat * w for n, w in weights) / total
    lon = sum(n.lon * w for n, w in weights) / total
    return Deficiency(
        kind="no_propagation", severity=SEVERITY["no_propagation"],
        summary="This mesh has no propagation node - no one's messages are "
                "stored while they are offline.",
        nodes=[n.id for n in located],
        detail={"centroid": (lat, lon), "weighted_by_occupancy": True},
        recommendation=(f"Add the first propagation node near ({lat:.5f}, "
                        f"{lon:.5f}) - the people-weighted centre of the "
                        "known nodes."))


def _orphans(graph: AnalysisGraph, dist: Dict[str, int]) -> List[Deficiency]:
    stranded = {i for i, n in graph.nodes.items()
                if i not in dist and not n.is_medic}
    out: List[Deficiency] = []
    seen: Set[str] = set()
    for seed in sorted(stranded):
        if seed in seen:
            continue
        group, stack = set(), [seed]           # one entry per stranded island
        while stack:
            u = stack.pop()
            if u in group:
                continue
            group.add(u)
            stack.extend(v for v in graph.adj.get(u, ())
                         if v in stranded and v not in group)
        seen |= group
        names = ", ".join(sorted(_name(graph, g) for g in group))
        out.append(Deficiency(
            kind="orphan_risk", severity=SEVERITY["orphan_risk"],
            summary=(f"No LoRa path to any propagation node for: {names}. "
                     "Their messages are not stored while they are offline."),
            nodes=sorted(group), detail={"count": len(group)}))
    return out


def _prop_distance(graph: AnalysisGraph, dist: Dict[str, int],
                   parent: Dict[str, Optional[str]],
                   reliability_samples: Optional[dict]) -> List[Deficiency]:
    out: List[Deficiency] = []
    over = sorted(i for i, d in dist.items()
                  if d > MAX_HOPS_TO_PROP and not graph.nodes[i].is_medic)
    at_limit = sorted(i for i, d in dist.items()
                      if d == MAX_HOPS_TO_PROP and not graph.nodes[i].is_medic)
    if over:
        names = ", ".join(_name(graph, i) for i in over)
        out.append(Deficiency(
            kind="prop_distance", severity=SEVERITY["prop_distance"],
            summary=(f"More than {MAX_HOPS_TO_PROP} hops from the nearest "
                     f"propagation node: {names}."),
            nodes=over,
            detail={"limit": MAX_HOPS_TO_PROP,
                    "hops": {i: dist[i] for i in over}}))
    if at_limit:
        # The boundary, pinned: exactly at the limit PASSES — but with zero
        # slack, so it is surfaced as marginal rather than silently fine.
        names = ", ".join(_name(graph, i) for i in at_limit)
        out.append(Deficiency(
            kind="prop_distance", severity=SEVERITY["prop_distance"],
            summary=(f"Exactly at the {MAX_HOPS_TO_PROP}-hop limit - passes, "
                     f"with no slack: {names}."),
            nodes=at_limit,
            detail={"limit": MAX_HOPS_TO_PROP, "at_limit": True,
                    "hops": {i: dist[i] for i in at_limit}}))
    if reliability_samples:
        for nid in sorted(dist):
            node = graph.nodes[nid]
            if dist[nid] == 0 or node.is_medic:
                continue
            hops, cur = [], nid
            while parent.get(cur) is not None:
                hops.append(tuple(sorted((cur, parent[cur]))))
                cur = parent[cur]
            rel = path_reliability(
                [reliability_samples.get(h) for h in hops])
            if rel["high"] < MIN_RELIABILITY:
                out.append(Deficiency(
                    kind="prop_distance",
                    severity=SEVERITY["prop_distance"],
                    summary=(f"{_name(graph, nid)}'s path to propagation "
                             f"delivers at most {rel['high']:.0%} of traffic "
                             f"(measured; the bar is {MIN_RELIABILITY:.0%})."),
                    nodes=[nid],
                    detail={"reliability_high": rel["high"],
                            "reliability_low": rel["low"],
                            "measured_hops": rel["measured_hops"],
                            "unmeasured_hops": rel["unmeasured_hops"],
                            "hops": dist[nid]}))
    return out


def _single_points(graph: AnalysisGraph) -> List[Deficiency]:
    out = []
    for cut in sorted(articulation_points(graph.adj)):
        node = graph.nodes[cut]
        if node.is_medic:
            # The medic is a cut vertex of the PICTURE in every medic-centric
            # scan — an artefact of where the observer stood — and it leaves.
            continue
        out.append(Deficiency(
            kind="single_point", severity=SEVERITY["single_point"],
            summary=(f"{node.name} is a single point of failure - if it goes "
                     "down, part of the mesh is cut off."),
            nodes=[cut],
            recommendation=(f"Add a parallel path around {node.name} - a "
                            "second route that keeps both sides connected "
                            "without it. Keep the node; add to it.")))
    return out


def _marginal_links(graph: AnalysisGraph,
                    reliability_samples: Optional[dict]) -> List[Deficiency]:
    floor = RSSI_FLOOR_DBM + FADE_MARGIN_DB
    out: List[Deficiency] = []
    unmeasured = 0
    for e in graph.placement_edges:
        pair = tuple(sorted((e.a, e.b)))
        sample = (reliability_samples or {}).get(pair)
        by_signal = e.rssi is not None and e.rssi <= floor
        by_success = False
        if sample and sample[1] > 0:
            by_success = wilson_bounds(*sample)[1] < MIN_RELIABILITY
        if by_signal or by_success:
            a, b = _name(graph, e.a), _name(graph, e.b)
            why = (f"heard at {e.rssi} dBm, within {FADE_MARGIN_DB} dB of "
                   f"the {RSSI_FLOOR_DBM} dBm floor" if by_signal else
                   f"delivers under {MIN_RELIABILITY:.0%} (measured)")
            out.append(Deficiency(
                kind="marginal_link", severity=SEVERITY["marginal_link"],
                summary=f"The link {a} - {b} is marginal: {why}.",
                nodes=[e.a, e.b],
                detail={"rssi_dbm": e.rssi, "by_signal": by_signal,
                        "by_success": by_success}))
        elif e.rssi is None and not sample:
            unmeasured += 1
    if unmeasured:
        out.append(Deficiency(
            kind="marginal_link", severity=SEVERITY["marginal_link"],
            summary=(f"Could not check {unmeasured} "
                     f"link{'s' if unmeasured != 1 else ''} for margin - no "
                     "signal or delivery measurements yet."),
            detail={"unmeasured_links": unmeasured},
            checked=False))
    return out


def _coverage_gaps(graph: AnalysisGraph, gap_km: float,
                   occupancy: dict) -> List[Deficiency]:
    """Located pairs with no placement link but close enough that one should
    work (within 2x the range spine — a midpoint relay halves each hop).
    Grid-bucketed so a big mesh stays O(V) with bounded neighbourhoods."""
    located = [n for n in graph.nodes.values()
               if not n.is_medic and n.lat is not None and n.lon is not None]
    cell_deg = max(gap_km / 60.0, 1e-6)      # oversized cells: +-1 covers gap_km
    grid: Dict[Tuple[int, int], List[MeshNode]] = {}
    for n in located:
        grid.setdefault((int(n.lat // cell_deg), int(n.lon // cell_deg)),
                        []).append(n)
    out: List[Deficiency] = []
    seen: Set[Tuple[str, str]] = set()
    for (ci, cj), bucket in grid.items():
        for di in (-1, 0, 1):
            for dj in (-1, 0, 1):
                for m in grid.get((ci + di, cj + dj), ()):
                    for n in bucket:
                        if m.id >= n.id:
                            continue
                        key = (m.id, n.id)
                        if key in seen or n.id in graph.adj.get(m.id, ()):
                            continue
                        km = link_distance_km(m.lat, m.lon, n.lat, n.lon)
                        if km is None or km > gap_km or km <= 0.01:
                            continue
                        seen.add(key)
                        known = m.id in occupancy or n.id in occupancy
                        weight = (occupancy.get(m.id, 1)
                                  + occupancy.get(n.id, 1))
                        out.append(Deficiency(
                            kind="coverage_gap",
                            severity=SEVERITY["coverage_gap"],
                            summary=(f"{m.name} and {n.name} are {km:.1f} km "
                                     "apart with no LoRa link between them."),
                            nodes=[m.id, n.id],
                            detail={"km": round(km, 2),
                                    "midpoint": ((m.lat + n.lat) / 2,
                                                 (m.lon + n.lon) / 2),
                                    "weight": weight,
                                    "occupancy_known": known}))
    out.sort(key=lambda d: (-d.detail["weight"], d.nodes))
    return out


def _saturation(graph: AnalysisGraph,
                traffic: Optional[dict]) -> List[Deficiency]:
    """Only ever speaks from data. No traffic readings -> no findings —
    silence, not "assumed high"."""
    if not traffic:
        return []
    out = []
    for nid in sorted(traffic):
        frac = traffic[nid]
        if frac is None:
            continue
        label, sev = utilisation_label(frac)
        if sev == "ok":
            continue
        out.append(Deficiency(
            kind="saturation", severity=SEVERITY["saturation"],
            summary=(f"{_name(graph, nid)} channel load {frac:.0%}: {label}"),
            nodes=[nid], detail={"channel_load": frac, "level": sev}))
    return out


def analyze(graph: AnalysisGraph, *,
            range_estimate=None,
            reliability_samples: Optional[dict] = None,
            traffic: Optional[dict] = None,
            occupancy: Optional[dict] = None) -> List[Deficiency]:
    """Every deficiency the evidence supports, in the handover's severity
    order. *range_estimate* (monitor.synapse_range.RangeEstimate) sets the
    coverage-gap window; *reliability_samples* maps sorted node-pair tuples to
    (successes, trials); *traffic* maps node id to channel-load fraction;
    *occupancy* maps node id to people served. All optional — missing data
    narrows what is claimed, never what is invented."""
    occupancy = occupancy or {}
    findings: List[Deficiency] = []

    real_nodes = [n for n in graph.nodes.values() if not n.is_medic]
    if real_nodes and not graph.propagation_ids():
        findings.append(_cold_start(graph, occupancy))     # R1: one, calm
        dist, parent = {}, {}
    else:
        dist, parent = hops_to_propagation(graph)
        findings.extend(_orphans(graph, dist))
        findings.extend(_prop_distance(graph, dist, parent,
                                       reliability_samples))

    findings.extend(_single_points(graph))
    findings.extend(_marginal_links(graph, reliability_samples))

    range_km = (range_estimate.range_km if range_estimate is not None
                else DEFAULT_HOP_RANGE_KM)
    findings.extend(_coverage_gaps(graph, 2.0 * range_km, occupancy))
    findings.extend(_saturation(graph, traffic))

    findings.sort(key=lambda d: d.severity)                # stable: keeps
    return findings                                        # per-kind ordering
