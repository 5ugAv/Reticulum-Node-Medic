"""SCAN mode — offline geographic view of known nodes (will also host the
topology graph). Formerly "Map".

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
    DEFAULT_MAX_ZOOM, DEFAULT_MIN_ZOOM, DEFAULT_RADIUS_KM, RADIUS_STEPS,
    DETAIL_MAX_ZOOM, ATTRIBUTION, download_region, download_node_details,
    estimate_download, is_online,
    storage_summary, disk_free_mb, parse_latlon, ip_geolocate)


class MapPlot(Widget):
    """Draws located nodes as status-coloured dots, over an offline tile basemap
    when one is available. Interactive: drag to pan, pinch to zoom (a level per
    pinch, clamped to the cached zoom range); until first touched, it auto-fits
    the nodes / cached area."""

    def __init__(self, nodes=None, tiles=None, **kwargs):
        super().__init__(**kwargs)
        self._nodes = list(nodes or [])
        self._tiles = tiles                      # MBTiles | None
        self._labels: List[Label] = []
        # interactive view state (None until the user pans/zooms = auto-fit)
        self._center = None                      # (lat, lon)
        self._zoom = None
        self._touches = {}                       # touch uid -> last (x, y)
        self._pinch_base = None                  # two-finger start distance
        self._trigger = Clock.create_trigger(self._redraw, 0.05)
        self.bind(size=self._redraw, pos=self._redraw)

    # -- gestures -----------------------------------------------------------

    def on_touch_down(self, touch):
        if not self.collide_point(*touch.pos) or self._tiles is None:
            return super().on_touch_down(touch)
        touch.grab(self)
        self._touches[touch.uid] = touch.pos
        if len(self._touches) == 2:
            pts = list(self._touches.values())
            self._pinch_base = max(1.0, ((pts[0][0] - pts[1][0]) ** 2 +
                                         (pts[0][1] - pts[1][1]) ** 2) ** 0.5)
        return True

    def on_touch_move(self, touch):
        if touch.grab_current is not self:
            return super().on_touch_move(touch)
        self._touches[touch.uid] = touch.pos
        view = self._current_view()
        if view is None:
            return True
        if len(self._touches) == 1:              # drag = pan
            from ui.map_tiles import unproject_px
            cx = view.off_x + view.width / 2.0 - touch.dx
            cy = view.off_y + view.height / 2.0 + touch.dy   # kivy y-up vs world y-down
            self._center = unproject_px(cx, cy, view.zoom)
            self._zoom = view.zoom
            self._trigger()
        elif len(self._touches) == 2 and self._pinch_base:
            pts = list(self._touches.values())
            dist = max(1.0, ((pts[0][0] - pts[1][0]) ** 2 +
                             (pts[0][1] - pts[1][1]) ** 2) ** 0.5)
            ratio = dist / self._pinch_base
            if ratio > 1.30 or ratio < 0.77:
                self._step_zoom(+1 if ratio > 1.0 else -1, view)
                self._pinch_base = dist          # re-arm for the next step
        return True

    def on_touch_up(self, touch):
        if touch.grab_current is self:
            touch.ungrab(self)
            self._touches.pop(touch.uid, None)
            if len(self._touches) < 2:
                self._pinch_base = None
            return True
        return super().on_touch_up(touch)

    def _step_zoom(self, direction, view):
        from ui.map_tiles import unproject_px
        # pinch may go past the regional zoom into the street-detail levels
        # (cached only around placed nodes — elsewhere those levels are blank)
        new_zoom = max(DEFAULT_MIN_ZOOM,
                       min(DETAIL_MAX_ZOOM, view.zoom + direction))
        if new_zoom == view.zoom:
            return
        cx = view.off_x + view.width / 2.0
        cy = view.off_y + view.height / 2.0
        self._center = unproject_px(cx, cy, view.zoom)
        self._zoom = new_zoom
        self._trigger()

    def _current_view(self):
        """The view as displayed right now (manual if touched, else auto-fit)."""
        return getattr(self, "_last_view", None)

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
        if self.width < 2 or self.height < 2:
            return
        pts = geo_points(self._nodes)
        if pts:
            lats = [p.lat for p in pts]
            lons = [p.lon for p in pts]
            bbox = (min(lats), max(lats), min(lons), max(lons))
            has_extent = bbox[0] != bbox[1] or bbox[2] != bbox[3]
            if self._tiles is not None and (has_extent or self._tile_bbox()):
                self._draw_tiled(pts, bbox if has_extent else self._tile_bbox())
            else:
                self._draw_coord_plot(pts)
            return
        # No located nodes yet — still show the cached basemap of YOUR area
        # (its bounds ride in the .mbtiles metadata), rather than a blank pane.
        if self._tiles is not None:
            bbox = self._tile_bbox()
            if bbox:
                self._draw_tiled([], bbox)

    def _tile_bbox(self):
        """(min_lat, max_lat, min_lon, max_lon) of the cached basemap, shrunk
        toward its centre so the default view is a regional look, not the whole
        200 km circle edge-to-edge."""
        b = self._tiles.bounds() if self._tiles is not None else None
        if not b:
            return None
        w, s, e, n = b                              # lon/lat order in metadata
        clat, clon = (s + n) / 2, (w + e) / 2
        f = 0.25                                    # show the central quarter
        return (clat - (n - s) / 2 * f, clat + (n - s) / 2 * f,
                clon - (e - w) / 2 * f, clon + (e - w) / 2 * f)

    def _draw_tiled(self, pts, bbox):
        from ui.map_tiles import view_at
        if self._center is not None and self._zoom is not None:
            view = view_at(self._center[0], self._center[1], self._zoom,
                           self.width, self.height)      # user-driven pan/zoom
        else:
            view = build_view(*bbox, self.width, self.height, padding=dp(32),
                              max_zoom=DEFAULT_MAX_ZOOM)  # auto-fit, cache-clamped
        self._last_view = view
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


class ScanScreen(BoxLayout):
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

        # Offline-map control: [-] radius stepper [+] around the download
        # button, a live size-vs-storage line beneath, and a typed home-base
        # coordinate as the fallback centre when there's no GPS fix or placed
        # node yet.
        row = BoxLayout(orientation="horizontal", size_hint=(1, None),
                        height=dp(44), spacing=dp(6))
        self.minus_btn = Button(text="-", size_hint=(None, 1), width=dp(40))
        self.minus_btn.bind(on_release=lambda *_: self._step_radius(-1))
        self.plus_btn = Button(text="+", size_hint=(None, 1), width=dp(40))
        self.plus_btn.bind(on_release=lambda *_: self._step_radius(+1))
        self.dl_button = Button(text="", size_hint=(1, 1))
        self.dl_button.bind(on_release=lambda *_: self._on_download())
        row.add_widget(self.minus_btn)
        row.add_widget(self.dl_button)
        row.add_widget(self.plus_btn)
        self.add_widget(row)

        self.dl_status = Label(text="", halign="left", valign="middle",
                               size_hint=(1, None), height=dp(26),
                               color=theme.status_rgba("unknown", 0.95))
        self.dl_status.bind(size=lambda i, v: setattr(i, "text_size", v))
        self.add_widget(self.dl_status)

        # Manual entry — LAST-resort centre, hidden unless self-location fails
        from kivy.uix.textinput import TextInput
        self.center_input = TextInput(
            hint_text="Couldn't find your location - type home base as: "
                      "lat, lon  (e.g. -37.79, 144.96)",
            multiline=False, size_hint=(1, None), height=0, opacity=0)
        self.center_input.bind(text=lambda *_: self._refresh_estimate())
        self.add_widget(self.center_input)

        self._refresh_header()
        self.set_nodes(nodes or [])
        self._refresh_estimate()

        # Self-locate in the background (IP geolocation — city-level is plenty
        # for a map radius, and downloads need internet anyway). The user just
        # presses download; typing coordinates is the fallback of last resort.
        self._ip_center = None            # (lat, lon, place) once found
        self._ip_tried = False
        threading.Thread(target=self._locate_self, daemon=True).start()

    def _locate_self(self):
        found = ip_geolocate() if is_online() else None
        def apply(dt):
            self._ip_tried = True
            self._ip_center = found
            if found is None and not geo_points(self._nodes):
                self.center_input.height = dp(44)   # last resort: show the field
                self.center_input.opacity = 1
            self._refresh_estimate()
        Clock.schedule_once(apply, 0)

    def _refresh_header(self):
        if self._tiles is not None:
            # basemap attribution is a licence condition, not decoration
            self.header.text = f"Map — node coverage   [{ATTRIBUTION}]"
        else:
            self.header.text = "Map — node coverage"

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
        else the centroid of placed nodes, else a typed home-base coordinate.
        Returns ((lat, lon), source_label) or (None, None)."""
        fix = read_gps(self._gps_reader) if self._gps_reader else read_gps()
        if fix and fix.has_fix:
            return (fix.lat, fix.lon), "current GPS location"
        pts = geo_points(self._nodes)
        if pts:
            lat = sum(p.lat for p in pts) / len(pts)
            lon = sum(p.lon for p in pts) / len(pts)
            return (lat, lon), "placed nodes"
        ip = getattr(self, "_ip_center", None)
        if ip:
            return (ip[0], ip[1]), f"{ip[2]} (approximate, from your internet)"
        typed = parse_latlon(getattr(self, "center_input", None)
                             and self.center_input.text or "")
        if typed:
            return typed, "entered home base"
        return None, None

    def _set_status(self, text, status="unknown"):
        self.dl_status.text = text
        self.dl_status.color = theme.status_rgba(status, 0.95)

    def _step_radius(self, direction):
        """[-]/[+]: move through the preset radii and re-estimate."""
        if self._downloading:
            return
        steps = list(RADIUS_STEPS)
        if self._radius_km not in steps:
            steps.append(self._radius_km)
            steps.sort()
        i = steps.index(self._radius_km) + direction
        self._radius_km = steps[max(0, min(len(steps) - 1, i))]
        self._refresh_estimate()

    def _refresh_estimate(self):
        """Keep the button + status honest: current radius, size estimate, and
        whether it fits the storage budget."""
        if self._downloading:
            return
        self.dl_button.text = f"Download offline map ({self._radius_km:g} km)"
        center, source = self._download_center()
        if center is None:
            self.dl_button.disabled = True
            if not getattr(self, "_ip_tried", False):
                self._set_status("Finding your location…", "unknown")
            else:
                self._set_status("Couldn't find your location automatically - "
                                 "type home base below.", "warn")
            return
        count, mb = estimate_download(center[0], center[1], self._radius_km,
                                      DEFAULT_MIN_ZOOM, DEFAULT_MAX_ZOOM)
        verdict = storage_summary(mb, disk_free_mb(MAPS_DIR
                                                   if os.path.isdir(MAPS_DIR)
                                                   else "."))
        self.dl_button.disabled = not verdict["ok"]
        self._set_status(f"Centred on {source}. {verdict['text']}",
                         "ok" if verdict["ok"] else "alert")

    def _on_download(self):
        if self._downloading:
            return
        if not is_online():
            self._set_status("No internet — connect to WiFi to download maps.",
                             "warn")
            return
        center, source = self._download_center()
        if center is None:
            self._refresh_estimate()
            return
        lat, lon = center
        count, mb = estimate_download(lat, lon, self._radius_km,
                                      DEFAULT_MIN_ZOOM, DEFAULT_MAX_ZOOM)
        if not storage_summary(mb, disk_free_mb("."))["ok"]:
            self._refresh_estimate()
            return
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
        # Street-detail top-up: a small z13-15 circle around every PLACED node,
        # so a service visit can navigate to the node's street. Tiny + polite.
        if not summary.get("blocked") and not summary.get("cancelled"):
            located = geo_points(self._nodes)
            if located:
                def dprog(s):
                    if "detail_of" in s:
                        Clock.schedule_once(
                            lambda dt, n=s["detail_of"]: self._set_status(
                                f"Caching street detail around {n}…"), 0)
                detail = download_node_details(
                    [(p.lat, p.lon, p.label or "a node") for p in located],
                    dest, on_progress=dprog)
                summary["fetched"] += detail["fetched"]
                if detail.get("blocked"):
                    summary["blocked"] = True
        Clock.schedule_once(lambda dt: self._download_done(summary), 0)

    def _download_done(self, summary):
        self._downloading = False
        self.dl_button.disabled = False
        self._tiles = find_mbtiles()
        self.plot.set_tiles(self._tiles)
        self._refresh_header()
        got, failed = summary["fetched"] + summary["skipped"], summary["failed"]
        if summary.get("blocked"):
            self._set_status("The tile server started refusing us (bulk "
                             "protection). Stopped cleanly - try again later "
                             "or with a smaller radius.", "alert")
            return
        if got and self._tiles is not None:
            msg = f"Offline map ready — {got} tiles cached."
            if failed:
                msg += f" ({failed} unavailable)"
            self._set_status(msg, "ok")
            self.center_input.height = 0            # centre solved; tidy away
            self.center_input.opacity = 0
        else:
            self._set_status("Download failed — no tiles cached. Check the "
                             "connection and try again.", "alert")
