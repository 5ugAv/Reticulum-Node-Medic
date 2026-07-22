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
from ui.birth_guide_flow import BIRTH_PATHS, guide_steps
from ui.widgets.wizard_step import WizardStep
from ui.widgets.birth_anims import ConnectBoardAnim, InsertSdAnim

#: Animation key (from ui.birth_guide_flow) -> the widget class that draws it.
_ANIMS = {"connect_board": ConnectBoardAnim, "insert_sd": InsertSdAnim}


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
        anim_cls = _ANIMS.get(s.get("anim"))
        anim = anim_cls() if anim_cls else None
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
