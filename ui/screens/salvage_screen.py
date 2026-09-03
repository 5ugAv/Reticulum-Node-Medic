"""Show me what you got — the front door for salvaged hardware.

Operator, 2026-09-03. The medic used to ask "which of these 16 boards is
yours?", which only answers for someone who went out and bought one of the 16.
This asks the other way round: show me what you have, and I will tell you what
it could be.

A shell over ``provisioning.salvage`` and ``provisioning.salvage_guide`` —
every decision and every sentence lives there, tested without a display.

WRITTEN FOR SOMEONE WHO HAS NOT DONE THIS BEFORE. One question per screen. The
questions are about what the thing LOOKS like, never about what it is, because
"is there a small metal box with an aerial socket next to it" can be answered by
anyone holding the board and "does it have a LoRa transceiver" cannot.
"""
from __future__ import annotations

import threading

from kivy.clock import Clock
from kivy.metrics import dp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.scrollview import ScrollView
from kivy.uix.widget import Widget

from provisioning import salvage as sv
from provisioning import salvage_guide as sg
from ui import theme


def _line(text, size="15sp", color="text_primary", bold=False):
    """Grows to its text. Fixed heights have clipped this project twice."""
    lbl = Label(text=text, font_size=theme.font_sp(size), bold=bold,
                halign="left", valign="top", size_hint_y=None,
                color=theme.hex_to_rgba(theme.COLORS[color]))
    lbl.bind(width=lambda i, w: setattr(i, "text_size", (w, None)),
             texture_size=lambda i, ts: setattr(i, "height", ts[1] + dp(6)))
    return lbl


def _button(text, on_press, color="surface", height=72):
    b = Button(text=text, size_hint_y=None, height=dp(height), font_size="17sp",
               bold=True, halign="left", valign="middle",
               background_normal="", background_down="",
               background_color=theme.hex_to_rgba(theme.COLORS[color]),
               color=theme.hex_to_rgba(theme.COLORS["text_primary"]))
    b.bind(size=lambda i, v: setattr(i, "text_size", (v[0] - dp(24), v[1])))
    b.bind(on_release=lambda *_: on_press())
    return b


class SalvageScreen(BoxLayout):
    """What have you got? -> what can it be? -> how do I do it?"""

    def __init__(self, detect_fn=None, **kwargs):
        kwargs.setdefault("orientation", "vertical")
        kwargs.setdefault("padding", dp(18))
        kwargs.setdefault("spacing", dp(10))
        super().__init__(**kwargs)
        self._detect_fn = detect_fn
        self._found = sv.Found()
        self._busy = False
        self._body = BoxLayout(orientation="vertical", size_hint_y=None,
                               spacing=dp(10))
        self._body.bind(minimum_height=self._body.setter("height"))
        scroll = ScrollView(do_scroll_x=False, bar_width=dp(4))
        scroll.add_widget(self._body)
        self.add_widget(scroll)
        self.show_start()

    def _stage(self, *widgets):
        self._body.clear_widgets()
        for w in widgets:
            self._body.add_widget(w)
        self._body.add_widget(Widget(size_hint_y=None, height=dp(8)))

    # -- 1. what have you got ------------------------------------------------

    def show_start(self):
        self._found = sv.Found()
        rows = [
            _line("Show me what you got", "24sp", bold=True, color="accent"),
            _line("Almost anything with a chip in it can do a job in the "
                  "network. Tell me what you are holding.", "15sp",
                  color="text_secondary"),
        ]
        for key, text in sv.KINDS:
            rows.append(_button(text, lambda k=key: self._picked_kind(k)))
        self._stage(*rows)

    def _picked_kind(self, kind):
        self._found = sv.Found(kind=kind)
        # Only things with a socket are worth plugging in. Offering to probe a
        # handheld radio would waste the keeper's time and teach them the tool
        # does not understand what they just said.
        if kind in ("dev_board", "unknown"):
            self.show_offer_probe()
        else:
            self.show_verdict()

    # -- 2. plug it in (optional) --------------------------------------------

    def show_offer_probe(self):
        self._stage(
            _line("Does it have a USB socket?", "22sp", bold=True),
            _line("If it does, plug it into the medic now and I will read what "
                  "I can straight off the chip. It saves you answering "
                  "questions.", "15sp", color="text_secondary"),
            _button("It is plugged in — read it", self._probe, color="accent"),
            _button("No socket, or it will not plug in", self.show_questions),
            _button("Start again", self.show_start))

    def _probe(self):
        if self._busy:
            return
        self._busy = True
        self._stage(_line("Reading the chip…", "22sp", bold=True),
                    _line("A few seconds.", "15sp", color="text_secondary"))

        def run():
            try:
                result = self._detect()
            except Exception as exc:
                result = {"found": False, "reason": str(exc)}
            Clock.schedule_once(lambda _dt: self._probed(result), 0)

        threading.Thread(target=run, daemon=True).start()

    def _detect(self):
        if self._detect_fn:
            return self._detect_fn()
        from ui.board_detect import detect_board
        from workflows.rnode_boards import RNODE_BOARDS
        boards = (list(RNODE_BOARDS.values()) if isinstance(RNODE_BOARDS, dict)
                  else list(RNODE_BOARDS))
        return detect_board(boards)

    def _probed(self, result):
        self._busy = False
        found = sv.found_from_detection(result)
        # Keep what the keeper already told us; the probe only ADDS.
        self._found = sv.Found(
            kind=self._found.kind if self._found.kind != "unknown" else found.kind,
            chip=found.chip, board_key=found.board_key,
            has_lora=found.has_lora, has_usb=found.has_usb)
        if not result.get("found"):
            self._stage(
                _line("I could not read anything.", "22sp", bold=True,
                      color="warning_yellow"),
                _line(result.get("reason") or
                      "Nothing answered on the USB. It may need a different "
                      "cable — a lot of cables only carry power, not data.",
                      "15sp", color="text_secondary"),
                _button("Answer some questions instead", self.show_questions,
                        color="accent"),
                _button("Try again", self._probe),
                _button("Start again", self.show_start))
            return
        self.show_questions()

    # -- 3. the questions ----------------------------------------------------

    def show_questions(self):
        pending = sv.questions_for(self._found)
        if not pending:
            return self.show_verdict()
        q = pending[0]
        rows = [_line(q.text, "22sp", bold=True)]
        if q.look_for:
            rows.append(_line(q.look_for, "15sp", color="text_secondary"))
        rows += [
            _button("Yes", lambda: self._answer(q.field, True), color="accent"),
            _button("No", lambda: self._answer(q.field, False)),
            _button("I cannot tell", lambda: self._answer(q.field, None)),
        ]
        self._stage(*rows)

    def _answer(self, field, value):
        """"I cannot tell" is a real answer and must not loop.

        Left as None the question would be asked again for ever. Recorded as
        False instead, with the verdict framed as what is certain — the keeper
        gets the safe route rather than an unanswerable screen they cannot
        leave.
        """
        import dataclasses
        self._found = dataclasses.replace(
            self._found, **{field: (False if value is None else value)})
        self.show_questions()

    # -- 4. what it can be ---------------------------------------------------

    def show_verdict(self):
        paths = sv.paths_for(self._found)
        rows = [_line(sv.summary(self._found), "20sp", bold=True,
                      color="accent")]
        for p in paths:
            colour = "green" if p.ready_now else "text_primary"
            rows.append(_line(p.title, "18sp", bold=True, color=colour))
            rows.append(_line(p.plain, "14sp", color="text_secondary"))
            if p.needs:
                rows.append(_line("You will need:  " + " · ".join(p.needs),
                                  "14sp", color="amber"))
            if p.caution:
                rows.append(_line(p.caution, "14sp", color="warning_yellow"))
            if sg.guide_for_path(p.title):
                rows.append(_button("Show me how",
                                    lambda t=p.title: self.show_guide(t),
                                    color="accent", height=58))
            rows.append(Widget(size_hint_y=None, height=dp(6)))
        rows.append(_button("Start again", self.show_start))
        self._stage(*rows)

    # -- 5. how to do it -----------------------------------------------------

    def show_guide(self, path_title):
        g = sg.guide_for_path(path_title)
        if g is None:
            return self.show_verdict()
        rows = [_line(g.title, "22sp", bold=True, color="accent"),
                _line(g.opening, "15sp", color="text_secondary"),
                _line("What you need", "17sp", bold=True)]
        for n in g.needs:
            rows.append(_line("•  " + n, "14sp", color="text_secondary"))
        rows.append(_line("What to do", "17sp", bold=True))
        for i, s in enumerate(g.steps, 1):
            rows.append(_line(f"{i}.  {s.text}", "15sp", bold=True))
            if s.detail:
                rows.append(_line("     " + s.detail, "14sp",
                                  color="text_secondary"))
            if s.watch_out:
                rows.append(_line("     Watch out:  " + s.watch_out, "14sp",
                                  color="amber"))
        if g.expect:
            rows += [_line("What to expect", "17sp", bold=True),
                     _line(g.expect, "14sp", color="text_secondary")]
        if g.caution:
            rows += [_line("Before you transmit", "17sp", bold=True,
                           color="warning_yellow"),
                     _line(g.caution, "14sp", color="warning_yellow")]
        if not g.medic_does_it:
            rows.append(_line(
                "The medic cannot do this part for you yet — these are "
                "instructions to follow yourself.", "14sp", color="amber"))
        rows.append(_button("Back", self.show_verdict))
        self._stage(*rows)
