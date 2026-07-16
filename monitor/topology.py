"""Mesh topology model — the SCAN graph-view core (backlog feature 4).

Builds a who-can-hear-whom graph from data the tool already collects:

* the registry — every node the medic itself has heard (beacon / HTTP / mesh),
  with the signal strength it heard them at;
* the mesh path table (``rnpath -t --json``) — entries reached *via* another
  node reveal node-to-node links the medic can't hear directly: a path to Y via
  X means X↔Y is a working link.

Honesty note: this is the mesh as seen FROM the medic plus what the path table
implies — links between two distant nodes that never relay for anyone are
invisible until one of them appears in a path. Nodes with no line between them
are the gaps.

Pure data + maths (including the deterministic ring layout for the graph view);
the Kivy widget just draws what this returns.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

MEDIC_ID = "medic"          # the tool itself is a node on the graph


@dataclass
class TopoNode:
    id: str
    name: str
    status: str = "unknown"          # ok | warn | alert | unknown (theme colours)
    lat: Optional[float] = None
    lon: Optional[float] = None
    is_medic: bool = False


@dataclass
class TopoEdge:
    a: str
    b: str
    rssi: Optional[int] = None       # dBm as heard (None for path-implied links)
    kind: str = "direct"             # "direct" (medic heard) | "relayed" (path-implied)

    def key(self) -> Tuple[str, str]:
        return tuple(sorted((self.a, self.b)))


@dataclass
class Topology:
    nodes: List[TopoNode] = field(default_factory=list)
    edges: List[TopoEdge] = field(default_factory=list)
    generated_at: float = 0.0        # "last updated" for the display

    def node_ids(self) -> List[str]:
        return [n.id for n in self.nodes]

    def degree(self, node_id: str) -> int:
        return sum(1 for e in self.edges if node_id in (e.a, e.b))

    def neighbours(self, node_id: str) -> List[TopoEdge]:
        return [e for e in self.edges if node_id in (e.a, e.b)]


def build_topology(registry, paths: List[dict], now: float) -> Topology:
    """Assemble the graph from the registry + a parsed ``rnpath -t --json``
    table (``[{hash, via, hops, ...}]``)."""
    topo = Topology(generated_at=now)
    topo.nodes.append(TopoNode(id=MEDIC_ID, name="Node Medic", status="ok",
                               is_medic=True))
    seen_edges: Dict[Tuple[str, str], TopoEdge] = {}

    def add_edge(e: TopoEdge) -> None:
        k = e.key()
        existing = seen_edges.get(k)
        if existing is None:
            seen_edges[k] = e
        elif existing.rssi is None and e.rssi is not None:
            seen_edges[k] = e            # a measured edge beats an implied one

    known = set()
    for dst, rec in registry.nodes.items():
        known.add(dst)
        topo.nodes.append(TopoNode(
            id=dst, name=rec.name or dst[:8], status=rec.status(now),
            lat=rec.lat, lon=rec.lon))
        rssi = rec.signal_dbm()
        if rssi is not None or rec.mesh_hops == 1:
            add_edge(TopoEdge(MEDIC_ID, dst, rssi=rssi, kind="direct"))

    for p in paths or []:
        dst, via = p.get("hash"), p.get("via")
        hops = p.get("hops")
        if not dst:
            continue
        for h in (dst, via):
            if h and h not in known and h != MEDIC_ID:
                known.add(h)
                topo.nodes.append(TopoNode(id=h, name=h[:8], status="unknown"))
        if hops == 1:
            add_edge(TopoEdge(MEDIC_ID, dst, kind="direct"))
        elif via:                        # reached via X -> the X<->dst link exists
            add_edge(TopoEdge(via, dst, kind="relayed"))

    topo.edges = list(seen_edges.values())
    return topo


# ---- analysis ----------------------------------------------------------------

def components(topo: Topology) -> List[set]:
    """Connected components — more than one means the mesh is split."""
    adj: Dict[str, set] = {n.id: set() for n in topo.nodes}
    for e in topo.edges:
        adj.setdefault(e.a, set()).add(e.b)
        adj.setdefault(e.b, set()).add(e.a)
    remaining = set(adj)
    out = []
    while remaining:
        seed = next(iter(remaining))
        comp, stack = set(), [seed]
        while stack:
            n = stack.pop()
            if n in comp:
                continue
            comp.add(n)
            stack.extend(adj.get(n, ()) - comp)
        out.append(comp)
        remaining -= comp
    return out


def _haversine_km(lat1, lon1, lat2, lon2) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def gap_pairs(topo: Topology, max_km: float = 3.0) -> List[dict]:
    """Located node pairs with NO line between them but close enough that a
    relay should work — the raw material for 'suggest next node'. Each gap:
    {a, b, km, midpoint(lat, lon)}."""
    edge_keys = {e.key() for e in topo.edges}
    located = [n for n in topo.nodes if n.lat is not None and n.lon is not None]
    comp_of = {}
    for i, comp in enumerate(components(topo)):
        for nid in comp:
            comp_of[nid] = i
    gaps = []
    for i, a in enumerate(located):
        for b in located[i + 1:]:
            if tuple(sorted((a.id, b.id))) in edge_keys:
                continue
            km = _haversine_km(a.lat, a.lon, b.lat, b.lon)
            if km <= max_km:
                gaps.append({
                    "a": a.id, "b": b.id, "km": round(km, 2),
                    "split": comp_of.get(a.id) != comp_of.get(b.id),
                    "midpoint": ((a.lat + b.lat) / 2, (a.lon + b.lon) / 2),
                })
    gaps.sort(key=lambda g: (not g["split"], g["km"]))
    return gaps


def edge_width(rssi: Optional[int]) -> float:
    """Line weight for the graph view: stronger heard signal = thicker line.
    Path-implied edges (no RSSI) draw at the minimum weight."""
    if rssi is None:
        return 1.0
    n = max(0.0, min(1.0, (rssi + 120) / 50.0))     # -120..-70 dBm -> 0..1
    return 1.0 + 3.0 * n


# ---- deterministic layout for the graph view ---------------------------------

def ring_layout(topo: Topology, width: float, height: float,
                margin_frac: float = 0.12) -> Dict[str, Tuple[float, float]]:
    """Positions for the abstract (non-geographic) graph view: the best-connected
    node sits at the centre, everything else on a ring ordered by connection
    density. Deterministic — same topology, same picture."""
    if not topo.nodes:
        return {}
    cx, cy = width / 2.0, height / 2.0
    r = (min(width, height) / 2.0) * (1.0 - margin_frac)
    order = sorted(topo.nodes, key=lambda n: (-topo.degree(n.id), n.id))
    pos = {order[0].id: (cx, cy)}
    ring = order[1:]
    for i, n in enumerate(ring):
        a = 2 * math.pi * i / max(1, len(ring)) - math.pi / 2
        pos[n.id] = (cx + r * math.cos(a), cy + r * math.sin(a))
    return pos
