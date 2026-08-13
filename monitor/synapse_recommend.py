"""SYNAPSE phase 4 — the recommender: what to add, where, and how cheap.

Turns phase 3's deficiency list into ranked, concrete actions. Two premises
carry the whole module:

* **The economic premise.** The default answer is the cheap transport node.
  The expensive tier — a propagation node, a Pi with storage — is recommended
  only on grounds that were actually CHECKED: users otherwise beyond
  MAX_HOPS_TO_PROP of any store-and-forward, a junction of confirmed
  branches, a broken store-peering spine, or measured saturation. Thin data
  never upgrades a tier; a junction manufactured from single one-way or
  fuzzed observations stays cheap and says "could not check". Cheapest of
  all is not buying hardware: a mast raise on an existing node is a
  first-class recommendation that outranks a new node when it resolves the
  same problem.

* **Confidence first, end to end.** Every predicted link names its source —
  "measured n=X" from the phase-2 range spine, "default" from the phase-1
  assumption, "extrapolated" past the farthest measured link — and carries
  the spine's confidence word. Terrain is consulted where tiles are cached
  (monitor.terrain.line_of_sight, the check_terrain fail-open pattern) and
  honestly absent otherwise. A mast raise's height in metres and its
  predicted gain in dB are SEPARATE typed fields, so a 10 and a 10 can never
  be silently confused.

The SCAN screen's "Suggest next node" button (monitor.placement.suggest) is
an adapter over this engine's LoRa-first view — see scan_view /
scan_gap_window_km at the bottom. No UI here (phase 5); pure data + maths,
no third-party imports; the numeric weights are engineering judgement.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Tuple

from monitor.channel_util import utilisation_label
from monitor.synapse_graph import (
    AnalysisGraph,
    Deficiency,
    PLACEMENT_TRANSPORTS,
    TIER_PROPAGATION,
    TIER_TRANSPORT,
    analyze,
    articulation_points,
    build_analysis_graph,
    hops_to_propagation,
)
from monitor.synapse_links import (
    LinkObservation,
    LinkObservationStore,
    MAX_HOPS_TO_PROP,
    link_distance_km,
)
from monitor.synapse_range import RangeEstimate, estimate_range
from monitor.topology import Topology

__all__ = [
    "AUTOPEER_MAXDEPTH", "MAST_RAISE_M", "MAST_RAISE_GAIN_DB",
    "PredictedLink", "AlternativeAction", "Recommendation",
    "predicted_links", "tier_for", "recommend",
    "scan_view", "scan_gap_window_km", "scan_recommendations",
]

#: How many transport hops apart two propagation nodes can sit and still
#: autopeer: LXMF announces carry a hop count and a propagation node peers
#: with others heard within ~this depth (assumptions-verifier finding,
#: 2026-08-13). The store network's PEERING graph is propagation nodes within
#: this many hops of each other — beyond it, stores stop syncing directly.
AUTOPEER_MAXDEPTH = 6

#: Suggested mast raise on an existing node, metres above its current mount.
#: Engineering judgement, not physics.
MAST_RAISE_M = 10.0

#: Predicted signal gain from that raise, dB — roughly the gain of doubling
#: a low mount's height. Engineering judgement, not physics; deliberately a
#: DIFFERENT number from MAST_RAISE_M so metres and decibels can never pass
#: for one another (the G5 confusion).
MAST_RAISE_GAIN_DB = 6.0

#: Antenna height assumed for a candidate site's terrain check — matches
#: monitor.placement.check_terrain's default. Judgement.
CANDIDATE_ANTENNA_M = 3.0

# Scoring weights — engineering judgement, kept small and named so the score
# stays explainable. Ranking is severity-first, cost-second; the score only
# orders candidates within those bands.
_W_RESOLVED = 2.0
_W_LINK = 1.0
_W_MARGIN = 0.2
_W_HOPS = 0.5
_W_REDUNDANCY = 2.0
_COST_PENALTY = {TIER_TRANSPORT: 0.5, TIER_PROPAGATION: 2.0}
#: cost order: raising a mast (0) < a cheap transport node (1) < a Pi (2).
_COST_RANK = {"raise_antenna": 0, TIER_TRANSPORT: 1, TIER_PROPAGATION: 2}

_COST_NOTES = {
    TIER_TRANSPORT: ("Cheap tier - an RTNode-class transport board is "
                     "enough here."),
    TIER_PROPAGATION: ("The expensive tier - a Pi with storage. Recommended "
                       "only because the grounds below were checked."),
}
_RAISE_COST_NOTE = "Cheapest option - no new hardware, just height."


# ---- predicted links ---------------------------------------------------------

@dataclass
class PredictedLink:
    """One link a candidate site is predicted to make — with the prediction's
    pedigree attached, because the number is only as good as its source."""
    node: str
    name: str
    km: float
    margin_km: float                  # spine range minus distance (may be < 0)
    confidence: str                   # the range spine's confidence word
    source: str                       # "measured n=X" | "default" | "extrapolated"
    terrain: Optional[str] = None     # "clear" | "obstructed" | "unknown" | None
    viable: bool = False


def _range_source(est: RangeEstimate) -> str:
    return (f"measured n={est.distinct_links}"
            if est.source == "measured" else "default")


def predicted_links(graph: AnalysisGraph, lat: float, lon: float,
                    est: RangeEstimate, terrain_store=None,
                    antenna_m: float = CANDIDATE_ANTENNA_M
                    ) -> Tuple[List[PredictedLink], List[str]]:
    """Links a node at (lat, lon) is predicted to make, from the range spine.

    Within the spine's range the link carries the spine's own source and
    confidence. Between the range and the (capped, advisory) signal
    projection it is "extrapolated" and never counted viable. Terrain is
    checked where a store is given — fail-open: no store, no terrain claims;
    tiles missing yields "unknown" plus one honest caution."""
    reach = est.range_km
    outer = max(reach, est.projected_reach_km or 0.0)
    links: List[PredictedLink] = []
    cautions: List[str] = []
    for n in graph.nodes.values():
        if n.is_medic or n.lat is None or n.lon is None:
            continue
        km = link_distance_km(lat, lon, n.lat, n.lon)
        if km is None or km <= 0.001 or km > outer:
            continue
        extrapolated = km > reach
        links.append(PredictedLink(
            node=n.id, name=n.name, km=round(km, 2),
            margin_km=round(reach - km, 2),
            confidence="low" if extrapolated else est.confidence,
            source="extrapolated" if extrapolated else _range_source(est),
            viable=not extrapolated))
    if terrain_store is not None and links:
        from monitor.terrain import line_of_sight
        blocked, unknown = [], 0
        for link in links:
            n = graph.nodes[link.node]
            v = line_of_sight(terrain_store, lat, lon, antenna_m,
                              n.lat, n.lon, antenna_m)
            link.terrain = v.status
            if v.status == "obstructed":
                link.viable = False
                blocked.append(link.name)
            elif v.status == "unknown":
                unknown += 1
        if blocked:
            cautions.append("Ground blocks the path to "
                            f"{', '.join(sorted(blocked))} - move or go higher.")
        elif unknown == len(links):
            cautions.append("No terrain map cached for this area - these "
                            "links are distance-only estimates.")
    links.sort(key=lambda l: l.km)
    return links, cautions


# ---- the tier rule -----------------------------------------------------------

def _confirmed_anchor(store: Optional[LinkObservationStore],
                      node_id: str) -> bool:
    """Is this node's link evidence CONFIRMED — repeated, heard in both
    directions, never through a fuzzed pin? A single one-way observation is
    not a branch; a fuzzed position can manufacture a junction that is not
    there. Judgement bar, deliberately conservative."""
    if store is None:
        return False
    obs = [o for o in store.observations
           if node_id in (o.heard_by, o.heard_from)]
    if len(obs) < 2:
        return False
    for o in obs:
        if (o.heard_by == node_id and o.heard_by_position == "fuzzed") or \
                (o.heard_from == node_id and o.heard_from_position == "fuzzed"):
            return False
    return (any(o.heard_by == node_id for o in obs)
            and any(o.heard_from == node_id for o in obs))


def _component_index(graph: AnalysisGraph) -> Dict[str, int]:
    comp: Dict[str, int] = {}
    idx = 0
    for seed in graph.adj:
        if seed in comp:
            continue
        stack = [seed]
        while stack:
            u = stack.pop()
            if u in comp:
                continue
            comp[u] = idx
            stack.extend(v for v in graph.adj.get(u, ()) if v not in comp)
        idx += 1
    return comp


def _peering_adjacency(graph: AnalysisGraph) -> Dict[str, set]:
    """The store network's own graph: propagation nodes within
    AUTOPEER_MAXDEPTH transport hops of each other."""
    props = graph.propagation_ids()
    adj: Dict[str, set] = {p: set() for p in props}
    prop_set = set(props)
    for p in props:
        depth = {p: 0}
        frontier = [p]
        while frontier:
            nxt = []
            for u in frontier:
                if depth[u] >= AUTOPEER_MAXDEPTH:
                    continue
                for v in graph.adj.get(u, ()):
                    if v not in depth:
                        depth[v] = depth[u] + 1
                        nxt.append(v)
            frontier = nxt
        for q in prop_set:
            if q != p and q in depth:
                adj[p].add(q)
                adj[q].add(p)
    return adj


def tier_for(linked_ids: Iterable[str], graph: AnalysisGraph,
             dist: Dict[str, int], *,
             store: Optional[LinkObservationStore] = None,
             traffic: Optional[dict] = None,
             affected_ids: Iterable[str] = (),
             cold_start: bool = False) -> Tuple[str, str, bool]:
    """(tier, why, checked) for a candidate that would link *linked_ids*.

    Propagation only on checked grounds; the default answer is the cheap
    transport node, and thin data never upgrades it — it downgrades the
    wording to "could not check" instead."""
    linked = [l for l in linked_ids if l in graph.nodes]
    affected = list(affected_ids)

    if cold_start:
        return (TIER_PROPAGATION,
                "This mesh has no store-and-forward at all - the first node "
                "must be the propagation node.", True)

    # (a) users otherwise beyond reach of any store-and-forward
    anchors = [dist[l] for l in linked if l in dist]
    if affected:
        new_hops = (min(anchors) + 2) if anchors else math.inf
        if new_hops > MAX_HOPS_TO_PROP:
            return (TIER_PROPAGATION,
                    f"Even with a relay here, these nodes stay more than "
                    f"{MAX_HOPS_TO_PROP} hops from any store-and-forward - "
                    "only a propagation node at this spot serves them.", True)

    # (c) restores a second spine where one propagation node is the only
    #     peering path (LXMF autopeers within ~AUTOPEER_MAXDEPTH hops)
    linked_props = [l for l in linked
                    if graph.nodes[l].tier == TIER_PROPAGATION]
    if len(linked_props) >= 2:
        peering = _peering_adjacency(graph)
        for cut in articulation_points(peering):
            trimmed = {p: (n - {cut}) for p, n in peering.items() if p != cut}
            comp: Dict[str, int] = {}
            idx = 0
            for seed in trimmed:
                if seed in comp:
                    continue
                stack = [seed]
                while stack:
                    u = stack.pop()
                    if u in comp:
                        continue
                    comp[u] = idx
                    stack.extend(v for v in trimmed.get(u, ()) if v not in comp)
                idx += 1
            sides = {comp[p] for p in linked_props if p in comp}
            if len(sides) >= 2:
                name = graph.nodes[cut].name
                return (TIER_PROPAGATION,
                        f"Gives the store network a second peering path "
                        f"around {name} (LXMF autopeers only within "
                        f"~{AUTOPEER_MAXDEPTH} hops).", True)

    # (b) a junction of >= 3 CONFIRMED branches
    comp = _component_index(graph)
    branch_comps = {comp[l] for l in linked if l in comp}
    could_not_confirm = False
    if len(branch_comps) >= 3:
        confirmed = {comp[l] for l in linked
                     if l in comp and _confirmed_anchor(store, l)}
        if len(confirmed) >= 3:
            return (TIER_PROPAGATION,
                    f"A junction of {len(confirmed)} confirmed branches - "
                    "traffic from every side meets here.", True)
        could_not_confirm = True

    # (d) measured saturation ONLY — zero data leaves this branch inert
    if traffic:
        for l in linked:
            frac = traffic.get(l)
            if frac is None:
                continue
            label, sev = utilisation_label(frac)
            if sev != "ok":
                return (TIER_PROPAGATION,
                        f"Measured channel load {frac:.0%} at "
                        f"{graph.nodes[l].name} ({label}) - local storage "
                        "takes sync traffic off the air.", True)

    if could_not_confirm:
        return (TIER_TRANSPORT,
                "Cheap transport tier. A junction is suggested here, but by "
                "single one-way or fuzzed observations - could not check it, "
                "and thin data never buys the expensive tier.", False)
    return (TIER_TRANSPORT,
            "The cheap transport tier serves this - nothing checked here "
            "needs storage.", True)


# ---- recommendations ---------------------------------------------------------

@dataclass
class AlternativeAction:
    """A cheaper action that resolves the same deficiency. Height and gain
    are separate typed fields on purpose: 10 m is not 10 dB."""
    node: str
    height_m: float
    predicted_gain_db: float
    summary: str


@dataclass
class Recommendation:
    action: str                       # "new_node" | "raise_antenna"
    summary: str = ""
    lat: Optional[float] = None      # new_node: where
    lon: Optional[float] = None
    node: Optional[str] = None       # raise_antenna: which existing node
    height_m: Optional[float] = None          # metres of mast — raise only
    predicted_gain_db: Optional[float] = None  # decibels of gain — raise only
    tier: Optional[str] = None       # new_node only
    tier_why: str = ""
    tier_checked: bool = True
    resolves: List[str] = field(default_factory=list)   # deficiency summaries
    resolved_points: float = 0.0     # sum of (7 - severity) over resolves
    predicted_links: List[PredictedLink] = field(default_factory=list)
    cost_note: str = ""
    cost_rank: int = 1
    score: float = 0.0
    score_parts: dict = field(default_factory=dict)
    alternative_actions: List[AlternativeAction] = field(default_factory=list)
    cautions: List[str] = field(default_factory=list)
    affected: List[str] = field(default_factory=list)

    def detail_affected(self) -> List[str]:
        return list(self.affected)


def _points(defs: Iterable[Deficiency]) -> float:
    return float(sum(7 - d.severity for d in defs))


def _located(graph: AnalysisGraph, ids: Iterable[str]) -> List:
    out = []
    for i in ids:
        n = graph.nodes.get(i)
        if n is not None and not n.is_medic \
                and n.lat is not None and n.lon is not None:
            out.append(n)
    return out


def _midpoint(a, b) -> Tuple[float, float]:
    return ((a.lat + b.lat) / 2.0, (a.lon + b.lon) / 2.0)


def _candidate_positions(graph: AnalysisGraph, defs: List[Deficiency],
                         dist: Dict[str, int], est: RangeEstimate,
                         occupancy: dict) -> List[dict]:
    """One raw candidate site per resolvable deficiency. Positions come only
    from located nodes — a candidate nobody could walk to is not advice."""
    window = 2.0 * est.range_km
    anchors = _located(graph, (i for i in dist))       # located, connected
    out: List[dict] = []

    def add(lat, lon, d, affected, redundancy=0):
        out.append({"lat": lat, "lon": lon, "defs": [d],
                    "affected": list(affected), "redundancy": redundancy})

    for d in defs:
        if d.kind == "orphan_risk":
            best = None
            for m in _located(graph, d.nodes):
                for a in anchors:
                    km = link_distance_km(m.lat, m.lon, a.lat, a.lon)
                    if km is not None and km <= window \
                            and (best is None or km < best[0]):
                        best = (km, m, a)
            if best:
                lat, lon = _midpoint(best[1], best[2])
                add(lat, lon, d, d.nodes)
        elif d.kind == "prop_distance" and not d.detail.get("at_limit") \
                and "reliability_high" not in d.detail:
            far = _located(graph, d.nodes)
            if far:
                w = [(n, float(occupancy.get(n.id, 1))) for n in far]
                total = sum(x for _, x in w)
                add(sum(n.lat * x for n, x in w) / total,
                    sum(n.lon * x for n, x in w) / total, d, d.nodes)
        elif d.kind == "single_point":
            cut = d.nodes[0]
            sides = _located(graph, graph.adj.get(cut, ()))
            if len(sides) >= 2:
                lat, lon = _midpoint(sides[0], sides[1])
                add(lat, lon, d, [s.id for s in sides[:2]], redundancy=1)
        elif d.kind == "marginal_link" and d.checked and len(d.nodes) == 2:
            ends = _located(graph, d.nodes)
            if len(ends) == 2:
                lat, lon = _midpoint(*ends)
                add(lat, lon, d, d.nodes)
        elif d.kind == "coverage_gap":
            mid = d.detail.get("midpoint")
            if mid:
                add(mid[0], mid[1], d, d.nodes)
        elif d.kind == "saturation":
            loaded = _located(graph, d.nodes)
            if loaded:
                add(loaded[0].lat, loaded[0].lon, d, d.nodes)

    # merge candidates that land on the same spot: one place, all its reasons
    merged: Dict[Tuple[float, float], dict] = {}
    for c in out:
        key = (round(c["lat"], 4), round(c["lon"], 4))
        if key in merged:
            merged[key]["defs"].extend(c["defs"])
            merged[key]["affected"].extend(
                a for a in c["affected"]
                if a not in merged[key]["affected"])
            merged[key]["redundancy"] = max(merged[key]["redundancy"],
                                            c["redundancy"])
        else:
            merged[key] = c
    return list(merged.values())


def _score(resolved_points, links, hop_reduction, redundancy, tier) -> dict:
    parts = {
        "resolved_points": resolved_points,
        "viable_links": sum(1 for l in links if l.viable),
        "margin_total": round(sum(max(0.0, l.margin_km)
                                  for l in links if l.viable), 2),
        "hop_reduction": hop_reduction,
        "redundancy_gain": redundancy,
        "cost_penalty": _COST_PENALTY.get(tier, 0.0),
    }
    parts["score"] = round(
        _W_RESOLVED * parts["resolved_points"]
        + _W_LINK * parts["viable_links"]
        + _W_MARGIN * parts["margin_total"]
        + _W_HOPS * parts["hop_reduction"]
        + _W_REDUNDANCY * parts["redundancy_gain"]
        - parts["cost_penalty"], 3)
    return parts


def recommend(graph: AnalysisGraph, *,
              deficiencies: Optional[List[Deficiency]] = None,
              store: Optional[LinkObservationStore] = None,
              range_estimate: Optional[RangeEstimate] = None,
              terrain_store=None,
              traffic: Optional[dict] = None,
              occupancy: Optional[dict] = None,
              reliability_samples: Optional[dict] = None
              ) -> List[Recommendation]:
    """Ranked actions for this mesh: severity first, then cost, then score.

    A zero-propagation mesh gets exactly ONE recommendation (the first
    propagation node at the people-weighted centroid — terrain-checked where
    tiles are cached), not a fix per symptom: every symptom has the same
    cause."""
    occupancy = occupancy or {}
    est = range_estimate or estimate_range(store or LinkObservationStore())
    defs = deficiencies if deficiencies is not None else analyze(
        graph, range_estimate=est, reliability_samples=reliability_samples,
        traffic=traffic, occupancy=occupancy)
    dist, _parent = hops_to_propagation(graph)
    recs: List[Recommendation] = []

    cold = [d for d in defs if d.kind == "no_propagation"]
    if cold:
        d = cold[0]
        centroid = d.detail.get("centroid")
        if centroid is None:
            return []                      # analysis already said why, honestly
        lat, lon = centroid
        links, cautions = predicted_links(graph, lat, lon, est, terrain_store)
        tier, why, checked = tier_for([l.node for l in links], graph, dist,
                                      store=store, traffic=traffic,
                                      cold_start=True)
        parts = _score(_points([d]), links, 0, 0, tier)
        return [Recommendation(
            action="new_node", summary=d.recommendation or d.summary,
            lat=lat, lon=lon, tier=tier, tier_why=why, tier_checked=checked,
            resolves=[d.summary], resolved_points=parts["resolved_points"],
            predicted_links=links, cost_note=_COST_NOTES[tier],
            cost_rank=_COST_RANK[tier], score=parts["score"],
            score_parts=parts, cautions=cautions, affected=list(d.nodes))]

    # mast raises first-class: the cheapest fix for a measured marginal link
    raises_by_pair: Dict[Tuple[str, str], List[AlternativeAction]] = {}
    for d in defs:
        if d.kind != "marginal_link" or not d.checked or len(d.nodes) != 2:
            continue
        pair = tuple(sorted(d.nodes))
        a, b = pair
        alts = []
        for node, other in ((a, b), (b, a)):
            # KIN AND KINDRED ONLY (operator, 2026-08-13): a neighbour's
            # position is fuzzed by design — where their antenna stands is
            # not the medic's to know, so it is not the medic's to advise.
            if getattr(graph.nodes[node], "provenance",
                       "unknown") not in ("kin", "kindred"):
                continue
            name = graph.nodes[node].name
            alts.append(AlternativeAction(
                node=node, height_m=MAST_RAISE_M,
                predicted_gain_db=MAST_RAISE_GAIN_DB,
                summary=(f"Raise the antenna at {name} by "
                         f"{MAST_RAISE_M:g} m - predicted gain about "
                         f"{MAST_RAISE_GAIN_DB:g} dB on the link to "
                         f"{graph.nodes[other].name}.")))
        if not alts:
            continue          # two strangers: the relay fix stands alone
        raises_by_pair[pair] = alts
        first = alts[0]
        parts = _score(_points([d]), [], 0, 0, None)
        recs.append(Recommendation(
            action="raise_antenna", summary=first.summary, node=first.node,
            height_m=first.height_m,
            predicted_gain_db=first.predicted_gain_db,
            resolves=[d.summary], resolved_points=parts["resolved_points"],
            cost_note=_RAISE_COST_NOTE, cost_rank=_COST_RANK["raise_antenna"],
            score=parts["score"], score_parts=parts,
            affected=list(d.nodes)))

    for c in _candidate_positions(graph, defs, dist, est, occupancy):
        links, cautions = predicted_links(graph, c["lat"], c["lon"], est,
                                          terrain_store)
        linked_ids = [l.node for l in links if l.viable] \
            or [l.node for l in links]
        tier, why, checked = tier_for(linked_ids, graph, dist, store=store,
                                      traffic=traffic,
                                      affected_ids=c["affected"])
        anchors = [dist[l] for l in linked_ids if l in dist]
        affected_dists = [dist.get(a, MAX_HOPS_TO_PROP + 2)
                          for a in c["affected"]]
        hop_reduction = 0
        if anchors and affected_dists:
            hop_reduction = max(0, max(affected_dists)
                                - (min(anchors) + 2))
        parts = _score(_points(c["defs"]), links, hop_reduction,
                       c["redundancy"], tier)
        alts: List[AlternativeAction] = []
        for d in c["defs"]:
            if d.kind == "marginal_link":
                alts.extend(raises_by_pair.get(tuple(sorted(d.nodes)), []))
        recs.append(Recommendation(
            action="new_node",
            summary=f"Add a {tier} node at ({c['lat']:.5f}, {c['lon']:.5f}).",
            lat=c["lat"], lon=c["lon"], tier=tier, tier_why=why,
            tier_checked=checked,
            resolves=[d.summary for d in c["defs"]],
            resolved_points=parts["resolved_points"],
            predicted_links=links, cost_note=_COST_NOTES[tier],
            cost_rank=_COST_RANK[tier], score=parts["score"],
            score_parts=parts, alternative_actions=alts,
            cautions=cautions, affected=c["affected"]))

    recs.sort(key=lambda r: (-r.resolved_points, r.cost_rank, -r.score,
                             r.summary))
    return recs


# ---- the SCAN adapter (one range spine, LoRa-first) --------------------------

def scan_view(topo: Topology) -> Topology:
    """*topo* with only placement-signal edges (lora/unknown/local). This is
    what kills gap_pairs' transport-blindness: a wifi edge means "same
    building", not "no relay needed", so it must not hide a LoRa gap."""
    return Topology(
        nodes=topo.nodes,
        edges=[e for e in topo.edges if e.transport in PLACEMENT_TRANSPORTS],
        generated_at=topo.generated_at)


def scan_gap_window_km(view: Topology) -> float:
    """The fill-gap qualifying distance for SCAN, from the ONE range spine:
    2x the phase-2 estimate (a midpoint relay halves each hop), fed with the
    view's own located links. Thin data -> the labelled 5 km default, so a
    brand-new mesh starts from the operator's assumption instead of a second
    hard-coded constant."""
    by_id = {n.id: n for n in view.nodes}
    store = LinkObservationStore()
    for e in view.edges:
        a, b = by_id.get(e.a), by_id.get(e.b)
        if not a or not b or None in (a.lat, a.lon, b.lat, b.lon):
            continue
        km = link_distance_km(a.lat, a.lon, b.lat, b.lon)
        if km is not None and km > 0.01:
            store.add(LinkObservation(
                heard_by=e.a, heard_from=e.b,
                observed_at=view.generated_at, distance_km=km,
                source="topology"))
    return 2.0 * estimate_range(store).range_km


def scan_recommendations(topo: Topology, registry=None,
                         roster: Optional[dict] = None,
                         **engine_kwargs) -> List[Recommendation]:
    """SCAN's live feed: the full recommender, run on the LoRa-first view —
    the same spine the suggest() adapter stands on, so the map pins and the
    Build next panel can never disagree. *roster* defaults to the registry's
    own kin roster (the operator's record of their fleet); *engine_kwargs*
    pass straight through to :func:`recommend` (store, terrain_store,
    traffic, occupancy...)."""
    if roster is None and registry is not None:
        roster = getattr(registry, "kin_roster", None)
    graph = build_analysis_graph(scan_view(topo), registry=registry,
                                 roster=roster)
    return recommend(graph, **engine_kwargs)
