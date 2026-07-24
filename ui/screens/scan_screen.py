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
from kivy.graphics import (Color, Ellipse, Line, Quad, Rectangle, RoundedRectangle,
                           StencilPush, StencilUse, StencilUnUse, StencilPop)
from kivy.metrics import dp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.label import Label
from kivy.uix.textinput import TextInput
from kivy.uix.widget import Widget

from monitor.geo import read_gps, read_splitter_fix, fix_trust, geocode_address
from ui import theme
from ui.onscreen_keyboard import bind_field

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
    storage_summary, disk_free_mb, parse_latlon, ip_geolocate,
    add_point_detail, SPOT_MIN_ZOOM, SPOT_MAX_ZOOM, SPOT_RADIUS_KM)


# ---- pure helpers (unit-tested; no Kivy) -------------------------------------

def link_segments(topo):
    """Flatten a ``monitor.topology.Topology`` into who-hears-whom LINE SEGMENTS
    between LOCATED nodes: ``[(lat1, lon1, lat2, lon2), ...]``. Only edges whose
    BOTH endpoints have coordinates draw a line (a link to an unplaced node has
    nowhere to go); endpoint order is normalised so an A-B / B-A pair collapses to
    one segment. Pure — the app wraps this in a ``links_provider`` and MapPlot just
    strokes what it returns, so the topology stays untouched by the widget."""
    by_id = {n.id: n for n in getattr(topo, "nodes", [])}
    seen = set()
    out = []
    for e in getattr(topo, "edges", []):
        a, b = by_id.get(e.a), by_id.get(e.b)
        if a is None or b is None:
            continue
        if None in (a.lat, a.lon, b.lat, b.lon):
            continue
        key = tuple(sorted(((a.lat, a.lon), (b.lat, b.lon))))
        if key in seen:
            continue
        seen.add(key)
        out.append((a.lat, a.lon, b.lat, b.lon))
    return out


def suggestion_markers(suggestions):
    """Normalise placement suggestions — ``monitor.placement.Suggestion`` objects
    (``.lat/.lon/.reason/.kind``) OR plain dicts — into
    ``[{lat, lon, reason, kind}]``, dropping any without coordinates and deduping
    by rounded (lat, lon, kind). Pure; feeds MapPlot's 'add a node here' rings."""
    out = []
    seen = set()
    for s in suggestions or []:
        if isinstance(s, dict):
            lat, lon = s.get("lat"), s.get("lon")
            reason, kind = s.get("reason", ""), s.get("kind", "")
        else:
            lat, lon = getattr(s, "lat", None), getattr(s, "lon", None)
            reason = getattr(s, "reason", "") or ""
            kind = getattr(s, "kind", "") or ""
        if lat is None or lon is None:
            continue
        key = (round(float(lat), 6), round(float(lon), 6), kind)
        if key in seen:
            continue
        seen.add(key)
        out.append({"lat": lat, "lon": lon, "reason": reason, "kind": kind})
    return out


class MapPlot(Widget):
    """Draws located nodes as status-coloured dots, over an offline tile basemap
    when one is available. Interactive: drag to pan, pinch to zoom (a level per
    pinch, clamped to the cached zoom range); until first touched, it auto-fits
    the nodes / cached area."""

    def __init__(self, nodes=None, tiles=None, interactive=True, on_pick=None,
                 on_node_pick=None, links_provider=None, suggestions_provider=None,
                 **kwargs):
        super().__init__(**kwargs)
        self._nodes = list(nodes or [])
        self._tiles = tiles                      # MBTiles | None
        self._interactive = interactive          # False = a fixed verify view
        self._on_pick = on_pick                  # tap-to-place callback (lat, lon)
        self._on_node_pick = on_node_pick        # tap-a-node-dot callback (name)
        # Optional data feeds (default None -> nothing extra drawn, map unchanged):
        #   links_provider()      -> [(lat1,lon1,lat2,lon2), ...] mesh connections
        #   suggestions_provider()-> [obj/dict with lat/lon/reason/kind] placements
        self._links_provider = links_provider
        self._suggestions_provider = suggestions_provider
        self._show_links = False                 # mesh-lines toggle (default OFF)
        self._suggestions = []                   # last-drawn markers (for hit-test)
        self._last_view = None                   # current MercatorView (for taps)
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

    def focus(self, latlon, zoom=None):
        """Pin the view: centre on *latlon* at a street-level zoom — for the GPS-
        confirm screen, where the operator checks the pin against streets. Defaults
        to a neighbourhood zoom (capped at 16) so a deep per-spot cache doesn't slam
        the opening view right down to building level; the operator pinches in from
        there."""
        self._me = latlon
        self._center = latlon
        self._zoom = zoom if zoom is not None else min(
            16, self._zooms[-1] if self._zooms else 15)
        self._trigger()

    def center_on(self, latlon, zoom=None):
        """Centre the view on *latlon* at a street-level zoom WITHOUT moving the
        'you are here' pin (unlike focus) — for 'See on map' from a certificate,
        where the node's own status dot is already drawn at that spot."""
        self._center = latlon
        self._zoom = zoom if zoom is not None else min(
            17, self._zooms[-1] if self._zooms else 16)
        self._trigger()

    def zoom_by(self, direction):
        """Step the zoom in (+1) or out (-1) around the current view centre — for
        explicit +/- buttons, so zooming never depends on pinch reliability (cheap
        touch panels don't multi-touch well). Caps at the deepest cached level."""
        view = self._last_view or self._current_view()
        if view is None:
            return
        self._step_zoom(direction, view)

    def on_touch_down(self, touch):
        if (not self._interactive or not self.collide_point(*touch.pos)
                or self._tiles is None):
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
            # A stationary tap (not a drag or pinch) on an INTERACTIVE map: if it
            # landed ON a node dot, open that node; otherwise drop the placement
            # pin. So tap-a-node and tap-to-place coexist with pan/pinch/zoom.
            moved = abs(touch.x - touch.ox) + abs(touch.y - touch.oy) > dp(10)
            if (not moved and not self._touches and not touch.is_double_tap
                    and self._last_view is not None and self.collide_point(*touch.pos)):
                node = self._node_at(touch.x, touch.y)
                sugg = None if node is not None else self._suggestion_at(touch.x, touch.y)
                if node is not None and self._on_node_pick:
                    self._on_node_pick(node)
                elif sugg is not None:            # tapped an 'add a node here' ring
                    self._show_suggestion(sugg)
                elif self._on_pick:
                    latlon = self._last_view.to_latlon(touch.x - self.x, touch.y - self.y)
                    self._me = latlon
                    self._trigger()
                    self._on_pick(latlon)
            return True
        # Tap-to-place: on a non-interactive map with a pick handler (the GPS-
        # confirm screen), a tap drops the pin at that spot — offline location entry.
        if (self._on_pick and self._last_view is not None
                and not self._touches and self.collide_point(*touch.pos)):
            latlon = self._last_view.to_latlon(touch.x - self.x, touch.y - self.y)
            self._me = latlon
            self._trigger()                      # move the pin to the tapped point
            self._on_pick(latlon)
            return True
        return super().on_touch_up(touch)

    # -- optional overlays: mesh lines + placement suggestions --------------

    def set_show_links(self, on):
        """Toggle the who-hears-whom connection lines between located nodes.
        No-op visual change unless a ``links_provider`` was supplied."""
        on = bool(on)
        if on == self._show_links:
            return
        self._show_links = on
        self._redraw()

    def _fetch_links(self):
        """Current link segments to draw, or [] (toggle off / no provider / it
        raised). Providers pull live topology, so lines refresh on each redraw."""
        if not self._show_links or self._links_provider is None:
            return []
        try:
            return list(self._links_provider() or [])
        except Exception:
            return []

    def _fetch_suggestions(self):
        if self._suggestions_provider is None:
            return []
        try:
            return suggestion_markers(self._suggestions_provider())
        except Exception:
            return []

    def _draw_links(self, view):
        """Faint accent connection lines UNDER the node dots — drawn inside an
        open canvas context by _draw_tiled."""
        segs = self._fetch_links()
        if not segs:
            return
        Color(*theme.hex_to_rgba(theme.COLORS["accent"], 0.35))
        for seg in segs:
            try:
                lat1, lon1, lat2, lon2 = seg
            except (TypeError, ValueError):
                continue
            x1, y1 = view.to_screen(lat1, lon1)
            x2, y2 = view.to_screen(lat2, lon2)
            Line(points=[self.x + x1, self.y + y1, self.x + x2, self.y + y2],
                 width=1.2)

    def _draw_suggestions(self, view):
        """A hollow accent ring + small '+' at each placement suggestion —
        'add a node here'. Cached in self._suggestions for tap hit-testing."""
        self._suggestions = self._fetch_suggestions()
        r = dp(9)
        for s in self._suggestions:
            sx, sy = view.to_screen(s["lat"], s["lon"])
            cx, cy = self.x + sx, self.y + sy
            Color(*theme.hex_to_rgba(theme.COLORS["accent"], 0.95))
            Line(circle=(cx, cy, r), width=1.6)
            Line(points=[cx - r * 0.5, cy, cx + r * 0.5, cy], width=1.6)
            Line(points=[cx, cy - r * 0.5, cx, cy + r * 0.5], width=1.6)

    def _suggestion_at(self, tx, ty):
        """The suggestion marker under the tap (window coords), within a finger
        radius — or None."""
        view = self._last_view
        if view is None:
            return None
        best, best_d = None, None
        hit = dp(18)
        for s in getattr(self, "_suggestions", []):
            sx, sy = view.to_screen(s["lat"], s["lon"])
            d = ((self.x + sx - tx) ** 2 + (self.y + sy - ty) ** 2) ** 0.5
            if d <= hit and (best_d is None or d < best_d):
                best, best_d = s, d
        return best

    def _show_suggestion(self, sugg):
        """Pop a small label with the suggestion's reason when its marker is
        tapped — why the engine thinks a node belongs here."""
        from kivy.uix.popup import Popup
        reason = sugg.get("reason") or "Suggested node location"
        kind = (sugg.get("kind") or "").replace("_", " ")
        title = "Add a node here" + (f"  ·  {kind}" if kind else "")
        body = Label(text=reason, halign="center", valign="middle",
                     padding=(dp(12), dp(12)))
        body.bind(size=lambda i, v: setattr(i, "text_size", v))
        Popup(title=title, content=body, size_hint=(0.8, 0.4)).open()

    def _node_at(self, tx, ty):
        """The label of the located node whose dot is under the tap (window coords
        tx,ty), within a finger-sized radius — or None. Used to tell 'tap a node'
        apart from 'tap empty map to place a pin'."""
        view = self._last_view
        if view is None:
            return None
        best, best_d = None, None
        hit = dp(18)
        for p in geo_points(self._nodes):
            if not p.label:
                continue
            sx, sy = view.to_screen(p.lat, p.lon)
            d = ((self.x + sx - tx) ** 2 + (self.y + sy - ty) ** 2) ** 0.5
            if d <= hit and (best_d is None or d < best_d):
                best, best_d = p.label, d
        return best

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
        # Clip ALL map drawing to our own rectangle — otherwise zoomed-in tiles
        # spill up over the header row and cover its buttons (Location/Recenter).
        with self.canvas:
            StencilPush()
            Rectangle(pos=self.pos, size=self.size)
            StencilUse()
        try:
            self._draw_content()
        finally:
            with self.canvas:
                StencilUnUse()
                Rectangle(pos=self.pos, size=self.size)
                StencilPop()

    def _draw_content(self):
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
        # No located nodes yet — FILL the pane (centred view, no letterbox) on the
        # medic's own GPS fix if we have one ("you are here"), else on the cached
        # basemap's centre. Fitting the whole region here left tall black bars.
        if self._tiles is not None:
            if self._me is not None:
                self._draw_tiled([], None,
                                 fill=(self._me, min(16, self._max_cached_zoom())))
            else:
                b = self._bounds()
                if b:
                    w, s, e, n = b                       # lon/lat order in metadata
                    centre = ((s + n) / 2.0, (w + e) / 2.0)
                    self._draw_tiled([], None,
                                     fill=(centre, min(13, self._max_cached_zoom())))

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

    def _draw_tiled(self, pts, bbox, fill=None):
        from ui.map_tiles import view_at
        if self._center is not None and self._zoom is not None:
            # snap the manual zoom to a level the cache actually has (no blank)
            z = self._snap_zoom(self._zoom)
            view = view_at(self._center[0], self._center[1], z,
                           self.width, self.height)      # user-driven pan/zoom
        elif fill is not None:
            # empty state: a CENTRED view that fills the pane (no letterbox)
            (flat, flon), fz = fill
            view = view_at(flat, flon, self._snap_zoom(fz), self.width, self.height)
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
            self._draw_links(view)                # faint connection lines UNDER dots
            for p in pts:
                sx, sy = view.to_screen(p.lat, p.lon)
                Color(*theme.status_rgba(p.status))
                Ellipse(pos=(self.x + sx - r, self.y + sy - r),
                        size=(2 * r, 2 * r))
            self._draw_suggestions(view)          # 'add a node here' rings over dots
            self._draw_me_marker(view)
        for p in pts:
            sx, sy = view.to_screen(p.lat, p.lon)
            self._add_label(p, sx, sy, r)
        self._add_me_label(view)

    def _draw_me_marker(self, view):
        """A red MAP PIN whose point sits on the exact spot (the medic's GPS fix /
        the position being confirmed). Drawn inside an open canvas context by
        _draw_tiled — a teardrop head + point + white centre, like a classic pin."""
        if self._me is None:
            return
        sx, sy = view.to_screen(self._me[0], self._me[1])
        x, y = self.x + sx, self.y + sy          # the exact point = the pin's tip
        r = dp(11)
        cy = y + dp(20)                          # head centre, above the tip
        Color(0.86, 0.05, 0.05, 1)               # red
        # the point: a triangle from the head's lower sides down to the tip
        Quad(points=[x - r * 0.72, cy - r * 0.4, x + r * 0.72, cy - r * 0.4,
                     x, y, x, y])
        Ellipse(pos=(x - r, cy - r), size=(2 * r, 2 * r))    # round head
        Color(0.35, 0, 0, 0.55)                  # thin dark rim for definition
        Line(circle=(x, cy, r), width=1.2)
        Color(1, 1, 1, 1)                        # white centre
        Ellipse(pos=(x - dp(4.6), cy - dp(4.6)), size=(dp(9.2), dp(9.2)))

    def _add_me_label(self, view):
        # The red map pin marks the spot on its own — no "you are here" text.
        return

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


#: Bubble fill per GPS fix level; a warning triangle is drawn for held/none.
_LEVEL_FILL = {"live": "green", "held": "warning_yellow", "none": "red",
               "info": "accent"}


class _FixBadge(BoxLayout):
    """A rounded, FILLED status bubble: green (live) / yellow (held) / red (none) /
    accent (info). Draws a warning triangle for held/none — the ⚠ glyph renders as
    tofu in the default font, so we draw it. Ported from the old GPS-confirm page
    when the two map screens merged into one."""

    def __init__(self, **kwargs):
        super().__init__(orientation="horizontal", size_hint_y=None, height=dp(44),
                         padding=[dp(14), dp(4)], spacing=dp(6), **kwargs)
        with self.canvas.before:
            self._fill = Color(0, 0, 0, 0)
            self._rect = RoundedRectangle(radius=[dp(16)] * 4)
        self.bind(pos=self._sync, size=self._sync)
        self._tri = Widget(size_hint=(None, 1), width=dp(0))
        self._tri.bind(pos=self._draw_tri, size=self._draw_tri)
        self.add_widget(self._tri)
        self.label = Label(font_size="15sp", bold=True, halign="left", valign="middle")
        self.label.bind(size=lambda i, v: setattr(i, "text_size", v))
        self.add_widget(self.label)
        self._tri_color = None

    def _sync(self, *_):
        self._rect.pos, self._rect.size = self.pos, self.size

    def _draw_tri(self, *_):
        self._tri.canvas.after.clear()
        if self._tri_color is None or self._tri.width < dp(6):
            return
        w = self._tri
        cx, cy, half, h = w.center_x, w.center_y, dp(10), dp(9)
        with w.canvas.after:
            Color(*self._tri_color)
            Line(points=[cx - half, cy - h, cx + half, cy - h, cx, cy + h],
                 width=dp(1.8), close=True, joint="round", cap="round")
            Line(points=[cx, cy - h + dp(4), cx, cy + dp(1)], width=dp(1.6), cap="round")
            Line(points=[cx, cy + dp(3), cx, cy + dp(4)], width=dp(1.8), cap="round")

    def set(self, text, level):
        self._fill.rgba = theme.hex_to_rgba(theme.COLORS[_LEVEL_FILL.get(level, "surface")])
        dark = level in ("live", "held", "info")           # dark text on light fills
        self.label.color = theme.hex_to_rgba(
            theme.COLORS["background" if dark else "text_primary"])
        self.label.text = text
        if level in ("held", "none"):
            self._tri.width = dp(26)
            self._tri_color = theme.hex_to_rgba(theme.COLORS[
                "background" if level == "held" else "text_primary"])
        else:
            self._tri.width, self._tri_color = dp(0), None
        self._draw_tri()


def _btn(text, color, on_tap):
    b = Button(text=text, bold=True, font_size="15sp", background_normal="",
               background_color=theme.hex_to_rgba(theme.COLORS[color]),
               color=theme.hex_to_rgba(theme.COLORS[
                   "background" if color != "surface" else "text_primary"]))
    b.bind(on_release=lambda *_: on_tap())
    return b


class ScanScreen(BoxLayout):
    """The single map page: node coverage + offline basemap caching + node
    PLACEMENT. Formerly two screens (SCAN + a near-identical GPS-confirm map);
    merged so there's one map that shows the mesh and starts a birth from a spot.

    ``on_place(lat, lon, source)`` fires when the operator commits a location with
    "Use this position" — the app stamps it and jumps into BIRTH."""

    def __init__(self, nodes=None, tiles=None, gps_reader=None, fix_reader=None,
                 radius_km=DEFAULT_RADIUS_KM, on_place=None, on_node_pick=None,
                 links_provider=None, suggestions_provider=None,
                 poll=True, **kwargs):
        kwargs.setdefault("orientation", "vertical")
        super().__init__(**kwargs)
        self.padding = dp(12)
        self.spacing = dp(8)
        self._gps_reader = gps_reader
        self._fix_reader = fix_reader or read_splitter_fix
        self._radius_km = radius_km
        self._on_place = on_place
        self._on_node_pick = on_node_pick
        self._links_on = False                    # mesh-lines toggle state
        self._nodes: List[dict] = []
        self._downloading = False
        # placement state — mirrors the old GPS-confirm page, now inline
        self._fix = None
        self._picked = None                       # (lat, lon) from tapping the map
        self._manual = False
        self._dl_busy = False                     # street-detail download in flight

        self._tiles = tiles if tiles is not None else find_mbtiles()
        header_row = BoxLayout(orientation="horizontal", size_hint=(1, None),
                               height=dp(30), spacing=dp(6))
        self.header = Label(halign="left", valign="middle", bold=True)
        self.header.bind(size=lambda i, v: setattr(i, "text_size", v))
        self.recenter_btn = Button(text="Recenter", size_hint=(None, 1),
                                   width=dp(100))
        self.recenter_btn.bind(on_release=lambda *_: self._recenter())
        # Mesh-lines toggle: draw the who-hears-whom connection lines. Default OFF;
        # does nothing visible unless a links_provider was wired.
        self.links_btn = Button(text="Links  off", size_hint=(None, 1), width=dp(92))
        self.links_btn.bind(on_release=lambda *_: self._toggle_links())
        header_row.add_widget(self.header)
        header_row.add_widget(self.links_btn)
        header_row.add_widget(self.recenter_btn)
        self.add_widget(header_row)

        # Interactive map: pan/pinch/double-tap to zoom, and a stationary TAP drops
        # the placement pin. Explicit +/- overlay so zoom never depends on the
        # panel's (unreliable) pinch.
        map_wrap = FloatLayout(size_hint_y=1)
        # pos_hint is REQUIRED: a FloatLayout only repositions children that carry a
        # pos_hint — a size_hint alone stretches the plot to fill the container but
        # leaves its pos stuck at (0,0) = the screen's bottom-left, so it drew
        # anchored to the screen bottom (behind the badge) and left the top of the
        # pane black. Pinning {x:0, y:0} makes it fill the container properly.
        self.plot = MapPlot(tiles=self._tiles, interactive=True,
                            on_pick=self._on_map_pick if on_place is not None else None,
                            on_node_pick=self._on_node_pick,
                            links_provider=links_provider,
                            suggestions_provider=suggestions_provider,
                            size_hint=(1, 1), pos_hint={"x": 0, "y": 0})
        map_wrap.add_widget(self.plot)
        zbox = BoxLayout(orientation="vertical", size_hint=(None, None),
                         size=(dp(50), dp(104)), spacing=dp(6),
                         pos_hint={"right": 0.98, "top": 0.98})
        for sym, d in (("+", +1), ("−", -1)):
            zb = Button(text=sym, font_size="26sp", bold=True, background_normal="",
                        background_color=theme.hex_to_rgba(theme.COLORS["surface"], 0.92),
                        color=theme.hex_to_rgba(theme.COLORS["text_primary"]))
            zb.bind(on_release=lambda _b, dd=d: self.plot.zoom_by(dd))
            zbox.add_widget(zb)
        map_wrap.add_widget(zbox)
        self.add_widget(map_wrap)

        # --- placement bar (only when this screen can start a birth) ----------
        if on_place is not None:
            self.badge = _FixBadge()
            self.add_widget(self.badge)
            self.coords = Label(text="", font_size="14sp", halign="left",
                                valign="middle", size_hint=(1, None), height=dp(22),
                                color=theme.hex_to_rgba(theme.COLORS["text_primary"]))
            self.coords.bind(size=lambda i, v: setattr(i, "text_size", v))
            self.add_widget(self.coords)

            act = BoxLayout(orientation="horizontal", size_hint=(1, None),
                            height=dp(50), spacing=dp(8))
            self.confirm_btn = _btn("Use this position  →", "green", self._use_position)
            self.confirm_btn.disabled = True
            act.add_widget(self.confirm_btn)
            act.add_widget(_btn("Enter manually", "surface", self._toggle_manual))
            self.add_widget(act)

            self.detail_btn = Button(
                text="Load street names for this spot  (needs WiFi)",
                size_hint=(1, None), height=dp(38), font_size="13.5sp", bold=True,
                background_normal="",
                background_color=theme.hex_to_rgba(theme.COLORS["accent"]),
                color=theme.hex_to_rgba(theme.COLORS["background"]))
            self.detail_btn.bind(on_release=lambda *_: self._load_detail())
            self.add_widget(self.detail_btn)

            # Manual entry: an address (geocoded) OR raw lat/lon — collapsed until asked.
            self.manual_row = BoxLayout(orientation="vertical", size_hint=(1, None),
                                        height=dp(0), spacing=dp(6), opacity=0)
            addr_row = BoxLayout(orientation="horizontal", size_hint_y=None,
                                 height=dp(44), spacing=dp(6))
            self.addr_in = TextInput(hint_text="street address  (needs internet)",
                                     multiline=False, font_size="15sp")
            bind_field(self.addr_in)
            find_btn = Button(text="Find", size_hint_x=None, width=dp(84), bold=True,
                              background_normal="",
                              background_color=theme.hex_to_rgba(theme.COLORS["accent"]),
                              color=theme.hex_to_rgba(theme.COLORS["background"]))
            find_btn.bind(on_release=lambda *_: self._find_address())
            addr_row.add_widget(self.addr_in)
            addr_row.add_widget(find_btn)
            coord_row = BoxLayout(orientation="horizontal", size_hint_y=None,
                                  height=dp(44), spacing=dp(6))
            self.lat_in = TextInput(hint_text="latitude", multiline=False,
                                    input_filter="float", font_size="16sp")
            self.lon_in = TextInput(hint_text="longitude", multiline=False,
                                    input_filter="float", font_size="16sp")
            bind_field(self.lat_in, numeric=True)
            bind_field(self.lon_in, numeric=True)
            coord_row.add_widget(self.lat_in)
            coord_row.add_widget(self.lon_in)
            self.manual_row.add_widget(addr_row)
            self.manual_row.add_widget(coord_row)
            self.add_widget(self.manual_row)

        # Coverage note — collapses to nothing when empty so it never leaves a gap.
        self.note = Label(text="", size_hint=(1, None), height=dp(0),
                          halign="left", valign="middle",
                          color=theme.status_rgba("warn", 0.9))
        self.note.bind(size=lambda i, v: setattr(i, "text_size", v))
        self.add_widget(self.note)

        # Offline-map caching is MAINTENANCE, not the primary flow — tuck it behind
        # a toggle so the map + placement own the screen (was crowding both out).
        self._offline_open = False
        self.offline_toggle = Button(text="Offline maps  ▾", size_hint=(1, None),
                                     height=dp(34), font_size="13sp", bold=True,
                                     background_normal="",
                                     background_color=theme.hex_to_rgba(theme.COLORS["surface"]),
                                     color=theme.hex_to_rgba(theme.COLORS["text_secondary"]))
        self.offline_toggle.bind(on_release=lambda *_: self._toggle_offline())
        self.add_widget(self.offline_toggle)

        self._offline_panel = BoxLayout(orientation="vertical", size_hint=(1, None),
                                        height=0, opacity=0, spacing=dp(6))
        self._offline_panel.bind(minimum_height=lambda *_: self._sync_offline_height())
        # [-] radius stepper [+] around the download button
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
        self._offline_panel.add_widget(row)

        self.dl_status = Label(text="", halign="left", valign="middle",
                               size_hint=(1, None), height=dp(26),
                               color=theme.status_rgba("unknown", 0.95))
        self.dl_status.bind(size=lambda i, v: setattr(i, "text_size", v))
        self._offline_panel.add_widget(self.dl_status)

        # Basemap attribution (a licence condition) — small dim line inside the panel.
        self.attribution = Label(text="", size_hint=(1, None), height=dp(15),
                                 halign="right", valign="middle", font_size="10sp",
                                 color=theme.hex_to_rgba(theme.COLORS["text_secondary"], 0.6))
        self.attribution.bind(size=lambda i, v: setattr(i, "text_size", v))
        self._offline_panel.add_widget(self.attribution)

        # Home-base coordinate — LAST-resort download centre, hidden unless
        # self-location fails (distinct from the placement manual-entry row above).
        self.center_input = TextInput(
            hint_text="Couldn't find your location - type home base as: "
                      "lat, lon  (e.g. -37.79, 144.96)",
            multiline=False, size_hint=(1, None), height=0, opacity=0)
        self.center_input.bind(text=lambda *_: self._refresh_estimate())
        self._offline_panel.add_widget(self.center_input)
        self.add_widget(self._offline_panel)

        self._refresh_header()
        self.set_nodes(nodes or [])
        self._refresh_estimate()

        # Self-locate in the background (IP geolocation — city-level is plenty
        # for a map radius, and downloads need internet anyway). The user just
        # presses download; typing coordinates is the fallback of last resort.
        self._ip_center = None            # (lat, lon, place) once found
        self._ip_tried = False
        threading.Thread(target=self._locate_self, daemon=True).start()

        # Live "you are here" + placement badge: poll the Tracker's fix, mark it on
        # the map, and (in placement mode) keep the fix-trust badge current.
        if poll:
            self._poll_gps(0)
            Clock.schedule_interval(self._poll_gps, 3)

    def _poll_gps(self, _dt):
        # Prefer the full fix (has trust/source) so the badge and marker agree; fall
        # back to the coords-only reader for the marker if that's all we were given.
        fix = None
        try:
            fix = self._fix_reader() if self._fix_reader else None
        except Exception:
            fix = None
        if fix is not None and getattr(fix, "has_fix", False):
            self.plot.set_me((fix.lat, fix.lon))
        else:
            try:
                self.plot.set_me(self._gps_reader() if self._gps_reader else None)
            except Exception:
                self.plot.set_me(None)
        # Badge only exists in placement mode, and only while the operator hasn't
        # overridden the live fix with a map tap / manual entry.
        if getattr(self, "_on_place", None) is not None and not self._picked and not self._manual:
            self._fix = fix
            self._show_live_badge()

    def show_location(self, lat, lon):
        """Centre the map on a node's coordinates — used by 'See on map' from a
        certificate. The node's own dot is already drawn there; the live GPS pin
        stays put so the operator sees where they are relative to it."""
        self.plot.center_on((lat, lon))

    # -- offline-map panel (collapsible) -----------------------------------
    def _toggle_offline(self):
        self._offline_open = not self._offline_open
        self._offline_panel.opacity = 1 if self._offline_open else 0
        self.offline_toggle.text = ("Offline maps  ▲" if self._offline_open
                                    else "Offline maps  ▾")
        self._sync_offline_height()

    def _sync_offline_height(self):
        self._offline_panel.height = (self._offline_panel.minimum_height
                                      if self._offline_open else 0)

    def _toggle_links(self):
        """Flip the mesh connection lines on/off (header button)."""
        self._links_on = not self._links_on
        self.plot.set_show_links(self._links_on)
        self.links_btn.text = "Links  on" if self._links_on else "Links  off"

    # -- placement ----------------------------------------------------------
    def _recenter(self):
        """Snap the view back to the auto-fit AND drop any map-tap/manual override,
        so the placement badge returns to tracking the live GPS fix."""
        self.plot.reset_view()
        if getattr(self, "_on_place", None) is not None:
            self._picked = None
            if self._manual:
                self._manual = False
                self.manual_row.height, self.manual_row.opacity = dp(0), 0
            self._show_live_badge()

    def _show_live_badge(self):
        """Reflect the live/held/none GPS fix in the badge + coords, without ever
        hijacking the operator's pan/zoom (unlike the old confirm page, which
        re-centred on every poll)."""
        t = fix_trust(self._fix)
        self.badge.set(t["title"], t["level"])
        hint = t["detail"]
        if t["level"] != "live":
            hint = "Tap the map to drop the pin, or " + hint[0].lower() + hint[1:]
        if self._fix is not None and getattr(self._fix, "has_fix", False):
            self.coords.text = f"{self._fix.lat:.6f},  {self._fix.lon:.6f}   ·   {hint}"
            self.confirm_btn.disabled = False
        else:
            self.coords.text = hint
            self.confirm_btn.disabled = True

    def _on_map_pick(self, latlon):
        """Operator tapped the map to set the location (no GPS/internet needed).
        The pin already moved; adopt the point."""
        self._manual = False
        self._picked = latlon
        self.badge.set("Picked from map", "info")
        self.coords.text = (f"{latlon[0]:.6f},  {latlon[1]:.6f}   ·   tap again to move, "
                            "or Recenter to go back to GPS")
        self.confirm_btn.disabled = False

    def _current_point(self):
        """The (lat, lon, source) the operator is committing — manual > map tap >
        GPS fix, in that priority. None if nothing is set."""
        if self._manual:
            try:
                return (float(self.lat_in.text), float(self.lon_in.text), "manual")
            except ValueError:
                return None
        if self._picked is not None:
            return (self._picked[0], self._picked[1], "map")
        if self._fix is not None and getattr(self._fix, "has_fix", False):
            return (self._fix.lat, self._fix.lon, self._fix.source)
        return None

    def _use_position(self):
        pt = self._current_point()
        if pt is None:
            self.badge.set("Set a location first — tap the map or enter it", "none")
            return
        if self._on_place:
            self._on_place(pt[0], pt[1], pt[2])

    def _toggle_manual(self):
        self._manual = not self._manual
        if self._manual:
            self._picked = None
            self.manual_row.height, self.manual_row.opacity = dp(96), 1
            self.badge.set("Enter a location", "info")
            self.coords.text = ("Type an address and Find (needs internet), or enter "
                                "lat/lon directly, then Use this position.")
            self.confirm_btn.disabled = False
            if self._fix is not None and getattr(self._fix, "has_fix", False):
                self.lat_in.text = f"{self._fix.lat:.6f}"
                self.lon_in.text = f"{self._fix.lon:.6f}"
        else:
            self.manual_row.height, self.manual_row.opacity = dp(0), 0
            self._show_live_badge()

    def _find_address(self):
        """Geocode the typed address (off-thread) and drop the pin to verify it."""
        addr = self.addr_in.text.strip()
        if not addr:
            self.badge.set("Type an address first, then Find", "info")
            return
        self.badge.set("Looking up address…", "info")

        def work():
            res = geocode_address(addr)
            Clock.schedule_once(lambda dt: self._apply_geocode(res), 0)
        threading.Thread(target=work, daemon=True).start()

    def _apply_geocode(self, res):
        if not res:
            self.badge.set("Address not found (no internet?) — enter lat/lon", "none")
            return
        self.lat_in.text = f"{res['lat']:.6f}"
        self.lon_in.text = f"{res['lon']:.6f}"
        self.badge.set("Found — check the pin sits right", "info")
        self.coords.text = res["name"][:120]
        self.plot.focus((res["lat"], res["lon"]))

    def _load_detail(self):
        """Cache street-level tiles (with names) for the current spot, over WiFi."""
        if self._dl_busy:
            return
        pt = self._current_point()
        if pt is None:
            self.badge.set("Pick or find a location first, then load its streets", "info")
            return
        if not is_online():
            self.badge.set("No internet — join WiFi to load street names", "none")
            return
        self._dl_busy = True
        self.detail_btn.disabled = True
        self.detail_btn.text = "Downloading street detail…"
        lat, lon = pt[0], pt[1]
        dest = os.path.join(MAPS_DIR, "offline.mbtiles")
        os.makedirs(MAPS_DIR, exist_ok=True)

        def prog(s):
            if "done" in s and "total" in s:
                Clock.schedule_once(lambda dt: setattr(
                    self.detail_btn, "text",
                    f"Street detail… {s['done']}/{s['total']} tiles"), 0)

        def work():
            summary = add_point_detail(lat, lon, dest, radius_km=SPOT_RADIUS_KM,
                                       zmin=SPOT_MIN_ZOOM, zmax=SPOT_MAX_ZOOM,
                                       on_progress=prog)
            Clock.schedule_once(lambda dt: self._detail_done(summary, (lat, lon)), 0)
        threading.Thread(target=work, daemon=True).start()

    def _detail_done(self, summary, pt):
        self._dl_busy = False
        self.detail_btn.disabled = False
        self.detail_btn.text = "Load street names for this spot  (needs WiFi)"
        self._tiles = find_mbtiles()
        self.plot.set_tiles(self._tiles)
        self.plot.focus(pt, zoom=17)                # land close; +/- to fine-tune
        if summary.get("blocked"):
            self.badge.set("Map server is rate-limiting — try again shortly", "none")
        elif summary.get("fetched") or summary.get("skipped"):
            self.badge.set("Street detail loaded — use +/− to zoom in", "info")
        else:
            self.badge.set("Couldn't fetch detail (check the connection)", "none")

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
        # Keep the header a clean one-liner. Basemap attribution is a licence
        # condition, so it lives in its own small footer (self.attribution) where
        # it's readable, rather than crammed into the header where it wrapped/cut.
        self.header.text = "Map — coverage & placement"
        self.attribution.text = ATTRIBUTION if self._tiles is not None else ""

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
        self.note.height = dp(24) if self.note.text else dp(0)   # no empty gap

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
