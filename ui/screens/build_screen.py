"""Build screen — provision a node, hardware selected first.

Hardware is chosen up front (Heltec V4 -> RTNode-2400 path; Raspberry Pi ->
full node path). The chosen workflow runs on a background thread with live step
progress; a Type-B build then shows the pre-filled onboarding form and ends on
a photographable birth-certificate card.

Workflow factories are injected, so the heavy lifting stays in the tested core
and this screen is transport-agnostic.
"""

from __future__ import annotations

import threading

from kivy.clock import Clock
from kivy.metrics import dp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.scrollview import ScrollView

from ui import theme


def _line(text, color="text_primary", bold=False, size="15sp"):
    lbl = Label(text=text, halign="left", valign="middle", bold=bold,
                font_size=size, color=theme.hex_to_rgba(theme.COLORS[color]),
                size_hint_y=None, height=dp(26))
    lbl.bind(size=lambda i, v: setattr(i, "text_size", v))
    return lbl


class BuildScreen(BoxLayout):
    def __init__(self, workflow_factories, **kwargs):
        super().__init__(**kwargs)
        self.orientation = "vertical"
        self.padding = dp(12)
        self.spacing = dp(8)
        # {"heltec_v4": factory, "pi": factory} — each returns a workflow with
        # .run_all(on_progress), .birth_certificate, and (optional) .onboarding
        self._factories = workflow_factories
        self._workflow = None

        self.add_widget(_line("Select the hardware to provision:", bold=True,
                              size="18sp"))
        picker = BoxLayout(orientation="horizontal", size_hint_y=None,
                           height=dp(56), spacing=dp(8))
        for key, label in (("heltec_v4", "Heltec V4  (RTNode-2400)"),
                           ("pi", "Raspberry Pi  (full node)")):
            if key not in self._factories:
                continue
            btn = Button(
                text=label, background_normal="",
                background_color=theme.hex_to_rgba(theme.COLORS["accent"]),
                color=theme.hex_to_rgba(theme.COLORS["background"]))
            btn.bind(on_release=lambda *_a, k=key: self.start(k))
            picker.add_widget(btn)
        self.add_widget(picker)

        self.scroll = ScrollView()
        self.list = BoxLayout(orientation="vertical", size_hint_y=None,
                              spacing=dp(2))
        self.list.bind(minimum_height=self.list.setter("height"))
        self.scroll.add_widget(self.list)
        self.add_widget(self.scroll)

    def start(self, hardware_key):
        self.list.clear_widgets()
        self.list.add_widget(_line(f"Building {hardware_key}...", bold=True))
        self._workflow = self._factories[hardware_key]()
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        self._workflow.run_all(on_progress=lambda r:
                              Clock.schedule_once(lambda dt: self._step(r), 0))
        Clock.schedule_once(lambda dt: self._finish(), 0)

    def _step(self, result):
        mark = "skip" if result.skipped else ("ok" if result.success else "FAIL")
        color = ("text_secondary" if result.skipped
                 else "green" if result.success else "red")
        self.list.add_widget(_line(f"  [{mark}] {result.name}", color=color,
                                   size="14sp"))

    def _finish(self):
        onboarding = getattr(self._workflow, "onboarding", None)
        if onboarding:
            self.list.add_widget(_line("Onboarding (enter at RTNode-Setup / "
                                       "http://10.0.0.1):", bold=True,
                                       size="16sp"))
            for k in ("node_name", "ssid", "psk", "freq", "bw", "sf", "cr",
                      "txp", "advert_en", "advert_lat", "advert_lon",
                      "advert_jitter"):
                if k not in onboarding:
                    continue
                v = onboarding.get(k, "")
                shown = v if v != "" else "____  (operator)"
                self.list.add_widget(_line(f"    {k}: {shown}", size="13sp"))

        cert = getattr(self._workflow, "birth_certificate", None)
        if cert:
            self.list.add_widget(_line("Birth certificate:", bold=True,
                                       size="16sp"))
            for k, v in cert.items():
                self.list.add_widget(_line(f"    {k}: {v}", size="13sp"))
