"""TRIAGE ▸ Antenna Test — rank real antennas with the node's own ear.

The 2026-08-27 bench campaign (docs/ANTENNA_BENCH_2026-08-27.md) proved that
antennas cannot be judged by looks, labels, or even a perfect impedance match:
a "perfectly matched" whip was 15 dB deaf (a lossy sponge), identical-looking
twins measured wildly differently, and a folded folder lost 4 dB that no
meter could see. What a keeper CAN do, with nothing but the medic and the
node in hand, is let the node LISTEN through each antenna and compare ears —
the noise floor it samples. Counter-intuitively, the antenna that hears the
MOST noise is the best one: a good ear hears more of everything. This screen
carries that interpretation with the numbers, so nobody reads "quietest" as
"cleanest".

Flow: plug the node in → the medic reads its ear (~half a minute) → name the
antenna → unplug, swap antennas (power off — never swap live, a transmit into
a bare socket can damage the radio), plug back in → repeat → ranked verdict.
The unplug is ENFORCED: the port must actually vanish and return before the
next reading arms, so two readings can never silently share one antenna.

All decisions live in :mod:`monitor.antenna_test` (kivy-free, unit-tested);
this screen renders and threads. Read-only end to end — it never writes,
flashes or resets the board, and the medic's own radios are never offered
(the onboard-roster guard, same as flashing).
"""

from __future__ import annotations

import threading

from kivy.clock import Clock
from kivy.metrics import dp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.scrollview import ScrollView

from monitor import antenna_test as at
from ui import theme
from ui.i18n import tr  # i18n: wrapped — antenna test guidance/verdicts

#: Preset names for the chips — a keeper should never need a keyboard here.
_PRESETS = ("Stock", "Short whip", "Long whip", "Folding", "Other")

_VERDICT_COLOURS = {"best": "22c55e", "good": "8bd48b",
                    "weak": "eab308", "suspect": "ef4444"}
_VERDICT_WORDS = {
    "best": "best ear",
    "good": "good",
    "weak": "weak",
    "suspect": "SUSPECT - wrong band, lossy or broken",
}


def _label(text="", size="15sp", bold=False, color="text_secondary"):
    lbl = Label(text=text, font_size=theme.font_sp(size), bold=bold,
                color=theme.hex_to_rgba(theme.COLORS[color]),
                halign="center", valign="top", markup=True,
                size_hint_y=None)
    lbl.bind(width=lambda w, v: setattr(w, "text_size", (v, None)),
             texture_size=lambda w, s: setattr(w, "height", s[1]))
    return lbl


class AntennaTestScreen(BoxLayout):
    """State machine: detect -> ready(name chips) -> reading -> swap -> ...
    -> results whenever two or more antennas have real readings."""

    def __init__(self, on_home=None, poll=at.poll_ear,
                 find_board=at.find_test_board, **kwargs):
        super().__init__(orientation="vertical", padding=dp(14),
                         spacing=dp(10), **kwargs)
        self._on_home = on_home
        self._poll = poll
        self._find_board = find_board
        self.session = at.AntennaSession()
        self._stage = "idle"
        self._port = None           # the board we read through last
        self._board_gone = False    # the enforced unplug was seen
        self._running = False
        self._watch = None

        self._title = _label(tr("Antenna test"), size="20sp", bold=True,
                             color="text_primary")
        self.add_widget(self._title)

        # the one rule, always on screen — the whole point of the feature
        self._rule = _label(tr("Higher is better: a good antenna hears MORE "
                               "of everything, noise included. The quiet one "
                               "is usually the deaf one."),
                            size="13sp", color="text_secondary")
        self.add_widget(self._rule)

        self._scroll = ScrollView()
        self._body = _label()
        self._scroll.add_widget(self._body)
        self.add_widget(self._scroll)

        self._buttons = BoxLayout(orientation="vertical", size_hint_y=None,
                                  spacing=dp(8))
        self._buttons.bind(minimum_height=lambda w, v: setattr(w, "height", v))
        self.add_widget(self._buttons)

    # ------------------------------------------------------------- lifecycle

    def begin_screen(self) -> None:
        """on_enter: start watching the USB bus. Dormant until shown —
        the hidden-screen poll trap (MITOSIS, 2026-08) must not recur."""
        if self._watch is None:
            self._watch = Clock.schedule_interval(self._tick_detect, 2.0)
        self._stage = "detect"
        self._render()
        self._tick_detect(0)

    def sleep(self) -> None:
        """on_leave: stop watching; a reading already in flight finishes on
        its own thread and lands harmlessly."""
        if self._watch is not None:
            self._watch.cancel()
            self._watch = None

    # ------------------------------------------------------------- detection

    def _tick_detect(self, _dt) -> None:
        if self._stage not in ("detect", "swap"):
            return
        port, problem = self._find_board()
        if self._stage == "swap":
            # the enforced unplug: the port must VANISH before a new reading
            # can belong to a new antenna...
            if port is None and not self._board_gone:
                self._board_gone = True
                self._render()
            # ...and then RETURN.
            elif port is not None and self._board_gone:
                self._stage = "detect"
                self._board_gone = False
                self._render()
            return
        if port is not None and self._stage == "detect":
            self._port = port
            self._stage = "ready"
            self._render()
        elif port is None and self._stage == "ready":
            self._stage = "detect"
            self._render()

    # --------------------------------------------------------------- reading

    def _start_reading(self, label: str) -> None:
        if self._running:
            return
        self._running = True
        self._stage = "reading"
        self._progress_n = 0
        self._render()

        port = self._port

        def progress(i, floor):
            Clock.schedule_once(lambda *_: self._on_progress(i, floor))

        def work():
            try:
                reading = self._poll(port, progress=progress)
            except Exception as exc:            # noqa: BLE001 — honest fail
                Clock.schedule_once(lambda *_: self._on_read_failed(str(exc)))
                return
            Clock.schedule_once(lambda *_: self._on_read_done(label, reading))

        threading.Thread(target=work, daemon=True).start()

    def _on_progress(self, i, floor) -> None:
        self._progress_n = i
        if self._stage == "reading":
            self._body.text = (tr("Listening through the antenna...")
                               + f"\n\n[b]{floor} dBm[/b]\n"
                               + tr("sample") + f" {i}")

    def _on_read_failed(self, why: str) -> None:
        self._running = False
        self._stage = "detect"
        self._render(extra=tr("The board did not answer. Check it is plugged "
                              "in and running node firmware, then try again.")
                     + f"\n[size=12sp]{why}[/size]")

    def _on_read_done(self, label: str, reading) -> None:
        self._running = False
        result = self.session.add(label, reading)
        if result is None:
            self._on_read_failed(tr("No believable readings arrived."))
            return
        self._stage = "swap"
        self._board_gone = False
        self._render()

    # ------------------------------------------------------------- rendering

    def _render(self, extra: str = "") -> None:
        for child in list(self._buttons.children):
            self._buttons.remove_widget(child)
        n = self.session.count

        if self._stage == "detect":
            self._body.text = (
                tr("Plug the node whose antennas you are comparing into a "
                   "spare USB port. Only that one board - the medic's own "
                   "radio never counts.")
                + ("\n\n" + extra if extra else "")
                + ("\n\n" + self._ranking_markup() if n else ""))
        elif self._stage == "ready":
            self._body.text = (
                tr("Board found. Which antenna is on it right now? Hold it "
                   "upright, the same way every round.")
                + ("\n\n" + self._ranking_markup() if n else ""))
            for name in _PRESETS:
                auto = f"{tr(name)} {n + 1}" if name == "Other" else tr(name)
                b = Button(text=auto, size_hint_y=None, height=dp(44),
                           font_size=theme.font_sp("15sp"))
                b.bind(on_release=lambda w, lab=auto: self._start_reading(lab))
                self._buttons.add_widget(b)
        elif self._stage == "reading":
            self._body.text = tr("Listening through the antenna...")
        elif self._stage == "swap":
            if not self._board_gone:
                self._body.text = (
                    self._ranking_markup() + "\n\n"
                    + tr("Recorded. To test the next antenna: UNPLUG the "
                         "node, swap the antenna while it has no power - "
                         "never swap live - then plug it back in."))
            else:
                self._body.text = (self._ranking_markup() + "\n\n"
                                   + tr("Board unplugged. Swap the antenna, "
                                        "then plug it back in."))
            if self.session.count >= 2:
                done = Button(text=tr("Finish - show the verdict"),
                              size_hint_y=None, height=dp(44),
                              font_size=theme.font_sp("15sp"))
                done.bind(on_release=lambda *_: self._finish())
                self._buttons.add_widget(done)

    def _finish(self) -> None:
        self._stage = "detect"
        body = self._ranking_markup(final=True)
        self._body.text = body + "\n\n" + tr(
            "Suspects can look PERFECT on an impedance meter - a lossy "
            "antenna absorbs power instead of radiating it, and the meter "
            "cannot tell the difference. Trust the ear, and never deploy a "
            "folding antenna folded.")

    def _ranking_markup(self, final: bool = False) -> str:
        ranked = self.session.rank()
        if not ranked:
            return ""
        lines = [("[b]" + tr("Verdict") + "[/b]") if final
                 else ("[b]" + tr("So far") + "[/b]")]
        for i, r in enumerate(ranked, 1):
            colour = _VERDICT_COLOURS.get(r.verdict, "ffffff")
            word = tr(_VERDICT_WORDS.get(r.verdict, r.verdict))
            lines.append(f"{i}. {r.label}   [b]{r.floor} dBm[/b]   "
                         f"[color={colour}]{word}[/color]")
            if r.interference_dbm is not None:
                lines.append("    [size=12sp]" +
                             tr("heard a strong nearby burst") +
                             f" ({r.interference_dbm} dBm)[/size]")
        return "\n".join(lines)
