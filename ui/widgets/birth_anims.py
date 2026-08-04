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
from kivy.graphics import (Color, Line, PopMatrix, PushMatrix, Quad, Rectangle,
                           Rotate, RoundedRectangle, StencilPop, StencilPush,
                           StencilUnUse, StencilUse)
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
ANTENNA_PNG = os.path.join(_ANIM_DIR, "antenna_sma.png")           # whip antenna w/ SMA female base
PIGTAIL_PNG = os.path.join(_ANIM_DIR, "pigtail_ipex.png")          # SMA-male <-> U.FL/IPEX pigtail
PI_ZERO_PNG = os.path.join(_ANIM_DIR, "pi_zero_2w.png")            # Pi Zero 2 W, SD slot on its left edge
#: The same board with its white studio background flood-filled away FROM THE
#: EDGES, so silkscreen inside the board survives. Lets the Pi sit on the dark
#: UI instead of in a white box (operator, 2026-08-02).
PI_ZERO_CUT_PNG = os.path.join(_ANIM_DIR, "pi_zero_2w_cut.png")
#: Connector centres along the bottom edge of that sprite, as fractions of its
#: width. MEASURED, not eyeballed — found by scanning the image for each
#: connector's metal shielding, and they agree with the board's own silkscreen
#: (HDMI / USB). The middle one is the DATA port, which is the entire reason
#: this animation exists: a Pi Zero has two identical micro-USB sockets and only
#: the inner one carries data.
PI_ZERO_PORTS = {"hdmi": 0.186, "data": 0.626, "power": 0.819}
#: The operator's own micro-USB cable, cut from a white studio shot. Cropped to
#: the plug end alone, tip UPWARD — which is the direction it enters a socket on
#: the board's lower edge. The connector TYPE matters: a Pi Zero is micro-USB,
#: and an earlier USB-C photo was rejected for this step precisely because it
#: would have shown the wrong plug entering the socket the step points at.
PLUG_MICRO_PNG = os.path.join(_ANIM_DIR, "plug_micro.png")
#: A straight slice of the same cable's braid, taken from a genuinely vertical
#: run of it (rows 423-672 of the source). Tiled down the screen so the cable
#: between the medic and the plug is the REAL cable, not a drawn line.
CABLE_BRAID_PNG = os.path.join(_ANIM_DIR, "cable_braid.png")
#: The medic with its tall antenna cropped off. The full sprite is portrait
#: (aspect 0.52) and this stage is wide and short, so height-capping the whole
#: thing shrank it to a sliver. The body alone is 0.94 and sits properly.
MEDIC_BODY_PNG = os.path.join(_ANIM_DIR, "node_medic_body.png")
PI_ZERO_PORT_Y = 0.93

#: The operator's own drawing of the medic with its micro-USB cable attached
#: (supplied 2026-08-04), white studio background flood-filled from the EDGES so
#: the cream screen face survives. Kept whole for reference; the connect-Pi step
#: uses the three pieces cut from it below.
MEDIC_CABLE_MICRO_PNG = os.path.join(_ANIM_DIR, "node_medic_cable_micro.png")
#: Why that sprite is CUT rather than drawn whole: its plug tip sits only 30% up
#: from the medic's base, so wherever the plug meets the Pi's socket the medic's
#: body and antenna rise above it — and this stage is wide and short, so the two
#: cannot be separated vertically. An offline render showed the medic squarely on
#: top of the Pi. Split, the braid tiles to ANY length, which is what restores
#: the operator's spec: cable permanently attached, complete, only the end moving.
#: The medic's knob + body + horn. No antenna (portrait sprites shrink to a
#: sliver when height-capped here) and no cable.
MEDIC_CASE_MICRO_PNG = os.path.join(_ANIM_DIR, "medic_case_micro.png")
#: The same drawing with ONLY its plug erased — the drawn curve of the cable is
#: kept, because that curve is what lets the Pi sit to the LEFT of the medic
#: rather than stacked above it (operator, 2026-08-04). Deliberately NOT cropped
#: to its bounding box: the anchors below are fractions of THIS canvas, and
#: trimming the now-empty left margin would shift every one of them.
MEDIC_CABLE_BODY_PNG = os.path.join(_ANIM_DIR, "medic_cable_body_micro.png")
#: Where the drawing's cable ends — the tip of the plug that was erased. The
#: moving plug starts here and the braid bridges from here up to the socket.
MEDIC_CABLE_TIP = (0.0361, 0.7010)          # (x, y) fractions, y top-down
#: Top edge of the medic's case. The Pi is parked just above this so the two
#: never overlap: placed level with the cable's end they sit on top of each
#: other, and hiding the Pi behind the medic hides the very socket the step is
#: about.
MEDIC_CASE_TOP = 0.4197
#: The drawn micro-USB plug, tip UPWARD. A real micro-USB moulding is about
#: 2.4:1, and it is drawn at that ratio — sized any wider it swallows the whole
#: gap between the boards.
PLUG_MICRO_DRAWN_PNG = os.path.join(_ANIM_DIR, "plug_micro_drawn.png")
#: A straight slice of the same drawing's braid, from its dead-vertical run.
BRAID_MICRO_DRAWN_PNG = os.path.join(_ANIM_DIR, "braid_micro_drawn.png")
#: The operator's actual card (SanDisk MAX Endurance) — background keyed out so
#: it drops onto the dark UI cleanly. The older square sd_card.png stays for the
#: insert-into-the-MEDIC animation, whose geometry is measured against it.
SD_ENDURANCE_PNG = os.path.join(_ANIM_DIR, "sd_card_endurance.png")

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

    def __init__(self, board_key: str = "", **kwargs):
        """*board_key* renders the REAL board the medic detected instead of the
        generic radio sprite.

        Standing design aim (operator, 2026-08-02): show a picture of the thing
        in their hand. The medic identifies boards from silicon they cannot see
        — chip package, USB vendor, flash size — so the picture is their only
        check on that identification. Falls back to the generic art when the
        board is genuinely unknown; never shows a WRONG board.
        """
        super().__init__(**kwargs)
        self._connected = False
        self._conn_tex = None                         # cached "Connected!" glyph texture
        self._board_png = ""
        if board_key:
            try:
                from ui import board_images
                self._board_png = board_images.image_for(board_key) or ""
            except Exception:
                self._board_png = ""
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
        medic_tex = _texture(MEDIC_CABLE_PNG)
        board_tex = (_texture(self._board_png) if self._board_png else None) \
            or _texture(LORA_PNG)
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
    """Antenna-first: three illustrated sprites — the LoRa32 board, the SMA<->U.FL
    pigtail, and the whip antenna — loop COMING TOGETHER. The pigtail's U.FL/IPEX
    end clicks onto the board's antenna socket while the antenna's SMA base screws
    onto the pigtail's SMA-male end (a small twist that damps out as it seats), then
    both junctions pulse green to say 'connected'. Falls back to the schematic
    vector below when the PNG art is absent, so the step always works.

    All connector anchor points are fractions of their sprite (measured from the
    art, y-DOWN from each sprite's TOP-LEFT). They're APPROXIMATE — expose them
    here so a human can nudge them on-device without touching the motion code.
    """

    #: Antenna SMA female mating face — bottom-centre of antenna_sma.png (the gold hex).
    _ANT_SMA = (0.48, 0.97)
    #: Pigtail SMA-male connector — bottom-LEFT of pigtail_ipex.png (the gold hex nut).
    _PIG_SMA = (0.11, 0.76)
    #: Pigtail U.FL/IPEX plug — top-RIGHT of pigtail_ipex.png (the tiny gold connector).
    _PIG_IPEX = (0.88, 0.12)
    #: Board U.FL antenna socket — the gold ring near the TOP of lora32.png.
    _BOARD_UFL = (0.47, 0.05)

    #: Sprite heights as a fraction of the stage height (aspect kept from the PNG).
    _BOARD_H = 0.72
    _PIG_H = 0.34
    _ANT_H = 0.48
    #: Where the board's U.FL socket sits in the stage (fractions of w, h; y-DOWN).
    #: Everything else is chained off this: pigtail seats its IPEX here, the antenna
    #: seats its SMA onto the pigtail's other end.
    _SOCKET_AT = (0.62, 0.30)
    #: Off-screen entry offsets (fractions of w, h) the parts slide IN from.
    _PIG_ENTER = (-0.30, 0.12)      # pigtail arrives from the left, slightly low
    _ANT_ENTER = (-0.10, -0.34)     # antenna descends from the upper-left
    _ANT_TWIST_DEG = 11.0           # antenna's screw-on rotation, damps to 0 as it seats

    def __init__(self, **kwargs):
        super().__init__(duration=3.6, **kwargs)

    @staticmethod
    def _ease(v):
        v = 0.0 if v < 0.0 else 1.0 if v > 1.0 else v
        return v * v * (3.0 - 2.0 * v)                    # smoothstep

    def _blit_anchor(self, tex, anchor, target_down, sprite_h, rot_deg=0.0):
        """Draw *tex* scaled to *sprite_h* px tall so its *anchor* (fraction, y-DOWN
        from the sprite's top-left) lands on *target_down* (a widget point, y-DOWN
        from the stage's top-left). Optionally rotate by *rot_deg* about the anchor
        (used for the antenna's screw-on twist)."""
        W = sprite_h * (tex.width / float(tex.height))
        H = sprite_h
        tlx = target_down[0] - anchor[0] * W
        tly = target_down[1] - anchor[1] * H
        kx = self.x + tlx
        ky = self.y + self.height - (tly + H)             # y-DOWN top-left -> kivy bottom-left
        if rot_deg:
            piv = (self.x + target_down[0], self.y + self.height - target_down[1])
            PushMatrix()
            Rotate(angle=rot_deg, origin=piv, axis=(0, 0, 1))
        Color(1, 1, 1, 1)
        Rectangle(texture=tex, pos=(kx, ky), size=(W, H))
        if rot_deg:
            PopMatrix()

    def _draw(self):
        board = _texture(LORA_PNG)
        pig = _texture(PIGTAIL_PNG)
        ant = _texture(ANTENNA_PNG)
        if board is None or pig is None or ant is None:
            return self._draw_fallback()
        w, h = self.width, self.height
        p = self.phase
        pig_t = self._ease(p / 0.55)                       # pigtail seats first
        ant_t = self._ease((p - 0.35) / 0.50)              # antenna follows, overlapping
        seat = self._ease((p - 0.82) / 0.18)               # 0->1 near the end
        pulse = math.sin(math.pi * seat)                   # 0..1..0 settle glow

        # seated (final) anchor points in y-DOWN widget px
        socket = (self._SOCKET_AT[0] * w, self._SOCKET_AT[1] * h)
        board_h = self._BOARD_H * h
        pig_h = self._PIG_H * h
        ant_h = self._ANT_H * h
        pig_w = pig_h * (pig.width / float(pig.height))
        # pigtail's SMA end relative to its IPEX end (in seated px)
        sma_dx = (self._PIG_SMA[0] - self._PIG_IPEX[0]) * pig_w
        sma_dy = (self._PIG_SMA[1] - self._PIG_IPEX[1]) * pig_h
        pig_sma_seated = (socket[0] + sma_dx, socket[1] + sma_dy)

        # live entry offsets (parts slide from off-stage toward seated)
        pig_off = ((1.0 - pig_t) * self._PIG_ENTER[0] * w,
                   (1.0 - pig_t) * self._PIG_ENTER[1] * h)
        ant_off = ((1.0 - ant_t) * self._ANT_ENTER[0] * w,
                   (1.0 - ant_t) * self._ANT_ENTER[1] * h)
        pig_ipex_now = (socket[0] + pig_off[0], socket[1] + pig_off[1])
        # antenna chases the pigtail's CURRENT SMA end so they stay mated as it seats
        pig_sma_now = (pig_sma_seated[0] + pig_off[0], pig_sma_seated[1] + pig_off[1])
        ant_target = (pig_sma_now[0] + ant_off[0], pig_sma_now[1] + ant_off[1])
        ant_rot = (1.0 - ant_t) * self._ANT_TWIST_DEG

        with self.canvas:
            # board (back) — static, socket pinned at _SOCKET_AT
            self._blit_anchor(board, self._BOARD_UFL, socket, board_h)
            # pigtail — IPEX slides onto the board socket
            self._blit_anchor(pig, self._PIG_IPEX, pig_ipex_now, pig_h)
            # antenna (front) — SMA base screws onto the pigtail's SMA end
            self._blit_anchor(ant, self._ANT_SMA, ant_target, ant_h, rot_deg=ant_rot)
            # settle glow: green rings pulse at BOTH junctions once seated
            if pulse > 0.01:
                g = theme.hex_to_rgba(theme.COLORS["green"])
                for jx_down, jy_down in (socket, pig_sma_seated):
                    kx = self.x + jx_down
                    ky = self.y + self.height - jy_down
                    Color(g[0], g[1], g[2], 0.85 * pulse)
                    Line(circle=(kx, ky, dp(6) + pulse * dp(16)), width=dp(2.4))
        # art is self-explanatory — hide the fallback labels
        self._hide_label("ufl")
        self._hide_label("sma")

    def _draw_fallback(self):
        """Schematic (no art): a U.FL plug clicks onto the board socket (phase 1),
        then an SMA hex nut screws onto the pigtail's threaded jack (phase 2, with a
        rotating nut + curved screw arrow). Labelled so a first-timer can tell which
        connector their board/antenna has."""
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


class InsertSdIntoPiAnim(_LoopAnim):
    """The imaged microSD slides into the Pi Zero 2 W's own slot.

    The mirror of InsertSdAnim: that one puts the card INTO THE MEDIC to be
    written; this one shows the finished card going HOME into the Pi. The slot
    is on the board's left edge, so the card approaches from the left and
    disappears into it (clipped at the slot mouth so it visibly goes *in*).
    Falls back to the schematic if the artwork is missing.
    """

    #: The slot mouth on pi_zero_2w.png, as a fraction of the sprite (y-DOWN):
    #: the left edge of the metal microSD cage, and its vertical span.
    _SLOT_X = 0.085
    _SLOT_TOP = 0.245
    _SLOT_BOT = 0.545
    #: Fine alignment of the card across the slot, as a fraction of the board's
    #: on-screen height. POSITIVE moves the card DOWN. The slot bounds above are
    #: measured off the sprite; this absorbs the small residual error between the
    #: measured cage and where the card sprite's own edge falls, which showed as
    #: the card's top edge sitting a hair proud of the cage (operator, on the
    #: live screen, 2026-08-02). Nudge in ~0.005 steps and look at it.
    _CARD_NUDGE = 0.013

    def __init__(self, pi_key: str = "", **kwargs):
        """*pi_key* renders THAT Raspberry Pi model instead of the stock Zero.

        Same contract as ConnectPiAnim: the operator is holding the board and
        checking the screen against it, so it must be the right one.
        """
        kwargs.setdefault("duration", 3.0)
        super().__init__(**kwargs)
        self._pi_png = ""
        if pi_key:
            try:
                from ui import board_images
                self._pi_png = board_images.image_for_pi(pi_key) or ""
            except Exception:
                self._pi_png = ""

    @staticmethod
    def _ease(v):
        v = 0.0 if v < 0.0 else 1.0 if v > 1.0 else v
        return v * v * (3.0 - 2.0 * v)                    # smoothstep

    def _draw(self):
        # NOTE: everything below must be inside `with self.canvas`. Without it
        # the instructions are constructed and then thrown away — the widget
        # renders nothing at all, which is exactly how this step reached the
        # operator: a correct animation on a blank screen (2026-08-02).
        pi_tex = (_texture(self._pi_png) if self._pi_png else None) \
            or _texture(PI_ZERO_PNG)
        card = _texture(SD_ENDURANCE_PNG) or _texture(SD_PNG)
        if pi_tex is None or card is None:
            return self._draw_fallback()
        w, h = self.width, self.height
        # the board sits centred-right, leaving room on the left for the card
        pa = pi_tex.width / float(pi_tex.height)
        pw = w * 0.68
        ph = pw / pa
        if ph > h * 0.7:
            ph = h * 0.7
            pw = ph * pa
        px = self.x + w - pw - dp(8)
        py = self.y + (h - ph) / 2.0
        slot_x = px + pw * self._SLOT_X
        slot_cy = py + ph * (1.0 - (self._SLOT_TOP + self._SLOT_BOT) / 2.0
                             - self._CARD_NUDGE)
        ch = ph * (self._SLOT_BOT - self._SLOT_TOP) * 0.92
        cw = ch * (card.width / float(card.height))
        travel = self._ease(min(1.0, self.phase * 1.35))  # arrive, then dwell
        start_x = self.x + dp(4)
        cx = start_x + (slot_x - cw * 0.55 - start_x) * travel
        from kivy.graphics import StencilPush, StencilUse, StencilUnUse, StencilPop
        with self.canvas:
            Color(1, 1, 1, 1)
            Rectangle(texture=pi_tex, pos=(px, py), size=(pw, ph))
            # clip the card at the slot mouth so it vanishes INTO the board
            StencilPush()
            Rectangle(pos=(self.x, self.y), size=(slot_x - self.x, h))
            StencilUse()
            Color(1, 1, 1, 1)
            Rectangle(texture=card, pos=(cx, slot_cy - ch / 2.0), size=(cw, ch))
            StencilUnUse()
            Rectangle(pos=(self.x, self.y), size=(slot_x - self.x, h))
            StencilPop()

    def _draw_fallback(self):
        """No artwork — a plain board outline with the card entering its edge."""
        w, h = self.width, self.height
        bx, by = self.x + w * 0.30, self.y + h * 0.30
        bw, bh = w * 0.62, h * 0.40
        travel = self._ease(min(1.0, self.phase * 1.35))
        cw, ch = w * 0.12, h * 0.16
        cx = self.x + w * 0.05 + (bx - cw * 0.5 - (self.x + w * 0.05)) * travel
        with self.canvas:
            Color(*theme.hex_to_rgba(theme.COLORS["surface"]))
            Rectangle(pos=(bx, by), size=(bw, bh))
            Color(*theme.hex_to_rgba(theme.COLORS["accent"]))
            Rectangle(pos=(cx, by + bh / 2 - ch / 2), size=(cw, ch))


class ConnectPiAnim(ConnectBoardAnim):
    """A cable slides into the Pi's DATA port. Both boards stay still.

    Rebuilt on the bench (operator, 2026-08-02): "have the Node Medic static and
    the Raspberry Pi static, and we'll just have the USB cable moving into the
    data port on the Pi."

    The old version slid the whole board along a line toward the medic. That
    showed A connection being made but never showed WHICH SOCKET — and the
    socket is the only thing this step is actually about. A Pi Zero has two
    identical micro-USB shells side by side: the inner one carries data, the
    outer one is PWR IN and cannot. Plug into the wrong one and nothing happens,
    with no error to explain why. So nothing moves except the plug, the target
    is ringed and named, and its identical twin is named as the one to avoid.
    """

    #: Green. The radio goes to the medic on a red cable, and these two
    #: connections must not read as the same action (operator, 2026-08-02).
    _CABLE = (0.30, 0.85, 0.36, 1)
    _PWR = (0.96, 0.52, 0.22, 1)
    #: The forbidden port. Red with a bar through it, not amber — this socket
    #: does not merely differ, it must not be used (operator, 2026-08-04).
    _NO = (0.90, 0.20, 0.20, 1)

    def __init__(self, pi_key: str = "", **kwargs):
        """*pi_key* draws the detected Pi model; falls back to the Zero sprite."""
        super().__init__(**kwargs)
        self._pi_png = ""
        if pi_key:
            try:
                from ui import board_images
                self._pi_png = board_images.image_for_pi(pi_key) or ""
            except Exception:
                self._pi_png = ""

    @staticmethod
    def _ease(v):
        v = 0.0 if v < 0.0 else 1.0 if v > 1.0 else v
        return v * v * (3.0 - 2.0 * v)

    def _draw(self):
        """Medic on the right, Pi to its LEFT, cable rising into the port.

        Operator, 2026-08-04: "don't put the pi on top, keep it to the left."
        Which is what their own drawing shows — the cable leaves the medic's
        bottom, curves left, and the plug points up at the far left. So the
        drawn curve is kept and the Pi hangs off its end, rather than the two
        being stacked.

        The Pi is parked just ABOVE the medic's case. Placed level with the
        cable's own end the two sprites sit squarely on top of each other, and
        the obvious alternative — tucking the Pi behind the medic — hides the
        socket, which is the one thing this step exists to point at.

        Operator's earlier correction (2026-08-02) still holds: "the USB cable
        permanently attached to the bottom of the Node Medic, and just the plug
        moving straight up into the port on the Pi Zero 2 W... the cable needs
        to be complete, and only the end of it moving directly upwards."

        So the medic sits DIRECTLY BELOW the socket, the run between them is the
        operator's own cable braid tiled vertically, and the plug starts already
        lined up with the hole it enters. Nothing travels diagonally and nothing
        is a drawn line — the previous version did both and read as a green
        stick crossing the screen.

        Geometry was tuned against an offline render of these same sprites (the
        medic was powered down), so the numbers here are the ones that were
        actually looked at rather than guessed.
        """
        pi = (_texture(self._pi_png) if self._pi_png else None) \
            or _texture(PI_ZERO_CUT_PNG) or _texture(PI_ZERO_PNG)
        if pi is None:
            return self._draw_fallback()
        # The operator's drawn art (2026-08-04) first; the earlier photo cut-outs
        # stay as fallback so a missing file degrades instead of blanking.
        medic = (_texture(MEDIC_CABLE_BODY_PNG) or _texture(MEDIC_BODY_PNG)
                 or _texture(MEDIC_PNG))
        plug = _texture(PLUG_MICRO_DRAWN_PNG) or _texture(PLUG_MICRO_PNG)
        braid = _texture(BRAID_MICRO_DRAWN_PNG) or _texture(CABLE_BRAID_PNG)
        x, y, w, h = self.x, self.y, self.width, self.height

        # --- the medic, still, standing on the floor at the RIGHT -----------
        # Operator, 2026-08-04: "don't put the pi on top, keep it to the left."
        # That is the arrangement the drawing itself depicts — the cable leaves
        # the medic's bottom, curves LEFT, and the plug points up at the far
        # left — so the drawn curve is kept and the Pi hangs off its end.
        mh = h * 0.80
        mw = mh * (medic.width / float(medic.height)) if medic else w * 0.2
        myy = y + dp(2)

        # --- the Pi, still, to the LEFT and just clear of the case ----------
        # A Pi Zero is ~0.46 tall for its width, so the height cap governs and
        # the width fraction never binds on this stage.
        pw = w * 0.40
        ph = pw * (pi.height / float(pi.width))
        if ph > h * 0.30:
            ph = h * 0.30
            pw = ph * (pi.width / float(pi.height))

        # Centre the PAIR, not each sprite: the medic is portrait, so anchoring
        # it to one edge strands the other half of a very wide stage.
        left_of_tip = pw * PI_ZERO_PORTS["data"]
        tip_dx = mw * MEDIC_CABLE_TIP[0]
        group = left_of_tip + (mw - tip_dx)
        data_x = x + (w - group) / 2.0 + left_of_tip
        mxx = data_x - tip_dx

        # Kivy y is bottom-up; the sprite fractions are top-down, hence 1.0 - f.
        cable_end = myy + mh * (1.0 - MEDIC_CABLE_TIP[1])
        case_top = myy + mh * (1.0 - MEDIC_CASE_TOP)
        # Parked just ABOVE the case, so the boards never overlap. Level with
        # the cable's own end they sit squarely on top of each other.
        port_y = max(case_top + dp(6), cable_end + dp(8))

        pxx = data_x - left_of_tip
        pyy = port_y - ph * (1.0 - PI_ZERO_PORT_Y)
        pwr_x = pxx + pw * PI_ZERO_PORTS["power"]

        # --- the plug travels up and INSERTS ---------------------------------
        # It must start clear of the board, approach, and go in (operator,
        # 2026-08-04). A short nudge read as a plug that was already in the
        # socket and merely twitching, so the travel is a real gap: the plug
        # begins a whole connector-length below the port and closes it.
        t = 1.0 if self._connected else self._ease(min(1.0, self.phase * 1.15))
        # A real micro-USB moulding is ~2.4:1, and the drawn one keeps that
        # ratio — at dp(26) it grew tall enough to swallow the whole gap
        # between the two boards, leaving no cable visible.
        plug_w = dp(21)
        plug_h = plug_w * (plug.height / float(plug.width)) if plug else dp(56)
        travel = plug_h * 0.85 + dp(10)
        # The metal tongue is the top ~28% of the moulding, so seating it means
        # sinking that much INTO the shell — at dp(3) the plug just kissed the
        # edge and never looked inserted.
        seated = port_y + plug_h * 0.28
        top = seated - travel * (1.0 - t)
        plug_bottom = top - plug_h

        with self.canvas:
            # Medic first, then the cable, then the Pi, then the plug on top.
            # The Pi goes in FRONT of the medic's thin antenna rather than
            # behind it: put the medic last and it covers the socket, which is
            # the one thing this step exists to point at.
            if medic is not None:
                Color(1, 1, 1, 1)
                Rectangle(texture=medic, pos=(mxx, myy), size=(mw, mh))

            # Bridge the DRAWING's cable end up to wherever the socket sits.
            # The drawn curve is untouched; braid is only added above it, cut
            # from the dead-vertical run of the very same cable, so the join is
            # the same rope rather than a drawn line.
            if braid is not None:
                bw = plug_w * 0.42
                bh = bw * (braid.height / float(braid.width))
                yy = cable_end - dp(4)
                Color(1, 1, 1, 1)
                while yy < plug_bottom + dp(2):
                    Rectangle(texture=braid, pos=(data_x - bw / 2.0, yy),
                              size=(bw, bh))
                    yy += bh * 0.92                 # overlap, so no seams

            Color(1, 1, 1, 1)
            Rectangle(texture=pi, pos=(pxx, pyy), size=(pw, ph))
            if plug is not None:
                Color(1, 1, 1, 1)
                Rectangle(texture=plug, pos=(data_x - plug_w / 2.0, plug_bottom),
                          size=(plug_w, plug_h))

            # the socket it is aiming at, breathing until it seats
            if t < 1.0:
                pulse = 0.5 + 0.5 * math.sin(self.phase * 4 * math.pi)
                Color(self._CABLE[0], self._CABLE[1], self._CABLE[2],
                      0.30 + 0.45 * pulse)
                Line(circle=(data_x, port_y, dp(14)), width=dp(2.0))
            # And its identical twin, which carries power only. Drawn as a NO
            # ENTRY sign — ring plus a bar through it — not just a differently
            # coloured ring (operator, 2026-08-04). Two rings side by side say
            # "here are two ports"; a struck-through one says "not this one",
            # which is the entire point of the step.
            Color(*self._NO)
            Line(circle=(pwr_x, port_y, dp(10)), width=dp(2.0))
            bar = dp(10) * 0.707                    # 45°, ends on the ring
            Line(points=[pwr_x - bar, port_y - bar, pwr_x + bar, port_y + bar],
                 width=dp(2.0), cap="none")

        d = self._label("data", text="DATA", font_size="12sp", bold=True,
                        color=self._CABLE, halign="center")
        d.size = (dp(64), dp(18))
        d.pos = (data_x - dp(32), port_y + dp(12))
        p = self._label("pwr", text="PWR IN", font_size="11sp", color=self._PWR,
                        halign="center")
        p.size = (dp(64), dp(16))
        p.pos = (pwr_x - dp(32), port_y + dp(12))

    def _draw_fallback(self):
        w, h = self.width, self.height
        t = self._ease(min(1.0, self.phase * 1.3))
        with self.canvas:
            Color(*theme.hex_to_rgba(theme.COLORS["surface"]))
            RoundedRectangle(pos=(self.x + w * 0.5, self.y + h * 0.36),
                             size=(w * 0.44, h * 0.3), radius=[dp(6)] * 4)
            Color(*self._CABLE)
            Line(points=[self.x + w * 0.08, self.y + h * 0.5,
                         self.x + w * (0.08 + 0.44 * t), self.y + h * 0.5],
                 width=dp(3))
