"""Self Diagnose — the medic checks (and heals) its OWN onboard radio/GPS board.

Reached from PROBE. Runs the safe, non-disruptive checks (monitor.self_diagnose_
runtime.gather), shows each finding with a severity colour, and offers the proven
repair for the ones we can fix on our own (restart the splitter); the bigger
repairs (reflash + provision) show guidance until the auto-recovery lands. Gather
and repair are injectable for tests; the work runs off-thread and marshals back
via the Kivy Clock.
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
from monitor import self_diagnose_runtime as rt
from monitor.self_diagnose import summarize, SEV_OK, SEV_WARN, SEV_CRIT

_SEV_COLOR = {SEV_OK: "green", SEV_WARN: "amber", SEV_CRIT: "red"}


def _line(text, color="text_primary", bold=False, size="14sp", h=None):
    lbl = Label(text=text, halign="left", valign="middle", bold=bold, font_size=size,
                color=theme.hex_to_rgba(theme.COLORS[color]), size_hint_y=None)
    if h is not None:
        lbl.height = dp(h)
        lbl.bind(size=lambda i, v: setattr(i, "text_size", v))
    else:
        lbl.bind(width=lambda i, w: setattr(i, "text_size", (w, None)))
        lbl.bind(texture_size=lambda i, ts: setattr(i, "height", ts[1] + dp(6)))
    return lbl


class SelfDiagnoseScreen(BoxLayout):
    def __init__(self, gather=None, repair=None, **kwargs):
        super().__init__(**kwargs)
        self.orientation = "vertical"
        self.padding = dp(12)
        self.spacing = dp(8)
        self._gather = gather or rt.gather
        self._repair = repair or rt.run_repair
        self._busy = False

        self.add_widget(_line("Self Diagnose — this medic's radio & GPS", bold=True,
                              size="19sp", h=32))
        self.add_widget(_line("Checks the medic's own onboard board (Jonesey) and "
                              "fixes what it safely can.", color="text_secondary",
                              size="12.5sp", h=36))

        self.run_btn = Button(text="Run self-diagnose", size_hint_y=None, height=dp(52),
                              bold=True, font_size="18sp", background_normal="",
                              background_color=theme.hex_to_rgba(theme.COLORS["accent"]),
                              color=theme.hex_to_rgba(theme.COLORS["background"]))
        self.run_btn.bind(on_release=lambda *_: self.start())
        self.add_widget(self.run_btn)

        self.summary = _line("", bold=True, size="15sp", h=30)
        self.add_widget(self.summary)

        scroll = ScrollView()
        self.list = BoxLayout(orientation="vertical", size_hint_y=None, spacing=dp(6))
        self.list.bind(minimum_height=self.list.setter("height"))
        scroll.add_widget(self.list)
        self.add_widget(scroll)

    # -- run ---------------------------------------------------------------

    def start(self):
        if self._busy:
            return
        self._busy = True
        self.run_btn.disabled = True
        self.run_btn.text = "Checking…"
        self.list.clear_widgets()
        self.summary.text = ""

        def work():
            try:
                findings = self._gather()
            except Exception as e:                       # never hang the UI
                findings = None
                err = str(e)
            Clock.schedule_once(lambda dt: self._show(
                findings, None if findings is not None else err), 0)
        threading.Thread(target=work, daemon=True).start()

    def _show(self, findings, err):
        self._busy = False
        self.run_btn.disabled = False
        self.run_btn.text = "Run again"
        if findings is None:
            self.summary.text = f"Couldn't run the checks: {err}"
            self.summary.color = theme.hex_to_rgba(theme.COLORS["red"])
            return
        s = summarize(findings)
        if s["healthy"]:
            self.summary.text = ":)  Radio & GPS healthy"
            self.summary.color = theme.hex_to_rgba(theme.COLORS["green"])
        else:
            self.summary.text = (f"{s['critical']} critical, {s['warning']} warning"
                                 + ("  ·  tap Fix below" if s["fixes"] else ""))
            self.summary.color = theme.hex_to_rgba(
                theme.COLORS["red" if s["critical"] else "amber"])
        for f in findings:
            self.list.add_widget(self._finding_row(f))

    def _finding_row(self, f):
        row = BoxLayout(orientation="vertical", size_hint_y=None, spacing=dp(2),
                        padding=[dp(8), dp(6)])
        row.bind(minimum_height=row.setter("height"))
        mark = "OK" if f.ok else ("X" if f.severity == SEV_CRIT else "!")
        row.add_widget(_line(f"[{mark}]  {f.detail}",
                             color=_SEV_COLOR.get(f.severity, "amber"), size="13.5sp"))
        if f.fix and not f.ok:
            kind = rt.repair_kind(f.fix)
            if kind == "auto":
                btn = Button(text="Fix it", size_hint=(None, None), size=(dp(110), dp(40)),
                             bold=True, background_normal="",
                             background_color=theme.hex_to_rgba(theme.COLORS["accent"]),
                             color=theme.hex_to_rgba(theme.COLORS["background"]))
                btn.bind(on_release=lambda _b, k=f.fix, b=btn: self._fix(k, b))
                row.add_widget(btn)
            else:
                row.add_widget(_line("→ " + rt.guidance(f.fix),
                                     color="text_secondary", size="12sp"))
        return row

    def _fix(self, key, button):
        if self._busy:
            return
        self._busy = True
        button.disabled = True
        button.text = "Fixing…"

        def work():
            ok, msg = self._repair(key)
            Clock.schedule_once(lambda dt: self._fix_done(button, ok, msg), 0)
        threading.Thread(target=work, daemon=True).start()

    def _fix_done(self, button, ok, msg):
        self._busy = False
        button.text = "Fixed — re-checking" if ok else "Failed"
        button.background_color = theme.hex_to_rgba(
            theme.COLORS["green" if ok else "red"])
        if ok:
            Clock.schedule_once(lambda dt: self.start(), 1.2)   # re-run to confirm
