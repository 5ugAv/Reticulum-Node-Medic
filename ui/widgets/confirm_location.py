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
from kivy.uix.label import Label
from kivy.uix.popup import Popup
from kivy.uix.textinput import TextInput
from kivy.uix.widget import Widget

from ui import theme


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
        body.add_widget(_lbl("or tap the map / Use GPS to place the pin", "12.5sp",
                             color="accent", h=18))

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
        self.plot = MapPlot(nodes=[], tiles=tiles, interactive=True,
                            on_pick=self._on_map_pick,
                            size_hint_y=None, height=dp(190))
        body.add_widget(self.plot)

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
        return f"{self._lat:.6f}, {self._lon:.6f}"

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
