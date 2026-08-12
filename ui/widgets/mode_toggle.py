"""The tool's two-position switches: the base mechanism, and the front page's
Home / Backpack one.

``TwoStateToggle`` is the shared mechanism — two states, a safe end, and the
touch rules — and ``ui.widgets.share_toggle`` is its other user. It lives here
rather than in a module of its own because this is where it was learned; a
second switch is not a reason to move the first one.

A tap flips the medic's network role and calls ``on_toggle(new_mode)``. The icon
is a supplied illustration poster (a cottage with a rooftop RNode / a hiker
carrying a Node Medic), blitted onto a rounded dark plate. When the PNG asset is
missing (CI / no-asset environments) it falls back to on-canvas line-art — the
Pi's default font has no emoji glyphs, so a 🏠/🎒 would render as tofu. Green
house = stable infrastructure (routing + propagation on); amber backpack = mobile
leaf (transport off, safe to move); the accent survives in the label + plate
outline. While a switch runs, the label shows "…". Purely visual; the app wires
``on_toggle`` to workflows.node_mode.set_mode off the UI thread.
"""

from __future__ import annotations

import os

from kivy.metrics import dp
from kivy.uix.label import Label
from kivy.uix.widget import Widget

from ui import theme
from ui.i18n import tr  # i18n: wrapped — the "HOME" caption

HOME, BACKPACK = "home", "backpack"

#: Illustration posters (RGBA, background keyed to transparent). Optional — the
#: widget falls back to line-art when they're absent.
_ASSET_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "assets", "ui")
_MODE_PNG = {
    HOME: os.path.join(_ASSET_DIR, "mode_home.png"),
    BACKPACK: os.path.join(_ASSET_DIR, "mode_backpack.png"),
}
_tex_cache: dict = {}


def _mode_texture(mode: str):
    """Decode the mode's poster once. Returns a Kivy texture, or None when the
    asset is missing / can't be decoded (then the caller draws line-art)."""
    if mode not in _tex_cache:
        tex = None
        path = _MODE_PNG.get(mode)
        if path and os.path.exists(path):
            try:
                from kivy.core.image import Image as CoreImage
                tex = CoreImage(path).texture
            except Exception:
                tex = None
        _tex_cache[mode] = tex
    return _tex_cache[mode]


class TwoStateToggle(Widget):
    """Two states, one tap, and one end that is the safe one.

    Pulled out of ModeToggle when the birth flow's "keep it hidden / show it
    roughly" pair became a switch as well (operator, 2026-08-11). What the two
    share is not their picture — one is an illustrated poster, the other a
    sliding switch — it is the part that is easy to get subtly wrong: what an
    unreadable stored value means, what counts as a tap rather than a drag, and
    whether a tap that changes nothing still fires the callback.

    Subclasses name their pair in ``STATES`` as ``(left, right)`` and draw
    themselves in ``_redraw``. ``STATES[0]`` is the resting end, and every
    subclass here makes it the harmless one — which end that IS differs, so
    :meth:`normalise` is theirs to answer, not this class's to assume.
    """

    #: ``(left, right)``. Left rests.
    STATES: tuple = ()

    def __init__(self, state=None, on_toggle=None, **kwargs):
        super().__init__(**kwargs)
        self.state = self.normalise(state)
        self._on_toggle = on_toggle
        self._busy = False
        # dp() reads the display, so it is measured ONCE here rather than on
        # every touch — and holding it on the instance is also what lets the
        # touch rules be tested on a machine with no window at all.
        self._tap_slop = dp(14)

    # -- state --------------------------------------------------------------
    @classmethod
    def normalise(cls, state):
        """Any stored/legacy value -> one of ``STATES``. Subclasses override to
        say which end an unreadable value falls to."""
        return state if state in cls.STATES else cls.STATES[0]

    def set_state(self, state):
        self.state = self.normalise(state)
        self._busy = False
        self._redraw()

    def set_busy(self, on: bool = True):
        self._busy = on
        self._redraw()

    def _redraw(self, *_):
        """Subclass responsibility."""

    # -- input --------------------------------------------------------------
    def target_state(self, touch):
        """The state this touch is asking for. Flipping is the default; a
        subclass whose two ends are far apart on screen picks by position."""
        return self.STATES[1] if self.state == self.STATES[0] else self.STATES[0]

    def touch_target(self, touch):
        """The state to move to, or None when the touch asks for nothing.

        Separate from :meth:`on_touch_up` so the rules can be run without a
        window (tests/test_share_toggle.py) — and because all three of them are
        about not acting: mid-switch, mid-drag, or on the end it already sits on.
        A tap that changes nothing must not re-fire, because on the node-detail
        panel the callback rewrites the record and clears the "written to the
        node" stamp.
        """
        if self._busy or not self.collide_point(*touch.pos):
            return None
        if abs(touch.x - touch.ox) + abs(touch.y - touch.oy) >= self._tap_slop:
            return None                       # a drag/scroll, not an answer
        target = self.target_state(touch)
        return None if target == self.state else target

    def on_touch_up(self, touch):
        target = self.touch_target(touch)
        if target is not None:
            if self._on_toggle:
                self._on_toggle(target)
            return True
        return super().on_touch_up(touch)


class ModeToggle(TwoStateToggle):
    STATES = (HOME, BACKPACK)

    def __init__(self, mode: str = HOME, on_toggle=None, **kwargs):
        super().__init__(state=mode, on_toggle=on_toggle, **kwargs)
        self.size_hint = (None, None)
        # Enlarged from 76x84 so the detailed illustration reads at icon size. Sits
        # at pos_hint right:0.85 on the home screen; the gear is at right:0.98, so
        # there's still ~100px of clearance between them at 1280 wide.
        self.size = (dp(90), dp(100))
        self.label = Label(text="", font_size="12sp", bold=True,
                           halign="center", valign="middle")
        self.label.bind(size=lambda i, v: setattr(i, "text_size", v))
        self.add_widget(self.label)
        self.bind(pos=self._redraw, size=self._redraw)
        self._redraw()

    # -- state --------------------------------------------------------------
    @classmethod
    def normalise(cls, mode):
        """Anything that is not exactly HOME is BACKPACK.

        Kept as it always was, and it is not the base class's default: a mode
        this tool cannot read must land on the end that disturbs nothing, and
        here that is the mobile leaf. ``set_mode`` returns an empty mode when a
        switch fails, and that has to settle as backpack.
        """
        return HOME if mode == HOME else BACKPACK

    #: ``mode`` is what the app and the home screen call this switch's state.
    mode = property(lambda self: self.state,
                    lambda self, value: setattr(self, "state",
                                                self.normalise(value)))

    # -- drawing ------------------------------------------------------------
    def _color(self):
        return theme.COLORS["green"] if self.mode == HOME else theme.COLORS["amber"]

    def _redraw(self, *_):
        from kivy.graphics import Color, Line, Rectangle, RoundedRectangle
        self.canvas.before.clear()
        x, y, w, h = self.x, self.y, self.width, self.height
        s = min(w, h * 0.72)                          # icon box side
        ox, oy = x + (w - s) / 2.0, y + h - s - dp(2)  # icon sits up top
        col = theme.hex_to_rgba(self._color())
        tex = _mode_texture(self.mode)
        with self.canvas.before:
            # a soft rounded plate behind so it reads as a button on the poster
            Color(*theme.hex_to_rgba(theme.COLORS["background"], 0.55))
            RoundedRectangle(pos=(x, y), size=(w, h), radius=[dp(12)] * 4)
            if tex is not None:
                tw, th = tex.size
                Color(1, 1, 1, 1)                     # no tint — true poster colours
                if self.mode == BACKPACK:
                    # Fill the whole button top-to-bottom so the hiker reads at a
                    # glance; the (black) label sits over his legs at the bottom.
                    dh = h
                    dw = tw * (h / th) if th else w
                    Rectangle(texture=tex, pos=(x + (w - dw) / 2.0, y),
                              size=(dw, dh))
                else:
                    # HOME: illustration up top in the icon box, label strip below.
                    scale = min(s / tw, s / th) if tw and th else 1.0
                    dw, dh = tw * scale, th * scale
                    Rectangle(texture=tex, pos=(ox + (s - dw) / 2.0,
                                                oy + (s - dh) / 2.0), size=(dw, dh))
            else:
                Color(*col)                           # line-art fallback (no asset)
                if self.mode == HOME:
                    self._draw_house(Line, ox, oy, s)
                else:
                    self._draw_backpack(Line, ox, oy, s)
            # accent outline round the plate keeps the green/amber cue with a poster
            Color(*col)
            Line(rounded_rectangle=(x, y, w, h, dp(12)), width=dp(1.2))
        self.label.pos = (x, y + dp(2))
        self.label.size = (w, dp(20))
        # The hiker illustration is self-explanatory, so BACKPACK carries no caption;
        # HOME keeps its green label in the strip below the cottage. Either mode
        # still shows "…" mid-switch so the tap has feedback.
        self.label.color = (0, 0, 0, 1) if self.mode == BACKPACK else col
        if self._busy:
            self.label.text = "…"
        else:
            self.label.text = tr("HOME") if self.mode == HOME else ""

    @staticmethod
    def _draw_house(Line, ox, oy, s):
        Line(points=[ox + 0.08 * s, oy + 0.48 * s, ox + 0.5 * s, oy + 0.92 * s,
                     ox + 0.92 * s, oy + 0.48 * s], width=dp(2))          # roof
        Line(rectangle=(ox + 0.18 * s, oy + 0.06 * s, 0.64 * s, 0.44 * s),
             width=dp(2))                                                 # body
        Line(rectangle=(ox + 0.4 * s, oy + 0.06 * s, 0.2 * s, 0.24 * s),
             width=dp(1.4))                                               # door

    @staticmethod
    def _draw_backpack(Line, ox, oy, s):
        Line(rounded_rectangle=(ox + 0.2 * s, oy + 0.04 * s, 0.6 * s, 0.72 * s,
                                dp(7)), width=dp(2))                      # body
        Line(rounded_rectangle=(ox + 0.3 * s, oy + 0.52 * s, 0.4 * s, 0.32 * s,
                                dp(6)), width=dp(2))                      # lid/flap
        Line(rectangle=(ox + 0.36 * s, oy + 0.14 * s, 0.28 * s, 0.22 * s),
             width=dp(1.4))                                              # front pocket

    # Input is the base class's: this switch FLIPS on a tap anywhere on it,
    # which is TwoStateToggle.target_state's default. It can, because neither of
    # its ends publishes anything about the physical world.
