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
    #: True when lat/lon came from the node's OWN live GPS (a v3 beacon) —
    #: the node placed itself; False = the birth-certificate stamp.
    self_located: bool = False


def transport_of(interface_name) -> str:
    """The transport an rnpath interface name implies — the one honest
    source of edge type. The SCAN screen used to draw the node's WI-FI RSSI
    as the strength of every direct edge (operator, 2026-08-13: 'bad news
    for placing nodes'); typing edges is what makes LoRa the standard view
    and everything else an overlay."""
    n = (interface_name or "")
    if n.startswith("RNodeInterface"):
        return "lora"
    if n.startswith("AutoInterface"):
        return "wifi"
    if n.startswith(("TCPClientInterface", "TCPServerInterface",
                     "I2PInterface")):
        return "internet"
    if n.startswith(("LocalServerInterface", "LocalClientInterface")):
        return "local"
    return "unknown"


@dataclass
class TopoEdge:
    a: str
    b: str
    rssi: Optional[int] = None       # dBm as heard (None for path-implied links)
    #: "lora" | "wifi" | "internet" | "bluetooth" | "local" | "unknown".
    #: The rssi above may only ever be a measurement OF this transport —
    #: a Wi-Fi number on a LoRa edge is assumption dressed as data.
    transport: str = "unknown"
    #: "direct" (medic heard) | "relayed" (path-implied) | "reported" (a NODE
    #: said it hears the other end — the one kind the medic did not witness;
    #: see monitor.neighbours).
    kind: str = "direct"
    snr: Optional[int] = None        # dB, only on reported edges (the node's own figure)

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


#: Liveliest-wins order when folding a device's aspects into one node.
_STATUS_RANK = {"ok": 3, "warn": 2, "alert": 1, "unknown": 0}


def _rank(status) -> int:
    return _STATUS_RANK.get(str(status), 0)


def build_topology(registry, paths: List[dict], now: float,
                   exclude=None) -> Topology:
    """Assemble the graph from the registry + a parsed ``rnpath -t --json``
    table (``[{hash, via, hops, ...}]``).

    *exclude* — hashes of DELETED nodes. RNS's path table outlives a node by
    seven days (the project's oldest scar: a cached path is not a sighting),
    so without this a node wiped in VITALS rose again on the map as an
    anonymous ghost built from stale rnpath rows (operator, 2026-08-13).
    Exclusion suppresses path-table memory ONLY: a hash the registry has
    heard anew is alive and returns regardless.
    """
    exclude = exclude or set()
    topo = Topology(generated_at=now)
    topo.nodes.append(TopoNode(id=MEDIC_ID, name="Node Medic", status="ok",
                               is_medic=True))
    seen_edges: Dict[Tuple[str, str], TopoEdge] = {}

    def add_edge(e: TopoEdge) -> None:
        # ONE PAIR MAY CARRY SEVERAL TRANSPORTS, and each is its own edge —
        # a wifi measurement must never evict the mesh link on the same pair
        # (phase-3 finding, 2026-08-13: the preference version dropped a
        # path-implied link out of placement adjacency because the pair also
        # had a wifi reading). Within one transport, a measured edge still
        # beats an implied one. One refinement: an "unknown" edge and a LORA
        # edge on the same pair are one fact at two certainty levels, so the
        # typed one absorbs it.
        k = e.key() + (e.transport,)
        ku = e.key() + ("unknown",)
        if e.transport == "lora" and ku in seen_edges:
            seen_edges.pop(ku)
        if e.transport == "unknown" and (e.key() + ("lora",)) in seen_edges:
            return
        existing = seen_edges.get(k)
        if existing is None:
            seen_edges[k] = e
        elif existing.rssi is None and e.rssi is not None:
            seen_edges[k] = e

    # ONE DEVICE, ONE NODE. A machine announces many destinations — a Pi's
    # transport, its LXMF propagation aspect, anything minted after the
    # paperwork — and each arrived here as its own row. ELSEWHERE drew FIVE
    # dots at one spot, an RTNode two, and every link landed on whichever
    # aspect the path table happened to name (operator, 2026-09-09: "we can't
    # have one node during five duplicate registries ... otherwise the map's
    # gonna get really messy").
    #
    # Nothing needs inferring: the roster already records which physical device
    # each entry belongs to, and set_kin_roster stamps it on the record as
    # device_id. This folds on that and NOTHING weaker — never on name, which
    # would merge two nodes a keeper happened to call the same thing.
    # TWO KINDS OF DUPLICATE, and they need different keys.
    #
    #  1. One IDENTITY minting several destinations. Verified 2026-09-09
    #     (stand-in prefixes): aa11aa11 and bb22bb22 both carry identity
    #     cc33cc33 (one RTNode), and dd44dd44/ee55ee55 both carry ff66ff66
    #     (that board before its rebirth). device_id does not catch these,
    #     because device_id only exists for nodes the kin roster names — a
    #     stranger's aspects, or our own before the paperwork lands, have none.
    #
    #  2. Several IDENTITIES on one machine. ELSEWHERE is three, deliberately:
    #     registry.py:437-441 keeps the health reporter's identity apart from
    #     rnsd's so "no amount of listening will ever link them". Only the
    #     roster knows they are one box, so only device_id can fold those.
    #
    # So: device first (it spans identities), identity second (it needs no
    # roster), destination last. Never name — two nodes a keeper happened to
    # call the same thing are still two nodes.
    fold = {}
    for dst, rec in registry.nodes.items():
        fold[dst] = (getattr(rec, "device_id", None)
                     or getattr(rec, "identity_hash", None)
                     or dst)
    # An identity is itself announced as a destination, so a record may key on
    # a hash that is ANOTHER record's fold target. Chase one level so both land
    # on the same node rather than forming a two-link chain (one record's
    # identity_hash IS another record's destination hash — seen live).
    for dst, key in list(fold.items()):
        target = fold.get(key)
        if target and target != key:
            fold[dst] = target

    def _f(h):
        """The node id a hash belongs to. Unknown hashes stand for themselves."""
        return fold.get(h, h)

    known = set()
    merged = {}
    for dst, rec in registry.nodes.items():
        known.add(dst)
        # WHERE THE NODE STANDS: its own live GPS claim (v3 beacon,
        # 2026-08-27) outranks the birth-certificate stamp — the node was
        # THERE when it last spoke, and the stamp only says where it was
        # born. Falls back to the stamp when the node has never self-located.
        lat, lon, self_located = rec.lat, rec.lon, False
        b = getattr(rec, "latest_beacon", None)
        if b is not None and getattr(b, "has_position", False):
            lat, lon, self_located = b.lat, b.lng, True
        key = _f(dst)
        prev = merged.get(key)
        if prev is None:
            merged[key] = TopoNode(
                id=key, name=rec.name or key[:8], status=rec.status(now),
                lat=lat, lon=lon, self_located=self_located)
            topo.nodes.append(merged[key])
        else:
            # Fold the aspects into the row already standing. Coordinates and
            # a real name come from whichever aspect HAS them — one silent
            # sibling must not blank a node that is located and named.
            if prev.lat is None and lat is not None:
                prev.lat, prev.lon, prev.self_located = lat, lon, self_located
            elif lat is not None and self_located and not prev.self_located:
                prev.lat, prev.lon, prev.self_located = lat, lon, True
            if rec.name and (not prev.name or prev.name == key[:8]):
                prev.name = rec.name
            # The device is as alive as its liveliest aspect: a node heard on
            # one destination is not down because another has gone quiet.
            if _rank(rec.status(now)) > _rank(prev.status):
                prev.status = rec.status(now)
        rssi = rec.signal_dbm()
        if rssi is not None or rec.mesh_hops == 1:
            # signal_dbm() is the node's WI-FI RSSI — so the edge it evidences
            # is a wifi edge, and the number stays with its own transport.
            add_edge(TopoEdge(MEDIC_ID, _f(dst), rssi=rssi, kind="direct",
                              transport="wifi" if rssi is not None
                              else "unknown"))
        # THE NODE'S OWN EAR ON THE MESH: a v2+ beacon carries the LoRa
        # RSSI of the last packet the node itself heard — on this hub
        # topology that is its side of the medic link, and it is a LORA
        # number on a LORA edge (never the WiFi figure, the 2026-08-13
        # lesson). It gives the mesh line its honest thickness.
        if b is not None and getattr(b, "lora_rssi_dbm", None) is not None:
            add_edge(TopoEdge(MEDIC_ID, _f(dst), rssi=b.lora_rssi_dbm,
                              kind="direct", transport="lora"))

    def _ghost(h):
        return h in exclude and h not in registry.nodes

    for p in paths or []:
        dst, via = p.get("hash"), p.get("via")
        hops = p.get("hops")
        if not dst:
            continue
        if _ghost(dst) or (via and _ghost(via)):
            continue                     # remembered by the path table only
        for h in (dst, via):
            k = _f(h) if h else h
            if k and k not in known and k not in merged and k != MEDIC_ID:
                known.add(k)
                topo.nodes.append(TopoNode(id=k, name=k[:8], status="unknown"))
        if hops == 1:
            # The path row's interface names how the MEDIC reaches dst —
            # honest transport for this one edge.
            add_edge(TopoEdge(MEDIC_ID, _f(dst), kind="direct",
                              transport=transport_of(p.get("interface"))))
        elif via:                        # reached via X -> the X<->dst link exists
            # The far segment's transport is NOT observable from here; the
            # interface field only names the medic's own first hop.
            # BOTH ends folded: a path reached via one of a node's aspects
            # is a link to the NODE. This is the "paths need to act the same"
            # half — without it the line hangs off a duplicate dot.
            if _f(via) != _f(dst):
                add_edge(TopoEdge(_f(via), _f(dst), kind="relayed",
                                  transport="unknown"))

    # WHAT THE NODES SAY THEY HEAR (2026-09-29). Every edge above starts at
    # the medic; these are the node-to-node links, from each node's own v4
    # beacon report, matched strictly and folded the same way. add_edge's
    # rule holds: a measured edge on the same pair still wins, and a reported
    # one never evicts a witnessed one.
    try:
        from monitor.neighbours import reported_edges
        for e in reported_edges(getattr(registry, "nodes", {}) or {}, now):
            if _f(e.a) == _f(e.b):
                continue                 # two aspects of one device: not a link
            add_edge(TopoEdge(_f(e.a), _f(e.b), rssi=None, transport="lora",
                              kind="reported", snr=e.snr))
    except Exception:                                              # noqa: BLE001
        pass                             # a bad report must never cost the map
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
