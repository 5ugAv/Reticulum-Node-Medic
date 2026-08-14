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
from ui.i18n import tr  # i18n: wrapped — guided-birth screen labels/buttons
from monitor.formatting import format_age
from ui.birth_guide_flow import (ANTENNA_STEP, BIRTH_PATHS, BLUETOOTH_STEP,
                                 LOCATION_SHARE_STEP, guide_steps)
from ui.widgets.wizard_step import WizardStep
from ui.widgets.birth_anims import (ConnectAntennaAnim, ConnectBoardAnim,
                                    InsertSdAnim, InsertSdIntoPiAnim,
                                    ProvisionAnim, ConnectPiAnim,
                                    SdHandoverAnim,
    DisconnectBoardAnim, RadioToPiAnim, ProvisionOverCableAnim,
)

#: Animation key (from ui.birth_guide_flow) -> the widget class that draws it.
_ANIMS = {"connect_antenna": ConnectAntennaAnim, "connect_board": ConnectBoardAnim,
          # the connect scene run BACKWARDS — the radio leaving the medic. Using
          # connect_board here drew the operator doing the opposite of the words.
          "disconnect_board": DisconnectBoardAnim,
          # the radio meeting the PI, not the medic. Deliberately points at no
          # socket: that is a per-board measurement, not a guess.
          "radio_to_pi": RadioToPiAnim,
          # NOT "provision": that one draws a radio board sending radio
          # waves. This step is a Pi on the end of a cable.
          "provision_cable": ProvisionOverCableAnim,
          "connect_pi": ConnectPiAnim,
          "insert_sd": InsertSdAnim, "insert_sd_pi": InsertSdIntoPiAnim,
          # the written card leaving the medic's reader and going home into
          # THIS operator's Pi — board-aware, see ui.pi_sd_geometry
          "sd_handover": SdHandoverAnim,
          "provision": ProvisionAnim}

#: Animations that draw a specific Raspberry Pi and so must be told which one.
_PI_ANIMS = (InsertSdIntoPiAnim, SdHandoverAnim, ConnectPiAnim,
             RadioToPiAnim, ProvisionOverCableAnim)


def _line(text, size, color="text_primary", bold=False, h=None):
    """A left-aligned line of copy at DESIGN *size*, routed through the type
    scale (ui/theme.py) like every other screen.

    *h* is a FLOOR, not a lid. These screens pin heights that were measured
    against the old, smaller font — several of them hold three or four wrapped
    lines — so enlarging the text inside a fixed row is exactly how the last
    type-scale attempt clipped its content. The row therefore grows past *h*
    whenever the wrapped text needs more, and never shrinks below it.
    """
    lbl = Label(text=text, font_size=theme.font_sp(size), bold=bold,
                halign="left", valign="middle",
                color=theme.hex_to_rgba(theme.COLORS[color]))
    if h is not None:
        floor = dp(max(h, theme.line_dp(size)))
        lbl.size_hint_y = None
        lbl.height = floor

        def _grow(*_a):
            lbl.text_size = (lbl.width, None)
            lbl.texture_update()
            lbl.height = max(floor, lbl.texture_size[1])
        lbl.bind(width=_grow, text=_grow)
    else:
        lbl.bind(size=lambda i, v: setattr(i, "text_size", v))
    return lbl


class BirthGuideScreen(BoxLayout):
    """The step-by-step birth walkthrough. ``on_complete(path)`` fires when the
    physical-prep steps are done, to hand off to the real BIRTH flow."""

    def __init__(self, on_complete=None, on_navigate=None, heard_fn=None,
                 adopt_air_fn=None, **kwargs):
        kwargs.setdefault("orientation", "vertical")
        super().__init__(**kwargs)
        self._on_complete = on_complete
        self._on_navigate = on_navigate       # (screen_name) -> switch to a screen
        self._heard_fn = heard_fn             # () -> [candidate dicts heard over mesh]
        self._adopt_air_fn = adopt_air_fn     # (key,name,type,board,fw) -> enroll kin
        self._path = None
        self._i = 0
        self._current = None
        self._node_name = ""
        self.reset()

    def reset(self):
        """Re-entered: back to the antenna-first landing (attach the antenna BEFORE
        the next screen powers the board over USB), which leads to the detect-first
        landing (plug a node in; the medic decides BIRTH vs ADOPT)."""
        self._stop_current()
        self._path = None
        self._i = 0
        self._node_name = ""
        self._pair_checked = False
        self._board_key = ""
        self._pi_key = ""
        # THE SHARING ANSWER BELONGS TO ONE NODE, and starts unanswered.
        #
        # Same rule as the map-stamped position (birth_screen._prefill_location,
        # which leaked the previous node's coordinates into the next birth until
        # 2026-08-01) — but worse if it leaked, because this one is a decision
        # rather than a fact: a second node would start announcing its position
        # on the strength of a yes given about a different node.
        from monitor import location_share
        self._share_location = location_share.HIDDEN
        self._share_asked = False
        # And the Bluetooth answer, for the same reason: OFF is the resting
        # end, and one node's yes must never become the next node's radio.
        self._bluetooth_on = False
        self._bt_asked = False
        # A new walkthrough owes nothing to the last one. A stale return point
        # would send a screen finishing LATE — a card write that outlived the
        # operator's patience, say — back into a walkthrough that has since
        # started over, landing them at a step for a different node.
        self._resume_at = None
        self._radio_verified = False
        self._gate_warning = ""
        # The cable link belongs to ONE node. Carried over, the next
        # walkthrough's gate would open on the PREVIOUS node still answering,
        # and the medic would provision the wrong Pi.
        self._stop_node_poll()
        self._node_addr = ""
        # A fresh walkthrough starts with the radio going ON, so the disconnect
        # warning must come back — otherwise one guided birth would silence it
        # for the rest of the session.
        self._expect_board_absence(False)
        # Never cleared before: after ONE board read it stayed True, so
        # _on_pi_detected returned early for the rest of the session and the Pi
        # auto-detect was dead (audit, 2026-08-03).
        self._reading_pending = False
        self._pi_art_key = ""
        # The antenna landing exists because plugging a RADIO in unpowered-
        # without-antenna can destroy it. A Raspberry Pi has no antenna and no
        # radio attached yet, so opening a Pi build with "attach the antenna"
        # is an instruction about a board the operator isn't holding (walkthrough
        # 2026-08-02). For the Pi path the same warning is carried on the step
        # where the radio actually appears.
        if self._pi_present_without_radio():
            self._render_detect()
        else:
            self._render_antenna()

    @staticmethod
    def _pi_present_without_radio():
        """A Pi is on USB and no radio work board is. Best-effort, never raises."""
        try:
            import subprocess
            from provisioning import pi_usbboot
            from ui.hw_factories import local_board_ports
            out = subprocess.run(["lsusb"], capture_output=True, text=True,
                                 timeout=8).stdout
            pi = pi_usbboot.classify(out).state != pi_usbboot.ABSENT
            return bool(pi) and not local_board_ports()
        except Exception:
            return False

    # -- antenna-first landing (before any board is powered) ---------------
    def _render_antenna(self):
        """The very first BIRTH screen: attach an antenna to the radio board, with
        the no-antenna damage warning — shown BEFORE detect, since plugging the
        board into USB there powers it, and powering a radio with no antenna can
        fry it. One landing covers every path (all reach detect)."""
        self._stop_current()
        self.clear_widgets()
        self._back_action = None          # antenna landing is the root -> home
        anim = ConnectAntennaAnim()
        step = WizardStep(
            index=0, total=1, title=ANTENNA_STEP["title"], body=ANTENNA_STEP["body"],
            anim=anim, hint=ANTENNA_STEP.get("hint", ""),
            warning=ANTENNA_STEP["warning"], next_text=tr("Antenna on  →"),
            on_next=self._render_detect,
            on_back=lambda: self._on_navigate and self._on_navigate("home"))
        self.add_widget(step)
        self._current = step
        step.start()

    def _back_row(self, label=None, height=44):
        """A VISIBLE way out of any screen that isn't a guided step.

        *label* renames the control where "Back" would understate it — the
        hardware-confirmation screen's way out is "Not right — change", which is
        the same journey and must stay the SAME control, so every screen keeps
        exactly one exit and the guard that checks for one keeps working.

        handle_back() and _back_action have existed all along, but they were
        reachable only by a left-edge SWIPE. An invisible affordance is no
        affordance: the operator hit the "Which Raspberry Pi is this?" chooser
        twice and reported it as a trap both times, because six options and no
        exit is exactly what it looks like ("this screen will not allow me to go
        back", 2026-08-06; "this screen is still a trap", 2026-08-07). It had to
        be recovered by restarting the UI over SSH — which a field operator
        cannot do.

        Returns None when there is nowhere to go (a terminal outcome), so the
        caller adds nothing rather than a button that lies.
        """
        if not callable(getattr(self, "_back_action", None)):
            return None
        row = BoxLayout(orientation="horizontal", size_hint_y=None,
                        height=dp(height))
        b = Button(text=tr(label) if label else tr("←  Back"),
                   size_hint=(None, 1), width=dp(220 if label else 150),
                   font_size="16sp", bold=True, background_normal="",
                   background_color=theme.hex_to_rgba(theme.COLORS["surface"]),
                   color=theme.hex_to_rgba(theme.COLORS["accent"]))
        b.bind(on_release=lambda *_: self.handle_back())
        row.add_widget(b)
        row.add_widget(BoxLayout())            # push it left, matching the steps
        return row

    def handle_back(self):
        """Left-edge swipe: step back ONE page within the flow. Returns True if it
        stepped back; False at the root (the detect landing) so the app goes home.
        Each _render_* sets ``self._back_action`` to its previous page (or None)."""
        action = getattr(self, "_back_action", None)
        if callable(action):
            action()
            return True
        return False

    # -- detect-first landing ---------------------------------------------
    def _render_detect(self):
        """Plug a node in; the medic reads it and routes to ADOPT (already ours)
        or BIRTH (fresh/foreign). 'Choose manually' opens the full chooser (also
        the home for Pi + Mitosis, which aren't plug-in-a-radio-board cases)."""
        self._stop_current()
        self.clear_widgets()
        self._back_action = self._render_antenna   # back -> the antenna landing
        anim = ConnectBoardAnim()
        step = WizardStep(
            index=0, total=1, title=tr("Connect your node"),
            # The medic speaks as "Node Medic" everywhere else; "I'll detect
            # it" was the one first-person sentence in the birth flow. And
            # what it detects is this screen's job, not the operator's.
            # The old body said "plug the node in" and promised routing —
            # naming nothing (operator, 2026-08-14): say WHAT gets plugged.
            body=tr("Plug the radio node (LoRa32) into Node Medic with a "
                    "USB data cable."),
            anim=anim,
            hint=tr("Use a DATA USB cable — a charge-only cable won't be seen."),
            next_text=tr("Choose manually  →"), on_next=self._render_intro,
            on_back=self._render_antenna)
        self.add_widget(step)
        self._current = step
        step.start()
        # 'Choose manually' is an ADVANCED escape, not the primary action (the
        # primary path is just plugging a node in) — muted khaki-green so it doesn't
        # invite like the usual green Next.
        step.next_btn.background_color = theme.hex_to_rgba("#78866b")
        step.next_btn.color = theme.hex_to_rgba("#f0f0f0")
        self._start_board_poll(anim, on_present=self._on_detect)
        self._start_detect_pi_poll()

    def _start_detect_pi_poll(self):
        """Watch for a Raspberry Pi alongside the serial-board poll.

        Without this the detect screen is blind to a Pi — it enumerates serial
        ports and a Pi has no tty — so the operator had to reach for "Choose
        manually", which is meant to be the ADVANCED escape, not the way anyone
        builds a Pi (walkthrough 2026-08-02).
        """
        from kivy.clock import Clock
        self._stop_detect_pi_poll()

        def tick(_dt):
            import threading

            def work():
                found = False
                try:
                    import subprocess
                    from provisioning import pi_usbboot
                    out = subprocess.run(["lsusb"], capture_output=True,
                                         text=True, timeout=8).stdout
                    st = pi_usbboot.classify(out)
                    found = st.state != pi_usbboot.ABSENT
                    # Remember the SoC while we can still see it: once rpiboot
                    # runs, the Pi reports only a mass-storage id and the model
                    # is no longer knowable.
                    if st.usb_id and not getattr(self, "_pi_art_key", ""):
                        self._pi_art_key = pi_usbboot.art_key(st.usb_id)
                except Exception:
                    found = False
                if found:
                    Clock.schedule_once(lambda _d: self._on_pi_detected(), 0)
            threading.Thread(target=work, daemon=True).start()

        self._pi_poll = Clock.schedule_interval(tick, 1.5)
        tick(0)

    def _stop_detect_pi_poll(self):
        poll = getattr(self, "_pi_poll", None)
        if poll is not None:
            try:
                poll.cancel()
            except Exception:
                pass
            self._pi_poll = None

    def _on_pi_detected(self):
        """A Pi is plugged in — take the Pi path without asking.

        Unless a radio board is here too. Both polls run on this screen, and for
        a Pi+radio build BOTH fire: the operator got the name step, had it
        whisked away 1.6s later by the board read, then got dropped back on the
        name step (walkthrough, 2026-08-02). Reading the board wins, because it
        is the step with a deadline — it resets the board and must not be
        interrupted — and naming can be asked at any point afterwards.
        """
        self._stop_detect_pi_poll()
        if getattr(self, "_reading_pending", False):
            return                        # a board read already owns the flow
        try:
            from ui.hw_factories import local_board_ports
            if local_board_ports():
                return                    # a board is here; let it be read first
        except Exception:
            pass
        self._stop_board_poll()
        self._path = "pi"
        self._i = 0
        self._render_name()

    def _on_detect(self, anim):
        """A board appeared — celebrate, then read + classify it off-thread."""
        self._stop_board_poll()
        # The Pi watch must stop too, or it fires DURING the read and replaces
        # the "Reading the board…" screen with the name step.
        self._stop_detect_pi_poll()
        if hasattr(anim, "mark_connected"):
            anim.mark_connected()
        from kivy.clock import Clock
        # Tokened: navigating away (a manual Back, or any other route taking
        # over) cancels this pending render instead of letting it land on top of
        # whatever screen the operator is now looking at.
        self._reading_pending = True
        self._nav_token = getattr(self, "_nav_token", 0) + 1
        tok = self._nav_token
        Clock.schedule_once(
            lambda _d: (getattr(self, "_nav_token", None) == tok
                        and self._render_reading()), 1.6)

    def _render_reading(self):
        self._stop_current()
        self.clear_widgets()
        self._back_action = self._render_detect   # reading -> re-detect
        wrap = BoxLayout(orientation="vertical", padding=dp(24), spacing=dp(18))
        from kivy.uix.widget import Widget
        wrap.add_widget(Widget())
        wrap.add_widget(_line(tr("Reading the board…"), "24sp", bold=True, h=40))
        wrap.add_widget(_line(tr("Checking whether it's already one of ours "
                                 "(this resets the board briefly)."),
                              "16sp", color="text_secondary", h=60))
        wrap.add_widget(Widget())
        _bk = self._back_row()
        if _bk is not None:
            wrap.add_widget(_bk)
        self.add_widget(wrap)
        try:
            # The banner read RESETS the board (USB re-enumerates for several
            # seconds) — quiet the disconnect watch so it doesn't false-alarm
            # right before the build chooser (live report 2026-07-31).
            from kivy.app import App
            App.get_running_app().quiet_board_watch(40)
        except Exception:
            pass
        import threading

        def work():
            c = {"kind": "birth", "reason": "Couldn't read the board."}
            try:
                from ui.hw_factories import local_board_ports
                from ui.adopt_live import read_board_banner, read_status_via_mdns
                from monitor.node_classifier import classify
                ports = local_board_ports()
                port = ports[0] if ports else None
                status = read_status_via_mdns(port)      # name, pre-reset
                banner = read_board_banner(port)         # identity + params
                c = classify(banner, status)
                c["_port"] = port
                # A plain RNode has NO identity/banner — recognise one of our
                # own by its USB fingerprint against stored birth certs
                # (operator report 2026-08-01: a just-flashed RNode wasn't
                # offered as kin).
                if port and not c.get("identity_hash"):
                    try:
                        from ui.hw_factories import LocalConnection
                        from workflows.rnode_flash import usb_id_for_port
                        from ui.cert_store import load_certs
                        usb = usb_id_for_port(LocalConnection(), port)
                        if usb:
                            # Match by HARDWARE SERIAL, not the whole by-id
                            # name. An exact compare failed on every nRF52
                            # board, because the vendor string differs between
                            # bootloader and firmware (RAKWireless vs
                            # RAKwireless). A rebirthed board came back
                            # unrecognised and LOST ITS NAME — the opposite of
                            # what a repair-rebirth needs, where the operator
                            # deliberately keeps the same name so their fleet
                            # records stay consistent (operator, 2026-08-05).
                            from ui.cert_store import cert_for_usb_serial
                            cert = cert_for_usb_serial(usb)
                            if cert:
                                c["_kin_by_serial"] = True
                                c["node_name"] = (c.get("node_name")
                                                  or cert.get("node_name"))
                                c["board"] = (c.get("board")
                                              or cert.get("board"))
                    except Exception:
                        pass
                # STILL unknown? Ask the board itself: a provisioned RNode
                # answers `rnodeconf --info` (no cert needed — catches boards
                # flashed before record-keeping, or by another tool). A
                # validated signature = flashed by THIS medic's key.
                if (port and not c.get("identity_hash")
                        and not c.get("_kin_by_serial")
                        and c.get("kind") != "adopt"):
                    try:
                        from ui.hw_factories import LocalConnection
                        # coreutils timeout = a HARD kill: rnodeconf never
                        # answers on non-RNode firmware and can wedge the
                        # port past our soft timeout (caught live 2026-08-01
                        # — a stock Tracker held ttyACM1 hostage).
                        code, out, err = LocalConnection().run(
                            f"sleep 3 && timeout 25 rnodeconf {port} --info",
                            timeout=45)
                        info = (out or "") + (err or "")
                        if ("Firmware version" in info
                                and "Device signature" in info):
                            c["_kin_by_serial"] = True     # -> already-flashed
                            c["_probed_alive"] = True      # it ANSWERED just now
                            c["_our_signature"] = "Validated" in info
                    except Exception:
                        pass
            except Exception as e:      # noqa: BLE001
                c = {"kind": "birth", "reason": f"Couldn't read the board: {e}"}
            from kivy.clock import Clock
            Clock.schedule_once(lambda _d: self._route(c), 0)
        threading.Thread(target=work, daemon=True).start()

    def _route(self, c):
        from ui.adopt_live import is_kin
        if is_kin(c.get("identity_hash")) or c.get("_kin_by_serial"):
            self._render_already_kin(c)   # already one of ours -> nothing to do
        elif c.get("kind") == "adopt":
            self._render_adopt(c)
        else:
            self._render_intro()          # birth -> the build chooser

    def _render_already_kin(self, c):
        self._stop_current()
        self.clear_widgets()
        from ui.adopt_live import known_name
        name = c.get("node_name") or known_name(c.get("identity_hash")) or tr("This node")
        self._back_action = self._render_detect   # already-kin info -> re-detect
        wrap = BoxLayout(orientation="vertical", padding=dp(24), spacing=dp(14))
        from kivy.uix.widget import Widget
        wrap.add_widget(Widget())
        # An identity-less board (a plain RNode) is 'already FLASHED', not
        # 'already kin reporting to VITALS' — a silent radio never beacons.
        # Either way the operator gets told UP FRONT it's not blank, with the
        # keep / rebirth choice (operator spec 2026-08-01: with many boards
        # on a bench, knowing what's flashed matters).
        rnode_like = not c.get("identity_hash")
        if rnode_like:
            if not c.get("node_name"):
                name = tr("This board")
            by = (tr(" — built by this Node Medic")
                  if c.get("_our_signature") else "")
            wrap.add_widget(_line(tr("Already flashed"), "28sp", bold=True,
                                  h=44, color="amber"))
            wrap.add_widget(_line(
                tr("{name} is already flashed as an RNode{by} — a radio for a "
                   "phone, computer or Pi. It has no mesh identity of its own. "
                   "Keep it as it is, or rebirth it as a different type of "
                   "node.").format(name=name, by=by),
                "16sp", color="text_secondary", h=110))
            # Only the LIVE probe branch may claim live proof — recognition
            # by stored fingerprint proves the board's IDENTITY, not that it
            # is working (2026-08-01 bug hunt: the claim was unconditional).
            if c.get("_probed_alive"):
                wrap.add_widget(_line(
                    tr("Verified alive: it answered over USB just now."),
                    "13sp", color="green", h=24))
            else:
                wrap.add_widget(_line(
                    tr("Recognised by its board ID from a past birth — not "
                       "health-checked just now."),
                    "13sp", color="text_secondary", h=34))
        else:
            # 'Already kin' must never mask a QUIET FAILURE (operator spec
            # 2026-08-01): verify the claim against when the medic actually
            # LAST HEARD this node. 12 h = the cadence plan's 'quiet' line.
            heard_h = None
            try:
                from kivy.app import App
                import time as _t
                app = App.get_running_app()
                rec = app.monitor_service.registry.get(c.get("identity_hash"))
                if rec is not None:
                    heard_h = rec.last_seen_hours(_t.time())
            except Exception:
                pass
            if heard_h is None or heard_h > 12:
                ago = (tr("yet") if heard_h is None
                       else tr("for {age}").format(age=format_age(heard_h)))
                wrap.add_widget(_line(tr("Already kin — but silent"), "28sp",
                                      bold=True, h=44, color="amber"))
                wrap.add_widget(_line(
                    tr("{name} is enrolled as your kin, but the medic hasn't "
                       "heard its beacon {ago}. It may be failing quietly — a "
                       "Rebirth gives it a clean start, or check its antenna "
                       "and power in TRIAGE.").format(name=name, ago=ago),
                    "16sp", color="text_secondary", h=100))
            else:
                wrap.add_widget(_line(tr("Already kin"), "28sp", bold=True,
                                      h=44, color="green"))
                wrap.add_widget(_line(
                    tr("{name} is already one of your kin — it's enrolled "
                       "and reporting to VITALS. Nothing to do.").format(name=name),
                    "16sp", color="text_secondary", h=80))
                heard_txt = (tr("Last heard under an hour ago.") if heard_h < 1
                             else tr("Last heard {age} ago.").format(
                                 age=format_age(heard_h)))
                wrap.add_widget(_line(heard_txt, "13sp", color="green", h=22))
            wrap.add_widget(_line(tr("Identity")
                                  + f"  {(c.get('identity_hash') or '')[:16]}…",
                                  "12.5sp", color="text_secondary", h=22))
        row = BoxLayout(orientation="horizontal", size_hint_y=None, height=dp(58),
                        spacing=dp(12))
        if not rnode_like:
            vit = Button(text=tr("See in VITALS"), bold=True, font_size="16sp",
                         background_normal="",
                         background_color=theme.hex_to_rgba(theme.COLORS["accent"]),
                         color=theme.hex_to_rgba(theme.COLORS["background"]))
            vit.bind(on_release=lambda *_: self._on_navigate
                     and self._on_navigate("vitals"))
            row.add_widget(vit)
        done = Button(text=tr("Keep it — done"), bold=True, font_size="16sp",
                      background_normal="",
                      background_color=theme.hex_to_rgba(theme.COLORS["surface"]),
                      color=theme.hex_to_rgba(theme.COLORS["text_primary"]))
        done.bind(on_release=lambda *_: self._on_navigate and self._on_navigate("home"))
        row.add_widget(done)
        wrap.add_widget(row)
        if rnode_like:
            # THE THIRD ROAD (operator, 2026-08-14, holding exactly this
            # board): it is already a healthy RNode and the operator wants a
            # Pi built TO MATCH it — no reflash, no dead end. Enters the Pi
            # walkthrough with the radio's proof carried in, so the radio
            # steps clear themselves and /dev/rnode still gets pinned to
            # THIS radio.
            cont = Button(text=tr("Keep it — and build its Pi  →"),
                          size_hint_y=None, height=dp(52), bold=True,
                          font_size="16sp", background_normal="",
                          background_color=theme.hex_to_rgba(theme.COLORS["green"]),
                          color=theme.hex_to_rgba(theme.COLORS["background"]))
            cont.bind(on_release=lambda *_: self._keep_and_continue(c))
            wrap.add_widget(cont)
        # REBIRTH (operator request 2026-07-31): wipe + flash fresh — the
        # deliberate path for a RENAME or a hard reset of a misbehaving node.
        reb = Button(text=tr("Rebirth — wipe this node & build it fresh"),
                     size_hint_y=None, height=dp(46), font_size="14sp",
                     background_normal="",
                     background_color=theme.hex_to_rgba(theme.COLORS["surface"]),
                     color=theme.hex_to_rgba(theme.COLORS["amber"]))
        reb.bind(on_release=lambda *_: self._confirm_rebirth(c))
        wrap.add_widget(reb)
        wrap.add_widget(Widget())
        _bk = self._back_row()
        if _bk is not None:
            wrap.add_widget(_bk)
        self.add_widget(wrap)

    def _keep_and_continue(self, c):
        """Already-an-RNode -> straight into the Pi walkthrough, radio proven.

        Carries the two things the Pi build needs from the radio it will
        never see again (it rides in the operator's pocket): that it VERIFIED
        alive just now (arms the radio gate), and its USB hardware serial
        (pins /dev/rnode to this exact radio, not any tty from five vendors).
        """
        port = c.get("port") or ""
        serial = ""
        try:
            from ui.hw_factories import LocalConnection
            from workflows.rnode_flash import by_id_serial, usb_id_for_port
            serial = by_id_serial(usb_id_for_port(LocalConnection(), port)) or ""
        except Exception:                                          # noqa: BLE001
            serial = ""
        self._radio_usb_serial = serial
        self._radio_verified = bool(c.get("_probed_alive"))
        self._board_key = self._board_key_of(c)
        self._path = "pi"
        self._i = 0
        self._render_pick_pi()

    def _board_key_of(self, c):
        """The rnode_boards key for a recognised board — the candidate may
        carry either the key itself or the display name off a certificate."""
        try:
            from workflows.rnode_boards import RNODE_BOARDS
            raw = (c.get("board") or "").strip()
            if raw in RNODE_BOARDS:
                return raw
            for key, b in RNODE_BOARDS.items():
                if b.display_name == raw:
                    return key
        except Exception:                                          # noqa: BLE001
            pass
        return ""

    def _confirm_rebirth(self, c):
        """Destructive-action gate: rebirth erases the board completely — new
        identity, config gone, VITALS history detaches from the old identity."""
        # open-once guard: the touchscreen can deliver a tap twice, which
        # stacked TWO identical confirms — the survivor looked like a popup
        # that 'wouldn't close' (live 2026-07-31)
        if getattr(self, "_rebirth_pop", None) is not None:
            return
        from kivy.uix.popup import Popup
        body = BoxLayout(orientation="vertical", spacing=dp(8), padding=dp(8))
        body.add_widget(_line(tr("This ERASES the node completely:"), "16sp",
                              bold=True, color="amber", h=28))
        body.add_widget(_line(tr("• its identity is wiped — it becomes a brand-new "
                                 "node (old history detaches)\n• its name, WiFi and "
                                 "radio settings are wiped\n• then the normal birth "
                                 "runs: flash, name it, auto-setup"),
                              "14sp", color="text_secondary", h=96))
        row = BoxLayout(orientation="horizontal", size_hint_y=None, height=dp(54),
                        spacing=dp(10))
        go = Button(text=tr("⚠  Wipe & rebirth"), bold=True, font_size="15sp",
                    background_normal="",
                    background_color=theme.hex_to_rgba(theme.COLORS["red"]),
                    color=theme.hex_to_rgba(theme.COLORS["text_primary"]))
        cancel = Button(text=tr("Cancel"), bold=True, font_size="15sp",
                        background_normal="",
                        background_color=theme.hex_to_rgba(theme.COLORS["green"]),
                        color=theme.hex_to_rgba(theme.COLORS["background"]))
        row.add_widget(go)
        row.add_widget(cancel)
        body.add_widget(row)
        pop = Popup(title=tr("Rebirth this node?"), content=body,
                    size_hint=(0.9, 0.55),
                    title_color=theme.hex_to_rgba(theme.COLORS["amber"]))
        self._rebirth_pop = pop
        pop.bind(on_dismiss=lambda *_: setattr(self, "_rebirth_pop", None))
        cancel.bind(on_release=lambda *_: pop.dismiss())
        go.bind(on_release=lambda *_: (pop.dismiss(), self._do_rebirth(c)))
        pop.open()

    def _do_rebirth(self, c):
        """Erase the board (guard-checked), forget its old roster identity, and
        hand off to the normal (proven single-pass) birth flow."""
        if getattr(self, "_rebirth_running", False):    # doubled-tap fuse
            return
        self._rebirth_running = True
        port = c.get("_port")
        old_ident = c.get("identity_hash") or ""
        old_name = c.get("node_name") or ""
        self._stop_current()
        self.clear_widgets()
        wrap = BoxLayout(orientation="vertical", padding=dp(24), spacing=dp(14))
        from kivy.uix.widget import Widget
        wrap.add_widget(Widget())
        wrap.add_widget(_line(tr("Wiping the board…"), "24sp", bold=True, h=40))
        wrap.add_widget(_line(tr("A few seconds — then the normal birth starts."),
                              "14sp", color="text_secondary", h=24))
        wrap.add_widget(Widget())
        self.add_widget(wrap)
        try:
            # Mark the erase as a running flash: it resets the board on
            # purpose, and the board-disconnect watch stays silent while
            # flash_in_progress() (false 'Board disconnected!' otherwise).
            from kivy.app import App
            App.get_running_app().begin_activity(
                "Wiping " + (old_name or "the board") + " — keep it plugged in")
        except Exception:
            pass

        def work():
            ok, msg = True, ""
            try:
                import glob
                import os
                # Wipe by the means the board's CHIP FAMILY supports. This used
                # to run esptool unconditionally, which on a RAK4631 failed with
                # "Could not connect to Espressif device" and sent the operator
                # to Espressif's troubleshooting page for a chip that isn't on
                # the board (live, 2026-08-05). assert_flashable and the
                # lock-race retry both live inside wipe_for_rebirth now.
                from ui.hw_factories import LocalConnection
                from workflows.rnode_flash import wipe_for_rebirth
                et = (glob.glob(os.path.expanduser(
                    "~/.platformio/packages/tool-esptoolpy/esptool.py")) or [""])[0]
                if not port:
                    ok, msg = False, tr("Couldn't find the board or the flash tool.")
                else:
                    ok, msg = wipe_for_rebirth(LocalConnection(), port,
                                               esptool_path=et)
                    if ok:
                        msg = ""
                if ok:
                    # The board is now BLANK — its old certificate must go too,
                    # or the fingerprint match reports it as 'already flashed'
                    # on the next plug-in (2026-08-01 bug hunt).
                    try:
                        from ui.hw_factories import LocalConnection
                        from workflows.rnode_flash import usb_id_for_port
                        from ui.cert_store import delete_by_usb_serial
                        usb = usb_id_for_port(LocalConnection(), port)
                        if usb:
                            delete_by_usb_serial(usb)
                    except Exception:
                        pass
                if ok and old_ident:
                    try:                              # forget the old identity
                        from monitor import kin_roster
                        ro = kin_roster.load_roster()
                        if old_ident in ro:
                            del ro[old_ident]
                            kin_roster._save(ro, kin_roster.KIN_ROSTER_PATH)
                    except Exception:
                        pass
            except Exception as e:                    # noqa: BLE001
                ok, msg = False, str(e)[:160]
            from kivy.clock import Clock

            def done(_dt):
                self._rebirth_running = False
                try:
                    from kivy.app import App
                    App.get_running_app().end_activity()
                except Exception:
                    pass
                if ok:
                    # A rebirth is EXACTLY when the node's type may change, so
                    # land on the CARD CHOOSER — the one that visibly offers
                    # RNode / RTNode-2400 / Pi + RNode — and let the operator
                    # pick the family afresh.
                    #
                    # This used to hand straight off to the BIRTH form, which
                    # arrives with a type already chosen and only a small
                    # "change" link, so a rebirth read as "same thing again"
                    # (operator, 2026-08-06: "reuse the screen that gives the
                    # user visible options of what they want to birth").
                    #
                    # _render_intro re-reads the board on the way in, which is
                    # exactly right here: the wipe just changed what this board
                    # is, so the options are computed from what it is NOW.
                    # THE DEFAULT MUST BE SAFE WHEN NOBODY READS IT. This used
                    # to carry the old name straight through — operator,
                    # 2026-08-05: "It says wiping rak3 ... but I'm not prompted
                    # to change the name from rak3." Nothing drew attention to
                    # the field, so the old name was simply kept, and keeping it
                    # OVERWRITES the previous certificate: born date, stamped
                    # location and notes gone, unannounced.
                    #
                    # So the box now offers the next free number (rak3 -> rak4).
                    # The old name is still shown on the way past as history, and
                    # the operator can type anything they like — this only
                    # decides what is already there.
                    try:
                        from ui.cert_store import next_free_name
                        self._node_name = next_free_name(old_name or "")
                    except Exception:
                        self._node_name = ""      # rather than the old name
                    self._rebirth_of = old_name or ""   # shown as history
                    self._pair_checked = False         # a fresh lap re-checks
                    self._i = 0
                    self._render_intro(builds_only=True)
                else:
                    from ui.requirement_popup import requirement_popup
                    requirement_popup(
                        tr("Couldn't wipe the board: ") + (msg or tr("unknown")),
                        tr("Rebirth failed"), False)
                    self._render_detect()
            Clock.schedule_once(done, 0)
        import threading
        threading.Thread(target=work, daemon=True).start()

    # -- adopt confirm / run ----------------------------------------------
    def _render_adopt(self, c):
        self._stop_current()
        self.clear_widgets()
        from kivy.uix.textinput import TextInput
        from ui.onscreen_keyboard import bind_field
        self._back_action = self._render_detect   # adopt confirm -> re-detect
        wrap = BoxLayout(orientation="vertical", padding=dp(20), spacing=dp(12))
        wrap.add_widget(_line(tr("Existing node found"), "24sp", bold=True, h=36))
        wrap.add_widget(_line(tr("This node is already running our config — adopt it "
                                 "as kin (no flashing, keeps its settings)."),
                              "15sp", color="text_secondary", h=48))
        # Adoption INHERITS the node's own name (it doesn't reflash, so it can't
        # rename the node — 'Re-birth instead' is the deliberate rename path). Show
        # it read-only when we can read it (from /status or the medic's records);
        # only offer a field as a fallback when the name is genuinely unknown.
        from ui.adopt_live import known_name
        name0 = c.get("node_name") or known_name(c.get("identity_hash")) or ""
        wrap.add_widget(_line(tr("Name (kept from the node)"), "13sp",
                              color="accent", h=20))
        if name0:
            wrap.add_widget(_line(name0, "20sp", bold=True, h=32))
            self._adopt_name = None
            self._adopt_name_value = name0
        else:
            ti = TextInput(hint_text=tr("Couldn't read the node's name — enter one"),
                           multiline=False, size_hint_y=None, height=dp(56),
                           font_size="32sp")
            bind_field(ti)
            self._adopt_name = ti
            self._adopt_name_value = ""
        p = c.get("params") or {}
        det = (f"Board:  {c.get('board') or '—'}      Firmware:  {c.get('firmware') or '—'}\n"
               f"Radio:  {(p.get('freq', 0) / 1e6):.3f} MHz   SF{p.get('sf')}   "
               f"{int(p.get('bw', 0) / 1000)}k   CR{p.get('cr')}   {p.get('txp')} dBm   [OK]\n"
               f"Identity:  {(c.get('identity_hash') or '')[:16]}…   Beaconing [OK]")
        wrap.add_widget(_line(det, "13.5sp", color="text_secondary", h=78))
        from kivy.uix.widget import Widget
        wrap.add_widget(Widget())
        row = BoxLayout(orientation="horizontal", size_hint_y=None, height=dp(62),
                        spacing=dp(12))
        reb = Button(text=tr("Re-birth instead"), font_size="16sp", bold=True,
                     background_normal="", size_hint_x=0.42,
                     background_color=theme.hex_to_rgba(theme.COLORS["surface"]),
                     color=theme.hex_to_rgba(theme.COLORS["text_primary"]))
        reb.bind(on_release=lambda *_: self._render_intro())
        adopt = Button(text=tr("Adopt as kin"), font_size="19sp", bold=True,
                       background_normal="",
                       background_color=theme.hex_to_rgba(theme.COLORS["green"]),
                       color=theme.hex_to_rgba(theme.COLORS["background"]))
        adopt.bind(on_release=lambda *_: self._do_adopt(c))
        row.add_widget(reb)
        row.add_widget(adopt)
        wrap.add_widget(row)
        _bk = self._back_row()
        if _bk is not None:
            wrap.add_widget(_bk)
        self.add_widget(wrap)

    def _do_adopt(self, c):
        # GATE: the medic is AT the node for a USB adopt, so confirm its GPS on a
        # map before it's baked into the cert (catches a wrong/stale fix).
        name = ((self._adopt_name.text or "").strip() if self._adopt_name
                else self._adopt_name_value)
        fix = None
        try:
            from monitor.geo import read_gps
            f = read_gps()
            fix = (f.lat, f.lon) if f else None
        except Exception:
            fix = None
        if fix is None:
            self._run_adopt(c, name, None)
            return
        try:
            from ui.widgets.confirm_location import ConfirmLocationPopup
            from monitor.geo import splitter_gps_reader
            ConfirmLocationPopup(
                fix[0], fix[1], node_name=name or c.get("name", ""),
                on_confirm=lambda lat, lon: self._run_adopt(c, name, (lat, lon)),
                on_cancel=lambda: self._run_adopt(c, name, None),
                gps_reader=splitter_gps_reader()).open()
        except Exception:
            self._run_adopt(c, name, fix)

    def _run_adopt(self, c, name, location):
        self._stop_current()
        self.clear_widgets()
        wrap = BoxLayout(orientation="vertical", padding=dp(24), spacing=dp(16))
        from kivy.uix.widget import Widget
        wrap.add_widget(Widget())
        wrap.add_widget(_line(tr("Adopting…"), "24sp", bold=True, h=40))
        self._adopt_status = _line(tr("Reading identity, writing certificate, "
                                      "enrolling as kin…"), "15sp",
                                   color="text_secondary", h=60)
        wrap.add_widget(self._adopt_status)
        wrap.add_widget(Widget())
        self.add_widget(wrap)
        import threading

        def work():
            ok, msg = False, "Adoption failed."
            try:
                from ui.adopt_live import make_adopt_workflow
                wf = make_adopt_workflow(board_port=c.get("_port"),
                                         name_override=name, location=location)
                wf.run_all()
                ok = wf.succeeded
                msg = wf.results[-1].message if wf.results else msg
            except Exception as e:      # noqa: BLE001
                ok, msg = False, f"Adoption failed: {e}"
            from kivy.clock import Clock
            Clock.schedule_once(lambda _d: self._render_adopt_done(ok, msg), 0)
        threading.Thread(target=work, daemon=True).start()

    def _render_adopt_done(self, ok, msg):
        self._stop_current()
        self.clear_widgets()
        self._back_action = None          # terminal outcome -> home
        wrap = BoxLayout(orientation="vertical", padding=dp(24), spacing=dp(16))
        from kivy.uix.widget import Widget
        wrap.add_widget(Widget())
        wrap.add_widget(_line(tr("Adopted [OK]") if ok else tr("Couldn't adopt"),
                              "26sp", bold=True, h=42,
                              color="green" if ok else "warning_yellow"))
        wrap.add_widget(_line(msg, "16sp", color="text_secondary", h=80))
        done = Button(text=tr("Done"), size_hint_y=None, height=dp(58), bold=True,
                      font_size="18sp", background_normal="",
                      background_color=theme.hex_to_rgba(theme.COLORS["accent"]),
                      color=theme.hex_to_rgba(theme.COLORS["background"]))
        done.bind(on_release=lambda *_: (self._on_navigate and
                                         self._on_navigate("vitals" if ok else "home")))
        wrap.add_widget(done)
        wrap.add_widget(Widget())
        self.add_widget(wrap)

    # -- rendering ---------------------------------------------------------
    def _render_intro(self, builds_only=False):
        """The card chooser. *builds_only* drops Mitosis and adopt-over-the-air,
        for the rebirth case where the question is only "what shall THIS board
        become?" — see the wipe handler."""
        self.clear_widgets()
        self._current = None
        self._back_action = self._render_detect   # chooser -> detect landing
        wrap = BoxLayout(orientation="vertical", padding=dp(22), spacing=dp(16))
        from ui.widgets.help_button import HelpButton
        head = BoxLayout(orientation="horizontal", size_hint_y=None, height=dp(44),
                         spacing=dp(8))
        head.add_widget(_line(tr("What are you building?"), "26sp", bold=True))
        head.add_widget(HelpButton())
        wrap.add_widget(head)
        wrap.add_widget(_line(tr("Not sure which is which? Tap the  ?  above."),
                              "16sp", color="text_secondary", h=32))
        # Once the board has been READ, drop the builds it cannot do. RTNode-2400
        # needs an ESP32-S3; a classic ESP32 (LoRa32, T-Beam, Heltec V2) can only
        # ever be an RNode. Offering an impossible choice and failing later
        # wastes the operator's time and teaches them to distrust the list
        # (operator, 2026-08-02). firmware_options() already knew this — the
        # chooser simply wasn't asking it.
        paths, dropped = self._paths_for_connected_board()
        for key, title, subtitle in paths:
            # The Pi card carries a longer description, so it needs more room.
            # Both grew by the 22dp the title gained (2026-08-05) — the card
            # height has to move with the title box or the description loses
            # exactly what the title gained.
            h = 192 if key == "pi" else 126
            wrap.add_widget(self._path_button(key, title, subtitle, height=h))
        if dropped:
            # Say WHY it is missing. An option that silently disappears between
            # one visit and the next reads as a glitch.
            wrap.add_widget(_line(dropped, "13.5sp", color="text_secondary", h=40))
        # After a WIPE the operator is answering a narrower question: this board,
        # in my hand, blank — what shall it become? Cloning the Node Medic and
        # adopting a node over the air are neither of those, and offering them
        # there invites a mis-tap that abandons the board mid-rebirth (operator,
        # 2026-08-06: "except without the mitosis or over air options").
        if not builds_only:
            # Mitosis is a different KIND of action — not building a node but
            # cloning the Node Medic itself — so it sits at the end, styled
            # apart, and routes straight to the MITOSIS screen.
            wrap.add_widget(self._mitosis_button())
            # Adopt a node the medic HEARS over LoRa (no USB) — field enrolment.
            if self._heard_fn is not None:
                wrap.add_widget(self._over_air_button())
        from kivy.uix.widget import Widget
        wrap.add_widget(Widget())
        _bk = self._back_row()
        if _bk is not None:
            wrap.add_widget(_bk)
        self.add_widget(wrap)

    def _paths_for_connected_board(self):
        """(paths to offer, why-one-is-missing line).

        Best-effort and fail-open: if the board can't be read we offer
        everything, because a wrong exclusion here blocks a real build.
        """
        try:
            from ui.board_detect import detect_board, firmware_options
            from workflows.rnode_boards import RNODE_BOARDS
            from ui.hw_factories import local_board_ports
            det = detect_board(list(RNODE_BOARDS.values()),
                               ports_fn=local_board_ports)
            # KEEP IT. _board_candidates reads self._detected to offer only the
            # boards the medic could not rule out; nothing ever assigned it, so
            # the fallback always fired and "Which radio board is this?" listed
            # the entire ~15-board catalogue directly under copy claiming it had
            # been narrowed (audit, 2026-08-03).
            self._detected = det
            from ui.birth_guide_flow import paths_for_chip
            chip = det.get("chip") if det.get("found") else None
            paths, why = paths_for_chip(
                chip, lambda c: "rtnode2400" in firmware_options(c))
            if not why:
                return paths, ""
            boards = det.get("boards") or []
            name = boards[0].display_name if len(boards) == 1 else "this board"
            return paths, tr(
                "A mesh transport node (RTNode-2400) isn't offered: it needs an "
                "ESP32-S3, and {board} uses an {chip}.").format(
                    board=name, chip=(chip or "").upper())
        except Exception:
            return list(BIRTH_PATHS), ""

    def _over_air_button(self):
        btn = Button(size_hint_y=None, height=dp(104), background_normal="",
                     background_color=theme.hex_to_rgba(theme.COLORS["green"]))
        inner = BoxLayout(orientation="vertical", padding=[dp(18), dp(12)], spacing=dp(4))
        inner.add_widget(_line(tr("Adopt over the air (LoRa)"), "21sp", bold=True,
                               color="background", h=30))
        inner.add_widget(_line(tr("Enrol a node you can hear on the mesh as kin - no "
                                  "cable needed. For nodes already in the field."),
                               "14sp", color="background"))
        inner.size = btn.size
        btn.bind(size=lambda _b, v: setattr(inner, "size", v),
                 pos=lambda _b, v: setattr(inner, "pos", v))
        btn.add_widget(inner)
        btn.bind(on_release=lambda *_: self._render_over_air_list())
        return btn

    # -- over-the-air adoption --------------------------------------------
    def _render_over_air_list(self):
        self._stop_current()
        self.clear_widgets()
        self._back_action = self._render_intro    # heard list -> chooser
        from kivy.uix.scrollview import ScrollView
        from kivy.uix.widget import Widget
        root = BoxLayout(orientation="vertical", padding=dp(16), spacing=dp(8))
        head = BoxLayout(orientation="horizontal", size_hint_y=None, height=dp(40),
                         spacing=dp(8))
        head.add_widget(_line(tr("Nodes heard on the mesh"), "22sp", bold=True))
        back = Button(text=tr("←  Back"), size_hint_x=None, width=dp(96),
                      font_size="14sp", background_normal="",
                      background_color=theme.hex_to_rgba(theme.COLORS["surface"]),
                      color=theme.hex_to_rgba(theme.COLORS["text_primary"]))
        back.bind(on_release=lambda *_: self._render_intro())
        head.add_widget(back)
        root.add_widget(head)
        root.add_widget(_line(tr("Pick one to adopt as kin over LoRa (no cable)."),
                              "14sp", color="text_secondary", h=26))
        cands = []
        try:
            cands = self._heard_fn() or []
        except Exception:
            cands = []
        sv = ScrollView()
        col = BoxLayout(orientation="vertical", size_hint_y=None, spacing=dp(8))
        col.bind(minimum_height=col.setter("height"))
        if not cands:
            col.add_widget(_line(tr("Nothing heard yet — the medic hasn't received a "
                                    "beacon/announce. Give it a moment on the mesh."),
                                 "14sp", color="text_secondary", h=60))
        for c in cands:
            col.add_widget(self._heard_row(c))
        sv.add_widget(col)
        root.add_widget(sv)
        _bk = self._back_row()
        if _bk is not None:
            root.add_widget(_bk)
        self.add_widget(root)

    def _heard_row(self, c):
        is_kin = c.get("provenance") == "kin"
        btn = Button(size_hint_y=None, height=dp(84), background_normal="",
                     background_color=theme.hex_to_rgba(
                         theme.COLORS["surface" if not is_kin else "background"]))
        inner = BoxLayout(orientation="vertical", padding=[dp(14), dp(8)], spacing=dp(2))
        tag = ("  " + tr("(already kin)")) if is_kin else ""
        inner.add_widget(_line(f"{c.get('name', tr('(unnamed)'))}{tag}", "18sp",
                               bold=True, h=26))
        lsh = c.get("last_seen_hours")
        seen = (tr("heard {age} ago").format(age=format_age(lsh))
                if isinstance(lsh, (int, float)) else tr("heard"))
        sig = c.get("signal_dbm")
        sigs = f" · {sig} dBm" if sig is not None else ""
        inner.add_widget(_line(f"{c.get('node_type', 'node')} · {seen}{sigs}",
                               "13sp", color="text_secondary"))
        inner.size = btn.size
        btn.bind(size=lambda _b, v: setattr(inner, "size", v),
                 pos=lambda _b, v: setattr(inner, "pos", v))
        btn.add_widget(inner)
        btn.bind(on_release=lambda *_: self._render_over_air_confirm(c))
        return btn

    def _render_over_air_confirm(self, c):
        self._stop_current()
        self.clear_widgets()
        from kivy.uix.textinput import TextInput
        from ui.onscreen_keyboard import bind_field
        self._back_action = self._render_over_air_list
        wrap = BoxLayout(orientation="vertical", padding=dp(20), spacing=dp(12))
        wrap.add_widget(_line(tr("Adopt over LoRa"), "24sp", bold=True, h=36))
        wrap.add_widget(_line(tr("Enrol this node as kin from its mesh beacon — no "
                                 "cable, keeps its settings."), "15sp",
                              color="text_secondary", h=44))
        ti = TextInput(text=c.get("name") or "", multiline=False,
                       hint_text=tr("Node name"), size_hint_y=None, height=dp(56),
                       font_size="32sp")
        bind_field(ti)
        self._air_name = ti
        wrap.add_widget(_line(tr("Name"), "13sp", color="accent", h=20))
        wrap.add_widget(ti)
        det = (f"Type:  {c.get('node_type', 'node')}      "
               f"Board:  {c.get('board') or '—'}      Firmware:  {c.get('firmware') or '—'}\n"
               f"Identity:  {(c.get('key') or '')[:16]}…   Heard on the mesh [OK]")
        wrap.add_widget(_line(det, "13.5sp", color="text_secondary", h=56))
        from kivy.uix.widget import Widget
        wrap.add_widget(Widget())
        row = BoxLayout(orientation="horizontal", size_hint_y=None, height=dp(62),
                        spacing=dp(12))
        cancel = Button(text=tr("Back"), font_size="16sp", bold=True, size_hint_x=0.4,
                        background_normal="",
                        background_color=theme.hex_to_rgba(theme.COLORS["surface"]),
                        color=theme.hex_to_rgba(theme.COLORS["text_primary"]))
        cancel.bind(on_release=lambda *_: self._render_over_air_list())
        adopt = Button(text=tr("Adopt as kin"), font_size="19sp", bold=True,
                       background_normal="",
                       background_color=theme.hex_to_rgba(theme.COLORS["green"]),
                       color=theme.hex_to_rgba(theme.COLORS["background"]))
        adopt.bind(on_release=lambda *_: self._do_over_air(c))
        row.add_widget(cancel)
        row.add_widget(adopt)
        wrap.add_widget(row)
        _bk = self._back_row()
        if _bk is not None:
            wrap.add_widget(_bk)
        self.add_widget(wrap)

    def _do_over_air(self, c):
        name = (self._air_name.text or "").strip() or c.get("name") or "node"
        # PLACEMENT: the medic can't sense a remote node, so the operator drops it
        # on the map. Start on its known location (if kin) or the medic's position.
        start = None
        try:
            from monitor.kin_roster import load_roster
            e = load_roster().get(c.get("key")) or {}
            if e.get("lat") is not None:
                start = (e["lat"], e["lon"])
        except Exception:
            start = None
        if start is None:
            try:
                from monitor.geo import read_gps
                f = read_gps()
                start = (f.lat, f.lon) if f else None
            except Exception:
                start = None
        if start is None:
            self._run_over_air(c, name, None)     # nowhere to centre a map
            return
        try:
            from ui.widgets.confirm_location import ConfirmLocationPopup
            from monitor.geo import splitter_gps_reader
            ConfirmLocationPopup(
                start[0], start[1], node_name=name,
                on_confirm=lambda lat, lon: self._run_over_air(c, name, (lat, lon)),
                on_cancel=lambda: self._run_over_air(c, name, None),
                gps_reader=splitter_gps_reader()).open()
        except Exception:
            self._run_over_air(c, name, start)

    def _run_over_air(self, c, name, location):
        ok, msg = False, "Adoption failed."
        try:
            if self._adopt_air_fn is not None:
                self._adopt_air_fn(c.get("key"), name, c.get("node_type", "rtnode2400"),
                                   c.get("board"), c.get("firmware"), location)
                ok, msg = True, f"{name} adopted as kin over LoRa — now in VITALS."
        except Exception as e:      # noqa: BLE001
            ok, msg = False, f"Adoption failed: {e}"
        self._render_adopt_done(ok, msg)

    def _mitosis_button(self):
        btn = Button(size_hint_y=None, height=dp(104), background_normal="",
                     background_color=theme.hex_to_rgba(theme.COLORS["accent"]))
        inner = BoxLayout(orientation="vertical", padding=[dp(18), dp(12)], spacing=dp(4))
        inner.add_widget(_line(tr("Mitosis - clone this Node Medic"), "21sp",
                               bold=True, color="background", h=30))
        inner.add_widget(_line(tr("Copy this Node Medic onto a fresh Raspberry Pi 5 - "
                                  "a second building tool."), "14sp", color="background"))
        inner.size = btn.size
        btn.bind(size=lambda _b, v: setattr(inner, "size", v),
                 pos=lambda _b, v: setattr(inner, "pos", v))
        btn.add_widget(inner)
        btn.bind(on_release=lambda *_: self._on_navigate and self._on_navigate("mitosis"))
        return btn

    def _path_button(self, key, title, subtitle, height=104):
        """A choice card. The title gets room for TWO lines.

        "A radio for phone or computer (RNode)" was squashed on the 5" panel —
        the "(RNode)" that names the thing collided with the description under
        it (operator, 2026-08-05). The title box was 30dp, one line's worth.

        The extra room is given rather than computed, deliberately. An earlier
        attempt made the card size itself to its content and that OVERFLOWED:
        the description ran past the card and over the text below it, which was
        worse than the clipping it replaced. Sizing a Button's overlaid content
        reactively is fiddly, and this screen is in the operator's hands right
        now. A fixed, generous box cannot overflow — the labels clip inside it
        at worst — and it costs a little whitespace on the short titles.

        Honest limit: this was NOT reproducible off the device. The string
        measures 25px at every width from 560 to 720, in both the default font
        and the DejaVu the app switches to, so the reason it wraps on the panel
        is still unknown. Two lines of room covers it either way.
        """
        btn = Button(size_hint_y=None, height=dp(height), background_normal="",
                     background_color=theme.hex_to_rgba(theme.COLORS["surface"]))
        inner = BoxLayout(orientation="vertical", padding=[dp(18), dp(12)], spacing=dp(4))
        inner.add_widget(_line(title, "21sp", bold=True, h=52))
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
        self._back_action = self._render_intro    # name step -> the chooser
        # +2 for the two decision screens folded in ahead of the physical
        # steps: this one (the name) and the map-sharing question.
        total = len(guide_steps(self._path, self._pi_key_for_text())) + 2
        ti = TextInput(text=self._node_name, multiline=False,
                       hint_text=tr("Name this node  (e.g. Rooftop-East)"),
                       size_hint_y=None, height=dp(58), font_size="33sp")
        bind_field(ti)
        self._name_input = ti
        body = tr("Give this node a short, memorable name — you'll see it "
                  "on the map and on its birth certificate.")
        # After a wipe, say what the board USED to be — as history, not as the
        # answer. The operator watched a screen announce "wiping rak3" and then
        # offer them "rak3" with nothing marking it as a decision still to make.
        was = getattr(self, "_rebirth_of", "")
        if was:
            body = tr("This board was {old}. It's blank now — we've suggested "
                      "a name, and you can change it to anything.").format(old=was)
        # A name already in the family is warned about ONCE, then allowed —
        # see _name_next. Re-rendering with the warning is what puts it on
        # screen, so the button changes with it.
        warn = getattr(self, "_name_warning", "")
        step = WizardStep(index=0, total=total, title=tr("Name this node"),
                          body=body, warning=warn,
                          input_widget=ti,
                          next_text=tr("Use it anyway  →") if warn else tr("Next  →"),
                          on_next=self._name_next, on_back=self.reset)
        self.clear_widgets()
        self.add_widget(step)
        self._current = step
        # Deliberately NOT auto-focused. The keyboard covers this step's own Back
        # and Next, so opening it unasked made the first step of the walkthrough
        # look like it had no way out (operator, 2026-08-02). The operator taps
        # the field when they're ready to type, and DONE puts it away.

    def _name_next(self):
        name = (self._name_input.text or "").strip()
        if not name:                         # a name is required to continue
            self._name_input.focus = True
            return
        # A name already in the family: WARN, then allow. Reusing a name can be
        # deliberate — rebuilding a node that died, keeping its place on the map
        # — so refusing would be wrong. But two nodes with one name are hard to
        # tell apart on the map and in a repair months from now, when whoever
        # built them may be long gone. Warn once per name; a second tap on the
        # (now differently-labelled) button goes ahead.
        if name != getattr(self, "_name_warned", None):
            try:
                from ui.node_names import clash
                msg = clash(name)
            except Exception:
                msg = ""                     # advice must never block a birth
            if not msg:
                # A CONTESTED NAME IS REFUSED AT ENTRY (operator, 2026-08-14):
                # if <name>.local already resolves to a live host, say so NOW
                # — not four minutes later as a detect_hardware mystery when
                # the build reaches the wrong machine. Advice, not a block:
                # the operator may be deliberately re-imaging that very node.
                try:
                    from provisioning.pi_discover import resolve
                    from provisioning.pi_imager import hostnameify
                    host = hostnameify(name)
                    addr = resolve(f"{host}.local") if host else None
                    if addr:
                        msg = tr("Something on your network already answers "
                                 "to this name (at {addr}). If that is the "
                                 "node you are re-imaging, carry on — "
                                 "otherwise a different name avoids the two "
                                 "machines being mistaken for each other."
                                 ).format(addr=addr)
                except Exception:                                  # noqa: BLE001
                    pass
            if msg:
                self._name_warned = name
                self._name_warning = msg
                self._node_name = name       # keep what they typed
                self._render_name()
                return
        self._name_warning = ""
        self._name_warned = None
        self._node_name = name
        self._i = 0
        # THE MAP QUESTION COMES NEXT, ONCE, before any work starts. It is
        # asked here rather than at the end because by the end the node has been
        # flashed and configured, and a decision taken then is a decision taken
        # under "just finish it" — which is not how anyone should agree to
        # publish where a thing is.
        #
        # NOT ON THE 'host' PATH. That path flashes a radio to plug into
        # somebody's phone or laptop; it is not a node, it has no Reticulum
        # config of its own here, and nothing the medic writes would ever
        # announce a position. Asking anyway would be a question whose answer
        # goes nowhere — which is precisely the "looks like it is sharing and
        # isn't" failure this whole feature exists to prevent.
        if self._path in ("radio", "pi") and not self._share_asked:
            self._render_location_share()
            return
        self._render_step()

    # -- the one question: does this node go on the public map? ---------------

    def _render_location_share(self):
        """The two birth answers as two sliders, and a Next that commits what
        they show — nothing else on the screen advances it.

        REDESIGNED 2026-08-12 (operator, with the first version on the glass):
        "the first screen should just have two sliders on it — pressing 'keep
        it hidden' takes you to the next build step, this is a confusing UI."
        The commit-button-that-named-an-end read as a label and acted as a
        button. Now the answers are POSITIONS — map: Hidden / Show on map,
        Bluetooth: off / on (Pi path only, with the power cost under it) —
        and the ordinary green Next commits exactly what is showing. The
        switches still select the half you touch, never flip, so a stray tap
        cannot move an answer; and both rest on their quiet ends.
        """
        self._stop_current()
        from ui.widgets.share_toggle import OnOffToggle, ShareToggle
        self._back_action = self._render_name          # prelude -> the name

        s = LOCATION_SHARE_STEP
        b = BLUETOOTH_STEP
        total = len(guide_steps(self._path, self._pi_key_for_text())) + 2
        self._share_pending = self._share_location     # never the last node's
        self._bt_pending = "on" if self._bluetooth_on else "off"

        choices = BoxLayout(orientation="vertical", size_hint_y=None,
                            spacing=dp(8))
        choices.bind(minimum_height=choices.setter("height"))
        self._share_toggle = ShareToggle(
            policy=self._share_pending, on_toggle=self._share_moved,
            hide_label=s["hide_label"], share_label=s["share_label"])
        choices.add_widget(self._share_toggle)
        self._share_consequence = Label(
            text="", font_size=theme.font_sp("13sp"), halign="left",
            valign="top", size_hint_y=None, line_height=1.2,
            color=theme.hex_to_rgba(theme.COLORS["text_secondary"]))
        self._share_consequence.bind(
            width=lambda i, w: setattr(i, "text_size", (w, None)),
            texture_size=lambda i, ts: setattr(i, "height", ts[1]))
        choices.add_widget(self._share_consequence)
        if getattr(self, "_path", "") == "pi":
            # Bluetooth belongs to the Pi build only — the node whose strength
            # is to bridge, and the only build that applies the answer.
            from kivy.uix.widget import Widget as _Spacer
            choices.add_widget(_Spacer(size_hint_y=None, height=dp(6)))
            self._bt_toggle = OnOffToggle(
                state=self._bt_pending, on_toggle=self._bt_moved,
                off_label=b["off_label"], on_label=b["on_label"])
            choices.add_widget(self._bt_toggle)
            bt_line = Label(
                text=b["hint"], font_size=theme.font_sp("13sp"), halign="left",
                valign="top", size_hint_y=None, line_height=1.2,
                color=theme.hex_to_rgba(theme.COLORS["amber"]))
            bt_line.bind(
                width=lambda i, w: setattr(i, "text_size", (w, None)),
                texture_size=lambda i, ts: setattr(i, "height", ts[1]))
            choices.add_widget(bt_line)
        self._share_show()                   # paint the resting position

        step = WizardStep(index=1, total=total, title=s["title"],
                          body="", hint=s["hint"], warning=s["warning"],
                          input_widget=choices,
                          on_next=self._prelude_next,
                          on_back=self._render_name)
        self.clear_widgets()
        self.add_widget(step)
        self._current = step

    def _share_moved(self, policy):
        """The switch moved. Nothing is decided until Next.

        RE-SEAT THE KNOB (2026-08-14, EVERYWHERE's rebirth): the toggle fires
        its callback but deliberately does not set its own state — the
        node-detail panel re-seats from the stored record instead. Here the
        pending answer IS the record, so the handler must tell the knob, or
        the tap changes the sentence while the switch sits still and reads
        as dead on the glass.
        """
        from monitor import location_share
        self._share_pending = location_share.normalise(policy)
        try:
            self._share_toggle.set_state(self._share_pending)
        except Exception:                                          # noqa: BLE001
            pass
        self._share_show()

    def _bt_moved(self, state):
        """The Bluetooth switch moved. Nothing is decided until Next.
        Same re-seat as _share_moved — the knob must follow the answer."""
        self._bt_pending = "off" if state != "on" else "on"
        try:
            self._bt_toggle.set_state(self._bt_pending)
        except Exception:                                          # noqa: BLE001
            pass

    def _share_show(self):
        """Say what the map switch's current position would do.

        The sentence is the model's (``location_share.consequence_line``),
        which is also what the node's own page shows. Written again here it
        would be two descriptions of one packet, free to drift apart — and
        the drift would be a promise about what leaves the device.
        """
        from monitor import location_share
        policy = getattr(self, "_share_pending", location_share.HIDDEN)
        self._share_consequence.text = location_share.consequence_line(
            policy, self._node_name)

    def _prelude_next(self):
        """Next commits BOTH positions exactly as shown, then the steps begin."""
        self._share_chosen(getattr(self, "_share_pending", None))

    def _share_chosen(self, policy):
        from monitor import location_share
        self._share_location = location_share.normalise(policy)
        self._share_asked = True
        self._bluetooth_on = (getattr(self, "_bt_pending", "off") == "on")
        self._bt_asked = True
        self._i = 0
        self._render_step()

    def _hand_over_name(self, screen_name, job="host"):
        """Hand the destination the name AND the job it has been sent to do.

        A hand-off used to pass only a name, and only via prefill_hostname —
        which the BIRTH screen does not have (it takes prefill_name). So tapping
        "Flash this radio" landed the operator on the full, unscoped birth form
        with an empty name field and no indication of why they were there
        (operator, live, 2026-08-09). The walkthrough already knows the answers
        to every question on that form; making them retype it is asking twice.

        The radio step's job is specifically "flash this board as an RNode",
        which is what begin_guided("host") scopes the screen to. Sending the
        Pi path's own key would scope it to the whole Pi+radio build, which is
        the walkthrough we are standing in the middle of.
        """
        try:
            from kivy.app import App
            app = App.get_running_app()
            scr = getattr(app, f"{screen_name}_screen", None)
            if scr is None:
                return
            name = self._node_name or ""
            if screen_name == "birth":
                # ONE call carries everything. Scoping and naming used to be two
                # calls in a fixed order, and the second (prefill_name) reset the
                # form again and threw the scoping away — see
                # BirthScreen.begin_guided.
                #
                # *job* is the step's own declaration of why it is going there:
                # "host" = flash this radio, "pi" = provision the finished node.
                # Hard-coded to "host", the final step scoped the screen to a
                # radio flash and then looked for a radio that was, by then,
                # attached to the Pi.
                if hasattr(scr, "begin_guided"):
                    scr.begin_guided(
                        job, name=name or None,
                        board_key=getattr(self, "_board_key", None) or None,
                        pi_key=getattr(self, "_pi_key", None) or None,
                        pi_address=getattr(self, "_node_addr", None) or None,
                        # The map answer travels WITH the hand-off, for the same
                        # reason the name does: the walkthrough already asked,
                        # and a screen that has to ask again is a screen that
                        # will be answered differently by a tired operator.
                        share_location=getattr(self, "_share_location", None),
                        # So does the radio's serial, captured when the flash
                        # step handed back (resume() stored it) — the build pins
                        # /dev/rnode with it, since the radio itself is in the
                        # operator's pocket by now.
                        radio_usb_serial=getattr(
                            self, "_radio_usb_serial", "") or None,
                        # And the Bluetooth answer, taken two screens after
                        # the name — the build applies it, so it rides the
                        # same hand-off as everything else already asked.
                        bluetooth=getattr(self, "_bluetooth_on", False))
                elif name and hasattr(scr, "prefill_name"):
                    scr.prefill_name(name)
                return
            if name and hasattr(scr, "prefill_hostname"):
                scr.prefill_hostname(name)
        except Exception:                                          # noqa: BLE001
            pass

    def _step_is_redundant(self, step):
        """True when the medic can SEE this step is already done.

        Used going FORWARD to skip it, and going BACK to step over it. Sharing
        one definition is the point: when only the forward path knew, Back
        decremented onto a step that immediately re-skipped forward, so the
        button did nothing at all while the board stayed plugged in (operator,
        2026-08-02).
        """
        # A STEP THAT DOES WORK, OR GUARDS IT, IS NEVER REDUNDANT.
        #
        # This is the trap the comment in _render_step already describes, walked
        # into a second time. That loop advances _i BEFORE rendering, so any
        # check living in _next() is simply bypassed — which is why the
        # power-compatibility gate had to be re-keyed on the path in 2026-08-03.
        #
        # On 2026-08-09 the radio gate was added to _next() and the same loop
        # ate it. Both new steps carry the connect_board animation, the V4 was
        # plugged in, so BOTH were judged "already done" and skipped in silence:
        # the flash hand-off and the gate guarding it, gone. The operator landed
        # on "Take the radio out" having flashed nothing, and the trace was empty
        # because _next never ran.
        #
        # "The board is plugged in" answers "have you plugged the board in?".
        # It does not answer "has it been flashed?" or "did it pass?" — and a
        # step carrying a `screen` hand-off or a `gate` is asking one of those.
        if step.get("gate") or step.get("screen"):
            return False
        anim = step.get("anim")
        if anim == "connect_pi":
            # PROOF, NOT PRESENCE (2026-08-14): the old EVERYWHERE, still
            # cabled from the night before, answered the presence probe and
            # this skip ate the connect-instruction screen. Only the Pi that
            # can quote the just-imaged card's birth token is THE Pi.
            return self._pi_proven()
        if anim == "connect_board":
            try:
                from ui.hw_factories import local_board_ports
                return bool(local_board_ports())
            except Exception:
                return False
        return False

    # -- the card arriving in the medic's own reader ------------------------

    def _start_card_poll(self, anim):
        """Watch for a card while the "put the SD card in" step is showing.

        Receive-only in spirit: it looks, it celebrates, it does not act. The
        write that follows is destructive and stays behind the operator's press
        — a card appearing must never begin one.
        """
        from kivy.clock import Clock
        self._stop_card_poll()
        self._card_greeted = False

        def tick(_dt):
            import threading

            def work():
                try:
                    from provisioning import pi_imager
                    st = pi_imager.card_status()
                except Exception:                                  # noqa: BLE001
                    return
                if st["state"] == "none":
                    return
                Clock.schedule_once(lambda _d: self._on_card_seen(anim), 0)

            threading.Thread(target=work, daemon=True).start()

        self._card_ev = Clock.schedule_interval(tick, 1.5)

    def _start_card_gone_poll(self, anim):
        """Watch for the card LEAVING the medic's reader, on the step that asks
        for it to be moved into the Pi.

        Operator, 2026-08-09: "node medic should register when the sd card and
        pi are plugged in correctly, no need for user to press button here."
        The medic can see both halves of that — the card vanishing from its own
        reader, and the Pi appearing on USB a minute later — so being asked to
        confirm it is the tool asking to be told what it can already see.

        The card leaving is the signal, not the Pi arriving: the Pi takes the
        better part of a minute to boot, and the very next step is the one that
        waits for it. Advancing when the card goes puts the operator on that
        waiting screen while the waiting is happening, instead of after.

        The button stays, as everywhere else this pattern is used: sensing is an
        accelerator, never the only road out.
        """
        from kivy.clock import Clock
        self._stop_card_poll()

        def tick(_dt):
            import threading

            def work():
                gone = False
                try:
                    from provisioning import pi_imager
                    gone = pi_imager.card_status()["state"] == "none"
                except Exception:                                  # noqa: BLE001
                    gone = False                    # can't tell -> don't advance
                if gone:
                    Clock.schedule_once(lambda _d: self._on_card_gone(anim), 0)

            threading.Thread(target=work, daemon=True).start()

        self._card_ev = Clock.schedule_interval(tick, 1.5)
        tick(0)

    def _on_card_gone(self, anim):
        """The card is out of the reader. NOW the animation may celebrate."""
        self._stop_card_poll()
        if hasattr(anim, "mark_moved"):
            anim.mark_moved()
        from kivy.clock import Clock
        self._advance_token = getattr(self, "_advance_token", 0) + 1
        tok = self._advance_token
        Clock.schedule_once(
            lambda _d: (getattr(self, "_advance_token", None) == tok
                        and self._current is not None and self._next()), 2.0)

    def _stop_card_poll(self):
        ev = getattr(self, "_card_ev", None)
        if ev is not None:
            try:
                ev.cancel()
            except Exception:                                      # noqa: BLE001
                pass
            self._card_ev = None

    def _on_card_seen(self, anim):
        """Fire the green ripple — the medic's "I can see it now" signal, the
        same burst a board gets, so it reads identically whatever the hardware."""
        if getattr(self, "_card_greeted", False):
            return                        # the poll keeps firing; greet once
        self._card_greeted = True
        self._stop_card_poll()
        fn = getattr(anim, "mark_card_found", None)
        if callable(fn):
            try:
                fn()
            except Exception:                                      # noqa: BLE001
                pass
        # SEEING THE CARD IS THE ANSWER TO THIS STEP, so answer it. The step
        # asks the operator to insert a card; once the medic can see one there
        # is nothing left for them to decide here, and leaving a green "Write
        # the card →" button under a finished animation invites the reading
        # that the tool is waiting on them (operator, 2026-08-08).
        #
        # Delayed past the ripple so the acknowledgement is still SEEN — the
        # ripple is the medic's only way of saying "I noticed", and skipping
        # straight past it would undo #71. 1.6s = the 1.4s burst plus a beat.
        #
        # The button stays for the case where NO card is found: detection can
        # fail on a marginal reader, and a step whose only way forward depends
        # on hardware working is a dead end when it doesn't. Two of those have
        # been fixed today already.
        from kivy.clock import Clock
        token = getattr(self, "_advance_token", 0)
        Clock.schedule_once(lambda _dt: self._advance_after_card(token), 1.6)

    def _advance_after_card(self, token):
        """Move on from the card step, unless the operator already has.

        Guarded by the SAME ``_advance_token`` ``_next`` bumps, so a manual tap
        during the ripple wins and this fires into nothing rather than skipping
        a step nobody saw."""
        try:
            if token != getattr(self, "_advance_token", 0):
                return                      # they moved first
            steps = guide_steps(self._path, self._pi_key_for_text())
            cur = steps[self._i] if 0 <= self._i < len(steps) else {}
            if cur.get("anim") != "insert_sd":
                return                      # not on the card step any more
            self._next()
        except Exception:                                          # noqa: BLE001
            pass               # never let an auto-advance break the walkthrough

    def _pi_key_for_art(self, anim_cls):
        """Which Pi the animation should draw.

        The operator's own answer to "Which Raspberry Pi is this?" comes first.
        The USB fallback can only ever name a SoC, and BCM283x is a Zero 2 W, a
        3A+ and a 3B+ at once — so ``_pi_art_key`` is "" for all three, and the
        card-into-the-Pi step used to fall through to a Pi Zero drawing while a
        3A+ sat on the bench. That is the fault behind the operator's restating
        of the rule as absolute (2026-08-06): every picture must be the
        hardware in their hand.

        ConnectPiAnim USED to be an exception, because its socket markers were
        fractions measured on the Zero sprite and handing it another model would
        have moved the board while leaving the rings pointing at nothing. It got
        the per-model treatment on 2026-08-09 (ui.pi_connector_geometry): a
        measured board is marked, an unmeasured one is drawn truthfully with
        nothing marked.
        
        The exception was left in place anyway, so the fix could never reach the
        case it was built for — a 3 A+ operator still got a Pi Zero, which is
        what they reported the very next morning. The rule is the same for every
        animation now: draw the board they chose.
        """
        return getattr(self, "_pi_key", "") or getattr(self, "_pi_art_key", "")

    def _pi_key_for_text(self):
        """Which board the WORDS should describe.

        Deliberately different from the art rule above. For a picture we prefer
        what we DETECTED, because a photo is a claim about the object in front of
        the operator and a measured sprite must match its own numbers. For text
        we prefer what the operator CHOSE, because they are holding the board and
        they told us what it is — and because the connector guidance has to be
        right before anything is detected at all: it is what gets the Pi plugged
        in in the first place.

        Empty when we know neither, which yields the generic line. Naming a
        specific socket on an unidentified board is the bug this exists to stop.
        """
        return getattr(self, "_pi_key", "") or getattr(self, "_pi_art_key", "")

    def _render_step(self):
        steps = guide_steps(self._path, self._pi_key_for_text())
        if not steps or self._i >= len(steps):
            self._finish()
            return
        # IDENTIFY THE PAIR FIRST. This gate used to live in _next() keyed on
        # `self._i == 0` — but _render_step advances _i past redundant steps
        # BEFORE rendering, and step 0 of the Pi path is "connect the radio",
        # which is redundant precisely because the radio is already plugged in.
        # So _i was always 1 by the time _next() looked, the gate never fired,
        # and the operator walked into the four-minute card write with no
        # power-compatibility check at all — defeating the whole reason the
        # radio step was moved first (audit, 2026-08-03).
        #
        # Keyed on the PATH now, which the skip logic cannot mutate.
        if self._path == "pi" and not getattr(self, "_pair_checked", False):
            self._pair_checked = True
            self._render_pick_board()
            return
        # A 'connect your board' step is REDUNDANT when the board is already
        # plugged in (operator feedback 2026-07-31: being told to connect a
        # connected board reads as a bug) — skip it silently.
        # A 'connect this' step is redundant when it is already connected. (A Pi
        # in boot-ROM mode is NOT a serial device, so the work-board check can
        # never see one — _step_is_redundant asks the USB classifier instead.)
        while self._i < len(steps) and self._step_is_redundant(steps[self._i]):
            self._i += 1
        if self._i >= len(steps):
            self._finish()
            return
        self._stop_current()
        self._back_action = self._back        # guided step -> previous step / name
        s = steps[self._i]
        anim_cls = _ANIMS.get(s.get("anim"))
        # Pi steps draw the DETECTED model when we know it. art_key() returns ""
        # for an ambiguous SoC, and the animations fall back to their generic
        # drawing rather than showing a photo of some other Raspberry Pi.
        if anim_cls in _PI_ANIMS:
            anim = anim_cls(pi_key=self._pi_key_for_art(anim_cls))
        else:
            anim = anim_cls() if anim_cls else None
        # +2 on index/total for the two decision screens folded in ahead of
        # these: the name, and the map-sharing question.
        step = WizardStep(index=self._i + 2, total=len(steps) + 2, title=s["title"],
                          body=s["body"], anim=anim, hint=s.get("hint", ""),
                          # A gate refusal outranks the step's standing warning:
                          # it is the reason THIS tap did nothing, and the
                          # standing one is already familiar by now.
                          warning=(getattr(self, "_gate_warning", "")
                                   or s.get("warning", "")),
                          next_text=s.get("next", "Next  →"),
                          on_next=self._next, on_back=self._back)
        self.clear_widgets()
        self.add_widget(step)
        self._current = step
        step.start()
        # A "connect your board" step loops until the medic SENSES a board on USB,
        # then the animation fires its green "Connected!" burst. Until then Next is
        # grayed out — you can't move on without a board actually plugged in.
        if isinstance(anim, ConnectPiAnim):
            # No Next button: plugging the Pi in IS the action, and the medic
            # advances itself when it sees one. The button only ever invited a
            # press that changed nothing (operator, 2026-08-02).
            step.hide_next()
            self._start_pi_poll(anim)
            # No patience-timer Next either (operator, 2026-08-14: "remove
            # that next and let the Node Medic move on by itself"). The
            # escape hatch existed for a Pi that came back on Wi-Fi, which
            # this step's old USB-only watcher could not see — but the proof
            # watcher now walks both roads, so the case the button served is
            # gone. Back remains for a dead card.
        elif s.get("gate"):
            # A GATE THAT HAS PASSED HAS NOTHING TO ASK.
            #
            # This step's whole content is a verdict on work already done: the
            # radio was flashed and verified two screens ago, and the medic is
            # holding the answer. Stopping to be told "yes, carry on" is the
            # tool asking to be told what it already knows (operator,
            # 2026-08-09: "this step also does not need user to press the
            # button").
            #
            # It still STOPS on a failure — that is the entire point of the
            # gate, and the screen then has something the operator must act on.
            #
            # This holds for the gate that guards the provisioning hand-off
            # too. I had it keeping its button on the grounds that nobody should
            # find a minutes-long run already going — but the operator had
            # already pressed "That's the node built" one screen earlier, and
            # this screen says plainly what it is about to do. Consent was given
            # there; asking again here is asking twice (operator, 2026-08-09).
            # The button remains, and now only ever means "try again".
            # A BUILD THAT JUST FAILED SUSPENDS THE SELF-DRIVING.
            #
            # Everything below assumes the gate's answer is the whole story:
            # node answering -> start the work. It is not, once the work has
            # been tried and failed. Left alone, the medic would come back to
            # this step, see the Pi still on the cable, and launch the very
            # same build again by itself, forever — which is why the step has
            # to stop steering and let the operator decide (2026-08-10).
            failed = bool(getattr(self, "_build_failed", False))
            if s["gate"] == "node_online":
                # The only gate whose answer the medic has to go and FETCH.
                # Start looking the moment the step appears, so the wait happens
                # while the operator is reading rather than after.
                self._start_node_poll()
            ok, why = self._gate_state(s["gate"])
            if not ok and why and not self._gate_warning:
                # Say what is being waited for FROM THE START. The warning used
                # to appear only after a blocked press, so a step that advances
                # itself showed nothing at all while it worked.
                self._gate_warning = why
                self._render_step()
                return
            if not ok and s["gate"] == "node_online" and not failed:
                # NO BUTTON WHILE THE WAIT IS REASONABLE.
                #
                # Operator, 2026-08-09: "it looked like I didn't have to press
                # the try again button, if that's the case the button should be
                # replaced with a text box that says please wait."
                #
                # They are right, and the imager already works this way: an
                # escape hatch offered too early invites a press that skips past
                # hardware which was merely slow — a Pi expanding its card on
                # first boot looks identical to a Pi that will never come up.
                # The message carries the wait; the button appears only once the
                # wait is clearly overdue, so nobody is stranded either.
                step.hide_next()
                from kivy.clock import Clock
                tok = getattr(self, "_nav_token", 0)
                Clock.schedule_once(
                    lambda _d: (getattr(self, "_nav_token", None) == tok
                                and self._current is step
                                and step.show_next()), self.WAIT_PATIENCE_S)
            if ok and not failed:
                # Driving itself -> nothing to press. The button is the retry
                # for a gate that is BLOCKED; on one that has passed it is an
                # invitation to race the tool (same rule as the unplug and
                # card-handover steps, operator 2026-08-10).
                step.hide_next()
                from kivy.clock import Clock
                self._advance_token = getattr(self, "_advance_token", 0) + 1
                tok = self._advance_token
                Clock.schedule_once(
                    lambda _d: (getattr(self, "_advance_token", None) == tok
                                and self._current is not None and self._next()),
                    1.6)
            if failed:
                # The way out is the operator's to take, so the button is there
                # from the first moment rather than after the patience timer.
                step.show_next()

        elif isinstance(anim, (DisconnectBoardAnim, RadioToPiAnim)):
            # From here to the end of the walkthrough the radio is DELIBERATELY
            # off the medic, so the global disconnect watcher must stop calling
            # that a fault. It fired on this very step and told the operator to
            # plug the board back in (2026-08-09) — the tool contradicting its
            # own instruction, which is worse than silence.
            self._expect_board_absence(True)
            # "Take the radio out" is something the medic CAN see: it watches
            # the board leave and advances itself (operator's ask, 2026-08-09).
            # "Put the radio onto the Pi" is not — that board lands on the PI's
            # USB, which the medic will never enumerate — so it keeps to its
            # button alone.
            # BEFORE the ConnectBoardAnim branch, because both subclass it — and
            # inheriting that branch would be exactly wrong. It waits for a board
            # to APPEAR on the medic's USB; on these two steps the board is
            # LEAVING (unplugged from the medic) or going onto the PI, where the
            # medic will never see it.
            #
            # The two steps then part company. Taking the radio OUT is something
            # the medic watches and acts on by itself, so its button is the tool
            # asking to be told what it can already see (operator, 2026-08-10:
            # "that doesn't need to be there because when the radio is
            # disconnected the Node Medic automatically detects that and moves
            # to the next screen"). Putting the radio ON THE PI is not — that
            # board lands on the Pi's USB — so that step keeps its button as its
            # only road.
            if isinstance(anim, DisconnectBoardAnim):
                self._start_absence_poll(anim)
                step.hide_next()
                # ...but not into a dead end. Same escape as the connect steps:
                # if the removal is somehow never sensed, the way forward comes
                # back once the wait is plainly overdue rather than never. That
                # is the trap that stranded the operator on step 7 of 8 when a
                # Pi's USB id was missing from a table.
                from kivy.clock import Clock
                tok = getattr(self, "_nav_token", 0)
                Clock.schedule_once(
                    lambda _d: (getattr(self, "_nav_token", None) == tok
                                and self._current is step
                                and step.show_next()), self.WAIT_PATIENCE_S)
        elif isinstance(anim, ConnectBoardAnim):
            # DETECTION IS FEEDBACK, NOT CONSENT.
            #
            # This branch's contract is "the medic senses a board, so hide Next
            # and carry the flow forward by itself" — right for a step whose
            # whole content IS "plug it in". It is WRONG for a step that hands
            # off to real work, or that guards it.
            #
            # Both new radio steps had it and both misbehaved (operator,
            # 2026-08-09, "it asked me to remove the radio awfully fast... I've
            # got a feeling it hasn't been flashed in that time"). They were
            # right: step 1 never showed its "Flash this radio" button and
            # advanced on the mere PRESENCE of a board — and a boot-looping V4
            # is present. Nothing was flashed. Step 2, the gate, auto-advanced
            # past itself for the same reason.
            #
            # So: a step carrying a `screen` hand-off or a `gate` keeps its
            # button and requires a press. The poll still runs, because the
            # green ripple is genuinely useful ("I can see your board") — it
            # just no longer decides anything.
            if s.get("screen") or s.get("gate"):
                self._start_board_poll(
                    anim, on_present=lambda a: (hasattr(a, "mark_connected")
                                                and a.mark_connected()))
            else:
                step.hide_next()      # same: detection drives this step
                self._start_board_poll(anim)
        elif isinstance(anim, InsertSdAnim):
            # THE MEDIC MUST NOTICE THE CARD ARRIVE. The operator asked exactly
            # this while looking at the step (2026-08-07): "should I be getting
            # the green circles when I plug an sd card in reader into medic
            # here?" — and the honest answer was no, because only the imager
            # screen was watching. A step that says "Put the SD card into Node
            # Medic" and then sits there is the same complaint as #71: the medic
            # visibly not knowing what is plugged into it.
            #
            # NO GREEN BUTTON. Asked for twice — "the previous green write card
            # button can be removed and node medic can move on by itself after
            # the sd card found animation completes" (2026-08-08), then "please
            # remove the green button" (2026-08-09).
            #
            # It was kept the first time as an escape hatch for a card the medic
            # fails to see. That was the wrong call. A button whose only purpose
            # is a failure mode still reads, on every SUCCESSFUL run, as "the
            # tool is waiting for you" — sitting under a finished animation,
            # which is precisely how this step came to look stalled.
            #
            # Nothing is trapped: Back is still on the step, and the destructive
            # write is still behind a deliberate press — it lives on the imager's
            # confirmation, which names the device, its size and what is lost.
            # That is the better place for it.
            step.hide_next()
            self._start_card_poll(anim)
        elif isinstance(anim, SdHandoverAnim):
            # The card going INTO the Pi is the mirror of it arriving here —
            # and so is its button. Taking the card out of the reader is the
            # action; the medic sees it go and moves on by itself, so a Next
            # sitting under a finished animation only ever reads as "the tool
            # is waiting for you" (operator's standing rule, 2026-08-10:
            # "anywhere the Node Medic moves the screen on by itself, remove
            # the green button and just let the Node Medic do the work").
            step.hide_next()
            self._start_card_gone_poll(anim)
            from kivy.clock import Clock
            tok = getattr(self, "_nav_token", 0)
            Clock.schedule_once(
                lambda _d: (getattr(self, "_nav_token", None) == tok
                            and self._current is step
                            and step.show_next()), self.WAIT_PATIENCE_S)


    # -- identify the pair BEFORE the card is written -----------------------
    # Operator's flow (2026-08-02): antenna -> the medic registers a radio ->
    # THE OPERATOR SAYS WHICH RADIO -> then the SD card. "If they match we
    # continue; if they're incompatible the user gets recommendations of
    # compatible hardware, and a sub-note saying they could continue this way
    # but they'd need a powered hub."
    #
    # Both halves have to be known here, and the Pi model is one the medic
    # genuinely cannot read — a Pi in boot-ROM mode reports only its SoC family,
    # and BCM2836/2837 covers the Zero 2 W, the 3 and the 2 alike. So it is
    # asked, once, on its own screen.

    def _change_hardware(self):
        """The operator says the confirmed pair is wrong. Take them at their word.

        A remembered board collapses the candidate list to one, and a one-item
        list auto-advances — so without this, "Not right — change" would bounce
        straight back to the same confirmation, which is a trap wearing the
        clothes of an escape hatch.

        This used to ERASE the memory of the board first. That was wrong, and
        it cost a walkthrough (live, 2026-08-09): backing out of the
        confirmation emptied the file, so the six-way grid came straight back
        and the medic had unlearned a board it had been told about correctly.
        A navigation gesture must never destroy learned state. Ignoring the
        memory for this one lookup shows the full list just as well, and
        whatever they pick next overwrites it — which is the correction, made
        by the choice itself rather than by walking backwards past a screen.
        """
        self._detected = None            # re-read rather than trust the snapshot
        self._render_pick_board(force_ask=True)

    def _render_pick_board(self, force_ask=False):
        """Which radio is this? Only ever the candidates the medic cannot rule
        out — the chip and the USB transport have already narrowed the list.

        *force_ask* keeps the list on screen even when only one candidate
        survives, for the operator who has just said the one we chose is wrong.
        """
        self._stop_current()
        self.clear_widgets()
        # NOT _render_step_zero: that sets _i = 0 and re-renders, which skips
        # the redundant radio step straight to step 1 — so Back moved the
        # operator FORWARD into a two-screen loop (audit, 2026-08-03).
        # Through the prelude: the share screen when it was asked, else the
        # name — never past the once-only map question.
        self._back_action = self._back_to_prelude
        from kivy.uix.scrollview import ScrollView
        from ui.widgets.board_card import BoardCard
        wrap = BoxLayout(orientation="vertical", padding=dp(20), spacing=dp(10))
        cands = self._board_candidates(ignore_memory=force_ask)
        if len(cands) == 1 and not force_ask:
            self._board_key = cands[0][0]        # nothing to ask
            self._render_pick_pi()
            return
        wrap.add_widget(_line(tr("Which radio board is this?"), "24sp", bold=True,
                              h=40))
        wrap.add_widget(_line(
            tr("Same chip, same USB — only you can see which one you're "
               "holding."), "15sp", color="text_secondary", h=32))
        wrap.add_widget(_line(
            tr("Answer once; Node Medic remembers this board."),
            "13.5sp", color="green", h=24))
        body = ScrollView()
        col = BoxLayout(orientation="vertical", size_hint_y=None, spacing=dp(10))
        col.bind(minimum_height=col.setter("height"))
        for key, name in cands:
            row = BoxLayout(orientation="horizontal", size_hint_y=None,
                            height=dp(96), spacing=dp(10))
            try:
                row.add_widget(BoardCard(key, name=name, selected=False,
                                         size_hint_x=None, width=dp(150)))
            except Exception:
                pass
            b = Button(text=name, font_size="17sp", bold=True,
                       background_normal="",
                       background_color=theme.hex_to_rgba(theme.COLORS["surface"]),
                       color=theme.hex_to_rgba(theme.COLORS["text_primary"]))
            b.bind(on_release=lambda _b, k=key: self._board_picked(k))
            row.add_widget(b)
            col.add_widget(row)
        body.add_widget(col)
        wrap.add_widget(body)
        _bk = self._back_row()
        if _bk is not None:
            wrap.add_widget(_bk)
        self.add_widget(wrap)

    def _board_candidates(self, ignore_memory=False):
        """[(key, display_name)] the medic could not rule out. Never empty.

        *ignore_memory* re-reads the board WITHOUT the "you already told me
        what this chip is" shortcut, for the operator who has just said the
        remembered answer is wrong. It does not unlearn anything — see
        _change_hardware.

        Re-reads the board when the snapshot cannot be trusted. ``_detected`` is
        taken once, on the "What are you building?" chooser — but on the Pi path
        this question comes several screens and a naming step later, so the
        operator may well have plugged the radio in AFTER that snapshot, or
        swapped it for another. Trusting it produced the exact failure the
        operator hit (2026-08-06): the RAK4631 was connected and cleanly
        identifiable (vendor 239a -> board_key rak4631, no ambiguity at all),
        yet the screen listed the whole ESP32 catalogue — with no RAK4631 in it —
        under copy promising the list had been narrowed. The medic knew and
        asked anyway.
        """
        det = getattr(self, "_detected", None) or {}
        stale = not det.get("boards")
        if not stale:
            try:                       # snapshot's board gone or swapped?
                from ui.hw_factories import local_board_ports
                stale = det.get("port") not in set(local_board_ports())
            except Exception:          # noqa: BLE001
                stale = False
        if stale:
            try:
                from ui.board_detect import detect_board
                from ui.hw_factories import local_board_ports
                from workflows.rnode_boards import RNODE_BOARDS
                fresh = detect_board(list(RNODE_BOARDS.values()),
                                     ports_fn=local_board_ports,
                                     use_memory=not ignore_memory) or {}
                if fresh.get("boards"):
                    self._detected = fresh
                    det = fresh
            except Exception:          # noqa: BLE001
                pass                   # fail OPEN — a wrong exclusion blocks a build
        try:
            out = [(b.key, b.display_name) for b in (det.get("boards") or [])]
            if out:
                return out
        except Exception:
            pass
        try:
            from ui.birth import rnode_board_choices
            return [(b.key, b.display_name) for b in rnode_board_choices()]
        except Exception:
            return []

    def _board_picked(self, key):
        self._board_key = key
        self._remember_board(key)
        self._render_pick_pi()

    def _remember_board(self, key):
        """Tie the operator's answer to THIS chip, so it is never asked twice.

        The medic can read a chip family off the silicon; it cannot read which
        PCB the chip was soldered to, which is why six ESP32-S3 boards look
        identical over USB. The operator can see that in a glance — and having
        told us once, about a MAC that will never change, they should not be
        shown that grid again ("I'm just hoping that we can whittle down this
        section of boards", 2026-08-09).

        Stays on this medic; a chip MAC is a bench note, not something to
        announce. Failing to remember costs one question, so it never raises.
        """
        try:
            mac = (getattr(self, "_detected", None) or {}).get("mac")
            if mac:
                from ui.board_memory import remember
                remember(mac, key)
        except Exception:                                          # noqa: BLE001
            pass

    def _back_to_prelude(self):
        """The screen before the hardware questions that the operator actually
        SAW: the map answer when this lap asked it, else the name. pick_board's
        Back pointed straight at the name, which carried an operator going back
        to change the share answer clean past it — the reasoning already
        written at the steps' back-walk (it is only asked once)."""
        if getattr(self, "_share_asked", False):
            self._render_location_share()
        else:
            self._render_name()

    def _back_from_pick_pi(self):
        """Step over what forward stepped over. With board memory leaving one
        candidate, _render_pick_board answers its own question and re-renders
        pick-pi — so Back was a tap that did nothing (operator, 2026-08-12;
        the 2026-08-03 audit's trap in a new spot). Same skip rule, both
        directions, one definition — _step_is_redundant's lesson."""
        if len(self._board_candidates()) == 1:
            self._back_to_prelude()
        else:
            self._render_pick_board()

    def _render_pick_pi(self):
        """Which Raspberry Pi is this? Asked because it cannot be read."""
        self._stop_current()
        self.clear_widgets()
        self._back_action = self._back_from_pick_pi
        from kivy.uix.scrollview import ScrollView
        from ui.screens.birth_screen import PI_HOSTS
        wrap = BoxLayout(orientation="vertical", padding=dp(20), spacing=dp(10))
        wrap.add_widget(_line(tr("Which Raspberry Pi is this?"), "24sp", bold=True,
                              h=40))
        # NOT "a Pi waiting with a blank card only reports its chip family".
        # That was true on 2026-08-02 (b8c5e37), when the Pi WAS on USB in
        # boot-ROM mode because it was about to be its own card reader. The
        # one-route decision four days later (cadaba3) retired rpiboot: by the
        # time this screen is reached the radio is plugged in and the Pi is
        # still in the operator's hand, so nothing is reporting anything. The
        # honest reason is better anyway — it says why a wrong tap costs a
        # night, which the old sentence never did.
        wrap.add_widget(_line(
            tr("The Pi isn't plugged in yet, so Node Medic can't read it. "
               "This answer decides how its card is written — the wrong one "
               "makes a card that boots and never appears."),
            "15sp", color="text_secondary", h=54))
        body = ScrollView()
        # dp(16), not dp(8): packed rows invited fat-finger picks of the
        # neighbouring model (operator, 2026-08-14), and a wrong Pi here
        # writes a card that boots and never appears.
        col = BoxLayout(orientation="vertical", size_hint_y=None, spacing=dp(16))
        col.bind(minimum_height=col.setter("height"))
        for key, name in PI_HOSTS:
            if key == "none":
                continue                      # this path always has a Pi
            b = Button(text=name, size_hint_y=None, height=dp(58),
                       font_size="17sp", bold=True, background_normal="",
                       background_color=theme.hex_to_rgba(theme.COLORS["surface"]),
                       color=theme.hex_to_rgba(theme.COLORS["text_primary"]))
            b.bind(on_release=lambda _b, k=key: self._pi_picked(k))
            col.add_widget(b)
        body.add_widget(col)
        wrap.add_widget(body)
        back = self._back_row()
        if back is not None:
            wrap.add_widget(back)
        self.add_widget(wrap)

    def _pi_picked(self, key):
        self._pi_key = key
        self._render_confirm_pair()

    def _render_confirm_pair(self):
        """Show BOTH chosen boards back, with their pictures, before anything acts.

        Asked for on the bench, 2026-08-09: "if I miss touched the selection I
        can't tell. there should be a hardware selected confirmation step for
        safety."

        This is not politeness. The Pi answer decides the dwc2 dr_mode written
        onto the card — a 3A+ needs dr_mode=peripheral because its USB-A socket
        has no ID pin. Choose the wrong Pi by a thumb's width and the card boots
        perfectly and never appears on the medic, which is a failure that reads
        as a dead cable and cost most of a night to find. Neither list gives any
        feedback that a row was hit, and both are scrolled with the same thumb
        that selects.

        Pictures, not just names — the operator is holding the hardware, and a
        photo is checkable at a glance where "v3" and "v4" are not.
        """
        self._stop_current()
        self.clear_widgets()
        # _change_hardware, not _render_pick_board: a remembered board leaves one
        # candidate, and a one-candidate list auto-advances straight back here.
        self._back_action = self._change_hardware
        from kivy.uix.scrollview import ScrollView
        from ui.screens.birth_screen import PI_HOSTS
        from ui import board_images

        board_name = dict(self._board_candidates()).get(
            getattr(self, "_board_key", ""), "this radio")
        pi_name = next((n for k, n in PI_HOSTS
                        if k == getattr(self, "_pi_key", "")), "this Pi")

        wrap = BoxLayout(orientation="vertical", padding=dp(18), spacing=dp(10))
        # WRAPPED FOR TRANSLATION. This screen and the "recognised from a
        # previous birth" line below were the only user-facing strings in the
        # birth flow that never went through tr() — added in a hurry on
        # 2026-08-09 and missed by the catalogs ever since.
        wrap.add_widget(_line(tr("Is this what you're holding?"), bold=True,
                              size="22sp", h=38))
        wrap.add_widget(_line(tr("The wrong Pi makes a card that boots and "
                                 "never appears."), size="14.5sp",
                              color="text_secondary", h=28))
        # When the radio came from memory rather than from a tap this lap, say
        # so — otherwise a board nobody chose just appears, and a confirmation
        # you don't know the origin of is one you can't really give.
        if (getattr(self, "_detected", None) or {}).get("remembered"):
            wrap.add_widget(_line(
                tr("Radio recognised — you told Node Medic what it is."),
                size="13.5sp", color="green", h=24))
        body = ScrollView()
        col = BoxLayout(orientation="vertical", size_hint_y=None, spacing=dp(10))
        col.bind(minimum_height=col.setter("height"))
        for label, name, png in (
                ("Radio", board_name,
                 board_images.image_for(getattr(self, "_board_key", "")) or ""),
                ("Raspberry Pi", pi_name,
                 board_images.image_for_pi(getattr(self, "_pi_key", "")) or "")):
            row = BoxLayout(orientation="horizontal", size_hint_y=None,
                            height=dp(96), spacing=dp(12))
            if png:
                from kivy.uix.image import Image as KvImage
                img = KvImage(source=png, size_hint_x=None, width=dp(130))
                img.fit_mode = "contain"
                row.add_widget(img)
            txt = BoxLayout(orientation="vertical")
            txt.add_widget(_line(label, size="13sp", color="text_secondary", h=20))
            txt.add_widget(_line(name, bold=True, size="19sp", h=32))
            row.add_widget(txt)
            col.add_widget(row)
        body.add_widget(col)
        wrap.add_widget(body)

        # "Not right — change" IS the way back, so it is the back control
        # itself, named for this screen. One exit per screen, always the same
        # one — see _back_row.
        btns = self._back_row(label="←  Not right — change", height=56)
        btns.spacing = dp(10)
        # _back_row pads with a stretchy spacer so Back sits alone on the left.
        # Here the primary action takes that room instead — left in, it halved
        # the green button and clipped its label to ", that's right" (photo,
        # 2026-08-09).
        for w in list(btns.children):
            if isinstance(w, BoxLayout):
                btns.remove_widget(w)
        yes = Button(text=tr("Yes, that's right  →"), bold=True, font_size="17sp",
                     background_normal="",
                     background_color=theme.hex_to_rgba(theme.COLORS["green"]),
                     color=theme.hex_to_rgba(theme.COLORS["background"]))
        yes.bind(on_release=lambda *_: self._check_pairing())
        btns.add_widget(yes)
        wrap.add_widget(btns)
        self.add_widget(wrap)

    def _check_pairing(self):
        """Go on if the pair can work; otherwise say so BEFORE the card write.

        TWO gates, impossibility first. can_cable() has encoded a tested fact
        since it was written — a Pi 3 B+ has a hub between the SoC and every
        USB port, so a cable birth is physically impossible — and had no
        production caller (2026-08-12 handover): a 3 B+ operator got a card
        baked, eight steps of walkthrough, and a 150-second wait for an
        enumeration physics forbids. The fact fires here now, before anything
        is written.
        """
        from ui import pi_connectors
        pi_key = getattr(self, "_pi_key", "")
        if not pi_connectors.can_cable(pi_key):
            self._render_cable_verdict(pi_key)
            return
        try:
            from workflows.power_compat import check as _check
            v = _check(pi_key, getattr(self, "_board_key", ""))
        except Exception:
            v = None
        if v and v.get("verdict") in ("blocked", "caution"):
            self._render_power_verdict(v)
            return
        self._resume_steps()

    def _render_cable_verdict(self, pi_key):
        """This Pi cannot be birthed over the cable — full stop, not a risk.

        Unlike the power verdict there is no "you may continue": no cable or
        hub changes what the board's USB topology is. The board's own reason
        (pi_connectors.why_not) is shown, and the way onward is a different Pi.
        """
        self._stop_current()
        self.clear_widgets()
        self._back_action = self._render_pick_pi
        from ui.widgets.callout import Callout
        from ui import pi_connectors
        from ui.screens.birth_screen import PI_HOSTS
        pi_name = next((n for k, n in PI_HOSTS if k == pi_key), "This Pi")
        c = pi_connectors.get(pi_key)
        why = (c.why_not if c is not None and not c.can_cable
               else tr("This board cannot do a cable birth."))
        wrap = BoxLayout(orientation="vertical", padding=dp(18), spacing=dp(10))
        wrap.add_widget(Callout(
            tr("{pi} can't be built this way").format(pi=pi_name), why))
        wrap.add_widget(_line(
            tr("Nothing was written. Pick a Pi that can take the cable — "
               "the picture step shows which port it uses."),
            "15sp", color="text_secondary", h=48))
        wrap.add_widget(BoxLayout())          # spacer: button row sits low
        btns = self._back_row(label=tr("←  Pick a different Pi"), height=56)
        wrap.add_widget(btns)
        self.add_widget(wrap)

    def _resume_steps(self):
        """Carry on with the physical steps, from the TOP of the list.

        This used to start at index 1, on the reasoning that step 0 was "connect
        the radio" and the radio was self-evidently already connected — the pair
        check only happens because a radio was detected. Reasonable then.

        It is wrong now. Step 0 is where the radio is FLASHED AND VERIFIED, and
        skipping it left the operator on the gate step being told "this radio
        hasn't been flashed and verified yet — finish it here first" with no
        step in front of them that could (operator, live, 2026-08-09).

        Start at 0 and let _step_is_redundant decide, which it can now do
        safely: a step carrying a gate or a hand-off is never judged redundant.
        """
        self._i = 0
        self._render_step()

    def _render_power_verdict(self, verdict):
        """This Pi cannot feed this radio. Recommend hardware that can — and say
        plainly that they MAY continue, with a powered hub.

        Not a block. The operator may already own a hub, and it is their bench;
        but they should not find out after the card is written (which is where
        this check used to live).
        """
        self._stop_current()
        self.clear_widgets()
        self._back_action = self._render_pick_board
        from kivy.uix.scrollview import ScrollView
        from ui.widgets.callout import Callout
        from workflows.power_compat import warning_lines
        from ui.screens.birth_screen import PI_HOSTS
        pi_name = next((n for k, n in PI_HOSTS
                        if k == getattr(self, "_pi_key", "")), "this Pi")
        board_name = dict(self._board_candidates()).get(
            getattr(self, "_board_key", ""), "this radio")
        lines = warning_lines(verdict, pi_name, board_name,
                              getattr(self, "_pi_key", ""))
        wrap = BoxLayout(orientation="vertical", padding=dp(18), spacing=dp(8))
        body = ScrollView()
        col = BoxLayout(orientation="vertical", size_hint_y=None, spacing=dp(8))
        col.bind(minimum_height=col.setter("height"))
        head = next((l["text"] for l in lines if l.get("kind") == "head"), "")
        why = " ".join(l["text"] for l in lines if l.get("kind") == "body")
        col.add_widget(Callout(head or tr("These two won't run together"), why))
        for l in lines:
            if l.get("kind") in ("good", "bullet"):
                col.add_widget(_line(
                    l["text"], "15sp",
                    color="green" if l["kind"] == "good" else "text_secondary",
                    h=30))
        col.add_widget(_line(
            tr("You can still build it this way — but the finished node will "
               "need a POWERED USB HUB between the Pi and the radio, or it will "
               "brown out when it transmits."), "14.5sp", color="amber", h=72))
        body.add_widget(col)
        wrap.add_widget(body)
        row = BoxLayout(orientation="horizontal", size_hint_y=None, height=dp(62),
                        spacing=dp(10))
        back = Button(text=tr("Pick different hardware"), font_size="16sp",
                      bold=True, background_normal="",
                      background_color=theme.hex_to_rgba(theme.COLORS["accent"]),
                      color=theme.hex_to_rgba(theme.COLORS["background"]))
        back.bind(on_release=lambda *_: self._render_pick_board())
        on = Button(text=tr("Continue anyway  →"), font_size="15sp",
                    size_hint_x=0.55, background_normal="",
                    background_color=theme.hex_to_rgba(theme.COLORS["surface"]),
                    color=theme.hex_to_rgba(theme.COLORS["text_secondary"]))
        on.bind(on_release=lambda *_: self._resume_steps())
        row.add_widget(back)                  # the safe choice reads first
        row.add_widget(on)
        wrap.add_widget(row)
        _bk = self._back_row()
        if _bk is not None:
            wrap.add_widget(_bk)
        self.add_widget(wrap)

    # -- returning from a hand-off ------------------------------------------

    def has_pending_resume(self) -> bool:
        """Is a walkthrough waiting mid-flight for a screen to hand back?

        Screens ask this before offering "Continue the walkthrough", so the
        offer only appears when there is genuinely somewhere to go back to —
        never on a card written from the BIRTH screen directly.
        """
        return getattr(self, "_resume_at", None) is not None

    def resume(self, result=None):
        """Come back from a hand-off and carry on at the next step.

        *result* is whatever the screen achieved, so the guide can record it and
        gates can consult it. ``{"radio_verified": True}`` is what will arm the
        radio gate once the radio step hands off to the flash.

        Safe to call when nothing is pending — a screen that finishes its work
        after the operator has already walked away must not drag them back.
        """
        at = getattr(self, "_resume_at", None)
        if at is None:
            return False
        self._resume_at = None
        self._trace(f"resumed at step {at + 1} with {result or {}}")
        for key, val in (result or {}).items():
            setattr(self, f"_{key}", val)
        steps = guide_steps(self._path, self._pi_key_for_text())
        # A FAILED BUILD DOES NOT MOVE THE WALKTHROUGH FORWARD. The hand-off
        # remembered where to come back to and came back there whatever
        # happened, so dismissing "Build didn't finish" landed the operator on
        # "That's the node built" — the tool contradicting itself one screen
        # apart, and worse, telling them to take an unbuilt node away and power
        # it up (operator, 2026-08-10). Stay on the step that did the work; its
        # button already reads "Try again".
        if (result or {}).get("build_failed"):
            at = max(0, at - 1)
            # A CACHED PATH IS NOT A SIGHTING — the project's oldest lesson,
            # found again here on 2026-08-12: the gate kept "passing" on the
            # address that answered before the build, while the node had
            # dropped that road (its cable address did not survive its own
            # first-boot reboot). Forget the sighting; the watcher probes
            # both roads fresh, and the retry goes to one that answers NOW.
            self._node_addr = ""
            self._node_probe = None
            self._gate_warning = tr(
                "That build didn't finish. The failed step and its reason are "
                "in the build log. Fix it, then tap Try again — nothing here "
                "is lost.")
        self._i = at
        if self._i >= len(steps):
            self._finish()
        else:
            self._render_step()
        return True

    def cancel_resume(self):
        """Forget the return point — the operator left the walkthrough."""
        self._resume_at = None

    def _trace(self, what):
        """One line per navigation decision, into ui.log.

        Added 2026-08-09 after two changes in one night behaved differently from
        what their tests asserted, and the only way to find out was the operator
        noticing at the bench. Reconstructing "how did I get from step 1 to step
        3" from USB events and guesswork cost more than this line ever will.

        Deliberately print(), which the UI already routes to ui.log unbuffered
        (main.py runs under python3 -u), so it lands in the same capture as
        everything else and needs no new plumbing.
        """
        try:
            print(f"[guide] {what}", flush=True)
        except Exception:                                          # noqa: BLE001
            pass

    def _expect_board_absence(self, expected=True):
        """Ask the app's disconnect watcher to treat a missing board as normal.

        Best-effort: if the app has no such hook the walkthrough still works,
        it just gets the spurious warning back. Never let a cosmetic concern
        raise inside a step render.
        """
        try:
            from kivy.app import App
            app = App.get_running_app()
            fn = getattr(app, "expect_board_absence", None)
            if callable(fn):
                fn(expected)
        except Exception:                                          # noqa: BLE001
            pass

    def _gate_state(self, gate):
        """``(may_proceed, why_not)`` for a gated step.

        A gate is not a nag. It exists where continuing past a failure costs the
        operator real time and then mis-attributes the fault — so the message has
        to say what is wrong and what to do, never just "no".
        """
        if gate == "radio_ready":
            return self._radio_gate()
        if gate == "node_online":
            return self._node_gate()
        return True, ""                    # unknown gate: never block on it

    #: How long a wait may run before the operator is offered a way out. A Pi
    #: answers in 30-45 s from power, but a FIRST boot expands the filesystem and
    #: runs cloud-init, which on a slow card is minutes. Offer the escape after
    #: that is plainly overdue, not during it.
    WAIT_PATIENCE_S = 150.0

    def _node_gate(self):
        """Can the medic actually REACH the node at the other end of the cable?

        Provisioning is an SSH session; starting one against a Pi that is not
        answering produces a wall of connection errors and no clue which of the
        four physical things is wrong. The poll (see _start_node_poll) says
        plainly which stage it got to.

        Never blocks the UI thread: discover_peer waits on hardware, so it runs
        off-thread and this only ever reads the answer it left behind.
        """
        if getattr(self, "_node_addr", ""):
            return True, ""
        if getattr(self, "_node_looking", False):
            return False, tr(
                "Please wait — Node Medic is looking for the Pi, on the "
                "cable and by name on your Wi-Fi. "
                "This starts by itself the moment it answers; there is nothing "
                "to press. Usually under a minute, but a Pi's very first boot "
                "expands its card and runs its setup, which can take up to five.")
        # ONE NUMBER FOR ONE WAIT. This said "up to two minutes on its very
        # first boot" while the waiting message one branch up said five, and
        # the step's own body said "up to a minute, longer on a first boot" —
        # three answers to the same question, on the same screen, within one
        # step. The operator reading the low one gives up while the card is
        # still expanding; the high one is the honest bound (it is what
        # WAIT_PATIENCE_S is reasoned from).
        return False, tr(
            "Node Medic can't reach the Pi — not over the cable, and not by "
            "name on your Wi-Fi either. Check it is plugged in with a DATA "
            "cable and that its power light is on. A Pi takes 30–45 seconds "
            "from power to answering, and up to five minutes on its very "
            "first boot.")

    def _radio_gate(self):
        """Has a radio been flashed and verified for this birth?

        Deliberately consults the BIRTH RESULT rather than "is something
        plugged in". A board can be present, powered, enumerating and still
        useless: the Heltec V4 on the bench on 2026-08-08 was visible to the
        medic the whole evening while boot-looping every 2.4 seconds, because
        its bootloader had been written with --flash_size keep. Presence proves
        nothing; a completed flash-and-verify does.
        """
        if getattr(self, "_radio_verified", False):
            return True, ""
        board = getattr(self, "_board_key", "") or getattr(self, "_board", "")
        if not board:
            return False, tr(
                "No radio board seen yet. Plug it into Node Medic — not into "
                "the Pi.")
        # The reasoning that used to follow ("a radio that can't work costs a
        # four-minute card write...") is the step's own body, two lines above
        # this box. A refusal repeats the argument at the worst moment.
        return False, tr(
            "This radio hasn't been flashed and verified yet. Finish it here "
            "first.")

    def _render_step_zero(self):
        self._i = 0
        self._render_step()

    # -- navigation --------------------------------------------------------
    def _next(self):
        self._advance_token = getattr(self, "_advance_token", 0) + 1   # cancel auto-advance
        # Tapping on IS the retry, so the last failure stops speaking for this
        # attempt — otherwise a second, successful build would still be held
        # behind the first one's warning.
        self._build_failed = False
        steps = guide_steps(self._path, self._pi_key_for_text())
        cur = steps[self._i] if self._i < len(steps) else {}
        # A gated step will not be walked past. See birth_guide_flow for why the
        # radio has one: an unusable radio discovered AFTER a four-minute card
        # write arrives attached to the wrong suspect.
        self._trace(f"next from step {self._i + 1} "
                    f"({cur.get('title', '?')!r}) "
                    f"gate={cur.get('gate') or '-'} screen={cur.get('screen') or '-'}")
        gate = cur.get("gate")
        if gate:
            ok, why = self._gate_state(gate)
            self._trace(f"gate {gate}: {'PASS' if ok else 'BLOCKED'} {why[:60]}")
            if not ok:
                self._gate_warning = why
                self._render_step()
                return
        self._gate_warning = ""
        if cur.get("screen") and self._on_navigate:   # step hands off to a full screen
            self._stop_board_poll()
            # REMEMBER WHERE TO COME BACK TO. A hand-off used to be the END of
            # the walkthrough: control went to the full screen and the guide's
            # remaining steps were simply never reached. That is why the imager
            # has to print "Next: take the card out, put it in the Pi, plug the
            # Pi in" as its own plain text — those are guide steps, stranded on
            # the far side of a one-way door — and why the operator watched that
            # screen "just sit there" on 2026-08-08. It was not waiting for
            # anything; it was the end of the road.
            #
            # It is also why the radio is never flashed mid-flow and why the
            # final "put the radio on the Pi and prove it" step has never
            # existed: neither can be a hand-off if a hand-off cannot return.
            self._resume_at = self._i + 1
            self._trace(f"hand off to {cur['screen']}, resume at step "
                        f"{self._resume_at + 1}")
            self._on_navigate(cur["screen"])
            # Carry the name across. The BIRTH screen route already did this;
            # this one did not, so anyone walking the GUIDE — which is the
            # normal way in, and which asks for the name in its own first step —
            # reached the card form with an empty hostname and had to invent a
            # second name for the same node (operator, 2026-08-02).
            self._hand_over_name(cur["screen"], job=cur.get("job", "host"))
            return
        # After the RADIO step on the Pi path, identify BOTH halves before the
        # card is written: which radio, which Pi, and whether they can run
        # together (operator's flow, 2026-08-02). Nothing here touches hardware
        # — it is two questions and an answer — but it is the last moment when
        # changing your mind is free.
        self._i += 1
        if self._i >= len(steps):
            self._finish()
        else:
            self._render_step()

    def _back(self):
        """One screen back — stepping OVER anything already done.

        Walking back onto a redundant step used to bounce straight forward
        again, so Back did nothing while the radio stayed plugged in. Now it
        keeps going until it finds a screen worth showing, and lands on the
        pairing questions (or the name) when it runs out.
        """
        self._advance_token = getattr(self, "_advance_token", 0) + 1
        steps = guide_steps(self._path, self._pi_key_for_text())
        i = self._i - 1
        while i >= 0 and self._step_is_redundant(steps[i]):
            i -= 1
        if i < 0:
            if getattr(self, "_pair_checked", False):
                self._render_pick_board()    # the screen actually before these
            elif getattr(self, "_share_asked", False):
                # The map question sits between the name and these steps, so
                # Back has to land ON it — otherwise an operator who wanted to
                # change that answer would go back to the name, press Next, and
                # be carried straight past the screen they were going back FOR
                # (it is only asked once).
                self._render_location_share()
            else:
                self._render_name()
            return
        self._i = i
        self._render_step()

    def _finish(self):
        """The walkthrough is over.

        The Pi path ENDS HERE. Every other path is an introduction that hands
        off to the BIRTH screen to do the actual work, and for those this
        hand-off is right. The Pi path is not an introduction: it flashes the
        radio, writes the card, brings the Pi up and provisions it over the
        cable, all through hand-offs that come BACK. Calling _on_complete at the
        end of that dropped the operator onto the same form again, scoped to
        build the node they had just built, and — with the radio now on the Pi —
        greeted them with "No work board on the medic's USB" (audit,
        2026-08-09). The last screen of a successful build must not be a form.
        """
        path = self._path
        self._stop_current()
        if path == "pi":
            self._render_done()
            return
        if self._on_complete:
            self._on_complete(path, self._node_name)

    def _render_done(self):
        """The closing screen: what was built, and the way out.

        Deliberately makes no claim the medic did not measure. The BIRTH screen
        has already shown its own step-by-step outcome and the certificate; this
        says the walkthrough is finished and points at where the node now lives.
        """
        self._stop_current()
        self.clear_widgets()
        self._back_action = None            # nothing behind a finished build
        name = self._node_name or tr("the node")
        wrap = BoxLayout(orientation="vertical", padding=dp(24), spacing=dp(12))
        from kivy.uix.widget import Widget
        wrap.add_widget(Widget())
        wrap.add_widget(_line(tr("{name} is built").format(name=name),
                              "28sp", bold=True, h=44))
        # SAY WHICH ROAD IT ACTUALLY TOOK. This asserted "over the cable" for
        # every birth, and SkyFinger was provisioned over Wi-Fi (2026-08-11).
        # The operator caught it: "was SkyFinger provisioned over the cable as
        # stated here? the text should never mislead the user."
        #
        # It is not a detail. A node reached over Wi-Fi can be repaired from
        # anywhere on the network; one reached over the cable needs a hand and
        # a lead. Saying the wrong one teaches the wrong thing about the node.
        reached = getattr(self, "_reached_at", "") or ""
        if reached.startswith("10.55.0."):
            how = tr("over the cable")
        elif reached:
            how = tr("over your network, at {addr}").format(addr=reached)
        else:
            how = tr("by Node Medic")
        wrap.add_widget(_line(
            tr("Radio flashed and verified, card written, Pi provisioned "
               "{how}. It lives in VITALS from now on.").format(how=how),
            "15sp", color="text_secondary", h=52))
        # "It is off Node Medic and running on its own power now" IS GONE. The
        # medic never checked: the step before this one ASKS for the Pi to be
        # unplugged and given a supply, and then this screen reported it as
        # done. On a screen whose whole rule is "no claim the medic did not
        # measure", it was the only line breaking it — and the one place the
        # operator would look to find out whether the last instruction took.
        #
        # GIVE IT TIME TO BOOT BEFORE LOOKING FOR IT.
        #
        # This screen appears the moment the node is unplugged and powered, and
        # "See it in VITALS" is right there — so the natural next action is to
        # press it immediately and find nothing. The node has to boot, start
        # the mesh software and announce itself first; an empty VITALS in that
        # window is not a fault, but it looks exactly like one, at the end of a
        # build the operator has just spent twenty minutes on (operator asked
        # for this warning, 2026-08-10, on the first birth that ever completed).
        wrap.add_widget(_line(
            tr("Give it two minutes to boot and announce itself. Until then "
               "VITALS will not show it, and nothing is wrong."),
            "14sp", color="amber", h=54))
        wrap.add_widget(Widget())
        row = BoxLayout(orientation="horizontal", size_hint_y=None,
                        height=dp(58), spacing=dp(10))
        vitals = Button(text=tr("See it in VITALS"), bold=True, font_size="16sp",
                        background_normal="",
                        background_color=theme.hex_to_rgba(theme.COLORS["surface"]),
                        color=theme.hex_to_rgba(theme.COLORS["accent"]))
        vitals.bind(on_release=lambda *_: self._go("vitals"))
        done = Button(text=tr("Done"), bold=True, font_size="17sp",
                      background_normal="",
                      background_color=theme.hex_to_rgba(theme.COLORS["green"]),
                      color=theme.hex_to_rgba(theme.COLORS["background"]))
        done.bind(on_release=lambda *_: self._go("home"))
        row.add_widget(vitals)
        row.add_widget(done)
        wrap.add_widget(row)
        self.add_widget(wrap)

    def _go(self, mode):
        try:
            from kivy.app import App
            App.get_running_app().switch_mode(mode)
        except Exception:                                          # noqa: BLE001
            pass

    def _stop_current(self):
        """Leaving a screen. Bumping the token here cancels any render that a
        poll scheduled but has not yet delivered — the comment on _on_detect
        claimed this happened, but nothing outside _on_detect ever moved the
        token, so a 1.6s "Reading the board…" could still land on top of a
        screen the operator had already navigated to (audit, 2026-08-03)."""
        self._nav_token = getattr(self, "_nav_token", 0) + 1
        self._stop_board_poll()
        self._stop_node_poll()
        # The Pi poll must die with the step too. Left running it keeps firing
        # _on_pi_detected and yanks the operator back to the name screen from
        # whatever step they had reached.
        self._stop_detect_pi_poll()
        # And the card poll, for the same reason. Left running it would keep
        # firing the ripple at an InsertSdAnim that is no longer on screen.
        self._stop_card_poll()
        # Dismiss the on-screen keyboard on EVERY step change. It only auto-hides
        # on the ENTER key, so advancing with the Next button carried it into the
        # next step — where it sat covering that step's nav buttons (the
        # "step 3 of 4 looks stalled" report, 2026-07-30: Next was simply hidden
        # behind the lingering keyboard).
        try:
            from kivy.app import App
            kb = getattr(App.get_running_app(), "keyboard", None)
            if kb is not None:
                kb.hide()
        except Exception:
            pass
        if self._current is not None and hasattr(self._current, "stop"):
            self._current.stop()
        self._current = None

    def _start_node_poll(self):
        """Look for the node on the cable link, off-thread, feeding _node_gate.

        discover_peer does three things the UI thread must never do: it waits on
        an interface appearing, it shells out to claim the medic's end of the
        /29, and it probes a TCP port. On the main thread that is a frozen
        screen for up to a minute.

        Short per-attempt timeout, repeated, rather than one long wait — so the
        gate's message can change from "still looking" to a real answer, and so
        a Pi plugged in late is still found.
        """
        from kivy.clock import Clock
        self._stop_node_poll()
        if getattr(self, "_node_addr", ""):
            return                       # already answered; don't go asking again
        self._node_looking = True
        self._node_probe = None          # (addr, its uptime) from the last tick

        #: A fresh card's first boot applies its baked config and REBOOTS
        #: ITSELF once — so "TCP answered" can be the doomed first boot, and
        #: the build's SSH session dies mid-step when the scheduled reboot
        #: lands (three times on 2026-08-12, node 'soon'). Online means: the
        #: node's own uptime read on two consecutive ticks, rising, and past
        #: the window in which it reboots itself.
        NODE_SETTLED_S = 90.0

        def tick(_dt):
            import threading

            def work():
                addr = ""
                try:
                    from provisioning.link import discover_peer
                    addr = discover_peer(timeout=8.0, poll=2.0) or ""
                except Exception:                                  # noqa: BLE001
                    addr = ""
                if not addr:
                    # AND OVER WI-FI. The card we wrote joins the operator's
                    # network, so a Pi whose cable link is dead may be perfectly
                    # reachable by name — and provisioning does not care which
                    # road it takes.
                    #
                    # The imager learned this on the bench in 2026-08-02 ("a
                    # card imaged WITH WiFi comes back on the network, not on
                    # USB... watching only the cable would sit there saying
                    # waiting while the Pi was up and pingable") and this gate,
                    # written later, did not inherit it. Operator asked the
                    # question outright on 2026-08-09, with a Pi that had gone
                    # deaf on the cable: "did you think about the wifi
                    # connection attempt if the cable connection fails?"
                    try:
                        from provisioning.pi_discover import resolve
                        from provisioning.pi_imager import hostnameify
                        host = hostnameify(getattr(self, "_node_name", "") or "")
                        addr = (resolve(host) or "") if host else ""
                    except Exception:                              # noqa: BLE001
                        addr = ""
                if not addr:
                    self._node_probe = None
                    return
                from provisioning.pi_discover import uptime_seconds
                up = uptime_seconds(addr)
                prev = getattr(self, "_node_probe", None)
                self._node_probe = (addr, up)
                if up is None or up < NODE_SETTLED_S:
                    return               # booting, or still inside the window
                if (prev and prev[0] == addr and prev[1] is not None
                        and up > prev[1]):
                    Clock.schedule_once(lambda _d: self._on_node_online(addr), 0)
            threading.Thread(target=work, daemon=True).start()

        self._node_poll = Clock.schedule_interval(tick, 10.0)
        tick(0)

    def _stop_node_poll(self):
        ev = getattr(self, "_node_poll", None)
        if ev is not None:
            try:
                ev.cancel()
            except Exception:                                      # noqa: BLE001
                pass
        self._node_poll = None
        self._node_looking = False

    def _on_node_online(self, addr):
        """The node answered. Record it, stop looking, and re-render so the
        gate's warning clears without the operator having to tap anything."""
        self._stop_node_poll()
        self._node_addr = addr
        self._trace(f"node online at {addr}")
        if self._gate_warning:
            self._gate_warning = ""
            self._render_step()

    def _pi_answering(self):
        """Is the node's Pi here — on the cable, OR answering by name on Wi-Fi?

        One probe for both the forward skip (_step_is_redundant) and the
        connect-step watcher (_start_pi_poll). The watcher looked only at
        lsusb, so a Pi whose card joined Wi-Fi — which the imager bakes in —
        answered and the screen sat on "this moves on by itself" until the
        operator pressed Next by hand (operator, 2026-08-12, node 'soon').
        The node_online gate one step later already walks both roads; this is
        the same lesson the imager learned on 2026-08-02, inherited at last.
        """
        try:
            import subprocess
            from provisioning import pi_usbboot
            out = subprocess.run(["lsusb"], capture_output=True, text=True,
                                 timeout=8).stdout
            if pi_usbboot.classify(out).state != pi_usbboot.ABSENT:
                return True
        except Exception:                                          # noqa: BLE001
            pass
        try:
            from provisioning.pi_discover import resolve
            from provisioning.pi_imager import hostnameify
            host = hostnameify(getattr(self, "_node_name", "") or "")
            return bool(resolve(host)) if host else False
        except Exception:                                          # noqa: BLE001
            return False

    # -- board-presence gate ------------------------------------------------
    def _pi_proven(self):
        """Is THE Pi here — the one whose card the medic just wrote?

        Walks the same two roads as _pi_answering but demands the card's
        birth token back (provisioning.pi_discover.imaged_pi_answers).
        _pi_answering stays for the questions where presence IS the answer;
        advancing the walkthrough is not one of them (2026-08-14: the node
        being REPLACED answered, and the connect instructions were skipped).
        """
        return self._pi_proof()[0]

    def _pi_proof(self):
        """The full proof verdict: ``(proven, imposter_addr, why)``."""
        try:
            from provisioning.pi_discover import imaged_pi_answers
            from provisioning.pi_imager import hostnameify
            host = hostnameify(getattr(self, "_node_name", "") or "")
            if not host:
                return (False, "", "")
            return imaged_pi_answers(host)
        except Exception:                                          # noqa: BLE001
            return (False, "", "")

    def _start_pi_poll(self, anim):
        """Poll for a RASPBERRY PI on USB — boot-ROM, card reader or node.

        Deliberately separate from _start_board_poll: that one enumerates serial
        ports, and a Pi never appears as one. Sharing it meant the "Connect the
        Pi" step could never fire, no matter how many times the operator
        replugged (walkthrough 2026-08-02).
        """
        from kivy.clock import Clock
        self._stop_board_poll()

        def tick(_dt):
            import threading

            def work():
                # Proof, not presence — same law as _step_is_redundant. An
                # answering machine with the WRONG token (the node being
                # replaced, usually) must never advance this screen — and
                # whatever the watcher sees, the screen SAYS (a silent
                # refusal reads as a hang; skyfinger, 2026-08-14).
                if getattr(self, "_proof_inflight", False):
                    return                 # ssh probes outlive the 1.5 s tick
                self._proof_inflight = True
                try:
                    proven, imposter, why = self._pi_proof()
                    if proven:
                        Clock.schedule_once(
                            lambda _d: self._on_board_present(anim), 0)
                        return
                    step = self._current
                    if step is None or not hasattr(step, "set_status"):
                        return
                    if why == "no-token":
                        msg = tr("Found a machine answering at {addr} — but "
                                 "its card carries no birth token, so it "
                                 "can't be proven yours. Re-image the card; "
                                 "or if an old node is still powered, unplug "
                                 "it.").format(addr=imposter)
                    elif why == "wrong-token":
                        msg = tr("A different machine is answering at {addr} "
                                 "— wrong birth token. Unplug or power off "
                                 "the old node.").format(addr=imposter)
                    else:
                        msg = tr("Watching the cable and this Wi-Fi…")
                    Clock.schedule_once(
                        lambda _d, m=msg: (self._current is step
                                           and step.set_status(m)), 0)
                finally:
                    self._proof_inflight = False
            threading.Thread(target=work, daemon=True).start()

        self._board_poll = Clock.schedule_interval(tick, 1.5)
        tick(0)

    def _start_board_poll(self, anim, on_present=None):
        """Poll for a work board on the medic's USB; fire *on_present(anim)* the
        moment one appears (default = the guided-flow handler). Checks off-thread
        (serial enumeration)."""
        from kivy.clock import Clock
        self._stop_board_poll()
        handler = on_present or self._on_board_present

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
                    Clock.schedule_once(lambda _d: handler(anim), 0)
            threading.Thread(target=work, daemon=True).start()

        self._board_poll = Clock.schedule_interval(tick, 1.2)
        tick(0)                                       # check immediately too

    def _on_board_present(self, anim):
        self._stop_board_poll()
        if hasattr(anim, "mark_connected"):
            anim.mark_connected()
        # A board is here — the green burst fires. There is no Next to un-gray
        # any more: the step has none, because the flow carries itself forward
        # after the celebration below.
        # Let the "Connected!" celebration play, then carry the flow forward on its
        # own — detection drives the wizard, no tap needed. A manual Next/Back
        # bumps the token and cancels this pending auto-advance.
        from kivy.clock import Clock
        self._advance_token = getattr(self, "_advance_token", 0) + 1
        tok = self._advance_token
        Clock.schedule_once(
            lambda _d: (getattr(self, "_advance_token", None) == tok
                        and self._current is not None and self._next()), 2.0)

    def _start_absence_poll(self, anim):
        """Watch for the work board LEAVING, and carry the flow forward itself.

        Operator's ask, mid-walkthrough 2026-08-09: "when the Node Medic senses
        the radio's been removed, the user doesn't have to press next — it
        automatically goes to the next step."

        The mirror image of _start_board_poll: those steps advance when a board
        APPEARS, because plugging it in IS the action; this one advances when a
        board GOES, for exactly the same reason. Doing the thing the screen
        asked for should be the whole interaction — a Next tap afterwards is the
        tool asking to be told what it can already see.

        THE BUTTON GOES — but comes back if the sensing fails. It stayed at
        first, on the grounds that a poll which never fires must not strand
        anyone (that is how the operator ended up stuck on step 7 of 8 the same
        day, when a Pi's USB id was missing from a table). But a button that
        does nothing except repeat what the medic already saw is an invitation
        to press it, and the operator asked for it gone (2026-08-10). So the
        caller hides it and schedules it back after WAIT_PATIENCE_S: nothing to
        press while this works, a way out if it does not.
        """
        from kivy.clock import Clock
        self._stop_board_poll()

        def tick(_dt):
            import threading

            def work():
                gone = False
                try:
                    from ui.hw_factories import hardware_present
                    gone = not hardware_present()
                except Exception:                                  # noqa: BLE001
                    gone = False                    # can't tell -> don't advance
                if gone:
                    Clock.schedule_once(lambda _d: self._on_board_absent(anim), 0)
            threading.Thread(target=work, daemon=True).start()

        self._board_poll = Clock.schedule_interval(tick, 1.2)
        tick(0)

    def _on_board_absent(self, anim):
        """The radio is off the medic. Let the picture finish, then move on.

        Same delayed, token-guarded advance as _on_board_present: the animation
        has to be SEEN completing, and a manual tap during that beat must win
        rather than be overtaken by a step that skips past it.
        """
        self._stop_board_poll()
        if hasattr(anim, "mark_removed"):
            anim.mark_removed()
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
