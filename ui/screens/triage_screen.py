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
from monitor.triage import TriageSession, thermal_color


def _hex(name: str) -> str:
    return theme.COLORS[name].lstrip("#")


class TriageScreen(FloatLayout):
    def __init__(self, feed_factory: Callable[[], Callable[[], Optional[dict]]],
                 poll_interval: float = 0.5, clock: Callable[[], float] = time.monotonic,
                 lighthouse=None, on_build=None, **kwargs):
        super().__init__(**kwargs)
        self._reader = feed_factory()
        self._lighthouse = lighthouse     # (active: bool) -> status dict
        self._on_build = on_build
        self._beacon_on = False
        self._beacon_answered = False
        self._beacon_names = ""
        self._watchdog = None
        self._session = TriageSession()
        self._clock = clock

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
            text="Move the antenna slowly to begin", bold=True,
            halign="center", valign="middle",
            size_hint=(0.92, None), height=dp(40),
            pos_hint={"center_x": 0.5, "center_y": 0.145},
            color=theme.hex_to_rgba(theme.COLORS["text_primary"]))
        self._guidance.bind(size=lambda *a: setattr(self._guidance, "text_size",
                                                    self._guidance.size))
        self.add_widget(self._guidance)

        # Save the best-scoring antenna position found this session (the reading
        # you'd write down / attach to the node when you bolt it down).
        self._button = Button(
            text="Save best spot", font_size="15sp",
            size_hint=(0.6, None), height=dp(56),
            pos_hint={"center_x": 0.5, "y": 0.03},
            background_normal="", background_down="",
            background_color=theme.hex_to_rgba(theme.COLORS["surface"]),
            color=theme.hex_to_rgba(theme.COLORS["text_primary"]))
        self._button.bind(on_release=self._save)
        self.add_widget(self._button)

        self._modal = None       # "connect an RTNode" prompt, shown on demand
        self.bind(size=self._relayout, pos=self._relayout)
        self._event = Clock.schedule_interval(self._tick, poll_interval)

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
                         size_hint=(0.86, None), height=dp(280),
                         pos_hint={"center_x": 0.5, "center_y": 0.5})
        with card.canvas.before:
            Color(*theme.hex_to_rgba(theme.COLORS["surface"]))
            self._card_bg = Rectangle(pos=card.pos, size=card.size)
        card.bind(size=lambda *a: setattr(self._card_bg, "size", card.size),
                  pos=lambda *a: setattr(self._card_bg, "pos", card.pos))
        msg = Label(
            text="To aim an antenna, Triage needs a distant beacon.\n\n"
                 "Connect (or build) an RTNode-2400 and leave it powered on at "
                 "a distance - it becomes the signal you tune against.",
            halign="center", valign="middle", font_size="16sp",
            color=theme.hex_to_rgba(theme.COLORS["text_primary"]))
        msg.bind(size=lambda i, v: setattr(i, "text_size", v))
        card.add_widget(msg)
        row = BoxLayout(orientation="horizontal", size_hint_y=None,
                        height=dp(52), spacing=dp(12))
        cancel = Button(text="Cancel", font_size="15sp", background_normal="",
                        background_color=theme.hex_to_rgba(theme.COLORS["background"]),
                        color=theme.hex_to_rgba(theme.COLORS["text_primary"]))
        cancel.bind(on_release=lambda *a: self._on_home and self._on_home())
        cont = Button(text="Continue - build one", font_size="15sp",
                      background_normal="",
                      background_color=theme.hex_to_rgba(theme.COLORS["accent"]),
                      color=theme.hex_to_rgba(theme.COLORS["background"]))
        cont.bind(on_release=lambda *a: self._on_build and self._on_build())
        row.add_widget(cancel)
        row.add_widget(cont)
        card.add_widget(row)
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
        self._hide_connect_modal()
        if self._watchdog is not None:
            self._watchdog.cancel()
            self._watchdog = None
        if self._lighthouse is None:
            return
        result = self._lighthouse(True) or {}
        state = result.get("state")
        self._beacon_names = result.get("names", "")
        self._guidance.markup = False
        self._guidance.text = result.get("text", "")
        if state == "active":
            self._beacon_on = True
            # if the commanded node stays silent, it's probably powered off
            self._watchdog = Clock.schedule_once(self._beacon_silent_check, 25)
        elif state == "need_build":
            # no beacon node at all — a centred prompt to connect/build one
            self._show_connect_modal()

    def _beacon_silent_check(self, dt) -> None:
        if self._beacon_on and not self._beacon_answered:
            who = self._beacon_names or "your beacon node"
            self._guidance.markup = False
            self._guidance.text = (f"{who} isn't answering - is it powered on "
                                   "and within range?")

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
                lbl.text = _label
                lbl.center = (x, y)

    def _tick(self, dt) -> None:
        try:
            sample = self._reader()
        except Exception:
            sample = None
        if not sample:
            return
        if sample.get("partial"):
            # live noise, but nothing heard yet — scoring needs a transmission
            self._noise.text = ("[color=9e9e9e]Background noise[/color]\n"
                                f"[color=f0f0f0][b]{sample['noise']:.0f} dBm[/b][/color]")
            self._guidance.markup = False
            self._guidance.text = ("Listening... noise floor is live. To begin "
                                   "scoring, another node must transmit - send "
                                   "an announce from your phone or a node.")
            return
        self._beacon_answered = True      # a real packet arrived (beacon works)
        snap = self._session.feed(sample["snr"], sample["rssi"], sample["noise"],
                                  self._clock())
        self._bullseye.update(snap)
        self._refresh(sample, snap)

    def _refresh(self, sample: dict, snap: dict) -> None:
        sec, pri = _hex("text_secondary"), _hex("text_primary")

        def cell(title, value):
            return (f"[color={sec}]{title}[/color]\n"
                    f"[color={pri}][b]{value}[/b][/color]")

        # Plain-English first, technical term in brackets (guided mode).
        margin = sample["rssi"] - sample["noise"]
        self._rssi.text = cell("Signal strength (RSSI)", f"{sample['rssi']:.0f} dBm")
        self._snr.text = cell("Clarity (SNR)", f"{sample['snr']:+.1f} dB")
        self._noise.text = cell("Background noise", f"{sample['noise']:.0f} dBm")
        self._margin.text = cell("Headroom (margin)", f"{margin:.0f} dB spare")
        self._peers.text = cell("Peers", f"{sample.get('peers', 0)} heard")

        if sample["rssi"] >= -35:
            self._guidance.markup = False
            self._guidance.text = ("Signal is TOO CLOSE to aim against "
                                   f"({sample['rssi']:.0f} dBm). Move the "
                                   "beacon/lighthouse further away - readings "
                                   "this hot look perfect in every direction.")
            return
        r, g, b = thermal_color(snap["score"])
        col = "%02x%02x%02x" % (int(r * 255), int(g * 255), int(b * 255))
        self._guidance.text = f"[color={col}]{snap['guidance']}[/color]"
        self._guidance.markup = True

        # The button always saves the BEST reading seen this session; its label
        # nudges you to lock in once you're in the hot zone.
        if snap["locked"] or snap["ring"] == "bullseye":
            self._button.text = "Bolt it here - save best spot"
        else:
            self._button.text = "Save best spot"

    def _save(self, *a) -> None:
        best = self._session.best_reading
        if best:
            self._guidance.markup = False
            self._guidance.text = (
                f"Best spot saved: clarity {best['snr']:+.1f} dB, "
                f"score {best['score']:.2f}. Mount the node here.")
        else:
            self._guidance.markup = False
            self._guidance.text = ("Nothing to save yet - wait for the beacon "
                                   "so a reading can be scored.")

    def stop(self) -> None:
        event = getattr(self, "_event", None)
        if event is not None:
            event.cancel()
