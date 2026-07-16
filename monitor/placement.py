"""Suggested node placement — backlog feature 6 (engine).

Two sub-modes over the SCAN topology:

* **Fill gaps** — pairs of located nodes with no link but close enough that a
  relay should work: suggest the midpoint, estimate the RSSI a relay there
  would see to each end, and flag anything near logged interference.
* **Extend reach** — when the mesh has no gaps, suggest points just beyond the
  network edge that should still reach at least one existing node.

Honesty is part of the spec: straight-line estimates only — terrain, buildings
and elevation are not modelled. Every suggestion carries the "this is an
estimate, run Triage there first" caution, plus an interference caution when
the ground is known-noisy. Pure maths; the SCAN map just renders it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Optional

from monitor.topology import Topology, gap_pairs, components, MEDIC_ID

ESTIMATE_CAUTION = ("This is an estimate. Run Triage at this location before "
                    "deploying.")

# Simple log-distance path model for 915 MHz suburban air (exponent 2.7),
# anchored at -40 dBm @ 10 m. Deliberately coarse — it ranks candidate spots,
# it does not promise a link.
_PL_ANCHOR_DBM = -40.0
_PL_ANCHOR_M = 10.0
_PL_EXPONENT = 2.7
MARGINAL_DBM = -110       # estimates at/below this get "marginal" wording
EXTEND_STEP_KM = 1.2      # how far past the edge an extension suggestion sits


def estimate_rssi_dbm(distance_km: float) -> int:
    """Estimated received signal at *distance_km* on the coarse path model."""
    d = max(distance_km * 1000.0, _PL_ANCHOR_M)
    return round(_PL_ANCHOR_DBM
                 - 10.0 * _PL_EXPONENT * math.log10(d / _PL_ANCHOR_M))


@dataclass
class Suggestion:
    kind: str                 # "fill_gap" | "extend_reach"
    lat: float
    lon: float
    reason: str               # plain-English headline
    estimates: List[dict] = field(default_factory=list)   # [{node, name, km, est_rssi_dbm}]
    cautions: List[str] = field(default_factory=list)


def _est(node, km: float) -> dict:
    return {"node": node.id, "name": node.name, "km": round(km, 2),
            "est_rssi_dbm": estimate_rssi_dbm(km)}


def suggest_fill_gaps(topo: Topology, interference_log=None,
                      max_km: float = 3.0) -> List[Suggestion]:
    """A relay at the midpoint of each close-but-unlinked located pair."""
    by_id = {n.id: n for n in topo.nodes}
    out: List[Suggestion] = []
    for gap in gap_pairs(topo, max_km=max_km):
        a, b = by_id[gap["a"]], by_id[gap["b"]]
        lat, lon = gap["midpoint"]
        half = gap["km"] / 2.0
        sug = Suggestion(
            kind="fill_gap", lat=lat, lon=lon,
            reason=f"Would bridge {a.name} and {b.name}",
            estimates=[_est(a, half), _est(b, half)],
            cautions=[ESTIMATE_CAUTION])
        if any(e["est_rssi_dbm"] <= MARGINAL_DBM for e in sug.estimates):
            sug.cautions.append(
                "Estimated signal is marginal - Triage there is essential.")
        if interference_log is not None:
            note = interference_log.caution_for(lat, lon)
            if note:
                sug.cautions.append(note)
        out.append(sug)
    return out


def suggest_extend_reach(topo: Topology, interference_log=None,
                         step_km: float = EXTEND_STEP_KM) -> List[Suggestion]:
    """When the located mesh is fully connected, push the coverage boundary:
    for each located edge node, a point *step_km* further out from the mesh's
    centre of mass, estimated back to that node."""
    located = [n for n in topo.nodes
               if n.lat is not None and n.lon is not None and not n.is_medic]
    if len(located) < 1:
        return []
    clat = sum(n.lat for n in located) / len(located)
    clon = sum(n.lon for n in located) / len(located)
    out: List[Suggestion] = []
    for n in located:
        dlat, dlon = n.lat - clat, n.lon - clon
        norm = math.hypot(dlat, dlon)
        if norm == 0:                       # a lone node: extend due north
            dlat, dlon, norm = 1.0, 0.0, 1.0
        km_per_deg = 111.0
        lat = n.lat + (dlat / norm) * (step_km / km_per_deg)
        lon = n.lon + (dlon / norm) * (step_km / (km_per_deg *
                                                  max(0.2, math.cos(math.radians(n.lat)))))
        sug = Suggestion(
            kind="extend_reach", lat=lat, lon=lon,
            reason=f"Would extend mesh coverage about {step_km:g} km past {n.name}",
            estimates=[_est(n, step_km)],
            cautions=[ESTIMATE_CAUTION])
        if interference_log is not None:
            note = interference_log.caution_for(lat, lon)
            if note:
                sug.cautions.append(note)
        out.append(sug)
    return out


def suggest(topo: Topology, interference_log=None) -> List[Suggestion]:
    """The 'Suggest next node' button: fill gaps when there are any, otherwise
    extend the network's reach."""
    gaps = suggest_fill_gaps(topo, interference_log)
    if gaps:
        return gaps
    return suggest_extend_reach(topo, interference_log)
