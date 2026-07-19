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
from kivy.graphics import Color, Ellipse, Line, Rectangle
from kivy.metrics import dp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.widget import Widget

from monitor.geo import read_gps
from ui import theme

#: How far a pinch must spread (or close) before it steps one zoom level. Higher
#: = subtler / needs more of a pinch, which also throttles tile loading. A step
#: fires at PINCH_STEP-x apart and again each further PINCH_STEP-x.
PINCH_STEP = 1.7

#: Two touch points must be at least this far apart (window px) to count as a
#: real two-finger pinch. Some panels report ONE finger as two contact points a
#: few px apart — without this floor a single-finger drag reads as a pinch and
#: the map zooms instead of scrolling (observed on the medic's 5" panel).
PINCH_MIN_SEP = 120
from ui.map_projection import geo_points, project
from ui.map_tiles import MAPS_DIR, TILE_SIZE, build_view, find_mbtiles, tiles_for_view
from ui.map_download import (
    DEFAULT_MAX_ZOOM, DEFAULT_MIN_ZOOM, DEFAULT_RADIUS_KM, RADIUS_STEPS,
    DETAIL_MAX_ZOOM, ATTRIBUTION, WORLD, download_region, download_world,
    download_node_details, estimate_download, estimate_world, is_online,
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
        self._zooms = self._cache_zooms(tiles)   # zoom levels the cache actually has
        # Decoded-texture cache keyed by (z,x,y). Decoding a PNG->texture is the
        # expensive part; without this the Pi re-decoded every visible tile on
        # EVERY redraw (~20/s while panning) and the UI froze. Decode once, reuse.
        self._tex_cache = {}
        self._me = None                          # medic's own GPS fix (lat, lon)
        self._labels: List[Label] = []
        # interactive view state (None until the user pans/zooms = auto-fit)
        self._center = None                      # (lat, lon)
        self._zoom = None
        self._touches = {}                       # touch uid -> last (x, y)
        self._primary = None                     # the finger that drives a pan
        self._pinch_base = None                  # two-finger start distance
        self._trigger = Clock.create_trigger(self._redraw, 0.05)
        self.bind(size=self._redraw, pos=self._redraw)

    # -- gestures -----------------------------------------------------------

    def on_touch_down(self, touch):
        if not self.collide_point(*touch.pos) or self._tiles is None:
            return super().on_touch_down(touch)
        if touch.is_double_tap:                   # double-tap = zoom in on the spot
            self._zoom_at(touch.pos, +1)
            return True
        touch.grab(self)
        self._touches[touch.uid] = touch.pos
        if self._primary is None:                # first finger drives the pan
            self._primary = touch.uid
        if self._is_pinch():
            self._pinch_base = self._touch_sep()
        return True

    def _touch_sep(self):
        """Largest gap (window px) between any two active touches — 0 with < 2."""
        from ui.map_tiles import touch_separation
        return touch_separation(list(self._touches.values()))

    def _is_pinch(self):
        """A REAL two-finger pinch: two touches at least PINCH_MIN_SEP apart. A
        panel that reports one finger as two nearby points does NOT qualify, so a
        single-finger drag pans instead of zooming."""
        return len(self._touches) >= 2 and self._touch_sep() >= PINCH_MIN_SEP

    def on_touch_move(self, touch):
        if touch.grab_current is not self:
            return super().on_touch_move(touch)
        self._touches[touch.uid] = touch.pos
        view = self._current_view()
        if view is None:
            return True
        if self._is_pinch():                     # two fingers apart = zoom
            if self._pinch_base is None:
                self._pinch_base = self._touch_sep()
            dist = max(1.0, self._touch_sep())
            ratio = dist / self._pinch_base
            if ratio > PINCH_STEP or ratio < 1.0 / PINCH_STEP:
                self._step_zoom(+1 if ratio > 1.0 else -1, view)
                self._pinch_base = dist          # re-arm for the next step
        elif touch.uid == self._primary:         # one finger (its moves) = pan
            # Ignore the panel's phantom second contact: only the primary finger
            # drives the pan, so one physical drag = one pan (not doubled).
            from ui.map_tiles import project_px, unproject_px
            # Accumulate on the PERSISTENT centre (redraws are throttled, so
            # several moves share one stale view — deriving each from it drops
            # every delta but the last, the jumpy drag).
            z = self._zoom if self._zoom is not None else view.zoom
            clat, clon = self._center_latlon(view)
            cx, cy = project_px(clat, clon, z)
            cx -= touch.dx
            cy += touch.dy                       # kivy y-up vs world y-down
            self._center = self._clamp_center(*unproject_px(cx, cy, z))
            self._zoom = z
            self._trigger()
        return True

    def on_touch_up(self, touch):
        if touch.grab_current is self:
            touch.ungrab(self)
            self._touches.pop(touch.uid, None)
            if touch.uid == self._primary:       # promote a remaining finger
                self._primary = next(iter(self._touches), None)
            if len(self._touches) < 2:
                self._pinch_base = None
            return True
        return super().on_touch_up(touch)

    def _step_zoom(self, direction, view):
        from ui.map_tiles import unproject_px
        # pinch spans the full cached range: out to the world-overview levels
        # (z2+, blank until the World tier is downloaded) and in past the
        # regional zoom to the per-node street-detail levels.
        new_zoom = self._step_to_next_zoom(view.zoom, direction)
        if new_zoom == view.zoom:
            return
        cx = view.off_x + view.width / 2.0
        cy = view.off_y + view.height / 2.0
        self._center = self._clamp_center(*unproject_px(cx, cy, view.zoom))
        self._zoom = new_zoom
        self._trigger()

    def _current_view(self):
        """The view as displayed right now (manual if touched, else auto-fit)."""
        return getattr(self, "_last_view", None)

    @staticmethod
    def _cache_zooms(tiles):
        try:
            return sorted(tiles.zoom_levels()) if tiles is not None else []
        except Exception:
            return []

    def _max_cached_zoom(self):
        return self._zooms[-1] if self._zooms else DETAIL_MAX_ZOOM

    def _snap_zoom(self, z):
        from ui.map_tiles import snap_zoom
        return snap_zoom(self._zooms, z)

    def _step_to_next_zoom(self, current, direction):
        from ui.map_tiles import step_zoom
        # with no cache, fall back to the full interactive range
        zs = self._zooms or list(range(2, DETAIL_MAX_ZOOM + 1))
        return step_zoom(zs, current, direction)

    def _zoom_at(self, pos, direction):
        """Zoom one level toward the tapped screen point, recentring on the geo
        location under the finger — double-tap to dive into a spot."""
        view = self._current_view()
        if view is None:
            return
        from ui.map_tiles import unproject_px
        cur_zoom = self._zoom if self._zoom is not None else view.zoom
        new_zoom = self._step_to_next_zoom(cur_zoom, direction)
        sx = pos[0] - self.x
        sy = pos[1] - self.y
        wx = view.off_x + sx
        wy = view.off_y + (view.height - sy)     # kivy y-up -> world y-down
        lat, lon = unproject_px(wx, wy, view.zoom)
        self._center = self._clamp_center(lat, lon)
        self._zoom = new_zoom
        self._trigger()

    def _center_latlon(self, view):
        """Current view centre as (lat, lon): the stored pan centre, or the
        centre of the last auto-fit view when the user hasn't panned yet — so
        the first drag continues smoothly from wherever the map is sitting."""
        if self._center is not None:
            return self._center
        from ui.map_tiles import unproject_px
        cx = view.off_x + view.width / 2.0
        cy = view.off_y + view.height / 2.0
        return unproject_px(cx, cy, view.zoom)

    def _bounds(self):
        """Cached basemap bounds — bounds() is a SQLite lookup and this is hit on
        every pan move + redraw, so memoise it (cleared in set_tiles)."""
        b = getattr(self, "_bounds_cache", "unset")
        if b == "unset":
            b = self._bounds_cache = (
                self._tiles.bounds() if self._tiles is not None else None)
        return b

    def _clamp_center(self, lat, lon):
        """Keep the centre over the cached basemap so a drag can never strand
        the view in an all-black void it can't pan back from."""
        from ui.map_tiles import clamp_latlon
        return clamp_latlon(self._bounds(), lat, lon)

    def reset_view(self, *_):
        """Snap back to the auto-fit of the cached area / located nodes. The
        escape hatch from a bad pan — wired to a Recenter button and double-tap."""
        self._center = None
        self._zoom = None
        self._pinch_base = None
        self._redraw()

    def set_nodes(self, nodes):
        self._nodes = list(nodes or [])
        self._redraw()

    def set_tiles(self, tiles):
        self._tiles = tiles
        self._zooms = self._cache_zooms(tiles)
        self._tex_cache = {}                      # different source -> drop textures
        self._bounds_cache = "unset"             # recompute bounds for the new source
        self._redraw()

    def _tile_texture(self, z, x, y):
        """A decoded GL texture for tile (z,x,y), cached. Decoding the PNG is the
        costly step — caching it turns a redraw from 'decode 20 PNGs' into
        'reposition 20 textures', which is what makes pan/zoom smooth."""
        key = (z, x, y)
        tex = self._tex_cache.get(key)
        if tex is not None:
            return tex
        data = self._tiles.get_tile(z, x, y)
        if not data:
            return None
        try:
            tex = CoreImage(io.BytesIO(data), ext="png").texture
        except Exception:
            return None
        self._tex_cache[key] = tex
        if len(self._tex_cache) > 300:            # bound memory; drop oldest
            self._tex_cache.pop(next(iter(self._tex_cache)))
        return tex

    def _draw_tile(self, t):
        """Draw one tile at its screen position. If the exact (z,x,y) tile isn't
        cached — the cache is sparse at high zoom / per-area — OVERZOOM from the
        nearest cached ancestor (a lower-zoom tile, its matching quadrant scaled
        up). Blurry beats black: the pane never goes blank when you zoom in."""
        from ui.map_tiles import subtile_cell
        pos = (self.x + t.screen_x, self.y + t.screen_y)
        tex = self._tile_texture(t.z, t.x, t.y)
        if tex is not None:
            Color(1, 1, 1, 1)
            Rectangle(texture=tex, pos=pos, size=(TILE_SIZE, TILE_SIZE))
            return
        for k in range(1, t.z + 1):               # walk up the zoom pyramid
            atex = self._tile_texture(t.z - k, t.x >> k, t.y >> k)
            if atex is None:
                continue
            col, row, cells = subtile_cell(t.x, t.y, k)
            sub = max(1, TILE_SIZE // cells)      # region size in the 256px tile
            rx = col * sub
            ry = TILE_SIZE - (row + 1) * sub      # texture origin is bottom-left
            try:
                region = atex.get_region(rx, ry, sub, sub)
            except Exception:
                return
            Color(1, 1, 1, 1)
            Rectangle(texture=region, pos=pos, size=(TILE_SIZE, TILE_SIZE))
            return

    def set_me(self, latlon):
        """Update the medic's own live GPS position (lat, lon) — the "you are
        here" marker. None clears it. Cheap no-op when unchanged."""
        if latlon == self._me:
            return
        self._me = latlon
        self._redraw()

    def _clear_labels(self):
        for lbl in self._labels:
            self.remove_widget(lbl)
        self._labels = []

    def _redraw(self, *_):
        try:
            self._redraw_inner()
        except Exception:
            # A bad view must never wedge the canvas black forever; the next
            # good redraw (pan, resize, Recenter) repaints it.
            pass

    def _redraw_inner(self):
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
        # No located nodes yet — centre on the medic's own GPS fix if we have
        # one ("you are here"), else the cached basemap of YOUR area (bounds ride
        # in the .mbtiles metadata), rather than a blank pane.
        if self._tiles is not None:
            bbox = self._me_bbox() or self._tile_bbox()
            if bbox:
                self._draw_tiled([], bbox)

    def _me_bbox(self):
        """A tight (~1 km) bbox around the medic's live fix, so the default view
        opens zoomed in on where you're standing. None when there's no fix."""
        if self._me is None:
            return None
        lat, lon = self._me
        d = 0.006                                   # ~600-700 m each way
        return (lat - d, lat + d, lon - d, lon + d)

    def _tile_bbox(self):
        """(min_lat, max_lat, min_lon, max_lon) of the cached basemap, shrunk
        toward its centre so the default view is a regional look, not the whole
        200 km circle edge-to-edge."""
        b = self._bounds()
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
            # snap the manual zoom to a level the cache actually has (no blank)
            z = self._snap_zoom(self._zoom)
            view = view_at(self._center[0], self._center[1], z,
                           self.width, self.height)      # user-driven pan/zoom
        else:
            view = build_view(*bbox, self.width, self.height, padding=dp(32),
                              max_zoom=self._max_cached_zoom())  # auto-fit
            if self._zooms and view.zoom not in self._zooms:
                # fit_zoom landed on a level with no tiles (a cache gap) — rebuild
                # the auto-fit view at the nearest cached zoom on the bbox centre.
                z = self._snap_zoom(view.zoom)
                clat, clon = (bbox[0] + bbox[1]) / 2.0, (bbox[2] + bbox[3]) / 2.0
                view = view_at(clat, clon, z, self.width, self.height)
        self._last_view = view
        r = dp(6)
        with self.canvas:
            for t in tiles_for_view(view):
                self._draw_tile(t)
            for p in pts:
                sx, sy = view.to_screen(p.lat, p.lon)
                Color(*theme.status_rgba(p.status))
                Ellipse(pos=(self.x + sx - r, self.y + sy - r),
                        size=(2 * r, 2 * r))
            self._draw_me_marker(view)
        for p in pts:
            sx, sy = view.to_screen(p.lat, p.lon)
            self._add_label(p, sx, sy, r)
        self._add_me_label(view)

    def _draw_me_marker(self, view):
        """A steel-blue dot in a white halo = the medic ("you are here"). Drawn
        inside an open canvas context by _draw_tiled."""
        if self._me is None:
            return
        sx, sy = view.to_screen(self._me[0], self._me[1])
        x, y = self.x + sx, self.y + sy
        Color(1, 1, 1, 0.95)
        Line(circle=(x, y, dp(11)), width=1.4)
        Color(0.30, 0.62, 0.97, 1)               # steel blue, matches the accent
        Ellipse(pos=(x - dp(7), y - dp(7)), size=(dp(14), dp(14)))

    def _add_me_label(self, view):
        if self._me is None:
            return
        sx, sy = view.to_screen(self._me[0], self._me[1])
        lbl = Label(text="you are here", font_size=dp(11), bold=True,
                    color=(0.30, 0.62, 0.97, 1), size_hint=(None, None))
        lbl.texture_update()
        lbl.size = lbl.texture_size
        lbl.pos = (self.x + sx + dp(12), self.y + sy - lbl.height / 2)
        self.add_widget(lbl)
        self._labels.append(lbl)

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
        header_row = BoxLayout(orientation="horizontal", size_hint=(1, None),
                               height=dp(30), spacing=dp(6))
        self.header = Label(halign="left", valign="middle", bold=True)
        self.header.bind(size=lambda i, v: setattr(i, "text_size", v))
        self.recenter_btn = Button(text="Recenter", size_hint=(None, 1),
                                   width=dp(100))
        self.recenter_btn.bind(on_release=lambda *_: self.plot.reset_view())
        header_row.add_widget(self.header)
        header_row.add_widget(self.recenter_btn)
        self.add_widget(header_row)

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

        # Live "you are here": poll the Tracker's GPS fix and mark it on the map.
        if self._gps_reader is not None:
            self._poll_gps(0)
            Clock.schedule_interval(self._poll_gps, 3)

    def _poll_gps(self, _dt):
        try:
            coords = self._gps_reader() if self._gps_reader else None
        except Exception:
            coords = None
        self.plot.set_me(coords)

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
        """[-]/[+]: through the preset radii; one past the largest = World."""
        if self._downloading:
            return
        steps = list(RADIUS_STEPS)
        if self._radius_km not in steps and self._radius_km != WORLD:
            steps.append(self._radius_km)
            steps.sort()
        steps.append(WORLD)                        # the tier past 200 km
        i = steps.index(self._radius_km) + direction
        self._radius_km = steps[max(0, min(len(steps) - 1, i))]
        self._refresh_estimate()

    def _refresh_estimate(self):
        """Keep the button + status honest: current radius, size estimate, and
        whether it fits the storage budget."""
        if self._downloading:
            return
        if self._radius_km == WORLD:
            # world overview: no centre needed — the whole planet at z0-8
            self.dl_button.text = "Download offline map (World overview)"
            count, mb = estimate_world()
            verdict = storage_summary(mb, disk_free_mb(
                MAPS_DIR if os.path.isdir(MAPS_DIR) else "."))
            self.dl_button.disabled = not verdict["ok"]
            self._set_status(
                f"The whole world at overview zoom (~{count} tiles - hours, "
                f"resumable). {verdict['text']}",
                "ok" if verdict["ok"] else "alert")
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
        if self._radius_km == WORLD:
            count, mb = estimate_world()
            if not storage_summary(mb, disk_free_mb("."))["ok"]:
                self._refresh_estimate()
                return
            self._downloading = True
            self.dl_button.disabled = True
            self._set_status(f"Downloading world overview (~{count} tiles)…")
            dest = os.path.join(MAPS_DIR, "offline.mbtiles")
            os.makedirs(MAPS_DIR, exist_ok=True)
            threading.Thread(target=self._run_download,
                             args=(None, None, dest), daemon=True).start()
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
        if self._radius_km == WORLD:
            summary = download_world(dest, on_progress=progress)
        else:
            summary = download_region(lat, lon, dest, radius_km=self._radius_km,
                                      zmin=DEFAULT_MIN_ZOOM,
                                      zmax=DEFAULT_MAX_ZOOM,
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
