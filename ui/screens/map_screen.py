"""Map mode — offline geographic view of known nodes (spec mode #4).

A dependency-free coord plot: status-coloured dots for every node that has a
birth-cert location, projected by ui.map_projection (aspect-correct, cos-lat, no
map tiles — a field tool has no internet). Nodes without coordinates are listed
below the plot so they aren't silently dropped. This screen is also where the
T-Beam Supreme coverage-mapper survey will layer in later.
"""

from __future__ import annotations

from typing import List

from kivy.graphics import Color, Ellipse
from kivy.metrics import dp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.label import Label
from kivy.uix.widget import Widget

from ui import theme
from ui.map_projection import geo_points, project


class MapPlot(Widget):
    """Draws the located nodes as status-coloured dots + name labels, via the
    pure projection, redrawing on any resize."""

    def __init__(self, nodes=None, **kwargs):
        super().__init__(**kwargs)
        self._nodes = list(nodes or [])
        self._labels: List[Label] = []
        self.bind(size=self._redraw, pos=self._redraw)

    def set_nodes(self, nodes):
        self._nodes = list(nodes or [])
        self._redraw()

    def _redraw(self, *_):
        self.canvas.clear()
        for lbl in self._labels:
            self.remove_widget(lbl)
        self._labels = []
        placed = project(geo_points(self._nodes), self.width, self.height,
                         padding=dp(32))
        r = dp(6)
        with self.canvas:
            for pl in placed:
                Color(*theme.status_rgba(pl.point.status))
                Ellipse(pos=(self.x + pl.x - r, self.y + pl.y - r),
                        size=(2 * r, 2 * r))
        for pl in placed:
            if not pl.point.label:
                continue
            lbl = Label(text=pl.point.label, font_size=dp(11),
                        color=theme.status_rgba(pl.point.status),
                        size_hint=(None, None))
            lbl.texture_update()
            lbl.size = lbl.texture_size
            lbl.pos = (self.x + pl.x + r + dp(3), self.y + pl.y - lbl.height / 2)
            self.add_widget(lbl)
            self._labels.append(lbl)


class MapScreen(BoxLayout):
    """Header + the offline plot + an un-located-nodes note."""

    def __init__(self, nodes=None, **kwargs):
        kwargs.setdefault("orientation", "vertical")
        super().__init__(**kwargs)
        self.padding = dp(12)
        self.spacing = dp(8)

        self.add_widget(Label(
            text="Map — node coverage", size_hint=(1, None), height=dp(28),
            halign="left", bold=True))

        self.plot = MapPlot()
        self.add_widget(self.plot)

        self.note = Label(text="", size_hint=(1, None), height=dp(24),
                          halign="left", color=theme.status_rgba("warn", 0.9))
        self.add_widget(self.note)

        self.set_nodes(nodes or [])

    def set_nodes(self, nodes):
        nodes = list(nodes or [])
        located = geo_points(nodes)
        self.plot.set_nodes(nodes)
        unlocated = [n.get("name", "(unnamed)") for n in nodes
                     if n.get("lat") is None or n.get("lon") is None]
        if not located and not unlocated:
            self.note.text = "No nodes yet — they appear here once built."
        elif not located:
            self.note.text = ("No node has a location yet. Build nodes with a GPS "
                              "fix to place them on the map.")
        elif unlocated:
            self.note.text = f"No location for: {', '.join(unlocated)}"
        else:
            self.note.text = ""
