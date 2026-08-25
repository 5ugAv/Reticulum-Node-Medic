"""MITOSIS screen (mode 6) — clone this medic onto a fresh Raspberry Pi 5.

A staged, guided sequence (operator's design, 2026-08-25, first bench run):

  1. INSERT — an animation asks for the new medic's SD card; the medic
     watches its own reader and greets the card with the green ripple (the
     same "I can see it now" every board gets), then moves on by itself.
  2. NAME — name the new medic (on-screen keyboard), press Continue; the
     card is written and the one-time password is shown BIG: write it down.
  3. CLONE — the thirteen-step ladder streams as live rows, exactly as the
     build flows do.

The workflow is injected (a factory) so the ladder stays transport-agnostic
and demo-able; the card write runs on a background thread with results
marshalled onto the UI thread by Clock.
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

#: step name -> plain-English row title. Rows are rebuilt from the workflow's
#: OWN ladder at run time, so an unknown step still gets a row (its raw name).
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
        self._card_written = False
        self._card_ev = None
        self._show_stage_insert()

    # -- stage plumbing ------------------------------------------------------

    def _clear(self):
        if self._card_ev is not None:
            self._card_ev.cancel()
            self._card_ev = None
        anim = getattr(self, "_anim", None)
        if anim is not None:
            try:
                anim.stop()
            except Exception:                              # noqa: BLE001
                pass
            self._anim = None
        self.clear_widgets()

    # -- stage 1: INSERT -----------------------------------------------------

    def _show_stage_insert(self):
        self._clear()
        title = _label("Clone this Node Medic", bold=True, size="22sp")
        title.size_hint_y, title.height = None, dp(34)
        self.add_widget(title)
        body = _label(
            "Insert the new medic's SD card into Node Medic.\n"
            "Everything on that card will be erased.",
            color="text_secondary", size="16sp")
        body.size_hint_y, body.height = None, dp(56)
        self.add_widget(body)
        try:
            from ui.widgets.birth_anims import InsertSdAnim
            self._anim = InsertSdAnim()
            self.add_widget(self._anim)
            self._anim.start()          # the loop: card slides toward the reader
        except Exception:                                  # noqa: BLE001
            self._anim = None
        # Escape hatches: a marginal reader that never greets, and the case
        # where the new medic is ALREADY booted from a card written earlier.
        skip = Button(text="The new medic is already booted \— skip to the clone",
                      size_hint_y=None, height=dp(40), font_size="14sp",
                      background_normal="",
                      background_color=theme.hex_to_rgba(theme.COLORS["surface"]),
                      color=theme.hex_to_rgba(theme.COLORS["text_secondary"]))
        skip.bind(on_release=lambda *_: self._show_stage_clone())
        self.add_widget(skip)
        self._start_card_poll()

    def _start_card_poll(self):
        self._card_greeted = False

        def tick(_dt):
            def work():
                try:
                    from provisioning import pi_imager
                    st = pi_imager.card_status()
                except Exception:                          # noqa: BLE001
                    return
                if st["state"] == "none":
                    return
                Clock.schedule_once(lambda _d: self._on_card_seen(), 0)
            threading.Thread(target=work, daemon=True).start()

        self._card_ev = Clock.schedule_interval(tick, 1.5)
        tick(0)                          # a card already in place greets NOW

    def _on_card_seen(self):
        if self._card_greeted:
            return
        self._card_greeted = True
        if self._card_ev is not None:
            self._card_ev.cancel()
            self._card_ev = None
        fn = getattr(self._anim, "mark_card_found", None)
        if callable(fn):
            try:
                fn()
            except Exception:                              # noqa: BLE001
                pass
        # Let the green ripple be SEEN (1.4s burst + a beat), then move on —
        # seeing the card answers this stage, nothing is left to decide here.
        Clock.schedule_once(lambda _d: self._show_stage_name(), 1.6)

    # -- stage 2: NAME + write ----------------------------------------------

    def _show_stage_name(self):
        self._clear()
        title = _label("Name this medic", bold=True, size="22sp")
        title.size_hint_y, title.height = None, dp(34)
        self.add_widget(title)
        body = _label(
            "The name becomes its address on the cable and its place in the "
            "family line.", color="text_secondary", size="14sp")
        body.size_hint_y, body.height = None, dp(40)
        self.add_widget(body)

        from kivy.uix.textinput import TextInput
        from ui.onscreen_keyboard import bind_field
        self.name_input = TextInput(text="NodeMedic2", multiline=False,
                                    font_size=theme.font_sp("20sp"),
                                    size_hint_y=None, height=dp(52))
        bind_field(self.name_input)
        self.add_widget(self.name_input)

        self.card_btn = Button(
            text="Write the card \→", size_hint_y=None,
            height=dp(56), font_size="20sp", background_normal="",
            background_color=theme.hex_to_rgba(theme.COLORS["accent"]),
            color=theme.hex_to_rgba(theme.COLORS["background"]))
        self.card_btn.bind(on_release=lambda *_: self._card_btn_tapped())
        self.add_widget(self.card_btn)

        self.card_status = _label("", color="text_secondary", size="14sp")
        self.card_status.size_hint_y = None
        self.card_status.height = dp(120)
        self.add_widget(self.card_status)

        from kivy.uix.widget import Widget
        self.add_widget(Widget())

    def _card_btn_tapped(self):
        # ONE binding for the button's whole life — kivy can't unbind a lambda,
        # and rebinding left the old handler live (a Continue tap would have
        # started a SECOND card write). The state routes instead.
        if self._card_written:
            self._show_stage_clone()
        else:
            self._write_card()

    def _write_card(self):
        if self.card_btn.disabled:
            return
        name = (self.name_input.text or "").strip()
        self.card_btn.disabled = True
        self.card_btn.text = "Writing the card\… (takes a few minutes)"
        self.card_status.text = "Imaging \— leave the card in until this finishes."

        def work():
            ok, msg, pw = False, "", ""
            try:
                from provisioning import pi_imager
                disks = pi_imager.list_target_disks()
                if len(disks) != 1:
                    msg = ("The card has gone \— put it back in the reader."
                           if not disks else
                           "More than one removable disk is attached \— "
                           "leave only the new medic's card in.")
                else:
                    from workflows.mitosis_card import image_medic_card
                    ok, msg, pw = image_medic_card(
                        disks[0]["path"], name or "NodeMedic2")
            except Exception as e:                         # noqa: BLE001
                msg = f"Card write failed: {e}"

            def done(_dt):
                self.card_btn.disabled = False
                if ok:
                    self._card_written = True
                    self.card_btn.text = "Card written \✓  \—  Continue \→"
                    self.card_btn.background_color = theme.hex_to_rgba(
                        theme.COLORS["green"])
                    self.card_status.text = (
                        "WRITE THIS DOWN \— the new medic's login:\\n\\n"
                        f"user:  medic\\npassword:  {pw}\\n\\n"
                        "Then: card into the new medic, power on (first boot "
                        "takes up to 5 minutes), cable or WiFi, and Continue.")
                else:
                    self.card_btn.text = "Write the card \→"
                    self.card_status.text = msg
            Clock.schedule_once(done, 0)

        threading.Thread(target=work, daemon=True).start()

    # -- stage 3: CLONE ------------------------------------------------------

    def _show_stage_clone(self):
        self._clear()
        self.run_btn = Button(
            text="Clone onto the new medic", size_hint_y=None,
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
                hostname=(getattr(self, "name_input", None) and
                          self.name_input.text or "").strip())
        except TypeError:                       # demo/legacy factory
            workflow = self._workflow_factory()
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
