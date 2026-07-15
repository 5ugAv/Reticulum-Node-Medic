"""Bullseye widget — the Triage thermal target with the metric triangle.

Thermal rings (cold violet edge -> hot centre) with three fixed spokes — SNR up,
link margin lower-left, noise floor lower-right. Each metric slides its corner
in (good) or out (bad) along its spoke; the corners join into a triangle whose
SHAPE is the reading: tight near the centre = lock it in; one flared corner =
that metric is the problem. A bright centroid dot (with fading trail) stays the
single "overall" headline. All geometry is the tested pure module; this widget
only draws.
"""

from __future__ import annotations

from kivy.uix.widget import Widget
from kivy.graphics import Color, Ellipse, Line

from ui.triage_geometry import (
    bullseye_geometry, spoke_end, triangle_points, triangle_centroid, SPOKES,
)
from monitor.triage import thermal_color

_TRAIL_LEN = 12
_DOT_PX = 16
_CORNER_PX = 11
_TRAIL_PX = 7


class BullseyeWidget(Widget):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._metrics = {"snr": 0.0, "margin": 0.0, "noise": 0.0}
        self._trail: list = []           # recent centroids, normalised offsets
        self._locked = False
        self.bind(pos=self._redraw, size=self._redraw)

    def update(self, snapshot: dict) -> None:
        """Feed a TriageSession.feed() snapshot; store + redraw."""
        self._metrics = snapshot.get("metrics", self._metrics)
        self._locked = bool(snapshot.get("locked", False))
        self._trail.append(dict(self._metrics))
        self._trail = self._trail[-_TRAIL_LEN:]
        self._redraw()

    def spoke_label_positions(self) -> list:
        """(key, label, x, y) for each spoke's outer label, in window coords —
        the screen places Labels here (widgets can't draw text on the canvas)."""
        w, h = self.size
        if w < 20 or h < 20:
            return []
        g = bullseye_geometry(w, h)
        ox, oy = self.pos
        out = []
        for key, angle, label in SPOKES:
            x, y = spoke_end(g, angle, 1.12)         # just past the outer ring
            out.append((key, label, ox + x, oy + y))
        return out

    def _redraw(self, *args) -> None:
        self.canvas.clear()
        w, h = self.size
        if w < 20 or h < 20:
            return
        g = bullseye_geometry(w, h)
        ox, oy = self.pos
        with self.canvas:
            # thermal rings, outer (largest) first so inner ones overlay
            for radius, _name, t in g["rings"]:
                r, gr, b = thermal_color(t)
                Color(r, gr, b, 1.0)
                Ellipse(pos=(ox + g["cx"] - radius, oy + g["cy"] - radius),
                        size=(radius * 2, radius * 2))
            # the three fixed spokes (faint guides)
            Color(1, 1, 1, 0.22)
            for _key, angle, _label in SPOKES:
                ex, ey = spoke_end(g, angle, 1.0)
                Line(points=[ox + g["cx"], oy + g["cy"], ox + ex, oy + ey], width=1)
            # centroid trail (older = fainter)
            n = max(1, len(self._trail))
            for i, m in enumerate(self._trail[:-1]):
                cx, cy = triangle_centroid(triangle_points(m, g))
                Color(1, 1, 1, (i + 1) / n * 0.30)
                Ellipse(pos=(ox + cx - _TRAIL_PX / 2, oy + cy - _TRAIL_PX / 2),
                        size=(_TRAIL_PX, _TRAIL_PX))
            # the metric triangle
            pts = triangle_points(self._metrics, g)
            flat = []
            for (px, py) in pts:
                flat += [ox + px, oy + py]
            Color(1, 1, 1, 0.85)
            Line(points=flat + flat[:2], width=1.6)   # close the loop
            # corner markers
            Color(1, 1, 1, 1.0)
            for (px, py) in pts:
                Ellipse(pos=(ox + px - _CORNER_PX / 2, oy + py - _CORNER_PX / 2),
                        size=(_CORNER_PX, _CORNER_PX))
            # centroid dot — the single "overall" headline
            cx, cy = triangle_centroid(pts)
            Color(1, 1, 1, 1.0)
            Ellipse(pos=(ox + cx - _DOT_PX / 2, oy + cy - _DOT_PX / 2),
                    size=(_DOT_PX, _DOT_PX))
            # lock pulse — a bright ring confirming a captured best reading
            if self._locked:
                Color(1, 1, 1, 0.7)
                Line(circle=(ox + g["cx"], oy + g["cy"], g["max_r"] * 1.05), width=2)
