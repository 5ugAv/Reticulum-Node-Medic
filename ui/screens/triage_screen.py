"""Triage screen — the antenna-aiming thermal bullseye.

Bullseye centre-stage with four corner readouts (RSSI / SNR / Noise / Peers), a
colour-coded guidance line, and one big touch button whose label tracks state.
Laid out with relative positioning so it works in portrait or landscape. The
signal feed is injected — emulated for the demo, the live splitter feed later.
"""

from __future__ import annotations

import time
from typing import Callable, Optional

from kivy.uix.floatlayout import FloatLayout
from kivy.uix.label import Label
from kivy.uix.button import Button
from kivy.clock import Clock
from kivy.metrics import dp

from ui.widgets.bullseye import BullseyeWidget
from ui import theme
from ui.i18n import tr  # i18n: wrapped — Triage guidance/readout labels/buttons
from monitor.triage import TriageSession, thermal_color


def _hex(name: str) -> str:
    return theme.COLORS[name].lstrip("#")


class TriageScreen(FloatLayout):
    #: seconds of radio silence before the bullseye is covered with "Not Reading"
    _NOT_READING_AFTER = 2.0

    def __init__(self, feed_factory: Callable[[], Callable[[], Optional[dict]]],
                 poll_interval: float = 0.5, clock: Callable[[], float] = time.monotonic,
                 lighthouse=None, on_build=None, on_home=None,
                 on_antenna_test=None, on_boundary_walk=None, **kwargs):
        super().__init__(**kwargs)
        self._reader = feed_factory()
        self._lighthouse = lighthouse     # (active: bool) -> status dict
        self._on_antenna_test = on_antenna_test   # the modal offers it too
        self._on_build = on_build
        # Cancel's way home. Referenced at the empty-triage prompt since
        # birth but NEVER SET — every fresh medic crashed the whole UI on
        # its first triage Cancel (found on HAWKEYE, first day alive).
        self._on_home = on_home
        self._beacon_on = False
        self._beacon_answered = False
        self._beacon_names = ""
        self._watchdog = None
        self._session = TriageSession()
        self._clock = clock
        self._not_reading = False
        self._last_read = clock()

        self._bullseye = BullseyeWidget(size_hint=(None, None))
        self.add_widget(self._bullseye)

        self._rssi = self._readout({"x": 0.03, "top": 0.97})
        self._snr = self._readout({"right": 0.97, "top": 0.97})
        self._noise = self._readout({"x": 0.03, "y": 0.21})
        self._margin = self._readout({"right": 0.97, "y": 0.21})
        self._peers = self._readout({"center_x": 0.5, "top": 0.97})

        # spoke labels — placed each relayout from the bullseye's geometry
        self._spoke_labels = {}
        for key in ("snr", "margin", "noise"):
            lbl = Label(text="", font_size="12sp", bold=True,
                        size_hint=(None, None), size=(dp(80), dp(20)),
                        color=theme.hex_to_rgba(theme.COLORS["text_secondary"]))
            self._spoke_labels[key] = lbl
            self.add_widget(lbl)

        self._guidance = Label(
            text=tr("Move the antenna slowly to begin"), bold=True,
            halign="center", valign="middle",
            size_hint=(0.92, None), height=dp(40),
            pos_hint={"center_x": 0.5, "center_y": 0.145},
            color=theme.hex_to_rgba(theme.COLORS["text_primary"]))
        self._guidance.bind(size=lambda *a: setattr(self._guidance, "text_size",
                                                    self._guidance.size))
        self.add_widget(self._guidance)

        # Goal glow: a green frame around the screen that BRIGHTENS as the
        # antenna nears the best spot found and DIMS as it drifts off — so the
        # final aiming is a brightness hill to climb. Plus a "MOUNT HERE" label
        # that fades in on the sweet spot.
        from kivy.graphics import Color, Line
        with self.canvas.after:
            self._glow_color = Color(*theme.hex_to_rgba(theme.COLORS["green"], 0))
            self._glow_line = Line(rectangle=(0, 0, 10, 10), width=dp(9))
        self.bind(pos=self._sync_glow, size=self._sync_glow)
        from kivy.uix.label import Label as _L
        self._goal_flash = _L(
            text="", bold=True, font_size="30sp", opacity=0,
            halign="center", valign="middle",
            pos_hint={"center_x": 0.5, "center_y": 0.75},
            color=theme.hex_to_rgba(theme.COLORS["green"]))
        self._goal_flash.bind(size=lambda i, v: setattr(i, "text_size", v))
        self.add_widget(self._goal_flash)

        # NO "SAVE GPS COORDINATES" BUTTON. It was removed on 2026-09-29 after
        # the operator asked the obvious question — "what is the user saving the
        # GPS coordinates TO?" — and the answer was: nothing. _save() read a
        # fix, wrote the sentence "Location saved: <lat>, <lon>. This node is
        # now on the map." into the guidance line, and returned. No registry
        # write, no node write, no map pin. A screen that says it saved
        # something and saved nothing is the worst failure this project has, and
        # it had been sitting one tap away from a page about placement.
        #
        # A node's position is set where it is actually written: at birth
        # (the location confirm gate) and afterwards on the node's own page
        # under Location, which writes the registry and can push to the node.

        # Antenna test — compare real antennas with the node's own ear
        # (bench campaign 2026-08-27; docs/ANTENNA_BENCH_2026-08-27.md).
        if on_antenna_test is not None:
            ant = Button(
                text=tr("Antenna test"), font_size="13sp",
                size_hint=(None, None), size=(dp(120), dp(40)),
                pos_hint={"x": 0.02, "y": 0.035},
                background_normal="", background_down="",
                background_color=theme.hex_to_rgba(theme.COLORS["surface"]),
                color=theme.hex_to_rgba(theme.COLORS["text_secondary"]))
            ant.bind(on_release=lambda *a: on_antenna_test())
            self.add_widget(ant)

        # Boundary test — the walk that measures a candidate site's real
        # reach. It lives on the node's own VITALS page too, but ANTENNA is
        # where someone THINKING about placement looks for it (operator,
        # 2026-09-19: "under antenna, leave a button for boundary test" —
        # their own instinct, and this screen is titled SIGNAL & PLACEMENT).
        if on_boundary_walk is not None:
            bwk = Button(
                text=tr("Range test"), font_size="13sp",
                size_hint=(None, None), size=(dp(120), dp(40)),
                pos_hint={"right": 0.98, "y": 0.035},
                background_normal="", background_down="",
                background_color=theme.hex_to_rgba(theme.COLORS["surface"]),
                color=theme.hex_to_rgba(theme.COLORS["text_secondary"]))
            bwk.bind(on_release=lambda *a: on_boundary_walk())
            self.add_widget(bwk)

        # "Not Reading" cover — dropped over the bullseye when the radio reports
        # nothing for a spell, so the HELD last score can't be mistaken for a live
        # (static) reading. Cleared the instant any real sample resumes.
        from kivy.graphics import Color as _C, Line as _Ln, RoundedRectangle as _RR
        self._nr_overlay = Label(
            text=tr("NOT READING") + "\n"
                 # amber: this overlay is a WARNING state, and amber is the
                 # warning colour the green repaint kept out of the green on
                 # purpose. It was a pink of its own (e8c9c9).
                 "[size=13sp][color=" + theme.COLORS["amber"].lstrip("#") + "]"
                 + tr("No signal from the antenna") + "[/color][/size]",
            markup=True, halign="center", valign="middle", bold=True,
            font_size="27sp", size_hint=(None, None), opacity=0,
            color=theme.hex_to_rgba(theme.COLORS["text_primary"]))
        with self._nr_overlay.canvas.before:
            self._nr_bg = _C(0, 0, 0, 0.82)
            self._nr_rect = _RR(pos=(0, 0), size=(10, 10), radius=[dp(16)] * 4)
            self._nr_bd = _C(*theme.hex_to_rgba(theme.COLORS["red"]))
            self._nr_line = _Ln(width=dp(2.5),
                                rounded_rectangle=(0, 0, 10, 10, dp(16)))
        self._nr_overlay.bind(size=self._sync_not_reading,
                              pos=self._sync_not_reading)
        self.add_widget(self._nr_overlay)

        self._modal = None       # "connect an RTNode" prompt, shown on demand
        self.bind(size=self._relayout, pos=self._relayout)
        # NOT scheduled here. The tick used to start at CONSTRUCTION — i.e. at
        # app startup, for a screen the operator may never open — and nothing
        # ever cancelled it. It is started by start() from on_enter and stopped
        # by stop() from on_leave, so a screen nobody is looking at costs
        # nothing (2026-09-05; see MetricRange for what that cost grew into).
        self._poll_interval = poll_interval
        self._event = None

    # -- "connect a lighthouse" modal (only when no beacon node exists) ------

    def _show_connect_modal(self) -> None:
        if self._modal is not None:
            return
        from kivy.uix.floatlayout import FloatLayout
        from kivy.graphics import Color, Rectangle
        overlay = FloatLayout(size_hint=(1, 1))
        with overlay.canvas.before:
            Color(0, 0, 0, 0.72)
            self._modal_bg = Rectangle(pos=self.pos, size=self.size)
        overlay.bind(size=lambda *a: setattr(self._modal_bg, "size", overlay.size),
                     pos=lambda *a: setattr(self._modal_bg, "pos", overlay.pos))
        from kivy.uix.boxlayout import BoxLayout
        card = BoxLayout(orientation="vertical", spacing=dp(14), padding=dp(20),
                         size_hint=(0.86, None),
                         height=dp(340) if self._on_antenna_test else dp(280),
                         pos_hint={"center_x": 0.5, "center_y": 0.5})
        with card.canvas.before:
            Color(*theme.hex_to_rgba(theme.COLORS["surface"]))
            self._card_bg = Rectangle(pos=card.pos, size=card.size)
        card.bind(size=lambda *a: setattr(self._card_bg, "size", card.size),
                  pos=lambda *a: setattr(self._card_bg, "pos", card.pos))
        msg = Label(
            text=tr("To aim an antenna, the medic needs a distant beacon.\n\n"
                    "Connect (or build) an RTNode-2400 and leave it powered on at "
                    "a distance - it becomes the signal you tune against."),
            halign="center", valign="middle", font_size="16sp",
            color=theme.hex_to_rgba(theme.COLORS["text_primary"]))
        msg.bind(size=lambda i, v: setattr(i, "text_size", v))
        card.add_widget(msg)
        row = BoxLayout(orientation="horizontal", size_hint_y=None,
                        height=dp(52), spacing=dp(12))
        cancel = Button(text=tr("Cancel"), font_size="15sp", background_normal="",
                        background_color=theme.hex_to_rgba(theme.COLORS["background"]),
                        color=theme.hex_to_rgba(theme.COLORS["text_primary"]))
        cancel.bind(on_release=lambda *a: self._on_home and self._on_home())
        cont = Button(text=tr("Continue - build one"), font_size="15sp",
                      background_normal="",
                      background_color=theme.hex_to_rgba(theme.COLORS["accent"]),
                      color=theme.hex_to_rgba(theme.COLORS["background"]))
        cont.bind(on_release=lambda *a: self._on_build and self._on_build())
        row.add_widget(cancel)
        row.add_widget(cont)
        card.add_widget(row)
        if self._on_antenna_test:
            # the one thing on this screen that needs NO beacon — it was
            # hidden under the modal's dark wash (readiness sweep, 2026-10-03)
            alt = Button(text=tr("Antenna test instead (no beacon needed)"),
                         font_size="15sp", size_hint_y=None, height=dp(48),
                         background_normal="",
                         background_color=theme.hex_to_rgba(theme.COLORS["surface"]),
                         color=theme.hex_to_rgba(theme.COLORS["text_primary"]))
            alt.bind(on_release=lambda *a: self._on_antenna_test())
            card.add_widget(alt)
        overlay.add_widget(card)
        self.add_widget(overlay)
        self._modal = overlay

    def _hide_connect_modal(self) -> None:
        if self._modal is not None:
            self.remove_widget(self._modal)
            self._modal = None

    # -- beacon (Triage lighthouse — auto on enter, off on leave) -----------

    def enter_triage(self, *a) -> None:
        """Called when the Triage screen opens: auto-activate the beacon and
        show the right prompt (aim / power-on / build)."""
        self._beacon_answered = False
        # Publish this survey as the active triage session so a subsequent BIRTH
        # consumes + auto-clears it (no stale session pins the next build).
        try:
            from monitor import triage
            triage.set_active_session(self._session)
        except Exception:
            pass
        self._hide_connect_modal()
        if self._watchdog is not None:
            self._watchdog.cancel()
            self._watchdog = None
        if self._lighthouse is None:
            return
        # _lighthouse(True) can run a mesh DISCOVERY (an rnpath subprocess,
        # seconds) — on the Kivy main thread that froze the whole UI on TRIAGE
        # entry (2026-08-01 bug hunt). Do it off-thread and apply the result
        # back on the main thread.
        self._guidance.markup = False
        self._guidance.text = tr("Looking for a beacon…")
        import threading

        def work():
            try:
                res = self._lighthouse(True) or {}
            except Exception:              # noqa: BLE001
                res = {}
            Clock.schedule_once(lambda _dt: self._apply_lighthouse(res), 0)
        threading.Thread(target=work, daemon=True).start()

    def _apply_lighthouse(self, result):
        state = result.get("state")
        self._beacon_names = result.get("names", "")
        self._guidance.markup = False
        self._guidance.text = result.get("text", "")
        if result.get("text"):
            # HOLD the sentence. The 0.5 s tick wrote "Listening..." over it
            # within half a second, so "Power on your beacon node" — the one
            # thing that unblocks a new user — was never readable (2026-10-03).
            self._pin_guidance(45.0 if state == "need_power" else 8.0)
        if state == "active":
            self._beacon_on = True
            # if the commanded node stays silent, it's probably powered off
            self._watchdog = Clock.schedule_once(self._beacon_silent_check, 25)
        elif state == "need_build":
            # no beacon node at all — a centred prompt to connect/build one
            self._show_connect_modal()

    def _beacon_silent_check(self, dt) -> None:
        if self._beacon_on and not self._beacon_answered:
            who = self._beacon_names or tr("your beacon node")
            self._guidance.markup = False
            self._guidance.text = tr("{who} isn't answering - is it powered on "
                                     "and within range?").format(who=who)
            self._pin_guidance(20.0)

    def stop_lighthouse(self, *a) -> None:
        """Stop the beacon — called automatically whenever Triage is left."""
        self._beacon_on = False
        if self._watchdog is not None:
            self._watchdog.cancel()
            self._watchdog = None
        if self._lighthouse:
            self._lighthouse(False)

    def _readout(self, pos_hint) -> Label:
        # fraction-of-screen width (not fixed dp) so density scaling can't
        # overlap the three top cells on the 720px panel
        lbl = Label(text="", markup=True, halign="left", valign="middle",
                    size_hint=(0.30, None), height=dp(54), pos_hint=pos_hint)
        lbl.bind(size=lambda *a: setattr(lbl, "text_size", lbl.size))
        self.add_widget(lbl)
        return lbl

    def _relayout(self, *a) -> None:
        # bullseye fills the band between the top readouts and the guidance/button
        side = max(dp(120), min(self.width * 0.92, self.height * 0.58))
        self._bullseye.size = (side, side)
        self._bullseye.pos = (self.x + (self.width - side) / 2.0,
                              self.y + self.height * 0.30)
        self._bullseye._redraw()
        for key, _label, x, y in self._bullseye.spoke_label_positions():
            lbl = self._spoke_labels.get(key)
            if lbl is not None:
                lbl.text = tr(_label)
                lbl.center = (x, y)
        # the "Not Reading" cover tracks the bullseye's central reading area
        self._nr_overlay.size = (side * 0.86, side * 0.44)
        self._nr_overlay.center = self._bullseye.center

    def _sync_not_reading(self, *a) -> None:
        o = self._nr_overlay
        o.text_size = o.size
        self._nr_rect.pos = o.pos
        self._nr_rect.size = o.size
        self._nr_line.rounded_rectangle = (o.x, o.y, o.width, o.height, dp(16))

    def _set_not_reading(self, on: bool) -> None:
        if on == self._not_reading:
            return
        self._not_reading = on
        self._nr_overlay.opacity = 1.0 if on else 0.0
        if on:
            self._sync_not_reading()
            self._write_guidance(tr(
                "Not reading any signal - check the antenna and cable, and that a "
                "beacon node is powered on and transmitting."))

    def _write_guidance(self, text, markup=False) -> None:
        """Set the guidance line UNLESS a message is pinned (e.g. the just-saved
        GPS location, held ~10s so the operator can actually read it before the
        live per-packet guidance overwrites it)."""
        if getattr(self, "_guidance_pinned", False):
            return
        self._guidance.markup = markup
        self._guidance.text = text

    def _pin_guidance(self, seconds: float = 10.0) -> None:
        self._guidance_pinned = True
        ev = getattr(self, "_guidance_pin_event", None)
        if ev is not None:
            ev.cancel()
        self._guidance_pin_event = Clock.schedule_once(
            lambda dt: setattr(self, "_guidance_pinned", False), seconds)

    def _tick(self, dt) -> None:
        try:
            sample = self._reader()
        except Exception:
            sample = None
        if not sample:
            # Nothing from the radio. After a short grace, cover the bullseye so
            # the held last score isn't read as a live (static) measurement.
            if self._clock() - self._last_read > self._NOT_READING_AFTER:
                self._set_not_reading(True)
            return
        self._last_read = self._clock()
        self._set_not_reading(False)      # a real sample (full or partial) resumed
        if sample.get("partial"):
            # live noise, but nothing heard yet — scoring needs a transmission
            # Theme colours, not the literal old grey/white: when the palette
            # went green (2026-09-29) this readout was still wearing 9e9e9e
            # and f0f0f0 by name — two colours that no longer exist anywhere
            # else on the glass.
            sec = theme.COLORS["text_secondary"].lstrip("#")
            pri = theme.COLORS["text_primary"].lstrip("#")
            self._noise.text = (f"[color={sec}]" + tr("Background noise") + "[/color]\n"
                                f"[color={pri}][b]{sample['noise']:.0f} dBm[/b][/color]")
            self._write_guidance(tr(
                "Listening... noise floor is live. To begin scoring, another "
                "node must transmit - send an announce from your phone or a node."))
            return
        self._beacon_answered = True      # a real packet arrived (beacon works)
        snap = self._session.feed(sample["snr"], sample["rssi"], sample["noise"],
                                  self._clock())
        self._bullseye.update(snap)
        self._refresh(sample, snap)
        self._update_goal_glow(snap.get("goal_proximity", 0.0))

    def _sync_glow(self, *a) -> None:
        self._glow_line.rectangle = (self.x + dp(3), self.y + dp(3),
                                     self.width - dp(6), self.height - dp(6))

    def _update_goal_glow(self, proximity: float) -> None:
        # brighter as you approach the goal; the label appears near the top
        self._glow_color.a = proximity          # 0 (off) .. 1 (right on it)
        self._goal_flash.opacity = max(0.0, (proximity - 0.5) * 2.0)
        self._goal_flash.text = tr("MOUNT HERE") if proximity > 0.85 else tr("getting hot...")

    def _refresh(self, sample: dict, snap: dict) -> None:
        sec, pri = _hex("text_secondary"), _hex("text_primary")

        def cell(title, value):
            return (f"[color={sec}]{title}[/color]\n"
                    f"[color={pri}][b]{value}[/b][/color]")

        # Plain-English first, technical term in brackets (guided mode).
        margin = sample["rssi"] - sample["noise"]
        self._rssi.text = cell(tr("Signal strength (RSSI)"), f"{sample['rssi']:.0f} dBm")
        self._snr.text = cell(tr("Clarity (SNR)"), f"{sample['snr']:+.1f} dB")
        self._noise.text = cell(tr("Background noise"), f"{sample['noise']:.0f} dBm")
        self._margin.text = cell(tr("Headroom (margin)"),
                                 tr("{margin} dB spare").format(margin=f"{margin:.0f}"))
        self._peers.text = cell(tr("Peers"),
                                tr("{n} heard").format(n=sample.get('peers', 0)))

        if sample["rssi"] >= -35:
            self._write_guidance(
                tr("Signal is TOO CLOSE to aim against "
                   "({rssi} dBm). Move the beacon/lighthouse further "
                   "away - readings this hot look perfect in every direction.").format(
                       rssi=f"{sample['rssi']:.0f}"))
            return
        r, g, b = thermal_color(snap["score"])
        col = "%02x%02x%02x" % (int(r * 255), int(g * 255), int(b * 255))
        # the scorer speaks English constants; the screen translates them
        self._write_guidance(f"[color={col}]{tr(snap['guidance'])}[/color]", markup=True)

    def start(self) -> None:
        """Begin sampling. Idempotent — re-entering the screen must not leave
        two ticks running."""
        if getattr(self, "_event", None) is not None:
            return
        self._event = Clock.schedule_interval(self._tick, self._poll_interval)

    def stop(self) -> None:
        event = getattr(self, "_event", None)
        if event is not None:
            event.cancel()
            self._event = None
