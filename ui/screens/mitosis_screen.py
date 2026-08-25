"""MITOSIS screen (mode 6) — clone this medic onto a fresh Raspberry Pi 5.

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

#: step name -> plain-English row title. ORDER MATCHES THE LADDER — the row
#: list is rebuilt from the workflow's own steps at run time, so a step this
#: table doesn't know still gets a row (its raw name), never silence.
STEP_TITLES = [
    ("find_new_medic", "Find the new medic (cable or WiFi) and log in"),
    ("verify_target_pi5", "Check the new computer is a Raspberry Pi 5"),
    ("carry_the_time", "Carry the time across (no RTC, no GPS yet)"),
    ("transfer_tool", "Copy the Node Medic tool across"),
    ("transfer_firmware_cache", "Copy the offline firmware cache"),
    ("install_dependencies", "Install the software stack (offline, from carried wheels)"),
    ("copy_monitoring_db", "Copy the monitoring records"),
    ("copy_kin_roster", "Carry the fleet roster (who and where)"),
    ("generate_fresh_identity", "Give the clone its own fresh mesh identity"),
    ("stamp_lineage", "Stamp the family line (child knows its parent)"),
    ("record_child_trust", "Trust the new medic as this unit's child"),
    ("configure_autostart", "Set the tool to start on boot"),
    ("final_verification", "Final check-over"),
]


def _label(text, color="text_primary", bold=False, size="16sp"):
    lbl = Label(text=text, halign="left", valign="middle", bold=bold,
                font_size=theme.font_sp(size),
                color=theme.hex_to_rgba(theme.COLORS[color]))
    lbl.bind(size=lambda i, v: setattr(i, "text_size", v))
    return lbl


class MitosisScreen(BoxLayout):
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

        # -- phase 1: write the new medic's SD card in THIS medic's reader --
        from kivy.uix.textinput import TextInput
        name_row = BoxLayout(size_hint_y=None, height=dp(48), spacing=dp(8))
        name_row.add_widget(_label("New medic's name", size="15sp"))
        self.name_input = TextInput(text="NodeMedic2", multiline=False,
                                    font_size=theme.font_sp("17sp"),
                                    size_hint_x=1.4)
        # Summon the medic's on-screen keyboard on focus — the field is on a
        # touchscreen; without this it can only be admired (caught live,
        # first MITOSIS bench run, 2026-08-25).
        from ui.onscreen_keyboard import bind_field
        bind_field(self.name_input)
        name_row.add_widget(self.name_input)
        self.add_widget(name_row)

        self.card_btn = Button(
            text="1 \u00b7 Write the new medic's SD card", size_hint_y=None,
            height=dp(50), font_size="18sp", background_normal="",
            background_color=theme.hex_to_rgba(theme.COLORS["surface"]),
            color=theme.hex_to_rgba(theme.COLORS["text_primary"]))
        self.card_btn.bind(on_release=lambda *_: self._write_card())
        self.add_widget(self.card_btn)
        self.card_status = _label(
            "Put the new medic's SD card in this medic's reader first. "
            "Everything on it will be erased.",
            color="text_secondary", size="13sp")
        self.card_status.size_hint_y = None
        self.card_status.height = dp(40)
        self.add_widget(self.card_status)

        self.run_btn = Button(
            text="2 \u00b7 Clone onto the new medic", size_hint_y=None,
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

    # -- phase 1: card writing ----------------------------------------------

    def _write_card(self):
        if self.card_btn.disabled:
            return
        name = (self.name_input.text or "").strip()
        self.card_btn.disabled = True
        self.card_btn.text = "Writing the card\u2026 (takes a few minutes)"
        self.card_status.text = "Imaging \u2014 leave the card in until this finishes."

        def work():
            ok, msg, pw = False, "", ""
            try:
                from provisioning import pi_imager
                disks = pi_imager.list_target_disks()
                if len(disks) != 1:
                    msg = ("No card found in the reader." if not disks else
                           "More than one removable disk is attached \u2014 "
                           "leave only the new medic's card in.")
                else:
                    from workflows.mitosis_card import image_medic_card
                    ok, msg, pw = image_medic_card(
                        disks[0]["device"], name or "NodeMedic2")
            except Exception as e:                     # noqa: BLE001
                msg = f"Card write failed: {e}"
            def done(_dt):
                self.card_btn.disabled = False
                if ok:
                    self.card_btn.text = "Card written \u2713"
                    self.card_btn.background_color = theme.hex_to_rgba(
                        theme.COLORS["green"])
                    self.card_status.text = (
                        f"WRITE THIS DOWN \u2014 the new medic's login:\n"
                        f"user  medic      password  {pw}\n" + msg)
                    self.card_status.height = dp(76)
                else:
                    self.card_btn.text = "1 \u00b7 Write the new medic's SD card"
                    self.card_status.text = msg
            Clock.schedule_once(done, 0)

        threading.Thread(target=work, daemon=True).start()

    # -- rows ----------------------------------------------------------------

    def _build_rows(self, steps=None):
        titles = dict(STEP_TITLES)
        pairs = ([(n, titles.get(n, n)) for n, _f in steps]
                 if steps else STEP_TITLES)
        self.list.clear_widgets()
        self._rows = {}
        for name, title in pairs:
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
        orig_label = self.run_btn.text
        self.run_btn.disabled = True
        self.run_btn.text = "Cloning..."
        try:
            workflow = self._workflow_factory(
                hostname=(self.name_input.text or "").strip())
        except TypeError:                       # demo/legacy factory
            workflow = self._workflow_factory()
        # Not wired to a real target Pi yet: plain popup, don't fake a clone.
        if getattr(workflow, "is_blocked", False):
            from ui.requirement_popup import requirement_popup
            requirement_popup(workflow.message,
                              getattr(workflow, "title", "Heads up"),
                              getattr(workflow, "under_construction", False))
            self.run_btn.disabled = False
            self.run_btn.text = orig_label
            return
        self._build_rows(steps=workflow.steps)
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
        if ok and len(results) == len(self._rows):
            self.run_btn.text = "Clone complete - the new medic is ready"
            self.run_btn.background_color = theme.hex_to_rgba(theme.COLORS["green"])
        else:
            self.run_btn.text = "Clone stopped - fix the failed step and try again"
            self.run_btn.background_color = theme.hex_to_rgba(theme.COLORS["red"])
