"""Animations for the guided birth wizard.

Each animation shows the physical action (plug a board in, insert an SD card) with
a looping motion. If a cartoon PNG is present in ``assets/ui/anim/`` it's used;
otherwise a schematic vector fallback draws in its place — so the flow works now
and simply gets prettier when the artwork is dropped in (no code change):

    assets/ui/anim/node_medic.png    # the Node Medic body
    assets/ui/anim/sd_card.png       # the SD card
    assets/ui/anim/radio_board.png   # the radio board

Final artwork replaces the placeholders by filename.
"""

from __future__ import annotations

import math
import os

from kivy.animation import Animation
from kivy.graphics import (Color, Ellipse, Line, PopMatrix, PushMatrix, Quad,
                           Rectangle, Rotate, RoundedRectangle, StencilPop,
                           StencilPush, StencilUnUse, StencilUse)
from kivy.metrics import dp
from kivy.properties import NumericProperty
from kivy.uix.label import Label
from kivy.uix.widget import Widget

from ui import pi_sd_geometry as sdgeo
from ui import theme

_ANIM_DIR = os.path.normpath(os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    os.pardir, "assets", "ui", "anim"))
MEDIC_PNG = os.path.join(_ANIM_DIR, "node_medic.png")             # angled medic (SD step)
MEDIC_CABLE_PNG = os.path.join(_ANIM_DIR, "node_medic_cable.png")  # medic w/ USB cable
LORA_PNG = os.path.join(_ANIM_DIR, "lora32.png")                   # the radio board
SD_READER_PNG = os.path.join(_ANIM_DIR, "sd_reader.png")           # microSD + card reader (combined)
SD_READER_BODY_PNG = os.path.join(_ANIM_DIR, "sd_reader_body.png")  # card reader alone

#: The card reader is drawn in a warm beige with a gold USB-A tab standing
#: proud of a blocky body. At full size it is obviously a dongle; at the size
#: the handover step draws it, the operator read the silhouette as a raised
#: middle finger (bench, 2026-08-09) — and once seen it cannot be unseen on a
#: screen that is meant to be teaching. Tinted cool so it reads as metal rather
#: than skin. A multiply, not a redraw: the sprite is the operator's own art and
#: the same object has to look the same on both screens that show it.
READER_TINT = (0.70, 0.79, 0.92, 1)
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
#: Why the sprite is CUT rather than drawn whole: its plug sits only 30% up from
#: the medic's base, so wherever the plug meets the Pi's socket the medic's body
#: rises above it — and this stage is wide and short, so the two cannot be
#: separated vertically. An offline render showed the medic squarely on top of
#: the Pi. Cut, the braid tiles to ANY length, which is what lets the boards be
#: placed freely while the cable still runs between them.
#: The drawing with its blue cable AND plug removed entirely — case, antenna and
#: the power lead that curls off to the right.
#:
#: Why the drawn cable had to go (operator, 2026-08-04: "keep the cable
#: uniform"): the tiled braid is sized off the PLUG so the plug stays readable,
#: while the drawn cable scales with the MEDIC. At the sizes this stage allows
#: those are about 9px and 3px, so the cable visibly stepped in width where the
#: two met — and the join moved as the plug travelled, which is what made it
#: look like the cable was changing size. Matching the tiled braid to the drawn
#: one instead would mean a 3px cable and a 7px plug: uniform, and far too small
#: to read. So the whole run is now ONE tiled braid at one width.
MEDIC_NOCABLE_PNG = os.path.join(_ANIM_DIR, "medic_nocable_micro.png")
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


def _bezier(p0, p1, p2, p3, n):
    """*n*+1 points along a cubic Bezier. Used to sweep a cable along a curve
    instead of a right angle — see ConnectPiAnim."""
    pts = []
    for i in range(n + 1):
        s = i / float(n)
        m = 1.0 - s
        pts.append((m ** 3 * p0[0] + 3 * m * m * s * p1[0]
                    + 3 * m * s * s * p2[0] + s ** 3 * p3[0],
                    m ** 3 * p0[1] + 3 * m * m * s * p1[1]
                    + 3 * m * s * s * p2[1] + s ** 3 * p3[1]))
    return pts


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


def _blit_card(tex, frame, card_len, card_w, alpha=1.0):
    """Draw the card for *frame*: right way round, right way up, right side out.

    Three things happen here that keep one piece of drawing code correct for
    every board:

    ROTATION. The card sprite's leading (contact) edge points +x, so a slot on
    another edge is the same blit at another angle rather than a second code
    path.

    FORESHORTENING. ``face_scale`` squeezes the short axis to nothing and back
    as the card turns over, which is what a flip looks like seen head-on.

    THE OTHER SIDE. Past halfway the CONTACT face is toward the viewer, and the
    artwork is the label face — so the reverse is drawn instead, from the shapes
    in ui.pi_sd_geometry. Mirroring the sprite was the alternative and it is not
    honest: a back-to-front SanDisk logo is a rendering artefact, not the other
    side of a card.
    """
    w = card_len
    h = max(1.0, card_w * frame.face_scale)
    if frame.angle:
        PushMatrix()
        Rotate(angle=frame.angle, origin=(frame.cx, frame.cy), axis=(0, 0, 1))
    if frame.showing_back:
        body, pads = sdgeo.card_back_shapes(w, h)
        Color(sdgeo.CARD_BACK_RGB[0], sdgeo.CARD_BACK_RGB[1],
              sdgeo.CARD_BACK_RGB[2], alpha)
        RoundedRectangle(pos=(frame.cx + body[0], frame.cy + body[1]),
                         size=(body[2], body[3]),
                         radius=[min(dp(3), h * 0.22)] * 4)
        Color(sdgeo.CARD_PAD_RGB[0], sdgeo.CARD_PAD_RGB[1],
              sdgeo.CARD_PAD_RGB[2], alpha)
        for px, py, pw, ph in pads:
            Rectangle(pos=(frame.cx + px, frame.cy + py), size=(pw, ph))
    else:
        Color(1, 1, 1, alpha)
        Rectangle(texture=tex, pos=(frame.cx - w / 2.0, frame.cy - h / 2.0),
                  size=(w, h))
    if frame.angle:
        PopMatrix()


def _card_texture():
    """The operator's own SanDisk MAX Endurance illustration — the card they
    are actually holding. The square placeholder stays as a fallback only."""
    return _texture(SD_ENDURANCE_PNG) or _texture(SD_PNG)


class _CardStage(_LoopAnim):
    """Shared plumbing for the two steps that put a card into a Raspberry Pi.

    Both need the same three things: the RIGHT Pi's picture, that Pi's slot
    geometry, and a way to draw a card sliding into it and disappearing at the
    mouth. Neither may improvise when the model is unknown — see ``_pi_art``.
    """

    def __init__(self, pi_key: str = "", **kwargs):
        super().__init__(**kwargs)
        self._pi_key = (pi_key or "").strip()
        self._geo = sdgeo.geometry_for(self._pi_key)
        # The picture comes from the GEOMETRY, not from board_images: the slot
        # fractions are measured inside one particular crop, so the two travel
        # together (see sdgeo.sprite_path).
        self._pi_png = sdgeo.sprite_path(self._geo) if self._geo else None

    def _pi_art(self):
        """``(texture, geometry)``, or ``(None, None)`` to draw the generic board.

        Deliberately all-or-nothing. A photo without geometry would send the
        card into a made-up edge; geometry without the photo has nothing to
        enter. And when the model is unknown, this shows a plain outline rather
        than the Pi Zero sprite the old code reached for — a Zero drawn while a
        3A+ is in the operator's hand is the exact fault the standing rule was
        restated for (2026-08-06). The picture is their only check on an
        identification made from silicon they cannot see, so an honest blank
        beats a confident wrong answer.
        """
        if not self._pi_png or self._geo is None:
            return (None, None)
        tex = _texture(self._pi_png)
        return (tex, self._geo) if tex is not None else (None, None)

    def _stage_bounds(self):
        return (self.x, self.y, self.width, self.height)

    def _draw_card(self, card, frame, card_len, card_w, behind_ok=True):
        """Blit the card for *frame*, clipped at the slot mouth when it has one.

        Two ways a card vanishes into a board, and which one is correct is a
        property of the BOARD: a top-mounted holder (the Pi Zero's) swallows it
        at the mouth line, so the card is clipped; an underside holder means it
        passes BEHIND the board and the board itself hides it. Callers draw the
        board between the two cases — see the ordering in each _draw.
        """
        if frame.clip_point is None or (frame.behind_board and behind_ok):
            _blit_card(card, frame, card_len, card_w)
            return
        mask = sdgeo.half_plane(frame.clip_point, frame.clip_normal,
                                self._stage_bounds())
        StencilPush()
        Quad(points=mask)
        StencilUse()
        _blit_card(card, frame, card_len, card_w)
        StencilUnUse()
        Quad(points=mask)
        StencilPop()

    def _draw_card_ghost(self, card, frame, card_len, card_w):
        """The part of the card that is UNDER the board, shown faintly through it.

        Only for underside slots, and it earns its keep: drawn honestly, the
        last two thirds of that action are invisible — the card slides beneath
        the board and the operator watches nothing happen (offline render,
        pi_4b). A dimmed card showing through is the cutaway convention every
        assembly diagram uses; it adds no hardware that isn't there and it is
        the only way this step can say WHERE under the board the card goes.
        """
        if frame.clip_point is None:
            return
        # the complement of the visible half-plane: everything past the mouth
        mask = sdgeo.half_plane(frame.clip_point,
                               (-frame.clip_normal[0], -frame.clip_normal[1]),
                               self._stage_bounds())
        StencilPush()
        Quad(points=mask)
        StencilUse()
        _blit_card(card, frame, card_len, card_w, alpha=0.30)
        StencilUnUse()
        Quad(points=mask)
        StencilPop()


class ConnectBoardAnim(_LoopAnim):
    """The LoRa32 radio board descends from above onto the Node Medic's USB plug.
    Uses the illustrated sprites (medic-with-cable on the right, board small on the
    left, docking on the plug tip); falls back to a schematic if the art is absent."""

    burst = NumericProperty(0.0)
    rise = NumericProperty(0.0)                       # "Connected!" banner slide-up

    #: Run the docking motion backwards (board lifts off the plug). See _draw.
    REVERSED = False

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
        # PAST 1.0, or it does not finish. Each ring's alpha is (1 - f) * 0.9
        # with f = burst - i*0.16, so at 1.0 only the FIRST ring has faded and
        # the other three stop mid-flight and sit there forever. The card burst
        # had the identical bug and the operator read it as a hang; they read
        # this one the same way ("this animation stopped and looks exactly like
        # this screen grab", 2026-08-09). The last ring needs 1 + 3*0.16 = 1.48.
        Animation(burst=1.6, duration=1.4, t="out_quad").start(self)
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
        if self.REVERSED:
            # Same sprites, same junction, motion RUN BACKWARDS: the board sits
            # docked and lifts away. Reversing beats drawing a new scene because
            # the operator has already learnt this picture on the way in — they
            # are being asked to undo the exact thing they just did.
            p = 1.0 - p
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



class DisconnectBoardAnim(ConnectBoardAnim):
    """The radio LIFTS OFF the medic's plug — the connect scene, run backwards.

    Steps 3 and 7 of the Pi path used to reuse ConnectBoardAnim, which draws a
    board descending ONTO Node Medic. On "take the radio out of Node Medic" that
    is not merely unhelpful, it depicts the operator doing the opposite of what
    the words ask — and the standing rule is that every picture must match the
    physical act, because a wrong picture reads as authoritative while wrong
    words merely read as wrong.

    Reversing the existing scene rather than drawing a new one is deliberate:
    the operator learnt this exact picture on the way in, and is now being asked
    to undo that exact thing. Same sprites, same junction, motion backwards.

    No "Connected!" banner and no green ripples: those mean "the medic can see
    it now", and this step is the moment it stops being able to.
    """

    REVERSED = True

    def mark_connected(self):                     # noqa: D102 - deliberately inert
        return

    def mark_removed(self):
        """The medic has SEEN the board go. Freeze the scene fully separated, so
        the last thing on screen agrees with the fact — a loop that carried on
        would swing the board back onto the plug while the flow moved forward,
        which is the same "picture contradicts the words" fault this class
        exists to fix."""
        if getattr(self, "_removed", False):
            return
        self._removed = True
        self.stop()                                   # halt the lift-off loop
        self.phase = 1.0                              # held clear of the plug


class RadioToPiAnim(ConnectBoardAnim):
    """The radio meets the RASPBERRY PI — the last step, and the one that makes
    a node out of two halves.

    DELIBERATELY DOES NOT POINT AT A SOCKET. ConnectPiAnim's own note explains
    why: its port markers are fractions measured on the Pi Zero sprite, so
    handing it another model moves the board picture while leaving the rings
    pointing at nothing. A 3A+ has one USB-A socket, a 4B has four, and claiming
    a specific one without having measured that board is the same class of
    mistake as showing the wrong board entirely.

    So this shows the two objects coming together and stops there. It is honest
    about what is known: THIS radio, THIS Pi, joined. Which socket is a per-board
    fact that has to be measured before it can be drawn — see ui.pi_sd_geometry
    for how the SD slots were done, and do the same before adding a marker here.
    """

    def __init__(self, board_key: str = "", pi_key: str = "", **kwargs):
        super().__init__(board_key=board_key, **kwargs)
        self._pi_png = ""
        if pi_key:
            try:
                from ui import board_images
                self._pi_png = board_images.image_for_pi(pi_key) or ""
            except Exception:
                self._pi_png = ""

    def _draw(self):
        """Its OWN scene, not the parent's. Inheriting ConnectBoardAnim's layout
        would inherit _PLUG_TIP, which is a fraction measured on the MEDIC
        sprite — landing the radio on a point of a Pi that means nothing.
        """
        pi_tex = _texture(self._pi_png) if self._pi_png else None
        board_tex = (_texture(self._board_png) if self._board_png else None) \
            or _texture(LORA_PNG)
        if pi_tex is None or board_tex is None:
            # An unknown Pi falls back to the schematic rather than borrowing the
            # medic art: drawing Node Medic here would say "plug it back into the
            # medic", which is the opposite of this step.
            return self._draw_fallback()
        x, y, w, h = self.x, self.y, self.width, self.height
        # Pi on the right, radio approaching from the left. Left-to-right because
        # every other step in this flow moves that way, and the operator reads
        # the row as a sequence.
        pa = pi_tex.width / float(pi_tex.height)
        ph = h * 0.78
        pw = ph * pa
        if pw > w * 0.52:
            pw = w * 0.52
            ph = pw / pa
        px = x + w - pw - dp(10)
        py = y + (h - ph) / 2.0

        ba = board_tex.width / float(board_tex.height)
        bh = ph * 0.46
        bw = bh * ba
        p = min(1.0, self.phase / 0.9)
        far_x = x + dp(6)
        near_x = px - bw * 0.72           # overlapping, not touching a named port
        bx = far_x + (near_x - far_x) * p
        by = y + (h - bh) / 2.0
        with self.canvas:
            Color(1, 1, 1, 1)
            Rectangle(texture=pi_tex, pos=(px, py), size=(pw, ph))
            Color(1, 1, 1, 1)
            Rectangle(texture=board_tex, pos=(bx, by), size=(bw, bh))

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
    stand-in for final artwork; the geometry + intent are what's set."""

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


class ProvisionOverCableAnim(_LoopAnim):
    """The Pi being worked on THROUGH THE CABLE — boluses down a tube.

    The step it replaces used ProvisionAnim, which draws a radio board sending
    radio waves to Node Medic. Nothing about this step is radio: it is a
    Raspberry Pi on the end of a USB cable, having its software installed over
    that cable. The operator caught it on the screen (2026-08-09): "the
    animation depicts a radio board talking via radio signals to the node medic;
    in fact it's a Raspberry Pi talking to the medic over cable."

    Their picture for it, and it is a good one: "the cable's like a python
    swallowing a tennis ball — there'll be balls going down the tube travelling
    towards the Node Medic from the Pi." So the cable is drawn as a TUBE whose
    thickness swells where a payload is passing, and the swellings travel. It
    reads as substance moving through a physical thing, which is exactly what is
    happening and exactly what radio waves fail to say.

    The board is the operator's OWN Pi, per the standing rule that every picture
    is the hardware in their hand.
    """

    #: Which way the boluses travel. The operator asked for Pi -> Node Medic.
    #: The session is two-way — the medic pushes packages and the Pi answers —
    #: so neither direction is false; this is the one they specified.
    TOWARD_MEDIC = True
    #: How many are in flight at once, evenly spaced around the loop.
    BOLUSES = 3

    def __init__(self, pi_key: str = "", **kwargs):
        kwargs.setdefault("duration", 2.6)
        super().__init__(**kwargs)
        self._pi_png = ""
        if pi_key:
            try:
                from ui import board_images
                self._pi_png = board_images.image_for_pi(pi_key) or ""
            except Exception:                                      # noqa: BLE001
                self._pi_png = ""

    def _draw(self):
        pi = (_texture(self._pi_png) if self._pi_png else None) \
            or _texture(PI_ZERO_CUT_PNG) or _texture(PI_ZERO_PNG)
        medic = (_texture(MEDIC_NOCABLE_PNG) or _texture(MEDIC_BODY_PNG)
                 or _texture(MEDIC_PNG))
        if pi is None or medic is None:
            return self._draw_fallback()
        x, y, w, h = self.x, self.y, self.width, self.height

        mh = h * 0.74
        mw = mh * (medic.width / float(medic.height))
        ph = h * 0.46
        pw = ph * (pi.width / float(pi.height))
        gap = max(dp(70), w * 0.18)
        total = pw + gap + mw
        pxx = x + (w - total) / 2.0
        pyy = y + (h - ph) / 2.0
        mxx = pxx + pw + gap
        myy = y + (h - mh) / 2.0

        # The tube runs from the Pi's edge to the medic's, with a gentle sag so
        # it reads as a cable lying there rather than a wire diagram.
        a = (pxx + pw - dp(2), pyy + ph * 0.42)
        b = (mxx + dp(2), myy + mh * 0.42)
        sag = min(h * 0.10, dp(26))
        pts = _bezier(a, (a[0] + gap * 0.35, a[1] - sag),
                      (b[0] - gap * 0.35, b[1] - sag), b, 56)

        base_r = dp(4.2)
        swell = dp(7.0)
        spread = 0.085                       # how much of the run each bolus fills
        cable = theme.hex_to_rgba(theme.COLORS["accent"])
        with self.canvas:
            Color(1, 1, 1, 1)
            Rectangle(texture=pi, pos=(pxx, pyy), size=(pw, ph))
            Rectangle(texture=medic, pos=(mxx, myy), size=(mw, mh))
            Color(cable[0], cable[1], cable[2], 0.95)
            n = len(pts)
            for i, (cx, cy) in enumerate(pts):
                u = i / float(n - 1)                 # 0 at the Pi, 1 at the medic
                r = base_r
                for k in range(self.BOLUSES):
                    c = (self.phase + k / float(self.BOLUSES)) % 1.0
                    if not self.TOWARD_MEDIC:
                        c = 1.0 - c
                    d = abs(u - c)
                    if d < spread:
                        # a smooth hump, fattest at the centre of the bolus
                        f = 1.0 - (d / spread)
                        r = max(r, base_r + swell * f * f * (3.0 - 2.0 * f))
                Ellipse(pos=(cx - r, cy - r), size=(r * 2, r * 2))

    def _draw_fallback(self):
        """No art: still a TUBE with something moving along it, never waves."""
        x, y, w, h = self.x, self.y, self.width, self.height
        cy = y + h * 0.5
        cable = theme.hex_to_rgba(theme.COLORS["accent"])
        with self.canvas:
            Color(*theme.hex_to_rgba(theme.COLORS["surface"]))
            RoundedRectangle(pos=(x + dp(8), cy - h * 0.18),
                             size=(w * 0.22, h * 0.36), radius=[dp(6)] * 4)
            RoundedRectangle(pos=(x + w - dp(8) - w * 0.22, cy - h * 0.22),
                             size=(w * 0.22, h * 0.44), radius=[dp(6)] * 4)
            Color(cable[0], cable[1], cable[2], 0.95)
            x0 = x + dp(8) + w * 0.22
            x1 = x + w - dp(8) - w * 0.22
            for k in range(self.BOLUSES):
                c = (self.phase + k / float(self.BOLUSES)) % 1.0
                bx = x0 + (x1 - x0) * c
                Ellipse(pos=(bx - dp(9), cy - dp(9)), size=(dp(18), dp(18)))
            Line(points=[x0, cy, x1, cy], width=dp(3.5), cap="round")


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

    burst = NumericProperty(0.0)                     # card-found ripple, 0->1

    def __init__(self, **kwargs):
        kwargs.setdefault("duration", 3.8)           # three phases -> a touch slower
        super().__init__(**kwargs)
        self._found = False
        self.bind(burst=self._redraw)

    def mark_card_found(self):
        """The medic can SEE the card — stop looping and fire the green ripple.

        Deliberately the same burst ConnectBoardAnim fires when a board appears:
        four green rings radiating from the junction, fading as they grow. It is
        one shared visual language meaning "the medic can see it now", and it
        should read identically whether what turned up is a board or a card.

        This is not decoration. A whole bench session went by with no way to
        tell whether the medic had noticed a hardware change (#71 — the medic
        sitting on a screen oblivious to what is plugged in), and the ripple is
        the answer to that.

        What it does NOT say is that anything is being written. Writing is
        destructive and stays behind a deliberate press; this means "I can see
        your card", so it is rings and no words.

        Safe to call repeatedly (the poll will), and safe to call before the
        widget has been laid out — the burst is a property animation, and the
        draw is skipped until there is a stage to draw on.
        """
        if self._found:
            return
        self._found = True
        self.stop()                                  # hold the finished frame
        self.phase = 1.0
        # RUN THE BURST PAST 1.0 SO IT ACTUALLY ENDS. Each ring's alpha is
        # (1 - f) * 0.9 where f = burst - i*0.16, so at burst == 1.0 only the
        # FIRST ring has faded out; the other three stop mid-flight and sit
        # there at alpha 0.14 / 0.29 / 0.43 forever. The operator read exactly
        # that as a hang: "the green ring animation ... froze ... it gives the
        # impression the process has stalled" (2026-08-08). It had in fact
        # finished — which is worse, because a finished animation that looks
        # stuck is indistinguishable from a wedged UI.
        #
        # The last ring needs f >= 1.0, i.e. burst >= 1 + 3*0.16 = 1.48. 1.6
        # leaves a margin and costs nothing: every ring completes its fade and
        # the stage returns to clean.
        Animation(burst=1.6, duration=1.4, t="out_quad").start(self)

    def _ripples(self, cx, cy):
        """The four rings, matching ConnectBoardAnim's exactly — except for how
        far they get to grow.

        That one radiates from a point near the middle of its stage; this one
        radiates from a card reader parked in the left-hand corner, and at the
        shared 0.52-of-the-stage radius the rings ran off the edge. Kivy does
        not clip a widget's canvas, so "off the edge" means drawn across the
        title above and the body text below (seen in the offline render before
        it went anywhere). The radius is capped to the room actually available;
        colour, count, stagger, fade and width are untouched, and those are
        what carry the meaning.
        """
        room = min(cx - self.x, cy - self.y,
                   self.x + self.width - cx, self.y + self.height - cy)
        maxr = min(min(self.width, self.height) * 0.52, max(dp(24), room))
        for i in range(4):
            f = self.burst - i * 0.16                # stagger: they radiate out
            if f <= 0.0:
                continue
            f = min(1.0, f)
            Color(0.2, 0.9, 0.4, (1.0 - f) * 0.9)    # fade as each ring grows
            Line(circle=(cx, cy, dp(10) + f * maxr), width=dp(3.0))

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
            Color(*READER_TINT)
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
            if self._found:
                # centred on the reader's own slot — the rings come from where
                # the card actually is, not from the middle of the stage
                self._ripples(*self._kv(
                    rtx + (self._SLOT_L[0] + self._SLOT_R[0]) / 2.0 * rw,
                    rty + (self._SLOT_L[1] + self._SLOT_R[1]) / 2.0 * rh))
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
            if self._found:                    # same signal without the artwork
                self._ripples(cx + cw / 2, cy)
        card = self._label("card", text="SD", font_size="14sp", bold=True,
                          halign="center", valign="middle",
                          color=theme.hex_to_rgba(theme.COLORS["background"]))
        card.size = (cw, ch)
        card.pos = (cx, cy - ch / 2)


class InsertSdIntoPiAnim(_CardStage):
    """The imaged microSD slides into THIS Pi's own slot.

    The mirror of InsertSdAnim: that one puts the card INTO THE MEDIC to be
    written; this one shows the finished card going HOME into the Pi.

    It used to know one board. The slot was three constants measured off
    pi_zero_2w.png and the card always came in from the left, so selecting a
    3A+ produced a Zero-shaped animation with the card entering an edge that
    model has nothing on. Which edge, which face and which way up now come from
    ``ui.pi_sd_geometry`` per model, and an unknown model gets an honest
    outline instead of somebody else's board.
    """

    def __init__(self, pi_key: str = "", **kwargs):
        kwargs.setdefault("duration", 3.0)
        super().__init__(pi_key=pi_key, **kwargs)

    def _draw(self):
        # NOTE: everything below must be inside `with self.canvas`. Without it
        # the instructions are constructed and then thrown away — the widget
        # renders nothing at all, which is exactly how this step reached the
        # operator: a correct animation on a blank screen (2026-08-02).
        pi_tex, geo = self._pi_art()
        card = _card_texture()
        if pi_tex is None or card is None:
            return self._draw_fallback()
        w, h = self.width, self.height
        pa = pi_tex.width / float(pi_tex.height)
        pw = w * 0.68
        ph = pw / pa
        if ph > h * 0.7:
            ph = h * 0.7
            pw = ph * pa
        px = self.x + w - pw - dp(8)
        py = self.y + (h - ph) / 2.0
        brect = sdgeo.board_rect(geo, (px, py, pw, ph))
        card_len, card_w = sdgeo.card_size(geo, brect,
                                           card.width / float(card.height))
        t = min(1.0, self.phase * 1.35)              # arrive, then dwell
        frame = sdgeo.insert_frame(t, geo, brect, card_len, gap=1.4)
        with self.canvas:
            if frame.behind_board:                   # underside slot: it goes
                self._draw_card(card, frame, card_len, card_w)   # UNDER the board
                Color(1, 1, 1, 1)
                Rectangle(texture=pi_tex, pos=(px, py), size=(pw, ph))
                self._draw_card_ghost(card, frame, card_len, card_w)
            else:                                    # top-mounted: clipped at
                Color(1, 1, 1, 1)                    # the mouth
                Rectangle(texture=pi_tex, pos=(px, py), size=(pw, ph))
                self._draw_card(card, frame, card_len, card_w, behind_ok=False)

    def _draw_fallback(self):
        """No artwork, or a model we can't place the slot on — a plain board
        outline with the card entering its edge. Deliberately generic: it says
        'a board', which is true, rather than naming the wrong one."""
        w, h = self.width, self.height
        bx, by = self.x + w * 0.30, self.y + h * 0.30
        bw, bh = w * 0.62, h * 0.40
        travel = min(1.0, self.phase * 1.35)
        travel = travel * travel * (3.0 - 2.0 * travel)
        cw, ch = w * 0.12, h * 0.16
        cx = self.x + w * 0.05 + (bx - cw * 0.5 - (self.x + w * 0.05)) * travel
        with self.canvas:
            Color(*theme.hex_to_rgba(theme.COLORS["surface"]))
            Rectangle(pos=(bx, by), size=(bw, bh))
            Color(*theme.hex_to_rgba(theme.COLORS["accent"]))
            Rectangle(pos=(cx, by + bh / 2 - ch / 2), size=(cw, ch))


class SdHandoverAnim(_CardStage):
    """The card leaves Node Medic's reader and goes home into the operator's Pi.

    One continuous motion, because it is one continuous action: the medic has
    finished writing the card, so it comes OUT of the reader, travels across,
    and goes INTO the Pi — at the edge that Pi's slot is actually on, the right
    way round, on the right face.

    Everything board-specific is data (``ui.pi_sd_geometry``); everything here
    is the staging. The medic side is held in the two constants below because
    the reader hardware is not settled — a built-in reader is planned, and when
    it arrives this is a layout edit rather than a rewrite.
    """

    def __init__(self, pi_key: str = "", **kwargs):
        kwargs.setdefault("duration", 4.4)           # three phases + a dwell
        super().__init__(pi_key=pi_key, **kwargs)
        self._moved = False

    def mark_moved(self):
        """The medic has SEEN the card go — its reader is empty. Stop looping,
        hold the card seated in the Pi, and release the green pulse that until
        now had nothing behind it."""
        if self._moved:
            return
        self._moved = True
        self.stop()
        self.phase = 1.0

    @staticmethod
    def reader_slot_fraction(reader_tex_aspect):
        """How long the reader's own slot is, as a fraction of its sprite
        height — used to scale the reader so its mouth matches the card. The
        mouth runs from _SLOT_L to _SLOT_R across sd_reader_body.png, angled,
        because the dongle is drawn in perspective."""
        sl, sr = InsertSdAnim._SLOT_L, InsertSdAnim._SLOT_R
        return math.hypot((sr[0] - sl[0]) * reader_tex_aspect, sr[1] - sl[1])

    def _draw(self):
        pi_tex, geo = self._pi_art()
        card = _card_texture()
        reader = _texture(SD_READER_BODY_PNG)
        medic = _texture(MEDIC_BODY_PNG) or _texture(MEDIC_PNG)
        if pi_tex is None or card is None or reader is None:
            return self._draw_fallback()
        ra = reader.width / float(reader.height)
        lay = sdgeo.handover_layout(
            (self.x, self.y, self.width, self.height), geo,
            pi_tex.width / float(pi_tex.height),
            card.width / float(card.height), ra,
            self.reader_slot_fraction(ra),
            medic.width / float(medic.height) if medic is not None else None)
        frame = sdgeo.handover_frame(self.phase, geo, lay.board, lay.card_len,
                                     lay.reader_seat, sdgeo.READER_AXIS_DEG)
        with self.canvas:
            if lay.medic is not None and medic is not None:
                Color(1, 1, 1, 1)
                Rectangle(texture=medic, pos=lay.medic[:2], size=lay.medic[2:])
            Color(*READER_TINT)
            Rectangle(texture=reader, pos=lay.reader[:2], size=lay.reader[2:])
            # the board goes down BEFORE the card for an underside slot (it has
            # to hide it) and AFTER for a top-mounted one (the card lies on the
            # board and is swallowed at the mouth)
            if frame.behind_board:
                self._draw_card(card, frame, lay.card_len, lay.card_w)
                Color(1, 1, 1, 1)
                Rectangle(texture=pi_tex, pos=lay.pi[:2], size=lay.pi[2:])
                self._draw_card_ghost(card, frame, lay.card_len, lay.card_w)
            else:
                Color(1, 1, 1, 1)
                Rectangle(texture=pi_tex, pos=lay.pi[:2], size=lay.pi[2:])
                self._draw_card(card, frame, lay.card_len, lay.card_w,
                                behind_ok=False)
            # seated: a short green pulse at the mouth. It is the only moment
            # the operator is told "that's it, it's in" without any words —
            # which is exactly why it must not be said until it is TRUE.
            #
            # This is a LOOPING animation: phase wraps 0 -> 1 -> 0 for as long
            # as the step is on screen, so a pulse keyed on the loop reaching
            # its end fired every few seconds, over and over, while the card was
            # still sitting in the medic's reader (operator, 2026-08-09: "the
            # animation shows the connect reward green circles on loop before
            # connection"). A success signal that plays before the success is a
            # fake demo with extra steps, and it trains the operator to stop
            # believing the green.
            #
            # It now waits for mark_moved() — the medic actually seeing the card
            # leave its reader — and the loop only ever draws the motion.
            if frame.seated >= 1.0 and getattr(self, "_moved", False):
                mxx, myy, _span = sdgeo.slot_mouth(geo, lay.board)
                pulse = 0.5 + 0.5 * math.sin(self.phase * 14.0)
                g = theme.hex_to_rgba(theme.COLORS["green"])
                Color(g[0], g[1], g[2], 0.35 + 0.45 * pulse)
                Line(circle=(mxx, myy, lay.card_w * 0.9), width=dp(2.0))

    def _draw_fallback(self):
        """Unknown model or missing art: the medic, and a card going into a
        plain board. Never a specific Pi we haven't confirmed."""
        x, y, w, h = self.x, self.y, self.width, self.height
        medic = _texture(MEDIC_BODY_PNG) or _texture(MEDIC_PNG)
        bw, bh = w * 0.30, h * 0.46
        bx, by = x + w - bw - dp(8), y + (h - bh) / 2.0
        t = min(1.0, self.phase * 1.25)
        t = t * t * (3.0 - 2.0 * t)
        cw, ch = w * 0.09, h * 0.20
        start = x + w * 0.30
        cx = start + (bx - cw * 0.4 - start) * t
        with self.canvas:
            if medic is not None:
                mh = h * 0.9
                mw = mh * (medic.width / float(medic.height))
                Color(1, 1, 1, 1)
                Rectangle(texture=medic, pos=(x + dp(2), y + (h - mh) / 2.0),
                          size=(mw, mh))
            Color(*theme.hex_to_rgba(theme.COLORS["surface"]))
            RoundedRectangle(pos=(bx, by), size=(bw, bh), radius=[dp(6)] * 4)
            Color(*theme.hex_to_rgba(theme.COLORS["accent"]))
            RoundedRectangle(pos=(cx, y + (h - ch) / 2.0), size=(cw, ch),
                             radius=[dp(3)] * 4)


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
        """Draw the operator's OWN Pi, and only mark sockets we have measured.

        ART AND GEOMETRY COME FROM THE SAME BOARD, ALWAYS. That is the whole
        rule. Before it, the sprite fell back to a Zero while the ring positions
        stayed the Zero's, so every operator was shown a Zero — with a 3 A+ on
        the bench, a picture of the wrong board with its sockets in the wrong
        places, under words that correctly described the right one.

        A board we have measured (ui.pi_connector_geometry) gets its own
        picture with its own sockets marked. A board we have not gets its own
        picture with NOTHING marked, and the per-model wording carries the
        answer. Only when we know no board at all does the Zero appear, and then
        the Zero's own numbers are the right ones to use.
        """
        super().__init__(**kwargs)
        self._pi_png = ""
        self._geo = None
        self._pi_key = pi_key or ""
        if pi_key:
            try:
                from ui import board_images
                self._pi_png = board_images.image_for_pi(pi_key) or ""
            except Exception:
                self._pi_png = ""
            try:
                from ui.pi_connector_geometry import sockets_for
                self._geo = sockets_for(pi_key)
            except Exception:
                self._geo = None
        if not self._pi_png:
            # No art for this model: the Zero sprite is what will be drawn, so
            # the Zero's fractions are the only consistent ones to mark with.
            try:
                from ui.pi_connector_geometry import sockets_for
                self._geo = sockets_for("pi_zero_2w")
            except Exception:
                self._geo = None

    @staticmethod
    def _ease(v):
        v = 0.0 if v < 0.0 else 1.0 if v > 1.0 else v
        return v * v * (3.0 - 2.0 * v)

    def _draw(self):
        """Pi on the LEFT, medic on the RIGHT, an L-shaped cable between them.

        Operator, 2026-08-04, asked three times: keep the Pi on the LEFT. Two
        earlier attempts drifted back to stacking it on top, and the reason is
        physical rather than cosmetic — a plug enters a Pi Zero's socket
        VERTICALLY, because the socket is on the board's underside. With one
        straight run of cable the medic is forced to sit below that socket, and
        "below" on a wide, short stage always reads as "underneath".

        The elbow is what breaks the tie: the cable leaves the medic's base,
        runs along, turns, and rises into the port. The last segment is still
        vertical, so the plug goes in straight, while the two objects stand
        side by side.

        Operator's earlier corrections still hold: the cable is complete and
        attached (2026-08-02), only the plug end moves, it starts clear of the
        board and visibly inserts, and the whole run is ONE braid at ONE width
        (2026-08-04).

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
        # A socket on the board's RIGHT EDGE is entered sideways, not from
        # below, and the whole scene below is built around a plug rising into
        # the bottom edge. A 3 A+ is the case in hand: full-size USB-A on the
        # right, micro-USB power underneath. Its own scene, rather than the
        # vertical one with the numbers bent to fit.
        if self._geo is not None and self._geo.approach == "right":
            return self._draw_side_entry(pi)
        # The operator's drawn art (2026-08-04) first; the earlier photo cut-outs
        # stay as fallback so a missing file degrades instead of blanking.
        medic = (_texture(MEDIC_NOCABLE_PNG) or _texture(MEDIC_BODY_PNG)
                 or _texture(MEDIC_PNG))
        plug = _texture(PLUG_MICRO_DRAWN_PNG) or _texture(PLUG_MICRO_PNG)
        braid = _texture(BRAID_MICRO_DRAWN_PNG) or _texture(CABLE_BRAID_PNG)
        x, y, w, h = self.x, self.y, self.width, self.height

        t = 1.0 if self._connected else self._ease(min(1.0, self.phase * 1.15))
        # A real micro-USB moulding is ~2.4:1 and the drawn one keeps that
        # ratio, so this is sized by WIDTH and the height follows.
        plug_w = dp(20)
        plug_h = plug_w * (plug.height / float(plug.width)) if plug else dp(48)
        travel = plug_h * 0.55 + dp(8)

        # --- side by side: Pi on the LEFT, medic on the RIGHT ---------------
        mh = h * 0.80
        mw = mh * (medic.width / float(medic.height)) if medic else w * 0.2
        myy = y + dp(2)
        ph = h * 0.27
        pw = ph * (pi.width / float(pi.height))

        # The medic sprite's leftmost ink IS its antenna, so this gap is
        # measured Pi-edge-to-antenna: the operator wants them almost
        # touching, which also buys back screen width.
        span = dp(14)
        total = pw + span + mw
        pxx = x + (w - total) / 2.0                 # centre the pair
        mxx = pxx + pw + span

        geo = self._geo
        # A board with no measured geometry is drawn TRUTHFULLY and marked not
        # at all — see __init__. The plug still travels and still seats, so the
        # step keeps showing a connection being made; it simply stops claiming
        # to know which hole, which is the one thing it was getting wrong.
        gdx = geo.data[0] if geo else 0.5
        gpx = geo.power[0] if (geo and geo.power) else None
        data_x = pxx + pw * gdx
        pwr_x = pxx + pw * gpx if gpx is not None else None
        floor = y + dp(16)                          # where the sweep bottoms out
        # Enough height that the plug still starts ABOVE the curve at t=0, or it
        # sets off from inside its own cable.
        port_y = floor + travel + plug_h + dp(34)
        gdy = geo.data[1] if geo else PI_ZERO_PORT_Y
        pyy = port_y - ph * (1.0 - gdy)

        # --- the plug travels up and INSERTS --------------------------------
        # It starts clear of the board, approaches, and goes in (operator,
        # 2026-08-04). The metal tongue is the top ~28% of the moulding, so
        # seating means sinking that much INTO the shell — at dp(3) the plug
        # just kissed the edge and never looked inserted.
        seated = port_y + plug_h * 0.28
        top = seated - travel * (1.0 - t)
        plug_bottom = top - plug_h

        with self.canvas:
            # Cable FIRST, so the case hides where it enters. ONE tiled braid at
            # ONE width for the whole run — mixing it with the drawing's own
            # cable made the rope visibly step in width partway along, and the
            # step moved as the plug travelled (operator: "keep the cable
            # uniform"). Then the medic, then the Pi, then the plug last: the
            # plug is the thing in motion and must never be occluded by the
            # board it is entering.
            bw = plug_w * 0.42
            Color(1, 1, 1, 1)
            if braid is not None:
                # A SWEEP, not an elbow. Modelled on the radio-board step's own
                # sprite (node_medic_cable.png), which the operator pointed at:
                # a thick braided cable leaving the medic's base, swooping down
                # and round, and rising to a plug that points up.
                #
                # The last control point sits directly beneath the plug, which
                # forces the final tangent VERTICAL — the plug still enters the
                # socket straight, which is the physical constraint behind this
                # whole layout. Both controls stay inside the widget: Kivy does
                # not clip a canvas, so dipping them below the floor would paint
                # over the body text under the stage.
                p0 = (mxx + mw * 0.42, myy + dp(6))      # out of the base
                p1 = (p0[0], y + dp(8))
                p2 = (data_x, y + dp(8))
                p3 = (data_x, plug_bottom + dp(3))
                pts = _bezier(p0, p1, p2, p3, 34)
                for i in range(len(pts) - 1):
                    (x0, y0), (x1, y1) = pts[i], pts[i + 1]
                    ddx, ddy = x1 - x0, y1 - y0
                    ln = math.hypot(ddx, ddy)
                    if ln < 0.5:
                        continue
                    # the braid slice runs along +y, so subtract 90°
                    ang = math.degrees(math.atan2(ddy, ddx)) - 90.0
                    cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
                    PushMatrix()
                    Rotate(angle=ang, origin=(cx, cy))
                    Rectangle(texture=braid,
                              pos=(cx - bw / 2.0, cy - ln / 2.0),
                              size=(bw, ln + dp(1.5)))   # overlap, no seams
                    PopMatrix()

            if medic is not None:
                Color(1, 1, 1, 1)
                Rectangle(texture=medic, pos=(mxx, myy), size=(mw, mh))

            Color(1, 1, 1, 1)
            Rectangle(texture=pi, pos=(pxx, pyy), size=(pw, ph))
            self._draw_card_seated(self._pi_key, (pxx, pyy, pw, ph))
            if plug is not None:
                Color(1, 1, 1, 1)
                Rectangle(texture=plug, pos=(data_x - plug_w / 2.0, plug_bottom),
                          size=(plug_w, plug_h))

            # the socket it is aiming at, breathing until it seats
            # THE AIMING RING IS NOT GREEN. Green is this UI's word for "the
            # medic can see it" — the Connected! burst, the health dots, the
            # card's seated pulse. Using it for "aim here" made the operator
            # read a target as an acknowledgement, with nothing plugged in
            # (2026-08-10, the same fault as the card animation's ripple a day
            # earlier). It aims in accent blue and only goes green once the
            # medic has actually seen the Pi.
            if geo is not None:
                if self._connected:
                    Color(self._CABLE[0], self._CABLE[1], self._CABLE[2], 0.95)
                    Line(circle=(data_x, port_y, dp(16)), width=dp(2.5))
                elif t < 1.0:
                    pulse = 0.5 + 0.5 * math.sin(self.phase * 4 * math.pi)
                    aim = theme.hex_to_rgba(theme.COLORS["accent"])
                    Color(aim[0], aim[1], aim[2], 0.30 + 0.45 * pulse)
                    Line(circle=(data_x, port_y, dp(14)), width=dp(2.0))
            # And its identical twin, which carries power only. Drawn as a NO
            # ENTRY sign — ring plus a bar through it — not just a differently
            # coloured ring (operator, 2026-08-04). Two rings side by side say
            # "here are two ports"; a struck-through one says "not this one",
            # which is the entire point of the step.
            if pwr_x is not None:
                Color(*self._NO)
                Line(circle=(pwr_x, port_y, dp(10)), width=dp(2.0))
                bar = dp(10) * 0.707                # 45°, ends on the ring
                Line(points=[pwr_x - bar, port_y - bar,
                             pwr_x + bar, port_y + bar],
                     width=dp(2.0), cap="none")

        if geo is not None:
            d = self._label("data", text="DATA", font_size="12sp", bold=True,
                            color=self._CABLE, halign="center")
            d.size = (dp(64), dp(18))
            d.pos = (data_x - dp(32), port_y + dp(12))
        if pwr_x is not None:
            pl = self._label("pwr", text="PWR IN", font_size="11sp",
                             color=self._PWR, halign="center")
            pl.size = (dp(64), dp(16))
            pl.pos = (pwr_x - dp(32), port_y + dp(12))

    def _draw_card_seated(self, pi_key, sprite_rect):
        """Show the microSD ALREADY IN the Pi.

        By this step the card has been written and moved into the board — the
        step before says so in words — but the picture showed an empty Pi, so
        the operator has to hold two ideas at once and trust that the tool has
        not forgotten (their note, 2026-08-10: "clearly show that the SD card is
        inserted in the pi at this stage").

        Drawn from the same per-board slot geometry the handover animation uses,
        so it lands in the real slot on whichever board is being shown, and
        simply does not draw for a board whose slot has never been measured.
        """
        try:
            from ui import pi_sd_geometry as sdgeo
        except Exception:                                          # noqa: BLE001
            return
        geo = sdgeo.geometry_for(pi_key or "")
        card = _card_texture()
        if geo is None or card is None:
            return
        brect = sdgeo.board_rect(geo, sprite_rect)
        cl, cw = sdgeo.card_size(geo, brect, card.width / float(card.height))
        cx, cy = sdgeo.seated_centre(geo, brect, cl)
        ang = sdgeo.entry_angle(geo)
        Color(1, 1, 1, 1)
        PushMatrix()
        Rotate(angle=ang, origin=(cx, cy))
        Rectangle(texture=card, pos=(cx - cl / 2.0, cy - cw / 2.0), size=(cl, cw))
        PopMatrix()

    def _draw_side_entry(self, pi):
        """The plug goes in from the SIDE — for boards whose data socket is on
        an edge facing the medic (Pi 3 A+: full-size USB-A on the right).

        Same grammar as the bottom-entry scene, turned through ninety degrees:
        nothing moves but the plug, the target socket is ringed and named, and
        the socket that takes power only is struck through where it actually is
        — underneath, on a different edge, which is exactly the confusion this
        step exists to prevent.

        The Pi keeps the LEFT, as the operator asked three times. With the
        socket facing right, the cable now runs straight across to the medic
        instead of swooping underneath, which is both simpler and truer.
        """
        geo = self._geo
        medic = (_texture(MEDIC_NOCABLE_PNG) or _texture(MEDIC_BODY_PNG)
                 or _texture(MEDIC_PNG))
        plug = _texture(PLUG_MICRO_DRAWN_PNG) or _texture(PLUG_MICRO_PNG)
        braid = _texture(BRAID_MICRO_DRAWN_PNG) or _texture(CABLE_BRAID_PNG)
        x, y, w, h = self.x, self.y, self.width, self.height
        t = 1.0 if self._connected else self._ease(min(1.0, self.phase * 1.15))

        plug_w = dp(20)
        plug_h = plug_w * (plug.height / float(plug.width)) if plug else dp(48)

        mh = h * 0.72
        mw = mh * (medic.width / float(medic.height)) if medic else w * 0.2
        ph = h * 0.62
        pw = ph * (pi.width / float(pi.height))
        gap = plug_h + dp(26)                       # room for the plug between
        total = pw + gap + mw
        pxx = x + (w - total) / 2.0
        pyy = y + (h - ph) / 2.0
        mxx = pxx + pw + gap
        myy = y + (h - mh) / 2.0

        # y measured DOWN in the geometry, up on the canvas
        data_x = pxx + pw * geo.data[0]
        data_y = pyy + ph * (1.0 - geo.data[1])
        pwr_x = pwr_y = None
        if geo.power:
            pwr_x = pxx + pw * geo.power[0]
            pwr_y = pyy + ph * (1.0 - geo.power[1])

        # The plug lies on its side, entering leftwards. Seated means its metal
        # tongue is INSIDE the shell, not kissing the edge.
        seated_x = data_x + plug_h * 0.22
        start_x = seated_x + plug_h * 0.55 + dp(10)
        cx = start_x + (seated_x - start_x) * t

        with self.canvas:
            Color(1, 1, 1, 1)
            if braid is not None:
                bw = plug_w * 0.42
                p0 = (mxx + mw * 0.10, myy + mh * 0.30)
                p3 = (cx + plug_h * 0.5, data_y)
                pts = _bezier(p0, (p0[0] - dp(20), p0[1]),
                              (p3[0] + dp(24), p3[1]), p3, 24)
                for i in range(len(pts) - 1):
                    (x0, y0), (x1, y1) = pts[i], pts[i + 1]
                    ddx, ddy = x1 - x0, y1 - y0
                    ln = math.hypot(ddx, ddy)
                    if ln < 0.5:
                        continue
                    ang = math.degrees(math.atan2(ddy, ddx)) - 90.0
                    mx, my = (x0 + x1) / 2.0, (y0 + y1) / 2.0
                    PushMatrix()
                    Rotate(angle=ang, origin=(mx, my))
                    Rectangle(texture=braid, pos=(mx - bw / 2.0, my - ln / 2.0),
                              size=(bw, ln + dp(1.5)))
                    PopMatrix()

            if medic is not None:
                Color(1, 1, 1, 1)
                Rectangle(texture=medic, pos=(mxx, myy), size=(mw, mh))
            Color(1, 1, 1, 1)
            Rectangle(texture=pi, pos=(pxx, pyy), size=(pw, ph))
            self._draw_card_seated(self._pi_key, (pxx, pyy, pw, ph))

            if plug is not None:
                # THE TIP MUST POINT AT THE PI, NOT BACK AT THE MEDIC.
                # The moulding is drawn pointing UP (+y). Rotating -90° turns
                # +y into +x — to the RIGHT, i.e. back the way the cable came,
                # while the plug travelled left into the socket. So it slid in
                # backwards for a fortnight and read as a plug being pulled
                # OUT (operator, 2026-08-10: "the plug is facing towards the
                # node medic, it needs to face towards the Raspberry Pi").
                # +90° turns +y into -x: tip leading, into the board.
                Color(1, 1, 1, 1)
                PushMatrix()
                Rotate(angle=90, origin=(cx, data_y))
                Rectangle(texture=plug,
                          pos=(cx - plug_w / 2.0, data_y - plug_h / 2.0),
                          size=(plug_w, plug_h))
                PopMatrix()

            if self._connected:
                Color(self._CABLE[0], self._CABLE[1], self._CABLE[2], 0.95)
                Line(circle=(data_x, data_y, dp(16)), width=dp(2.5))
            elif t < 1.0:
                pulse = 0.5 + 0.5 * math.sin(self.phase * 4 * math.pi)
                aim = theme.hex_to_rgba(theme.COLORS["accent"])
                Color(aim[0], aim[1], aim[2], 0.30 + 0.45 * pulse)
                Line(circle=(data_x, data_y, dp(14)), width=dp(2.0))
            if pwr_x is not None:
                Color(*self._NO)
                Line(circle=(pwr_x, pwr_y, dp(10)), width=dp(2.0))
                bar = dp(10) * 0.707
                Line(points=[pwr_x - bar, pwr_y - bar, pwr_x + bar, pwr_y + bar],
                     width=dp(2.0), cap="none")

        # Labels go OUTSIDE the board. Sat next to their sockets they landed on
        # the PCB itself — green text on green silkscreen, over the very detail
        # the operator is being asked to look at (offline render, 2026-08-09).
        d = self._label("data", text="DATA", font_size="12sp", bold=True,
                        color=self._CABLE, halign="center")
        d.size = (dp(64), dp(18))
        d.pos = (data_x - dp(32), pyy + ph + dp(4))
        if pwr_x is not None:
            pl = self._label("pwr", text="PWR IN", font_size="11sp",
                             color=self._PWR, halign="center")
            pl.size = (dp(64), dp(16))
            pl.pos = (pwr_x - dp(32), pyy - dp(20))

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
