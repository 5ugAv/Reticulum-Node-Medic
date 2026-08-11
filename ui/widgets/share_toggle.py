"""The map-sharing switch: left is hidden, right is shown roughly.

One control, two screens — the birth walkthrough and a node's own detail page —
so the answer means the same thing wherever the operator meets it.

WHY A SWITCH AND NOT TWO BUTTONS (operator, 2026-08-11: "that way it's clear
for the user to know where it sits"). Two buttons say what you may press. They
say nothing about what this node is currently going to do, so an operator
returning to the screen — or handing the medic to somebody else — has to read
both labels and infer. A switch has a POSITION, and a position is legible from
across a bench: a bright block on the left is a node telling nobody where it is.

THE SIDE IS THE MEANING, NOT JUST THE PICTURE. This one does not flip on a tap
the way the front page's Home/Backpack switch does; it selects the half that was
touched. A flip means any stray contact with the control can publish a position
that cannot be recalled, and the two ends of this switch are not equally
harmless. The left half is always the safe half, whatever it was showing.

Colour carries the same message twice over: grey for a node that says nothing,
the accent blue — the tool's "this is live" colour — for one that announces.
The captions still say it in words for anyone who wants them.

Nothing here decides anything. ``on_toggle(policy)`` hands the new position to
the screen, and the screen owns what happens next: at birth, a button below it
commits; on the node's page, the record is written and read back.
"""

from __future__ import annotations

from kivy.metrics import dp
from kivy.uix.label import Label

from monitor import location_share
from ui import theme
from ui.i18n import tr  # i18n: wrapped — the two end captions
from ui.widgets.mode_toggle import TwoStateToggle


def side_at(fraction: float) -> str:
    """Which end a touch *fraction* of the way across the switch is asking for.

    Pure, and the whole left/right convention lives in this one line: hidden on
    the left, shared on the right, as asked for.
    """
    return location_share.HIDDEN if fraction < 0.5 else location_share.APPROX


class ShareToggle(TwoStateToggle):
    """``on_toggle(policy)`` fires only when the position actually changes."""

    STATES = (location_share.HIDDEN, location_share.APPROX)

    def __init__(self, policy=None, on_toggle=None, hide_label: str = "",
                 share_label: str = "", **kwargs):
        kwargs.setdefault("size_hint_y", None)
        kwargs.setdefault("height", dp(60))
        super().__init__(state=policy, on_toggle=on_toggle, **kwargs)
        # The captions are arguments so the birth step can pass its own step
        # data (the same strings, from ui.birth_guide_flow, which is Kivy-free
        # and therefore testable in CI). Defaults are here so the node-detail
        # panel needs no step data to say the same thing.
        self._captions = (hide_label or tr("Keep it hidden"),
                          share_label or tr("Show it, roughly"))
        self._labels = []
        for text in self._captions:
            lbl = Label(text=text, font_size=theme.font_sp("16sp"),
                        halign="center", valign="middle")
            lbl.bind(size=lambda i, v: setattr(i, "text_size", v))
            self._labels.append(lbl)
            self.add_widget(lbl)
        self.bind(pos=self._redraw, size=self._redraw)
        self._redraw()

    # -- state --------------------------------------------------------------
    @classmethod
    def normalise(cls, policy):
        """The model's own rule, not a second one: anything unreadable is
        hidden, because an unreadable setting is not consent to publish."""
        return location_share.normalise(policy)

    @property
    def policy(self) -> str:
        return self.state

    # -- input --------------------------------------------------------------
    def target_state(self, touch):
        """The half that was touched — see the module note on why this is not a
        flip. A zero-width widget (before layout) reads as the left half."""
        span = self.width or 1.0
        return side_at((touch.x - self.x) / span)

    # -- drawing ------------------------------------------------------------
    def _redraw(self, *_):
        from kivy.graphics import Color, Line, RoundedRectangle
        self.canvas.before.clear()
        x, y, w, h = self.x, self.y, self.width, self.height
        pad = dp(4)
        half = (w - pad * 2) / 2.0
        shared = location_share.is_shared(self.state)
        knob_h = h - pad * 2
        knob_x = x + pad + (half if shared else 0.0)
        # Grey = this node says nothing; accent = it announces. Same pair of
        # colours the two buttons used, so the switch is not a new vocabulary.
        knob = theme.COLORS["accent"] if shared else theme.COLORS["text_secondary"]
        with self.canvas.before:
            # The track is DARKER than the surface behind it, so the knob reads
            # as sitting in a slot rather than floating on the panel.
            Color(*theme.hex_to_rgba(theme.COLORS["background"]))
            RoundedRectangle(pos=(x, y), size=(w, h), radius=[h / 2.0] * 4)
            Color(*theme.hex_to_rgba(theme.COLORS["surface"]))
            Line(rounded_rectangle=(x, y, w, h, h / 2.0), width=dp(1.4))
            Color(*theme.hex_to_rgba(knob))
            RoundedRectangle(pos=(knob_x, y + pad), size=(half, knob_h),
                             radius=[knob_h / 2.0] * 4)
        for i, lbl in enumerate(self._labels):
            lbl.pos = (x + pad + i * half, y + pad)
            lbl.size = (half, knob_h)
            on_it = (i == 1) == shared
            # Dark text on the lit half, quiet text on the unlit one — so the
            # position survives being read on a screen in daylight, where a
            # colour difference alone may not.
            lbl.bold = on_it
            lbl.color = theme.hex_to_rgba(
                theme.COLORS["background"] if on_it else theme.COLORS["text_secondary"])
