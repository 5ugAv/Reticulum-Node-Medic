"""On-medic Pi SD imaging — the guided screen (guided birth ▸ Pi path).

Detects a USB card reader, collects the few details a headless Pi needs (hostname,
WiFi, a login password — WiFi pre-filled from the medic's own network so the Pi
lands on the same LAN), then writes Pi OS + a firstboot config to the card. The
destructive write is guarded (never the medic's own disk) and gated behind a
typed-confirmation popup that names the exact card.
"""

from __future__ import annotations

import threading

from kivy.clock import Clock
from kivy.metrics import dp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.popup import Popup
from kivy.uix.scrollview import ScrollView
from kivy.uix.textinput import TextInput

from ui import theme
from ui.onscreen_keyboard import bind_field
from ui.widgets.progress_ring import ProgressRing
from ui.widgets.birth_anims import InsertSdAnim
from ui.widgets.callout import Callout
from ui.widgets.surgery_anim import SurgeryAnim
from provisioning import pi_imager
from provisioning.pi_imager import hostnameify, validate_new_password

_EST_WRITE_S = 240.0                       # rough dd+config time for the fill estimate


def _line(text, size="15sp", color="text_primary", bold=False, h=None):
    lbl = Label(text=text, font_size=theme.font_sp(size), bold=bold,
                halign="left", valign="middle",
                color=theme.hex_to_rgba(theme.COLORS[color]))
    if h is not None:
        lbl.size_hint_y = None
        lbl.height = dp(max(h, theme.line_dp(size)))
    lbl.bind(size=lambda i, v: setattr(i, "text_size", v))
    return lbl


class PiImagerScreen(BoxLayout):
    """``wifi_credentials()`` (optional) pre-fills WiFi from the medic's own network."""

    def __init__(self, wifi_credentials=None, **kwargs):
        super().__init__(**kwargs)
        self.orientation = "vertical"
        self.padding = dp(16)
        self.spacing = dp(8)
        self._wifi_credentials = wifi_credentials
        self._target = None
        self._busy = False
        # Which route opened the card. Decides what the operator is told to do
        # afterwards: a card reached through the Pi itself is ALREADY in the Pi.
        self._via_pi_reader = False
        self._pi_name = ""
        self.add_widget(_line("Image a Raspberry Pi SD card", bold=True,
                              size="22sp", h=40))
        body = ScrollView()
        self.col = BoxLayout(orientation="vertical", size_hint_y=None, spacing=dp(10))
        self.col.bind(minimum_height=self.col.setter("height"))
        body.add_widget(self.col)
        self.add_widget(body)
        try:
            from ui.widgets.scroll_hint import attach as _attach_hint
            _attach_hint(body, parent=self)
        except Exception:
            pass
        self._build()

    def prefill_hostname(self, name):
        """Seed the hostname from the node name the operator already typed.

        They named the node once on the BIRTH screen; asking again here invites
        two different names for one node — and the hostname is what the medic
        later resolves to find it, so a mismatch means a node it cannot reach
        (operator, 2026-08-02).
        """
        self._prefill_name = str(name or "")
        ti = (getattr(self, "_inputs", {}) or {}).get("hostname")
        if ti is not None and not ti.text.strip():
            ti.text = hostnameify(self._prefill_name)

    def reset_for_new_card(self):
        """Forget the last card. Called when BIRTH sends us here for a new node.

        This screen is a singleton, so state survives between nodes. If the last
        node's card was opened through the Pi over rpiboot and the next one sits
        in a USB card reader, a stale flag would tell the operator to "leave the
        card where it is" when it is in fact in a reader on the desk. A new
        birth is the real boundary, so it is where the reset belongs.

        Rebuilds too, otherwise re-entering shows the LAST card's "Done!" panel
        — which claims a card is written when none is.
        """
        if self._busy:
            return                      # a write is running; leave it alone
        self._via_pi_reader = False
        self._pi_name = ""
        self._target = None
        self._kept = {}                 # a new node, so no carried-over answers
        self._build()

    def set_pi_name(self, pi_name):
        """Which Raspberry Pi this card is for, in the operator's words.

        Used only for wording — "Unplug the Pi Zero 2 W" beats "unplug the
        Raspberry Pi" when there may be more than one thing plugged in.
        """
        self._pi_name = str(pi_name or "")

    def _field(self, label, hint, key, password=False, numeric=False):
        self.col.add_widget(_line(label, size="15sp", color="accent", bold=True, h=24))
        ti = TextInput(hint_text=hint, multiline=False, password=password,
                       size_hint_y=None, height=dp(48), font_size="27sp")
        bind_field(ti, numeric=numeric)
        prev = (getattr(self, "_kept", {}) or {}).get(key)
        if prev:
            ti.text = prev              # survived a rebuild — don't retype it
        self._inputs[key] = ti
        if not password:
            self.col.add_widget(ti)
            return ti
        # A masked field typed on a touchscreen keypad is easy to get wrong with
        # no way to check — and a mistyped login password is only discovered
        # much later, when the Pi refuses to let you in (operator, 2026-08-02).
        # Starts hidden; revealing is the operator's choice.
        row = BoxLayout(orientation="horizontal", size_hint_y=None,
                        height=dp(48), spacing=dp(8))
        row.add_widget(ti)
        btn = Button(text="Show", size_hint=(None, 1), width=dp(96), bold=True,
                     font_size="15sp", background_normal="",
                     background_color=theme.hex_to_rgba(theme.COLORS["surface"]),
                     color=theme.hex_to_rgba(theme.COLORS["accent"]))

        def _toggle(*_a):
            ti.password = not ti.password
            btn.text = "Show" if ti.password else "Hide"
            try:
                ti.focus = True          # keep the keypad up so typing carries on
            except Exception:
                pass
        btn.bind(on_release=_toggle)
        row.add_widget(btn)
        self.col.add_widget(row)
        return ti


    # -- the Pi is its own card reader --------------------------------------

    def _offer_pi_as_reader(self):
        """If a brand-new Pi is plugged in, make it present its card — itself.

        Operator requirement (2026-08-02): *"when it's initially plugged in and
        Node Medic recognises it as a brand new Pi it needs to automatically
        turn it into a card reader as we start the birth process."* Asking for a
        USB card reader when the Pi in front of you can BE one is the tool
        making its own limitation the operator's problem.

        Returns True when it has taken over the screen (either working on it, or
        explaining why it can't), False to fall through to the reader prompt.
        """
        try:
            import subprocess
            from provisioning import pi_usbboot
            out = subprocess.run(["lsusb"], capture_output=True, text=True,
                                 timeout=10).stdout
            state = pi_usbboot.classify(out)
        except Exception:                       # never block imaging on this
            return False

        if state.state == pi_usbboot.GADGET:
            self.col.add_widget(_line(
                "This Raspberry Pi already has an operating system and starts "
                "up as a node — it isn't offering its card.", size="15sp",
                color="amber", h=48))
            # This screen used to say "wipe it first" and then offer no way to
            # do it, which is an instruction with a dead end. The engine exists
            # (provisioning.decommission) so give the operator the action here.
            self._add_wipe_offer()
            return True
        if state.state != pi_usbboot.BOOTROM:
            return False                        # nothing plugged in — normal prompt

        self.col.add_widget(_line("Raspberry Pi detected", size="17sp",
                                  color="green", bold=True, h=28))
        self.col.add_widget(_line(
            "It's waiting with a blank card. Node Medic is opening the card now "
            "— no card reader needed.", size="15sp", h=44))
        status = _line("Waking the Pi's card…", size="14sp", color="accent", h=26)
        self.col.add_widget(status)
        ring = ProgressRing(size_hint_y=None, height=dp(160))
        self.col.add_widget(ring)
        try:
            ring.start()
        except Exception:
            pass

        def work():
            r = pi_usbboot.ensure_card_reader(
                lambda: subprocess.run(["lsusb"], capture_output=True,
                                       text=True, timeout=10).stdout,
                pi_imager.list_target_disks,
                on_progress=lambda m: Clock.schedule_once(
                    lambda _dt, msg=m: setattr(status, "text", msg), 0))
            Clock.schedule_once(lambda _dt: self._reader_done(r), 0)

        threading.Thread(target=work, daemon=True).start()
        return True

    # -- wipe and start over ------------------------------------------------

    def _add_wipe_offer(self):
        """Offer to make this Pi's card unbootable so it can be built again.

        Gated behind a SLIDE, never a tap: this erases a working node. Modelled
        on the vault reset for the same reason — the cost of an accidental tap
        is far higher than the cost of a deliberate drag (operator spec
        2026-08-02).
        """
        self.col.add_widget(_line(
            "Build it again from scratch?", bold=True, size="16sp",
            color="text_primary", h=28))
        self.col.add_widget(_line(
            "Node Medic can wipe this Pi's card so it starts fresh. It erases "
            "the operating system, this node's identity and its certificate — "
            "it will no longer be the node it is now, and it can't be undone.",
            size="13.5sp", color="text_secondary", h=76))
        self.col.add_widget(_line(
            "Your mesh is not affected. Other nodes keep running.",
            size="13sp", color="green", h=22))
        self._wipe_status = _line("", size="13.5sp", color="amber", h=44)
        from ui.widgets.slide_to_power import SlideToPowerOff
        self.col.add_widget(SlideToPowerOff(
            on_power_off=self._do_wipe,
            hint_text="slide to wipe and start over  →"))
        self.col.add_widget(self._wipe_status)

    def _do_wipe(self):
        """Wipe over the cable, off-thread. Names the node it is about to erase
        and refuses if the medic can't confirm which node that is."""
        status = self._wipe_status
        status.color = theme.hex_to_rgba(theme.COLORS["text_secondary"])
        status.text = "Finding the Pi on the cable…"

        def work():
            msg, ok = "", False
            try:
                from provisioning.pi_discover import cable_address
                from provisioning.decommission import decommission
                from transport.connection import SSHConnection
                addr = cable_address(timeout=8.0)
                if not addr:
                    msg = ("Couldn't reach the Pi over the cable. It needs to "
                           "be plugged into Node Medic by its DATA port.")
                else:
                    conn = SSHConnection(addr, user="pi")
                    # expected_hostname left empty on purpose: the medic has no
                    # independent claim about WHICH node is on the cable here,
                    # and asserting one it cannot check would be worse than the
                    # guard decommission() already applies (it refuses to touch
                    # anything calling itself the medic).
                    r = decommission(conn)
                    ok, msg = r.ok, r.message
            except Exception as exc:                      # noqa: BLE001
                msg = f"Couldn't wipe it: {str(exc)[:90]}"
            Clock.schedule_once(lambda _dt: self._wipe_done(ok, msg), 0)

        threading.Thread(target=work, daemon=True).start()

    def _wipe_done(self, ok, msg):
        status = self._wipe_status
        status.color = theme.hex_to_rgba(
            theme.COLORS["green" if ok else "amber"])
        status.text = msg or ("Wiped." if ok else "Couldn't wipe it.")
        if not ok:
            return
        # The one step the medic cannot do for itself: uhubctl on the Pi 5 root
        # hub does not cut VBUS (measured — the device stays powered across a
        # 20-second "off"), so the operator has to power-cycle it.
        self.col.add_widget(_line(
            "Now unplug the Pi, wait ten seconds, then plug it back in. It will "
            "start up with a blank card and offer it to Node Medic.",
            size="14.5sp",
            color="accent", h=48))
        again = Button(text="I've replugged it — look again", size_hint_y=None,
                       height=dp(52), bold=True, background_normal="",
                       background_color=theme.hex_to_rgba(theme.COLORS["accent"]),
                       color=theme.hex_to_rgba(theme.COLORS["background"]))
        again.bind(on_release=lambda *_: self._build())
        self.col.add_widget(again)

    def _reader_done(self, result):
        """Back on the UI thread once the Pi has (or hasn't) opened its card."""
        if result.ok:
            # Remember HOW we got at the card: it is inside the Pi, so when the
            # write finishes the operator must not be told to go and fetch it.
            self._via_pi_reader = True
            self._build()                       # the card is a target now
            return
        self.col.clear_widgets()
        self.col.add_widget(_line("Couldn't open the Pi's card", size="17sp",
                                  color="amber", bold=True, h=28))
        self.col.add_widget(_line(result.message, size="14sp", h=64))
        again = Button(text="Try again", size_hint_y=None, height=dp(52),
                       bold=True, background_normal="",
                       background_color=theme.hex_to_rgba(theme.COLORS["accent"]),
                       color=theme.hex_to_rgba(theme.COLORS["background"]))
        again.bind(on_release=lambda *_: self._build())
        self.col.add_widget(again)

    def _build(self):
        # Keep whatever the operator has already typed. This screen rebuilds
        # whenever USB changes, and rpiboot MAKES USB change — the Pi
        # re-enumerates as it starts presenting its card. So filling in the
        # password and then watching the form reset itself was not a rare race,
        # it was the normal path (walkthrough, 2026-08-02).
        keep = {}
        for k, t in (getattr(self, "_inputs", None) or {}).items():
            try:
                if t.text.strip():
                    keep[k] = t.text
            except Exception:
                pass
        self._kept = {**getattr(self, "_kept", {}), **keep}
        self.col.clear_widgets()
        self._inputs = {}
        targets = pi_imager.list_target_disks()
        if not targets and self._offer_pi_as_reader():
            return                      # a Pi is plugged in; we took the screen
        if not targets:
            # No card reader — show the insert animation + a rescan.
            self.col.add_widget(_line(
                "Put the Pi's microSD into a USB card reader and plug it into Node "
                "Medic (it has no built-in card slot).", size="15sp", h=48))
            anim = InsertSdAnim(size_hint_y=None, height=dp(180))
            self.col.add_widget(anim)
            anim.start()
            rescan = Button(text="I've plugged it in — look again", size_hint_y=None,
                            height=dp(52), bold=True, background_normal="",
                            background_color=theme.hex_to_rgba(theme.COLORS["accent"]),
                            color=theme.hex_to_rgba(theme.COLORS["background"]))
            rescan.bind(on_release=lambda *_: self._build())
            self.col.add_widget(rescan)
            return
        self._target = targets[0]
        self.col.add_widget(_line(
            f"Card detected: {self._target['model'] or 'USB card'} "
            f"({self._target['size']}) at {self._target['path']}",
            size="14sp", color="green", h=24))
        self.col.add_widget(_line(
            "Everything on this card will be erased.", size="13sp",
            color="warning_yellow", h=22))

        hn = self._field("Node hostname", "e.g. propagation-01", "hostname")
        if not hn.text.strip():
            hn.text = hostnameify(getattr(self, "_prefill_name", ""))
        ssid, psk = ("", "")
        if self._wifi_credentials:
            try:
                ssid, psk = self._wifi_credentials()
            except Exception:
                ssid, psk = ("", "")
        self._field("WiFi network", "SSID the Pi should join", "ssid").text = ssid
        self._field("WiFi password", "WiFi password", "psk", password=True).text = psk
        self._field("Set a login password", "for user 'pi' (SSH login)", "pw",
                    password=True)
        # WRITE IT DOWN. This name and password are how anyone reaches this node
        # over SSH for the rest of its life, and Node Medic does not keep the
        # password — it is hashed onto the card and cannot be read back. A node
        # whose password is lost can only be recovered by re-imaging it
        # (operator, 2026-08-02).
        # Boxed, not just coloured: this is the one thing on the screen the
        # operator has to DO before pressing Write, and an amber heading in a
        # column of coloured headings did not read as different in kind
        # (operator, 2026-08-02).
        self.col.add_widget(Callout(
            "Write these down now!",
            "The node name and this password are how you reach this Pi over SSH "
            "later. Node Medic does NOT store the password — it goes onto the "
            "card as a one-way hash and can't be read back. Lose it and the only "
            "way in is to image the card again."))

        write = Button(text="Write SD card", size_hint_y=None, height=dp(56), bold=True,
                       font_size="18sp", background_normal="",
                       background_color=theme.hex_to_rgba(theme.COLORS["green"]),
                       color=theme.hex_to_rgba(theme.COLORS["background"]))
        write.bind(on_release=lambda *_: self._confirm())
        self.col.add_widget(write)
        self._status = _line("", size="13.5sp", color="text_secondary", h=26)
        self.col.add_widget(self._status)

    def _vals(self):
        return {k: v.text.strip() for k, v in self._inputs.items()}

    def _confirm(self):
        if self._busy or not self._target:
            return
        v = self._vals()
        if not v.get("hostname"):
            self._status.text = "Enter at least a hostname and a login password."
            self._status.color = theme.hex_to_rgba(theme.COLORS["red"])
            return
        # A too-short password is as unrecoverable as a mistyped one: it is
        # hashed onto the card and can never be read back. Empty was already
        # refused here; the length floor was not.
        pw_ok, pw_msg = validate_new_password(v.get("pw", ""))
        if not pw_ok:
            self._status.text = pw_msg
            self._status.color = theme.hex_to_rgba(theme.COLORS["red"])
            return
        box = BoxLayout(orientation="vertical", spacing=dp(10), padding=dp(12))
        msg = Label(halign="center", valign="middle", markup=True, text=(
            f"Write Pi OS to [b]{self._target['model'] or 'the USB card'} "
            f"({self._target['size']})[/b] at [b]{self._target['path']}[/b]?\n\n"
            "[color=ff5555]This ERASES everything on that card.[/color] It cannot be "
            "the medic's own storage — only a removable USB card is allowed."))
        msg.bind(size=lambda i, val: setattr(i, "text_size", val))
        box.add_widget(msg)
        row = BoxLayout(orientation="horizontal", size_hint_y=None, height=dp(52),
                        spacing=dp(8))
        popup = Popup(title="Confirm — this erases the card", content=box,
                      size_hint=(0.9, 0.6))
        cancel = Button(text="Cancel", background_normal="",
                        background_color=theme.hex_to_rgba(theme.COLORS["surface"]))
        cancel.bind(on_release=popup.dismiss)
        go = Button(text="Erase & write", bold=True, background_normal="",
                    background_color=theme.hex_to_rgba(theme.COLORS["red"]),
                    color=theme.hex_to_rgba(theme.COLORS["background"]))
        go.bind(on_release=lambda *_: (popup.dismiss(), self._write(v)))
        row.add_widget(cancel)
        row.add_widget(go)
        box.add_widget(row)
        popup.open()

    def _write(self, v):
        self._busy = True
        self.col.clear_widgets()
        self.col.add_widget(_line(f"Imaging {self._target['path']} as "
                                  f"'{v['hostname']}'…", bold=True, size="16sp", h=30))
        ring_row = BoxLayout(orientation="horizontal", size_hint_y=None, height=dp(80),
                             spacing=dp(12))
        self._ring = ProgressRing()
        ring_row.add_widget(self._ring)
        ring_row.add_widget(_line("Writing Pi OS and applying your settings. This "
                                  "takes a few minutes.",
                                  size="13sp", color="accent"))
        self.col.add_widget(ring_row)
        # Boxed, because this is the window where the operator can actually
        # WRECK something, and a few minutes of a spinning ring is exactly when
        # someone tidies cables or decides to plug the radio in (operator,
        # 2026-08-02). Interrupting a write leaves a half-written card that
        # boots far enough to look plausible and then fails.
        # Kept on the screen object: the moment the write finishes this exact
        # box has to STOP saying "don't unplug anything", because the very next
        # instruction is to unplug the Pi. Leaving it up put two contradictory
        # instructions on one screen with the louder one wrong (operator,
        # 2026-08-06: "conflicting instructions at this stage").
        self._callout = Callout(
            "Leave everything alone until this finishes",
            "Don't unplug anything from Node Medic, don't take the card out, "
            "and don't power Node Medic off. A card interrupted part-way "
            "through has to be written again from the start.")
        self.col.add_widget(self._callout)
        # The wait is minutes long with nothing to look at, and the most costly
        # thing an operator can do in that window is decide it has hung and pull
        # the card. The theatre shows organs landing one at a time and a heart
        # trace steadying, so "is this still working?" is answered without
        # anyone having to trust a number (operator's scene, 2026-08-02).
        self._surgery = SurgeryAnim(pi_key=self._pi_art_key(),
                                    size_hint_y=None, height=dp(230))
        self.col.add_widget(self._surgery)
        self._stage_lbl = _line(pi_imager.current_stage_label(0.0),
                                size="15sp", color="accent", h=28)
        self.col.add_widget(self._stage_lbl)
        self._surgery.start()
        import time
        self._t0 = time.monotonic()
        self._ev = Clock.schedule_interval(self._tick, 0.3)

        dev, path = self._target, self._target["path"]

        def work():
            ok, msg = pi_imager.flash(
                path, v["hostname"], "pi", v["pw"],
                wifi_ssid=v.get("ssid", ""), wifi_password=v.get("psk", ""))
            if ok:
                # Remember what we just named it, so BIRTH can offer the Pi's
                # address instead of asking the operator for an IP they have
                # no way of knowing (operator report 2026-08-01).
                try:
                    from provisioning.pi_discover import record_imaged_pi
                    record_imaged_pi(v["hostname"], "pi")
                except Exception:
                    pass
            Clock.schedule_once(lambda dt: self._done(ok, msg), 0)
        threading.Thread(target=work, daemon=True).start()

    def _card_is_in_the_pi(self):
        """Is the card we just wrote sitting inside a Raspberry Pi right now?

        ASKED of the hardware, not remembered. The stored _via_pi_reader flag
        only knew about a Pi that THIS visit converted with rpiboot — so a Pi
        still presenting its card from an earlier attempt looked like a USB
        reader, and the operator was told to take a card out of a reader that
        was not in the room (walkthrough, 2026-08-02).

        A Pi in mass-storage mode announces itself on USB, so the honest answer
        is one lsusb away. Falls back to the flag if the check cannot run.
        """
        try:
            import subprocess
            from provisioning import pi_usbboot
            out = subprocess.run(["lsusb"], capture_output=True, text=True,
                                 timeout=8).stdout
            return pi_usbboot.classify(out).state == pi_usbboot.CARD_READER
        except Exception:
            return bool(self._via_pi_reader)

    def _pi_art_key(self):
        """The detected Pi model, for the surgeon's portrait. "" = generic."""
        try:
            from kivy.app import App
            guide = getattr(App.get_running_app(), "birth_guide_screen", None)
            return getattr(guide, "_pi_art_key", "") or ""
        except Exception:
            return ""

    def _tick(self, _dt):
        import time
        frac = min(0.95, (time.monotonic() - self._t0) / _EST_WRITE_S)
        self._ring.set_fraction(frac)
        surgery = getattr(self, "_surgery", None)
        if surgery is not None:
            surgery.set_fraction(frac)
        lbl = getattr(self, "_stage_lbl", None)
        if lbl is not None:
            lbl.text = pi_imager.current_stage_label(frac)

    def _done(self, ok, msg):
        self._busy = False
        ev = getattr(self, "_ev", None)
        if ev is not None:
            ev.cancel()
        surgery = getattr(self, "_surgery", None)
        if surgery is not None:
            if ok:
                surgery.set_fraction(1.0)      # smile + thumbs up + happy beep
            else:
                surgery.stop()                 # a failure gets no celebration
        lbl = getattr(self, "_stage_lbl", None)
        if lbl is not None:
            lbl.text = (pi_imager.current_stage_label(1.0) if ok
                        else "The operation stopped.")
        self._ring.set_fraction(1.0 if ok else self._ring.fraction)
        self.col.add_widget(_line("✓  Done!" if ok else "✗  Couldn't finish",
                                  bold=True, size="19sp",
                                  color="green" if ok else "red", h=30))
        # On success the next-steps block below carries the instruction, and
        # flash()'s own prose still ends "Put it in the Pi and power on" — which
        # is wrong when the card never left the Pi. Failures keep the full text:
        # there the message IS the information (operator, 2026-08-02).
        if not ok:
            self.col.add_widget(_line(msg, size="14sp", h=60))
        if ok:
            # Deliberately the ONLY action. There is no "image another card":
            # every card carries one node's hostname, password and identity, so
            # a second card off this same form would be a clone of the node just
            # built, not a new one (operator, 2026-08-02). Another card means
            # another node, which means starting another birth — and that is
            # where the next name gets asked for.
            self._add_next_steps()
            return
        retry = Button(text="Try again", size_hint_y=None, height=dp(52),
                       bold=True, font_size="16sp", background_normal="",
                       background_color=theme.hex_to_rgba(theme.COLORS["accent"]),
                       color=theme.hex_to_rgba(theme.COLORS["background"]))
        retry.bind(on_release=lambda *_: self._build())
        self.col.add_widget(retry)

    def _add_next_steps(self):
        """The forward path, as the primary action.

        A written card is the middle of building a node, not the end of one. The
        only thing offered here used to be "Image another card" — which reads as
        "that's the job done" and leaves the operator to work out for themselves
        what to do with the Pi in their other hand (operator, 2026-08-02).
        """
        v = {k: t.text.strip() for k, t in (self._inputs or {}).items()
             if hasattr(t, "text")}
        plan = pi_imager.next_steps_after_imaging(
            self._card_is_in_the_pi(), hostname=v.get("hostname", ""),
            pi_name=self._pi_name, wifi_ssid=v.get("ssid", ""))
        # THE BOX CHANGES ITS MIND HERE. Same position on the screen, so the eye
        # that has been resting on "leave everything alone" for four minutes
        # lands on the thing to do next — in green, because the operator has
        # been trained by the yellow one to read that colour as "don't".
        self._swap_callout_to_action(plan)
        self.col.add_widget(_line(plan["title"], bold=True, size="17sp",
                                  color="accent", h=30))
        for step in plan["steps"]:
            self.col.add_widget(_line(step, size="15sp", h=26))
        self.col.add_widget(_line(plan["note"], size="14sp", color="green", h=40))
        # No button. Replugging the Pi is a physical act the medic can SEE — a
        # freshly imaged card boots into the USB-gadget link and announces
        # itself — so asking for a press afterwards is asking the operator to
        # tell the medic something it already knows (operator, 2026-08-02, the
        # same rule as the connect steps in the guided flow).
        self._boot_lbl = _line("Waiting for the Pi to come back…",
                               size="15sp", color="accent", h=30)
        self.col.add_widget(self._boot_lbl)
        self._start_boot_poll(v.get("hostname", ""))

    def _start_boot_poll(self, hostname=""):
        """Watch for the imaged Pi booting its new card, then move on by itself.

        The signal is the USB-gadget link: a card written by this screen brings
        one up on first boot, so seeing it means the card WORKS -- a far better
        thing to wait for than a timer. First boot expands the filesystem and
        runs cloud-init, so it can take a couple of minutes; the escape hatch
        only appears once that is clearly overdue, rather than inviting an early
        press that skips past a Pi still starting.
        """
        import time
        self._boot_t0 = time.monotonic()
        self._boot_host = hostname or ""
        self._stop_boot_poll()

        def tick(_dt):
            def work():
                state, ip = None, ""
                try:
                    import subprocess
                    from provisioning import pi_usbboot
                    out = subprocess.run(["lsusb"], capture_output=True,
                                         text=True, timeout=8).stdout
                    state = pi_usbboot.classify(out).state
                except Exception:
                    state = None
                # A card imaged WITH WiFi comes back on the network, not on
                # USB — the operator can power it from anything. Watching only
                # the cable would sit there saying "waiting" while the Pi was
                # up and pingable (caught on the bench, 2026-08-02).
                if self._boot_host:
                    try:
                        from provisioning import pi_discover
                        ip = pi_discover.resolve(self._boot_host) or ""
                    except Exception:
                        ip = ""
                Clock.schedule_once(lambda _d: self._boot_tick(state, ip), 0)
            threading.Thread(target=work, daemon=True).start()

        self._boot_ev = Clock.schedule_interval(tick, 2.0)
        tick(0)

    def _stop_boot_poll(self):
        ev = getattr(self, "_boot_ev", None)
        if ev is not None:
            try:
                ev.cancel()
            except Exception:
                pass
            self._boot_ev = None

    def _boot_tick(self, state, ip=""):
        """One poll result, on the UI thread. Either route counts as alive."""
        import time
        from provisioning import pi_usbboot
        lbl = getattr(self, "_boot_lbl", None)
        if state == pi_usbboot.GADGET or ip:
            self._stop_boot_poll()
            if lbl is not None:
                lbl.text = (f"The Pi is up on your network at {ip}." if ip
                            else "The Pi is up and talking over the cable.")
                lbl.color = theme.hex_to_rgba(theme.COLORS["green"])
            Clock.schedule_once(lambda _d: self._back_to_birth(), 1.4)
            return
        waited = time.monotonic() - getattr(self, "_boot_t0", 0)
        if lbl is not None:
            if state == pi_usbboot.CARD_READER:
                lbl.text = "Still showing its card…"      # instruction is above
            elif state == pi_usbboot.ABSENT:
                lbl.text = "Unplugged — plug it back in…"
            else:
                lbl.text = f"Waiting for the Pi… ({int(waited)}s)"
        # Overdue: offer a way on rather than trapping anyone behind a Pi that
        # is not going to appear (bad cable, PWR-only port, a card that failed).
        if waited > 180 and not getattr(self, "_boot_escape", None):
            # NOT "carry on without waiting" — that reads as though the step is
            # optional and quietly done, when in fact the Pi has NOT come up and
            # nothing downstream will work until it does. It is safe (it only
            # stops the poll and navigates; nothing is written), but the label
            # has to say what it really is: giving up on this attempt and going
            # back to sort the Pi out (operator, 2026-08-06: "seems like asking
            # for trouble... this needs to be super user friendly").
            self._boot_escape = Button(
                text="Stop waiting — go back and check the Pi", size_hint_y=None,
                height=dp(52), bold=True, background_normal="",
                background_color=theme.hex_to_rgba(theme.COLORS["surface"]),
                color=theme.hex_to_rgba(theme.COLORS["text_primary"]))
            self._boot_escape.bind(on_release=lambda *_: (self._stop_boot_poll(),
                                                          self._back_to_birth()))
            self.col.add_widget(_line(
                "Taking longer than expected. Check it's on a DATA port, not "
                "PWR IN.", size="13.5sp", color="amber", h=40))
            self.col.add_widget(self._boot_escape)

    def _swap_callout_to_action(self, plan):
        """Turn the "don't touch anything" box into the DO-THIS-NOW box.

        In place, at the same spot on the screen, because that is where the
        operator has been looking. The instruction underneath is easy to miss
        after four minutes of watching a progress ring — and worse than missed,
        it was being contradicted by the yellow box still shouting "don't unplug
        anything from Node Medic" (operator, 2026-08-06).
        """
        old = getattr(self, "_callout", None)
        if old is None or old.parent is None:
            return
        steps = [s for s in (plan.get("steps") or []) if s.strip()]
        body = " ".join(steps) or plan.get("note", "")
        try:
            idx = self.col.children.index(old)          # children are reversed
            self.col.remove_widget(old)
            self._callout = Callout(plan.get("title") or "Do this next", body,
                                    act=True)
            self.col.add_widget(self._callout, index=idx)
        except Exception:                                         # noqa: BLE001
            pass            # a missing swap must never cost the operator the
                            # next-steps text, which is added separately below

    def _back_to_birth(self):
        from kivy.app import App
        app = App.get_running_app()
        try:
            scr = getattr(app, "birth_screen", None)
            if scr is not None and hasattr(scr, "arrived_from_imaging"):
                v = {k: t.text.strip() for k, t in (self._inputs or {}).items()
                     if hasattr(t, "text")}
                scr.arrived_from_imaging(v.get("hostname", ""))
            if scr is not None and hasattr(scr, "rescan_after_imaging"):
                scr.rescan_after_imaging()
        except Exception:
            pass
        app.switch_mode("birth")
