"""Offline geo-projection for SCAN mode — pure math, no Kivy, no map tiles.

Projects node (lat, lon) positions into screen coordinates for a dependency-free
coverage/coord plot. A field tool has no internet, so there is no basemap: we
just plot the nodes' relative geography, correctly. Uses an equirectangular
projection with a cos(latitude) longitude correction (1° of longitude is shorter
than 1° of latitude away from the equator), then fits the data into the viewport
**preserving aspect ratio** (letter/pillar-boxed) so the layout isn't stretched.

Kivy's origin is bottom-left with y increasing UP, so higher latitude (north)
maps to higher y and higher longitude (east) to higher x — the map reads the way
you'd expect. Degenerate inputs (no points, a single point, or a zero-range axis)
are handled by centring rather than dividing by zero.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List


@dataclass
class GeoPoint:
    lat: float
    lon: float
    label: str = ""
    status: str = "unknown"      # ok | warn | alert | unknown (drives dot colour)
    approximate: bool = False    # a deliberately fuzzed claim: drawn as a ring


@dataclass
class Placed:
    x: float
    y: float
    point: GeoPoint


def geo_points(nodes) -> List["GeoPoint"]:
    """Adapt Map node dicts (``{lat, lon, name, status}`` from
    ``NodeRegistry.located_nodes``) to ``GeoPoint``s, dropping any without
    coordinates. Pure — the plot draws whatever this returns."""
    out = []
    for n in nodes or []:
        if n.get("lat") is not None and n.get("lon") is not None:
            out.append(GeoPoint(lat=n["lat"], lon=n["lon"],
                                label=n.get("name", ""),
                                status=n.get("status", "unknown"),
                                approximate=bool(n.get("approximate"))))
    return out


def project(points: List[GeoPoint], width: float, height: float,
            padding: float = 24.0) -> List[Placed]:
    """Fit *points* into a ``width`` x ``height`` viewport (inset by ``padding``
    on every side), aspect-preserved. Returns ``[Placed(x, y, point), ...]`` in
    Kivy screen coordinates. ``[]`` for no points; a single point (or an all-same
    axis) is centred on that axis."""
    if not points:
        return []

    min_lat = min(p.lat for p in points)
    max_lat = max(p.lat for p in points)
    min_lon = min(p.lon for p in points)
    max_lon = max(p.lon for p in points)

    kx = math.cos(math.radians((min_lat + max_lat) / 2.0))  # lon compression
    dlat = max_lat - min_lat
    dlon = (max_lon - min_lon) * kx

    inner_w = max(width - 2 * padding, 1.0)
    inner_h = max(height - 2 * padding, 1.0)

    sx = inner_w / dlon if dlon > 0 else math.inf
    sy = inner_h / dlat if dlat > 0 else math.inf
    scale = min(sx, sy)

    if not math.isfinite(scale):
        # zero range on BOTH axes (single point / all identical) -> centre all
        cx, cy = width / 2.0, height / 2.0
        return [Placed(cx, cy, p) for p in points]

    used_w = dlon * scale
    used_h = dlat * scale
    off_x = padding + (inner_w - used_w) / 2.0     # centre the used extent
    off_y = padding + (inner_h - used_h) / 2.0

    placed = []
    for p in points:
        x = off_x + ((p.lon - min_lon) * kx) * scale
        y = off_y + (p.lat - min_lat) * scale
        placed.append(Placed(x, y, p))
    return placed


def boxes_overlap(a, b) -> bool:
    """Do two ``(x, y, w, h)`` rectangles share any area? Touching edges do
    not count — two labels sitting exactly shoulder to shoulder are readable."""
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return not (ax + aw <= bx or bx + bw <= ax
                or ay + ah <= by or by + bh <= ay)


def place_label(desired, size, taken, step, tries: int = 6):
    """Where to actually put a map label so it can be read.

    Returns ``(x, y)`` — the first candidate that clears everything already
    placed, starting at *desired* and stepping alternately DOWN then UP.

    THE DOT NEVER MOVES; only the name does. A node's position is a fact and
    the map must not lie about it, but the text beside it is just a name, so
    the name is what gives way. Two nodes a few metres apart land on one pixel
    at street zoom — skyfinger sits a few metres from ELSEWHERE — and their
    names printed over each other into an unreadable smear (operator,
    2026-09-28).

    Down first, because a label below its dot still reads as belonging to it;
    the eye follows the column. Alternating keeps a pair symmetrical about
    their shared point instead of marching one direction off the screen.

    Gives up after *tries* steps and returns the desired spot: a label that
    has wandered half a screen from its dot is worse than one that overlaps,
    and at that point the map is too crowded for labels to solve it anyway.
    """
    dx, dy = desired
    w, h = size
    for k in range(tries + 1):
        for sign in ((0,) if k == 0 else (-1, 1)):
            cand_y = dy + sign * k * step
            if not any(boxes_overlap((dx, cand_y, w, h), t) for t in taken):
                return (dx, cand_y)
    return (dx, dy)
