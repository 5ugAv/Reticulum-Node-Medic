"""Hexagonal status indicator — the tool's signature element (not a circle).

A filled hexagon whose colour reflects a node's status (ok / warn / alert /
unknown), with an optional label drawn beside it by the caller.
"""

from __future__ import annotations

import math

from kivy.graphics import Color, Mesh
from kivy.properties import StringProperty
from kivy.uix.widget import Widget

from ui import theme


def _hexagon_vertices(cx, cy, radius):
    """Flat-topped hexagon: return (mesh_vertices, indices) for a triangle fan."""
    verts = [cx, cy, 0.0, 0.0]  # centre point for the fan
    indices = []
    points = []
    for i in range(6):
        angle = math.radians(60 * i - 30)
        x = cx + radius * math.cos(angle)
        y = cy + radius * math.sin(angle)
        points.append((x, y))
    for i, (x, y) in enumerate(points):
        verts.extend([x, y, 0.0, 0.0])
    # fan indices: centre (0) + each edge
    for i in range(6):
        indices.extend([0, 1 + i, 1 + (i + 1) % 6])
    return verts, indices


class HexStatus(Widget):
    status = StringProperty("unknown")

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.bind(pos=self._redraw, size=self._redraw, status=self._redraw)
        self._redraw()

    def _redraw(self, *args):
        self.canvas.clear()
        radius = min(self.width, self.height) / 2.0
        cx = self.x + self.width / 2.0
        cy = self.y + self.height / 2.0
        verts, indices = _hexagon_vertices(cx, cy, radius)
        with self.canvas:
            Color(*theme.status_rgba(self.status))
            Mesh(vertices=verts, indices=indices, mode="triangles")
