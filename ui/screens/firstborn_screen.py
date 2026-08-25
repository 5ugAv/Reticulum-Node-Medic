"""THE FIRSTBORN — a new medic births its own Heltec Wireless Tracker.

The Pi 5 has no GNSS and no battery-backed clock, so a fresh medic cannot see
where it is or what time it is. Its first child fixes that: a Tracker flashed
with the GPS→USB NMEA passthrough, adopted as the medic's own position + time
source. This screen makes a ceremony of it — number-one child, a real
celebration — but every DECISION it makes (is a Tracker plugged in, does the
medic already have GPS, did the flash succeed) lives in :mod:`ui.firstborn_flow`
where it is unit-tested. The screen only renders what that returns and runs the
real :class:`workflows.gps_setup.GpsTrackerSetup` — never an emulated stand-in,
per the no-fake-demos rule: on a medic it does real work or fails honestly.
"""

from __future__ import annotations

import threading

from kivy.animation import Animation
from kivy.clock import Clock
from kivy.graphics import Color, Ellipse
from kivy.metrics import dp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.widget import Widget

from ui import firstborn_flow as ff
from ui import theme


def _label(text, color="text_secondary", bold=False, size="15sp"):
    lbl = Label(text=text, color=theme.hex_to_rgba(theme.COLORS[color]),
                font_size=theme.font_sp(size), bold=bold, halign="center",
                valign="top", markup=False)
    lbl.bind(size=lambda w, s: setattr(w, "text_size", (s[0], None)))
    return lbl


class _JoyBurst(Widget):
    """Green rings blooming outward — the 'circles of joy'. Pure canvas, so it
    costs nothing when it isn't celebrating."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self._anims = []

    def celebrate(self):
        self._ring(0.0)
        Clock.schedule_once(lambda dt: self._ring(0.0), 0.5)
        Clock.schedule_once(lambda dt: self._ring(0.0), 1.0)

    def _ring(self, _):
        cx, cy = self.center
        with self.canvas:
            col = Color(*theme.hex_to_rgba(theme.COLORS["green"]))
            col.a = 0.9
            e = Ellipse(pos=(cx, cy), size=(dp(8), dp(8)))
        r = dp(160)
        anim = Animation(size=(r, r),
                         pos=(cx - r / 2, cy - r / 2), duration=1.4,
                         t="out_quad")
        anim &= Animation(a=0.0, duration=1.4)  # fade the Color
        anim.start(col)
        Animation(size=(r, r), pos=(cx - r / 2, cy - r / 2),
                  duration=1.4, t="out_quad").start(e)


class FirstbornScreen(BoxLayout):
    """The ceremony. Injected seams (ports_fn / gps_check / setup_factory) keep
    the logic path testable and let a bench run it with real hardware."""

    def __init__(self, on_home=None, ports_fn=None, gps_check=None,
                 setup_factory=None, **kw):
        super().__init__(orientation="vertical", padding=dp(20),
                         spacing=dp(10), **kw)
        self._on_home = on_home
        self._ports_fn = ports_fn or _default_ports
        self._gps_check = gps_check or _medic_has_gps
        self._setup_factory = setup_factory or _default_setup_factory
        self._running = False
        self._result = None            # None -> unknown, True/False -> outcome
        self._failure = ""
        self._progress = ""
        self._gps_live = False
        self._poll = None
        self._last_stage = None

    # -- lifecycle -----------------------------------------------------------
    def begin_screen(self):
        """Called on entry. Kick a one-off GPS check (a medic that already has a
        Tracker should not be pushed to birth another), then poll the plug
        state so NEED_TRACKER↔READY tracks the cable."""
        self._result = None
        self._running = False
        self._last_stage = None
        threading.Thread(target=self._check_gps_once, daemon=True).start()
        if self._poll is None:
            self._poll = Clock.schedule_interval(self._tick, 1.5)
        self._render(force=True)

    def sleep(self):
        if self._poll is not None:
            self._poll.cancel()
            self._poll = None

    def _check_gps_once(self):
        try:
            live = bool(self._gps_check())
        except Exception:              # noqa: BLE001 — a probe error means "no GPS"
            live = False
        Clock.schedule_once(lambda dt: self._set_gps_live(live), 0)

    def _set_gps_live(self, live):
        self._gps_live = live
        self._render()

    def _tick(self, _dt):
        if self._running or self._result is not None:
            return
        self._render()

    # -- view ----------------------------------------------------------------
    def _view(self) -> ff.FirstbornView:
        try:
            candidates = len(list(self._ports_fn()))
        except Exception:              # noqa: BLE001
            candidates = 0
        return ff.decide(gps_live=self._gps_live,
                         tracker_candidates=candidates,
                         running=self._running,
                         result=self._result,
                         failure=self._failure)

    def _render(self, force=False):
        view = self._view()
        if not force and view.stage == self._last_stage and not self._running:
            return
        self._last_stage = view.stage
        self.clear_widgets()

        self.add_widget(Widget(size_hint_y=None, height=dp(4)))
        title = Label(text=view.title, bold=True,
                      font_size=theme.font_sp("23sp"),
                      color=theme.hex_to_rgba(theme.COLORS["text_primary"]),
                      size_hint_y=None, height=dp(40), halign="center")
        title.bind(size=lambda w, s: setattr(w, "text_size", (s[0], None)))
        self.add_widget(title)

        if view.stage == ff.DONE:
            burst = _JoyBurst(size_hint_y=None, height=dp(150))
            self.add_widget(burst)
            Clock.schedule_once(lambda dt: burst.celebrate(), 0.1)

        body = _label(view.body, size="15sp")
        self.add_widget(body)

        if self._running and self._progress:
            self.add_widget(_label(self._progress, color="text_primary",
                                   size="14sp"))

        self.add_widget(Widget())      # spring

        if view.can_begin:
            begin = Button(
                text=("Try again  →" if view.stage == ff.FAILED
                      else "Begin — birth the firstborn  →"),
                size_hint_y=None, height=dp(58), font_size=theme.font_sp("18sp"),
                background_normal="",
                background_color=theme.hex_to_rgba(theme.COLORS["green"]),
                color=theme.hex_to_rgba(theme.COLORS["background"]))
            begin.bind(on_release=lambda *_: self._begin())
            self.add_widget(begin)

        if view.stage == ff.DONE and self._on_home:
            done = Button(text="Wonderful — carry on  →", size_hint_y=None,
                          height=dp(50), font_size=theme.font_sp("16sp"),
                          background_normal="",
                          background_color=theme.hex_to_rgba(
                              theme.COLORS["surface"]),
                          color=theme.hex_to_rgba(theme.COLORS["text_primary"]))
            done.bind(on_release=lambda *_: self._on_home())
            self.add_widget(done)

    # -- the birth -----------------------------------------------------------
    def _begin(self):
        if self._running:
            return
        self._running = True
        self._result = None
        self._failure = ""
        self._progress = "Starting…"
        self._render(force=True)
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        def on_progress(result):
            msg = getattr(result, "message", str(result))
            Clock.schedule_once(lambda dt: self._show_progress(msg), 0)
        ok = False
        failure = ""
        try:
            workflow = self._setup_factory()
            # GpsTrackerSetup.run_all returns the LIST of StepResults (it stops
            # at the first failure). Success = a non-empty run whose every step
            # passed — a non-empty list is NOT itself a win.
            results = workflow.run_all(on_progress=on_progress) or []
            ok = ff.succeeded(results)
            if not ok:
                failure = ff.first_failure(results)
        except Exception as exc:       # noqa: BLE001
            ok = False
            failure = f"The birth hit a snag: {exc}"
        Clock.schedule_once(lambda dt: self._finish(ok, failure), 0)

    def _show_progress(self, msg):
        self._progress = msg
        self._render(force=True)

    def _finish(self, ok, failure):
        self._running = False
        self._result = ok
        self._failure = failure
        self._gps_live = ok or self._gps_live
        self._render(force=True)


# -- default (real-hardware) seams ------------------------------------------

def _default_ports():
    from ui.hw_factories import local_board_ports
    return local_board_ports()


def _medic_has_gps() -> bool:
    """Best-effort: does the medic already have a live GPS fix? Any failure
    (no gpsd, no Tracker, import error) means 'no', so the ceremony offers
    itself rather than assuming a fix exists."""
    try:
        from monitor.geo import read_splitter_fix
        return read_splitter_fix() is not None
    except Exception:                  # noqa: BLE001
        return False


def _default_setup_factory():
    from transport.connection import LocalConnection
    from workflows.gps_setup import GpsTrackerSetup
    return GpsTrackerSetup(LocalConnection())
