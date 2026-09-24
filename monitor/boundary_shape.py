"""The boundary ring — turning a walk's LOSSES into a node's edge on the map.

Operator, 2026-09-24, on the glass: "Using the data from the boundary walk,
after a boundary walk is finished on all sides of a node, it would be nice
to be able to have a thin line around a node that dictates its boundary
edge on the map. Switchable." And, correcting the colour a moment later:
"not necessarily green, whatever colour the node is, the line around it
should be the same colour as the node" — so this module hands the SCAN
screen geometry only; colour is read off the node's own live status
(ui.theme.status_color) at the draw call, not decided here.

Only a LOSS can place this ring. monitor.synapse_range.LinkFailure carries
a position (distance_km, observed_at, lat, lon, snr_db, note, node_key);
monitor.synapse_links.LinkObservation (a HIT) carries distance_km but
never a position — the medic knows a link worked at some range, never
*where* the walker was standing when it did. A ring drawn from hits would
be drawing a shape nothing on the walk actually stood at. This module
therefore never touches LinkObservation.

Honesty rule (project law — "no defaults printed as facts"): a compass
sector with no failure in it carries ``radius_km: None`` forever, never
interpolated and never defaulted from a neighbour. A partial walk draws a
partial ring; the gap in the ring IS the fact — a boundary walked on three
sides and not a fourth has no business drawing a line on the fourth.

Pure geometry + arithmetic; no third-party imports, no Kivy.
"""

from __future__ import annotations

import math
from typing import List, Optional, Tuple

from monitor.geo import valid_position
from monitor.movement import haversine_m
from monitor.synapse_range import LinkFailure

__all__ = ["DEFAULT_SECTORS", "bearing_deg", "destination_point",
          "node_boundary", "boundary_segments"]

#: Sectors per ring, 360 / 24 = 15 degrees each. Engineering judgement
#: (operator design, 2026-09-24), not physics: fine enough that a real
#: boundary's shape — a hillside cutting one side short, a valley letting
#: another run long — actually shows up, coarse enough that one walk's
#: dozen-odd loss samples can light up several sectors instead of scattering
#: one-per-sector across 360 mostly-empty slots.
DEFAULT_SECTORS = 24

_EARTH_R_KM = 6371.0        # monitor.boundary_walk._km's radius — one earth


def bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Initial compass bearing (0..360, 0 = north, clockwise) from point 1 to
    point 2 — the standard great-circle forward-azimuth formula. No name for
    this already existed in monitor.movement (checked: only haversine_m
    lives there), so it lives here beside the module that needs it."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dlon = math.radians(lon2 - lon1)
    x = math.sin(dlon) * math.cos(phi2)
    y = (math.cos(phi1) * math.sin(phi2)
         - math.sin(phi1) * math.cos(phi2) * math.cos(dlon))
    return math.degrees(math.atan2(x, y)) % 360.0


def destination_point(lat: float, lon: float, bearing: float,
                      distance_km: float) -> Tuple[float, float]:
    """The point *distance_km* out from (*lat*, *lon*) on initial compass
    *bearing* — the direct/inverse counterpart of :func:`bearing_deg`,
    standard great-circle formula, sphere of radius _EARTH_R_KM (the same
    radius monitor.boundary_walk._km already measures a walk's samples
    with — one earth, one number, not two that could disagree)."""
    delta = distance_km / _EARTH_R_KM
    theta = math.radians(bearing)
    phi1 = math.radians(lat)
    lam1 = math.radians(lon)
    phi2 = math.asin(math.sin(phi1) * math.cos(delta)
                     + math.cos(phi1) * math.sin(delta) * math.cos(theta))
    lam2 = lam1 + math.atan2(
        math.sin(theta) * math.sin(delta) * math.cos(phi1),
        math.cos(delta) - math.sin(phi1) * math.sin(phi2))
    # Normalise longitude back to (-180, 180] — atan2 above can walk it past
    # the antimeridian for a destination near it.
    lon2 = (math.degrees(lam2) + 540.0) % 360.0 - 180.0
    return (math.degrees(phi2), lon2)


def node_boundary(node_lat: float, node_lon: float,
                  failures: List[LinkFailure],
                  n_sectors: int = DEFAULT_SECTORS,
                  max_age_days: Optional[float] = None,
                  now: Optional[float] = None) -> dict:
    """Bucket a node's walked LOSSES into ``n_sectors`` compass wedges
    around it, the nearest loss in each wedge standing for where the edge
    starts — a farther loss in the same direction is still inside the edge
    a nearer one already drew, so the minimum is the honest edge, not the
    average.

    Bearing AND distance are both measured fresh from (*node_lat*,
    *node_lon*) to the failure's own position — never trusting
    ``LinkFailure.distance_km`` as-stored. That field was computed against
    whatever anchor point was current AT WALK TIME (monitor.boundary_walk
    ping_result's ``self.node_lat, self.node_lon``, which itself may have
    been the first live GPS fix, not the registry's stamped position); the
    ring must measure bearing and radius from the SAME centre point, so
    every sample here is re-derived from the centre this call was actually
    asked about.

    *max_age_days* / *now*: when BOTH given, a failure older than
    *max_age_days* is dropped — a node's surroundings change (new foliage,
    a parked truck, a season), and a year-old loss is not evidence of
    today's edge. Default is no filtering: nothing here invents a cutoff
    the operator did not ask for.

    A failure with an invalid position (monitor.geo.valid_position —
    NaN/inf/None/out-of-range/(0,0)) is skipped, never crashes this — the
    only geometry input this function actually trusts from the record.

    Returns ``{"sectors": [{"bearing_from", "bearing_to", "radius_km",
    "sample_count"}, ...], "coverage_frac", "total_failures_used"}``. A
    sector nothing landed in reads ``radius_km: None`` — never guessed."""
    width = 360.0 / n_sectors
    best_km: List[Optional[float]] = [None] * n_sectors
    counts = [0] * n_sectors
    used = 0
    for f in failures or ():
        if f is None:
            continue
        if not valid_position(getattr(f, "lat", None), getattr(f, "lon", None)):
            continue
        if max_age_days is not None and now is not None:
            age_days = (now - f.observed_at) / 86400.0
            if age_days > max_age_days:
                continue
        brg = bearing_deg(node_lat, node_lon, f.lat, f.lon)
        dist_km = haversine_m(node_lat, node_lon, f.lat, f.lon) / 1000.0
        idx = min(int(brg // width), n_sectors - 1)
        counts[idx] += 1
        used += 1
        if best_km[idx] is None or dist_km < best_km[idx]:
            best_km[idx] = dist_km
    sectors = [{"bearing_from": i * width, "bearing_to": (i + 1) * width,
               "radius_km": best_km[i], "sample_count": counts[i]}
               for i in range(n_sectors)]
    with_data = sum(1 for s in sectors if s["radius_km"] is not None)
    coverage = (with_data / n_sectors) if n_sectors else 0.0
    return {"sectors": sectors, "coverage_frac": coverage,
            "total_failures_used": used}


def boundary_segments(shape: dict, node_lat: float, node_lon: float
                      ) -> List[Tuple[float, float, float, float]]:
    """Turn a :func:`node_boundary` shape into line segments around the
    node: one per pair of ADJACENT sectors that BOTH have data, connecting
    the destination points at each sector's mid-bearing and radius.

    A sector with ``radius_km: None`` breaks the chain on both sides — no
    segment is drawn across the gap, and none from/to the None sector
    itself. So a walk done on three sides of a node draws three short arcs,
    never a shape closed by a guess across the side never walked; only a
    sector filled all the way round produces the full closed loop of
    ``n_sectors`` segments."""
    sectors = shape.get("sectors") or []
    n = len(sectors)
    if n < 2:
        return []
    points: List[Optional[Tuple[float, float]]] = []
    for s in sectors:
        r = s.get("radius_km")
        if r is None:
            points.append(None)
            continue
        mid = (s["bearing_from"] + s["bearing_to"]) / 2.0
        points.append(destination_point(node_lat, node_lon, mid, r))
    segments: List[Tuple[float, float, float, float]] = []
    for i in range(n):
        a = points[i]
        b = points[(i + 1) % n]
        if a is None or b is None:
            continue
        segments.append((a[0], a[1], b[0], b[1]))
    return segments
