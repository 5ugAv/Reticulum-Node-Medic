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

#: The DO-THIS-NOW variant. Same red outline so it still reads as "this one
#: matters", green fill so it does not read as the same warning the operator has
#: just spent four minutes obeying. Introduced because the imager left the
#: yellow "don't unplug anything" box on screen while the step underneath it
#: said to unplug the Pi — two instructions in direct contradiction, with the
#: louder one wrong (operator, 2026-08-06).
FILL_ACT = (0.22, 0.78, 0.35, 1)      # green
INK_ACT = (0.03, 0.14, 0.05, 1)       # near-black, for contrast on green


class Callout(BoxLayout):
    """A heading and body in a yellow box with a red border.

    Height follows its content, so a longer body cannot spill out of the box —
    the failure that makes a boxed warning look broken.
    """

    def __init__(self, heading: str, body: str = "", act: bool = False,
                 pulse: bool = False, **kwargs):
        """*act* switches to the green DO-THIS-NOW fill. Use it for the step the
        operator must perform, and keep yellow for the thing they must NOT do —
        the two must never be the same colour on the same screen.

        *pulse* breathes the HEADING (opacity, ~1.4 s cycle) to catch the eye
        mid-flow — asked for on the imager's write-these-down box (operator,
        2026-08-14). The body stays still: the pulse is a wave, not a strobe,
        and the words being read must not move.
        """
        fill, ink = (FILL_ACT, INK_ACT) if act else (FILL, INK)
        super().__init__(**kwargs)
        self.orientation = "vertical"
        self.size_hint_y = None
        self.padding = (dp(12), dp(10), dp(12), dp(10))
        self.spacing = dp(4)

        # Big. This is the line that has to stop someone mid-flow — on the 5"
        # panel at 15.5sp it read as just another bold line and the operator
        # watched it float past (2026-08-02).
        self._head = Label(text=heading, bold=True, font_size="23sp",
                           color=ink, size_hint_y=None, halign="left",
                           valign="middle", markup=False)
        self._body = Label(text=body, font_size="15sp", color=ink,
                           size_hint_y=None, halign="left", valign="top")
        for lbl in (self._head, self._body):
            # Wrap to the box's width, not the screen's, or long lines run out
            # past the red border.
            lbl.bind(width=lambda w, val: setattr(w, "text_size", (val, None)),
                     texture_size=lambda w, ts: setattr(w, "height", ts[1]))
            self.add_widget(lbl)
        if not body:
            self.remove_widget(self._body)
        if pulse:
            from kivy.animation import Animation
            anim = (Animation(opacity=0.35, duration=0.7)
                    + Animation(opacity=1.0, duration=0.7))
            anim.repeat = True
            anim.start(self._head)
            # A repeating animation holds its widget forever; let it go the
            # moment this box leaves the tree (screens rebuild their widgets
            # on every render, so a detached box never comes back).
            self.bind(parent=lambda _w, par: par is None
                      and anim.cancel(self._head))

        with self.canvas.before:
            Color(*fill)
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
