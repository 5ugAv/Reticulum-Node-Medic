"""Diagnose / Repair screen.

One "Run full diagnostic" button runs the RepairWorkflow across all six Pi
categories. Categories stream in as live rows (expanding to their individual
checks, collapsing to a ✓/✗ summary); when the run completes, failed checks get
"Fix" buttons plus a "Fix all" for the auto-fixable ones.

The workflow runs on a background thread; progress events are marshalled onto
the UI thread with Clock so the display stays responsive. The workflow itself is
injected (a factory) so this screen is transport-agnostic and the heavy lifting
stays in the tested core.
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

_SEV_COLOR = {"critical": "red", "warning": "amber", "info": "text_secondary"}


def _label(text, color="text_primary", bold=False, size="16sp"):
    lbl = Label(text=text, halign="left", valign="middle", bold=bold,
                font_size=size, color=theme.hex_to_rgba(theme.COLORS[color]))
    lbl.bind(size=lambda i, v: setattr(i, "text_size", v))
    return lbl


class RepairScreen(BoxLayout):
    def __init__(self, workflow_factory, **kwargs):
        super().__init__(**kwargs)
        self.orientation = "vertical"
        self.padding = dp(10)
        self.spacing = dp(8)
        self._workflow_factory = workflow_factory
        self._workflow = None
        self._category_boxes = {}   # category name -> (header, checks box)

        self.run_btn = Button(
            text="Run full diagnostic", size_hint_y=None, height=dp(56),
            font_size="20sp", background_normal="",
            background_color=theme.hex_to_rgba(theme.COLORS["accent"]),
            color=theme.hex_to_rgba(theme.COLORS["background"]))
        self.run_btn.bind(on_release=lambda *_: self.start())
        self.add_widget(self.run_btn)

        self.scroll = ScrollView()
        self.list = BoxLayout(orientation="vertical", size_hint_y=None,
                              spacing=dp(2))
        self.list.bind(minimum_height=self.list.setter("height"))
        self.scroll.add_widget(self.list)
        self.add_widget(self.scroll)

    # -- run ---------------------------------------------------------------

    def start(self):
        self.run_btn.disabled = True
        self.run_btn.text = "Running diagnostic..."
        self.list.clear_widgets()
        self._category_boxes = {}
        self._workflow = self._workflow_factory()
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        # Background thread: fire the workflow, marshal every event onto the UI.
        self._workflow.run(on_progress=lambda e:
                           Clock.schedule_once(lambda dt: self._on_event(e), 0))

    # -- UI-thread event handling ------------------------------------------

    def _on_event(self, event):
        if event.type == "category_start":
            self._add_category(event.category)
        elif event.type == "check_done":
            self._add_check(event.category, event.check_name, event.issue)
        elif event.type == "category_done":
            self._mark_category(event.category, event.category_result)
        elif event.type == "run_complete":
            self._finish(event.session)

    def _add_category(self, name):
        header = _label(f"▸ {name}", bold=True, size="18sp")
        header.size_hint_y = None
        header.height = dp(36)
        checks = BoxLayout(orientation="vertical", size_hint_y=None,
                           height=dp(0), padding=(dp(18), 0, 0, 0))
        checks.bind(minimum_height=checks.setter("height"))
        self._category_boxes[name] = (header, checks)
        self.list.add_widget(header)
        self.list.add_widget(checks)

    def _add_check(self, category, check_name, issue):
        box = self._category_boxes.get(category)
        if not box:
            return
        _, checks = box
        mark = "✓" if issue is None else "✗"
        color = "green" if issue is None else _SEV_COLOR.get(
            issue.severity, "amber")
        row = _label(f"  {mark} {check_name}", color=color, size="14sp")
        row.size_hint_y = None
        row.height = dp(24)
        checks.add_widget(row)

    def _mark_category(self, name, result):
        box = self._category_boxes.get(name)
        if not box:
            return
        header, _ = box
        mark = "✓" if result.passed else f"✗ {len(result.issues)}"
        header.text = f"▾ {name}   {mark}"
        header.color = theme.hex_to_rgba(
            theme.COLORS["green" if result.passed else "amber"])

    def _finish(self, session):
        self.run_btn.disabled = False
        self.run_btn.text = "Run full diagnostic"
        issues = session.all_issues
        summary = _label(
            f"{len(issues)} issue(s) — {len(session.auto_fixable_issues)} "
            f"auto-fixable", bold=True, size="17sp")
        summary.size_hint_y = None
        summary.height = dp(40)
        self.list.add_widget(summary)

        if session.auto_fixable_issues:
            fix_all = Button(
                text="Fix all", size_hint_y=None, height=dp(48),
                background_normal="",
                background_color=theme.hex_to_rgba(theme.COLORS["green"]),
                color=theme.hex_to_rgba(theme.COLORS["background"]))
            fix_all.bind(on_release=lambda *_: self._fix_all())
            self.list.add_widget(fix_all)

        for issue in issues:
            self.list.add_widget(self._issue_row(issue))

    def _issue_row(self, issue):
        row = BoxLayout(orientation="horizontal", size_hint_y=None,
                        height=dp(44), spacing=dp(6))
        row.add_widget(_label(
            f"[{issue.severity}] {issue.description}",
            color=_SEV_COLOR.get(issue.severity, "amber"), size="14sp"))
        if issue.auto_fixable:
            btn = Button(text="Fix", size_hint_x=None, width=dp(80),
                         background_normal="",
                         background_color=theme.hex_to_rgba(theme.COLORS["accent"]))
            btn.bind(on_release=lambda *_a, i=issue: self._fix_one(i))
            row.add_widget(btn)
        return row

    def _fix_all(self):
        threading.Thread(
            target=lambda: self._workflow.fix_all(), daemon=True).start()

    def _fix_one(self, issue):
        threading.Thread(
            target=lambda: self._workflow.fix_one(issue), daemon=True).start()
