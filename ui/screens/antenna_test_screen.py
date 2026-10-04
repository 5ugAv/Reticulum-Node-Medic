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
import time

from kivy.clock import Clock
from kivy.graphics import Color, RoundedRectangle
from kivy.logger import Logger
from kivy.metrics import dp
from kivy.properties import NumericProperty
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.scrollview import ScrollView
from kivy.uix.widget import Widget

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


class _Bar(Widget):
    """A plain filled bar: 0..1, drawn in the screen's own colours."""

    value = NumericProperty(0.0)

    def __init__(self, **kwargs):
        super().__init__(size_hint_y=None, height=0, opacity=0, **kwargs)
        with self.canvas:
            Color(*theme.hex_to_rgba(theme.COLORS["sidebar"]))
            self._track = RoundedRectangle(radius=[dp(7)])
            Color(*theme.hex_to_rgba(theme.COLORS["accent"]))
            self._fill = RoundedRectangle(radius=[dp(7)])
        self.bind(pos=self._redraw, size=self._redraw, value=self._redraw)

    def _redraw(self, *_):
        self._track.pos, self._track.size = self.pos, self.size
        w = max(dp(14), self.width * max(0.0, min(1.0, self.value)))
        self._fill.pos, self._fill.size = self.pos, (w, self.height)

    def show(self, on: bool) -> None:
        self.height = dp(16) if on else 0
        self.opacity = 1 if on else 0


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
        self._problem = ""          # why no board can be read right now (from the backend)
        self._notice = ""           # the last failure, kept until the next reading starts
        self._port = None           # the board we read through last
        self._board_gone = False    # the enforced unplug was seen
        self._running = False
        self._watch = None
        self._ticker = None         # drives the bar between samples
        self._progress_n = 0
        self._progress_floor = None
        self._progress_at = time.monotonic()
        self._shown = None

        self._title = _label(tr("Antenna test"), size="20sp", bold=True,
                             color="text_primary")
        self.add_widget(self._title)

        # the one rule, always on screen — the whole point of the feature
        self._rule = _label(tr("Higher is better: a good antenna hears MORE "
                               "of everything, noise included. The quiet one "
                               "is usually the deaf one.")
                            + " " + tr("(These are negative numbers: -95 dBm "
                                       "is HIGHER than -105 dBm.)"),
                            size="13sp", color="text_secondary")
        self.add_widget(self._rule)

        self._bar = _Bar()      # only visible while a reading runs
        self.add_widget(self._bar)

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
        if self._stage == "done":
            # a finished verdict belongs to the last visit; a new visit is a
            # new comparison (readiness sweep, 2026-10-03)
            self.session = at.AntennaSession()
        self._stage = "detect"
        self._render()
        self._tick_detect(0)

    def sleep(self) -> None:
        """on_leave: stop watching; a reading already in flight finishes on
        its own thread and lands harmlessly."""
        if self._watch is not None:
            self._watch.cancel()
            self._watch = None
        self._stop_ticker()

    # ------------------------------------------------------------- detection

    def _tick_detect(self, _dt) -> None:
        if self._stage not in ("detect", "swap"):
            return
        port, problem = self._find_board()
        # SAY WHY when the backend has a reason (two boards plugged in, or
        # none) — the screen used to drop it and show the generic sentence
        # "no board" is not shown: the instructions above it already say
        # exactly that, and it read as the same sentence twice (2026-10-04)
        why = (tr(problem[1]) if (port is None and problem
                                  and problem[0] != "no_board") else "")
        if why != self._problem and self._stage == "detect":
            self._problem = why
            self._render()
        elif why != self._problem:
            self._problem = why
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
        self._notice = ""            # a new reading clears the last failure
        if self._running:
            return
        self._running = True
        self._stage = "reading"
        self._progress_n = 0
        self._progress_floor = None
        self._progress_at = time.monotonic()
        self._render()

        port = self._port

        def progress(i, floor):
            Clock.schedule_once(lambda *_: self._on_progress(i, floor))

        def work():
            try:
                reading = self._poll(port, progress=progress)
            except Exception as exc:            # noqa: BLE001 — honest fail
                # Python deletes `exc` when this block ends; the lambda runs
                # later, on the UI thread, so it must hold a copy. It did not,
                # and the NameError took the whole app down (2026-10-04).
                why = str(exc)
                Logger.warning("AntennaTest: reading failed: %s", why)
                Clock.schedule_once(lambda *_, w=why: self._on_read_failed(w))
                return
            Clock.schedule_once(lambda *_: self._on_read_done(label, reading))

        threading.Thread(target=work, daemon=True).start()

    def _on_progress(self, i, floor) -> None:
        self._progress_n = i
        self._progress_floor = floor
        self._progress_at = time.monotonic()
        if self._stage == "reading":
            self._paint_progress()

    def _start_ticker(self) -> None:
        if self._ticker is None:
            self._ticker = Clock.schedule_interval(
                lambda _dt: self._paint_progress(), 0.1)

    def _stop_ticker(self) -> None:
        if self._ticker is not None:
            self._ticker.cancel()
            self._ticker = None

    def _paint_progress(self) -> None:
        """The bar and the 'sample 3 of 8' line - redrawn 10x a second, the
        text only when what it says has changed."""
        if self._stage != "reading":
            return
        n, total = self._progress_n, at.EAR_SAMPLES
        since = time.monotonic() - self._progress_at
        self._bar.value = at.progress_fraction(n, total, since)
        left = at.seconds_left(n, total, since)
        shown = (n, self._progress_floor, left)
        if shown == self._shown:
            return
        self._shown = shown
        lines = [tr("Listening through the antenna..."),
                 tr("Hold the node steady and upright. Do not touch the "
                    "antenna.")]
        if n == 0:
            lines.append(tr("Waiting for the first reading..."))
        else:
            if self._progress_floor is not None:
                lines.append(f"[size=30sp][b]{self._progress_floor} dBm[/b]"
                             "[/size]")
            when = (tr("Almost done...") if n >= total else
                    tr("about {s} seconds left").format(s=left))
            lines.append(tr("Sample {n} of {total}").format(n=n, total=total)
                         + "  -  " + when)
        self._body.text = "\n\n".join(lines)

    def _on_read_failed(self, why: str) -> None:
        self._running = False
        self._stage = "detect"
        # kept in _notice until the next reading starts: the detect watch
        # re-rendered 0-2 s later and the reason vanished (2026-10-03)
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
        if self._stage == "reading":
            self._bar.show(True)
            self._start_ticker()
        else:
            self._bar.show(False)
            self._bar.value = 0.0
            self._stop_ticker()
        n = self.session.count
        if extra:
            self._notice = extra
        notice = self._notice

        if self._stage == "detect":
            self._body.text = (
                "\u2022 " + tr("Plug the node whose antennas you are comparing into a "
                   "spare USB port. Only that one board - the medic's own "
                   "radio never counts.")
                + "\n\u2022 " + tr("A long USB cable lets you hold the node clear "
                                  "of the medic.")
                + "\n\u2022 " + tr("Keep it upright in the same spot for every "
                                  "antenna. Each reading takes about half a "
                                  "minute.")
                + ("\n\n[b]" + self._problem + "[/b]" if self._problem else "")
                + ("\n\n" + notice if notice else "")
                + ("\n\n" + self._ranking_markup() if n else ""))
        elif self._stage == "ready":
            self._body.text = (
                tr("Board found. Which antenna is on it right now? Hold it "
                   "upright, the same way every round.")
                + ("\n\n" + notice if notice else "")
                + ("\n\n" + self._ranking_markup() if n else ""))
            for name in _PRESETS:
                auto = f"{tr(name)} {n + 1}" if name == "Other" else tr(name)
                b = Button(text=auto, size_hint_y=None, height=dp(44),
                           font_size=theme.font_sp("15sp"))
                b.bind(on_release=lambda w, lab=auto: self._start_reading(lab))
                self._buttons.add_widget(b)
        elif self._stage == "reading":
            self._shown = None
            self._paint_progress()
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
        # "done", not "detect": the 2 s watch used to see the board, flip to
        # "ready" and overwrite the verdict with "Board found…" (2026-10-03)
        self._stage = "done"
        for child in list(self._buttons.children):
            self._buttons.remove_widget(child)
        body = self._ranking_markup(final=True)
        self._body.text = body + "\n\n" + tr(
            "Suspects can look PERFECT on an impedance meter - a lossy "
            "antenna absorbs power instead of radiating it, and the meter "
            "cannot tell the difference. Trust the ear, and never deploy a "
            "folding antenna folded.")
        again = Button(text=tr("Start again - a new comparison"),
                       size_hint_y=None, height=dp(44),
                       font_size=theme.font_sp("15sp"))
        again.bind(on_release=lambda *_: self._start_again())
        self._buttons.add_widget(again)

    def _start_again(self) -> None:
        """Forget the finished comparison and look for a board afresh."""
        self.session = at.AntennaSession()
        self._notice = ""
        self._stage = "detect"
        self._render()
        self._tick_detect(0)

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
