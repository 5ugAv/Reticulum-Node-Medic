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
from ui.birth_guide_flow import ANTENNA_STEP, BIRTH_PATHS, guide_steps
from ui.widgets.wizard_step import WizardStep
from ui.widgets.birth_anims import (ConnectAntennaAnim, ConnectBoardAnim,
                                    InsertSdAnim, InsertSdIntoPiAnim,
                                    ProvisionAnim, ConnectPiAnim)

#: Animation key (from ui.birth_guide_flow) -> the widget class that draws it.
_ANIMS = {"connect_antenna": ConnectAntennaAnim, "connect_board": ConnectBoardAnim,
          "connect_pi": ConnectPiAnim,
          "insert_sd": InsertSdAnim, "insert_sd_pi": InsertSdIntoPiAnim,
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
            body=tr("Plug the node into Node Medic with a USB data cable. I'll detect "
                    "it and decide whether to build it or adopt it as kin."),
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
                            for cert in load_certs():
                                if cert.get("usb_serial") == usb:
                                    c["_kin_by_serial"] = True
                                    c["node_name"] = (c.get("node_name")
                                                      or cert.get("node_name"))
                                    c["board"] = (c.get("board")
                                                  or cert.get("board"))
                                    break
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
                       else tr("for {hours} hours").format(hours=int(heard_h)))
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
                             else tr("Last heard {hours} hours ago.").format(
                                 hours=int(heard_h)))
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
        self.add_widget(wrap)

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
                from ui.onboard_roster import assert_flashable
                assert_flashable(port)               # NEVER the medic's own radio
                import glob
                import os
                import subprocess
                et = (glob.glob(os.path.expanduser(
                    "~/.platformio/packages/tool-esptoolpy/esptool.py")) or [None])[0]
                if not (port and et):
                    ok, msg = False, tr("Couldn't find the board or the flash tool.")
                else:
                    # The detect step's serial banner-read can still hold the
                    # port for a beat when the operator taps Rebirth right
                    # after — Errno 11 'could not exclusively lock' (live,
                    # 2026-07-31). Retry through the race instead of failing.
                    import time as _t
                    for attempt in range(4):
                        r = subprocess.run(
                            ["python3", et, "--port", port, "erase_flash"],
                            capture_output=True, text=True, timeout=120)
                        ok = r.returncode == 0
                        if ok:
                            break
                        msg = (r.stderr or r.stdout or "").strip()[-160:]
                        _t.sleep(3)
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
                    # blank board -> the proven birth flow takes over; old name
                    # prefilled as a starting point (rename freely). Path "any":
                    # a rebirth is EXACTLY when the node's type may change, so
                    # land on an unscoped chooser — the operator picks the
                    # family fresh (RTNode-2400 / RNode / Pi) instead of being
                    # steered back to what it was (operator feedback 2026-07-31).
                    if self._on_complete:
                        self._on_complete("any", old_name)
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
    def _render_intro(self):
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
        wrap.add_widget(_line(tr("Not sure which is which? Tap the  ?  above. Node Medic "
                                 "will guide you the rest of the way."),
                              "16sp", color="text_secondary", h=56))
        # Once the board has been READ, drop the builds it cannot do. RTNode-2400
        # needs an ESP32-S3; a classic ESP32 (LoRa32, T-Beam, Heltec V2) can only
        # ever be an RNode. Offering an impossible choice and failing later
        # wastes the operator's time and teaches them to distrust the list
        # (operator, 2026-08-02). firmware_options() already knew this — the
        # chooser simply wasn't asking it.
        paths, dropped = self._paths_for_connected_board()
        for key, title, subtitle in paths:
            # the Pi card carries a longer description — give it room so it doesn't
            # clip; the shorter cards stay compact.
            h = 170 if key == "pi" else 104
            wrap.add_widget(self._path_button(key, title, subtitle, height=h))
        if dropped:
            # Say WHY it is missing. An option that silently disappears between
            # one visit and the next reads as a glitch.
            wrap.add_widget(_line(dropped, "13.5sp", color="text_secondary", h=40))
        # Mitosis is a different KIND of action — not building a node but cloning
        # the Node Medic itself — so it sits at the end, styled apart, and routes
        # straight to the MITOSIS screen (no guided build steps).
        wrap.add_widget(self._mitosis_button())
        # Adopt a node the medic HEARS over LoRa (no USB) — field fleet enrolment.
        if self._heard_fn is not None:
            wrap.add_widget(self._over_air_button())
        from kivy.uix.widget import Widget
        wrap.add_widget(Widget())
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
        seen = (tr("heard {h}h ago").format(h=f"{lsh:.1f}")
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
        """A choice card that GROWS to fit its own text.

        The title used to be pinned to 30dp and the card to a hard-coded 104
        (170 for the Pi one). "A radio for phone or computer (RNode)" wraps to
        two lines at 21sp, so its second line — the "(RNode)" that names the
        thing — was squashed into the description under it (operator, on the
        5" panel, 2026-08-05). Hard-coded heights only ever fit the strings
        they were measured against, and these strings are translated into
        eight languages, several of which run longer than the English.

        So *height* is now a MINIMUM, not the size: both labels take the height
        their rendered text actually needs, and the card follows. No loop —
        width flows down from the button, height flows up from the text, and
        the two never depend on each other.
        """
        btn = Button(size_hint_y=None, height=dp(height), background_normal="",
                     background_color=theme.hex_to_rgba(theme.COLORS["surface"]))
        pad_y = dp(12)
        inner = BoxLayout(orientation="vertical", size_hint=(None, None),
                          padding=[dp(18), pad_y], spacing=dp(4))

        def _grows(lbl):
            lbl.size_hint_y = None
            lbl.bind(texture_size=lambda i, ts: setattr(i, "height", ts[1]))
            return lbl

        inner.add_widget(_grows(_line(title, "21sp", bold=True)))
        inner.add_widget(_grows(_line(subtitle, "14sp", color="text_secondary")))
        inner.bind(minimum_height=inner.setter("height"))
        # width DOWN from the card (so the labels know where to wrap), height UP
        # from the text (so nothing is ever clipped).
        btn.bind(width=lambda _b, w: setattr(inner, "width", w),
                 pos=lambda _b, v: setattr(inner, "pos", v))
        inner.bind(height=lambda _i, h: setattr(btn, "height", max(dp(height), h)))
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
        total = len(guide_steps(self._path)) + 1
        ti = TextInput(text=self._node_name, multiline=False,
                       hint_text=tr("Name this node  (e.g. Rooftop-East)"),
                       size_hint_y=None, height=dp(58), font_size="33sp")
        bind_field(ti)
        self._name_input = ti
        step = WizardStep(index=0, total=total, title=tr("Name this node"),
                          body=tr("Give this node a short, memorable name — you'll see it "
                                  "on the map and on its birth certificate."),
                          input_widget=ti, next_text=tr("Next  →"),
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
        self._node_name = name
        self._i = 0
        self._render_step()

    def _hand_over_name(self, screen_name):
        """Give a full screen the node name this walkthrough already collected."""
        if not self._node_name:
            return
        try:
            from kivy.app import App
            app = App.get_running_app()
            scr = getattr(app, f"{screen_name}_screen", None)
            if scr is not None and hasattr(scr, "prefill_hostname"):
                scr.prefill_hostname(self._node_name)
        except Exception:
            pass

    def _step_is_redundant(self, step):
        """True when the medic can SEE this step is already done.

        Used going FORWARD to skip it, and going BACK to step over it. Sharing
        one definition is the point: when only the forward path knew, Back
        decremented onto a step that immediately re-skipped forward, so the
        button did nothing at all while the board stayed plugged in (operator,
        2026-08-02).
        """
        anim = step.get("anim")
        if anim == "connect_pi":
            try:
                import subprocess
                from provisioning import pi_usbboot
                out = subprocess.run(["lsusb"], capture_output=True, text=True,
                                     timeout=8).stdout
                return pi_usbboot.classify(out).state != pi_usbboot.ABSENT
            except Exception:
                return False
        if anim == "connect_board":
            try:
                from ui.hw_factories import local_board_ports
                return bool(local_board_ports())
            except Exception:
                return False
        return False

    def _render_step(self):
        steps = guide_steps(self._path)
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
        if anim_cls in (InsertSdIntoPiAnim, ConnectPiAnim):
            anim = anim_cls(pi_key=getattr(self, "_pi_art_key", ""))
        else:
            anim = anim_cls() if anim_cls else None
        # +1 on index/total for the name step folded in ahead of these
        step = WizardStep(index=self._i + 1, total=len(steps) + 1, title=s["title"],
                          body=s["body"], anim=anim, hint=s.get("hint", ""),
                          warning=s.get("warning", ""),
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
        elif isinstance(anim, ConnectBoardAnim):
            step.hide_next()          # same: detection drives this step
            self._start_board_poll(anim)


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

    def _render_pick_board(self):
        """Which radio is this? Only ever the candidates the medic cannot rule
        out — the chip and the USB transport have already narrowed the list."""
        self._stop_current()
        self.clear_widgets()
        # NOT _render_step_zero: that sets _i = 0 and re-renders, which skips
        # the redundant radio step straight to step 1 — so Back moved the
        # operator FORWARD into a two-screen loop (audit, 2026-08-03).
        self._back_action = self._render_name
        from kivy.uix.scrollview import ScrollView
        from ui.widgets.board_card import BoardCard
        wrap = BoxLayout(orientation="vertical", padding=dp(20), spacing=dp(10))
        cands = self._board_candidates()
        if len(cands) == 1:
            self._board_key = cands[0][0]        # nothing to ask
            self._render_pick_pi()
            return
        wrap.add_widget(_line(tr("Which radio board is this?"), "24sp", bold=True,
                              h=40))
        wrap.add_widget(_line(
            tr("Node Medic has narrowed it to these — they share the same chip "
               "and the same kind of USB connection, so only you can see which "
               "one you're holding."), "15sp", color="text_secondary", h=64))
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
        self.add_widget(wrap)

    def _board_candidates(self):
        """[(key, display_name)] the medic could not rule out. Never empty."""
        try:
            det = getattr(self, "_detected", None) or {}
            boards = det.get("boards") or []
            out = [(b.key, b.display_name) for b in boards]
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
        self._render_pick_pi()

    def _render_pick_pi(self):
        """Which Raspberry Pi is this? Asked because it cannot be read."""
        self._stop_current()
        self.clear_widgets()
        self._back_action = self._render_pick_board
        from kivy.uix.scrollview import ScrollView
        from ui.screens.birth_screen import PI_HOSTS
        wrap = BoxLayout(orientation="vertical", padding=dp(20), spacing=dp(10))
        wrap.add_widget(_line(tr("Which Raspberry Pi is this?"), "24sp", bold=True,
                              h=40))
        wrap.add_widget(_line(
            tr("A Pi waiting with a blank card only reports its chip family, "
               "which several models share — so this one is down to you."),
            "15sp", color="text_secondary", h=54))
        body = ScrollView()
        col = BoxLayout(orientation="vertical", size_hint_y=None, spacing=dp(8))
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
        self.add_widget(wrap)

    def _pi_picked(self, key):
        self._pi_key = key
        self._check_pairing()

    def _check_pairing(self):
        """Go on if the pair can work; otherwise say so BEFORE the card write."""
        try:
            from workflows.power_compat import check as _check
            v = _check(getattr(self, "_pi_key", ""), getattr(self, "_board_key", ""))
        except Exception:
            v = None
        if v and v.get("verdict") in ("blocked", "caution"):
            self._render_power_verdict(v)
            return
        self._resume_steps()

    def _resume_steps(self):
        """Carry on with the physical steps, after the radio step."""
        self._i = 1
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
        self.add_widget(wrap)

    def _render_step_zero(self):
        self._i = 0
        self._render_step()

    # -- navigation --------------------------------------------------------
    def _next(self):
        self._advance_token = getattr(self, "_advance_token", 0) + 1   # cancel auto-advance
        steps = guide_steps(self._path)
        cur = steps[self._i] if self._i < len(steps) else {}
        if cur.get("screen") and self._on_navigate:   # step hands off to a full screen
            self._stop_board_poll()
            self._on_navigate(cur["screen"])
            # Carry the name across. The BIRTH screen route already did this;
            # this one did not, so anyone walking the GUIDE — which is the
            # normal way in, and which asks for the name in its own first step —
            # reached the card form with an empty hostname and had to invent a
            # second name for the same node (operator, 2026-08-02).
            self._hand_over_name(cur["screen"])
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
        steps = guide_steps(self._path)
        i = self._i - 1
        while i >= 0 and self._step_is_redundant(steps[i]):
            i -= 1
        if i < 0:
            if getattr(self, "_pair_checked", False):
                self._render_pick_board()    # the screen actually before these
            else:
                self._render_name()
            return
        self._i = i
        self._render_step()

    def _finish(self):
        path = self._path
        self._stop_current()
        if self._on_complete:
            self._on_complete(path, self._node_name)

    def _stop_current(self):
        """Leaving a screen. Bumping the token here cancels any render that a
        poll scheduled but has not yet delivered — the comment on _on_detect
        claimed this happened, but nothing outside _on_detect ever moved the
        token, so a 1.6s "Reading the board…" could still land on top of a
        screen the operator had already navigated to (audit, 2026-08-03)."""
        self._nav_token = getattr(self, "_nav_token", 0) + 1
        self._stop_board_poll()
        # The Pi poll must die with the step too. Left running it keeps firing
        # _on_pi_detected and yanks the operator back to the name screen from
        # whatever step they had reached.
        self._stop_detect_pi_poll()
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

    # -- board-presence gate ------------------------------------------------
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
                present = False
                try:
                    import subprocess
                    from provisioning import pi_usbboot
                    out = subprocess.run(["lsusb"], capture_output=True,
                                         text=True, timeout=8).stdout
                    present = pi_usbboot.classify(out).state != pi_usbboot.ABSENT
                except Exception:
                    present = False
                if present:
                    Clock.schedule_once(lambda _d: self._on_board_present(anim), 0)
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

    def _stop_board_poll(self):
        ev = getattr(self, "_board_poll", None)
        if ev is not None:
            ev.cancel()
            self._board_poll = None
