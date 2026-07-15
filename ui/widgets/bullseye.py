"""Bullseye widget — the Triage thermal target.

A thin Canvas renderer over the tested pure geometry (ui.triage_geometry) and
thermal ramp (monitor.triage.thermal_color): concentric thermal rings, a moving
dot with a fading trail, and a lock pulse. Responsive — it re-lays-out on resize,
so it is correct in portrait or landscape.
"""

from __future__ import annotations

from kivy.uix.widget import Widget
from kivy.graphics import Color, Ellipse, Line

from ui.triage_geometry import bullseye_geometry, dot_position
from monitor.triage import thermal_color

_TRAIL_LEN = 12
_DOT_PX = 15
_TRAIL_PX = 7


class BullseyeWidget(Widget):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._dot_radius = 1.0          # start at the cold edge
        self._trail: list = []           # recent normalised radii (older first)
        self._locked = False
        self.bind(pos=self._redraw, size=self._redraw)

    def update(self, snapshot: dict) -> None:
        """Feed a TriageSession.feed() snapshot; store + redraw."""
        self._dot_radius = snapshot.get("dot_radius", 1.0)
        self._locked = bool(snapshot.get("locked", False))
        self._trail.append(self._dot_radius)
        self._trail = self._trail[-_TRAIL_LEN:]
        self._redraw()

    def _redraw(self, *args) -> None:
        self.canvas.clear()
        w, h = self.size
        if w < 20 or h < 20:
            return
        g = bullseye_geometry(w, h)
        ox, oy = self.pos                # canvas is in window coords
        with self.canvas:
            # rings, outer (largest) first so inner ones overlay
            for radius, _name, t in g["rings"]:
                r, gr, b = thermal_color(t)
                Color(r, gr, b, 1.0)
                Ellipse(pos=(ox + g["cx"] - radius, oy + g["cy"] - radius),
                        size=(radius * 2, radius * 2))
            # fading trail (older = fainter)
            n = max(1, len(self._trail))
            for i, rn in enumerate(self._trail[:-1]):
                x, y = dot_position(rn, g)
                Color(1, 1, 1, (i + 1) / n * 0.35)
                Ellipse(pos=(ox + x - _TRAIL_PX / 2, oy + y - _TRAIL_PX / 2),
                        size=(_TRAIL_PX, _TRAIL_PX))
            # the dot
            x, y = dot_position(self._dot_radius, g)
            Color(1, 1, 1, 1.0)
            Ellipse(pos=(ox + x - _DOT_PX / 2, oy + y - _DOT_PX / 2),
                    size=(_DOT_PX, _DOT_PX))
            # lock pulse — a bright ring confirming a captured best reading
            if self._locked:
                Color(1, 1, 1, 0.7)
                Line(circle=(ox + g["cx"], oy + g["cy"], g["max_r"] * 1.05), width=2)
