"""The card's operation: NODE MEDIC implanting organs into an SD card.

The operator's own scene (2026-08-02): *"The SD card lies on an operating table.
The surgeon gently installs little glowing components — kernel, filesystem,
reticulum logo, bootloader — like organs. A heart monitor gradually stabilises.
At the end: the monitor beeps happily, the SD card smiles, the surgeon gives a
thumbs up."*

THE SURGEON CHANGED, and the reason is a change in the hardware route rather
than a change of mind. The scene was built when the card was imaged INSIDE the
Pi — the Pi acted as its own card reader over rpiboot — so the Pi was quite
literally the one doing the work. That route is retired: the card is written in
Node Medic's own reader now. Operator, watching this screen live (2026-08-06):
*"it's not the pie provisioning the SD card anymore. I need to change it to the
node medic as the character that's provisioning the SD card."*

So the actor is the medic, drawn from the same illustration the card steps use,
and the Pi is not in this scene at all — at this moment it is in the operator's
other hand, waiting for the card.

THE SURGEON GOT A REAL ARM (2026-09-22). The operator, with a photo of this
screen: *"just the node medic with some pink coloured lines that are supposed
to represent arms holding the SD card, throwing the icons back and forwards
... the arms are very rudimentary."* The staging — where everything stands,
the pick-and-place cycle, the arm's joints — now lives in ``ui.surgery_layout``
(pure, shared with scripts/preview_surgery.py so the frames that were looked
at are the frames that ship). This file only paints it. What changed and why
is written at the top of that module.

Why it earns its screen. Writing a card takes minutes with nothing to look at,
and the most damaging thing an operator can do in that window is decide it has
hung and pull the card. A scene that visibly PROGRESSES — organs landing one by
one, a heart trace steadying — answers "is this still working?" without anyone
having to trust a number. The stage list it narrates
(``provisioning.pi_imager.IMAGING_STAGES``) follows the real order of
``flash()``, so it is a picture of the actual operation rather than a loading
toy.

Everything is drawn from primitives plus the existing board/card sprites: no new
artwork to commission, and no emoji — the glyph fonts on this panel render them
as tofu boxes (the on-screen keyboard learned that the hard way).
"""

from __future__ import annotations

import math

from kivy.graphics import (Color, Ellipse, Line, Quad, Rectangle,
                           RoundedRectangle)
from kivy.metrics import dp
from kivy.properties import NumericProperty
from kivy.uix.widget import Widget

from ui import surgery_layout as sl
from ui import theme
from ui.widgets.birth_anims import (MEDIC_BODY_PNG, MEDIC_PNG,
                                    SD_ENDURANCE_PNG, SD_PNG, _texture)

from ui.organ_art import ORGANS as _ORGANS, organ_file


def _organ_texture(key):
    """The sprite for an organ, or None so the caller falls back to a disc."""
    path = organ_file(key)
    return _texture(path) if path else None


_TABLE = (0.16, 0.19, 0.22, 1)
_TABLE_EDGE = (0.38, 0.44, 0.50, 1)
_TRACE = (0.35, 0.95, 0.55, 1)
#: The arm is the medic's own: the dark linework every illustration here has,
#: the case's gunmetal-bronze for the limb, its rim colour for the highlights,
#: steel for the instrument. Nothing skin-coloured — the medic has no skin.
_OUTLINE = (0.10, 0.09, 0.08, 1)
_ARM = (0.33, 0.30, 0.27, 1)
_ARM_HI = (0.62, 0.56, 0.46, 1)
_STEEL = (0.80, 0.83, 0.86, 1)

_DISCHARGE_TEX = None


def _discharge_texture():
    """The word on the monitor once the patient has left, rendered once.

    "DISCHARGED" is the honest word for what happened and it keeps the theatre
    intact — but it is also the one word here an operator might not know in a
    hospital sense, so it is run through ``tr()`` like everything else and the
    screen underneath still says "Done" in plain language.
    """
    global _DISCHARGE_TEX
    if _DISCHARGE_TEX is None:
        try:
            from kivy.core.text import Label as CoreLabel
            from ui.i18n import tr
            cl = CoreLabel(text=tr("DISCHARGED"), font_size=dp(15), bold=True,
                           color=(_TRACE[0], _TRACE[1], _TRACE[2], 1))
            cl.refresh()
            _DISCHARGE_TEX = cl.texture
        except Exception:
            _DISCHARGE_TEX = False        # never try again; the tick carries it
    return _DISCHARGE_TEX or None


def _stroke(points, width, colour, outline=True):
    """A drawn line: dark linework underneath, the colour on top — the look of
    the illustrated sprites, so the arm belongs to the same picture."""
    if outline:
        Color(*_OUTLINE)
        Line(points=points, width=width + dp(1.2), cap="round", joint="round")
    Color(*colour)
    Line(points=points, width=width, cap="round", joint="round")


def _joint(p, r, colour=_ARM):
    Color(*_OUTLINE)
    Ellipse(pos=(p[0] - r - dp(1.2), p[1] - r - dp(1.2)),
            size=(2 * r + dp(2.4), 2 * r + dp(2.4)))
    Color(*colour)
    Ellipse(pos=(p[0] - r, p[1] - r), size=(2 * r, 2 * r))
    Color(*_ARM_HI)
    Ellipse(pos=(p[0] - r * 0.35, p[1] - r * 0.35), size=(r * 0.7, r * 0.7))


class SurgeryAnim(Widget):
    """Drive with :meth:`set_fraction` (0→1).

    ``pi_key`` is accepted and IGNORED. It used to pick the surgeon's board,
    back when the Pi wrote its own card; the surgeon is Node Medic now and the
    Pi is not in the scene, so there is nothing for it to choose. Kept only so
    the imaging screen's call site does not have to change in the same breath —
    it is not a hook to hang a Pi back on.
    """

    phase = NumericProperty(0.0)          # free-running, for the idle motion
    fraction = NumericProperty(0.0)       # the real progress of the write
    #: 0 = under the monitor, 1 = discharged. See :meth:`finish`.
    discharged = NumericProperty(0.0)

    def __init__(self, pi_key: str = "", **kwargs):
        super().__init__(**kwargs)
        self._ev = None
        self._landed = []                 # organ keys already implanted
        self.bind(phase=self._redraw, fraction=self._redraw,
                  discharged=self._redraw, pos=self._redraw, size=self._redraw)

    # -- driving ----------------------------------------------------------
    def start(self):
        from kivy.clock import Clock
        self.stop()
        self._ev = Clock.schedule_interval(self._tick, 1 / 30.0)

    def stop(self):
        if self._ev is not None:
            self._ev.cancel()
            self._ev = None

    def _tick(self, dt):
        # one loop of phase is one pick-and-place; the rate is the layout
        # module's so the previewer and the device agree on what 2.4 s shows
        self.phase = (self.phase + dt * sl.PHASE_PER_S) % 1.0

    def set_fraction(self, f):
        from provisioning.pi_imager import stages_upto
        f = max(0.0, min(1.0, float(f or 0.0)))
        self._landed = [s["organ"] for s in stages_upto(f)]
        self.fraction = f

    #: How long the happy rhythm holds before the patient is discharged, and
    #: how long the handover itself takes. Short enough not to make anyone wait,
    #: long enough that the beat is seen to be STRONG before it stops.
    SETTLE_S = 1.6
    DISCHARGE_S = 1.2

    def finish(self):
        """End the operation: settle, then DISCHARGE — and stop the clock.

        The bug this exists to fix (operator, on the live screen, 2026-08-07):
        *"the heartbeat has been going all through the load — let's get rid of
        the heartbeat to let the user know that this is actually finished."*
        ``_done`` called ``set_fraction(1.0)`` and nothing else, so the trace
        scrolled on for ever. A monitor that never stops says the operation
        never ended; the operator sat looking at a finished card being watched
        by a machine that had not noticed.

        The one thing this must NOT do is flatline. A flat green line is the
        single most legible image in medicine and it means the patient died —
        exactly backwards on the screen that says the card is alive. So the
        trace keeps its rhythm and FADES, and a tick rises in its place: the
        monitoring stopped because there is nothing left to watch for.
        """
        from kivy.clock import Clock
        self.set_fraction(1.0)
        if self._ev is None:              # never started, or already stopped
            self.start()
        Clock.schedule_once(lambda _dt: self._discharge(), self.SETTLE_S)

    def _discharge(self):
        from kivy.animation import Animation
        anim = Animation(discharged=1.0, duration=self.DISCHARGE_S, t="out_cubic")
        # Stop the free-running clock only once the fade is DONE — the trace has
        # to still be beating while it fades, or it freezes mid-stroke and reads
        # as a crash rather than an ending.
        anim.bind(on_complete=lambda *_: self.stop())
        anim.start(self)

    # -- drawing ----------------------------------------------------------
    def _redraw(self, *_):
        self.canvas.clear()
        if self.width < dp(120) or self.height < dp(90):
            return                        # too small to read; draw nothing
        with self.canvas:
            self._draw_scene()

    def _next_stage(self):
        from provisioning.pi_imager import IMAGING_STAGES
        return next((s for s in IMAGING_STAGES if s["at"] > self.fraction), None)

    def _draw_scene(self):
        card = _texture(SD_ENDURANCE_PNG) or _texture(SD_PNG)
        medic = _texture(MEDIC_BODY_PNG) or _texture(MEDIC_PNG)
        ca = card.width / float(card.height) if card is not None else 1.324
        ma = medic.width / float(medic.height) if medic is not None else 0.935
        lay = sl.layout((self.x, self.y, self.width, self.height), ca, ma,
                        pad=dp(2))
        done = self.fraction >= 1.0
        nxt = self._next_stage()
        seat = sl.seat_point(lay.window, nxt["organ"]) if nxt else None
        pose = sl.arm_pose(self.phase, lay, seat)

        # --- the surgical light, tying the scene together --------------------
        # A soft cone from under the monitor onto the patient. It says these
        # things belong together; 0.07 alpha was invisible on the panel.
        cx, cy, cw, ch = lay.card
        top = lay.monitor[1] - dp(2)
        Color(1.0, 0.98, 0.85, 0.10)
        Quad(points=[cx + cw * 0.30, top, cx + cw * 0.70, top,
                     cx + cw * 1.15, cy, cx - cw * 0.15, cy])

        # --- the operating table: base, pedestal, slab, lit edge -------------
        Color(0.10, 0.12, 0.14, 1)
        RoundedRectangle(pos=lay.base[:2], size=lay.base[2:], radius=[dp(3)] * 4)
        Rectangle(pos=lay.pedestal[:2], size=lay.pedestal[2:])
        Color(*_TABLE)
        RoundedRectangle(pos=lay.table[:2], size=lay.table[2:], radius=[dp(4)] * 4)
        tx, ty, tw, th = lay.table
        Color(*_TABLE_EDGE)
        Line(points=[tx + dp(3), ty + th - dp(1), tx + tw - dp(3), ty + th - dp(1)],
             width=dp(1.0))

        # --- the instrument tray, with the next organ waiting on it ----------
        trx, try_, trw, trh = lay.tray
        Color(0.08, 0.09, 0.10, 1)
        RoundedRectangle(pos=(trx, try_), size=(trw, trh), radius=[dp(3)] * 4)
        Color(*_TABLE_EDGE)
        Line(rounded_rectangle=(trx, try_, trw, trh, dp(3)), width=dp(1.0))

        # --- the patient: the microSD card, LYING ON the table ---------------
        if card is not None:
            Color(1, 1, 1, 1)
            Rectangle(texture=card, pos=(cx, cy), size=(cw, ch))
        else:
            Color(0.90, 0.90, 0.92, 1)
            RoundedRectangle(pos=(cx, cy), size=(cw, ch), radius=[dp(4)] * 4)

        # --- the monitor's lead, from the band down to a pad on the patient --
        lead = []
        for px, py in sl.lead_points(lay):
            lead += [px, py]
        _stroke(lead, dp(1.6), (_TRACE[0], _TRACE[1], _TRACE[2], 0.85))
        pr = max(dp(3), lay.organ_r * 0.45)
        Color(*_OUTLINE)
        Ellipse(pos=(lay.lead_pad[0] - pr - dp(1), lay.lead_pad[1] - pr - dp(1)),
                size=(2 * pr + dp(2), 2 * pr + dp(2)))
        Color(*_TRACE)
        Ellipse(pos=(lay.lead_pad[0] - pr, lay.lead_pad[1] - pr), size=(2 * pr, 2 * pr))

        # --- the window the organs live in ----------------------------------
        wx, wy, ww, wh = lay.window
        Color(0.10, 0.10, 0.11, 1)
        RoundedRectangle(pos=(wx, wy), size=(ww, wh), radius=[dp(3)] * 4)

        # --- the organs, seated in the patient ------------------------------
        for i, key in enumerate(self._landed):
            ox, oy = sl.seat_point(lay.window, key)
            # a soft glow that breathes, so an implanted organ reads as ALIVE
            pulse = 0.5 + 0.5 * math.sin(self.phase * 2 * math.pi + i * 0.9)
            self._draw_organ(key, (ox, oy), lay, glow=0.22 + 0.16 * pulse)

        # --- the one being fitted right now ---------------------------------
        if nxt and pose.fit > 0.0 and nxt["organ"] not in self._landed:
            self._draw_organ(nxt["organ"], seat, lay, glow=0.0,
                             alpha=0.35 + 0.55 * pose.fit)
        # --- and the one waiting on the tray --------------------------------
        if nxt:
            wait = sl.tray_organ_alpha(self.phase)
            if wait > 0.0:
                self._draw_organ(nxt["organ"], lay.pick, lay, glow=0.30 * wait,
                                 alpha=wait)

        # --- the surgeon --------------------------------------------------
        mx, my, mw, mh = lay.medic
        if medic is not None:
            Color(1, 1, 1, 1)
            Rectangle(texture=medic, pos=(mx, my), size=(mw, mh))
        else:
            Color(0.20, 0.22, 0.24, 1)
            RoundedRectangle(pos=(mx, my), size=(mw, mh), radius=[dp(5)] * 4)
            Color(0.84, 0.0, 0.0, 1)      # the red cross, so it is still a medic
            t = min(mw, mh) * 0.16
            ccx, ccy = mx + mw / 2.0, my + mh / 2.0
            arm = min(mw, mh) * 0.30
            RoundedRectangle(pos=(ccx - t / 2, ccy - arm), size=(t, 2 * arm),
                             radius=[t / 2] * 4)
            RoundedRectangle(pos=(ccx - arm, ccy - t / 2), size=(2 * arm, t),
                             radius=[t / 2] * 4)

        self._draw_arm(pose, lay, nxt["organ"] if nxt else None)

        # --- the heart monitor, as a band across the top ---------------------
        self._draw_monitor(*lay.monitor, done)

        if done:
            self._draw_smile(cx, cy, cw, ch)

    def _draw_organ(self, key, at, lay, glow=0.0, alpha=1.0):
        """One organ, centred on *at*: its glow, then its sprite."""
        art = _ORGANS.get(key)
        col = art[1] if art else (1, 1, 1, 1)
        r = lay.organ_r
        ox, oy = at
        if glow > 0.0:
            Color(col[0], col[1], col[2], glow * alpha)
            Ellipse(pos=(ox - r * 1.9, oy - r * 1.9), size=(r * 3.8, r * 3.8))
        tex = _organ_texture(key)
        if tex is not None:
            gw = sl.organ_diam(lay)
            gh = gw * (tex.height / float(tex.width))
            Color(1, 1, 1, alpha)
            Rectangle(texture=tex, pos=(ox - gw / 2, oy - gh / 2), size=(gw, gh))
        else:
            Color(col[0], col[1], col[2], alpha)
            Ellipse(pos=(ox - r, oy - r), size=(r * 2, r * 2))

    def _draw_arm(self, pose, lay, carrying_key):
        """Shoulder on the case, upper arm, elbow, telescoping forearm, forceps
        — and the organ in the forceps when there is one.

        Drawn AFTER the medic so it comes out of the case, and after the card
        so it reaches over the patient. Widths scale with the organ so the arm
        is in proportion to what it handles, on any size of stage.
        """
        r = lay.organ_r
        w_up = max(dp(4), r * 0.70)
        w_fore = w_up * 0.72
        w_fore2 = w_up * 0.56
        w_rod = w_up * 0.42
        w_tine = max(dp(1.6), r * 0.22)
        sh, el, sv, sv2, wr, tip = (lay.shoulder, pose.elbow, pose.sleeve,
                                    pose.sleeve2, pose.wrist, pose.tip)

        _stroke([sh[0], sh[1], el[0], el[1]], w_up, _ARM)
        _stroke([el[0], el[1], wr[0], wr[1]], w_rod, _STEEL)        # inner rod
        _stroke([el[0], el[1], sv2[0], sv2[1]], w_fore2, _ARM)      # 2nd stage
        _stroke([el[0], el[1], sv[0], sv[1]], w_fore, _ARM)         # sleeve
        _joint(sh, w_up * 1.1)
        _joint(el, w_up * 0.85)
        # the forceps: two tines from the wrist pivot to the tips
        _stroke([wr[0], wr[1], pose.tine_a[0], pose.tine_a[1]], w_tine, _STEEL)
        _stroke([wr[0], wr[1], pose.tine_b[0], pose.tine_b[1]], w_tine, _STEEL)
        # the organ, held between them
        if pose.carrying and carrying_key:
            self._draw_organ(carrying_key, tip, lay, glow=0.30)
            Color(*_STEEL)
            Line(points=[wr[0], wr[1], pose.tine_a[0], pose.tine_a[1]], width=w_tine)
            Line(points=[wr[0], wr[1], pose.tine_b[0], pose.tine_b[1]], width=w_tine)
        # the pivot: the one red accent, the medic's own colour
        _joint(wr, max(dp(2.2), w_fore * 0.55),
               colour=theme.hex_to_rgba(theme.COLORS["red"]))

    def _draw_monitor(self, x, y, w, h, done):
        """The trace: noise and dropouts early, a clean rhythm by the end."""
        Color(0.06, 0.09, 0.08, 1)
        RoundedRectangle(pos=(x, y), size=(w, h), radius=[dp(6)] * 4)
        Color(0.20, 0.30, 0.26, 1)
        Line(rounded_rectangle=(x, y, w, h, dp(6)), width=dp(1.2))

        steady = self.fraction                     # 0 = erratic, 1 = strong
        mid = y + h * 0.5
        amp = h * (0.16 + 0.26 * steady)           # as monitor_trace draws it
        pts = []
        for px, py in sl.monitor_trace((x, y, w, h), steady, self.phase):
            pts.extend([px, py])
        gone = self.discharged                  # 0 = still monitoring, 1 = left
        if gone < 1.0:
            Color(_TRACE[0], _TRACE[1], _TRACE[2],
                  (0.55 + 0.45 * steady) * (1.0 - gone))
            Line(points=pts, width=dp(1.8))

            if done:
                # a happy beep: a bright ring at the last spike
                Color(_TRACE[0], _TRACE[1], _TRACE[2], 0.9 * (1.0 - gone))
                r = h * 0.18
                Line(circle=(x + w * 0.86, mid + amp * 0.9, r), width=dp(2))

        if gone > 0.0:
            self._draw_discharged(x, y, w, h, gone)

    def _draw_discharged(self, x, y, w, h, t):
        """What replaces the trace: a tick, and the word for what happened.

        Drawn from primitives like everything else here. The tick is LINES, not
        a glyph — this panel's fonts render a U+2713 as a tofu box, which is how
        the on-screen keyboard learned the same lesson.

        Two things the first offline render (PIL, before this ever reached the
        medic) showed were wrong, and both only show up as a PICTURE:

        * the tick came up while the trace was still bright and landed straight
          across the QRS spike — two green shapes crossing, illegible;
        * the word butted against the tick with no gap, so mid-fade it read as
          one smeared blob.

        So the tick is HELD BACK until the trace is mostly gone, and the tick
        and word are laid out as one group and centred together.
        """
        cy = y + h * 0.5
        s = min(h * 0.30, w * 0.06)             # tick half-size
        gap = s * 0.85

        # Hold back until the trace has largely faded. Below this the panel
        # belongs to the heartbeat and nothing else may share it.
        p = (t - 0.45) / 0.55
        if p <= 0.0:
            return
        p = min(1.0, p)

        tex = _discharge_texture()
        tw = tex.width if tex is not None else 0.0
        group = s * 2.0 + (gap + tw if tw else 0.0)
        left = x + (w - group) / 2.0
        tx = left + s                            # tick centre-ish

        # the tick draws itself on, left stroke first
        Color(_TRACE[0], _TRACE[1], _TRACE[2], p)
        start = (tx - s, cy + s * 0.05)
        knee = (tx - s * 0.35, cy - s * 0.55)
        if p <= 0.45:
            u = p / 0.45
            Line(points=[start[0], start[1],
                         start[0] + (knee[0] - start[0]) * u,
                         start[1] + (knee[1] - start[1]) * u],
                 width=dp(3), cap="round")
        else:
            u = (p - 0.45) / 0.55
            Line(points=[start[0], start[1], knee[0], knee[1],
                         knee[0] + (s * 1.35) * u, knee[1] + (s * 1.25) * u],
                 width=dp(3), cap="round")

        # the word only once the tick is complete, so they never blur together
        if tex is not None and p > 0.9:
            Color(1, 1, 1, (p - 0.9) / 0.1)
            Rectangle(texture=tex, pos=(tx + s + gap, cy - tex.height / 2.0),
                      size=(tex.width, tex.height))

    def _draw_smile(self, cx, cy, cw, ch):
        """The patient, pleased with the outcome.

        Drawn in the blank corner of the label to the right of the NODE MEDIC
        header (x 0.74-0.94, y 0.76-0.92 of the card): the window below is
        full of organs by now, and the first render put the eyes straight
        across the lettering.
        """
        ey = ch * 0.87
        er = min(cw, ch) * 0.030
        Color(0.15, 0.13, 0.10, 1)
        for dx in (0.79, 0.89):
            Ellipse(pos=(cx + cw * dx - er, cy + ey - er), size=(er * 2, er * 2))
        # a curved mouth, drawn as the lower arc of an ellipse
        mw, mh = cw * 0.12, ch * 0.08
        Line(ellipse=(cx + cw * 0.84 - mw / 2, cy + ch * 0.80 - mh / 2,
                      mw, mh, 100, 260), width=dp(1.8))
