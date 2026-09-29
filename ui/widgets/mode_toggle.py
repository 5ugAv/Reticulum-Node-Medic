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


class ModeIcon(Widget):
    """ONE mode's illustration, shown whether or not it is the chosen one.

    Operator, 2026-09-29, with a sketch: both pictures on the front page — the
    cottage to the left of the gear, the hiker below it — "and when one is
    pressed, it will go into colour and the other one will go grey and vice
    versa. So the coloured one will show that it's selected through colour and
    the non-selected one will be greyed out."

    That is a different idea from the switch it replaces. ``ModeToggle`` showed
    only the CURRENT mode, so the picture was the state and the other mode was
    invisible — you had to know that tapping a cottage would give you a hiker.
    Two pictures, one lit, says the same thing without the knowledge.

    No caption and no outline. The operator asked for "just the existing
    pictures", and the orange ring the old switch drew round the plate was the
    last non-green thing on the poster.
    """

    #: How the unchosen mode is drawn: dimmed toward grey rather than hidden.
    #: Kivy multiplies the texture by this colour, so equal channels desaturate
    #: it as they darken it — which is what "greyed out" means here.
    DIM = (0.42, 0.42, 0.42, 0.62)

    def __init__(self, mode: str, selected: bool = False, on_select=None, **kwargs):
        super().__init__(**kwargs)
        self.mode = ModeToggle.normalise(mode)
        self._selected = bool(selected)
        self._on_select = on_select
        self._busy = False
        self.bind(pos=self._redraw, size=self._redraw)
        self._redraw()

    # -- state --------------------------------------------------------------
    @property
    def selected(self) -> bool:
        return self._selected

    def set_selected(self, on: bool) -> None:
        on = bool(on)
        if on != self._selected:
            self._selected = on
            self._redraw()

    def set_busy(self, busy: bool) -> None:
        """Mid-switch. Both icons dim, so a tap is never left looking ignored
        while rnsd restarts."""
        busy = bool(busy)
        if busy != self._busy:
            self._busy = busy
            self._redraw()

    # -- drawing ------------------------------------------------------------
    def _redraw(self, *_):
        from kivy.graphics import Color, Line, Rectangle
        self.canvas.before.clear()
        x, y, w, h = self.x, self.y, self.width, self.height
        tex = _mode_texture(self.mode)
        lit = self._selected and not self._busy
        with self.canvas.before:
            if tex is not None:
                tw, th = tex.size
                scale = min(w / tw, h / th) if tw and th else 1.0
                dw, dh = tw * scale, th * scale
                Color(1, 1, 1, 1) if lit else Color(*self.DIM)
                Rectangle(texture=tex, pos=(x + (w - dw) / 2.0, y + (h - dh) / 2.0),
                          size=(dw, dh))
            else:
                # no asset (CI): line-art, in the palette rather than the old
                # green/amber pair — the colour cue is now lit-vs-dim, not hue.
                col = theme.hex_to_rgba(
                    theme.COLORS["accent" if lit else "text_secondary"])
                Color(*col)
                s = min(w, h)
                ox, oy = x + (w - s) / 2.0, y + (h - s) / 2.0
                if self.mode == HOME:
                    ModeToggle._draw_house(Line, ox, oy, s)
                else:
                    ModeToggle._draw_backpack(Line, ox, oy, s)

    # -- touch --------------------------------------------------------------
    def on_touch_down(self, touch):
        if self.collide_point(*touch.pos) and not self._busy:
            if self._on_select and not self._selected:
                self._on_select(self.mode)
            return True
        return super().on_touch_down(touch)


class ModePair:
    """The two ModeIcons as one switch, so the app keeps its old handle.

    ui/app.py drives the front page's mode control through ``mode_toggle`` —
    ``.mode``, ``.set_state()``, ``.set_busy()`` — from three places, including
    the movement detector that flips the medic to Backpack on its own. Splitting
    one widget into two pictures must not require any of that to change, so the
    pair answers to the same four calls and forwards them.

    Not a Widget: the two icons sit at different corners of the screen, and a
    container spanning both would be an invisible box over the globe swallowing
    taps meant for the poster.
    """

    def __init__(self, home_icon, backpack_icon):
        self.icons = {HOME: home_icon, BACKPACK: backpack_icon}

    @property
    def mode(self):
        return HOME if self.icons[HOME].selected else BACKPACK

    def set_state(self, mode):
        mode = ModeToggle.normalise(mode)
        for key, icon in self.icons.items():
            icon.set_selected(key == mode)
            icon.set_busy(False)

    #: ``ModeToggle`` spelled it both ways; keep both so no caller has to care.
    set_mode = set_state

    def set_busy(self, busy):
        for icon in self.icons.values():
            icon.set_busy(busy)

    def collide_point(self, *pos):
        """True over EITHER picture — the home screen uses this to keep corner
        controls out of the poster's tap-map."""
        return any(i.collide_point(*pos) for i in self.icons.values())


class ModeSwapArrow(Widget):
    """A small curved arrow, headed at both ends, drawn between the two mode
    pictures (operator, 2026-09-29 — and it is on their sketch too, looping
    from the cottage to the hiker and back).

    It says the one thing two lit-or-grey pictures cannot: that these are the
    SAME control, and pressing either swaps you to it. Without it they read as
    two unrelated buttons that happen to sit near each other.

    Purely decorative — no touch handling, so a tap here falls through to the
    poster underneath rather than being quietly eaten by a decoration.
    """

    #: How far the curve bows away from the straight line between the two
    #: pictures, as a fraction of the box. A straight line between two icons
    #: reads as a join; a bowed one reads as a movement.
    #: Negative bows the curve the OTHER way — toward the globe rather than
    #: away from it (operator, 2026-09-29, looking at it on the glass).
    BOW = -0.42
    HEAD = 0.30          # arrowhead length, as a fraction of the box
    #: Half brightness: at full accent it competed with the two pictures it is
    #: only there to relate (operator, same look).
    ALPHA = 0.42

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.bind(pos=self._redraw, size=self._redraw)
        self._redraw()

    def _redraw(self, *_):
        import math
        from kivy.graphics import Color, Line
        self.canvas.before.clear()
        x, y, w, h = self.x, self.y, self.width, self.height
        if w < 4 or h < 4:
            return
        # The cottage sits up-left of this box and the hiker down-right, so the
        # arrow runs corner to corner and bows away from the globe.
        ax, ay = x + w * 0.10, y + h * 0.90          # toward the cottage
        bx, by = x + w * 0.90, y + h * 0.10          # toward the hiker
        cx = x + w * (0.5 + self.BOW)                 # control point, bowed right
        cy = y + h * (0.5 + self.BOW)
        head = min(w, h) * self.HEAD
        with self.canvas.before:
            Color(*theme.hex_to_rgba(theme.COLORS["accent"], self.ALPHA))
            Line(bezier=[ax, ay, cx, cy, bx, by], width=dp(1.6))
            # A head at each end, angled off the curve's tangent there — which
            # at a quadratic Bezier's ends is simply the line to the control
            # point, so the heads sit on the curve instead of beside it.
            for (px, py, qx, qy) in ((ax, ay, cx, cy), (bx, by, cx, cy)):
                ang = math.atan2(py - qy, px - qx)
                for spread in (+2.6, -2.6):
                    Line(points=[px, py,
                                 px + head * math.cos(ang + spread),
                                 py + head * math.sin(ang + spread)],
                         width=dp(1.6))
