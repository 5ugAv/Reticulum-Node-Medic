"""The nine-dot pad the operator draws to unlock the vault.

Geometry lives in ``ui.pattern_geometry`` so the part that decides which dot a
finger is on can be tested without a display — see the tests there for why the
hit target is sized the way it is.

DRAWN, NOT TAPPED. One touch-down, drag through the dots, lift. That is the
gesture people already know, and it is the one that works with cold hands and
gloves off in a field.

NO FEEDBACK ABOUT CORRECTNESS while drawing. The dots light as they are
collected — that is confirmation the pad SAW the finger, not that the pattern is
right. Anything that reacted to a wrong pattern early would let someone guess
the first dots cheaply.
"""

from __future__ import annotations

from kivy.metrics import dp
from kivy.uix.widget import Widget

from ui import theme
from ui.pattern_geometry import dot_at, dot_centres, extend


class PatternPad(Widget):
    """A 3x3 pad. ``on_complete(path)`` fires on lift with the dots in order.

    The path is a list of ints 0..8. A path shorter than the minimum is still
    reported — the CALLER decides what is too short, so the rule lives in one
    place (``provisioning.vault_factors.encode_pattern``) instead of being
    duplicated in a widget where it would drift.
    """

    def __init__(self, on_complete=None, **kwargs):
        kwargs.setdefault("size_hint", (1, None))
        kwargs.setdefault("height", dp(260))
        super().__init__(**kwargs)
        self._on_complete = on_complete
        self._path = []
        self._drawing = False
        self.bind(pos=lambda *_: self._redraw(), size=lambda *_: self._redraw())
        self._redraw()

    # -- state -------------------------------------------------------------

    @property
    def path(self):
        return list(self._path)

    def clear(self):
        """Forget the drawn pattern (after a failed unlock, or on cancel)."""
        self._path = []
        self._drawing = False
        self._redraw()

    # -- touch -------------------------------------------------------------

    def on_touch_down(self, touch):
        if not self.collide_point(*touch.pos):
            return super().on_touch_down(touch)
        self._path = []
        self._drawing = True
        self._collect(touch.pos)
        return True

    def on_touch_move(self, touch):
        if not self._drawing:
            return super().on_touch_move(touch)
        self._collect(touch.pos)
        return True

    def on_touch_up(self, touch):
        if not self._drawing:
            return super().on_touch_up(touch)
        self._drawing = False
        self._redraw()
        if self._on_complete:
            self._on_complete(list(self._path))
        return True

    def _collect(self, pos):
        dot = dot_at(pos[0], pos[1], self.x, self.y, self.width, self.height)
        new = extend(self._path, dot)
        if new != self._path:
            self._path = new
            self._redraw()

    # -- drawing -----------------------------------------------------------

    def _redraw(self):
        from kivy.graphics import Color, Ellipse, Line

        self.canvas.clear()
        centres = dot_centres(self.x, self.y, self.width, self.height)
        r_idle = dp(9)
        r_used = dp(15)
        with self.canvas:
            # The strokes first, so the dots sit on top of them.
            if len(self._path) > 1:
                pts = []
                for i in self._path:
                    pts.extend(centres[i])
                Color(*theme.hex_to_rgba(theme.COLORS["accent"]))
                Line(points=pts, width=dp(3), joint="round", cap="round")
            for i, (cx, cy) in enumerate(centres):
                used = i in self._path
                Color(*theme.hex_to_rgba(
                    theme.COLORS["accent" if used else "text_secondary"]))
                r = r_used if used else r_idle
                Ellipse(pos=(cx - r, cy - r), size=(r * 2, r * 2))
