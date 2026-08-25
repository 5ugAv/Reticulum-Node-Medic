"""MITOSIS screen (mode 6) — clone this medic onto a fresh Raspberry Pi 5.

A staged, guided sequence (operator's design, 2026-08-25, refined live at the
bench across the first real runs):

  1. INSERT — an animation asks for the new medic's SD card; the medic
     watches its own reader and greets the card with the green ripple, then
     moves on by itself.
  2. NAME — name the new medic (on-screen keyboard).
  3. PASSWORD — the keeper TYPES their own password and confirms it; the
     button walks amber ("Type a password") → amber ("Confirm the password")
     → green ("Write the card →") only when the two match and pass the
     validator. Chosen, never generated: an operator-owned password is one
     they can actually remember at the new medic's own screen.
  4. WRITE — the BIRTH imaging progress ring with the organ narration
     ("Kernel in — the beating heart"), screensaver suppressed.
  5. WRITTEN — the physical hand-off instructions, one clear page.
  6. CLONE — the ladder streams as live rows; the active row ticks elapsed
     time; a failure keeps its WHOLE message and retries FROM the failed
     step, never from the top.

The screen is DORMANT until shown (begin/sleep wired to the Screen's
enter/leave) — its card poll must never advance stages behind the operator's
back while they use the rest of the tool.
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

#: Rough dd+config seconds for the write's fill estimate — the same model the
#: BIRTH imaging screen uses (time-based, capped at 95% until truth arrives).
EST_WRITE_S = 240.0


def _label(text, color="text_primary", bold=False, size="16sp"):
    lbl = Label(text=text, halign="left", valign="middle", bold=bold,
                font_size=theme.font_sp(size),
                color=theme.hex_to_rgba(theme.COLORS[color]))
    lbl.bind(size=lambda i, v: setattr(i, "text_size", v))
    return lbl


def _small_btn(text):
    b = Button(text=text, size_hint_y=None, height=dp(40), font_size="14sp",
               background_normal="",
               background_color=theme.hex_to_rgba(theme.COLORS["surface"]),
               color=theme.hex_to_rgba(theme.COLORS["text_secondary"]))
    return b


class MitosisScreen(BoxLayout):
    def __init__(self, workflow_factory, **kwargs):
        super().__init__(**kwargs)
        self.orientation = "vertical"
        self.padding = dp(10)
        self.spacing = dp(8)
        self._workflow_factory = workflow_factory
        self._card_written = False
        self._card_ev = None
        self._advance_ev = None
        self._write_ev = None
        self._stage_gen = 0
        self._retry_workflow = None
        self._cloning = False
        self._skip_mode = False
        self._name = ""

    # -- lifecycle (dormant until shown) -------------------------------------

    def begin(self):
        if self._cloning:
            return
        self._card_written = False
        self._retry_workflow = None
        self._show_stage_insert()

    def sleep(self):
        if self._cloning:
            return
        self._clear()

    def _clear(self):
        self._stage_gen += 1
        for attr in ("_card_ev", "_advance_ev", "_write_ev"):
            ev = getattr(self, attr, None)
            if ev is not None:
                ev.cancel()
                setattr(self, attr, None)
        anim = getattr(self, "_anim", None)
        if anim is not None:
            try:
                anim.stop()
            except Exception:                              # noqa: BLE001
                pass
            self._anim = None
        self.clear_widgets()

    # -- stage 1: INSERT ------------------------------------------------------

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
            self._anim.start()
        except Exception:                                  # noqa: BLE001
            self._anim = None
        skip = _small_btn("The new medic is already booted — skip to the clone")
        skip.bind(on_release=lambda *_: self._show_stage_name(skip_mode=True))
        self.add_widget(skip)
        self._start_card_poll()

    def _start_card_poll(self):
        self._card_greeted = False
        gen = self._stage_gen

        def tick(_dt):
            def work():
                try:
                    from provisioning import pi_imager
                    st = pi_imager.card_status()
                except Exception:                          # noqa: BLE001
                    return
                if st["state"] == "none":
                    return
                Clock.schedule_once(lambda _d: self._on_card_seen(gen), 0)
            threading.Thread(target=work, daemon=True).start()

        self._card_ev = Clock.schedule_interval(tick, 1.5)
        tick(0)

    def _on_card_seen(self, gen):
        if gen != self._stage_gen or self._card_greeted:
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

        def _advance(_d, g=gen):
            if g == self._stage_gen:
                self._show_stage_name()
        self._advance_ev = Clock.schedule_once(_advance, 1.6)

    # -- stage 2: NAME --------------------------------------------------------

    def _show_stage_name(self, skip_mode=False):
        self._clear()
        self._skip_mode = skip_mode
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
        self.name_input = TextInput(text=self._name or "NodeMedic2",
                                    multiline=False,
                                    font_size=theme.font_sp("20sp"),
                                    size_hint_y=None, height=dp(52))
        bind_field(self.name_input)
        self.add_widget(self.name_input)

        nxt = Button(
            text=("Find and clone →" if skip_mode else "Continue →"),
            size_hint_y=None, height=dp(56), font_size="20sp",
            background_normal="",
            background_color=theme.hex_to_rgba(theme.COLORS["accent"]),
            color=theme.hex_to_rgba(theme.COLORS["background"]))
        nxt.bind(on_release=lambda *_: self._name_continue())
        self.add_widget(nxt)

        back = _small_btn("← Back")
        back.bind(on_release=lambda *_: self._show_stage_insert())
        self.add_widget(back)
        from kivy.uix.widget import Widget
        self.add_widget(Widget())

    def _name_continue(self):
        self._name = (self.name_input.text or "").strip()
        if self._skip_mode:
            self._show_stage_clone()
        else:
            self._show_stage_password()

    # -- stage 3: PASSWORD (typed + confirmed, never generated) ---------------

    def _show_stage_password(self):
        self._clear()
        title = _label("Create its password", bold=True, size="22sp")
        title.size_hint_y, title.height = None, dp(34)
        self.add_widget(title)
        body = _label(
            "You will type this to log in on the new medic's own screen "
            "(user 'pi'). Choose one you can remember — a lost password "
            "means re-imaging the card.",
            color="text_secondary", size="14sp")
        body.size_hint_y, body.height = None, dp(56)
        self.add_widget(body)

        from kivy.uix.textinput import TextInput
        from ui.onscreen_keyboard import bind_field
        self.pw1 = TextInput(hint_text="Password", password=True,
                             multiline=False,
                             font_size=theme.font_sp("20sp"),
                             size_hint_y=None, height=dp(52))
        self.pw2 = TextInput(hint_text="Confirm password", password=True,
                             multiline=False,
                             font_size=theme.font_sp("20sp"),
                             size_hint_y=None, height=dp(52))
        for f in (self.pw1, self.pw2):
            bind_field(f)
            f.bind(text=lambda *_: self._password_state())
            self.add_widget(f)

        # The button WALKS ITS COLOURS (operator spec): amber "Type a
        # password" → amber "Confirm the password" → green "Write the card →"
        # that lights only when the two match and pass the validator.
        self.pw_btn = Button(
            text="Type a password", size_hint_y=None, height=dp(56),
            font_size="20sp", background_normal="", disabled=True,
            background_color=theme.hex_to_rgba(theme.COLORS["amber"]),
            color=theme.hex_to_rgba(theme.COLORS["background"]))
        self.pw_btn.bind(on_release=lambda *_: self._password_continue())
        self.add_widget(self.pw_btn)

        self.pw_status = _label("", color="text_secondary", size="13sp")
        self.pw_status.size_hint_y, self.pw_status.height = None, dp(24)
        self.add_widget(self.pw_status)

        back = _small_btn("← Back")
        back.bind(on_release=lambda *_: self._show_stage_name())
        self.add_widget(back)
        from kivy.uix.widget import Widget
        self.add_widget(Widget())
        self._password_state()

    def _password_state(self):
        from provisioning.pi_imager import validate_new_password
        p1 = self.pw1.text or ""
        p2 = self.pw2.text or ""
        if not p1:
            self.pw_btn.text = "Type a password"
            self.pw_btn.disabled = True
            self.pw_btn.background_color = theme.hex_to_rgba(
                theme.COLORS["amber"])
            self.pw_status.text = ""
            return
        ok, why = validate_new_password(p1, p2 if p2 else None)
        if not p2 or p1 != p2:
            self.pw_btn.text = "Confirm the password"
            self.pw_btn.disabled = True
            self.pw_btn.background_color = theme.hex_to_rgba(
                theme.COLORS["amber"])
            self.pw_status.text = ("" if not p2 else
                                   "The two passwords don't match yet.")
            return
        if not ok:
            self.pw_btn.text = "Confirm the password"
            self.pw_btn.disabled = True
            self.pw_btn.background_color = theme.hex_to_rgba(
                theme.COLORS["amber"])
            self.pw_status.text = why
            return
        self.pw_btn.text = "Write the card →"
        self.pw_btn.disabled = False
        self.pw_btn.background_color = theme.hex_to_rgba(theme.COLORS["green"])
        self.pw_status.text = "Passwords match."

    def _password_continue(self):
        if self.pw_btn.disabled:
            return
        self._show_stage_write(self.pw1.text)

    # -- stage 4: WRITE (the BIRTH progress ring) ------------------------------

    def _show_stage_write(self, password):
        self._clear()
        gen = self._stage_gen
        title = _label("Writing the new medic's card", bold=True, size="22sp")
        title.size_hint_y, title.height = None, dp(34)
        self.add_widget(title)

        from kivy.uix.anchorlayout import AnchorLayout
        from ui.widgets.progress_ring import ProgressRing
        holder = AnchorLayout(size_hint_y=None, height=dp(120))
        self._ring = ProgressRing(size=(dp(104), dp(104)),
                                  label_font_size="19sp")
        holder.add_widget(self._ring)
        self.add_widget(holder)

        from provisioning import pi_imager
        self._stage_lbl = _label(pi_imager.current_stage_label(0.0),
                                 color="text_secondary", size="15sp")
        self._stage_lbl.size_hint_y, self._stage_lbl.height = None, dp(48)
        self.add_widget(self._stage_lbl)

        warn = _label("Leave the card in until this finishes.",
                      color="text_secondary", size="13sp")
        warn.size_hint_y, warn.height = None, dp(24)
        self.add_widget(warn)
        from kivy.uix.widget import Widget
        self.add_widget(Widget())

        self._mark_activity(True)
        import time as _time
        t0 = _time.monotonic()

        def _tick(_dt):
            frac = min(0.95, (_time.monotonic() - t0) / EST_WRITE_S)
            self._ring.set_fraction(frac)
            self._stage_lbl.text = pi_imager.current_stage_label(frac)
        self._write_ev = Clock.schedule_interval(_tick, 1.0)

        def work():
            ok, msg = False, ""
            try:
                disks = pi_imager.list_target_disks()
                if len(disks) != 1:
                    msg = ("The card has gone — put it back in the reader."
                           if not disks else
                           "More than one removable disk is attached — "
                           "leave only the new medic's card in.")
                else:
                    from workflows.mitosis_card import image_medic_card
                    ok, msg, _pw = image_medic_card(
                        disks[0]["path"], self._name or "NodeMedic2",
                        password=password)
            except Exception as e:                         # noqa: BLE001
                msg = f"Card write failed: {e}"

            def done(_dt, g=gen):
                self._mark_activity(False)
                print(f"[mitosis] card write ok={ok}: {msg}")
                if g != self._stage_gen:
                    return
                if self._write_ev is not None:
                    self._write_ev.cancel()
                    self._write_ev = None
                if ok:
                    self._card_written = True
                    self._ring.set_fraction(1.0)
                    self._show_stage_written()
                else:
                    self._show_stage_write_failed(msg)
            Clock.schedule_once(done, 0)

        threading.Thread(target=work, daemon=True).start()

    def _show_stage_write_failed(self, msg):
        self._clear()
        title = _label("The card write failed", bold=True, size="22sp",
                       color="red")
        title.size_hint_y, title.height = None, dp(34)
        self.add_widget(title)
        body = _label(msg, color="text_primary", size="15sp")
        body.valign = "top"
        body.size_hint_y = None
        body.height = dp(220)
        self.add_widget(body)
        again = Button(text="Try again →", size_hint_y=None, height=dp(56),
                       font_size="20sp", background_normal="",
                       background_color=theme.hex_to_rgba(theme.COLORS["accent"]),
                       color=theme.hex_to_rgba(theme.COLORS["background"]))
        again.bind(on_release=lambda *_: self._show_stage_password())
        self.add_widget(again)
        from kivy.uix.widget import Widget
        self.add_widget(Widget())

    def _mark_activity(self, on):
        """Screensaver off + the don't-power-off banner while writing/cloning —
        a screen going dark mid-dd invites the power yank that corrupts cards."""
        try:
            from kivy.app import App
            app = App.get_running_app()
            if app is None:
                return
            if on:
                app.begin_activity("MITOSIS in progress — keep everything "
                                   "plugged in, don't power off")
            else:
                app.end_activity()
        except Exception:                                  # noqa: BLE001
            pass

    # -- stage 5: the physical hand-off, ANIMATED (three acts) ----------------
    # Show-don't-tell (operator, live at the bench): every page that asks for
    # hardware to move gets the picture, so the flow works for someone who
    # cannot read the words. Act 1 even advances ITSELF — the medic can SEE
    # its own reader go empty.

    def _show_stage_written(self):
        self._clear()
        gen = self._stage_gen
        title = _label("Card written ✓ — move it to the new medic",
                       bold=True, size="20sp", color="green")
        title.size_hint_y, title.height = None, dp(34)
        self.add_widget(title)
        try:
            from ui.widgets.birth_anims import SdHandoverAnim
            self._anim = SdHandoverAnim()
            self.add_widget(self._anim)
            self._anim.start()
        except Exception:                                  # noqa: BLE001
            self._anim = None
        hint = _label("Take the card out of Node Medic and put it into the "
                      "NEW medic. Node Medic sees it leave and moves on by "
                      "itself.", color="text_secondary", size="14sp")
        hint.size_hint_y, hint.height = None, dp(44)
        self.add_widget(hint)
        # watch for the card LEAVING the reader
        def tick(_dt):
            def work():
                try:
                    from provisioning import pi_imager
                    st = pi_imager.card_status()
                except Exception:                          # noqa: BLE001
                    return
                if st["state"] == "none":
                    Clock.schedule_once(lambda _d: self._on_card_gone(gen), 0)
            threading.Thread(target=work, daemon=True).start()
        self._card_ev = Clock.schedule_interval(tick, 1.5)

    def _on_card_gone(self, gen):
        if gen != self._stage_gen:
            return
        if self._card_ev is not None:
            self._card_ev.cancel()
            self._card_ev = None
        fn = getattr(self._anim, "mark_moved", None)
        if callable(fn):
            try:
                fn()
            except Exception:                              # noqa: BLE001
                pass
        def _advance(_d, g=gen):
            if g == self._stage_gen:
                self._show_stage_power()
        self._advance_ev = Clock.schedule_once(_advance, 1.8)

    def _show_stage_power(self):
        self._clear()
        title = _label("Power the new medic on", bold=True, size="22sp")
        title.size_hint_y, title.height = None, dp(34)
        self.add_widget(title)
        body = _label(
            "Press BOOT on its power pack if it plays dead — a fresh pack "
            "sleeps until asked.

"
            "First boot takes up to 5 minutes. A flickering green LED means "
            "it is working — leave it be.",
            color="text_primary", size="17sp")
        body.size_hint_y, body.height = None, dp(130)
        self.add_widget(body)
        login = _label("Its screen login is  pi  +  the password you chose.",
                       color="text_secondary", size="13.5sp")
        login.size_hint_y, login.height = None, dp(24)
        self.add_widget(login)
        nxt = Button(text="It's powered — next →", size_hint_y=None,
                     height=dp(56), font_size="20sp", background_normal="",
                     background_color=theme.hex_to_rgba(theme.COLORS["accent"]),
                     color=theme.hex_to_rgba(theme.COLORS["background"]))
        nxt.bind(on_release=lambda *_: self._show_stage_cable())
        self.add_widget(nxt)
        from kivy.uix.widget import Widget
        self.add_widget(Widget())

    def _show_stage_cable(self):
        self._clear()
        title = _label("Connect the two medics", bold=True, size="22sp")
        title.size_hint_y, title.height = None, dp(34)
        self.add_widget(title)
        try:
            from ui.widgets.birth_anims import ProvisionOverCableAnim
            self._anim = ProvisionOverCableAnim()
            self.add_widget(self._anim)
            self._anim.start()
        except Exception:                                  # noqa: BLE001
            self._anim = None
        hint = _label("An ordinary network patch cable between the two "
                      "medics' network ports — or let the new medic join "
                      "your WiFi on its own.",
                      color="text_secondary", size="14sp")
        hint.size_hint_y, hint.height = None, dp(44)
        self.add_widget(hint)
        nxt = Button(text="Connected — go to the clone →", size_hint_y=None,
                     height=dp(56), font_size="20sp", background_normal="",
                     background_color=theme.hex_to_rgba(theme.COLORS["green"]),
                     color=theme.hex_to_rgba(theme.COLORS["background"]))
        nxt.bind(on_release=lambda *_: self._show_stage_clone())
        self.add_widget(nxt)
        from kivy.uix.widget import Widget
        self.add_widget(Widget())

    # -- stage 6: CLONE --------------------------------------------------------

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
            text.text = f"{base}\n[{detail}]"
            row = text.parent
            if color == "red" and row is not None:
                text.bind(texture_size=lambda i, ts, r=row:
                          setattr(r, "height", max(dp(44), ts[1] + dp(10))))

    # -- run -------------------------------------------------------------------

    def start(self):
        if self.run_btn.disabled:
            return
        orig_label = self.run_btn.text
        self.run_btn.disabled = True
        self.run_btn.text = "Cloning..."
        if self._retry_workflow is not None:
            workflow = self._retry_workflow
            self._retry_workflow = None
            self._cloning = True
            self._mark_activity(True)
            threading.Thread(target=self._run, args=(workflow,),
                             daemon=True).start()
            return
        try:
            workflow = self._workflow_factory(hostname=self._name)
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
        self._cloning = True
        self._mark_activity(True)
        threading.Thread(target=self._run, args=(workflow,), daemon=True).start()

    def _run(self, workflow):
        import time as _time
        self._step_t0 = _time.monotonic()

        def _tick(_dt):
            done = {r.name for r in workflow.results}
            m, sec = divmod(int(_time.monotonic() - self._step_t0), 60)
            for name, _f in workflow.steps:
                if name not in done:
                    self._set_row(name, ">", "accent", f"{m}m {sec:02d}s")
                    break
        ev = Clock.schedule_interval(_tick, 1.0)

        def _progress(r):
            import time as _t
            self._step_t0 = _t.monotonic()
            Clock.schedule_once(lambda dt, res=r: self._on_step(workflow, res), 0)

        results = workflow.run_all(on_progress=_progress)
        ev.cancel()
        Clock.schedule_once(lambda dt: self._finish(workflow, results), 0)

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

    def _finish(self, workflow, results):
        self._cloning = False
        self._mark_activity(False)
        seen = {r.name for r in results}
        ok = all(r.success or r.skipped for r in results) and results
        self.run_btn.disabled = False
        if ok and len(seen) >= len(self._rows):
            self.run_btn.text = "Clone finished — every step verified"
            self.run_btn.background_color = theme.hex_to_rgba(theme.COLORS["green"])
            done = _label(
                "Now: power the new medic on ITS OWN screen. It boots into "
                "the tool and its first act is birthing its own radio — "
                "the firstborn.", color="text_primary", size="16sp")
            done.size_hint_y = None
            done.height = dp(64)
            self.add_widget(done)
        else:
            failed = next((r for r in results
                           if not r.success and not r.skipped), None)
            self._retry_workflow = workflow
            titles = dict(STEP_TITLES)
            step_name = titles.get(failed.name, failed.name) if failed else "?"
            self.run_btn.text = f"Retry from: {step_name}"
            self.run_btn.background_color = theme.hex_to_rgba(theme.COLORS["red"])
