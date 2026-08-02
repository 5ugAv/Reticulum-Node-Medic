"""A boxed warning — yellow fill, red outline, dark text.

For the small number of things the operator must ACT on before moving past a
screen, as distinct from the many things a screen merely tells them. Coloured
text alone is not enough: on the 5" panel in daylight an amber heading reads as
just another line in a column of coloured lines, and the one line that had to be
obeyed looked exactly like the ones that didn't (operator, 2026-08-02).

The box is deliberately not part of the normal palette. Dark text on yellow is
the highest-contrast pairing available here, and it is the only place in the app
that inverts the dark theme — which is the point.
"""

from __future__ import annotations

from kivy.metrics import dp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.label import Label
from kivy.graphics import Color, Rectangle, Line

#: Fill and outline. Kept local rather than added to the theme: these are the
#: colours of a warning label, not of the app.
FILL = (0.99, 0.85, 0.20, 1)          # yellow
OUTLINE = (0.84, 0.00, 0.00, 1)       # red
INK = (0.10, 0.08, 0.00, 1)           # near-black, for contrast on yellow


class Callout(BoxLayout):
    """A heading and body in a yellow box with a red border.

    Height follows its content, so a longer body cannot spill out of the box —
    the failure that makes a boxed warning look broken.
    """

    def __init__(self, heading: str, body: str = "", **kwargs):
        super().__init__(**kwargs)
        self.orientation = "vertical"
        self.size_hint_y = None
        self.padding = (dp(12), dp(10), dp(12), dp(10))
        self.spacing = dp(4)

        self._head = Label(text=heading, bold=True, font_size="15.5sp",
                           color=INK, size_hint_y=None, halign="left",
                           valign="middle", markup=False)
        self._body = Label(text=body, font_size="13.5sp", color=INK,
                           size_hint_y=None, halign="left", valign="top")
        for lbl in (self._head, self._body):
            # Wrap to the box's width, not the screen's, or long lines run out
            # past the red border.
            lbl.bind(width=lambda w, val: setattr(w, "text_size", (val, None)),
                     texture_size=lambda w, ts: setattr(w, "height", ts[1]))
            self.add_widget(lbl)
        if not body:
            self.remove_widget(self._body)

        with self.canvas.before:
            Color(*FILL)
            self._bg = Rectangle(pos=self.pos, size=self.size)
            Color(*OUTLINE)
            self._border = Line(rectangle=(0, 0, 0, 0), width=dp(1.6))
        self.bind(pos=self._redraw, size=self._redraw,
                  minimum_height=self._fit)
        self._fit()

    def _fit(self, *_a):
        self.height = max(self.minimum_height, dp(48))

    def _redraw(self, *_a):
        self._bg.pos = self.pos
        self._bg.size = self.size
        self._border.rectangle = (self.x, self.y, self.width, self.height)
