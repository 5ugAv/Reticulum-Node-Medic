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
from ui.text_fit import grow_to_text
from ui.i18n import tr  # i18n: wrapped — stage titles/bodies/buttons; step rows
                        # translate at render (STEP_TITLES stays English source)

#: step name -> plain-English row title. Rows are rebuilt from the workflow's
#: OWN ladder at run time, so an unknown step still gets a row (its raw name).
STEP_TITLES = [
    ("find_new_medic", "Finding the new medic (cable or WiFi) and logging in"),
    ("verify_target_pi5", "Checking the new computer is a Raspberry Pi 5"),
    ("carry_the_time", "Carrying the time across (it has no clock yet)"),
    ("transfer_tool", "Copying the Node Medic tool across"),
    ("transfer_firmware_cache", "Copying the offline firmware cache"),
    ("carry_the_toolchain",
     "Carrying the toolchains, firmware and OS image (several GB - slow)"),
    ("install_dependencies", "Installing the software stack (offline, from carried wheels)"),
    ("carry_touch_cure", "Carrying the touch settings across"),
    ("install_display_stack", "Installing the screen stack (carried, offline)"),
    ("install_carried_packages",
     "Installing the carried radio-modem packages (offline)"),
    ("copy_monitoring_db", "Copying the monitoring records"),
    ("copy_offline_maps", "Handing down the offline maps"),
    ("copy_kin_roster", "Carrying the fleet roster (who and where)"),
    ("generate_fresh_identity", "Giving it its own fresh mesh identity"),
    ("stamp_lineage", "Stamping the family line (child knows its parent)"),
    ("record_child_trust", "Trusting the new medic as this unit's child"),
    ("configure_autostart", "Setting the tool to start on boot"),
    ("install_card_helper", "Installing the card-writing helper (so it can clone itself)"),
    ("ensure_ssh_keypair", "Giving it its own SSH key"),
    ("bake_recovery_bootorder", "Teaching its boot chip to ask for help"),
    ("final_verification", "Final check-over"),
    ("restart_into_tool", "Waking the new medic into the tool"),
]

#: Expected seconds per step — drives each row's progress bar. Estimates from
#: the real shape of the work (discovery waits on a first boot; the tool tree
#: and the pip install are the long hauls); the bar creeps to 95% on the
#: estimate and snaps full on truth. An estimate is honest as long as the bar
#: never claims DONE.
STEP_EST_S = {
    "find_new_medic": 300, "verify_target_pi5": 6, "carry_the_time": 6,
    "transfer_tool": 240, "transfer_firmware_cache": 90,
    # ~7GB over the cable: the toolchains alone are 5.2GB. By far the
    # longest step, and the one that makes the clone able to replicate.
    "carry_the_toolchain": 900,
    "install_dependencies": 300, "carry_touch_cure": 6,
    "copy_monitoring_db": 12, "copy_kin_roster": 6,
    "generate_fresh_identity": 12, "stamp_lineage": 6,
    "record_child_trust": 4, "configure_autostart": 12, "bake_recovery_bootorder": 15,
    "install_carried_packages": 120, "install_card_helper": 8, "ensure_ssh_keypair": 8,
    # Both of these were missing, so _run fell back to 30s: install_display_stack
    # unpacks 72 carried debs, so its bar sprinted to 95% in half a minute and
    # then sat there for minutes - the exact "looks frozen" the write ring was
    # rebuilt to avoid.
    "install_display_stack": 180, "copy_offline_maps": 30,
    "final_verification": 12,
    "restart_into_tool": 4,
}

#: Rough dd+config seconds for the write's fill estimate — the same model the
#: BIRTH imaging screen uses (time-based, capped at 95% until truth arrives).
EST_WRITE_S = 240.0


def _label(text, color="text_primary", bold=False, size="16sp"):
    lbl = Label(text=text, halign="left", valign="middle", bold=bold,
                font_size=theme.font_sp(size),
                color=theme.hex_to_rgba(theme.COLORS[color]))
    lbl.bind(size=lambda i, v: setattr(i, "text_size", v))
    return lbl


class _PowerGlyph(__import__("kivy.uix.widget", fromlist=["Widget"]).Widget):
    """A large drawn power symbol (circle + stem), pulsing — canvas-drawn so
    it can never render as a tofu box (the surgery-anim lesson)."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self._alpha = 1.0
        self._dir = -1
        self.bind(pos=self._draw, size=self._draw)
        from kivy.clock import Clock
        self._ev = Clock.schedule_interval(self._pulse, 1 / 20)

    def stop(self):
        if self._ev is not None:
            self._ev.cancel()
            self._ev = None

    def _pulse(self, _dt):
        self._alpha += self._dir * 0.04
        if self._alpha <= 0.35 or self._alpha >= 1.0:
            self._dir *= -1
            self._alpha = max(0.35, min(1.0, self._alpha))
        self._draw()

    def _draw(self, *_a):
        from kivy.graphics import Color, Line
        self.canvas.clear()
        cx, cy = self.center_x, self.center_y
        r = min(self.width, self.height) * 0.28
        with self.canvas:
            Color(0.18, 0.80, 0.35, self._alpha)
            # the ring, with a gap at the top for the stem
            Line(circle=(cx, cy, r, 30, 330), width=dp(4))
            Line(points=[cx, cy + r * 0.25, cx, cy + r * 1.25], width=dp(4))


def _small_btn(text):
    b = Button(text=text, size_hint_y=None, height=dp(40), font_size="14sp",
               background_normal="",
               background_color=theme.hex_to_rgba(theme.COLORS["surface"]),
               color=theme.hex_to_rgba(theme.COLORS["text_secondary"]))
    return b


#: stage -> (phase, step-in-phase, steps-in-phase, phase title). Banded into
#: TWO phases rather than one flat count, because a flat "step 4 of 10"
#: spanning a 4-minute card write and a 20-minute network copy is a worse lie
#: than no counter at all. The band also carries the fact the flow never said
#: out loud: writing the card is not the end.
_STAGE_POS = {
    "preflight": (1, 1, 6), "insert": (1, 2, 6), "name": (1, 3, 6),
    "password": (1, 4, 6), "wifi": (1, 5, 6), "write": (1, 6, 6),
    "written": (2, 1, 4), "power": (2, 2, 4), "cable": (2, 3, 4),
    "clone": (2, 4, 4),
}
_PHASE_NAME = {1: "Write the card", 2: "Copy the tool across"}


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
        # A write is as unabandonable as a clone: re-entering mid-write used
        # to rebuild the pre-flight page over a live dd (2026-10-04).
        if self._cloning or getattr(self, "_writing", False):
            return
        self._card_written = False
        self._retry_workflow = None
        self._show_stage_preflight()

    # -- PRE-FLIGHT: what you need, before anything is erased -----------------

    def _stage_header(self, key):
        """One line of orientation on every stage. Ten screens deep with no map
        was why every silent moment read as a failure."""
        pos = _STAGE_POS.get(key)
        if not pos:
            return
        phase, idx, total = pos
        # size_hint_y/height are set AFTER construction, as every other call
        # site in this file does - _label() does not take them, and passing
        # them in crashed the first stage of the flow outright.
        hdr = _label(
            tr("PHASE {phase} of 2 - {name}   .   step {idx} of {total}").format(
                phase=phase, name=tr(_PHASE_NAME[phase]), idx=idx, total=total),
            color="text_secondary", size="12sp")
        hdr.size_hint_y, hdr.height = None, dp(18)
        self.add_widget(hdr)

    def _show_stage_preflight(self):
        """Nobody should reach 'move the card to the new medic' holding a card
        with nowhere to put it (walkthrough 2026-08-26, Marnie). Say up front
        what this needs, with a graceful way back out if they don't have it."""
        self._clear()
        self._stage_header("preflight")
        title = _label(tr("Make another Node Medic"), bold=True, size="22sp")
        title.size_hint_y, title.height = None, dp(34)
        self.add_widget(title)
        # Parts, not products (operator, 2026-08-30 walkthrough): the person
        # is MAKING the second medic, so "a second Node Medic" was circular —
        # name the raw parts, plainly. "Ethernet cable" not "flat internet
        # cable" (not everybody's is flat). 8 GB matches this medic's own
        # board (verified live: Pi 5 Model B, 8 GB).
        # The FULL build list (operator, 2026-08-30: "give them the full
        # list of the build... spell it all out") — everything a person
        # needs to make a complete working medic, not just the imaging
        # parts. The Tracker is the new medic's GPS and clock (its
        # firstborn). It is NOT its mesh radio: the firstborn flashes the
        # GPS-only passthrough, and this flow sets up no radio for the clone
        # — said plainly since 2026-10-05 (readiness ledger #120); the
        # Tracker-as-RNode build for a clone is a bench job still to come.
        body = _label(tr(
            "This copies this Node Medic onto a second one. Before you start, "
            "have these to hand:\n\n"
            "  •  a Raspberry Pi 5 (8 GB) and its 5 V / 5 A power supply\n"
            "  •  a 5-inch touch screen (same as this one)\n"
            "  •  a memory (SD) card — 32 GB minimum, 64 GB is better\n"
            "  •  a memory card reader\n"
            "  •  an ethernet cable\n"
            "  •  a Heltec Wireless Tracker — the new medic's GPS and clock\n"
            "  •  a USB-A to USB-C cable, for the Tracker\n"
            "  •  a 915 MHz antenna and its u.FL-to-SMA pigtail\n\n"
            "Two halves: this medic writes the card (~5 min), then you move "
            "the card across and the two talk over the cable while the tool "
            "is copied (~20 min). You are NOT finished when the card is "
            "written. About half an hour in all, mostly waiting.\n\n"
            "The new medic's own mesh radio is not set up by this flow yet — "
            "its Tracker gives it position and time."),
            color="text_primary", size="15sp")
        # SCROLLED, and sized to the wrapped text rather than a fixed dp.
        # The hardcoded dp(330) silently clipped the TOP of the list the moment
        # the text grew - so the Raspberry Pi and its 5V/5A supply, the first
        # and most important items, vanished off-screen while every other line
        # stayed. A parts list that hides parts is worse than no parts list.
        grow_to_text(body)
        _scroll = ScrollView(size_hint=(1, 1))
        _scroll.add_widget(body)
        self.add_widget(_scroll)
        go = Button(text=tr("I have these — start  →"), size_hint_y=None,
                    height=dp(56), font_size="19sp", background_normal="",
                    background_color=theme.hex_to_rgba(theme.COLORS["green"]),
                    color=theme.hex_to_rgba(theme.COLORS["background"]))
        go.bind(on_release=lambda *_: self._show_stage_insert())
        self.add_widget(go)
        back = _small_btn(tr("Not yet — take me back"))
        back.bind(on_release=lambda *_: self._leave_home())
        self.add_widget(back)

    def _leave_home(self):
        try:
            from kivy.app import App
            App.get_running_app().switch_mode("home")
        except Exception:                                  # noqa: BLE001
            pass

    def sleep(self):
        # A card write is as unabandonable as a clone. Only _cloning was
        # checked, so a left-edge swipe during the write wiped the page while
        # dd carried on invisibly - and re-entering walked the operator back to
        # a SECOND write on the same card, two writers on one device.
        if self._cloning or getattr(self, "_writing", False):
            return
        self._clear()

    def handle_back(self):
        """True = swallowed. The back gesture must not leave a live write."""
        if self._cloning or getattr(self, "_writing", False):
            return True
        return False

    def handle_home(self):
        """Home during a write or a clone: say why, stay put. The wrapper only
        calls this when it exists — before 2026-10-04 Home left the page
        while dd carried on invisibly, and the write button came back dead
        until a restart. When nothing is running, Home is plain Home."""
        if self._cloning or getattr(self, "_writing", False):
            from ui.requirement_popup import requirement_popup
            requirement_popup(
                tr("The new medic's card is still being written. Leave it in "
                   "and wait for the finish page — then Home.")
                if getattr(self, "_writing", False) else
                tr("The clone is still running. Wait for the ladder to finish "
                   "— then Home."),
                tr("Not yet"), False)
            return
        from kivy.app import App
        App.get_running_app().switch_mode("home")

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
        # dismiss the on-screen keyboard — it outlived its stage and sat on
        # top of the ladder (seen live at the bench)
        try:
            from kivy.app import App
            kb = getattr(App.get_running_app(), "keyboard", None)
            if kb is not None:
                kb.hide()
        except Exception:                                  # noqa: BLE001
            pass
        self.clear_widgets()

    # -- stage 1: INSERT ------------------------------------------------------

    def _show_stage_insert(self):
        self._clear()
        self._stage_header("insert")
        title = _label(tr("Clone this Node Medic"), bold=True, size="22sp")
        title.size_hint_y, title.height = None, dp(34)
        self.add_widget(title)
        # "into Node Medic" was ambiguous in the one direction that matters:
        # the card goes into the READER, and the reader plugs into a USB port.
        # The only actual card slot on this machine holds the card this medic
        # is running from, so a person taking the old wording literally goes
        # looking for a slot and finds the one that must not be touched.
        body = _label(tr(
            "Put the new medic's memory card into the card reader,\n"
            "then plug the reader into any USB socket on this Node Medic.\n\n"
            "Do NOT open this Node Medic or touch the card inside it.\n"
            "Everything on the new card will be erased."),
            color="text_secondary", size="16sp")
        # Sized to the wrapped text, never a fixed dp: a hardcoded height
        # clips silently from the top the moment the copy grows.
        grow_to_text(body)
        self._insert_body = body
        self.add_widget(body)
        try:
            from ui.widgets.birth_anims import InsertSdAnim
            self._anim = InsertSdAnim()
            self.add_widget(self._anim)
            self._anim.start()
        except Exception:                                  # noqa: BLE001
            self._anim = None
        skip = _small_btn(tr("The new medic is already booted — skip to the clone"))
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
                if st["state"] != "one":
                    # "several" used to advance too, so the refusal that
                    # card_status() writes specifically to avoid guessing was
                    # bypassed - and the operator only learned about it after
                    # typing a name, a password twice and a WiFi password.
                    detail = st.get("detail") or ""
                    if st["state"] == "several" and detail:
                        Clock.schedule_once(
                            lambda _d, d=detail: self._say_insert(d), 0)
                    return
                Clock.schedule_once(
                    lambda _d, d=st.get("detail") or "": self._on_card_seen(
                        gen, d), 0)
            threading.Thread(target=work, daemon=True).start()

        self._card_ev = Clock.schedule_interval(tick, 1.5)
        tick(0)

    def _say_insert(self, detail):
        """Replace the insert page's body with a live message when several
        cards are attached. Stays on this page - the old flow greeted one of
        them and only refused minutes later, after three forms."""
        lbl = getattr(self, '_insert_body', None)
        if lbl is not None:
            lbl.text = (detail + "\n\n" + tr("Take the others out and leave only "
                                             "the new medic's card in the reader."))

    def _on_card_seen(self, gen, detail=''):
        if gen != self._stage_gen or self._card_greeted:
            return
        self._card_greeted = True
        # Name what was found. The machine already knew the size and model -
        # card_status() composes this very sentence - and the screen threw it
        # away, so the one page that mentions erasing never said WHAT.
        self._card_detail = detail
        _lbl = getattr(self, '_insert_body', None)
        if _lbl is not None and detail:
            _lbl.text = detail + "\n" + tr("It will be erased and written.")
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
        # 1.6s was not long enough to read which card had been found.
        self._advance_ev = Clock.schedule_once(_advance, 3.2)

    # -- stage 2: NAME --------------------------------------------------------

    def _show_stage_name(self, skip_mode=False):
        self._clear()
        self._stage_header("name")
        self._skip_mode = skip_mode
        title = _label(tr("Name the new medic"), bold=True, size="22sp")
        title.size_hint_y, title.height = None, dp(34)
        self.add_widget(title)
        body = _label(tr(
            "This is the name you will see when you look for it later. "
            "NodeMedic2 is a perfectly good answer.\n"
            "The name becomes its address on the cable and its place in the "
            "family line."), color="text_secondary", size="14sp")
        grow_to_text(body)
        self.add_widget(body)

        from kivy.uix.textinput import TextInput
        from ui.onscreen_keyboard import bind_field
        self.name_input = TextInput(text=self._name or "NodeMedic2",
                                    multiline=False,
                                    font_size=theme.font_sp("20sp"),
                                    size_hint_y=None, height=dp(52))
        bind_field(self.name_input)
        self.add_widget(self.name_input)
        # What the network will call it, shown as it is typed — and the refusal
        # for a name that reduces to nothing (Cyrillic, Japanese, emoji) HERE,
        # not after the password and Wi-Fi stages, where "Try again" could never
        # get back to the name (readiness ledger #122).
        self._name_note = _label("", color="text_secondary", size="13.5sp")
        self._name_note.size_hint_y, self._name_note.height = None, dp(24)
        self.add_widget(self._name_note)
        self.name_input.bind(text=lambda *_: self._refresh_name_note())
        self._refresh_name_note()

        nxt = Button(
            text=(tr("Find and clone →") if skip_mode else tr("Continue →")),
            size_hint_y=None, height=dp(56), font_size="20sp",
            background_normal="",
            background_color=theme.hex_to_rgba(theme.COLORS["accent"]),
            color=theme.hex_to_rgba(theme.COLORS["background"]))
        nxt.bind(on_release=lambda *_: self._name_continue())
        self.add_widget(nxt)

        back = _small_btn(tr("← Back"))
        back.bind(on_release=lambda *_: self._show_stage_insert())
        self.add_widget(back)
        from kivy.uix.widget import Widget
        self.add_widget(Widget())

    def _hostname_for(self, name):
        from provisioning import pi_imager
        return pi_imager.hostnameify(name or "")

    def _refresh_name_note(self):
        note = getattr(self, "_name_note", None)
        if note is None:
            return
        typed = (self.name_input.text or "").strip()
        host = self._hostname_for(typed)
        if typed and not host:
            note.text = tr("Use letters a-z or digits - this becomes its network name.")
            note.color = theme.hex_to_rgba(theme.COLORS["warning_yellow"])
        elif host and host != typed.lower():
            note.text = tr("On the network it will be called {host}").format(host=host)
            note.color = theme.hex_to_rgba(theme.COLORS["text_secondary"])
        else:
            note.text = ""

    def _name_continue(self):
        self._name = (self.name_input.text or "").strip()
        if not self._hostname_for(self._name):
            self._refresh_name_note()          # the refusal is already on screen
            return
        if self._skip_mode:
            self._show_stage_clone()
        else:
            self._show_stage_password()

    # -- stage 3: PASSWORD (typed + confirmed, never generated) ---------------

    def _show_stage_password(self):
        self._clear()
        self._stage_header("password")
        title = _label(tr("Create its password"), bold=True, size="22sp")
        title.size_hint_y, title.height = None, dp(34)
        self.add_widget(title)
        body = _label(tr(
            "The new medic never asks for this on its own screen - it starts "
            "straight into Node Medic. It is the password for its user 'pi' if "
            "a keyboard is ever plugged in. Write it down: a lost password "
            "means re-imaging the card."),
            color="text_secondary", size="14sp")
        grow_to_text(body)
        self.add_widget(body)

        from kivy.uix.textinput import TextInput
        from ui.onscreen_keyboard import bind_field
        self.pw1 = TextInput(hint_text=tr("Password"), password=True,
                             multiline=False,
                             font_size=theme.font_sp("20sp"),
                             size_hint_y=None, height=dp(52))
        self.pw2 = TextInput(hint_text=tr("Confirm password"), password=True,
                             multiline=False,
                             font_size=theme.font_sp("20sp"),
                             size_hint_y=None, height=dp(52))
        for f in (self.pw1, self.pw2):
            bind_field(f)
            f.bind(text=lambda *_: self._password_state())
            self.add_widget(f)

        # SHOW/HIDE (operator ask): mistyping both fields the same way is the
        # unrecoverable case — seeing the letters beats guessing. Same reveal
        # the node imaging screen uses for the same risk.
        self._pw_visible = False
        reveal = _small_btn(tr("Show the password"))

        def _toggle(*_a):
            self._pw_visible = not self._pw_visible
            for f in (self.pw1, self.pw2):
                f.password = not self._pw_visible
            reveal.text = (tr("Hide the password") if self._pw_visible
                           else tr("Show the password"))
        reveal.bind(on_release=_toggle)
        self.add_widget(reveal)

        # The button WALKS ITS COLOURS (operator spec): amber "Type a
        # password" → amber "Confirm the password" → green "Write the card →"
        # that lights only when the two match and pass the validator.
        self.pw_btn = Button(
            text=tr("Type a password"), size_hint_y=None, height=dp(56),
            font_size="20sp", background_normal="", disabled=True,
            background_color=theme.hex_to_rgba(theme.COLORS["amber"]),
            color=theme.hex_to_rgba(theme.COLORS["background"]))
        self.pw_btn.bind(on_release=lambda *_: self._password_continue())
        self.add_widget(self.pw_btn)

        self.pw_status = _label("", color="text_secondary", size="13sp")
        self.pw_status.size_hint_y, self.pw_status.height = None, dp(24)
        self.add_widget(self.pw_status)

        back = _small_btn(tr("← Back"))
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
            self.pw_btn.text = tr("Type a password")
            self.pw_btn.disabled = True
            self.pw_btn.background_color = theme.hex_to_rgba(
                theme.COLORS["amber"])
            self.pw_status.text = ""
            return
        ok, why = validate_new_password(p1, p2 if p2 else None)
        if not p2 or p1 != p2:
            self.pw_btn.text = tr("Confirm the password")
            self.pw_btn.disabled = True
            self.pw_btn.background_color = theme.hex_to_rgba(
                theme.COLORS["amber"])
            self.pw_status.text = ("" if not p2 else
                                   tr("The two passwords don't match yet."))
            return
        if not ok:
            self.pw_btn.text = tr("Confirm the password")
            self.pw_btn.disabled = True
            self.pw_btn.background_color = theme.hex_to_rgba(
                theme.COLORS["amber"])
            self.pw_status.text = why
            return
        self.pw_btn.text = tr("Next →")          # the Wi-Fi question comes first (#131)
        self.pw_btn.disabled = False
        self.pw_btn.background_color = theme.hex_to_rgba(theme.COLORS["green"])
        self.pw_status.text = tr("Passwords match.")

    def _password_continue(self):
        if self.pw_btn.disabled:
            return
        self._chosen_password = self.pw1.text
        self._show_stage_wifi()

    # -- stage 3b: the WiFi gift (typed, never silently read) ----------------
    # The first clone shipped an EMPTY WiFi password: NetworkManager refuses
    # to reveal secrets to the unprivileged UI, and the failure surfaced days
    # later as a clone that joined nothing ("no-secrets", HAWKEYE 2026-08-25).
    # Assume nothing: ASK the keeper.

    def _show_stage_wifi(self):
        self._clear()
        self._stage_header("wifi")
        title = _label(tr("Share your Wi-Fi with it?"), bold=True, size="22sp")
        title.size_hint_y, title.height = None, dp(34)
        self.add_widget(title)
        from workflows.mitosis_card import medic_wifi_credentials
        ssid = ""
        try:
            ssid = medic_wifi_credentials()[0]
        except Exception:                                  # noqa: BLE001
            pass
        body = _label(
            (tr("This medic is on '{ssid}'. Type that network's password and "
                "the new medic joins it by itself — or skip, and it lives on "
                "the cable.").format(ssid=ssid)) if ssid else
            tr("Type your Wi-Fi network's name and password, or skip and the "
               "new medic lives on the cable."),
            color="text_secondary", size="14sp")
        grow_to_text(body)
        self.add_widget(body)
        from kivy.uix.textinput import TextInput
        from ui.onscreen_keyboard import bind_field
        self._wifi_ssid_input = TextInput(text=ssid, hint_text=tr("Network name"),
                                          multiline=False,
                                          font_size=theme.font_sp("18sp"),
                                          size_hint_y=None, height=dp(48))
        bind_field(self._wifi_ssid_input)
        self.add_widget(self._wifi_ssid_input)
        self._wifi_psk_input = TextInput(hint_text=tr("Wi-Fi password"),
                                         password=True, multiline=False,
                                         font_size=theme.font_sp("18sp"),
                                         size_hint_y=None, height=dp(48))
        bind_field(self._wifi_psk_input)
        self.add_widget(self._wifi_psk_input)
        reveal = _small_btn(tr("Show the password"))

        def _toggle(*_a):
            self._wifi_psk_input.password = not self._wifi_psk_input.password
            reveal.text = (tr("Hide the password")
                           if not self._wifi_psk_input.password
                           else tr("Show the password"))
        reveal.bind(on_release=_toggle)
        self.add_widget(reveal)
        share = Button(text=tr("Share Wi-Fi and write the card →"),
                       size_hint_y=None, height=dp(56), font_size="19sp",
                       background_normal="",
                       background_color=theme.hex_to_rgba(theme.COLORS["green"]),
                       color=theme.hex_to_rgba(theme.COLORS["background"]))
        share.bind(on_release=lambda *_: self._wifi_continue(True))
        self.add_widget(share)
        self._wifi_warn = _label("", color="text_secondary", size="13sp")
        self._wifi_warn.size_hint_y, self._wifi_warn.height = None, dp(20)
        self.add_widget(self._wifi_warn)
        skip = _small_btn(tr("No Wi-Fi — write the card now →"))
        skip.bind(on_release=lambda *_: self._wifi_continue(False))
        self.add_widget(skip)
        back = _small_btn(tr("← Back"))                       # (#131)
        back.bind(on_release=lambda *_: self._show_stage_password())
        self.add_widget(back)
        from kivy.uix.widget import Widget
        self.add_widget(Widget())

    def _wifi_continue(self, share):
        ssid = (self._wifi_ssid_input.text or "").strip() if share else ""
        psk = (self._wifi_psk_input.text or "") if share else ""
        if share and ssid and not psk:
            # Was a hint_text change on a field the eye is not on, so the big
            # green button appeared to do nothing at all: tap, nothing, tap
            # harder, conclude it is broken. Say it where the person is looking.
            self._wifi_psk_input.hint_text = tr("Type the Wi-Fi password first")
            warn = getattr(self, "_wifi_warn", None)
            if warn is not None:
                warn.text = tr("Type the Wi-Fi password before you carry on - or "
                               "choose 'No Wi-Fi' below.")
                warn.color = theme.hex_to_rgba(theme.COLORS["amber"])
            return
        self._wifi = (ssid, psk)
        self._confirm_write(self._chosen_password)

    # -- the last word before anything is erased ------------------------------

    def _confirm_write(self, password):
        """Name the card and ask once. Until 2026-10-04 the flow erased
        whichever single disk was present with no word of which — including
        the vault's own key stick the setup wizard had told the keeper to
        plug in. That stick is refused outright; everything else is named."""
        from kivy.uix.popup import Popup
        from kivy.uix.label import Label
        from kivy.uix.button import Button
        from ui.requirement_popup import requirement_popup
        from provisioning import pi_imager
        from workflows import mitosis_card
        try:
            st = pi_imager.card_status()
        except Exception:                                  # noqa: BLE001
            st = {"state": "none", "path": "", "label": "", "detail": ""}
        if st.get("state") != "one" or not st.get("path"):
            requirement_popup(st.get("detail") or tr(
                "No card to write — put the new medic's card in the reader."),
                tr("Which card?"), False)
            return
        try:
            if mitosis_card.holds_vault_key(st["path"]):
                requirement_popup(tr(
                    "That drive holds this medic's vault key — it must not be "
                    "erased. Take it out and put the new medic's card in the "
                    "reader."), tr("Not that one"), False)
                return
        except Exception:                                  # noqa: BLE001
            pass
        missing = ""
        try:
            missing = mitosis_card.debs_missing()
        except Exception:                                  # noqa: BLE001
            missing = ""
        if missing:
            requirement_popup(missing, tr("Can't write yet"), False)
            return
        box = BoxLayout(orientation="vertical", spacing=dp(10), padding=dp(12))
        msg = Label(halign="center", valign="middle", markup=True, text=(
            tr("Erase [b]{label}[/b] at [b]{path}[/b] and write the new "
               "medic's card to it?").format(label=st.get("label") or tr("the USB card"),
                                             path=st["path"])
            + "\n\n" + tr("Everything on it will be lost. It cannot be the "
                          "medic's own storage — only a removable USB card is "
                          "allowed.")))
        msg.bind(size=lambda i, val: setattr(i, "text_size", val))
        box.add_widget(msg)
        row = BoxLayout(orientation="horizontal", size_hint_y=None, height=dp(52),
                        spacing=dp(8))
        popup = Popup(title=tr("Confirm — this erases the card"), content=box,
                      size_hint=(0.9, 0.6))
        cancel = Button(text=tr("Cancel"), background_normal="",
                        background_color=theme.hex_to_rgba(theme.COLORS["surface"]))
        cancel.bind(on_release=popup.dismiss)
        go = Button(text=tr("Erase & write"), bold=True, background_normal="",
                    background_color=theme.hex_to_rgba(theme.COLORS["red"]),
                    color=theme.hex_to_rgba(theme.COLORS["background"]))
        go.bind(on_release=lambda *_: (popup.dismiss(),
                                       self._show_stage_write(password)))
        row.add_widget(cancel)
        row.add_widget(go)
        box.add_widget(row)
        popup.open()

    # -- stage 4: WRITE (the BIRTH progress ring) ------------------------------

    def _show_stage_write(self, password):
        # RE-ENTRANCY GUARD. Two taps on the write button ~200ms apart used to
        # start two worker threads, both running xzcat|dd at the same card and
        # both using the same fixed config path and mountpoint. The first
        # thread's result was then discarded by the generation check, so the
        # screen reported one clean write while a second dd was still chewing
        # the card. The clone ladder has always had this guard (start() checks
        # run_btn.disabled); the card write never got one.
        if getattr(self, "_writing", False):
            return
        try:
            from kivy.app import App
            if App.get_running_app().flash_in_progress():
                return          # the node imager is already dd-ing something
        except Exception:                                  # noqa: BLE001
            pass
        self._writing = True
        self._clear()
        self._stage_header("write")
        gen = self._stage_gen
        self._write_gen = gen          # the write that may let go of the flag
        title = _label(tr("Writing the new medic's card"), bold=True, size="22sp")
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

        warn = _label(tr("Leave the card in until this finishes."),
                      color="text_secondary", size="13sp")
        warn.size_hint_y, warn.height = None, dp(24)
        self.add_widget(warn)
        from kivy.uix.widget import Widget
        self.add_widget(Widget())

        self._mark_activity(True)
        import time as _time
        t0 = _time.monotonic()

        # REAL progress, not a stopwatch. The kernel counts sectors written to
        # the card and the .xz footer says how large the image expands to, so
        # the ring can show what has actually been written.
        #
        # The old ring was elapsed/EST_WRITE_S capped at 95%: on a card slower
        # than the fixed 4-minute guess it parked at 95% and looked frozen -
        # precisely when someone is most tempted to pull the card out, which is
        # the one action that ruins it. The estimate is kept ONLY as a fallback
        # for a host that cannot report either number, so the ring never sits
        # dead at zero.
        #
        # The baseline is sampled HERE, before the writer starts, because this
        # counter is cumulative since boot and the card may already have been
        # written earlier in the session.
        _prog = {"dev": None, "base": None,
                 "total": pi_imager.uncompressed_image_size(
                     pi_imager.carried_image() or "")}
        # Choose the target ONCE, here, and hand the same one to the writer.
        # Two separate listings (one for the ring, one in the worker) could
        # disagree, leaving the ring sampling a device nobody was writing and
        # frozen at 0% for the whole write. Pinning it also closes a worse
        # hole: the worker used to re-list at write time, minutes after the
        # card was greeted, so a reader swapped for a USB stick while the
        # operator typed a name and two passwords would be silently erased.
        _disks = pi_imager.list_target_disks()
        self._target = _disks[0] if len(_disks) == 1 else None
        if self._target:
            _prog["dev"] = self._target["path"]
            _prog["base"] = pi_imager.device_bytes_written(_prog["dev"])
            try:
                self._target_serial = pi_imager.disk_serial(_prog["dev"])
            except Exception:                              # noqa: BLE001
                self._target_serial = ""


        def _tick(_dt):
            frac = None
            if _prog["dev"] and _prog["total"]:
                now = pi_imager.device_bytes_written(_prog["dev"])
                if now is None:
                    # The counter vanished - the card was pulled, or the reader
                    # re-enumerated. HOLD the last fraction rather than falling
                    # to 0%: resetting the ring and restarting the narration at
                    # the exact moment a write is doomed reads as "it started
                    # over", which is the opposite of the truth.
                    frac = _prog.get("last")
                    self._stage_lbl.text = tr("The card stopped responding - "
                                              "do not remove it")
                    if frac is not None:
                        self._ring.set_fraction(frac)
                    return
                written = now - (_prog["base"] or 0)
                # Held below 1.0 until the writer actually returns: the card is
                # not finished when the last byte lands, there is still a sync.
                frac = max(0.0, min(0.99, written / _prog["total"]))
                _prog["last"] = frac
            if frac is None:
                frac = min(0.95, (_time.monotonic() - t0) / EST_WRITE_S)
            self._ring.set_fraction(frac)
            self._stage_lbl.text = pi_imager.current_stage_label(frac)
        self._write_ev = Clock.schedule_interval(_tick, 1.0)

        def work():
            ok, msg = False, ""
            try:
                target = getattr(self, "_target", None)
                disks = pi_imager.list_target_disks()
                if target is None or len(disks) != 1:
                    msg = (tr("The card has gone - put it back in the reader.")
                           if not disks else
                           tr("There is more than one memory card or USB stick "
                              "plugged in. Take the others out and leave only "
                              "the new medic's card."))
                elif disks[0]["path"] != target["path"] or (
                        getattr(self, "_target_serial", "")
                        and pi_imager.disk_serial(target["path"])
                        != self._target_serial):
                    # The card was swapped between being greeted and being
                    # written. Refusing is the whole point: this is the window
                    # in which a reader can become a USB stick full of photos,
                    # and the old code simply wrote to whatever was there.
                    msg = tr("That is not the same card any more. Put the new "
                             "medic's card back in and start again.")
                else:
                    from workflows.mitosis_card import (image_medic_card,
                                                        verify_medic_card)
                    from workflows.mitosis_card import debs_missing
                    ok, msg, _pw = image_medic_card(
                        target["path"], self._name or "NodeMedic2",
                        password=password,
                        wifi=getattr(self, "_wifi", ("", "")),
                        deb_check=debs_missing)
                    # Read the card back before calling it done. The image can
                    # write perfectly while the configuration silently does
                    # not, and without this the first anyone knows is a medic
                    # that boots nameless and unreachable - by which time it is
                    # closed up and carried away.
                    if ok:
                        want = pi_imager.hostnameify(
                            self._name or "NodeMedic2")
                        try:
                            verdict, checks = verify_medic_card(
                                target["path"], want)
                        except Exception as e:             # noqa: BLE001
                            verdict = "unknown"
                            checks = [(tr("Not checked"), None,
                                       tr("could not read the card back "
                                          "({err})").format(err=e))]
                        self._verify_checks = checks
                        # THREE outcomes, not two. "could not read it back" is
                        # not the same as "it is wrong": condemning an
                        # unreadable card sent good cards back for a nine-minute
                        # rewrite, and the mirror error - reporting a check that
                        # never ran as a green pass - was the same lie the other
                        # way round. Only "bad" fails; "unknown" goes on, and
                        # says on the next screen that it is unproven.
                        if verdict == "bad":
                            ok = False
                            bad = ", ".join(c[0].lower()
                                            for c in checks if c[1] is False)
                            msg = tr("The card was written, but reading it back "
                                     "shows a problem with: {bad}. "
                                     "Write it again.").format(bad=bad)
            except Exception as e:                         # noqa: BLE001
                # Not the exception text: a raw Python error on a 5-inch
                # screen reads as "I have broken it" to the person holding it.
                print(f"[mitosis] card write exception: {e}")
                msg = tr("Something went wrong while writing the card. "
                         "Check the card is pushed fully into the reader, "
                         "then try again.")

            def done(_dt, g=gen):
                print(f"[mitosis] card write ok={ok}: {msg}")
                # The LATEST write always lets go of the flag and the activity
                # counter, generation or not: a stale flag left the page with
                # a dead write button until a restart (2026-10-04). An OLDER
                # write's thread — a newer one in flight — still touches
                # neither, the hazard _mark_activity exists for.
                if g == getattr(self, "_write_gen", g):
                    self._writing = False
                    self._mark_activity(False)
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
        self._writing = False
        self._clear()
        title = _label(tr("The card write failed"), bold=True, size="22sp",
                       color="red")
        title.size_hint_y, title.height = None, dp(34)
        self.add_widget(title)
        body = _label(msg, color="text_primary", size="15sp")
        body.valign = "top"
        grow_to_text(body)
        self.add_widget(body)
        again = Button(text=tr("Try again →"), size_hint_y=None, height=dp(56),
                       font_size="20sp", background_normal="",
                       background_color=theme.hex_to_rgba(theme.COLORS["accent"]),
                       color=theme.hex_to_rgba(theme.COLORS["background"]))
        # A name fault goes back to the NAME, not to the password it never
        # concerned (readiness ledger #122); everything else retries from the
        # password stage as before.
        back_to = (self._show_stage_name if "hostname" in (msg or "")
                   else self._show_stage_password)
        again.bind(on_release=lambda *_: back_to())
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
                app.begin_activity(tr("CLONE in progress — keep everything "
                                      "plugged in, don't power off"))
            else:
                app.end_activity()
        except Exception:                                  # noqa: BLE001
            pass

    # -- stage 5: the hand-off, ANIMATED and AUTOMATED ------------------------
    # Operator, live at the bench (twice): "we need to automate as much of
    # this process as possible". After the password there are ZERO taps:
    # the medic watches its own reader for the card leaving, then starts
    # hunting for the new medic IMMEDIATELY — discovery polls for 5 minutes,
    # which is exactly the physical work's duration. The operator powers and
    # cables while the first ladder row is already searching.

    def _show_stage_written(self):
        self._clear()
        self._stage_header("written")
        gen = self._stage_gen
        title = _label(tr("Card written ✓ — move it to the new medic"),
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
        # Say WHAT was checked, in terms of what it means for the operator
        # rather than the field names. Four short true lines beat one
        # unfalsifiable "verified".
        for _lbl, _ok, _detail in getattr(self, "_verify_checks", []):
            # THREE states. None means the check never ran, which is not a
            # pass: showing an unrun check in green under a green tick told
            # the operator a card was proven when nothing had been read.
            mark = "OK   " if _ok else ("?    " if _ok is None else "X    ")
            tone = "green" if _ok else "amber"
            row = _label(mark + f"{tr(_lbl)} - {_detail}", color=tone, size="13sp")
            row.size_hint_y, row.height = None, dp(19)
            self.add_widget(row)

        hint = _label(tr("Make sure the new medic is switched OFF first, then push "
                         "the little card into its slot until it clicks. Node "
                         "Medic sees the card leave here and carries on by itself."),
                      color="text_secondary", size="14sp")
        # Sized to the wrapped text, never a fixed dp: a hardcoded height
        # clips silently from the top the moment the copy grows.
        grow_to_text(hint)
        self.add_widget(hint)

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

    # -- the POWER page: instruct FIRST (words + picture), then one tap -------
    # The medic cannot SEE power arrive, so this page keeps a single button;
    # everything it CAN see (card leaving, cable arriving, the machine
    # answering) advances by itself.

    def _show_stage_power(self):
        self._clear()
        self._stage_header("power")
        title = _label(tr("Power the new medic on"), bold=True, size="22sp")
        title.size_hint_y, title.height = None, dp(34)
        self.add_widget(title)
        self._anim = _PowerGlyph()
        self.add_widget(self._anim)
        # "press BOOT on its battery pack" named an object that appears NOWHERE
        # in the parts list, so a person who bought exactly what they were told
        # to buy went hunting for a thing they do not own. It is now framed as
        # the alternative it actually is, and the jargon is described rather
        # than named.
        body = _label(tr(
            "Plug the power supply into the new medic. It should start by "
            "itself - a small green light flickering means it is working.\n\n"
            "If you are using a battery pack instead of a wall plug, press the "
            "button marked BOOT on the pack once: a fresh pack sleeps until "
            "it is asked for power.\n\n"
            "Its OWN screen will stay dark, or show start-up text, until later "
            "- that's normal. Keep watching THIS screen."),
            color="text_primary", size="16sp")
        # Sized to the wrapped text, never a fixed dp: a hardcoded height
        # clips silently from the top the moment the copy grows.
        grow_to_text(body)
        self.add_widget(body)
        stuck = _small_btn(tr("Nothing is happening"))
        stuck.bind(on_release=lambda *_: self._show_power_help())
        self.add_widget(stuck)
        nxt = Button(text=tr("It has power - next"), size_hint_y=None,
                     height=dp(56), font_size="20sp", background_normal="",
                     background_color=theme.hex_to_rgba(theme.COLORS["accent"]),
                     color=theme.hex_to_rgba(theme.COLORS["background"]))
        nxt.bind(on_release=lambda *_: self._show_stage_cable())
        self.add_widget(nxt)

    # -- the CABLE page: instruct FIRST, then the medic WATCHES for copper ----

    def _show_power_help(self):
        """The way out of the only screen in the flow with no way out. Its one
        button asserted "It has power" - which is exactly what has NOT happened
        for the person reading it."""
        self._clear()
        title = _label(tr("It won't start"), bold=True, size="22sp")
        title.size_hint_y, title.height = None, dp(34)
        self.add_widget(title)
        self.add_widget(_label(tr(
            "Work down this list - it is nearly always the first one.\n\n"
            "1.  Is the power supply pushed all the way in, at BOTH ends?\n\n"
            "2.  Is it the 5 V / 5 A supply that came with the Pi? A phone "
            "charger or a smaller supply will not start a Pi 5.\n\n"
            "3.  Is the memory card pushed in until it clicks? A card that is "
            "not seated looks exactly like no power at all.\n\n"
            "4.  On a battery pack, press the button marked BOOT once.\n\n"
            "If a small green light flickers even briefly, it IS starting - "
            "give it a full minute before deciding it is dead."),
            color="text_primary", size="15sp"))
        back = Button(text=tr("It's working now - carry on  →"), size_hint_y=None,
                      height=dp(52), font_size="17sp", background_normal="",
                      background_color=theme.hex_to_rgba(theme.COLORS["green"]),
                      color=theme.hex_to_rgba(theme.COLORS["background"]))
        back.bind(on_release=lambda *_: self._show_stage_cable())
        self.add_widget(back)
        again = _small_btn(tr("Back"))
        again.bind(on_release=lambda *_: self._show_stage_power())
        self.add_widget(again)

    def _show_stage_cable(self):
        self._clear()
        self._stage_header("cable")
        gen = self._stage_gen
        title = _label(tr("Connect the network cable"), bold=True, size="22sp")
        title.size_hint_y, title.height = None, dp(34)
        self.add_widget(title)
        try:
            from ui.widgets.birth_anims import ProvisionOverCableAnim
            self._anim = ProvisionOverCableAnim()
            self.add_widget(self._anim)
            self._anim.start()
        except Exception:                                  # noqa: BLE001
            self._anim = None
        body = _label(tr(
            "Plug the ethernet cable - the wide plug that clicks in - "
            "into the network socket on BOTH medics. (Not the little USB "
            "shapes; the wider one.) Node Medic sees the cable arrive and "
            "carries on by itself. The new medic's own screen may still be "
            "dark or showing text - that's normal."),
            color="text_primary", size="15sp")
        # Sized to the wrapped text, never a fixed dp: a hardcoded height
        # clips silently from the top the moment the copy grows.
        grow_to_text(body)
        self.add_widget(body)
        wifi = _small_btn(tr("No cable - it joins my Wi-Fi instead"))
        wifi.bind(on_release=lambda *_: self._show_stage_clone(auto=True))
        self.add_widget(wifi)

        # WATCH FOR COPPER: the wired port's carrier line flips to 1 the
        # moment a live cable connects both ends - readable with no
        # privileges, and the honest signal that the instruction was done.
        #
        # It must watch for the TRANSITION, not the level. A medic already
        # sitting on the household LAN has carrier == 1 before this page is
        # even drawn, so the level test fired instantly: the instruction
        # flashed past unread and the clone began with nothing plugged in.
        # Ports already up at entry are remembered and ignored; if EVERY port
        # is already up there is nothing to detect, so the page simply waits
        # for the button - which is honest, rather than guessing.
        def _live_ports():
            import os as _os
            up = set()
            try:
                for ifc in _os.listdir("/sys/class/net"):
                    if not ifc.startswith(("eth", "end", "enp", "eno")):
                        continue
                    try:
                        with open(f"/sys/class/net/{ifc}/carrier") as fh:
                            if fh.read().strip() == "1":
                                up.add(ifc)
                    except Exception:                      # noqa: BLE001
                        pass
            except Exception:                              # noqa: BLE001
                pass
            return up

        self._cable_already_up = _live_ports()
        if self._cable_already_up:
            # Both medics may already share a switch or a desk LAN: with every
            # wired port up before this page was drawn there is no transition
            # to see, and the page had no way forward (readiness ledger #117).
            note = _label(tr("This medic's network socket is already in use - if "
                             "the new medic is on the same network, carry on."),
                          color="text_secondary", size="14sp")
            grow_to_text(note)
            self.add_widget(note, index=1)            # above the WiFi button
            go = Button(text=tr("It's plugged in - carry on →"), size_hint_y=None,
                        height=dp(52), font_size="18sp", background_normal="",
                        background_color=theme.hex_to_rgba(theme.COLORS["accent"]),
                        color=theme.hex_to_rgba(theme.COLORS["background"]))
            go.bind(on_release=lambda *_: self._on_cable_seen(gen))
            self.add_widget(go, index=1)

        def tick(_dt):
            def work():
                try:
                    fresh = _live_ports() - self._cable_already_up
                    if fresh:
                        Clock.schedule_once(
                            lambda _d: self._on_cable_seen(gen), 0)
                        return
                except Exception:                          # noqa: BLE001
                    pass
            threading.Thread(target=work, daemon=True).start()
        self._card_ev = Clock.schedule_interval(tick, 1.5)
        tick(0)

    def _on_cable_seen(self, gen):
        if gen != self._stage_gen:
            return
        if self._card_ev is not None:
            self._card_ev.cancel()
            self._card_ev = None
        self._show_stage_clone(auto=True)

    # -- stage 6: CLONE --------------------------------------------------------

    def _show_stage_clone(self, auto=False):
        self._clear()
        self._stage_header("clone")
        if auto:
            head = _label(tr(
                "Node Medic is doing the rest itself. First boot takes up "
                "to 5 minutes - the search waits that long."),
                color="text_primary", size="15sp")
            head.size_hint_y, head.height = None, dp(48)
            self.add_widget(head)
        self.run_btn = Button(
            text=tr("Clone onto the new medic"), size_hint_y=None,
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
        # Under the ladder, not above it: the rows are the thing being watched.
        _wait = self._while_you_wait()
        # Sized to the wrapped text, never a fixed dp: a hardcoded height
        # clips silently from the top the moment the copy grows.
        grow_to_text(_wait)
        self.add_widget(_wait)

        self._rows = {}
        self._build_rows()
        if auto:
            # ZERO TAPS: the hunt begins now, while the operator's hands are
            # full of hardware. The button becomes the status/retry surface.
            self.run_btn.text = tr("Searching for the new medic…")
            Clock.schedule_once(lambda _d: self.start(auto=True), 0.2)

    def _while_you_wait(self):
        """The ladder runs for ~20 minutes with nothing for the operator to do,
        while the preflight has already made them buy a Tracker, a USB cable,
        an aerial and a pigtail that the flow then never mentions again. This
        is real preparation, it closes that loop, and it hands them straight
        into the new medic's first job."""
        return _label(tr(
            "[b]While this runs:[/b] get the Tracker, its USB cable, the aerial "
            "and the little pigtail lead to hand. Screw the aerial onto the "
            "pigtail and clip the pigtail onto the Tracker. Never power the "
            "Tracker up without its aerial attached. The new medic will ask "
            "for it shortly after it wakes."),
            color="text_secondary", size="13sp")

    def _build_rows(self, steps=None):
        titles = dict(STEP_TITLES)
        pairs = ([(n, titles.get(n, n)) for n, _f in steps]
                 if steps else STEP_TITLES)
        self.list.clear_widgets()
        self._rows = {}
        from kivy.uix.progressbar import ProgressBar
        for name, title in pairs:
            row = BoxLayout(orientation="vertical", size_hint_y=None,
                            height=dp(52))
            top = BoxLayout(spacing=dp(8), size_hint_y=None, height=dp(40))
            status = _label("-", color="text_secondary", bold=True, size="18sp")
            status.size_hint_x = None
            status.width = dp(34)
            text = _label(tr(title), color="text_secondary")
            top.add_widget(status)
            top.add_widget(text)
            row.add_widget(top)
            bar = ProgressBar(max=1.0, value=0.0, size_hint_y=None,
                              height=dp(8))
            row.add_widget(bar)
            self.list.add_widget(row)
            self._rows[name] = (status, text, bar)

    def _set_row(self, name, mark, color, detail=None):
        pair = self._rows.get(name)
        if not pair:
            return
        status, text, bar = pair
        if color == "green":
            bar.value = 1.0
        elif color == "red":
            bar.value = 1.0
        status.text = mark
        status.color = theme.hex_to_rgba(theme.COLORS[color])
        text.color = theme.hex_to_rgba(theme.COLORS["text_primary"])
        if detail:
            base = tr(dict(STEP_TITLES).get(name, name))
            text.text = f"{base}\n[{detail}]"
            top = text.parent
            outer = top.parent if top is not None else None
            if color == "red" and top is not None and outer is not None:
                def _grow(_i, ts, t=top, o=outer):
                    h = max(dp(40), ts[1] + dp(10))
                    t.height = h
                    o.height = h + dp(12)
                text.bind(texture_size=_grow)

    # -- run -------------------------------------------------------------------

    def start(self, auto=False):
        if self.run_btn.disabled:
            return
        orig_label = self.run_btn.text
        self.run_btn.disabled = True
        self.run_btn.text = (tr("Searching for the new medic\u2026") if auto
                             else tr("Cloning\u2026"))
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
                              getattr(workflow, "title", tr("Heads up")),
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
            elapsed = _time.monotonic() - self._step_t0
            m, sec = divmod(int(elapsed), 60)
            for name, _f in workflow.steps:
                if name not in done:
                    self._set_row(name, ">", "accent", f"{m}m {sec:02d}s")
                    pair = self._rows.get(name)
                    if pair:
                        est = STEP_EST_S.get(name, 30)
                        pair[2].value = min(0.95, elapsed / est)
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
            self._set_row(result.name, "s", "text_secondary", tr("skipped"))
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
        # Judge each step by its LAST result, not by every result ever
        # recorded. run_all appends and never clears, so after a retry the list
        # still holds the original failure - and a clone that had genuinely
        # completed reported failure, offered "Retry from: <a step that already
        # succeeded>", and re-running it did nothing and said the same again.
        # find_new_medic failing once (a first boot slower than five minutes)
        # is the case the retry mechanism exists for, so this was on the
        # likeliest path through the flow.
        latest = {}
        for r in results:
            latest[r.name] = r
        final = list(latest.values())
        seen = set(latest)
        ok = bool(final) and all(r.success or r.skipped for r in final)
        if ok and len(seen) >= len(self._rows):
            # NOT re-enabled on success: the finished green button was still
            # live, so tapping "Clone finished" started a second complete
            # clone, five-minute search and all.
            self.run_btn.disabled = True
            self.run_btn.text = tr("Clone finished — every step verified")
            self.run_btn.background_color = theme.hex_to_rgba(theme.COLORS["green"])
            # It worked / here is what you have / here is what to do next.
            # The old text said only that it was restarting - it never said
            # what had been made, and it never mentioned the radio, cable,
            # antenna and pigtail the parts list made the operator buy and
            # then never spoke of again.
            name = self._name or tr("The new medic")
            ident = getattr(workflow, "fresh_identity_hash", "") or ""
            done = _label(
                tr("[b]Done - you have made a Node Medic.[/b]\n\n"
                   "{name} is restarting now. It has its own place on the mesh"
                   "{ident} - a new one, not a copy of this medic's - along "
                   "with the whole tool, the offline maps, the firmware store "
                   "and the list of your other nodes.\n\n"
                   "[b]Next, on the NEW medic's own screen[/b] (about a minute):\n"
                   "1.  It starts straight into Node Medic and walks you through "
                   "its own setup - no login, nothing to type from here.\n"
                   "2.  Then it fits its GPS - that is the Tracker, its USB "
                   "cable, the aerial and the little pigtail lead from the list "
                   "at the start. Its own mesh radio is a separate job, still "
                   "to come.\n\n"
                   "Until its screen appears it may show start-up text. That is "
                   "normal, and there is nothing left to do on this screen.").format(
                       name=name, ident=(f" ({ident[:8]})" if ident else "")),
                color="text_primary", size="15sp")
            done.markup = True
            grow_to_text(done)
            # Into the SCROLLED list, not onto the screen. As a sibling of the
            # ladder it had to share 480px with eighteen rows, so the closing
            # message - the part saying what you now have and what to do next -
            # was pushed off the bottom exactly when it mattered.
            try:
                self.list.add_widget(done)
                self.scroll.scroll_y = 0          # bring the ending into view
            except Exception:                                  # noqa: BLE001
                self.add_widget(done)
            home = _small_btn(tr("← Back to home"))
            home.bind(on_release=lambda *_: self._leave_home())
            self.add_widget(home)
        else:
            self.run_btn.disabled = False
            failed = next((r for r in final
                           if not r.success and not r.skipped), None)
            self._retry_workflow = workflow
            titles = dict(STEP_TITLES)
            step_name = tr(titles.get(failed.name, failed.name)) if failed else "?"
            self.run_btn.text = tr("Retry from: {step}").format(step=step_name)
            self.run_btn.background_color = theme.hex_to_rgba(theme.COLORS["red"])
