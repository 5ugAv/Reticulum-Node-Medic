"""Confirm-a-node's-location gate — a map modal shown BEFORE a location is written
to a birth/adopt certificate, so a wrong or stale GPS fix (or a mistyped address)
can't silently pin a node in the wrong place (a repair crew's wasted drive).

Shows the proposed pin on a street-level map; the operator TAPS the map to move
the pin, TYPES an address to jump to it, or pulls the medic's current GPS, checks
the coordinates + reverse-geocoded address, then commits with a small, deliberate
CIRCULAR confirm button (deliberately not an inviting full-width bar — committing
a node's location should feel definitive). Reuses the SCAN map widget + the carried
MBTiles basemap so it works offline (address lookup needs net).
"""

from __future__ import annotations

from typing import Callable, Optional

from kivy.clock import Clock
from kivy.metrics import dp
from kivy.uix.anchorlayout import AnchorLayout
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.label import Label
from kivy.uix.popup import Popup
from kivy.uix.textinput import TextInput
from kivy.uix.widget import Widget

from ui import theme
from ui.i18n import tr  # i18n: wrapped — the 2026-09-22 zoom / select-here controls
from ui.map_pick import centre_of, format_pin, tile_caption

#: Map pane height, by the window's ACTUAL shape (2026-09-22). 190dp is the
#: figure proven on the landscape panel, where six fixed-height siblings
#: starved the pane (the long comment at its use). Operated portrait
#: (720x1280 — the 2026-09-22 brief) there is ~700px more height, and the
#: operator's complaint that day was "you can move it around a little bit":
#: a taller pane is part of the answer, +/− and Select here the rest. The
#: shape is READ from the Window at build time, never assumed — the app
#: goes fullscreen on whatever the display reports (ui/app.py build()).
MAP_HEIGHT_PORTRAIT_DP = 240
MAP_HEIGHT_LANDSCAPE_DP = 190


def _map_height():
    try:
        from kivy.core.window import Window
        portrait = Window.height > Window.width
    except Exception:                                              # noqa: BLE001
        portrait = False
    return dp(MAP_HEIGHT_PORTRAIT_DP if portrait else MAP_HEIGHT_LANDSCAPE_DP)


def _overlay_btn(text, width, on_tap, font="26sp", color="surface", fg="text_primary"):
    b = Button(text=text, font_size=font, bold=True, background_normal="",
               background_color=theme.hex_to_rgba(theme.COLORS[color], 0.92),
               color=theme.hex_to_rgba(theme.COLORS[fg]),
               size_hint=(None, None), width=width)
    b.bind(on_release=lambda *_: on_tap())
    return b


class _Chip(Label):
    """A one-line caption drawn OVER the map on a translucent dark strip, so
    it reads on any tile. No touch handler: taps fall through to the map."""

    def __init__(self, **kwargs):
        super().__init__(font_size="11.5sp", halign="left", valign="middle",
                         shorten=True, shorten_from="right",
                         color=theme.hex_to_rgba(theme.COLORS["text_primary"]),
                         padding=(dp(6), 0), **kwargs)
        from kivy.graphics import Color, Rectangle
        with self.canvas.before:
            Color(*theme.hex_to_rgba(theme.COLORS["background"], 0.72))
            self._bg = Rectangle(pos=self.pos, size=self.size)
        self.bind(pos=self._sync, size=self._sync)

    def _sync(self, *a):
        self._bg.pos, self._bg.size = self.pos, self.size
        self.text_size = self.size


class _Crosshair(Widget):
    """The 'icon on the map' (operator, 2026-09-22): a ring with four ticks
    fixed at the pane's centre. Moving the map moves what sits under it;
    'Select here' adopts that point. Deliberately NO touch handler — a plain
    Widget passes every touch through, so pan / pinch / tap-to-place under
    the marker keep working exactly as before."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        from kivy.graphics import Color, Line
        with self.canvas.after:
            Color(*theme.hex_to_rgba(theme.COLORS["accent"], 0.95))
            self._ring = Line(circle=(0, 0, 0), width=dp(1.6))
            self._ticks = [Line(points=[], width=dp(1.5)) for _ in range(4)]
            self._dot = Line(circle=(0, 0, 0), width=dp(1.2))
        self.bind(pos=self._redraw, size=self._redraw)

    def _redraw(self, *a):
        cx, cy = self.center
        r, gap, arm = dp(12), dp(4), dp(14)
        self._ring.circle = (cx, cy, r)
        self._dot.circle = (cx, cy, dp(1.2))
        self._ticks[0].points = [cx - r - arm, cy, cx - r - gap, cy]
        self._ticks[1].points = [cx + r + gap, cy, cx + r + arm, cy]
        self._ticks[2].points = [cx, cy - r - arm, cx, cy - r - gap]
        self._ticks[3].points = [cx, cy + r + gap, cx, cy + r + arm]


def _lbl(text, size="14sp", color="text_primary", bold=False, h=None):
    l = Label(text=text, font_size=size, bold=bold, halign="center",
              valign="middle", color=theme.hex_to_rgba(theme.COLORS[color]))
    if h is not None:
        l.size_hint_y = None
        l.height = dp(h)
    l.bind(size=lambda i, v: setattr(i, "text_size", v))
    return l


class _CircleConfirm(Widget):
    """A small round commit button — a drawn circle + checkmark. Small on purpose:
    a definitive, deliberate 'yes', not a full-width bar you press by reflex."""

    def __init__(self, on_press=None, diameter=76, **kwargs):
        super().__init__(size_hint=(None, None),
                         size=(dp(diameter), dp(diameter)), **kwargs)
        self._on_press = on_press
        from kivy.graphics import Color, Ellipse, Line
        with self.canvas:
            self._ring_c = Color(*theme.hex_to_rgba(theme.COLORS["green"]))
            self._circle = Ellipse(pos=self.pos, size=self.size)
            self._tick_c = Color(1, 1, 1, 1)
            self._tick = Line(points=[], width=dp(3.2), cap="round", joint="round")
        self.bind(pos=self._redraw, size=self._redraw)

    def _redraw(self, *a):
        self._circle.pos = self.pos
        self._circle.size = self.size
        x, y = self.pos
        w, h = self.size
        # a checkmark (y-up): down-left -> low point -> up-right
        self._tick.points = [x + w * 0.28, y + h * 0.52,
                             x + w * 0.44, y + h * 0.36,
                             x + w * 0.72, y + h * 0.66]

    def on_touch_down(self, touch):
        # circular hit-test so only a real tap on the disc commits
        cx, cy = self.center
        if ((touch.x - cx) ** 2 + (touch.y - cy) ** 2) <= (self.width / 2) ** 2:
            self._ring_c.rgba = theme.hex_to_rgba(theme.COLORS["green"], 0.6)
            if self._on_press:
                self._on_press()
            return True
        return super().on_touch_down(touch)


class ConfirmLocationPopup(Popup):
    """``on_confirm(lat, lon)`` fires when the operator accepts the (possibly
    moved) pin; ``on_cancel()`` if they back out."""

    def __init__(self, lat: float, lon: float, node_name: str = "",
                 on_confirm: Optional[Callable[[float, float], None]] = None,
                 on_cancel: Optional[Callable[[], None]] = None,
                 gps_reader: Optional[Callable[[], Optional[tuple]]] = None,
                 tiles=None, **kwargs):
        self._lat, self._lon = float(lat), float(lon)
        self._on_confirm = on_confirm
        self._on_cancel = on_cancel
        self._gps_reader = gps_reader
        self._decided = False
        self._edge = None                 # (touch, start_x) mid back-swipe-to-cancel

        who = f" — {node_name}" if node_name else ""
        body = BoxLayout(orientation="vertical", spacing=dp(6), padding=dp(10))

        # type-an-address search FIRST — at the top so it stays visible ABOVE the
        # on-screen keyboard (a modal can't pan its content up like a screen does).
        from ui.onscreen_keyboard import bind_field
        addr_row = BoxLayout(orientation="horizontal", size_hint_y=None,
                             height=dp(50), spacing=dp(6))
        self._addr_in = TextInput(hint_text="Type an address to place the pin…",
                                  multiline=False, font_size="28sp")
        bind_field(self._addr_in)
        self._addr_in.bind(on_text_validate=self._find_address)
        find = Button(text="Find", size_hint_x=None, width=dp(78), bold=True,
                      background_normal="",
                      background_color=theme.hex_to_rgba(theme.COLORS["accent"]),
                      color=theme.hex_to_rgba(theme.COLORS["background"]))
        find.bind(on_release=self._find_address)
        addr_row.add_widget(self._addr_in)
        addr_row.add_widget(find)
        body.add_widget(addr_row)
        # search status / address feedback lives HERE (top) so it's visible above
        # the on-screen keyboard — a failed lookup must never look like nothing.
        # Name the button that is actually NEXT TO the field (operator,
        # 2026-08-14: the caption said 'Show address', which lives below the
        # map and is off-view with the keyboard up — a caption must not point
        # at a control the eye cannot find).
        self._addr = _lbl("Type an address and tap Find to look up the spot "
                          "online (optional).",
                          "12.5sp", color="text_secondary", h=30)
        body.add_widget(self._addr)
        body.add_widget(_lbl(tr("or tap the map · pan under the crosshair and "
                                "Select here · Use GPS"),
                             "12.5sp", color="accent", h=18))

        # the map (reused SCAN widget) with a draggable-by-tap pin
        if tiles is None:
            try:
                from ui.map_tiles import MAPS_DIR, find_mbtiles
                tiles = find_mbtiles(MAPS_DIR)
            except Exception:
                tiles = None
        from ui.screens.scan_screen import MapPlot
        # MapPlot's on_pick fires with a SINGLE (lat, lon) TUPLE — unpack it (and
        # never let a touch-callback error crash the app).
        # A GUARANTEED minimum height, not whatever the other rows leave over.
        #
        # This widget had no size of its own, so it took size_hint_y=1 against
        # SIX fixed-height siblings in the same vertical BoxLayout — on the
        # real medic (800x480, KIVY_METRICS_DENSITY=1.5) those siblings alone
        # summed to more than the popup's own height, leaving the map a sliver
        # or nothing at all. What the operator saw and called "smeared, not
        # clear" (2026-09-06, with a photo) was a real map tile, correctly
        # decoded (checked by hand: a clean JPEG, PIL opens it fine) — but
        # overzoomed for a location outside this medic's downloaded detail
        # AND squashed into a strip a few dozen pixels tall. A blurry crop
        # blown up 8x and then viewed through a two-pixel-wide letterbox
        # reads as flat horizontal bands, because that is almost exactly what
        # it is.
        #
        # 190dp is deliberately fixed rather than computed: this is the same
        # "the one thing that matters gets starved by fixed-height siblings"
        # shape as the BIRTH parts list clipping (2026-08-xx) — a resizing
        # widget under several rigid neighbours is the wrong tool here.
        # (2026-09-22: still fixed — 190 landscape / 240 portrait, chosen by
        # the Window's real shape; see _map_height.)
        #
        # ZOOM + SELECT HERE (operator, 2026-09-22: "there is no option to
        # zoom in to the map ... select a location by pressing an icon on the
        # map and saying 'select here'"). MapPlot always had pinch-to-zoom
        # and tap-to-place; what it lacked HERE was SCAN's explicit +/− (a
        # 7-inch panel's pinch is not something to depend on), a fixed centre
        # marker, and a button that adopts the centre. Overlaid in a
        # FloatLayout like SCAN's, so no extra rows compete for height:
        #   top-left  — an honest caption: which zoom, how many of its tiles
        #               the CARRIED cache has (never fetched mid-birth)
        #   top-right — + / −, greyed where a press would do nothing
        #   centre    — the crosshair
        #   bottom    — 'Select here' carrying the live centre coordinates,
        #               so the number it will save is the number it shows
        map_wrap = FloatLayout(size_hint_y=None, height=_map_height())
        self.plot = MapPlot(nodes=[], tiles=tiles, interactive=True,
                            on_pick=self._on_map_pick, on_view=self._on_view,
                            size_hint=(1, 1), pos_hint={"x": 0, "y": 0})
        map_wrap.add_widget(self.plot)
        map_wrap.add_widget(_Crosshair(size_hint=(1, 1), pos_hint={"x": 0, "y": 0}))
        self._tiles_lbl = _Chip(size_hint=(0.64, None), height=dp(22),
                                pos_hint={"x": 0.01, "top": 0.99})
        map_wrap.add_widget(self._tiles_lbl)
        zbox = BoxLayout(orientation="vertical", size_hint=(None, None),
                         size=(dp(50), dp(104)), spacing=dp(6),
                         pos_hint={"right": 0.98, "top": 0.98})
        self._zoom_btns = {}
        for sym, d in (("+", +1), ("−", -1)):
            zb = _overlay_btn(sym, dp(50), lambda dd=d: self.plot.zoom_by(dd))
            zb.height = dp(49)
            self._zoom_btns[d] = zb
            zbox.add_widget(zb)
        map_wrap.add_widget(zbox)
        self._select_btn = _overlay_btn(tr("Select here"), dp(300), self._select_here,
                                        font="13.5sp", color="accent",
                                        fg="background")
        self._select_btn.height = dp(40)
        self._select_btn.pos_hint = {"center_x": 0.5, "y": 0.03}
        map_wrap.add_widget(self._select_btn)
        body.add_widget(map_wrap)
        if tiles is None:
            # No carried basemap: MapPlot draws nothing and takes no touch,
            # so say so and grey the controls that would press into nothing.
            self._tiles_lbl.text = tile_caption(None, 0, 0, zooms=[])
            for zb in self._zoom_btns.values():
                zb.disabled = True
            self._select_btn.disabled = True

        # coords (always shown, offline) + an OPT-IN "Show address" button. Address
        # lookup is NOT automatic: reverse-geocoding sends the exact pin to a third
        # party (OpenStreetMap), so it only happens when the operator asks. The map +
        # coordinates are fully offline and enough to confirm placement.
        coord_row = BoxLayout(orientation="horizontal", size_hint_y=None,
                              height=dp(34), spacing=dp(8))
        self._coords = _lbl(self._coord_text(), "14sp", bold=True, h=34)
        show_addr = Button(text="Show address (online)", size_hint_x=None,
                           width=dp(168), font_size="12.5sp", bold=True,
                           background_normal="",
                           background_color=theme.hex_to_rgba(theme.COLORS["surface"]),
                           color=theme.hex_to_rgba(theme.COLORS["text_primary"]))
        show_addr.bind(on_release=lambda *_: self._refresh_address())
        coord_row.add_widget(self._coords)
        coord_row.add_widget(show_addr)
        body.add_widget(coord_row)

        # controls: Cancel + Use GPS on the left; a small round commit on the right
        row = BoxLayout(orientation="horizontal", size_hint_y=None, height=dp(84),
                        spacing=dp(8))
        cancel = Button(text="Cancel", bold=True, font_size="15sp", size_hint_x=0.3,
                        background_normal="",
                        background_color=theme.hex_to_rgba(theme.COLORS["surface"]),
                        color=theme.hex_to_rgba(theme.COLORS["text_primary"]))
        cancel.bind(on_release=lambda *_: self._cancel())
        row.add_widget(cancel)
        if gps_reader is not None:
            usegps = Button(text="Use GPS", bold=True, font_size="14sp",
                            size_hint_x=0.3, background_normal="",
                            background_color=theme.hex_to_rgba(theme.COLORS["accent"]),
                            color=theme.hex_to_rgba(theme.COLORS["background"]))
            usegps.bind(on_release=lambda *_: self._use_gps())
            row.add_widget(usegps)
        row.add_widget(Widget())                       # push the commit to the right
        commit = BoxLayout(orientation="vertical", size_hint_x=None, width=dp(96),
                           spacing=dp(2))
        holder = AnchorLayout(anchor_x="center", anchor_y="center")
        holder.add_widget(_CircleConfirm(on_press=self._confirm))
        commit.add_widget(holder)
        commit.add_widget(_lbl("Confirm", "11.5sp", color="text_secondary", h=16))
        row.add_widget(commit)
        body.add_widget(row)

        super().__init__(title=f"Confirm this node's location{who}",
                         content=body, size_hint=(0.96, 0.94),
                         auto_dismiss=False, **kwargs)
        Clock.schedule_once(lambda *_: self.plot.focus((self._lat, self._lon)), 0)
        # No automatic reverse-geocode — the operator taps "Show address" if they
        # want it (that sends the pin to OpenStreetMap). Confirm offline by default.

    # -- pin movement ------------------------------------------------------
    def _coord_text(self):
        # The SAVED pair, printed the one way (ui/map_pick.format_pin) — the
        # certificate gets exactly these floats (see _confirm).
        return tr("Pin: {coords}").format(coords=format_pin(self._lat, self._lon))

    def _on_view(self, view):
        """After every drawn view (MapPlot.on_view): the crosshair's centre
        onto the Select-here button, the honest zoom/tiles caption, and +/−
        greyed where a press would change nothing. Reads the drawer's own
        record of the draw — never a fresh tile query, never the network."""
        lat, lon = centre_of(view)
        self._select_btn.text = tr("Select here") + "   " + format_pin(lat, lon)
        rep = self.plot.view_tile_report()
        z, present, total = rep if rep else (view.zoom, 0, 0)
        self._tiles_lbl.text = tile_caption(z, present, total,
                                            zooms=self.plot._zooms)
        for d, zb in self._zoom_btns.items():
            zb.disabled = not self.plot.can_zoom(d)
        self._select_btn.disabled = False

    def _select_here(self):
        """'Select here' (operator, 2026-09-22): adopt the point under the
        crosshair — the map's drawn centre — as the pin, through the same
        _move_pin every other road (tap, address, GPS) uses. The numbers on
        the button and the numbers saved are the same floats."""
        ll = self.plot.centre_latlon()
        if ll is None:
            self._addr.text = tr("No map drawn yet — type an address or use GPS.")
            return
        self._move_pin(ll[0], ll[1])

    def _on_map_pick(self, latlon):
        """MapPlot tap-to-place: it passes a (lat, lon) tuple. Guarded so a bad
        touch can never propagate up and kill the app."""
        try:
            self._move_pin(latlon[0], latlon[1])
        except Exception:
            pass

    def _move_pin(self, lat, lon):
        self._lat, self._lon = float(lat), float(lon)
        self._coords.text = self._coord_text()
        self.plot._me = (self._lat, self._lon)     # move the pin, keep the view
        self.plot._trigger()
        self._addr.text = ("Pin placed — coordinates are below the map. "
                           "'Show address (online)' looks up its street "
                           "address.")

    def _find_address(self, *a):
        q = (self._addr_in.text or "").strip()
        if not q:
            return
        self._addr.text = "Searching…"
        import threading

        def work():
            res = None
            try:
                from monitor.geo import geocode_address
                res = geocode_address(q)
            except Exception:
                res = None
            if res:
                Clock.schedule_once(lambda *_: (
                    self.plot.focus((res["lat"], res["lon"])),
                    self._move_pin(res["lat"], res["lon"])), 0)
            else:
                Clock.schedule_once(lambda *_: setattr(
                    self._addr, "text",
                    "Couldn't look up that address (lookup busy or no match) — "
                    "tap the map or use GPS instead."), 0)
        threading.Thread(target=work, daemon=True).start()

    def _use_gps(self):
        if self._gps_reader is None:
            return
        try:
            fix = self._gps_reader()
        except Exception:
            fix = None
        if fix:
            self.plot.focus((fix[0], fix[1]))       # recentre on the live fix
            self._move_pin(fix[0], fix[1])
        else:
            self._addr.text = "No GPS fix right now — tap the map instead."

    def _refresh_address(self):
        import threading
        lat, lon = self._lat, self._lon
        self._addr.text = "Looking up address…"

        def work():
            addr = None
            try:
                from monitor.geo import reverse_geocode
                addr = reverse_geocode(lat, lon)
            except Exception:
                addr = None
            if (lat, lon) != (self._lat, self._lon):
                return                              # pin moved again; stale result
            Clock.schedule_once(lambda *_: setattr(
                self._addr, "text", addr or "(no address — offline; judge by the map)"), 0)
        threading.Thread(target=work, daemon=True).start()

    # -- back-swipe-to-cancel (a modal captures the screen's edge gesture) --
    _EDGE_DP = 26
    _TRIGGER_DP = 55

    def on_touch_down(self, touch):
        if touch.x - self.x <= dp(self._EDGE_DP):
            self._edge = (touch, touch.x)     # claim the left edge for 'back'
            return True
        return super().on_touch_down(touch)

    def on_touch_move(self, touch):
        if self._edge and touch is self._edge[0]:
            if touch.x - self._edge[1] >= dp(self._TRIGGER_DP):
                self._edge = None
                self._cancel()                # swipe right from the edge = cancel
            return True
        return super().on_touch_move(touch)

    def on_touch_up(self, touch):
        if self._edge and touch is self._edge[0]:
            self._edge = None
            return True
        return super().on_touch_up(touch)

    # -- decision ----------------------------------------------------------
    def _confirm(self):
        if self._decided:
            return
        self._decided = True
        self.dismiss()
        if self._on_confirm:
            self._on_confirm(self._lat, self._lon)

    def _cancel(self):
        if self._decided:
            return
        self._decided = True
        self.dismiss()
        if self._on_cancel:
            self._on_cancel()
