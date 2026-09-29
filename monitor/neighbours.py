"""Node-reported neighbours -> map edges the MEDIC could never see itself.

Every edge the medic's own path table yields starts at the medic. Two nodes
that hear each other all day were drawn as two unrelated dots (measured live,
2026-09-29: 13 nodes, 6 edges, all medic->node). The beacon's v4 tail is a
node's own report of who it hears; this module turns those reports into edges,
and is deliberately strict about it, because these lines are the first thing on
the map that the medic did not witness.

TWO BYTES IS A POINTER, NOT AN IDENTITY. A short hash is the first 16 bits of a
destination hash — 65,536 buckets, which WILL collide on a big enough mesh. So
a report is matched only against nodes the registry ALREADY KNOWS, and only
when exactly one of them fits. Zero matches: the node heard somebody we have
never met — nothing to draw. Two matches: ambiguous — nothing to draw, rather
than a line to the wrong node.

FRESHNESS IS THE NODE'S CLAIM PLUS OUR CLOCK. The node said "no older than
age_s" at the moment we heard its beacon (``observed_at``). So the neighbour
was last heard no earlier than ``observed_at - age_s``; if that is older than
MAX_AGE_S the line is not drawn. A node's report also expires with the node:
a beacon we heard yesterday is yesterday's map.

Pure — no Kivy, no RNS — so it is tested directly.
"""

from __future__ import annotations

from typing import Dict, Iterable, List, Optional

from monitor.health_beacon import NEIGHBOUR_HASH_LEN
from monitor.topology import TopoEdge

#: Draw a node-reported link only if the neighbour was heard within this long.
MAX_AGE_S = 2 * 3600.0


def resolve_short_hash(short_hash: int, known: Iterable[str],
                       exclude: Iterable[str] = ()) -> Optional[str]:
    """The ONE known destination hash beginning with these 16 bits, or None.

    ``known`` are full hex hashes; ``exclude`` are hashes that cannot be the
    answer (the reporting node itself — a node is not its own neighbour).
    """
    if short_hash is None:
        return None
    prefix = "%0*x" % (NEIGHBOUR_HASH_LEN * 2, int(short_hash) & 0xFFFF)
    ex = {h.lower() for h in exclude}
    hits = [h for h in known if h and h.lower().startswith(prefix)
            and h.lower() not in ex]
    return hits[0] if len(hits) == 1 else None


def reported_edges(records: Dict[str, object], now: float,
                   max_age_s: float = MAX_AGE_S) -> List[TopoEdge]:
    """Edges from every node's own neighbour report, kind="reported".

    ``records`` is ``registry.nodes`` (dst_hash -> NodeRecord). Each record's
    ``latest_beacon.neighbours`` is read with the time that beacon was
    observed (``record.seen.observed_at``). Duplicates collapse: A reporting
    B and B reporting A is one link, and it keeps the stronger SNR.
    """
    known = [h for h in records.keys() if h]
    out: Dict[tuple, TopoEdge] = {}
    for dst, rec in records.items():
        beacon = getattr(rec, "latest_beacon", None)
        entries = getattr(beacon, "neighbours", None) or []
        if not entries:
            continue
        seen = getattr(rec, "seen", None)
        heard_at = getattr(seen, "observed_at", None)
        if heard_at is None:
            continue                       # a report with no time is no report
        for n in entries:
            age = n.get("age_s")
            if age is None:
                continue                   # the stale bucket: the node itself said don't
            last_heard = heard_at - float(age)
            if now - last_heard > max_age_s:
                continue
            other = resolve_short_hash(n.get("short_hash"), known, exclude=(dst,))
            if other is None:
                continue
            key = tuple(sorted((dst, other)))
            snr = n.get("snr_db")
            prev = out.get(key)
            if prev is not None and (snr is None or
                                     (prev.snr is not None and prev.snr >= snr)):
                continue
            out[key] = TopoEdge(dst, other, rssi=None, transport="lora",
                                kind="reported", snr=snr)
    return list(out.values())
