"""Bullseye geometry for the Triage screen — pure, no Kivy.

Given a canvas size, lay out the concentric thermal rings and place the dot from
its normalised radius. Sizes to the *smaller* dimension and centres, so the same
code gives a correct bullseye in portrait or landscape — the responsive core the
Kivy widget just draws.
"""

from __future__ import annotations

import math
from typing import Dict, List, Tuple

# Rings outer -> inner: (outer-radius fraction of max_r, name, thermal 0..1).
# The colour for each comes from monitor.triage.thermal_color(t).
RING_STOPS: List[Tuple[float, str, float]] = [
    (1.00, "freezing", 0.10),
    (0.80, "cold",     0.30),
    (0.60, "warming",  0.50),
    (0.40, "warm",     0.75),
    (0.20, "bullseye", 1.00),
]


def bullseye_geometry(width: float, height: float, margin_frac: float = 0.08) -> Dict:
    """Centre, max radius and rings for a *width* x *height* canvas. ``margin_frac``
    keeps the outer ring off the very edge."""
    max_r = (min(width, height) / 2.0) * (1.0 - margin_frac)
    rings = [(frac * max_r, name, t) for frac, name, t in RING_STOPS]
    return {
        "cx": width / 2.0,
        "cy": height / 2.0,
        "max_r": max_r,
        "rings": rings,   # list of (radius, name, thermal_t), outer first
    }


def dot_position(dot_radius_norm: float, geometry: Dict,
                 angle_deg: float = -90.0) -> Tuple[float, float]:
    """Pixel position of the dot. ``dot_radius_norm`` 0 = dead centre (hot),
    1 = outer edge (freezing). Angle defaults to straight up; the dot moves
    radially in/out as the score changes."""
    n = max(0.0, min(1.0, dot_radius_norm))
    r = n * geometry["max_r"]
    a = math.radians(angle_deg)
    return (geometry["cx"] + r * math.cos(a), geometry["cy"] + r * math.sin(a))


def ring_for_score(score: float) -> str:
    """Which ring band a 0..1 score falls in (mirrors triage.score_to_ring but on
    the geometry side, for labelling the dot's current band)."""
    from monitor.triage import score_to_ring
    return score_to_ring(score)


# Fixed spoke bearing per metric (degrees, Kivy y-up): SNR straight up, link
# margin lower-left, noise floor lower-right. Same thirds every session, so the
# operator learns "top corner = clarity" once. Labels are plain English (guided
# mode) with the technical term kept so operators pick it up.
SPOKES = [
    ("snr", 90.0, "CLARITY (SNR)"),
    ("margin", 210.0, "HEADROOM"),
    ("noise", 330.0, "NOISE"),
]


def spoke_end(geometry: Dict, angle_deg: float, frac: float = 1.0) -> Tuple[float, float]:
    """Point at *frac* of max radius along a spoke bearing."""
    a = math.radians(angle_deg)
    r = geometry["max_r"] * frac
    return (geometry["cx"] + r * math.cos(a), geometry["cy"] + r * math.sin(a))


def triangle_points(metrics: Dict[str, float], geometry: Dict) -> List[Tuple[float, float]]:
    """Corner positions for the metric triangle. Each metric is 0..1 (1 = best);
    a better metric pulls its corner toward the centre (radius = 1 - value on its
    fixed spoke). Returns corners in SPOKES order."""
    pts = []
    for key, angle, _label in SPOKES:
        v = max(0.0, min(1.0, metrics.get(key, 0.0)))
        pts.append(spoke_end(geometry, angle, 1.0 - v))
    return pts


def triangle_centroid(points: List[Tuple[float, float]]) -> Tuple[float, float]:
    xs = sum(p[0] for p in points) / len(points)
    ys = sum(p[1] for p in points) / len(points)
    return (xs, ys)
