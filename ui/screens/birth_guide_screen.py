"""Guided birth — one instruction per screen, for a first-time operator.

Instead of one dense form, BIRTH can be walked through step by step: pick what
you're building, then follow a screen per action (plug the board in, insert the
SD card, …) with a simple animation showing the motion. The physical-prep steps
live here; once the hardware is connected the guide hands off to the existing
BIRTH screen (``on_complete``) which does the detect / name / flash work.

The step LISTS are pure data (``guide_steps``) so the ordering is unit-testable
without Kivy; the screen is just the presentation over them.
"""

from __future__ import annotations

from kivy.metrics import dp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label

from ui import theme
from ui.widgets.wizard_step import WizardStep
from ui.widgets.birth_anims import ConnectBoardAnim, InsertSdAnim

#: What the operator can build — the intro chooser. key -> (title, subtitle).
BIRTH_PATHS = [
    ("radio", "A standalone radio node",
     "An RTNode-2400 or RNode board on its own — reports health, remotely repairable."),
    ("pi", "A Raspberry Pi + radio",
     "A Pi running Reticulum with an attached radio (a propagation / host node)."),
    ("host", "A radio for a computer",
     "Just flash a radio (RNode) to plug into a computer you've already set up."),
]

#: Ordered guided steps per path. Each step: title, body, optional anim factory,
#: optional hint. ``anim`` is a zero-arg callable so a fresh widget is built per
#: visit. The last step's Next hands off to the real BIRTH flow.
_STEPS = {
    "radio": [
        {"title": "Connect your radio board",
         "body": "Plug the radio board into Node Medic with a USB cable. Node Medic "
                 "powers it and will detect it automatically.",
         "hint": "Use a DATA USB cable — a charge-only cable won't be seen.",
         "anim": ConnectBoardAnim},
        {"title": "Let's set it up",
         "body": "Node Medic will now detect the board, then walk you through naming "
                 "it and flashing the firmware.",
         "anim": None, "next": "Start setup  →"},
    ],
    "pi": [
        {"title": "Insert the Pi's SD card",
         "body": "Put the Raspberry Pi's SD card into Node Medic's card reader so we "
                 "can write its operating system.",
         "anim": InsertSdAnim},
        {"title": "Image the Pi",
         "body": "Next we'll write Raspberry Pi OS to the card and set its name, "
                 "Wi-Fi and password — a few details at a time.",
         "hint": "SD imaging on the medic is coming — for now, image the card with "
                 "Raspberry Pi Imager, then continue.",
         "anim": None},
        {"title": "Connect the radio board",
         "body": "Put the SD card into the Pi and power it on, then plug the radio "
                 "board into Node Medic with a USB cable.",
         "anim": ConnectBoardAnim},
        {"title": "Let's set it up",
         "body": "Node Medic will now provision the Pi and its radio, then walk you "
                 "through naming it.",
         "anim": None, "next": "Start setup  →"},
    ],
    "host": [
        {"title": "Connect the radio board",
         "body": "Plug the radio board into Node Medic with a USB cable so it can be "
                 "flashed as an RNode.",
         "hint": "Use a DATA USB cable — a charge-only cable won't be seen.",
         "anim": ConnectBoardAnim},
        {"title": "Let's flash it",
         "body": "Node Medic will detect the board and flash it as an RNode. Then "
                 "plug it into your computer.",
         "anim": None, "next": "Start setup  →"},
    ],
}


def guide_steps(path):
    """The ordered step dicts for a birth *path* (pure — unit-testable). Unknown
    paths return an empty list."""
    return list(_STEPS.get(path, []))


def _line(text, size, color="text_primary", bold=False, h=None):
    lbl = Label(text=text, font_size=size, bold=bold, halign="left", valign="middle",
                color=theme.hex_to_rgba(theme.COLORS[color]))
    if h is not None:
        lbl.size_hint_y = None
        lbl.height = dp(h)
    lbl.bind(size=lambda i, v: setattr(i, "text_size", v))
    return lbl


class BirthGuideScreen(BoxLayout):
    """The step-by-step birth walkthrough. ``on_complete(path)`` fires when the
    physical-prep steps are done, to hand off to the real BIRTH flow."""

    def __init__(self, on_complete=None, **kwargs):
        kwargs.setdefault("orientation", "vertical")
        super().__init__(**kwargs)
        self._on_complete = on_complete
        self._path = None
        self._i = 0
        self._current = None
        self.reset()

    def reset(self):
        """Back to the 'what are you building?' chooser (call when the guide is
        (re)entered)."""
        self._stop_current()
        self._path = None
        self._i = 0
        self._render_intro()

    # -- rendering ---------------------------------------------------------
    def _render_intro(self):
        self.clear_widgets()
        self._current = None
        wrap = BoxLayout(orientation="vertical", padding=dp(22), spacing=dp(16))
        wrap.add_widget(_line("What are you building?", "26sp", bold=True, h=44))
        wrap.add_widget(_line("Pick one — Node Medic will guide you the rest of the way.",
                              "16sp", color="text_secondary", h=30))
        for key, title, subtitle in BIRTH_PATHS:
            wrap.add_widget(self._path_button(key, title, subtitle))
        from kivy.uix.widget import Widget
        wrap.add_widget(Widget())
        self.add_widget(wrap)

    def _path_button(self, key, title, subtitle):
        btn = Button(size_hint_y=None, height=dp(104), background_normal="",
                     background_color=theme.hex_to_rgba(theme.COLORS["surface"]))
        inner = BoxLayout(orientation="vertical", padding=[dp(18), dp(12)], spacing=dp(4))
        inner.add_widget(_line(title, "21sp", bold=True, h=30))
        sub = _line(subtitle, "14sp", color="text_secondary")
        inner.add_widget(sub)
        inner.size = btn.size
        btn.bind(size=lambda _b, v: setattr(inner, "size", v),
                 pos=lambda _b, v: setattr(inner, "pos", v))
        btn.add_widget(inner)
        btn.bind(on_release=lambda *_: self._choose(key))
        return btn

    def _choose(self, path):
        self._path = path
        self._i = 0
        self._render_step()

    def _render_step(self):
        steps = guide_steps(self._path)
        if not steps or self._i >= len(steps):
            self._finish()
            return
        self._stop_current()
        s = steps[self._i]
        anim = s["anim"]() if s.get("anim") else None
        step = WizardStep(index=self._i, total=len(steps), title=s["title"],
                          body=s["body"], anim=anim, hint=s.get("hint", ""),
                          next_text=s.get("next", "Next  →"),
                          on_next=self._next, on_back=self._back)
        self.clear_widgets()
        self.add_widget(step)
        self._current = step
        step.start()

    # -- navigation --------------------------------------------------------
    def _next(self):
        self._i += 1
        if self._i >= len(guide_steps(self._path)):
            self._finish()
        else:
            self._render_step()

    def _back(self):
        if self._i == 0:
            self.reset()                    # off the first step -> intro chooser
        else:
            self._i -= 1
            self._render_step()

    def _finish(self):
        path = self._path
        self._stop_current()
        if self._on_complete:
            self._on_complete(path)

    def _stop_current(self):
        if self._current is not None and hasattr(self._current, "stop"):
            self._current.stop()
        self._current = None
