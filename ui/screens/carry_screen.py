"""Settings ▸ Field readiness — "is this medic ready to be taken somewhere with
no signal?"

Wires workflows.carry (audit/carry_all), which shipped complete and tested
(commit d9b29ca) with no caller anywhere in the app — the same shape of bug
that let the phone-app downloader sit reachable-by-nobody for weeks (see
ui/screens/comms_screen.py's history). This screen is the caller.

Two separate operations, deliberately not merged:
  * enter() runs audit() ONLY — a handful of local `ls`/`du`/`cat` reads
    (~75ms measured on the medic's own Pi 5 hardware for all five items).
    Read-only, no network, safe to run every time the screen is opened.
  * "Prepare for the field" runs carry_all() — which, if online, actually
    downloads (RNode firmware + phone-app APKs can be tens to hundreds of MB).
    That only happens on an explicit tap, never on screen entry, so opening
    this screen can never silently start a multi-minute download.

Both run off-thread (LocalConnection shells out several times per call) and
post back to the UI via Clock.schedule_once — never block the Kivy thread.
"""

from __future__ import annotations

import threading

from kivy.clock import Clock
from kivy.metrics import dp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.scrollview import ScrollView

from ui import theme
from ui.i18n import tr


def _line(text, bold=False, size="15sp", color="text_primary", h=28):
    lbl = Label(text=text, bold=bold, font_size=theme.font_sp(size),
                halign="left", valign="middle",
                size_hint_y=None, color=theme.hex_to_rgba(theme.COLORS[color]))
    lbl.bind(width=lambda i, w: setattr(i, "text_size", (w, None)),
             texture_size=lambda i, ts: setattr(i, "height", max(dp(h), ts[1])))
    return lbl


class CarryScreen(BoxLayout):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.orientation = "vertical"
        self.padding = dp(16)
        self.spacing = dp(8)
        self._busy = False

        self.add_widget(_line(tr("Field readiness"), bold=True, size="24sp", h=42))
        self.add_widget(_line(tr(
            "What this medic must carry to work with no signal. Checked against "
            "the disk right now, not against what was meant to be here."),
            size="13.5sp", color="text_secondary", h=44))
        self.status = _line("", size="13sp", color="accent", h=32)
        self.add_widget(self.status)

        body = ScrollView()
        self.list = BoxLayout(orientation="vertical", size_hint_y=None, spacing=dp(10))
        self.list.bind(minimum_height=self.list.setter("height"))
        body.add_widget(self.list)
        self.add_widget(body)

        self.prep_btn = Button(text=tr("Prepare for the field"), size_hint_y=None,
                               height=dp(52), bold=True, font_size="16sp",
                               background_normal="",
                               background_color=theme.hex_to_rgba(theme.COLORS["accent"]),
                               color=theme.hex_to_rgba(theme.COLORS["background"]))
        self.prep_btn.bind(on_release=lambda *_: self._prepare())
        self.add_widget(self.prep_btn)

    def enter(self):
        """Called every time the screen is shown — a read-only audit(), off
        thread. No network call here: this must stay safe to open at a glance."""
        if self._busy:
            return
        self.status.text = tr("Checking what's carried…")
        self.list.clear_widgets()

        def work():
            from transport.connection import LocalConnection
            from workflows.carry import audit
            statuses = audit(LocalConnection())
            Clock.schedule_once(lambda dt: self._render(statuses), 0)
        threading.Thread(target=work, daemon=True).start()

    def _render(self, statuses):
        self.list.clear_widgets()
        missing = [s for s in statuses if not s.carried]
        if not missing:
            self.status.text = tr("Ready to go — everything is aboard.")
            self.status.color = theme.hex_to_rgba(theme.COLORS["green"])
        else:
            # s.name is data from workflows.carry, not a literal in this file —
            # the AST coverage guard (tests/test_i18n.py) only walks ui/ for
            # tr("literal") calls, so tr(s.name) would be invisible to it and
            # silently never translate. Left in English, matching how
            # self_diagnose_screen.py treats workflow-authored check text.
            self.status.text = tr("STILL MISSING: {gaps}").format(
                gaps=", ".join(s.name for s in missing))
            self.status.color = theme.hex_to_rgba(theme.COLORS["warning_yellow"])
        for s in statuses:
            self.list.add_widget(self._card(s))

    def _card(self, s):
        from kivy.graphics import Color, RoundedRectangle
        card = BoxLayout(orientation="vertical", size_hint_y=None, spacing=dp(4),
                         padding=dp(12))
        card.bind(minimum_height=card.setter("height"))
        with card.canvas.before:
            Color(*theme.hex_to_rgba(theme.COLORS["surface"]))
            card._bg = RoundedRectangle(radius=[dp(10)] * 4)
        card.bind(pos=lambda i, v: setattr(i._bg, "pos", i.pos),
                  size=lambda i, v: setattr(i._bg, "size", i.size))

        mark = "✓" if s.carried else "✗"
        colour = "green" if s.carried else "warning_yellow"
        # s.name / s.detail / s.why are workflows.carry data, not literals here —
        # left untranslated deliberately (see the note in _render above).
        card.add_widget(_line(f"{mark}  {s.name}", bold=True, size="17sp",
                              color=colour, h=26))
        card.add_widget(_line(s.detail, size="13sp", color="text_secondary", h=22))
        if not s.carried:
            card.add_widget(_line(s.why, size="12.5sp",
                                  color="text_secondary", h=32))
            if not s.toppable:
                # HOW to get it aboard — the exact move, not "needs a
                # decision" (operator, 2026-10-04); the generic line only
                # when the workflow has no better answer.
                card.add_widget(_line(s.how or tr(
                    "This needs a decision from you — it will not fill itself."),
                    size="12.5sp", color="warning_yellow", h=22))
        return card

    def _prepare(self):
        """Explicit, on-tap only: tops up everything carry_all() CAN top up
        unattended, then re-audits. May download — can take minutes. Never
        called from enter()."""
        if self._busy:
            return
        self._busy = True
        self.prep_btn.disabled = True
        self.prep_btn.text = tr("Preparing…")
        self.status.text = tr("Checking connectivity and topping up what it can…")

        def work():
            from transport.connection import LocalConnection
            from workflows.carry import carry_all

            def progress(text):
                # each step named as it runs, so "Preparing…" is seen working
                Clock.schedule_once(
                    lambda dt, t=text: setattr(self.status, "text", t), 0)
            report = carry_all(LocalConnection(), progress=progress)
            Clock.schedule_once(lambda dt: self._prepared(report), 0)
        threading.Thread(target=work, daemon=True).start()

    def _prepared(self, report):
        self._busy = False
        self.prep_btn.disabled = False
        self.prep_btn.text = tr("Prepare for the field")
        # Re-render from the report's OWN fresh audit — not from what the
        # downloaders claimed — so the cards reflect the disk, same discipline
        # as comms_screen's _fetched().
        self._render(report.statuses)
        # The report says which of fetched / already current / failed
        # happened; a press with everything aboard used to look like nothing
        # (operator, 2026-10-04). Red only for a fetch that failed.
        self.status.text = report.message or self.status.text
        colour = ("red" if report.failed else
                  "green" if report.ready else "warning_yellow")
        self.status.color = theme.hex_to_rgba(theme.COLORS[colour])
