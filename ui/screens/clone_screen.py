"""Clone Tool screen (mode #5).

Replicates this medic onto a fresh Pi 5. One button; the eight clone steps
stream in as live rows (pending -> running -> done/failed) with plain-English
names. The clone gets a FRESH mesh identity — the screen says so up front,
since it's the one surprising design decision.

The workflow runs on a background thread; results are marshalled onto the UI
thread with Clock. The workflow is injected (a factory), mirroring the other
screens, so this stays transport-agnostic and demo-able without a second Pi.
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

#: step name -> plain-English row title (guided mode; order matches _CLONE_STEPS)
STEP_TITLES = [
    ("verify_target_pi5", "Check the new computer is a Raspberry Pi 5"),
    ("transfer_tool", "Copy the Node Medic tool across"),
    ("transfer_firmware_cache", "Copy the offline firmware cache"),
    ("install_dependencies", "Install the software stack (offline, from carried wheels)"),
    ("copy_monitoring_db", "Copy the monitoring records"),
    ("generate_fresh_identity", "Give the clone its own fresh mesh identity"),
    ("configure_autostart", "Set the tool to start on boot"),
    ("final_verification", "Final check-over"),
]


def _label(text, color="text_primary", bold=False, size="16sp"):
    lbl = Label(text=text, halign="left", valign="middle", bold=bold,
                font_size=size, color=theme.hex_to_rgba(theme.COLORS[color]))
    lbl.bind(size=lambda i, v: setattr(i, "text_size", v))
    return lbl


class CloneScreen(BoxLayout):
    def __init__(self, workflow_factory, **kwargs):
        super().__init__(**kwargs)
        self.orientation = "vertical"
        self.padding = dp(10)
        self.spacing = dp(8)
        self._workflow_factory = workflow_factory

        intro = _label(
            "Clone this Node Medic onto a fresh Raspberry Pi 5.\n"
            "Everything travels: the tool, the offline firmware cache and the "
            "software stack (no internet needed). The clone gets its OWN fresh "
            "mesh identity - it becomes a new, separate medic.",
            color="text_secondary", size="15sp")
        intro.size_hint_y = None
        intro.height = dp(92)
        self.add_widget(intro)

        self.run_btn = Button(
            text="Clone onto the connected Pi 5", size_hint_y=None,
            height=dp(56), font_size="20sp", background_normal="",
            background_color=theme.hex_to_rgba(theme.COLORS["accent"]),
            color=theme.hex_to_rgba(theme.COLORS["background"]))
        self.run_btn.bind(on_release=lambda *_: self.start())
        self.add_widget(self.run_btn)

        self.scroll = ScrollView()
        self.list = BoxLayout(orientation="vertical", size_hint_y=None,
                              spacing=dp(2))
        self.list.bind(minimum_height=self.list.setter("height"))
        self.scroll.add_widget(self.list)
        self.add_widget(self.scroll)

        self._rows = {}
        self._build_rows()

    # -- rows ----------------------------------------------------------------

    def _build_rows(self):
        self.list.clear_widgets()
        self._rows = {}
        for name, title in STEP_TITLES:
            row = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(8))
            status = _label("-", color="text_secondary", bold=True, size="18sp")
            status.size_hint_x = None
            status.width = dp(34)
            text = _label(title, color="text_secondary")
            row.add_widget(status)
            row.add_widget(text)
            self.list.add_widget(row)
            self._rows[name] = (status, text)

    def _set_row(self, name, mark, color, detail=None):
        pair = self._rows.get(name)
        if not pair:
            return
        status, text = pair
        status.text = mark
        status.color = theme.hex_to_rgba(theme.COLORS[color])
        text.color = theme.hex_to_rgba(theme.COLORS["text_primary"])
        if detail:
            base = dict(STEP_TITLES).get(name, name)
            text.text = f"{base}\n[{detail}]" if detail else base

    # -- run -------------------------------------------------------------------

    def start(self):
        if self.run_btn.disabled:
            return
        self.run_btn.disabled = True
        self.run_btn.text = "Cloning..."
        self._build_rows()
        workflow = self._workflow_factory()
        if workflow.steps:
            self._set_row(workflow.steps[0][0], ">", "accent")
        threading.Thread(target=self._run, args=(workflow,), daemon=True).start()

    def _run(self, workflow):
        results = workflow.run_all(on_progress=lambda r: Clock.schedule_once(
            lambda dt, res=r: self._on_step(workflow, res), 0))
        Clock.schedule_once(lambda dt: self._finish(results), 0)

    def _on_step(self, workflow, result):
        if result.skipped:
            self._set_row(result.name, "s", "text_secondary", "skipped")
        elif result.success:
            self._set_row(result.name, "OK", "green")
        else:
            self._set_row(result.name, "X", "red", result.message)
        # highlight the next pending step
        done = {r.name for r in workflow.results}
        for name, _f in workflow.steps:
            if name not in done:
                self._set_row(name, ">", "accent")
                break

    def _finish(self, results):
        ok = all(r.success or r.skipped for r in results) and results
        self.run_btn.disabled = False
        if ok and len(results) == len(STEP_TITLES):
            self.run_btn.text = "Clone complete - the new medic is ready"
            self.run_btn.background_color = theme.hex_to_rgba(theme.COLORS["green"])
        else:
            self.run_btn.text = "Clone stopped - fix the failed step and try again"
            self.run_btn.background_color = theme.hex_to_rgba(theme.COLORS["red"])
