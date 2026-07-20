"""PROBE — diagnose & repair a node.

A header names the node being checked; "Run full diagnostic" streams the
categories in, each collapsing to an OK / X-count summary. When it finishes, a
fault-count summary + "Fix all" sit at the top. Fixes are HONEST: a fix runs,
then the exact check is re-tested, and the row only says "Fixed" once the fault
is actually gone. Fix-all shows a progress bar, then a green smiley if the node
is clean, or the remaining count ("may need individual attention").

The workflow runs on a background thread; events marshal onto the UI thread via
Clock. The workflow is injected (a factory) so this stays transport-agnostic.
"""

from __future__ import annotations

import threading

from kivy.clock import Clock
from kivy.metrics import dp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.progressbar import ProgressBar
from kivy.uix.scrollview import ScrollView

from ui import theme

_SEV_COLOR = {"critical": "red", "warning": "amber", "info": "text_secondary"}


def _label(text, color="text_primary", bold=False, size="16sp"):
    lbl = Label(text=text, halign="left", valign="middle", bold=bold,
                font_size=size, color=theme.hex_to_rgba(theme.COLORS[color]))
    lbl.bind(size=lambda i, v: setattr(i, "text_size", v))
    return lbl


class ProbeScreen(BoxLayout):
    def __init__(self, workflow_factory, target_name="this node", **kwargs):
        super().__init__(**kwargs)
        self.orientation = "vertical"
        self.padding = dp(10)
        self.spacing = dp(6)
        self._workflow_factory = workflow_factory
        self._target = target_name
        self._workflow = None
        self._category_boxes = {}
        self._issue_rows = {}
        self._busy = False

        self.header = _label(f"Checking: {target_name}", bold=True, size="18sp")
        self.header.size_hint_y = None
        self.header.height = dp(30)
        self.add_widget(self.header)

        self.run_btn = Button(
            text="Run full diagnostic", size_hint_y=None, height=dp(56),
            font_size="20sp", background_normal="",
            background_color=theme.hex_to_rgba(theme.COLORS["accent"]),
            color=theme.hex_to_rgba(theme.COLORS["background"]))
        self.run_btn.bind(on_release=lambda *_: self.start())
        self.add_widget(self.run_btn)

        self.summary_bar = BoxLayout(size_hint_y=None, height=dp(0),
                                     spacing=dp(8), opacity=0)
        self.summary_lbl = _label("", bold=True, size="17sp")
        self.fix_all_btn = Button(
            text="Fix all", size_hint_x=None, width=dp(130), opacity=0,
            disabled=True, background_normal="",
            background_color=theme.hex_to_rgba(theme.COLORS["green"]),
            color=theme.hex_to_rgba(theme.COLORS["background"]))
        self.fix_all_btn.bind(on_release=lambda *_: self._fix_all())
        self.summary_bar.add_widget(self.summary_lbl)
        self.summary_bar.add_widget(self.fix_all_btn)
        self.add_widget(self.summary_bar)

        self.progress = ProgressBar(max=1, value=0, size_hint_y=None,
                                    height=dp(0), opacity=0)
        self.add_widget(self.progress)

        self.scroll = ScrollView()
        self.list = BoxLayout(orientation="vertical", size_hint_y=None,
                              spacing=dp(2))
        self.list.bind(minimum_height=self.list.setter("height"))
        self.scroll.add_widget(self.list)
        self.add_widget(self.scroll)

    # -- run ----------------------------------------------------------------

    def start(self):
        if self.run_btn.disabled or self._busy:
            return
        orig_label = self.run_btn.text
        self.run_btn.disabled = True
        self.run_btn.text = f"Checking {self._target}..."
        self._workflow = self._workflow_factory()
        # No board attached (or path not wired): plain popup, don't run/fake it.
        if getattr(self._workflow, "is_blocked", False):
            from ui.requirement_popup import requirement_popup
            requirement_popup(self._workflow.message,
                              getattr(self._workflow, "title", "Heads up"),
                              getattr(self._workflow, "under_construction", False))
            self.run_btn.disabled = False
            self.run_btn.text = orig_label
            return
        self.list.clear_widgets()
        self._category_boxes = {}
        self._issue_rows = {}
        self._set_summary(hidden=True)
        self._set_progress(hidden=True)
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        self._workflow.run(on_progress=lambda e:
                           Clock.schedule_once(lambda dt: self._on_event(e), 0))

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
        header = _label(f"> {name}", bold=True, size="18sp")
        header.size_hint_y = None
        header.height = dp(34)
        checks = BoxLayout(orientation="vertical", size_hint_y=None, height=dp(0),
                           padding=(dp(18), 0, 0, 0))
        checks.bind(minimum_height=checks.setter("height"))
        self._category_boxes[name] = (header, checks)
        self.list.add_widget(header)
        self.list.add_widget(checks)

    def _add_check(self, category, check_name, issue):
        box = self._category_boxes.get(category)
        if not box:
            return
        _, checks = box
        mark = "OK" if issue is None else "X"
        color = "green" if issue is None else _SEV_COLOR.get(issue.severity, "amber")
        row = _label(f"  {mark} {check_name}", color=color, size="14sp")
        row.size_hint_y = None
        row.height = dp(24)
        checks.add_widget(row)

    def _mark_category(self, name, result):
        box = self._category_boxes.get(name)
        if not box:
            return
        header, _ = box
        mark = "OK" if result.passed else f"X {len(result.issues)}"
        header.text = f"{name}   {mark}"
        header.color = theme.hex_to_rgba(
            theme.COLORS["green" if result.passed else "amber"])

    # -- summary + outcome --------------------------------------------------

    def _finish(self, session):
        self.run_btn.disabled = False
        self.run_btn.text = "Run again"
        self._render_summary(session)

    def _render_summary(self, session):
        issues = session.all_issues
        n = len(issues)
        fixable = session.auto_fixable_issues
        if n == 0:
            self.summary_lbl.text = ":)   All clear - no faults found"
            self.summary_lbl.color = theme.hex_to_rgba(theme.COLORS["green"])
            self.fix_all_btn.opacity = 0
            self.fix_all_btn.disabled = True
        else:
            self.summary_lbl.text = (f"{n} fault{'s' if n != 1 else ''} found"
                                     + (f"  -  {len(fixable)} auto-fixable"
                                        if fixable else ""))
            self.summary_lbl.color = theme.hex_to_rgba(theme.COLORS["amber"])
            self.fix_all_btn.opacity = 1 if fixable else 0
            self.fix_all_btn.disabled = not fixable
            self.fix_all_btn.text = f"Fix all {len(fixable)}"
        self._set_summary(hidden=False)
        for issue in issues:
            self.list.add_widget(self._issue_row(issue))

    def _issue_row(self, issue):
        row = BoxLayout(orientation="horizontal", size_hint_y=None,
                        height=dp(46), spacing=dp(6))
        row.add_widget(_label(f"[{issue.severity}] {issue.description}",
                              color=_SEV_COLOR.get(issue.severity, "amber"),
                              size="14sp"))
        if issue.auto_fixable:
            btn = Button(text="Fix", size_hint_x=None, width=dp(96),
                         background_normal="",
                         background_color=theme.hex_to_rgba(theme.COLORS["accent"]),
                         color=theme.hex_to_rgba(theme.COLORS["background"]))
            btn.bind(on_release=lambda *_a, i=issue, b=btn: self._fix_one(i, b))
            row.add_widget(btn)
            self._issue_rows[issue.check_name] = (row, btn)
        return row

    def _set_summary(self, hidden):
        self.summary_bar.height = dp(0) if hidden else dp(48)
        self.summary_bar.opacity = 0 if hidden else 1

    def _set_progress(self, hidden, value=0.0, maximum=1.0):
        self.progress.height = dp(0) if hidden else dp(14)
        self.progress.opacity = 0 if hidden else 1
        self.progress.max = maximum
        self.progress.value = value

    # -- fixes (apply -> RE-TEST -> only then say "Fixed") ------------------

    def _fix_one(self, issue, button):
        if self._busy:
            return
        self._busy = True
        button.disabled = True
        button.text = "Fixing..."

        def work():
            fix = self._workflow.fix_one(issue)
            ok = fix.success and self._workflow.verify_fixed(issue)
            Clock.schedule_once(lambda dt: self._fix_one_done(button, ok), 0)

        threading.Thread(target=work, daemon=True).start()

    def _fix_one_done(self, button, ok):
        self._busy = False
        if ok:
            button.text = "Fixed"
            button.background_color = theme.hex_to_rgba(theme.COLORS["green"])
        else:
            button.text = "Still failing"
            button.background_color = theme.hex_to_rgba(theme.COLORS["red"])
            button.disabled = False

    def _fix_all(self):
        if self._busy or self._workflow is None:
            return
        self._busy = True
        self.fix_all_btn.disabled = True
        issues = list(self._workflow.session.auto_fixable_issues)
        self._set_progress(hidden=False, value=0, maximum=max(1, len(issues)))
        self.summary_lbl.text = "Working through the faults..."
        self.summary_lbl.color = theme.hex_to_rgba(theme.COLORS["text_primary"])

        def work():
            for i, issue in enumerate(issues, 1):
                self._workflow.fix_one(issue)
                Clock.schedule_once(
                    lambda dt, v=i: setattr(self.progress, "value", v), 0)
            self._workflow.rescan()
            Clock.schedule_once(lambda dt: self._fix_all_done(), 0)

        threading.Thread(target=work, daemon=True).start()

    def _fix_all_done(self):
        self._busy = False
        self._set_progress(hidden=True)
        session = self._workflow.session
        remaining = len(session.all_issues)
        self.list.clear_widgets()
        self._category_boxes = {}
        self._issue_rows = {}
        for issue in session.all_issues:
            self.list.add_widget(self._issue_row(issue))
        if remaining == 0:
            self.summary_lbl.text = ":)   All faults resolved"
            self.summary_lbl.color = theme.hex_to_rgba(theme.COLORS["green"])
            self.fix_all_btn.opacity = 0
        else:
            self.summary_lbl.text = (f"{remaining} fault"
                                     f"{'s' if remaining != 1 else ''} may need "
                                     "individual attention")
            self.summary_lbl.color = theme.hex_to_rgba(theme.COLORS["amber"])
            still = session.auto_fixable_issues
            self.fix_all_btn.opacity = 1 if still else 0
            self.fix_all_btn.disabled = not still
            self.fix_all_btn.text = f"Fix all {len(still)}"
