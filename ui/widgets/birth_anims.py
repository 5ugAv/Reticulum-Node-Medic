"""Animations for the guided birth wizard.

Each animation shows the physical action (plug a board in, insert an SD card) with
a looping motion. If a cartoon PNG is present in ``assets/ui/anim/`` it's used;
otherwise a schematic vector fallback draws in its place — so the flow works now
and simply gets prettier when the artwork is dropped in (no code change):

    assets/ui/anim/node_medic.png    # the Node Medic body
    assets/ui/anim/sd_card.png       # the SD card
    assets/ui/anim/radio_board.png   # the radio board

the designer's artwork replaces the placeholders by filename.
"""

from __future__ import annotations

import math
import os

from kivy.animation import Animation
from kivy.graphics import (Color, Line, Quad, Rectangle, RoundedRectangle,
                           StencilPop, StencilPush, StencilUnUse, StencilUse)
from kivy.metrics import dp
from kivy.properties import NumericProperty
from kivy.uix.label import Label
from kivy.uix.widget import Widget

from ui import theme

_ANIM_DIR = os.path.normpath(os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    os.pardir, "assets", "ui", "anim"))
MEDIC_PNG = os.path.join(_ANIM_DIR, "node_medic.png")             # angled medic (SD step)
MEDIC_CABLE_PNG = os.path.join(_ANIM_DIR, "node_medic_cable.png")  # medic w/ USB cable
LORA_PNG = os.path.join(_ANIM_DIR, "lora32.png")                   # the radio board
SD_READER_PNG = os.path.join(_ANIM_DIR, "sd_reader.png")           # microSD + card reader (combined)
SD_READER_BODY_PNG = os.path.join(_ANIM_DIR, "sd_reader_body.png")  # card reader alone
SD_PNG = os.path.join(_ANIM_DIR, "sd_card.png")                    # the microSD card alone
BOARD_PNG = os.path.join(_ANIM_DIR, "radio_board.png")

#: The medic's USB plug tip within node_medic_cable.png (normalised, from top-left).
#: The board's bottom USB port descends onto this point.
_PLUG_TIP = (0.041, 0.583)

_TEX_CACHE: dict = {}


def _texture(path):
    """A GL texture for *path*, or None if the file is absent/unreadable. Cached
    (per run) so the placeholder check isn't repeated every frame."""
    if path in _TEX_CACHE:
        return _TEX_CACHE[path]
    tex = None
    if path and os.path.exists(path):
        try:
            from kivy.core.image import Image as CoreImage
            tex = CoreImage(path).texture
        except Exception:
            tex = None
    _TEX_CACHE[path] = tex
    return tex


class _LoopAnim(Widget):
    """Base: a ``phase`` 0→1 that loops while the step is shown. Subclasses draw
    themselves from ``phase`` in ``_draw``. Text (fallback labels) uses child
    Labels repositioned each frame since canvas text is awkward."""

    phase = NumericProperty(0.0)

    def __init__(self, duration=2.2, **kwargs):
        super().__init__(**kwargs)
        self._duration = duration
        self._anim = None
        self._ev = None
        self._labels = {}
        self.bind(phase=self._redraw, pos=self._redraw, size=self._redraw)

    def _label(self, key, **kw):
        lbl = self._labels.get(key)
        if lbl is None:
            lbl = Label(**kw)
            lbl.bind(size=lambda i, v: setattr(i, "text_size", v))
            self._labels[key] = lbl
            self.add_widget(lbl)
        lbl.opacity = 1
        return lbl

    def _hide_label(self, key):
        lbl = self._labels.get(key)
        if lbl is not None:
            lbl.opacity = 0

    def start(self):
        # Clock-driven so it ACTUALLY loops: Kivy's Animation(repeat=True) reaches
        # phase 1.0 and freezes (the board just stuck docked). A ticked phase that
        # wraps 0->1->0 loops the descend/pulse until the step ends or is stopped.
        from kivy.clock import Clock
        self.stop()
        self.phase = 0.0
        # 30 fps, not 60: each tick does a full canvas clear+rebuild, and these
        # descend/pulse loops look identical at 30 while halving the redraw cost.
        self._ev = Clock.schedule_interval(self._tick, 1 / 30.0)

    def _tick(self, dt):
        self.phase = (self.phase + dt / max(0.1, self._duration)) % 1.0

    def stop(self):
        if self._ev is not None:
            self._ev.cancel()
            self._ev = None

    def _redraw(self, *_):
        self.canvas.clear()
        if self.width < dp(40) or self.height < dp(40):
            return
        self._draw()

    def _draw(self):
        raise NotImplementedError


def _draw_medic_vector(x, y, w, h):
    """Fallback Node Medic: a rounded slab with a red cross."""
    Color(*theme.hex_to_rgba(theme.COLORS["surface"]))
    RoundedRectangle(pos=(x, y), size=(w, h), radius=[dp(10)] * 4)
    Color(*theme.hex_to_rgba(theme.COLORS["red"]))
    t = min(w, h) * 0.14
    cx, cy = x + w / 2, y + h / 2
    arm = min(w, h) * 0.28
    RoundedRectangle(pos=(cx - t / 2, cy - arm), size=(t, 2 * arm), radius=[t / 2] * 4)
    RoundedRectangle(pos=(cx - arm, cy - t / 2), size=(2 * arm, t), radius=[t / 2] * 4)


class ConnectBoardAnim(_LoopAnim):
    """The LoRa32 radio board descends from above onto the Node Medic's USB plug.
    Uses the illustrated sprites (medic-with-cable on the right, board small on the
    left, docking on the plug tip); falls back to a schematic if the art is absent."""

    burst = NumericProperty(0.0)
    rise = NumericProperty(0.0)                       # "Connected!" banner slide-up

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._connected = False
        self._conn_tex = None                         # cached "Connected!" glyph texture
        self.bind(burst=self._redraw, rise=self._redraw)

    def mark_connected(self):
        """The medic sensed a board on USB — stop looping, dock the board, and fire
        a celebratory burst: four green ripples radiating from the plug/board
        junction while a big 'Connected!' banner pops up from the bottom."""
        if self._connected:
            return
        self._connected = True
        self.stop()                                   # halt the descend loop
        self.phase = 1.0                              # freeze the board docked
        # pre-render the banner glyphs to a texture so it draws in-canvas (child
        # Labels don't reliably render inside this canvas-drawing widget).
        try:
            from kivy.core.text import Label as CoreLabel
            cl = CoreLabel(text="Connected!", font_size=dp(34), bold=True)
            cl.refresh()
            self._conn_tex = cl.texture
        except Exception:
            self._conn_tex = None
        Animation(burst=1.0, duration=1.1, t="out_quad").start(self)
        Animation(rise=1.0, duration=0.55, t="out_back").start(self)

    def _draw(self):
        medic_tex, board_tex = _texture(MEDIC_CABLE_PNG), _texture(LORA_PNG)
        if medic_tex is None or board_tex is None:
            return self._draw_fallback()
        x, y, w, h = self.x, self.y, self.width, self.height
        # medic: anchored right, scaled to fill the height (capped so it never
        # eats more than 60% of the width), aspect preserved.
        ma = medic_tex.width / float(medic_tex.height)
        mh = h * 0.96
        mw = mh * ma
        if mw > w * 0.60:
            mw = w * 0.60
            mh = mw / ma
        mx = x + w - mw - dp(4)
        my = y + (h - mh) / 2.0
        tipx = mx + _PLUG_TIP[0] * mw
        tipy = my + (1.0 - _PLUG_TIP[1]) * mh        # norm-from-top -> kivy y-up
        # board: small (¼ the medic height), bottom USB port descends onto the tip
        ba = board_tex.width / float(board_tex.height)
        bh = mh * 0.25
        bw = bh * ba
        p = min(1.0, self.phase / 0.9)
        start_y = tipy + h * 0.42                     # begins above, moves down
        by = start_y - (start_y - tipy) * p
        bx = tipx - bw / 2.0
        with self.canvas:
            Color(1, 1, 1, 1)
            Rectangle(texture=medic_tex, pos=(mx, my), size=(mw, mh))
            Color(1, 1, 1, 1)
            Rectangle(texture=board_tex, pos=(bx, by), size=(bw, bh))
            if self._connected:                      # 4 green ripples from the junction
                maxr = min(w, h) * 0.52
                for i in range(4):
                    f = self.burst - i * 0.16         # stagger so they radiate outward
                    if f <= 0.0:
                        continue
                    f = min(1.0, f)
                    Color(0.2, 0.9, 0.4, (1.0 - f) * 0.9)   # fade as each ring grows
                    Line(circle=(tipx, tipy, dp(10) + f * maxr), width=dp(3.0))
                # big "Connected!" banner rising from the bottom of the panel
                if self._conn_tex is not None:
                    tw, th = self._conn_tex.size
                    start_y, target_y = y - dp(56), y + h * 0.24
                    by = start_y + (target_y - start_y) * self.rise
                    g = theme.hex_to_rgba(theme.COLORS["green"])
                    Color(g[0], g[1], g[2], self.rise)
                    Rectangle(texture=self._conn_tex,
                              pos=(x + (w - tw) / 2.0, by), size=(tw, th))
        self._hide_label("medic")
        self._hide_label("board")

    def _draw_fallback(self):
        """Schematic (no art): a board slides in from the left into the medic."""
        x, y, w, h = self.x, self.y, self.width, self.height
        cy = y + h / 2
        mw, mh = w * 0.40, h * 0.62
        mx, my = x + w - mw, cy - mh / 2
        bw, bh = w * 0.24, h * 0.30
        start_x = x + w * 0.04
        dock_x = mx - dp(16) - bw
        bx = start_x + (dock_x - start_x) * min(1.0, self.phase / 0.85)
        with self.canvas:
            _draw_medic_vector(mx, my, mw, mh)
            Color(*theme.hex_to_rgba(theme.COLORS["accent"]))
            RoundedRectangle(pos=(bx, cy - bh / 2), size=(bw, bh), radius=[dp(6)] * 4)
            Color(*theme.hex_to_rgba(theme.COLORS["text_secondary"]))
            Line(points=[bx + bw, cy, mx, cy], width=dp(2))
        board = self._label("board", text="radio\nboard", font_size="13sp",
                            bold=True, halign="center", valign="middle",
                            color=theme.hex_to_rgba(theme.COLORS["background"]))
        board.size = (bw, bh)
        board.pos = (bx, cy - bh / 2)


class ConnectAntennaAnim(_LoopAnim):
    """Antenna-first, showing BOTH connector types in one looped story: a tiny
    U.FL / IPEX plug clicks DOWN onto the board's socket (phase 1), then an SMA
    antenna SCREWS onto the pigtail's threaded jack (phase 2, with a rotating hex
    nut + a curved screw arrow). Pure vector — no art asset needed. Labelled so a
    first-timer can tell which connector their board/antenna has."""

    def __init__(self, **kwargs):
        super().__init__(duration=3.4, **kwargs)

    def _draw(self):
        x, y, w, h = self.x, self.y, self.width, self.height
        ph1 = min(1.0, self.phase / 0.5)                 # U.FL push-to-click
        ph2 = max(0.0, min(1.0, (self.phase - 0.5) / 0.45))   # SMA screw-on
        # board (lower-left) with a U.FL socket on its top edge
        bw, bh = w * 0.34, h * 0.16
        bx, by = x + w * 0.05, y + h * 0.24
        sock_x, sock_y = bx + bw * 0.62, by + bh
        # SMA jack (threaded post), mid-right
        jack_x, jack_y = x + w * 0.72, y + h * 0.46
        jw, jh = dp(15), dp(22)
        with self.canvas:
            # --- board + U.FL socket
            Color(*theme.hex_to_rgba(theme.COLORS["surface"]))
            RoundedRectangle(pos=(bx, by), size=(bw, bh), radius=[dp(6)] * 4)
            Color(*theme.hex_to_rgba(theme.COLORS["background"]))
            RoundedRectangle(pos=(bx + bw * 0.10, by + bh * 0.28),
                             size=(bw * 0.24, bh * 0.44), radius=[dp(2)] * 4)
            Color(*theme.hex_to_rgba(theme.COLORS["accent"]))
            Line(circle=(sock_x, sock_y, dp(7)), width=dp(2))
            # --- U.FL plug descends onto the socket (phase 1)
            plug_y = sock_y + h * 0.24 * (1.0 - ph1)
            Color(*theme.hex_to_rgba(theme.COLORS["accent"]))
            RoundedRectangle(pos=(sock_x - dp(8), plug_y - dp(2)),
                             size=(dp(16), dp(11)), radius=[dp(3)] * 4)
            if ph1 < 1.0:                                # short whip above the plug
                Color(*theme.hex_to_rgba(theme.COLORS["text_primary"]))
                Line(points=[sock_x, plug_y + dp(9), sock_x, plug_y + dp(9) + h * 0.16],
                     width=dp(3))
            else:                                        # seated: pigtail runs to the SMA jack
                Color(*theme.hex_to_rgba(theme.COLORS["text_secondary"]))
                Line(points=[sock_x, sock_y + dp(6),
                             (sock_x + jack_x) / 2.0, sock_y + h * 0.20,
                             jack_x, jack_y - jh / 2], width=dp(2.5))
            # --- SMA jack (threaded post) at the pigtail end
            Color(*theme.hex_to_rgba(theme.COLORS["text_secondary"]))
            RoundedRectangle(pos=(jack_x - jw / 2, jack_y - jh / 2), size=(jw, jh),
                             radius=[dp(2)] * 4)
            Color(*theme.hex_to_rgba(theme.COLORS["background"]))
            for i in range(3):
                yy = jack_y - jh / 2 + jh * (0.30 + 0.20 * i)
                Line(points=[jack_x - jw / 2, yy, jack_x + jw / 2, yy], width=dp(1))
            # --- SMA antenna: hex nut screws down onto the jack (phase 2)
            if ph1 >= 1.0:
                ant_y = jack_y + jh / 2 + h * 0.22 * (1.0 - ph2)
                rot = ph2 * math.pi / 2                   # rotate as it threads on
                Color(*theme.hex_to_rgba(theme.COLORS["green"]))
                pts = []
                for i in range(7):
                    a = math.pi / 6 + i * math.pi / 3 + rot
                    pts += [jack_x + dp(9) * math.cos(a), ant_y + dp(9) * math.sin(a)]
                Line(points=pts, width=dp(2.2))
                Color(*theme.hex_to_rgba(theme.COLORS["text_primary"]))
                Line(points=[jack_x, ant_y + dp(9), jack_x, ant_y + dp(9) + h * 0.20],
                     width=dp(3))
                Color(*theme.hex_to_rgba(theme.COLORS["green"]))
                Line(circle=(jack_x, ant_y + dp(9) + h * 0.20, dp(4)), width=dp(2))
                if 0.1 < ph2 < 1.0:                       # curved "screw" arrow
                    Color(*theme.hex_to_rgba(theme.COLORS["accent"], 0.9))
                    Line(circle=(jack_x, ant_y, dp(15), 20, 210), width=dp(2))
        # connector labels (persistent so both types are always identifiable)
        l1 = self._label("ufl", text="U.FL / IPEX\npush to click", font_size="11.5sp",
                         bold=True, halign="center", valign="middle",
                         color=theme.hex_to_rgba(theme.COLORS["accent"]))
        l1.size = (w * 0.42, dp(30)); l1.pos = (sock_x - w * 0.21, by - dp(34))
        l2 = self._label("sma", text="SMA\nscrew on", font_size="11.5sp", bold=True,
                         halign="center", valign="middle",
                         color=theme.hex_to_rgba(theme.COLORS["green"]))
        l2.size = (w * 0.30, dp(30)); l2.pos = (jack_x - w * 0.15, jack_y - jh / 2 - dp(34))


class ProvisionAnim(_LoopAnim):
    """PLACEHOLDER: Node Medic configuring the node over its setup WiFi — the medic
    (right) and the small node (left) with pulsing WiFi arcs between them. Rough
    stand-in for the designer's artwork; the geometry + intent are what's set."""

    def _draw(self):
        medic = _texture(MEDIC_PNG)
        node = _texture(LORA_PNG)
        x, y, w, h = self.x, self.y, self.width, self.height
        cy = y + h / 2
        with self.canvas:
            if medic is not None:
                ma = medic.width / float(medic.height)
                mh = h * 0.72
                mw = mh * ma
                Color(1, 1, 1, 1)
                Rectangle(texture=medic, pos=(x + w - mw - dp(6), cy - mh / 2),
                          size=(mw, mh))
            if node is not None:
                na = node.width / float(node.height)
                nh = h * 0.34
                nw = nh * na
                Color(1, 1, 1, 1)
                Rectangle(texture=node, pos=(x + w * 0.06, cy - nh / 2), size=(nw, nh))
            # pulsing WiFi arcs from the node toward the medic (3 staggered rings)
            ax = x + w * 0.30
            for i in range(3):
                p = (self.phase + i / 3.0) % 1.0
                Color(*theme.hex_to_rgba(theme.COLORS["accent"], max(0.0, 1.0 - p)))
                r = dp(8) + p * dp(60)
                Line(circle=(ax, cy, r, -35, 35), width=dp(3))
        lbl = self._label("prov", text="Node Medic is setting up your node…",
                         font_size="13sp", bold=True, halign="center", valign="middle",
                         color=theme.hex_to_rgba(theme.COLORS["accent"]))
        lbl.size = (w, dp(24))
        lbl.pos = (x, y + dp(4))


class InsertSdAnim(_LoopAnim):
    """Two-phase: the microSD card slides into the card reader, then the reader +
    card move together toward the Node Medic (it has no native card slot). Uses the
    illustrated sprites; falls back to a schematic if the art is absent."""

    # card position RELATIVE to the reader top-left, in the SOURCE image px the
    # sprites were cut from (y DOWN): start = card sitting below-left of the reader;
    # inserted = slid up into the slot. Scaled by the reader's screen scale.
    _CARD_START = (-235.0, 618.0)
    _CARD_IN = (-30.0, 250.0)
    #: Slot mouth as an ANGLED line across the reader sprite (fractions, y-DOWN):
    #: two points on the thin light edge just above the "microSD" label. The card is
    #: clipped to below this line so its inserted portion vanishes into the angled
    #: slot (measured from sd_reader_body.png — the slot descends left→right).
    _SLOT_L = (0.10, 0.505)
    _SLOT_R = (0.50, 0.705)

    def __init__(self, **kwargs):
        kwargs.setdefault("duration", 3.8)           # three phases -> a touch slower
        super().__init__(**kwargs)

    def _blit(self, tex, tlx, tly, w, h):
        """Draw *tex* given its TOP-LEFT in a y-DOWN widget frame (0,0 = top-left)."""
        Color(1, 1, 1, 1)
        Rectangle(texture=tex, pos=(self.x + tlx, self.y + self.height - tly - h),
                  size=(w, h))

    def _kv(self, x, y_down):
        """y-DOWN widget point -> Kivy (y-up) window point."""
        return (self.x + x, self.y + self.height - y_down)

    def _draw(self):
        medic = _texture(MEDIC_PNG)
        reader = _texture(SD_READER_BODY_PNG)
        card = _texture(SD_PNG)
        if medic is None or reader is None or card is None:
            return self._draw_fallback()
        w, h = self.width, self.height
        # medic anchored upper-right (smaller, so the arrow has room to U-turn below)
        ma = medic.width / float(medic.height)
        mh = h * 0.64
        mw = mh * ma
        if mw > w * 0.42:
            mw = w * 0.42
            mh = mw / ma
        m_tlx, m_tly = w - mw - dp(6), 0.03 * h
        # reader + card, left / upper-middle; card scaled by the same source scale
        s = (0.34 * h) / reader.height
        rw, rh = reader.width * s, reader.height * s
        cw, ch = card.width * s, card.height * s
        rtx, rty = 0.05 * w, 0.20 * h
        start_rel = (self._CARD_START[0] * s, self._CARD_START[1] * s)
        in_rel = (self._CARD_IN[0] * s, self._CARD_IN[1] * s)
        # phase 1 (0-0.35): card slides into the reader
        t1 = min(1.0, self.phase / 0.35)
        crel = (start_rel[0] + (in_rel[0] - start_rel[0]) * t1,
                start_rel[1] + (in_rel[1] - start_rel[1]) * t1)
        # arrow: reader-bottom -> DOWN -> U-turn -> UP to the medic's bottom-centre
        S = (rtx + rw * 0.5, rty + rh)
        E = (m_tlx + mw * 0.5, m_tly + mh)
        C1, C2 = (S[0], 0.93 * h), (E[0], 0.93 * h)

        def bez(t):
            u = 1 - t
            return (u ** 3 * S[0] + 3 * u * u * t * C1[0] + 3 * u * t * t * C2[0] + t ** 3 * E[0],
                    u ** 3 * S[1] + 3 * u * u * t * C1[1] + 3 * u * t * t * C2[1] + t ** 3 * E[1])

        # The slot MOUTH is an ANGLED line (it descends left→right, following the
        # reader's perspective). Clip the card to BELOW that line so the part that
        # has slid into the reader disappears along the real slot edge — it reads as
        # inserting into the angled slot, not overlapping flat on top.
        def slot_kivy_at(X):
            """Kivy y of the slot line at widget-absolute x=X (extends the two
            measured slot points across the whole widget)."""
            f = (X - self.x - rtx) / rw
            L, R = self._SLOT_L, self._SLOT_R
            fy = L[1] + (R[1] - L[1]) / (R[0] - L[0]) * (f - L[0])
            return self.y + self.height - (rty + fy * rh)

        kL = slot_kivy_at(self.x)
        kR = slot_kivy_at(self.x + self.width)
        # quad covering everything BELOW the angled line (the card's visible side)
        slot_mask = [self.x, self.y, self.x + self.width, self.y,
                     self.x + self.width, kR, self.x, kL]
        with self.canvas:
            self._blit(medic, m_tlx, m_tly, mw, mh)
            self._blit(reader, rtx, rty, rw, rh)
            StencilPush()
            Quad(points=slot_mask)
            StencilUse()
            self._blit(card, rtx + crel[0], rty + crel[1], cw, ch)
            StencilUnUse()
            Quad(points=slot_mask)
            StencilPop()
            if self.phase > 0.4:                      # phase 2 (0.4-0.85): arrow travels
                q = min(1.0, (self.phase - 0.4) / 0.45)
                n = 48
                k = max(2, int(n * q))
                pts = []
                for i in range(k + 1):
                    pts += list(self._kv(*bez(i / n)))
                Color(*theme.hex_to_rgba(theme.COLORS["red"]))   # red — Node Medic scheme
                Line(points=pts, width=dp(3.4), joint="round", cap="round")
                # arrowhead — computed in KIVY (y-up) space so the head points the
                # way the line is travelling (mixing y-down angle with the y-flip
                # used to make it face backwards)
                tip = self._kv(*bez(k / n))
                prev = self._kv(*bez((k - 1) / n))
                ang = math.atan2(tip[1] - prev[1], tip[0] - prev[0])
                for a in (ang + 2.6, ang - 2.6):      # barbs trail behind the tip
                    barb = (tip[0] + dp(13) * math.cos(a), tip[1] + dp(13) * math.sin(a))
                    Line(points=[tip[0], tip[1], barb[0], barb[1]],
                         width=dp(3.4), cap="round")
                # phase 3 (>0.85): flash a ring at the target
                if self.phase > 0.85 and int((self.phase - 0.85) / 0.04) % 2 == 0:
                    ecx, ecy = self._kv(*E)
                    Color(1.0, 0.4, 0.4, 1)           # bright red flash ring
                    Line(circle=(ecx, ecy, dp(11)), width=dp(3))
        self._hide_label("medic")
        self._hide_label("card")

    def _draw_fallback(self):
        """Schematic (no art): an SD card slides into the medic."""
        x, y, w, h = self.x, self.y, self.width, self.height
        cy = y + h / 2
        mw, mh = w * 0.44, h * 0.62
        mx, my = x + w - mw, cy - mh / 2
        cw, ch = w * 0.20, h * 0.34
        start_x = x + w * 0.05
        cx = start_x + (mx - cw + dp(12) - start_x) * min(1.0, self.phase / 0.85)
        with self.canvas:
            _draw_medic_vector(mx, my, mw, mh)
            Color(*theme.hex_to_rgba(theme.COLORS["warning_yellow"]))
            RoundedRectangle(pos=(cx, cy - ch / 2), size=(cw, ch), radius=[dp(4)] * 4)
        card = self._label("card", text="SD", font_size="14sp", bold=True,
                          halign="center", valign="middle",
                          color=theme.hex_to_rgba(theme.COLORS["background"]))
        card.size = (cw, ch)
        card.pos = (cx, cy - ch / 2)
