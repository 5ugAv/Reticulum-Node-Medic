"""Map mode — offline geographic view of known nodes (spec mode #4).

Status-coloured dots for every node with a birth-cert location. When an offline
**MBTiles** basemap is carried (assets/maps/*.mbtiles), the dots sit on real map
tiles (Web Mercator, ui.map_tiles); otherwise it falls back to a dependency-free
aspect-correct coord plot (ui.map_projection) — either way, fully offline (a
field tool has no internet). Nodes without coordinates are listed below so they
aren't silently dropped. This screen also hosts the coverage-mapper survey layer.
"""

from __future__ import annotations

import io
from typing import List

from kivy.core.image import Image as CoreImage
from kivy.graphics import Color, Ellipse, Rectangle
from kivy.metrics import dp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.label import Label
from kivy.uix.widget import Widget

from ui import theme
from ui.map_projection import geo_points, project
from ui.map_tiles import TILE_SIZE, build_view, find_mbtiles, tiles_for_view


class MapPlot(Widget):
    """Draws located nodes as status-coloured dots, over an offline tile basemap
    when one is available, redrawing on any resize."""

    def __init__(self, nodes=None, tiles=None, **kwargs):
        super().__init__(**kwargs)
        self._nodes = list(nodes or [])
        self._tiles = tiles                      # MBTiles | None
        self._labels: List[Label] = []
        self.bind(size=self._redraw, pos=self._redraw)

    def set_nodes(self, nodes):
        self._nodes = list(nodes or [])
        self._redraw()

    def set_tiles(self, tiles):
        self._tiles = tiles
        self._redraw()

    def _clear_labels(self):
        for lbl in self._labels:
            self.remove_widget(lbl)
        self._labels = []

    def _redraw(self, *_):
        self.canvas.clear()
        self._clear_labels()
        pts = geo_points(self._nodes)
        if not pts or self.width < 2 or self.height < 2:
            return
        lats = [p.lat for p in pts]
        lons = [p.lon for p in pts]
        bbox = (min(lats), max(lats), min(lons), max(lons))
        has_extent = bbox[0] != bbox[1] or bbox[2] != bbox[3]
        if self._tiles is not None and has_extent:
            self._draw_tiled(pts, bbox)
        else:
            self._draw_coord_plot(pts)

    def _draw_tiled(self, pts, bbox):
        view = build_view(*bbox, self.width, self.height, padding=dp(32))
        r = dp(6)
        with self.canvas:
            for t in tiles_for_view(view):
                data = self._tiles.get_tile(t.z, t.x, t.y)
                if not data:
                    continue
                try:
                    tex = CoreImage(io.BytesIO(data), ext="png").texture
                except Exception:
                    continue          # skip an unreadable tile, keep the map
                Color(1, 1, 1, 1)
                Rectangle(texture=tex,
                          pos=(self.x + t.screen_x, self.y + t.screen_y),
                          size=(TILE_SIZE, TILE_SIZE))
            for p in pts:
                sx, sy = view.to_screen(p.lat, p.lon)
                Color(*theme.status_rgba(p.status))
                Ellipse(pos=(self.x + sx - r, self.y + sy - r),
                        size=(2 * r, 2 * r))
        for p in pts:
            sx, sy = view.to_screen(p.lat, p.lon)
            self._add_label(p, sx, sy, r)

    def _draw_coord_plot(self, pts):
        placed = project(pts, self.width, self.height, padding=dp(32))
        r = dp(6)
        with self.canvas:
            for pl in placed:
                Color(*theme.status_rgba(pl.point.status))
                Ellipse(pos=(self.x + pl.x - r, self.y + pl.y - r),
                        size=(2 * r, 2 * r))
        for pl in placed:
            self._add_label(pl.point, pl.x, pl.y, r)

    def _add_label(self, point, sx, sy, r):
        if not point.label:
            return
        lbl = Label(text=point.label, font_size=dp(11),
                    color=theme.status_rgba(point.status), size_hint=(None, None))
        lbl.texture_update()
        lbl.size = lbl.texture_size
        lbl.pos = (self.x + sx + r + dp(3), self.y + sy - lbl.height / 2)
        self.add_widget(lbl)
        self._labels.append(lbl)


class MapScreen(BoxLayout):
    """Header + the offline plot (tiled when a basemap is carried) + a note."""

    def __init__(self, nodes=None, tiles=None, **kwargs):
        kwargs.setdefault("orientation", "vertical")
        super().__init__(**kwargs)
        self.padding = dp(12)
        self.spacing = dp(8)

        self._tiles = tiles if tiles is not None else find_mbtiles()
        basemap = " (offline basemap)" if self._tiles is not None else ""
        self.add_widget(Label(
            text=f"Map — node coverage{basemap}", size_hint=(1, None),
            height=dp(28), halign="left", bold=True))

        self.plot = MapPlot(tiles=self._tiles)
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
