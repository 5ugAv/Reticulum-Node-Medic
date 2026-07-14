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
import os
import threading
from typing import List

from kivy.clock import Clock
from kivy.core.image import Image as CoreImage
from kivy.graphics import Color, Ellipse, Rectangle
from kivy.metrics import dp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.widget import Widget

from monitor.geo import read_gps
from ui import theme
from ui.map_projection import geo_points, project
from ui.map_tiles import MAPS_DIR, TILE_SIZE, build_view, find_mbtiles, tiles_for_view
from ui.map_download import (
    DEFAULT_MAX_ZOOM, DEFAULT_MIN_ZOOM, DEFAULT_RADIUS_KM,
    download_region, estimate_download, is_online)


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
    """Header + the offline plot (tiled when a basemap is carried) + a note, and
    a control to cache a basemap for offline use while the medic has WiFi."""

    def __init__(self, nodes=None, tiles=None, gps_reader=None,
                 radius_km=DEFAULT_RADIUS_KM, **kwargs):
        kwargs.setdefault("orientation", "vertical")
        super().__init__(**kwargs)
        self.padding = dp(12)
        self.spacing = dp(8)
        self._gps_reader = gps_reader
        self._radius_km = radius_km
        self._nodes: List[dict] = []
        self._downloading = False

        self._tiles = tiles if tiles is not None else find_mbtiles()
        self.header = Label(size_hint=(1, None), height=dp(28), halign="left",
                            bold=True)
        self.add_widget(self.header)

        self.plot = MapPlot(tiles=self._tiles)
        self.add_widget(self.plot)

        self.note = Label(text="", size_hint=(1, None), height=dp(24),
                          halign="left", color=theme.status_rgba("warn", 0.9))
        self.add_widget(self.note)

        # Offline-map control: fetch tiles around the medic while it has WiFi.
        row = BoxLayout(orientation="horizontal", size_hint=(1, None),
                        height=dp(40), spacing=dp(8))
        self.dl_button = Button(
            text=f"⬇  Download offline map ({radius_km:g} km)",
            size_hint=(None, 1), width=dp(240))
        self.dl_button.bind(on_release=lambda *_: self._on_download())
        self.dl_status = Label(text="", halign="left", valign="middle",
                               color=theme.status_rgba("unknown", 0.95))
        self.dl_status.bind(size=lambda i, v: setattr(i, "text_size", v))
        row.add_widget(self.dl_button)
        row.add_widget(self.dl_status)
        self.add_widget(row)

        self._refresh_header()
        self.set_nodes(nodes or [])

    def _refresh_header(self):
        basemap = " (offline basemap)" if self._tiles is not None else ""
        self.header.text = f"Map — node coverage{basemap}"

    def set_nodes(self, nodes):
        nodes = list(nodes or [])
        self._nodes = nodes
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

    # -- offline map download ---------------------------------------------

    def _download_center(self):
        """Where to centre the download: the medic's GPS fix if it has one,
        else the centroid of nodes already placed on the map. Returns
        ((lat, lon), source_label) or (None, None)."""
        fix = read_gps(self._gps_reader) if self._gps_reader else read_gps()
        if fix and fix.has_fix:
            return (fix.lat, fix.lon), "current GPS location"
        pts = geo_points(self._nodes)
        if pts:
            lat = sum(p.lat for p in pts) / len(pts)
            lon = sum(p.lon for p in pts) / len(pts)
            return (lat, lon), "placed nodes"
        return None, None

    def _set_status(self, text, status="unknown"):
        self.dl_status.text = text
        self.dl_status.color = theme.status_rgba(status, 0.95)

    def _on_download(self):
        if self._downloading:
            return
        if not is_online():
            self._set_status("No internet — connect to WiFi to download maps.",
                             "warn")
            return
        center, source = self._download_center()
        if center is None:
            self._set_status("No location yet — need a GPS fix or a placed node "
                             "to centre on.", "warn")
            return
        lat, lon = center
        count, mb = estimate_download(lat, lon, self._radius_km,
                                      DEFAULT_MIN_ZOOM, DEFAULT_MAX_ZOOM)
        self._downloading = True
        self.dl_button.disabled = True
        self._set_status(f"Downloading ~{count} tiles (~{mb:g} MB) around "
                         f"{source}…")
        dest = os.path.join(MAPS_DIR, "offline.mbtiles")
        os.makedirs(MAPS_DIR, exist_ok=True)
        threading.Thread(target=self._run_download, args=(lat, lon, dest),
                         daemon=True).start()

    def _run_download(self, lat, lon, dest):
        def progress(s):
            if "cancelled" in s:
                return
            Clock.schedule_once(lambda dt: self._set_status(
                f"Downloading… {s['done']}/{s['total']} tiles"), 0)
        summary = download_region(lat, lon, dest, radius_km=self._radius_km,
                                  zmin=DEFAULT_MIN_ZOOM, zmax=DEFAULT_MAX_ZOOM,
                                  on_progress=progress)
        Clock.schedule_once(lambda dt: self._download_done(summary), 0)

    def _download_done(self, summary):
        self._downloading = False
        self.dl_button.disabled = False
        self._tiles = find_mbtiles()
        self.plot.set_tiles(self._tiles)
        self._refresh_header()
        got, failed = summary["fetched"] + summary["skipped"], summary["failed"]
        if got and self._tiles is not None:
            msg = f"Offline map ready — {got} tiles cached."
            if failed:
                msg += f" ({failed} unavailable)"
            self._set_status(msg, "ok")
        else:
            self._set_status("Download failed — no tiles cached. Check the "
                             "connection and try again.", "alert")
