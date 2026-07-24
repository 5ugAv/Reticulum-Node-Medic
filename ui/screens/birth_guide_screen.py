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
from ui.widgets.birth_anims import ConnectBoardAnim, InsertSdAnim, ProvisionAnim

#: Animation key (from ui.birth_guide_flow) -> the widget class that draws it.
_ANIMS = {"connect_board": ConnectBoardAnim, "insert_sd": InsertSdAnim,
          "provision": ProvisionAnim}


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

    def __init__(self, on_complete=None, on_navigate=None, **kwargs):
        kwargs.setdefault("orientation", "vertical")
        super().__init__(**kwargs)
        self._on_complete = on_complete
        self._on_navigate = on_navigate       # (screen_name) -> switch to a screen
        self._path = None
        self._i = 0
        self._current = None
        self._node_name = ""
        self.reset()

    def reset(self):
        """Back to the 'what are you building?' chooser (call when the guide is
        (re)entered)."""
        self._stop_current()
        self._path = None
        self._i = 0
        self._node_name = ""
        self._render_intro()

    # -- rendering ---------------------------------------------------------
    def _render_intro(self):
        self.clear_widgets()
        self._current = None
        wrap = BoxLayout(orientation="vertical", padding=dp(22), spacing=dp(16))
        from ui.widgets.help_button import HelpButton
        head = BoxLayout(orientation="horizontal", size_hint_y=None, height=dp(44),
                         spacing=dp(8))
        head.add_widget(_line("What are you building?", "26sp", bold=True))
        head.add_widget(HelpButton())
        wrap.add_widget(head)
        wrap.add_widget(_line("Not sure which is which? Tap the  ?  above. Node Medic "
                              "will guide you the rest of the way.",
                              "16sp", color="text_secondary", h=56))
        for key, title, subtitle in BIRTH_PATHS:
            # the Pi card carries a longer description — give it room so it doesn't
            # clip; the shorter cards stay compact.
            h = 170 if key == "pi" else 104
            wrap.add_widget(self._path_button(key, title, subtitle, height=h))
        # Mitosis is a different KIND of action — not building a node but cloning
        # the Node Medic itself — so it sits at the end, styled apart, and routes
        # straight to the MITOSIS screen (no guided build steps).
        wrap.add_widget(self._mitosis_button())
        from kivy.uix.widget import Widget
        wrap.add_widget(Widget())
        self.add_widget(wrap)

    def _mitosis_button(self):
        btn = Button(size_hint_y=None, height=dp(104), background_normal="",
                     background_color=theme.hex_to_rgba(theme.COLORS["accent"]))
        inner = BoxLayout(orientation="vertical", padding=[dp(18), dp(12)], spacing=dp(4))
        inner.add_widget(_line("Mitosis - clone this Node Medic", "21sp",
                               bold=True, color="background", h=30))
        inner.add_widget(_line("Copy this Node Medic onto a fresh Raspberry Pi 5 - "
                               "a second building tool.", "14sp", color="background"))
        inner.size = btn.size
        btn.bind(size=lambda _b, v: setattr(inner, "size", v),
                 pos=lambda _b, v: setattr(inner, "pos", v))
        btn.add_widget(inner)
        btn.bind(on_release=lambda *_: self._on_navigate and self._on_navigate("mitosis"))
        return btn

    def _path_button(self, key, title, subtitle, height=104):
        btn = Button(size_hint_y=None, height=dp(height), background_normal="",
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
        self._render_name()

    def _render_name(self):
        """First guided step: name the node. Folded into the flow here (instead of
        on the BIRTH screen) so the whole birth is one continuous walkthrough; the
        name is carried to the BIRTH hand-off prefilled."""
        self._stop_current()
        from kivy.uix.textinput import TextInput
        from kivy.clock import Clock
        from ui.onscreen_keyboard import bind_field
        total = len(guide_steps(self._path)) + 1
        ti = TextInput(text=self._node_name, multiline=False,
                       hint_text="Name this node  (e.g. Rooftop-East)",
                       size_hint_y=None, height=dp(58), font_size="20sp")
        bind_field(ti)
        self._name_input = ti
        step = WizardStep(index=0, total=total, title="Name this node",
                          body="Give this node a short, memorable name — you'll see it "
                               "on the map and on its birth certificate.",
                          input_widget=ti, next_text="Next  →",
                          on_next=self._name_next, on_back=self.reset)
        self.clear_widgets()
        self.add_widget(step)
        self._current = step
        Clock.schedule_once(lambda *_: setattr(ti, "focus", True), 0.3)

    def _name_next(self):
        name = (self._name_input.text or "").strip()
        if not name:                         # a name is required to continue
            self._name_input.focus = True
            return
        self._node_name = name
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
        # +1 on index/total for the name step folded in ahead of these
        step = WizardStep(index=self._i + 1, total=len(steps) + 1, title=s["title"],
                          body=s["body"], anim=anim, hint=s.get("hint", ""),
                          next_text=s.get("next", "Next  →"),
                          on_next=self._next, on_back=self._back)
        self.clear_widgets()
        self.add_widget(step)
        self._current = step
        step.start()
        # A "connect your board" step loops until the medic SENSES a board on USB,
        # then the animation fires its green "Connected!" burst. Until then Next is
        # grayed out — you can't move on without a board actually plugged in.
        if isinstance(anim, ConnectBoardAnim):
            step.set_next_enabled(False)
            self._start_board_poll(anim)

    # -- navigation --------------------------------------------------------
    def _next(self):
        self._advance_token = getattr(self, "_advance_token", 0) + 1   # cancel auto-advance
        steps = guide_steps(self._path)
        cur = steps[self._i] if self._i < len(steps) else {}
        if cur.get("screen") and self._on_navigate:   # step hands off to a full screen
            self._stop_board_poll()
            self._on_navigate(cur["screen"])
            return
        self._i += 1
        if self._i >= len(steps):
            self._finish()
        else:
            self._render_step()

    def _back(self):
        self._advance_token = getattr(self, "_advance_token", 0) + 1   # cancel auto-advance
        if self._i == 0:
            self._render_name()             # off the first step -> the name step
        else:
            self._i -= 1
            self._render_step()

    def _finish(self):
        path = self._path
        self._stop_current()
        if self._on_complete:
            self._on_complete(path, self._node_name)

    def _stop_current(self):
        self._stop_board_poll()
        if self._current is not None and hasattr(self._current, "stop"):
            self._current.stop()
        self._current = None

    # -- board-presence gate ------------------------------------------------
    def _start_board_poll(self, anim):
        """Poll for a work board on the medic's USB; fire the anim's Connected!
        burst the moment one appears. Checks off-thread (serial enumeration)."""
        from kivy.clock import Clock
        self._stop_board_poll()

        def tick(_dt):
            import threading

            def work():
                present = False
                try:
                    from ui.hw_factories import hardware_present
                    present = hardware_present()
                except Exception:
                    present = False
                if present:
                    Clock.schedule_once(lambda _d: self._on_board_present(anim), 0)
            threading.Thread(target=work, daemon=True).start()

        self._board_poll = Clock.schedule_interval(tick, 1.2)
        tick(0)                                       # check immediately too

    def _on_board_present(self, anim):
        self._stop_board_poll()
        if hasattr(anim, "mark_connected"):
            anim.mark_connected()
        # A board is here — the green burst fires and Next un-grays (so the operator
        # can go on, and the flow also auto-advances after the celebration below).
        if self._current is not None and hasattr(self._current, "set_next_enabled"):
            self._current.set_next_enabled(True)
        # Let the "Connected!" celebration play, then carry the flow forward on its
        # own — detection drives the wizard, no tap needed. A manual Next/Back
        # bumps the token and cancels this pending auto-advance.
        from kivy.clock import Clock
        self._advance_token = getattr(self, "_advance_token", 0) + 1
        tok = self._advance_token
        Clock.schedule_once(
            lambda _d: (getattr(self, "_advance_token", None) == tok
                        and self._current is not None and self._next()), 2.0)

    def _stop_board_poll(self):
        ev = getattr(self, "_board_poll", None)
        if ev is not None:
            ev.cancel()
            self._board_poll = None
