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
                 beacon_toggle=None, **kwargs):
        super().__init__(**kwargs)
        self._reader = feed_factory()
        self._beacon_toggle = beacon_toggle
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

        self._button = Button(
            text="Save current reading",
            size_hint=(None, None), size=(dp(280), dp(56)),
            pos_hint={"center_x": 0.5, "y": 0.03},
            background_normal="", background_down="",
            background_color=theme.hex_to_rgba(theme.COLORS["surface"]),
            color=theme.hex_to_rgba(theme.COLORS["text_primary"]))
        self._button.bind(on_release=self._save)
        self.add_widget(self._button)

        self.bind(size=self._relayout, pos=self._relayout)
        self._event = Clock.schedule_interval(self._tick, poll_interval)

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

        if snap["locked"]:
            self._button.text = "Locked - secure the antenna"
        elif snap["ring"] == "bullseye":
            self._button.text = "Hold steady..."
        elif snap["score"] > 0.1:
            self._button.text = "Save this position"
        else:
            self._button.text = "Save current reading"

    def _save(self, *a) -> None:
        best = self._session.best_reading
        if best:
            self._guidance.markup = False
            self._guidance.text = (f"Saved best spot - score {best['score']:.2f} "
                                   f"(SNR {best['snr']:+.1f} dB)")

    def stop(self) -> None:
        event = getattr(self, "_event", None)
        if event is not None:
            event.cancel()
