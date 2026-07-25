"""Home / Backpack mode toggle for the front page.

A tap flips the medic's network role and calls ``on_toggle(new_mode)``. Line-art
icons (a house / a backpack) are drawn on canvas — the Pi's default font has no
emoji glyphs, so a 🏠/🎒 would render as tofu. Green house = stable infrastructure
(routing + propagation on); amber backpack = mobile leaf (transport off, safe to
move). While a switch runs, the label shows "…". Purely visual; the app wires
``on_toggle`` to workflows.node_mode.set_mode off the UI thread.
"""

from __future__ import annotations

from kivy.metrics import dp
from kivy.uix.label import Label
from kivy.uix.widget import Widget

from ui import theme

HOME, BACKPACK = "home", "backpack"


class ModeToggle(Widget):
    def __init__(self, mode: str = HOME, on_toggle=None, **kwargs):
        super().__init__(**kwargs)
        self.mode = HOME if mode == HOME else BACKPACK
        self._on_toggle = on_toggle
        self._busy = False
        self.size_hint = (None, None)
        self.size = (dp(76), dp(84))
        self.label = Label(text="", font_size="12sp", bold=True,
                           halign="center", valign="middle")
        self.label.bind(size=lambda i, v: setattr(i, "text_size", v))
        self.add_widget(self.label)
        self.bind(pos=self._redraw, size=self._redraw)
        self._redraw()

    # -- public -------------------------------------------------------------
    def set_state(self, mode: str):
        self.mode = HOME if mode == HOME else BACKPACK
        self._busy = False
        self._redraw()

    def set_busy(self, on: bool = True):
        self._busy = on
        self._redraw()

    # -- drawing ------------------------------------------------------------
    def _color(self):
        return theme.COLORS["green"] if self.mode == HOME else theme.COLORS["amber"]

    def _redraw(self, *_):
        from kivy.graphics import Color, Line, RoundedRectangle
        self.canvas.before.clear()
        x, y, w, h = self.x, self.y, self.width, self.height
        s = min(w, h * 0.72)                          # icon box side
        ox, oy = x + (w - s) / 2.0, y + h - s - dp(2)  # icon sits up top
        col = theme.hex_to_rgba(self._color())
        with self.canvas.before:
            # a soft rounded plate behind so it reads as a button on the poster
            Color(*theme.hex_to_rgba(theme.COLORS["background"], 0.55))
            RoundedRectangle(pos=(x, y), size=(w, h), radius=[dp(12)] * 4)
            Color(*col)
            if self.mode == HOME:
                self._draw_house(Line, ox, oy, s)
            else:
                self._draw_backpack(Line, ox, oy, s)
        self.label.pos = (x, y + dp(2))
        self.label.size = (w, dp(20))
        self.label.color = col
        self.label.text = "…" if self._busy else ("HOME" if self.mode == HOME
                                                   else "BACKPACK")

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

    # -- input --------------------------------------------------------------
    def on_touch_up(self, touch):
        if (not self._busy and self.collide_point(*touch.pos)
                and abs(touch.x - touch.ox) + abs(touch.y - touch.oy) < dp(14)):
            other = BACKPACK if self.mode == HOME else HOME
            if self._on_toggle:
                self._on_toggle(other)
            return True
        return super().on_touch_up(touch)
