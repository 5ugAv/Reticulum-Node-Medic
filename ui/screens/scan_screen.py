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
import traceback
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

from monitor.geo import (
    read_gps, read_splitter_fix, fix_trust, geocode_address, accuracy_label,
)
from ui import theme
from ui.i18n import tr  # i18n: wrapped — SCAN controls/labels/status messages
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
    download_node_details, download_terrain, estimate_download,
    estimate_world, is_online,
    storage_summary, disk_free_mb, parse_latlon, ip_geolocate,
    add_point_detail, SPOT_MIN_ZOOM, SPOT_MAX_ZOOM, SPOT_RADIUS_KM)


#: A pan / pinch / tap must NEVER crash the whole app — Kivy re-raises exceptions
#: from touch handlers by default, so one bad delta while sliding the map takes
#: the UI down (and the auto-restart truncates ui.log, losing the trace). We wrap
#: the interactive handlers and route any error here: append the traceback to a
#: PERSISTENT file (survives restarts, so the elusive map crash finally leaves
#: evidence) and carry on — Recenter recovers the view.
_MAP_ERR_LOG = os.path.join(os.path.expanduser("~"), ".nodemedic-map-errors.log")


def _record_map_error(where):
    try:
        with open(_MAP_ERR_LOG, "a") as f:
            f.write("--- map touch error in %s ---\n" % where)
            traceback.print_exc(file=f)
            f.write("\n")
    except Exception:
        pass
    try:
        traceback.print_exc()          # also to stderr / ui.log for a live tail
    except Exception:
        pass


# ---- pure helpers (unit-tested; no Kivy) -------------------------------------

def link_segments(topo, transports=None):
    """Flatten a ``monitor.topology.Topology`` into who-hears-whom LINE SEGMENTS
    between LOCATED nodes: ``[(lat1, lon1, lat2, lon2, transport, rssi), ...]``. Only
    edges whose BOTH endpoints have coordinates draw a line; endpoint order is
    normalised so an A-B / B-A pair collapses to one segment. *transports*
    filters (None = all): LoRa is the map's standard view, everything else an
    overlay the operator can switch off (2026-08-13 — the old screen drew
    Wi-Fi RSSI as if it were LoRa strength, which is exactly the wrong signal
    for placing nodes). Pure — the app wraps this in a ``links_provider``."""
    by_id = {n.id: n for n in getattr(topo, "nodes", [])}
    seen = set()
    out = []
    for e in getattr(topo, "edges", []):
        t = getattr(e, "transport", "unknown")
        if transports is not None and t not in transports:
            continue
        a, b = by_id.get(e.a), by_id.get(e.b)
        if a is None or b is None:
            continue
        if None in (a.lat, a.lon, b.lat, b.lon):
            continue
        key = tuple(sorted(((a.lat, a.lon), (b.lat, b.lon)))) + (t,)
        if key in seen:
            continue
        seen.add(key)
        # the edge's heard RSSI rides along so the map can draw thickness =
        # strength (edge_width was built for exactly this and sat unwired
        # until 2026-08-27); None = path-implied, minimum weight.
        out.append((a.lat, a.lon, b.lat, b.lon, t,
                    getattr(e, "rssi", None)))
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
        marker = {"lat": lat, "lon": lon, "reason": reason, "kind": kind}
        if isinstance(s, dict):
            # A recommendation marker arrives already carrying its §3.5
            # rationale (tier/links/cost/...). Re-normalising must not strip
            # it back to four keys — the tap popup reads these fields.
            for k, v in s.items():
                marker.setdefault(k, v)
        out.append(marker)
    return out


def recommendation_markers(recs, positions=None):
    """Normalise ``monitor.synapse_recommend.Recommendation`` objects into the
    pin-marker dicts MapPlot already draws and hit-tests — the same plumbing,
    now carrying the full rationale. A mast raise has no coordinates of its
    own; *positions* (``{node_id: (lat, lon)}``) places it AT the kin node it
    names, and a raise whose node is unlocated stays off the map (it still
    appears in the Build next panel). Pure."""
    out, seen = [], set()
    for r in recs or []:
        action = getattr(r, "action", "new_node")
        lat, lon = getattr(r, "lat", None), getattr(r, "lon", None)
        if action == "raise_antenna" and (lat is None or lon is None):
            pos = (positions or {}).get(getattr(r, "node", None))
            if not pos or pos[0] is None or pos[1] is None:
                continue
            lat, lon = pos
        if lat is None or lon is None:
            continue
        key = (round(float(lat), 6), round(float(lon), 6), action)
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "lat": lat, "lon": lon, "kind": action, "action": action,
            "reason": getattr(r, "summary", "") or "",
            "node": getattr(r, "node", None),
            "height_m": getattr(r, "height_m", None),
            "predicted_gain_db": getattr(r, "predicted_gain_db", None),
            "tier": getattr(r, "tier", None),
            "tier_why": getattr(r, "tier_why", ""),
            "tier_checked": getattr(r, "tier_checked", True),
            "resolves": list(getattr(r, "resolves", []) or []),
            "links": [{"name": l.name, "km": l.km, "margin_km": l.margin_km,
                       "source": l.source, "confidence": l.confidence,
                       "viable": l.viable}
                      for l in getattr(r, "predicted_links", []) or []],
            "cost_note": getattr(r, "cost_note", ""),
            "alternatives": [a.summary for a in
                             getattr(r, "alternative_actions", []) or []],
            "cautions": list(getattr(r, "cautions", []) or []),
        })
    return out


def rationale_text(marker):
    """The tap popup's body: the engine's rationale as plain lines — tier and
    why (with "could not check" kept visible), what it fixes, each predicted
    link with its distance, margin and SOURCE, the cost note, cheaper
    alternatives, cautions. Pure text; the popup just shows it."""
    m = marker or {}
    lines = []
    if m.get("action") == "raise_antenna":
        if m.get("reason"):
            lines.append(m["reason"])
        h, g = m.get("height_m"), m.get("predicted_gain_db")
        if h is not None and g is not None:
            # metres and decibels on one labelled line each side — a 10 and
            # a 10 must never read as the same number
            lines.append(f"Mast: +{h:g} m.  Predicted gain: {g:g} dB.")
    else:
        if m.get("tier"):
            suffix = "" if m.get("tier_checked", True) \
                else "  (could not check)"
            lines.append(f"Tier: {m['tier']}{suffix}")
        if m.get("tier_why"):
            lines.append(m["tier_why"])
    if m.get("resolves"):
        lines.append(tr("Fixes:"))
        lines.extend(f"- {x}" for x in m["resolves"])
    if m.get("links"):
        lines.append(tr("Predicted links:"))
        for l in m["links"]:
            lines.append(f"- {l.get('name')}: {l.get('km'):g} km, margin "
                         f"{l.get('margin_km'):+g} km ({l.get('source')}, "
                         f"{l.get('confidence')})")
    if m.get("cost_note"):
        lines.append(m["cost_note"])
    if m.get("alternatives"):
        lines.append(tr("Cheaper first:"))
        lines.extend(f"- {x}" for x in m["alternatives"])
    lines.extend(m.get("cautions") or [])
    return "\n".join(lines)


def build_next_lines(markers, limit=3):
    """The Build next panel's rows: at most *limit* recommendations, one line
    each — action + place/node + the most important reason. The markers
    arrive ranked (severity first, cost second), so the top three are simply
    the first three. Pure."""
    out = []
    for m in (markers or [])[:limit]:
        if m.get("action") == "raise_antenna":
            out.append((m.get("reason")
                        or tr("Raise an antenna")).split("\n")[0])
            continue
        links = m.get("links") or []
        if links:
            place = f"near {links[0].get('name')}"
        elif m.get("lat") is not None:
            place = f"at ({m['lat']:.3f}, {m['lon']:.3f})"
        else:
            place = tr("(no located spot)")
        why = (m.get("resolves") or [m.get("reason") or ""])[0]
        tier = m.get("tier") or "transport"
        out.append(f"New {tier} node {place} - {why}".split("\n")[0])
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
        # Tiles KNOWN to be absent (z,x,y). The cache above only remembers HITS;
        # without this, every missing tile re-ran a SQLite query on EVERY redraw,
        # and _draw_tile's overzoom walk multiplies that by the zoom depth — a pan
        # over sparse high-zoom area became thousands of queries/sec (100% CPU
        # freeze). Cleared by set_tiles() when new tiles are downloaded.
        self._tile_misses = set()
        self._me = None                          # medic's own GPS fix (lat, lon)
        self._labels: List[Label] = []
        # interactive view state (None until the user pans/zooms = auto-fit)
        self._center = None                      # (lat, lon)
        self._zoom = None
        self._touches = {}                       # touch uid -> last (x, y)
        self._primary = None                     # the finger that drives a pan
        self._pinch_base = None                  # two-finger start distance
        self._trigger = Clock.create_trigger(self._redraw, 0.05)
        # Route size/pos through the DEBOUNCED trigger, not a synchronous _redraw:
        # the on-screen keyboard pans/resizes the ScreenManager, and a direct
        # size->_redraw that itself nudges layout can re-fire without end (a 100%
        # CPU redraw storm — the map-placement freeze). The trigger coalesces a
        # burst of changes into ONE redraw next frame, so it can't spin.
        self.bind(size=self._trigger, pos=self._trigger)

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
        try:
            if touch.is_double_tap:               # double-tap = zoom in on the spot
                self._zoom_at(touch.pos, +1)
                return True
            touch.grab(self)
            self._touches[touch.uid] = touch.pos
            if self._primary is None:             # first finger drives the pan
                self._primary = touch.uid
            if self._is_pinch():
                self._pinch_base = self._touch_sep()
        except Exception:
            _record_map_error("on_touch_down")
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
        try:
            self._touches[touch.uid] = touch.pos
            view = self._current_view()
            if view is None:
                return True
            if self._is_pinch():                 # two fingers apart = zoom
                if self._pinch_base is None:
                    self._pinch_base = self._touch_sep()
                dist = max(1.0, self._touch_sep())
                ratio = dist / self._pinch_base
                if ratio > PINCH_STEP or ratio < 1.0 / PINCH_STEP:
                    self._step_zoom(+1 if ratio > 1.0 else -1, view)
                    self._pinch_base = dist      # re-arm for the next step
            elif touch.uid == self._primary:     # one finger (its moves) = pan
                # Ignore the panel's phantom second contact: only the primary
                # finger drives the pan, so one physical drag = one pan (not
                # doubled).
                from ui.map_tiles import project_px, unproject_px
                # Accumulate on the PERSISTENT centre (redraws are throttled, so
                # several moves share one stale view — deriving each from it drops
                # every delta but the last, the jumpy drag).
                z = self._zoom if self._zoom is not None else view.zoom
                clat, clon = self._center_latlon(view)
                cx, cy = project_px(clat, clon, z)
                cx -= touch.dx
                cy += touch.dy                   # kivy y-up vs world y-down
                self._center = self._clamp_center(*unproject_px(cx, cy, z))
                self._zoom = z
                self._trigger()
        except Exception:
            _record_map_error("on_touch_move")
        return True

    def on_touch_up(self, touch):
        if touch.grab_current is self:
            try:
                touch.ungrab(self)
                self._touches.pop(touch.uid, None)
                if touch.uid == self._primary:   # promote a remaining finger
                    self._primary = next(iter(self._touches), None)
                if len(self._touches) < 2:
                    self._pinch_base = None
                # A stationary tap (not a drag/pinch) on an INTERACTIVE map: if it
                # landed ON a node dot, open that node; otherwise drop the
                # placement pin. Tap-a-node and tap-to-place coexist with pan/zoom.
                moved = abs(touch.x - touch.ox) + abs(touch.y - touch.oy) > dp(10)
                if (not moved and not self._touches and not touch.is_double_tap
                        and self._last_view is not None and self.collide_point(*touch.pos)):
                    node = self._node_at(touch.x, touch.y)
                    sugg = None if node is not None else self._suggestion_at(touch.x, touch.y)
                    if node is not None and self._on_node_pick:
                        self._on_node_pick(node)
                    elif sugg is not None:        # tapped an 'add a node here' ring
                        self._show_suggestion(sugg)
                    elif self._on_pick:
                        latlon = self._last_view.to_latlon(touch.x - self.x, touch.y - self.y)
                        self._me = latlon
                        self._trigger()
                        self._on_pick(latlon)
            except Exception:
                _record_map_error("on_touch_up")
            return True
        # Tap-to-place: on a non-interactive map with a pick handler (the GPS-
        # confirm screen), a tap drops the pin at that spot — offline location entry.
        try:
            if (self._on_pick and self._last_view is not None
                    and not self._touches and self.collide_point(*touch.pos)):
                latlon = self._last_view.to_latlon(touch.x - self.x, touch.y - self.y)
                self._me = latlon
                self._trigger()                  # move the pin to the tapped point
                self._on_pick(latlon)
                return True
        except Exception:
            _record_map_error("on_touch_up")
        return super().on_touch_up(touch)

    # -- optional overlays: mesh lines + placement suggestions --------------

    def set_terrain(self, store):
        """Shade high ground light and low ground dark, or None to turn it off.

        Drawn UNDER the nodes and links: it is context for reading them, not a
        thing to read on its own. Sampled on a coarse grid rather than
        per-pixel — this is a 5" panel on a Pi, and the map already has one
        documented redraw-storm in its history.
        """
        self._terrain = store
        self._terrain_grid = None                # recomputed on the next redraw
        self._redraw()

    def _draw_terrain(self, view):
        """A hypsometric wash over the visible area.

        The scale is stretched to the RANGE ACTUALLY IN VIEW, not to absolute
        altitude, because what helps a radio is standing above its surroundings.
        A 40 m rise in a flat suburb has to read as strongly as a peak does in a
        mountain range, or the overlay tells a Sampleton operator nothing.
        """
        store = getattr(self, "_terrain", None)
        if store is None:
            return
        from monitor.terrain import elevation_colour
        STEP = dp(18)                            # coarse: legibility, not detail
        cols = max(2, int(self.width / STEP))
        rows = max(2, int(self.height / STEP))
        cw, ch = self.width / cols, self.height / rows
        grid, lo, hi = [], None, None
        for i in range(cols):
            for j in range(rows):
                sx = (i + 0.5) * cw
                sy = (j + 0.5) * ch
                try:
                    lat, lon = view.to_latlon(sx, sy)
                except Exception:
                    continue
                m = store.elevation(lat, lon)
                if m is None:
                    continue
                grid.append((i, j, m))
                lo = m if lo is None else min(lo, m)
                hi = m if hi is None else max(hi, m)
        if not grid or lo is None or hi is None:
            return
        for i, j, m in grid:
            r, g, b = elevation_colour(m, lo, hi)
            Color(r / 255.0, g / 255.0, b / 255.0, 0.45)
            Rectangle(pos=(self.x + i * cw, self.y + j * ch), size=(cw, ch))

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

    #: One colour per transport, so a line's hue says what carried it. LoRa
    #: keeps the accent (the standard view); the overlays each get their own
    #: lane; "unknown"/"local" draw with LoRa (path-implied mesh links).
    LINK_COLOURS = {"lora": "accent", "unknown": "accent", "local": "accent",
                    "wifi": "link_wifi", "internet": "link_internet",
                    "bluetooth": "link_bt"}

    def _draw_links(self, view):
        """Connection lines UNDER the node dots, coloured by transport —
        drawn inside an open canvas context by _draw_tiled."""
        segs = self._fetch_links()
        if not segs:
            return
        from monitor.topology import edge_width
        for seg in segs:
            try:
                lat1, lon1, lat2, lon2 = seg[0], seg[1], seg[2], seg[3]
                t = seg[4] if len(seg) > 4 else "unknown"
                rssi = seg[5] if len(seg) > 5 else None
            except (TypeError, ValueError, IndexError):
                continue
            # thickness = heard strength (operator vision: "line THICKNESS =
            # connection strength"); a strong link also draws a little more
            # opaque so weight reads even when zoomed out.
            w = edge_width(rssi)
            Color(*theme.hex_to_rgba(
                theme.COLORS[self.LINK_COLOURS.get(t, "accent")],
                0.30 + 0.08 * (w - 1.0)))
            x1, y1 = view.to_screen(lat1, lon1)
            x2, y2 = view.to_screen(lat2, lon2)
            Line(points=[self.x + x1, self.y + y1, self.x + x2, self.y + y2],
                 width=max(1.0, dp(0.45) * w))

    def _draw_suggestions(self, view):
        """A hollow accent ring + small '+' at each placement suggestion —
        'add a node here'. Cached in self._suggestions for tap hit-testing."""
        self._suggestions = self._fetch_suggestions()
        r = dp(9)
        for s in self._suggestions:
            sx, sy = view.to_screen(s["lat"], s["lon"])
            cx, cy = self.x + sx, self.y + sy
            Color(*theme.hex_to_rgba(theme.COLORS["accent"], 0.95))
            if s.get("action") == "raise_antenna":
                # A small up-arrow AT the kin node: raise what already
                # stands here. Distinct from the new-node ring — same accent
                # (both are the engine's suggestions), different shape.
                Line(points=[cx, cy - r * 0.7, cx, cy + r * 0.7], width=1.6)
                Line(points=[cx - r * 0.45, cy + r * 0.1,
                             cx, cy + r * 0.7,
                             cx + r * 0.45, cy + r * 0.1], width=1.6)
                continue
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
        """Pop the suggestion's story when its marker is tapped. A
        recommender pin (it carries a tier or is a mast raise) opens the full
        rationale — tier and why, fixes, predicted links with their sources,
        cost, alternatives, cautions (rationale_text). A plain placement
        suggestion keeps the old one-line reason."""
        from kivy.uix.popup import Popup
        if sugg.get("tier") is not None or sugg.get("action") == "raise_antenna":
            title = (tr("Raise this antenna")
                     if sugg.get("action") == "raise_antenna"
                     else tr("Add a node here"))
            text = rationale_text(sugg) or tr("Suggested node location")
            body = Label(text=text, halign="left", valign="top",
                         padding=(dp(12), dp(12)))
            body.bind(size=lambda i, v: setattr(i, "text_size", v))
            Popup(title=title, content=body, size_hint=(0.9, 0.7)).open()
            return
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

    #: How far interactive zoom may pass the cached edge. The drawer
    #: overzooms from the nearest cached ancestor ("blurry beats black"),
    #: so these levels render soft where detail tiles are absent — but a
    #: pin cannot be placed on a suburb from a state view (operator,
    #: 2026-08-14, on the prelude map with only the world basemap cached).
    OVERZOOM_MAX = 16

    def _step_to_next_zoom(self, current, direction):
        from ui.map_tiles import step_zoom
        # with no cache, fall back to the full interactive range
        zs = self._zooms or list(range(2, DETAIL_MAX_ZOOM + 1))
        nxt = step_zoom(zs, current, direction)
        if direction > 0 and nxt == current and current < self.OVERZOOM_MAX:
            return current + 1        # past the cached edge: overzoom renders it
        return nxt

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
        self._tile_misses = set()                 # and any remembered absences
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
        if key in self._tile_misses:              # known-absent: skip the SQLite hit
            return None
        data = self._tiles.get_tile(z, x, y) if self._tiles is not None else None
        if not data:
            self._tile_misses.add(key)            # remember the miss (see __init__)
            return None
        try:
            # sniff the actual format: the world tier is JPEG since the Esri
            # provider swap (2026-08-27), older region tiles are PNG, and one
            # mbtiles can hold both — ext must follow the bytes, not a guess.
            ext = "jpeg" if data[:3] == b"\xff\xd8\xff" else "png"
            tex = CoreImage(io.BytesIO(data), ext=ext).texture
        except Exception:
            self._tile_misses.add(key)            # undecodable -> treat as absent
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
            # THE SOURCE TEXTURE'S OWN SIZE — NOT TILE_SIZE. TILE_SIZE (512)
            # is a DRAWING choice ("2x display: labels legible on the HiDPI 5"
            # panel"); the decoded tile pixel data is 256x256, whatever this
            # mbtiles actually stores (checked by hand: dumped the exact bytes
            # DireWolf... no — dumped the exact ancestor tile this bug hit,
            # decoded it in Kivy, tex.size reported (256, 256)). Every
            # get_region() call below had been computed against 512 and
            # clamped into a texture that is only 256px wide — so even the
            # FIRST ancestor level (k=1) asked for regions starting at x=256
            # on a 256px-wide texture, entirely past its edge, every time.
            #
            # The first fix (2026-09-05) caught the case where the request
            # went past a 256px ceiling and clamped it — using the WRONG
            # ceiling throughout, so it clamped requests INTO the wrong
            # region instead of out of the texture, which is why the operator
            # kept seeing smeared, banded map previews after that fix shipped
            # (2026-09-06, live, with photos: correct-looking tiles only where
            # an EXACT zoom match existed and no get_region() ever ran).
            # Reading the real size makes this correct for whatever an mbtiles
            # actually stores, not just whatever TILE_SIZE happens to be today.
            src = atex.width
            scale = src / float(cells)
            sub = max(1, int(scale))
            rx = max(0, min(src - sub, int(col * scale)))
            top = int(row * scale)
            ry = max(0, min(src - sub, src - top - sub))
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

    def set_walk_trail(self, points):
        """The boundary walk's ping history: [{lat, lon, connected}]. [] ends
        the overlay. Redraw is triggered; the walk owns the cadence."""
        self._walk_trail = list(points or ())
        self._trigger()

    def _draw_tiled(self, pts, bbox, fill=None):
        from ui.map_tiles import view_at
        if self._center is not None and self._zoom is not None:
            # A manual zoom BELOW the cached edge snaps to a level the cache
            # has (no mid-range gaps); ABOVE it, it stands — the per-tile
            # drawer overzooms from the nearest ancestor, so the pane blurs
            # instead of blanking (and instead of snapping the operator back
            # to state level the moment they zoomed past the basemap).
            z = self._zoom
            if not self._zooms or z <= self._zooms[-1]:
                z = self._snap_zoom(z)
            else:
                z = min(z, self.OVERZOOM_MAX)
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
            self._draw_terrain(view)              # high/low wash UNDER everything
            # BOUNDARY WALK TRAIL (operator spec 2026-08-13, built
            # 2026-09-15): every ping the walk has taken, where it happened —
            # green answered, red silent. Drawn over tiles, under nodes, so
            # the trail reads as history beneath the live mesh.
            for wp in getattr(self, "_walk_trail", ()):
                if wp.get("lat") is None:
                    continue
                from ui.map_tiles import project_px
                wx, wy = project_px(wp["lat"], wp["lon"], view.zoom)
                sx = self.x + (wx - view.off_x)
                sy = self.y + view.height - (wy - view.off_y)
                good = wp.get("connected")
                Color(*((0.24, 0.78, 0.35, 0.95) if good
                        else (0.86, 0.16, 0.12, 0.95)))
                d = dp(7) if good else dp(9)
                Ellipse(pos=(sx - d / 2, sy - d / 2), size=(d, d))
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
        # The DOT carries health (drawn above, from point.status) — the name
        # does not need to say it twice, and saying it in status green made the
        # name unreadable on the basemap. Dark blue: legible over map tiles,
        # and it leaves colour meaning ONE thing on this screen.
        lbl = Label(text=point.label, font_size=dp(15), bold=True,
                    color=theme.hex_to_rgba(theme.COLORS["map_label"]),
                    size_hint=(None, None))
        lbl.texture_update()
        lbl.size = lbl.texture_size
        lbl.pos = (self.x + sx + r + dp(3), self.y + sy - lbl.height / 2)
        self.add_widget(lbl)
        self._labels.append(lbl)


#: Bubble fill per GPS fix level; a warning triangle is drawn for held/none.
_LEVEL_FILL = {"live": "green", "held": "warning_yellow", "none": "red",
               "info": "accent"}


class _FixBadge(BoxLayout):
    """Left: transient placement guidance text. Right: the compact satellite
    pill — a DRAWN satellite (no emoji fonts on the Pi) beside the
    used-satellite count, on green unless the count is 0, then red
    (operator, 2026-08-13: the full-width 'Live GPS' bar gave the map its
    space back). The old full-row fill and warning triangle are gone; the
    pill and the words carry the state."""

    def __init__(self, **kwargs):
        super().__init__(orientation="horizontal", size_hint_y=None,
                         height=dp(34), padding=[dp(6), dp(2)], spacing=dp(6),
                         **kwargs)
        self._fill = None                 # legacy no-op target for set()
        self.label = Label(font_size="14sp", halign="left", valign="middle")
        self.label.bind(size=lambda i, v: setattr(i, "text_size", v))
        self.add_widget(self.label)
        self._pill = BoxLayout(orientation="horizontal", size_hint=(None, 1),
                               width=dp(92), padding=[dp(8), dp(2)],
                               spacing=dp(4))
        with self._pill.canvas.before:
            self._pill_fill = Color(*theme.hex_to_rgba(theme.COLORS["red"]))
            self._pill_rect = RoundedRectangle(radius=[dp(14)] * 4)
        self._pill.bind(pos=self._sync, size=self._sync)
        # The operator's own satellite art when the asset is carried;
        # the drawn glyph stands in on any medic without it.
        import os as _os
        _icon = _os.path.join(_os.path.dirname(__file__), _os.pardir,
                              _os.pardir, "assets", "ui", "satellite_pill.png")
        if _os.path.exists(_icon):
            from kivy.uix.image import Image as _KivyImage
            self._sat_icon = _KivyImage(source=_icon, size_hint=(None, 1),
                                        width=dp(24), fit_mode="contain")
        else:
            self._sat_icon = Widget(size_hint=(None, 1), width=dp(24))
            self._sat_icon.bind(pos=self._draw_sat, size=self._draw_sat)
        self._pill.add_widget(self._sat_icon)
        self.count = Label(font_size="16sp", bold=True, halign="left",
                           valign="middle",
                           color=theme.hex_to_rgba(theme.COLORS["background"]))
        self.count.bind(size=lambda i, v: setattr(i, "text_size", v))
        self._pill.add_widget(self.count)
        self.add_widget(self._pill)
        self.set_sats(None)

    def _draw_sat(self, *_):
        """A minimal drawn satellite: body, two solar panels, an up-beam.
        Drawn because emoji fonts don't exist on the Pi (standing constraint)."""
        w = self._sat_icon
        w.canvas.after.clear()
        cx, cy = w.center_x, w.center_y
        s = dp(5)
        with w.canvas.after:
            Color(*theme.hex_to_rgba(theme.COLORS["background"]))
            Line(rectangle=(cx - s * 0.7, cy - s * 0.7, s * 1.4, s * 1.4),
                 width=dp(1.4))                                   # body
            Line(points=[cx - s * 2.0, cy, cx - s * 0.8, cy], width=dp(1.4))
            Line(points=[cx + s * 0.8, cy, cx + s * 2.0, cy], width=dp(1.4))
            Line(points=[cx, cy + s * 0.8, cx, cy + s * 1.6], width=dp(1.2))

    def set_sats(self, n):
        """The pill: count + colour. Green needs at least one satellite; 0 or
        unknown is red — the operator's rule, and the honest one."""
        alive = isinstance(n, int) and n > 0
        self.count.text = str(n) if isinstance(n, int) else "–"
        self._pill_fill.rgba = theme.hex_to_rgba(
            theme.COLORS["green" if alive else "red"])

    def _sync(self, *_):
        self._pill_rect.pos, self._pill_rect.size = (self._pill.pos,
                                                     self._pill.size)

    def set(self, text, level):
        """Transient guidance on the LEFT; the pill is untouched — GPS state
        travels only through set_sats. Colour by level: info = accent,
        none = amber (something to do), else quiet."""
        self.label.color = theme.hex_to_rgba(theme.COLORS[
            "accent" if level == "info"
            else "amber" if level == "none" else "text_secondary"])
        self.label.text = text


def _btn(text, color, on_tap):
    b = Button(text=text, bold=True, font_size="18sp", background_normal="",
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
                 recommendations_provider=None,
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
        self.recenter_btn = Button(text=tr("Recenter"), size_hint=(None, 1),
                                   width=dp(100))
        self.recenter_btn.bind(on_release=lambda *_: self._recenter())
        # Mesh-lines toggle: draw the who-hears-whom connection lines. Default OFF;
        # does nothing visible unless a links_provider was wired.
        self.links_btn = Button(text=tr("Links  off"), size_hint=(None, 1), width=dp(92))
        self.links_btn.bind(on_release=lambda *_: self._toggle_links())
        # Terrain overlay: high ground shaded light, low ground dark. Default
        # OFF and silent when no terrain has been cached — the map download
        # fetches it in the same pass, so it appears when there is something to
        # show (operator, 2026-08-04).
        self.terrain_btn = Button(text=tr("Terrain  off"), size_hint=(None, 1),
                                  width=dp(104))
        self.terrain_btn.bind(on_release=lambda *_: self._toggle_terrain())
        header_row.add_widget(self.header)
        header_row.add_widget(self.links_btn)
        header_row.add_widget(self.terrain_btn)
        header_row.add_widget(self.recenter_btn)
        self.add_widget(header_row)

        # --- connections row (operator layout, 2026-08-13): a second header
        # line — "Connections" under the title, then Wi-Fi / Bluetooth /
        # Internet aligned under Links / Terrain / Recenter. Tap to toggle;
        # GREY text = that overlay is off, its lane colour = on. LoRa is the
        # standard view and has no switch anywhere.
        self._overlay_on = {"wifi": True, "bluetooth": True, "internet": True}
        conn_row = BoxLayout(orientation="horizontal", size_hint=(1, None),
                             height=dp(30), spacing=dp(6))
        conn_lbl = Label(text=tr("Connections"), halign="left",
                         valign="middle",
                         color=theme.hex_to_rgba(theme.COLORS["text_secondary"]))
        conn_lbl.bind(size=lambda i, v: setattr(i, "text_size", v))
        self._overlay_btns = {}
        specs = (("wifi", tr("Wi-Fi"), dp(92)),
                 ("bluetooth", tr("Bluetooth"), dp(104)),
                 ("internet", tr("Internet"), dp(100)))
        conn_row.add_widget(conn_lbl)
        for t, label, w in specs:
            b = Button(text=label, size_hint=(None, 1), width=w,
                       background_normal="",
                       background_color=theme.hex_to_rgba(
                           theme.COLORS["surface"]))
            b.bind(on_release=lambda _b, tt=t: self._toggle_overlay(tt))
            self._overlay_btns[t] = b
            conn_row.add_widget(b)
        self._paint_overlay_btns()
        self.add_widget(conn_row)

        # --- Build next (operator: NO seventh mode — the recommender folds
        # into SCAN). One collapsed header line in the Connections row's
        # taste; tap to expand into the top three recommendations, one line
        # each. The map stays the hero: collapsed is the default and the
        # expanded panel is three thin rows, nothing more.
        self._recs_provider = recommendations_provider
        self._bn_expanded = False
        if recommendations_provider is not None:
            self._bn_btn = Button(
                text=tr("Build next"), size_hint=(1, None), height=dp(30),
                halign="left", background_normal="",
                background_color=theme.hex_to_rgba(theme.COLORS["surface"]),
                color=theme.hex_to_rgba(theme.COLORS["text_secondary"]))
            self._bn_btn.bind(on_release=lambda *_: self._toggle_build_next())
            self.add_widget(self._bn_btn)
            self._bn_box = BoxLayout(orientation="vertical",
                                     size_hint=(1, None), height=0,
                                     spacing=dp(2))
            self.add_widget(self._bn_box)

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
            self.coords = Label(text="", font_size="16sp", halign="left",
                                valign="middle", size_hint=(1, None), height=dp(26),
                                color=theme.hex_to_rgba(theme.COLORS["text_primary"]))
            self.coords.bind(size=lambda i, v: setattr(i, "text_size", v))
            self.add_widget(self.coords)

            act = BoxLayout(orientation="horizontal", size_hint=(1, None),
                            height=dp(50), spacing=dp(8))
            self.confirm_btn = _btn(tr("Use this position  →"), "green", self._use_position)
            self.confirm_btn.disabled = True
            act.add_widget(self.confirm_btn)
            act.add_widget(_btn(tr("Enter manually"), "surface", self._toggle_manual))
            self.add_widget(act)
            # Kept so a boundary walk can put the PLACEMENT controls away —
            # during a walk this screen is a measuring instrument, not a
            # place-a-node form (operator, mid-walk 2026-09-21: the big green
            # "Use this position" jumped them into BIRTH).
            self._place_row = act

            self.detail_btn = Button(
                text=tr("Load street names for this spot  (needs WiFi)"),
                size_hint=(1, None), height=dp(46), font_size="15.5sp", bold=True,
                background_normal="",
                background_color=theme.hex_to_rgba(theme.COLORS["accent"]),
                color=theme.hex_to_rgba(theme.COLORS["background"]))
            self.detail_btn.bind(on_release=lambda *_: self._load_detail())
            self.add_widget(self.detail_btn)

            # Manual entry: an address (geocoded) OR raw lat/lon — collapsed until asked.
            # Starts DISABLED (as well as height 0 / opacity 0): a collapsed row's
            # invisible TextInputs would otherwise still swallow a touch and pop the
            # keyboard when the operator is tapping "Use this position" nearby.
            self.manual_row = BoxLayout(orientation="vertical", size_hint=(1, None),
                                        height=dp(0), spacing=dp(6), opacity=0,
                                        disabled=True)
            addr_row = BoxLayout(orientation="horizontal", size_hint_y=None,
                                 height=dp(44), spacing=dp(6))
            self.addr_in = TextInput(hint_text=tr("street address  (needs internet)"),
                                     multiline=False, font_size="25sp")
            bind_field(self.addr_in)
            find_btn = Button(text=tr("Find"), size_hint_x=None, width=dp(84), bold=True,
                              font_size="17sp",
                              background_normal="",
                              background_color=theme.hex_to_rgba(theme.COLORS["accent"]),
                              color=theme.hex_to_rgba(theme.COLORS["background"]))
            find_btn.bind(on_release=lambda *_: self._find_address())
            addr_row.add_widget(self.addr_in)
            addr_row.add_widget(find_btn)
            coord_row = BoxLayout(orientation="horizontal", size_hint_y=None,
                                  height=dp(44), spacing=dp(6))
            self.lat_in = TextInput(hint_text=tr("latitude"), multiline=False,
                                    input_filter="float", font_size="25sp")
            self.lon_in = TextInput(hint_text=tr("longitude"), multiline=False,
                                    input_filter="float", font_size="25sp")
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
        self.offline_toggle = Button(text=tr("Offline maps  ▾"), size_hint=(1, None),
                                     height=dp(34), font_size="15sp", bold=True,
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
            hint_text=tr("Couldn't find your location - type home base as: "
                         "lat, lon  (e.g. -37.79, 144.96)"),
            multiline=False, size_hint=(1, None), height=0, opacity=0,
            font_size="25sp")
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
        self.offline_toggle.text = (tr("Offline maps  ▲") if self._offline_open
                                    else tr("Offline maps  ▾"))
        self._sync_offline_height()

    def _sync_offline_height(self):
        self._offline_panel.height = (self._offline_panel.minimum_height
                                      if self._offline_open else 0)

    def _toggle_links(self):
        """Flip the mesh connection lines on/off (header button)."""
        self._links_on = not self._links_on
        self.plot.set_show_links(self._links_on)
        self.links_btn.text = tr("Links  on") if self._links_on else tr("Links  off")

    def _toggle_terrain(self):
        """Flip the terrain shading on/off.

        Says so plainly when there is nothing cached, rather than appearing to
        do nothing: an overlay that silently fails looks like a broken button.
        """
        want = not getattr(self, "_terrain_on", False)
        if want and not self._terrain_store():
            # This refusal spoke into a hidden widget for ten days: dl_status
            # lives INSIDE the offline-maps panel, which starts collapsed, so
            # on a device with no terrain file every tap "did nothing"
            # (operator, 2026-08-14). Open the panel first — the explanation
            # then sits right next to the download button that cures it.
            if not self._offline_open:
                self._toggle_offline()
            # SAY WHICH HALF IS MISSING. "It downloads with the offline map"
            # read as a flat contradiction next to "This area — already
            # carried" (operator, with a photo, 2026-09-06): the STREET tiles
            # for this spot genuinely are carried — that button is telling the
            # truth — but terrain is a SEPARATE file (offline.terrain.mbtiles)
            # that downloads ALONGSIDE the street tiles, not automatically
            # bundled INTO them after the fact. An area cached before this
            # medic ever tried terrain, or where the terrain fetch failed
            # silently on the day (it never fails the base map for it), ends
            # up in exactly this state: streets carried, terrain not.
            if self._tiles is not None:
                self._set_status(tr(
                    "This area's streets are carried, but not its terrain — "
                    "download this area again to try adding it."), "alert")
            else:
                self._set_status(tr("No terrain cached for this area yet — it "
                                    "downloads with the offline map."), "alert")
            return
        self._terrain_on = want
        self.plot.set_terrain(self._terrain_store() if want else None)
        self.terrain_btn.text = (tr("Terrain  on") if want
                                 else tr("Terrain  off"))

    def _terrain_store(self):
        """The cached terrain beside the current basemap, or None."""
        cached = getattr(self, "_terrain_cache", "unset")
        if cached != "unset":
            return cached
        store = None
        try:
            import os
            from ui.map_download import TERRAIN_ZOOM, terrain_dest
            from monitor.terrain import TerrariumStore
            if self._tiles:
                path = terrain_dest(self._tiles)
                if os.path.exists(path):
                    store = TerrariumStore(path, zoom=TERRAIN_ZOOM)
        except Exception:
            store = None
        self._terrain_cache = store
        return store

    # -- placement ----------------------------------------------------------
    def _recenter(self):
        """Snap the view back to the auto-fit AND drop any map-tap/manual override,
        so the placement badge returns to tracking the live GPS fix."""
        self.plot.reset_view()
        if getattr(self, "_on_place", None) is not None:
            self._picked = None
            if self._manual:
                self._set_manual_shown(False)
            self._show_live_badge()

    def _show_live_badge(self):
        """Reflect the live/held/none GPS fix in the badge + coords, without ever
        hijacking the operator's pan/zoom (unlike the old confirm page, which
        re-centred on every poll)."""
        t = fix_trust(self._fix)
        self.badge.set_sats(getattr(self._fix, "sats", None)
                            if self._fix is not None else None)
        # Live needs no words — the pill says it. Held/none keep their
        # honest sentence (coasting on memory is worth a sentence).
        self.badge.set("" if t["level"] == "live" else t["title"], t["level"])
        hint = t["detail"]
        if t["level"] != "live":
            hint = tr("Tap the map to drop the pin, or ") + hint[0].lower() + hint[1:]
        if self._fix is not None and getattr(self._fix, "has_fix", False):
            # Show the HDOP-estimated accuracy when we have one, so the operator
            # can judge the fix — always self-labelled "(est. from HDOP)" so it
            # never reads as a measured figure. None -> nothing shown, never a
            # fabricated number (accuracy_label is the sole source of the words,
            # keeping this literal-free for the i18n guard).
            acc = accuracy_label(self._fix)
            acc_part = f"   ·   {acc}" if acc else ""
            self.coords.text = (f"{self._fix.lat:.6f},  {self._fix.lon:.6f}"
                                f"{acc_part}   ·   {hint}")
            self.confirm_btn.disabled = False
        else:
            self.coords.text = hint
            self.confirm_btn.disabled = True

    def _toggle_build_next(self):
        """Expand/collapse the Build next panel. Collapsed leaves only the
        header line — the map keeps its room."""
        self._bn_expanded = not self._bn_expanded
        self._refresh_build_next()

    def _refresh_build_next(self):
        self._bn_box.clear_widgets()
        if not self._bn_expanded:
            self._bn_box.height = 0
            self._bn_btn.text = tr("Build next")
            return
        try:
            markers = list(self._recs_provider() or [])
        except Exception:
            markers = []
        lines = build_next_lines(markers) or [
            tr("Nothing to build yet - no located gaps or weak links.")]
        for ln in lines:
            lbl = Label(text=ln, halign="left", valign="middle",
                        shorten=True, shorten_from="right",
                        font_size="14sp", size_hint=(1, None), height=dp(24),
                        color=theme.hex_to_rgba(theme.COLORS["text_primary"]))
            lbl.bind(size=lambda i, v: setattr(i, "text_size", v))
            self._bn_box.add_widget(lbl)
        self._bn_box.height = dp(24) * len(lines)
        self._bn_btn.text = tr("Build next  (hide)")

    def _toggle_overlay(self, transport):
        """Flip one overlay lane, repaint the button, redraw the map. Grey
        text = off; the lane's own colour = on (operator, 2026-08-13)."""
        self._overlay_on[transport] = not self._overlay_on.get(transport, True)
        self._paint_overlay_btns()
        try:
            self.plot.refresh()
        except Exception:                                          # noqa: BLE001
            pass

    def _paint_overlay_btns(self):
        for t, b in getattr(self, "_overlay_btns", {}).items():
            on = self._overlay_on.get(t, True)
            b.color = theme.hex_to_rgba(
                theme.COLORS[MapPlot.LINK_COLOURS.get(t, "accent")] if on
                else theme.COLORS["text_secondary"])

    def visible_transports(self):
        """What the map should draw right now: LoRa (+ path-implied unknown/
        local) always; each overlay only while its switch says on."""
        vis = {"lora", "unknown", "local"}
        for t, on in self._overlay_on.items():
            if on:
                vis.add(t)
        return vis

    def _on_map_pick(self, latlon):
        """Operator tapped the map to set the location (no GPS/internet needed).
        The pin already moved; adopt the point."""
        if self._manual:                       # a map tap supersedes manual entry
            self._set_manual_shown(False)
        self._picked = latlon
        self.badge.set(tr("Picked from map"), "info")
        self.coords.text = (f"{latlon[0]:.6f},  {latlon[1]:.6f}   ·   "
                            + tr("tap again to move, or Recenter to go back to GPS"))
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
            self.badge.set(tr("Set a location first — tap the map or enter it"), "none")
            return
        if self._on_place:
            self._on_place(pt[0], pt[1], pt[2])

    def _set_manual_shown(self, show):
        """Reveal or collapse the manual-entry row. Collapsed, the row is also
        DISABLED (not just height 0 / opacity 0): a Kivy widget with opacity 0 still
        receives touches, so its invisible address/lat/lon inputs would otherwise
        grab a tap meant for the buttons around them and pop the on-screen keyboard —
        the stray-keyboard bug on 'Use this position'. Disabling gates that off."""
        self._manual = show
        self.manual_row.height = dp(96) if show else dp(0)
        self.manual_row.opacity = 1 if show else 0
        self.manual_row.disabled = not show

    def _toggle_manual(self):
        if self._manual:
            self._set_manual_shown(False)
            self._show_live_badge()
            return
        self._set_manual_shown(True)
        self._picked = None
        self.badge.set(tr("Enter a location"), "info")
        self.coords.text = tr("Type an address and Find (needs internet), or enter "
                              "lat/lon directly, then Use this position.")
        self.confirm_btn.disabled = False
        if self._fix is not None and getattr(self._fix, "has_fix", False):
            self.lat_in.text = f"{self._fix.lat:.6f}"
            self.lon_in.text = f"{self._fix.lon:.6f}"
        # Focus the address field so the keyboard opens right on it — manual entry
        # exists to type an address here. Deferred a frame so the row has finished
        # un-collapsing (and re-enabling) before the field takes focus. Blur first:
        # if the field was left focused from a previous open, setting focus=True is a
        # no-op and the keyboard never fires — the "tap Enter a location, nothing
        # happens" bug. Forcing False->True guarantees the focus event (and keyboard).
        self.addr_in.focus = False
        Clock.schedule_once(lambda *_: setattr(self.addr_in, "focus", True), 0.1)

    def _find_address(self):
        """Geocode the typed address (off-thread) and drop the pin to verify it."""
        addr = self.addr_in.text.strip()
        if not addr:
            self.badge.set(tr("Type an address first, then Find"), "info")
            return
        self.badge.set(tr("Looking up address…"), "info")

        def work():
            res = geocode_address(addr)
            Clock.schedule_once(lambda dt: self._apply_geocode(res), 0)
        threading.Thread(target=work, daemon=True).start()

    def _apply_geocode(self, res):
        if not res:
            self.badge.set(tr("Address not found (no internet?) — enter lat/lon"), "none")
            return
        self.lat_in.text = f"{res['lat']:.6f}"
        self.lon_in.text = f"{res['lon']:.6f}"
        self.badge.set(tr("Found — check the pin sits right"), "info")
        self.coords.text = res["name"][:120]
        self.plot.focus((res["lat"], res["lon"]))

    def _load_detail(self):
        """Cache street-level tiles (with names) for the current spot, over WiFi."""
        if self._dl_busy:
            return
        pt = self._current_point()
        if pt is None:
            self.badge.set(tr("Pick or find a location first, then load its streets"), "info")
            return
        if not is_online():
            self.badge.set(tr("No internet — join WiFi to load street names"), "none")
            return
        self._dl_busy = True
        self.detail_btn.disabled = True
        self.detail_btn.text = tr("Downloading street detail…")
        lat, lon = pt[0], pt[1]
        dest = os.path.join(MAPS_DIR, "offline.mbtiles")
        os.makedirs(MAPS_DIR, exist_ok=True)

        def prog(s):
            if "done" in s and "total" in s:
                Clock.schedule_once(lambda dt: setattr(
                    self.detail_btn, "text",
                    tr("Street detail… {done}/{total} tiles").format(
                        done=s['done'], total=s['total'])), 0)

        def work():
            # ALWAYS post back, even on error — else _dl_busy sticks True and the
            # button silently does nothing on every later press (the bug this fixes).
            try:
                summary = add_point_detail(lat, lon, dest, radius_km=SPOT_RADIUS_KM,
                                           zmin=SPOT_MIN_ZOOM, zmax=SPOT_MAX_ZOOM,
                                           on_progress=prog)
            except Exception as e:
                summary = {"error": str(e), "fetched": 0, "skipped": 0}
            Clock.schedule_once(lambda dt: self._detail_done(summary, (lat, lon)), 0)
        threading.Thread(target=work, daemon=True).start()

    def _detail_done(self, summary, pt):
        self._dl_busy = False
        self.detail_btn.disabled = False
        self.detail_btn.text = tr("Load street names for this spot  (needs WiFi)")
        if summary.get("error"):
            self.badge.set(tr("Couldn't load street names — check WiFi and try again"),
                           "none")
            return
        self._tiles = find_mbtiles()
        self.plot.set_tiles(self._tiles)
        self.plot.focus(pt, zoom=17)                # land close; +/- to fine-tune
        if summary.get("blocked"):
            self.badge.set(tr("Map server is rate-limiting — try again shortly"), "none")
        elif summary.get("fetched") or summary.get("skipped"):
            self.badge.set(tr("Street detail loaded — use +/− to zoom in"), "info")
        else:
            self.badge.set(tr("Couldn't fetch detail (check the connection)"), "none")

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
        self.header.text = tr("Map — coverage & placement")
        self.attribution.text = ATTRIBUTION if self._tiles is not None else ""

    def set_nodes(self, nodes):
        nodes = list(nodes or [])
        self._nodes = nodes
        located = geo_points(nodes)
        self.plot.set_nodes(nodes)
        unlocated = [n.get("name", "(unnamed)") for n in nodes
                     if n.get("lat") is None or n.get("lon") is None]
        if not located and not unlocated:
            self.note.text = tr("No nodes yet — they appear here once built.")
        elif not located:
            self.note.text = tr("No node has a location yet. Build nodes with a GPS "
                                "fix to place them on the map.")
        elif unlocated:
            self.note.text = tr("No location for: {names}").format(
                names=", ".join(unlocated))
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
            return (fix.lat, fix.lon), tr("current GPS location")
        pts = geo_points(self._nodes)
        if pts:
            lat = sum(p.lat for p in pts) / len(pts)
            lon = sum(p.lon for p in pts) / len(pts)
            return (lat, lon), tr("placed nodes")
        ip = getattr(self, "_ip_center", None)
        if ip:
            return (ip[0], ip[1]), tr("{place} (approximate, from your internet)").format(
                place=ip[2])
        typed = parse_latlon(getattr(self, "center_input", None)
                             and self.center_input.text or "")
        if typed:
            return typed, tr("entered home base")
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
            count, mb = estimate_world()
            # ALREADY CARRIED? Then don't offer it (operator, 2026-08-31:
            # "anything that has been permanently downloaded should be removed
            # from the download options"). The medic finished the world on
            # 2026-08-31 and the screen still invited an hours-long 1.2 GB
            # re-download of what it already had — an offer that cannot be
            # true is its own kind of lie.
            from ui.map_download import carried_tile_count
            carried = carried_tile_count(
                os.path.join(MAPS_DIR, "offline.mbtiles"))
            if carried is not None and carried >= count:
                self.dl_button.text = tr("World overview - already carried")
                self.dl_button.disabled = True
                self._set_status(
                    tr("The whole world is already on this medic ({n} tiles) "
                       "- nothing to download.").format(n=carried), "ok")
                return
            self.dl_button.text = tr("Download offline map (World overview)")
            verdict = storage_summary(mb, disk_free_mb(
                MAPS_DIR if os.path.isdir(MAPS_DIR) else "."))
            self.dl_button.disabled = not verdict["ok"]
            done = tr(" {n} already carried.").format(n=carried) if carried else ""
            self._set_status(
                tr("The whole world at overview zoom (~{count} tiles - hours, "
                   "resumable).").format(count=count) + done + " " + verdict['text'],
                "ok" if verdict["ok"] else "alert")
            return
        self.dl_button.text = tr("Download offline map ({km} km)").format(
            km=f"{self._radius_km:g}")
        center, source = self._download_center()
        if center is None:
            self.dl_button.disabled = True
            if not getattr(self, "_ip_tried", False):
                self._set_status(tr("Finding your location…"), "unknown")
            else:
                self._set_status(tr("Couldn't find your location automatically - "
                                    "type home base below."), "warn")
            return
        count, mb = estimate_download(center[0], center[1], self._radius_km,
                                      DEFAULT_MIN_ZOOM, DEFAULT_MAX_ZOOM)
        verdict = storage_summary(mb, disk_free_mb(MAPS_DIR
                                                   if os.path.isdir(MAPS_DIR)
                                                   else "."))
        self.dl_button.disabled = not verdict["ok"]
        # NO OVERLAP TALK (operator, 2026-08-31): "user doesn't need to know
        # about overlap if the map does." The downloader already skips every
        # tile it carries (_fetch_tiles: `if writer.has(...)`), so a bigger
        # radius after a smaller one fetches only the new ring and the keeper
        # still gets the full circle they asked for — just faster. The one
        # thing worth saying is when there is NOTHING to fetch, because
        # offering a download that would do nothing is a lie.
        try:
            from ui.map_download import carried_of, tiles_in_radius
            tiles = tiles_in_radius(center[0], center[1], self._radius_km,
                                    zmin=DEFAULT_MIN_ZOOM,
                                    zmax=DEFAULT_MAX_ZOOM)
            if carried_of(tiles, os.path.join(MAPS_DIR,
                                              "offline.mbtiles")) >= len(tiles):
                self.dl_button.text = tr("This area - already carried")
                self.dl_button.disabled = True
                self._set_status(
                    tr("This area is already on the medic - nothing to "
                       "download."), "ok")
                return
        except Exception:                                  # noqa: BLE001
            pass
        self._set_status(tr("Centred on {source}.").format(source=source)
                         + " " + verdict['text'],
                         "ok" if verdict["ok"] else "alert")

    def _on_download(self):
        if self._downloading:
            return
        if not is_online():
            self._set_status(tr("No internet — connect to WiFi to download maps."),
                             "warn")
            return
        if self._radius_km == WORLD:
            count, mb = estimate_world()
            if not storage_summary(mb, disk_free_mb("."))["ok"]:
                self._refresh_estimate()
                return
            self._downloading = True
            self.dl_button.disabled = True
            self._set_status(tr("Downloading world overview (~{count} tiles)…").format(
                count=count))
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
        self._set_status(tr("Downloading ~{count} tiles (~{mb} MB) around {source}…").format(
            count=count, mb=f"{mb:g}", source=source))
        dest = os.path.join(MAPS_DIR, "offline.mbtiles")
        os.makedirs(MAPS_DIR, exist_ok=True)
        # THE WORLD ALREADY HAS AN OWNER: the boot-resuming fill service
        # (2026-08-30). A second fetcher just starves against it on the
        # SQLite lock and freezes its own counter on glass — so when the
        # service is running, the button becomes a live WINDOW onto its
        # progress instead of a competitor.
        if self._radius_km == WORLD:
            from ui.map_download import world_fill_service_active
            if world_fill_service_active():
                self._watch_world_fill(dest)
                return
        threading.Thread(target=self._run_download, args=(lat, lon, dest),
                         daemon=True).start()

    def _watch_world_fill(self, dest):
        """Live label for the background world fill: carried count polled
        every few seconds; stops itself when the world is complete."""
        from ui.map_download import carried_tile_count, estimate_world
        total, _mb = estimate_world()
        ev_holder = {}

        def tick(_dt):
            n = carried_tile_count(dest)
            if n is None:
                return
            if n >= total:
                self._set_status(tr("World map complete — {n} tiles carried."
                                    ).format(n=n))
                ev = ev_holder.get("ev")
                if ev is not None:
                    ev.cancel()
                return
            self._set_status(
                tr("The medic is downloading the world in the background — "
                   "{n}/{total} tiles carried.").format(n=n, total=total))

        tick(0)
        ev_holder["ev"] = Clock.schedule_interval(tick, 5)

    def _run_download(self, lat, lon, dest):
        def progress(s):
            if "cancelled" in s:
                return
            Clock.schedule_once(lambda dt: self._set_status(
                tr("Downloading… {done}/{total} tiles").format(
                    done=s['done'], total=s['total'])), 0)
        if self._radius_km == WORLD:
            summary = download_world(dest, on_progress=progress)
        else:
            summary = download_region(lat, lon, dest, radius_km=self._radius_km,
                                      zmin=DEFAULT_MIN_ZOOM,
                                      zmax=DEFAULT_MAX_ZOOM,
                                      on_progress=progress)
        # TERRAIN, in the same pass. The operator asked for "map data for this
        # area" — elevation IS map data, and it answers the question the
        # placement suggester cannot answer from distance alone. One zoom level,
        # so it adds a few dozen tiles to a run of thousands. A separate button
        # would be a chore nobody does until the day they need it, in the field,
        # with no internet (operator, 2026-08-04).
        if (self._radius_km != WORLD and not summary.get("blocked")
                and not summary.get("cancelled")):
            def tprog(s):
                Clock.schedule_once(lambda dt: self._set_status(
                    tr("Caching terrain… {done}/{total}").format(
                        done=s['done'], total=s['total'])), 0)
            try:
                terr = download_terrain(lat, lon, dest,
                                        radius_km=self._radius_km,
                                        on_progress=tprog)
                summary["terrain"] = terr.get("fetched", 0) + terr.get("skipped", 0)
            except Exception:
                summary["terrain"] = 0     # never fail the map for the terrain

        # Street-detail top-up: a small z13-15 circle around every PLACED node,
        # so a service visit can navigate to the node's street. Tiny + polite.
        if not summary.get("blocked") and not summary.get("cancelled"):
            located = geo_points(self._nodes)
            if located:
                def dprog(s):
                    if "detail_of" in s:
                        Clock.schedule_once(
                            lambda dt, n=s["detail_of"]: self._set_status(
                                tr("Caching street detail around {name}…").format(name=n)), 0)
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
        # _terrain_store memoises its answer — None included — and this
        # download may have just created the terrain file (or moved which
        # basemap it sits beside). Forget the memo, or a pre-download tap's
        # cached None keeps refusing until an app restart (2026-08-14).
        self._terrain_cache = "unset"
        if getattr(self, "_terrain_on", False):
            # The overlay is up: re-point it at the fresh file. If the new
            # basemap has no terrain beside it, "Terrain  on" would be the
            # button lying — turn it off visibly instead.
            store = self._terrain_store()
            self.plot.set_terrain(store)
            if store is None:
                self._terrain_on = False
                self.terrain_btn.text = tr("Terrain  off")
        self._refresh_header()
        got, failed = summary["fetched"] + summary["skipped"], summary["failed"]
        if summary.get("blocked"):
            self._set_status(tr("The tile server started refusing us (bulk "
                                "protection). Stopped cleanly - try again later "
                                "or with a smaller radius."), "alert")
            return
        if got and self._tiles is not None:
            msg = tr("Offline map ready — {got} tiles cached.").format(got=got)
            if failed:
                msg += " " + tr("({failed} unavailable)").format(failed=failed)
            # TERRAIN'S OWN FATE, SAID NOW — not left for the operator to find
            # out independently later by toggling "Terrain" on. The fetch
            # deliberately swallows its own exceptions ("never fail the map
            # for the terrain" — right instinct, a stalled terrain provider
            # must not block the base map the operator is actually waiting
            # on) but that meant a silent terrain failure looked identical to
            # success: "This area — already carried" sat right above "No
            # terrain cached for this area yet — it downloads with the
            # offline map", which reads as a straight contradiction when nothing
            # explains they are two separate caches (operator, with a photo,
            # 2026-09-06). radius_km == WORLD never attempts terrain at all,
            # so it says nothing here — silence is correct there, not a gap.
            if (self._radius_km != WORLD
                    and summary.get("terrain", 0) == 0):
                msg += " " + tr("Terrain could not be downloaded this time — "
                                "try again later.")
            self._set_status(msg, "ok")
            self.center_input.height = 0            # centre solved; tidy away
            self.center_input.opacity = 0
        else:
            self._set_status(tr("Download failed — no tiles cached. Check the "
                                "connection and try again."), "alert")

    # -- the boundary walk (operator spec 2026-08-13; built 2026-09-15) -----

    def begin_walk(self, record, ping_fn, on_finished=None, reach_probe=None):
        """Walk-away range truth for ONE node, folded into MAPS as ordered
        ("fold it into the map view", 2026-08-13 — no seventh mode).

        Since 2026-09-21 this does NOT start pinging. It opens TWO GATES, in
        order, and hands over:

          1. ``_show_walk_check`` — does the node answer a ping RIGHT NOW? A
             no is a refusal, and nothing about GPS is shown, because there
             is no point waiting on satellites for a node that is dead.
          2. ``_show_walk_gate`` — stand at the node and wait for a fix. The
             start button does not exist until there is one.

        They are deliberately two screens with their own words rather than
        one "getting ready…" spinner: a stranger standing in a yard has to be
        able to tell whether the hold-up is the node or the sky (operator,
        2026-09-21). BOTH doors into the walk come through here, so a walk
        starts the same way whichever button was pressed.

        *reach_probe* is ``(dst_hash) -> bool``, injected — the app supplies
        ``_mesh_reachable``, the same primitive the ANTENNA picker uses, so
        the two doors cannot drift apart on what "online" means.
        """
        self.end_walk(persist=False)          # one walk (or gate) at a time
        self._walk_record = record
        self._walk_ping_fn = ping_fn
        self._walk_done_cb = on_finished
        self._walk_reach_probe = reach_probe
        # From the first gate onward this screen is a measuring instrument,
        # not a place-a-node form: the full-width green "Use this position →"
        # stamps a position and jumps into BIRTH, and the operator hit exactly
        # that mid-walk (2026-09-21). Restored by end_walk.
        self._show_placement(False)
        self._show_walk_check()

    # -- gate 1: is the node even there? -------------------------------------

    def _walk_panel(self):
        """The chrome shared by all three pre-walk panels — one look, so the
        operator sees a sequence rather than three unrelated screens.

        SIZED BY ITS CONTENT, never by a guessed height. Four birth screens
        once pushed their animation off the glass because the text grew past
        the box drawn for it (2026-08-12), and these panels carry the longest
        sentences in the feature — in eight languages, on a short panel.
        """
        panel = BoxLayout(orientation="vertical", size_hint=(1, None),
                          spacing=dp(6), padding=[dp(10), dp(8)])
        panel.bind(minimum_height=panel.setter("height"))
        with panel.canvas.before:
            from kivy.graphics import Color as _C, Rectangle as _R
            _C(*theme.hex_to_rgba(theme.COLORS["surface"]))
            bg = _R()
        panel.bind(pos=lambda *_: setattr(bg, "pos", panel.pos),
                   size=lambda *_: setattr(bg, "size", panel.size))
        return panel

    @staticmethod
    def _walk_text(text, size, color, bold=False):
        """A wrapping label that GROWS to its text. The gate's instructions
        are the whole of what a first-time operator is told; a German or
        Russian sentence clipped at a fixed height would silently take half
        of them away."""
        lbl = Label(text=text, font_size=theme.font_sp(size), bold=bold,
                    halign="left", valign="top", size_hint_y=None,
                    color=theme.hex_to_rgba(theme.COLORS[color]))

        def _sync(*_):
            lbl.text_size = (lbl.width, None)
            lbl.texture_update()
            lbl.height = max(lbl.texture_size[1], dp(20))
        lbl.bind(width=_sync, text=_sync)
        return lbl

    def _walk_node_name(self):
        r = self._walk_record
        return (getattr(r, "name", "")
                or (getattr(r, "dst_hash", "") or "")[:8])

    def _show_walk_check(self):
        """GATE 1. "if the user selects a node underneath VITALS and they
        click boundary walk, it automatically pings the node to make sure
        it's online first. And if it's not online, you can say no, not
        available" (operator, 2026-09-21).

        The node's own VITALS page used to start a walk against whatever was
        tapped, reachable or not — so an operator could walk away from a box
        that had been silent for hours and learn it from a trail of red
        pings. The ANTENNA picker already probed; this puts the same probe in
        front of BOTH doors.

        THE ANTENNA DOOR IS RE-CHECKED HERE TOO, deliberately, rather than
        trusting the sweep that built its list. The sweep's answer is up to
        twenty-five seconds old before the list even appears, plus however
        long the operator spent reading it — and the project's oldest law is
        that a remembered sighting is not a sighting, which a half-minute-old
        probe result already is. It also keeps ONE flow with two entrances
        instead of two flows; a door that skips a gate is a second flow. The
        cost is one probe the operator is standing still for anyway.
        """
        from kivy.uix.button import Button
        name = self._walk_node_name()
        self._tear_down_walk_gate()
        panel = self._walk_panel()
        head = self._walk_text(
            tr("Checking {name} is on the mesh…").format(name=name),
            "19sp", "accent", bold=True)
        why = self._walk_text(
            tr("Node Medic is pinging it now. There is no point walking away "
               "from a node that is not answering, and a node last heard an "
               "hour ago may be long gone. This takes a few seconds."),
            "14sp", "text_secondary")
        row = BoxLayout(orientation="horizontal", size_hint=(1, None),
                        height=dp(48), spacing=dp(8))
        cancel = Button(text=tr("Cancel"), size_hint_x=None, width=dp(110),
                        background_normal="",
                        background_color=theme.hex_to_rgba(
                            theme.COLORS["surface"]),
                        color=theme.hex_to_rgba(theme.COLORS["text_primary"]))
        cancel.bind(on_release=lambda *_: self.end_walk(persist=False))
        row.add_widget(Widget())
        row.add_widget(cancel)
        for w in (head, why, row):
            panel.add_widget(w)
        self._walk_gate = panel
        self.add_widget(panel, index=len(self.children))
        self._run_walk_check()

    def _run_walk_check(self):
        """The probe itself, off-thread — it blocks for ~10 s and the operator
        is standing outside in the weather. The RULE is answers_now's, shared
        with the ANTENNA picker; this only supplies the probe and the thread."""
        import threading
        from kivy.clock import Clock as _Clock
        from monitor.boundary_walk import answers_now
        probe = getattr(self, "_walk_reach_probe", None)
        token = object()
        self._walk_check_token = token
        if not callable(probe):
            # "I could not check" is its own answer, and it is NOT "it works".
            # A walk started on an unchecked node is the thing this gate
            # exists to prevent, so a missing probe refuses like a silent one.
            self._show_walk_unavailable(checked=False)
            return
        node = {"dst_hash": getattr(self._walk_record, "dst_hash", "")}

        def work():
            live = answers_now(node, probe=probe)
            _Clock.schedule_once(
                lambda _d: self._walk_check_done(live is not None, token), 0)
        threading.Thread(target=work, daemon=True).start()

    def _walk_check_done(self, ok, token=None):
        """Gate 1's verdict. A stale answer from a cancelled check is dropped
        — the operator may have pressed Cancel and started another walk."""
        if token is not None and token is not getattr(
                self, "_walk_check_token", None):
            return
        if getattr(self, "_walk_gate", None) is None:
            return                          # cancelled while the probe ran
        if ok:
            self._show_walk_gate()
        else:
            self._show_walk_unavailable(checked=True)

    def _show_walk_unavailable(self, checked=True):
        """The refusal, worded as the operator asked: not available. It names
        what to check and leaves two ways on — try again, or back out — never
        a dead end, and never a "walk anyway" that would measure a node that
        is not there."""
        from kivy.uix.button import Button
        name = self._walk_node_name()
        self._tear_down_walk_gate()
        panel = self._walk_panel()
        head = self._walk_text(
            (tr("{name} is not available.") if checked
             else tr("Node Medic could not check {name}.")).format(name=name),
            "19sp", "red", bold=True)
        why = self._walk_text(
            (tr("It did not answer a ping just now, so there is nothing "
                "to walk away from. Check it is powered and within "
                "range, then try again — a node can also simply be busy, "
                "and a minute's wait is often enough.") if checked
             else tr("The mesh check could not be run, so the medic does "
                     "not know whether this node is there. It will not "
                     "start a walk on a guess.")),
            "14sp", "text_secondary")
        row = BoxLayout(orientation="horizontal", size_hint=(1, None),
                        height=dp(50), spacing=dp(8))
        again = Button(text=tr("Ping it again"), bold=True,
                       font_size=theme.font_sp("16sp"), background_normal="",
                       background_color=theme.hex_to_rgba(
                           theme.COLORS["accent"]),
                       color=theme.hex_to_rgba(theme.COLORS["background"]))
        again.bind(on_release=lambda *_: self._show_walk_check())
        close = Button(text=tr("Close"), size_hint_x=None, width=dp(110),
                       background_normal="",
                       background_color=theme.hex_to_rgba(
                           theme.COLORS["surface"]),
                       color=theme.hex_to_rgba(theme.COLORS["text_primary"]))
        close.bind(on_release=lambda *_: self.end_walk(persist=False))
        row.add_widget(again)
        row.add_widget(close)
        for w in (head, why, row):
            panel.add_widget(w)
        self._walk_gate = panel
        self.add_widget(panel, index=len(self.children))

    # -- gate 2: stand at the node, wait for sky -----------------------------

    def _show_walk_gate(self):
        """"STAND NEXT TO THE NODE and wait for a fix" — the operator's own
        order, 2026-09-21, given outdoors while testing this as a
        first-time-user experience rather than as a range test.

        A stranger arriving here has just pressed a button and been moved to
        a map. This panel is the whole of what they are told, so it carries
        all three things they need: where to put their body, what the medic
        is doing, and what will happen next. The big green button is the ONLY
        way forward and it is absent until there is a fix to anchor on —
        nothing here offers a start that would measure nothing.
        """
        import time as _t
        from kivy.uix.button import Button
        from kivy.clock import Clock as _Clock
        name = self._walk_node_name()
        self._tear_down_walk_gate()
        gate = self._walk_panel()

        # SAY WHICH GATE PASSED. The operator has just watched a screen ping
        # the node; without this line the next screen reads as the same wait
        # continuing, and they cannot tell whether the hold-up is the node or
        # the sky — which is the whole reason these are two screens.
        done = self._walk_text(
            tr("{name} answered — it is on the mesh.").format(name=name),
            "14.5sp", "green", bold=True)
        head = self._walk_text(
            tr("Stand next to {name}.").format(name=name),
            "19sp", "warning_yellow", bold=True)
        why = self._walk_text(
            tr("Node Medic measures how far this node reaches by the distance "
               "you walk from it — so it has to know where you started. Wait "
               "here until it has a satellite fix, then press the button and "
               "walk away."),
            "14sp", "text_secondary")
        self._walk_gate_badge = _FixBadge()
        self._walk_gate_state = self._walk_text("", "14.5sp", "text_secondary")

        row = BoxLayout(orientation="horizontal", size_hint=(1, None),
                        height=dp(58), spacing=dp(8))
        # LARGE and unmistakable, as ordered — and it says WHY it has appeared,
        # because a button that materialises without explanation is a button
        # people press without reading.
        self._walk_go = Button(
            text=tr("GPS found — start the walk"), bold=True,
            font_size=theme.font_sp("19sp"), background_normal="",
            background_color=theme.hex_to_rgba(theme.COLORS["green"]),
            color=theme.hex_to_rgba(theme.COLORS["background"]))
        self._walk_go.bind(on_release=lambda *_: self._walk_gate_go())
        cancel = Button(text=tr("Cancel"), size_hint_x=None, width=dp(110),
                        background_normal="",
                        background_color=theme.hex_to_rgba(
                            theme.COLORS["surface"]),
                        color=theme.hex_to_rgba(theme.COLORS["text_primary"]))
        cancel.bind(on_release=lambda *_: self.end_walk(persist=False))
        row.add_widget(self._walk_go)
        row.add_widget(cancel)
        # Absent, not greyed: a disabled button still reads as "the way on",
        # and an operator taps it and learns the medic ignores them.
        self._walk_go.opacity = 0
        self._walk_go.disabled = True

        for w in (done, head, why, self._walk_gate_badge,
                  self._walk_gate_state, row):
            gate.add_widget(w)
        self._walk_gate = gate
        self._walk_gate_since = _t.time()
        self._walk_anchor = None
        self.add_widget(gate, index=len(self.children))   # top of the screen
        self._walk_gate_tick(0)
        self._walk_gate_ev = _Clock.schedule_interval(self._walk_gate_tick, 1.0)

    def _walk_gate_tick(self, _dt):
        """One look at the sky. The verdict is monitor.boundary_walk.gps_gate's
        (pure, tested); this only paints it and shows or hides the button."""
        import time as _t
        from monitor.boundary_walk import gps_gate
        if getattr(self, "_walk_go", None) is None:
            return                          # gate 2 is not the panel on screen
        fix = None
        try:
            fix = self._fix_reader() if self._fix_reader else None
        except Exception:                                          # noqa: BLE001
            fix = None
        waited = _t.time() - getattr(self, "_walk_gate_since", _t.time())
        g = gps_gate(fix, waited_s=waited)
        self._walk_gate_badge.set_sats(g["sats"])
        self._walk_anchor = g["anchor"]
        # SAY WHAT IT IS WAITING FOR, in the words of the thing to do about it
        # — never a bare spinner. Each stage has a different answer.
        if g["ready"]:
            words, tone = tr("Satellite fix — ready to start."), "green"
        elif g["stage"] == "held":
            words, tone = (tr("The GPS is coasting on an old position, not "
                              "tracking. Step into the open and wait."), "amber")
        elif g["stage"] == "slow":
            words, tone = (tr("Still no fix after two minutes. Move away from "
                              "walls and trees — or run PROBE ▸ Self Diagnose "
                              "to check the GPS itself."), "amber")
        else:
            words, tone = (tr("Looking for satellites — this can take a couple "
                              "of minutes from cold. Keep the sky in view."),
                           "text_secondary")
        self._walk_gate_state.text = words
        self._walk_gate_state.color = theme.hex_to_rgba(theme.COLORS[tone])
        self._walk_go.opacity = 1 if g["ready"] else 0
        self._walk_go.disabled = not g["ready"]

    def _walk_gate_go(self):
        """The press. Anchor HERE — see _start_walk_now."""
        anchor = getattr(self, "_walk_anchor", None)
        if anchor is None:                 # the fix died between paint and tap
            self._walk_gate_tick(0)
            return
        self._tear_down_walk_gate()
        self._start_walk_now(anchor)

    def _tear_down_walk_gate(self):
        """Remove whichever pre-walk panel is up (check / refusal / GPS gate)
        and stop its clock. Safe to call idle, and called by end_walk — so
        Cancel on any of the three unwinds the whole thing."""
        ev = getattr(self, "_walk_gate_ev", None)
        if ev is not None:
            ev.cancel()
        self._walk_gate_ev = None
        self._walk_go = None                 # gate 2 is no longer on screen
        gate = getattr(self, "_walk_gate", None)
        if gate is not None and gate.parent is not None:
            self.remove_widget(gate)
        self._walk_gate = None

    # -- the walk itself -----------------------------------------------------

    def _start_walk_now(self, anchor):
        """Begin pinging, anchored at *anchor* — the fix the operator was
        standing on when they pressed the button.

        NOT the registry's remembered position for the node. That may be
        hours old, and where the operator shares a fuzzed location it is
        deliberately wrong by hundreds of metres; either would put a false
        distance on every sample in the walk. The gate's own words are
        "stand next to {name}", so the press is the operator asserting they
        are there, and a measurement taken now beats a remembered one — the
        project's oldest law, applied to our own coordinates.
        """
        import time as _t
        from kivy.clock import Clock as _Clock
        from monitor.boundary_walk import BoundaryWalkSession
        rec = self._walk_record
        lat, lon = anchor
        self._walk = BoundaryWalkSession(
            node_key=rec.dst_hash, node_name=rec.name or rec.dst_hash[:8],
            node_lat=lat, node_lon=lon, now=_t.time())
        self._show_walk_hud()
        self._walk_ev = _Clock.schedule_interval(self._walk_tick, 1.0)
        self._walk_flash_ev = _Clock.schedule_interval(self._walk_flash, 0.5)
        self._walk_flash_on = False

    def _show_walk_hud(self):
        """Banner + a plain instruction line + the button that ends it.

        The instruction line is new on 2026-09-21. Before it the flashing
        banner was the end of the conversation: a stranger stood in a field
        holding a device that said MESH CONNECTION LOST and said nothing
        about what to do about it.
        """
        from kivy.uix.button import Button
        hud = BoxLayout(orientation="vertical", size_hint=(1, None),
                        spacing=dp(4))
        hud.bind(minimum_height=hud.setter("height"))
        top = BoxLayout(orientation="horizontal", size_hint=(1, None),
                        height=dp(54), spacing=dp(8))
        self._walk_lbl = Label(text=tr("Boundary walk — walk away from "
                                       "{name}. Pinging…").format(
                                           name=self._walk.node_name),
                               bold=True, font_size=theme.font_sp("16sp"),
                               color=theme.hex_to_rgba(theme.COLORS["background"]))
        with self._walk_lbl.canvas.before:
            from kivy.graphics import Color as _C, Rectangle as _R
            self._walk_bg_col = _C(*theme.hex_to_rgba(theme.COLORS["warning_yellow"]))
            self._walk_bg = _R()
        self._walk_lbl.bind(pos=self._walk_fit, size=self._walk_fit)
        # NOT red. Red on this tool means Delete / Rebirth — things that
        # destroy — and this is the button that SAVES the walk. Worded as
        # what it does, which no colour can be misread into contradicting.
        stop = Button(text=tr("Stop & save"), size_hint_x=None, width=dp(132),
                      bold=True, background_normal="",
                      background_color=theme.hex_to_rgba(theme.COLORS["accent"]),
                      color=theme.hex_to_rgba(theme.COLORS["background"]))
        stop.bind(on_release=lambda *_: self.end_walk())
        top.add_widget(self._walk_lbl)
        top.add_widget(stop)
        self._walk_hint = self._walk_text("", "14sp", "text_secondary")
        hud.add_widget(top)
        hud.add_widget(self._walk_hint)
        self._walk_hud = hud
        self.add_widget(hud, index=len(self.children))   # top of the screen
        self._walk_step_hint()

    def _walk_step_hint(self):
        """The one sentence telling the operator what to do with their body,
        right now. Re-read after every ping because the answer changes."""
        w = getattr(self, "_walk", None)
        hint = getattr(self, "_walk_hint", None)
        if w is None or hint is None:
            return
        if w.state == "lost":
            hint.text = tr("This is the edge of its reach. Press Stop & save "
                           "to keep it — or walk on a little to check it "
                           "stays lost.")
            hint.color = theme.hex_to_rgba(theme.COLORS["warning_yellow"])
            return
        if not w.samples:
            hint.text = tr("Start walking away from the node, in a straight "
                           "line if the ground lets you.")
        else:
            hint.text = tr("Keep walking. Node Medic pings every 20 seconds "
                           "and will flash when the mesh drops.")
        hint.color = theme.hex_to_rgba(theme.COLORS["text_secondary"])

    def _walk_fit(self, *_):
        self._walk_bg.pos = self._walk_lbl.pos
        self._walk_bg.size = self._walk_lbl.size

    def _walk_tick(self, _dt):
        import time as _t
        w = getattr(self, "_walk", None)
        if w is None or not w.due(_t.time()):
            return
        w.begin_ping(_t.time())

        def _report(ok, snr_db=None):
            from kivy.clock import Clock as _Clock
            _Clock.schedule_once(lambda _d: self._walk_result(ok, snr_db), 0)
        try:
            self._walk_ping_fn(w.node_key, _report)
        except Exception:                                          # noqa: BLE001
            _report(False)

    def _walk_result(self, ok, snr_db):
        import time as _t
        w = getattr(self, "_walk", None)
        if w is None:
            return
        # Only a LIVE fix places a sample. The map's gps_reader hands back a
        # coasting fix (flag up, 0 sats) as a position, and the operator's
        # walk plan (2026-09-21) goes from the tree back through the house:
        # a silent ping stamped indoors on the anchor is a "loss at 0 km".
        from monitor.boundary_walk import walk_position
        gps = None
        try:
            gps = walk_position(self._fix_reader() if self._fix_reader else None)
        except Exception:                                          # noqa: BLE001
            gps = None
        w.ping_result(_t.time(), ok, snr_db=snr_db, gps=gps)
        self.plot.set_walk_trail(w.samples)
        if w.state != "lost":
            text, _f = w.banner()
            self._walk_lbl.text = text
            self._walk_bg_col.rgba = theme.hex_to_rgba(theme.COLORS["green"])
            self._walk_lbl.color = theme.hex_to_rgba(theme.COLORS["background"])
        self._walk_step_hint()

    def _walk_flash(self, _dt):
        """The found boundary flashes yellow/black, as specified on the bench
        (2026-08-13). Steady green otherwise — a banner that flashes for
        anything less teaches eyes to ignore it."""
        w = getattr(self, "_walk", None)
        if w is None or w.state != "lost":
            return
        self._walk_flash_on = not getattr(self, "_walk_flash_on", False)
        yellow = theme.hex_to_rgba(theme.COLORS["warning_yellow"])
        black = (0.05, 0.05, 0.05, 1)
        self._walk_lbl.text, _f = w.banner()
        if self._walk_flash_on:
            self._walk_bg_col.rgba = yellow
            self._walk_lbl.color = black
        else:
            self._walk_bg_col.rgba = black
            self._walk_lbl.color = yellow

    def end_walk(self, persist=True):
        """Stop, bank the evidence, tell the story. Safe to call idle — and
        it also cancels a GPS gate that never got as far as a walk, which is
        what the gate's Cancel button calls."""
        w = getattr(self, "_walk", None)
        self._tear_down_walk_gate()
        for ev in ("_walk_ev", "_walk_flash_ev"):
            e = getattr(self, ev, None)
            if e is not None:
                e.cancel()
                setattr(self, ev, None)
        hud = getattr(self, "_walk_hud", None)
        if hud is not None and hud.parent is not None:
            self.remove_widget(hud)
        self._walk_hud = None
        self._walk_hint = None
        self.plot.set_walk_trail([])
        self._walk = None
        self._show_placement(True)
        if w is None or not persist:
            return
        try:
            from monitor.boundary_walk import append_evidence
            from monitor.topology import MEDIC_ID
            obs, fails = w.evidence(medic_id=MEDIC_ID)
            append_evidence(obs, fails)
            from ui.requirement_popup import requirement_popup
            # "banked as range evidence" is a sentence for someone who already
            # knows this tool. SAY WHAT READS IT — otherwise a newcomer's whole
            # walk ends in a number with no consequence (operator, 2026-09-21).
            requirement_popup(
                w.summary() + "\n\n" + tr(
                    "{o} link sightings and {f} boundary losses banked as "
                    "range evidence.").format(o=len(obs), f=len(fails))
                + "\n\n" + tr(
                    "Node Medic uses this to work out how far your nodes "
                    "really reach, and where the next one should go — see "
                    "Build next on this map."),
                tr("Boundary walk finished"), False, tone="success")
        except Exception:                                          # noqa: BLE001
            pass
        cb = getattr(self, "_walk_done_cb", None)
        if cb is not None:
            try:
                cb(w)
            except Exception:                                      # noqa: BLE001
                pass

    def _show_placement(self, show):
        """Reveal or put away the place-a-node controls.

        A boundary walk turns this screen into a measuring instrument, and a
        full-width green "Use this position →" sitting under the walk banner
        reads as the way forward — it is not: it stamps a position and jumps
        into BIRTH. The operator hit exactly that, standing outdoors with a node (2026-09-21). The GPS badge and the coordinates
        STAY: during a walk they are the two most useful numbers on the
        screen. Same disable-not-just-hide rule as the manual row — an
        opacity-0 Kivy widget still takes touches.
        """
        for w in (getattr(self, "_place_row", None),
                  getattr(self, "detail_btn", None)):
            if w is None:
                continue
            w.opacity = 1 if show else 0
            w.disabled = not show
            if not show:
                w._walk_saved_h = w.height
                w.height = 0
            elif getattr(w, "_walk_saved_h", None):
                w.height = w._walk_saved_h
        if not show:
            # a half-open manual-entry row would survive the hide otherwise
            try:
                self._set_manual_shown(False)
            except Exception:                                      # noqa: BLE001
                pass
