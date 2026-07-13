"""Offline geo-projection for Map mode — pure math, no Kivy, no map tiles.

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
                                status=n.get("status", "unknown")))
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
