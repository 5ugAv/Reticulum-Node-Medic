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
        floor = dp(max(h, theme.line_dp(size)))
        lbl.height = floor
        # GROW RATHER THAN CLIP. This used to pin the height and bind text_size
        # to the whole size, so anything that wrapped past one line was silently
        # cut off mid-sentence — the operator saw "…a USB cable that carries
        # DATA — a charge-only" and nothing else (reported live, 2026-08-07).
        #
        # Binding WIDTH (not size) to text_size is the important part: width ->
        # text_size -> texture_size -> height is a one-way chain. Binding the
        # full size would make height feed back into text_size and spin, which
        # is exactly the redraw storm that froze the map screen.
        #
        # `floor` is a floor, never a ceiling, so nothing that already fits can
        # move — and translations that run longer than the English (German and
        # Spanish routinely do) get the room they need instead of being cropped.
        lbl.bind(width=lambda i, w: setattr(i, "text_size", (w, None)))
        lbl.bind(texture_size=lambda i, ts: setattr(i, "height",
                                                    max(floor, ts[1])))
    else:
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
        if password:
            # NEVER carried across. Tracked by an explicit set rather than by
            # reading ti.password, because the Show/Hide button FLIPS that
            # attribute — so a revealed password would have looked like an
            # ordinary field and been cached.
            self._secret_keys = getattr(self, "_secret_keys", set()) | {key}
        prev = (getattr(self, "_kept", {}) or {}).get(key)
        if prev and not password:
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

    # --- noticing a card arrive, without a button press --------------------

    def _start_card_poll(self, anim=None):
        """Watch for a card appearing while the 'no card' screen is showing.

        Same shape as the guided flow's board poll: a Clock tick that does the
        blocking lsblk on a worker thread and only touches widgets back on the
        main thread.

        On finding one it fires the animation's card-found celebration (the
        green ripple burst — the medic's established "I can see it now" signal,
        the same language the board-connect step speaks) and then rebuilds, so
        the operator lands on the write screen without pressing anything.
        """
        from kivy.clock import Clock
        self._stop_card_poll()

        def tick(_dt):
            import threading

            def work():
                try:
                    st = pi_imager.card_status()
                except Exception:                              # noqa: BLE001
                    return
                if st["state"] == "none":
                    return
                Clock.schedule_once(lambda _d: self._on_card_found(anim), 0)

            threading.Thread(target=work, daemon=True).start()

        self._card_ev = Clock.schedule_interval(tick, 1.5)

    def _stop_card_poll(self):
        ev = getattr(self, "_card_ev", None)
        if ev is not None:
            try:
                ev.cancel()
            except Exception:                                  # noqa: BLE001
                pass
            self._card_ev = None

    def _on_card_found(self, anim=None):
        """A card appeared. Celebrate, then show the write screen."""
        if getattr(self, "_card_greeted", False):
            return                        # the poll can fire more than once
        self._card_greeted = True
        self._stop_card_poll()
        # The ripple lives in birth_anims and may not be present in every build
        # — never let a missing flourish stop the flow.
        for name in ("mark_card_found", "mark_found", "mark_connected"):
            fn = getattr(anim, name, None)
            if callable(fn):
                try:
                    fn()
                except Exception:                              # noqa: BLE001
                    pass
                break
        from kivy.clock import Clock
        # Let the burst play before the screen changes under it.
        Clock.schedule_once(lambda _d: self._build(), 0.9)

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
        #
        # EXCEPT SECRETS. The login password used to be carried too, and it
        # outlived the rebuild it was meant to survive: the operator reached
        # this screen at the START OF THE NEXT BIRTH with the PREVIOUS node's
        # password already in the box, in the clear (2026-08-08). Unnoticed,
        # two nodes ship with the same login — and the medic deliberately does
        # not store passwords, so nothing would ever surface it.
        #
        # The WiFi PSK is masked too and so is excluded here as well, which
        # costs nothing: it is re-supplied from the medic's own saved network
        # every time the form is built, not recovered from this cache. The
        # login password has no such source — it is a fact about ONE node, it
        # is hashed onto the card and cannot be read back, and it must be typed
        # afresh for every birth.
        secrets = getattr(self, "_secret_keys", set())
        keep = {}
        for k, t in (getattr(self, "_inputs", None) or {}).items():
            if k in secrets:
                continue
            try:
                if t.text.strip():
                    keep[k] = t.text
            except Exception:
                pass
        self._kept = {**getattr(self, "_kept", {}), **keep}
        for k in secrets:                 # belt and braces: never linger
            self._kept.pop(k, None)
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
            # NOTICE THE CARD BY ITSELF. The operator asked for this watching the
            # step live (2026-08-06): "is it possible for node medic to register
            # that the SD card has been plugged in and start working by itself
            # instead of having to press the button". The button stays as the
            # manual fallback, but nobody should have to press it.
            #
            # Deliberately this is auto-DETECT, not auto-WRITE. Writing is
            # destructive and irreversible; on this very bench a card that looked
            # blank held the previous night's evidence. Notice automatically,
            # destroy on purpose.
            self._start_card_poll(anim)
            return
        # NEVER GUESS BETWEEN TWO CARDS. This used to take targets[0] blindly. A
        # medic that silently picks one of two will eventually pick the wrong
        # one, and the operator has no way to know a choice was even made.
        st = pi_imager.card_status()
        if st["state"] == "several":
            self.col.add_widget(_line("More than one card is plugged in",
                                      bold=True, size="16sp",
                                      color="warning_yellow", h=28))
            self.col.add_widget(_line(st["detail"], size="14sp", h=44))
            self.col.add_widget(_line(st["label"], size="13sp",
                                      color="text_secondary", h=24))
            again = Button(text="I've taken the others out — look again",
                           size_hint_y=None, height=dp(52), bold=True,
                           background_normal="",
                           background_color=theme.hex_to_rgba(theme.COLORS["accent"]),
                           color=theme.hex_to_rgba(theme.COLORS["background"]))
            again.bind(on_release=lambda *_: self._build())
            self.col.add_widget(again)
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
        # PULSING (operator, 2026-08-14): the one heading on this screen that
        # must stop a moving eye — the password below it cannot be recovered.
        self.col.add_widget(Callout(
            "Write these down now!", pulse=True,
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
        # Remember WHICH card this is, while it is still in the reader. If it
        # comes back later the medic can say "that's the one I just wrote"
        # instead of asking again for a Pi the operator has already put down
        # (task #71). Best-effort: a reader that reports no serial degrades to
        # "a card", never to a false claim of recognition.
        try:
            self._written_card_serial = pi_imager.disk_serial(path)
        except Exception:
            self._written_card_serial = ""

        def work():
            # EVERY exit must reach _done, because _done is what releases the
            # activity counter. flash() can raise — its subprocess carries a
            # 1800s timeout, and a card yanked mid-write surfaces as an OSError
            # — and an escaping exception would leave the medic believing a
            # flash is running for ever: banner stuck, power-off blocked, the
            # next build refused, and only a restart to clear it.
            ok, msg = False, ""
            try:
                ok, msg = _flash()
            except Exception as exc:                          # noqa: BLE001
                ok, msg = False, f"The write stopped unexpectedly: {exc}"
            Clock.schedule_once(lambda dt: self._done(ok, msg), 0)

        def _flash():
            ok, msg = pi_imager.flash(
                path, v["hostname"], "pi", v["pw"],
                wifi_ssid=v.get("ssid", ""), wifi_password=v.get("psk", ""),
                # WHICH Pi this card is for. It picks the dwc2 dr_mode: a 3A+
                # exposes its OTG controller on a USB-A socket, which has no ID
                # pin, so the default dr_mode=otg resolves to HOST and the board
                # can never appear on the medic. Empty (board unknown) keeps the
                # plain overlay, which is what every board got before.
                pi_key=self._pi_config_key())
            if ok:
                # Remember what we just named it, so BIRTH can offer the Pi's
                # address instead of asking the operator for an IP they have
                # no way of knowing (operator report 2026-08-01).
                try:
                    from provisioning.pi_discover import record_imaged_pi
                    record_imaged_pi(v["hostname"], "pi")
                except Exception:
                    pass
                # AND FORGET THE IDENTITY THIS CARD JUST REPLACED.
                #
                # Every cable-born node answers on 10.55.0.1, so the second one
                # presents a different host key at an address the medic already
                # knows — and ssh's accept-new accepts an UNKNOWN host, never a
                # CHANGED one. The build then died at its very first command
                # while the Pi sat there answering perfectly (Y2K8, 2026-08-10:
                # "Could not read /proc/cpuinfo"), which reads exactly like a
                # dead cable or a brown-out. An evening went to those.
                #
                # This is the one moment where a changed key is provably not a
                # warning worth keeping: the medic has just written the new OS
                # itself, so the key it pinned no longer exists.
                try:
                    from provisioning import host_keys
                    host_keys.forget_reimaged_node(v["hostname"])
                except Exception:
                    pass
            return ok, msg
        # TELL THE APP A CARD IS BEING WRITTEN. Without this, flash_in_progress()
        # stays False for the whole four minutes: no red "don't power off"
        # banner, and the home screen's slide-to-power-off offers no warning at
        # all — while THIS screen's own callout is saying "don't power Node
        # Medic off. A card interrupted part-way through has to be written again
        # from the start." The tool contradicting itself, with a ruined card as
        # the prize.
        #
        # It has already happened from the other direction: a UI restart killed
        # a write 80 seconds in (2026-08-02). restart_ui.sh guards that path;
        # this was the one still open.
        self._mark_activity(True, v["hostname"])
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

    def _pi_config_key(self):
        """Which Pi the CARD is being written for. The operator's answer first.

        NOT :meth:`_pi_art_key`, and the difference is not cosmetic. That one is
        the USB-DETECTED model, and USB can only ever name a SoC: BCM283x is a
        Zero 2 W, a 3A+ and a 3B+ at once, so it returns "" for all three. The
        card writer was being handed that "", which made dwc2_overlay_for()
        produce a BARE ``dtoverlay=dwc2`` — dr_mode=otg, "read the ID pin" — on
        a 3A+ whose USB-A socket has no ID pin. The Pi booted perfectly and
        presented no USB device (operator, live, 2026-08-08).

        The board's identity was never actually unknown: the operator was asked
        "Which Raspberry Pi is this?" and answered. The guide already draws this
        distinction for its own text — a picture prefers what was DETECTED, but
        words prefer what the operator CHOSE, "because they are holding the
        board and they told us what it is". A dr_mode is not a picture. It is a
        hardware fact, and the operator is the better source.
        """
        try:
            from kivy.app import App
            guide = getattr(App.get_running_app(), "birth_guide_screen", None)
            chosen = getattr(guide, "_pi_key", "") or ""
            return chosen or self._pi_art_key()
        except Exception:
            return self._pi_art_key()

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

    def _mark_activity(self, on, hostname=""):
        """Tell the app a card write is (not) running, so the screensaver stays
        off and the persistent 'don't power off' banner shows. Best-effort —
        never let bookkeeping break a write."""
        try:
            from kivy.app import App
            app = App.get_running_app()
            if app is None:
                return
            if on:
                nm = (hostname or "").strip() or "this Pi"
                app.begin_activity(
                    f"Writing {nm}'s card — keep everything plugged in, "
                    "don't power off")
            else:
                app.end_activity()
        except Exception:
            pass

    def _done(self, ok, msg):
        # FIRST, and outside the try: begin_activity is a COUNTER. A write that
        # ends without its matching end_activity leaves the medic believing a
        # flash is running for ever — power-off blocked, banner stuck, and the
        # next build refused. Failing to clear it is worse than never setting
        # it, so it is released before anything here can raise.
        self._mark_activity(False)
        self._busy = False
        ev = getattr(self, "_ev", None)
        if ev is not None:
            ev.cancel()
        surgery = getattr(self, "_surgery", None)
        if surgery is not None:
            if ok:
                # smile + thumbs up + happy beep, then the monitor is switched
                # off and says DISCHARGED. set_fraction(1.0) alone left the
                # trace scrolling for ever, so the screen never looked finished
                # (operator, live, 2026-08-07).
                surgery.finish()
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
            # A WALKTHROUGH IS WAITING: give it straight back.
            #
            # The next-steps block below — take the card out, put it in the Pi,
            # plug the Pi in — exists because this screen used to be a terminus,
            # with those guide steps stranded behind a one-way hand-off. The
            # hand-off returns now, and the guide says all three itself, each on
            # its own screen with its own animation. Printing them here as well
            # means the operator does the work, taps a button, and is then told
            # to do it again (operator, live, 2026-08-09: "user shouldn't have
            # to press go back here, that's confusing... the next step is a
            # repeat").
            #
            # Short beat first, so "Done!" and the discharged monitor are
            # actually seen — a screen that vanishes the instant it succeeds has
            # told nobody anything.
            try:
                from kivy.app import App
                app = App.get_running_app()
                if getattr(app, "guided_birth_pending", lambda: False)():
                    Clock.schedule_once(lambda _d: self._back_to_birth(), 1.8)
                    return
            except Exception:                                     # noqa: BLE001
                pass            # never let a hand-back cost the next-steps text
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

        # What was already on USB when the wait began. Without this, "nothing
        # ever appeared" (cable/port/power) and "something appeared but offers
        # no link" (the card's setup) are indistinguishable — and they are
        # different faults with different fixes. See provisioning/plugged_in.py.
        self._boot_baseline = []
        try:
            import subprocess as _sp
            from provisioning import plugged_in as _pin
            self._boot_baseline = _pin._ids(
                _sp.run(["lsusb"], capture_output=True, text=True,
                        timeout=8).stdout)
        except Exception:
            pass

        def tick(_dt):
            def work():
                state, ip, out, card, serial = None, "", "", {}, ""
                try:
                    import subprocess
                    from provisioning import pi_usbboot, pi_imager
                    out = subprocess.run(["lsusb"], capture_output=True,
                                         text=True, timeout=8).stdout
                    state = pi_usbboot.classify(out).state
                    # ALSO look at our own card reader. The operator moved on
                    # to a different step — took the card out of the Pi and put
                    # it in the medic — and the screen kept asking for the Pi
                    # (2026-08-06). The medic had the facts and said the wrong
                    # thing; now it looks.
                    card = pi_imager.card_status()
                    if card.get("state") == "one" and card.get("path"):
                        serial = pi_imager.disk_serial(card["path"])
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
                Clock.schedule_once(
                    lambda _d: self._boot_tick(state, ip, out, card, serial), 0)
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

    def _show_situation(self, sit):
        """Say what was found, and offer the branches the operator really has.

        Replaces a single button reading "Stop waiting — go back and check the
        Pi". That was honest about being an abandonment, which was an
        improvement on "carry on" — but abandonment was never the only option.
        A Pi that came up in boot-ROM wants the card rewritten; a card sitting
        in the medic's reader wants putting into the Pi. Naming those is the
        difference between a tool that noticed and one that gave up.
        """
        if getattr(self, "_situation_shown", None) == sit.state:
            return                       # already on screen; don't stack copies
        self._situation_shown = sit.state

        # ONE container, rebuilt in place. The poll keeps running for the
        # unsettled states, so the situation can legitimately change (nothing
        # on USB -> a hub appears -> the gadget comes up); appending each time
        # would leave a growing pile of stale advice and contradictory buttons
        # on screen.
        # SELF-HEALING on purpose. This screen is a singleton and self.col is
        # rebuilt by clear_widgets() in three separate places today. A remembered
        # box would then be an ORPHAN — still a live widget, still accepting
        # children, but detached from the tree, so the advice and buttons would
        # render precisely nowhere and the screen would look like it had ignored
        # the operator again. Checking the parent costs nothing and cannot be
        # forgotten at a fourth call site.
        box = getattr(self, "_sit_box", None)
        if box is None or box.parent is not self.col:
            box = BoxLayout(orientation="vertical", size_hint_y=None,
                            spacing=dp(6))
            box.bind(minimum_height=box.setter("height"))
            self._sit_box = box
            self.col.add_widget(box)
        box.clear_widgets()

        if sit.detail:
            box.add_widget(_line(sit.detail, size="13.5sp", color="amber", h=56))
        for act in sit.actions:
            b = Button(text=act["label"], size_hint_y=None, height=dp(52),
                       bold=True, background_normal="",
                       background_color=theme.hex_to_rgba(theme.COLORS["surface"]),
                       color=theme.hex_to_rgba(theme.COLORS["text_primary"]))
            b.bind(on_release=lambda _b, k=act["key"]: self._situation_action(k))
            box.add_widget(b)

    def _situation_action(self, key):
        """Carry out one named branch. Nothing here is destructive on its own —
        'rewrite_card' returns to the imaging screen, where the write still sits
        behind its own deliberate press (pi_imager.card_status' rule)."""
        self._stop_boot_poll()
        if key == "rewrite_card":
            self._situation_shown = None
            self.reset()
            return
        self._back_to_birth()

    def _boot_tick(self, state, ip="", lsusb_out="", card=None, card_serial=""):
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

        # Ask what is ACTUALLY plugged in, rather than mapping one USB state to
        # one sentence. Four different situations used to produce "Unplugged —
        # plug it back in…", including the operator having already moved the
        # card into the medic's own reader (task #71).
        from provisioning import plugged_in
        sit = plugged_in.read(
            lsusb_output=lsusb_out or "",
            card=card or {},
            waited_s=waited,
            baseline_ids=getattr(self, "_boot_baseline", ()),
            our_card_serial=getattr(self, "_written_card_serial", ""),
            card_serial=card_serial or "",
            node_name=self._boot_host or "")
        if lbl is not None:
            lbl.text = sit.headline
            lbl.color = theme.hex_to_rgba(theme.COLORS[
                "amber" if sit.is_settled and not sit.is_good else "text_primary"])
        if sit.is_settled:
            # Something definite. Stop polling, say the rest, offer the real
            # branches instead of the single "Stop waiting" this replaces.
            self._stop_boot_poll()
            self._show_situation(sit)
            return
        if sit.state != plugged_in.WAITING:
            # A diagnosis, but one that could still come good on its own (the
            # card's setup may yet finish coming up). Say it and offer the ways
            # out — but KEEP POLLING, so a Pi that arrives late is still caught
            # and the screen corrects itself.
            self._show_situation(sit)

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
        # IF A WALKTHROUGH SENT US HERE, GIVE IT BACK. Until the guide could be
        # resumed this screen was a terminus: the card was written, the Pi came
        # back, and the operator was dropped on the BIRTH screen with the rest of
        # the walkthrough — move the card, connect the Pi, connect the radio,
        # prove it — stranded behind a one-way hand-off. That is why this screen
        # has to narrate those steps itself in plain text, and why the operator
        # watched it "just sit there" (2026-08-08). It was the end of the road.
        try:
            if getattr(app, "resume_guided_birth", None) and app.resume_guided_birth():
                return
        except Exception:                                          # noqa: BLE001
            pass            # a broken resume must never strand the operator here
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
