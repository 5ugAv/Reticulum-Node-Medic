"""The slot switches: a shared drawing, the map-sharing switch, and on/off.

One mechanism, several answers. ``SlotToggle`` is the captioned two-half
switch — a knob resting in a slot, the half you touch is the half you chose —
and its two users are the map-sharing answer (``ShareToggle``) and the plain
on/off question (``OnOffToggle``, first used for Bluetooth at birth). The
touch rules live in ``TwoStateToggle`` (ui.widgets.mode_toggle), where they
were learned.

WHY A SWITCH AND NOT TWO BUTTONS (operator, 2026-08-11: "that way it's clear
for the user to know where it sits"). Two buttons say what you may press. They
say nothing about what this node is currently going to do, so an operator
returning to the screen — or handing the medic to somebody else — has to read
both labels and infer. A switch has a POSITION, and a position is legible from
across a bench: a bright block on the left is a node telling nobody where it is.

THE SIDE IS THE MEANING, NOT JUST THE PICTURE. These do not flip on a tap the
way the front page's Home/Backpack switch does; they select the half that was
touched. A flip means any stray contact with the control can move the answer,
and the two ends of these switches are not equally consequential. The left
half is always the quiet half, whatever it was showing.

Colour carries the same message twice over: grey for the quiet end, the accent
blue — the tool's "this is live" colour — for the end that emits (a position,
a radio). The captions still say it in words for anyone who wants them — in
FULL-STRENGTH text on both halves: the unlit caption was once text_secondary
on the dark track and vanished on the glass, so the switch read as one grey
button and the operator reported the toggle missing (2026-08-12).

Nothing here decides anything. ``on_toggle(state)`` hands the new position to
the screen, and the screen owns what happens next: at birth, a button below it
commits; on the node's page, the record is written and read back.
"""

from __future__ import annotations

from kivy.metrics import dp
from kivy.uix.label import Label

from monitor import location_share
from ui import theme
from ui.i18n import tr  # i18n: wrapped — the end captions
from ui.widgets.mode_toggle import TwoStateToggle


def side_at(fraction: float) -> str:
    """Which end a touch *fraction* of the way across the share switch asks
    for. Pure, and the whole left/right convention lives in this one line:
    hidden on the left, shared on the right, as asked for."""
    return location_share.HIDDEN if fraction < 0.5 else location_share.APPROX


class SlotToggle(TwoStateToggle):
    """The captioned two-half switch: ``STATES[0]`` rests on the left and is
    the quiet end; ``STATES[1]`` is lit in the accent colour when chosen.

    Subclasses name their pair in ``STATES`` and their captions via
    ``captions``; everything else — geometry, colours, the select-not-flip
    touch — is this class's, so a third slot answer costs a class body, not a
    second drawing that drifts.
    """

    def __init__(self, state=None, on_toggle=None,
                 captions: "tuple[str, str]" = ("", ""), **kwargs):
        kwargs.setdefault("size_hint_y", None)
        kwargs.setdefault("height", dp(60))
        super().__init__(state=state, on_toggle=on_toggle, **kwargs)
        self._captions = captions
        self._labels = []
        for text in self._captions:
            lbl = Label(text=text, font_size=theme.font_sp("16sp"),
                        halign="center", valign="middle")
            lbl.bind(size=lambda i, v: setattr(i, "text_size", v))
            self._labels.append(lbl)
            self.add_widget(lbl)
        self.bind(pos=self._redraw, size=self._redraw)
        self._redraw()

    # -- input --------------------------------------------------------------
    # target_state is each subclass's, as a call into a PURE side rule — so
    # the select-not-flip decision stays reachable with a stub self and no
    # window (tests/test_share_toggle.py's established contract).

    # -- drawing ------------------------------------------------------------
    def _redraw(self, *_):
        from kivy.graphics import Color, Line, RoundedRectangle
        self.canvas.before.clear()
        x, y, w, h = self.x, self.y, self.width, self.height
        pad = dp(4)
        half = (w - pad * 2) / 2.0
        lit = self.state == self.STATES[1]
        knob_h = h - pad * 2
        knob_x = x + pad + (half if lit else 0.0)
        # Grey = the quiet end; accent = the end that emits. Same pair of
        # colours the two buttons used, so the switch is not a new vocabulary.
        knob = theme.COLORS["accent"] if lit else theme.COLORS["text_secondary"]
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
            on_it = (i == 1) == lit
            # Dark text on the lit half, FULL-STRENGTH text on the unlit one.
            # It was text_secondary — dim grey on the dark track — and on the
            # bench the unlit half disappeared entirely: the switch read as
            # one grey button saying "Keep it hidden", and the operator
            # reported the toggle missing (2026-08-12). A switch whose other
            # end cannot be seen is two buttons again, minus one.
            lbl.bold = on_it
            lbl.color = theme.hex_to_rgba(
                theme.COLORS["background"] if on_it
                else theme.COLORS["text_primary"])


class ShareToggle(SlotToggle):
    """The map-sharing switch: left is hidden, right is shown roughly.

    One control, two screens — the birth walkthrough and a node's own detail
    page — so the answer means the same thing wherever the operator meets it.
    ``on_toggle(policy)`` fires only when the position actually changes.
    """

    STATES = (location_share.HIDDEN, location_share.APPROX)

    def __init__(self, policy=None, on_toggle=None, hide_label: str = "",
                 share_label: str = "", **kwargs):
        # The captions are arguments so the birth step can pass its own step
        # data (the same strings, from ui.birth_guide_flow, which is Kivy-free
        # and therefore testable in CI). Defaults are here so the node-detail
        # panel needs no step data to say the same thing.
        super().__init__(state=policy, on_toggle=on_toggle,
                         captions=(hide_label or tr("Hidden"),
                                   share_label or tr("Show on map")),
                         **kwargs)

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
        """The half that was touched — a flip would mean any stray contact
        can publish a position that cannot be recalled. A zero-width widget
        (before layout) reads as the left half."""
        span = self.width or 1.0
        return side_at((touch.x - self.x) / span)


class OnOffToggle(SlotToggle):
    """A plain on/off answer as a slot switch: left is OFF and left rests.

    First used for the Bluetooth question at birth (operator, 2026-08-12):
    the quiet end is the one that emits nothing and draws nothing, so it is
    the resting end — same convention as the share switch, learned once.
    """

    OFF, ON = "off", "on"
    STATES = (OFF, ON)

    def __init__(self, state=None, on_toggle=None, off_label: str = "",
                 on_label: str = "", **kwargs):
        super().__init__(state=state, on_toggle=on_toggle,
                         captions=(off_label or tr("Off"),
                                   on_label or tr("On")),
                         **kwargs)

    @classmethod
    def normalise(cls, state):
        """Anything that is not exactly "on" is off — an unreadable stored
        value must land on the end that emits and drains nothing."""
        if state is True:
            return cls.ON
        return state if state == cls.ON else cls.OFF

    @property
    def on(self) -> bool:
        return self.state == self.ON

    # -- input --------------------------------------------------------------
    def target_state(self, touch):
        """The half that was touched; left is off. Same select-not-flip rule
        as the share switch, same stub-testable shape."""
        span = self.width or 1.0
        return "off" if (touch.x - self.x) / span < 0.5 else "on"
