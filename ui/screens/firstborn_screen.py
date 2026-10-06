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

import os

from kivy.animation import Animation
from kivy.clock import Clock
from kivy.graphics import Color, Ellipse
from kivy.metrics import dp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.image import Image as UIImage
from kivy.uix.label import Label
from kivy.uix.widget import Widget

from ui import firstborn_flow as ff
from ui import theme
from ui.text_fit import grow_to_text
from ui.i18n import tr  # i18n: wrapped — firstborn ceremony buttons/plate/progress

#: The board the firstborn always is — show it, don't just name it
#: ([[show-dont-tell-ux]]).
_TRACKER_BOARD = "heltec_wireless_tracker"


def _label(text, color="text_secondary", bold=False, size="15sp"):
    lbl = Label(text=text, color=theme.hex_to_rgba(theme.COLORS[color]),
                font_size=theme.font_sp(size), bold=bold, halign="center",
                valign="top", markup=False)
    lbl.bind(size=lambda w, s: setattr(w, "text_size", (s[0], None)))
    return lbl


def _tracker_image(height=140):
    """The Heltec Tracker's picture, or None if the asset is missing (best-effort
    — a missing image must never break the ceremony)."""
    try:
        from ui.board_images import image_for
        path = image_for(_TRACKER_BOARD)
        if not path or not os.path.exists(path):
            return None
        return UIImage(source=path, size_hint_y=None, height=dp(height),
                       allow_stretch=True, keep_ratio=True)
    except Exception:                  # noqa: BLE001
        return None


class _JoyBurst(Widget):
    """Green rings blooming outward — the 'circles of joy'. Five staggered rings
    over ~3s make it a celebration, not a blink. Pure canvas, so it costs
    nothing when idle; the max radius is bounded to the widget's smaller side so
    the rings never overdraw the labels around them (geometry PIL-previewed,
    [[preview-animations-offline]])."""

    def __init__(self, **kw):
        super().__init__(**kw)

    def celebrate(self):
        for i in range(5):
            Clock.schedule_once(lambda dt: self._ring(), i * 0.4)

    def _ring(self):
        cx, cy = self.center
        r = max(dp(30), min(self.width, self.height) * 0.9)   # diameter, bounded
        with self.canvas:
            col = Color(*theme.hex_to_rgba(theme.COLORS["green"]))
            col.a = 0.85
            e = Ellipse(pos=(cx - dp(4), cy - dp(4)), size=(dp(8), dp(8)))
        grow = Animation(size=(r, r), pos=(cx - r / 2, cy - r / 2),
                         duration=1.4, t="out_quad")
        grow.start(e)
        fade = Animation(a=0.0, duration=1.4)
        fade.start(col)


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
        self._proof = ""               # first real fix, shown as celebration proof
        self._gps_live = False
        self._poll = None
        self._last_stage = None

    # -- lifecycle -----------------------------------------------------------
    def begin_screen(self):
        """Called on entry. Kick a one-off GPS check (a medic that already has a
        Tracker should not be pushed to birth another), then poll the plug
        state so NEED_TRACKER↔READY tracks the cable.

        A birth already in flight (the keeper navigated away mid-flash and back)
        is left ALONE — resetting _running/_result here would orphan the worker
        and let a second _begin launch a concurrent flash (adversarial review
        2026-08-25)."""
        from workflows import medic_radio as _mr
        if not self._running and _mr.check_pending():
            # back from the hand-over restart: check the radio and GPS now
            self._begin(check_only=True)
        elif not self._running:
            self._result = None
            self._last_stage = None
            threading.Thread(target=self._check_gps_once, daemon=True).start()
        if self._poll is None:
            self._poll = Clock.schedule_interval(self._tick, 1.5)
        self._render(force=True)

    def sleep(self):
        if self._poll is not None:
            self._poll.cancel()
            self._poll = None
        # leaving without finishing: the walkthrough must not jump ahead next
        # time it opens (a stale "come back here" marker)
        if not self._running and self._result is not True:
            try:
                from ui import setup_flow as _sf
                _sf.take_resume()
            except Exception:                              # noqa: BLE001
                pass

    def handle_back(self):
        """Back/Home are refused while the set-up runs: a flash or the hand-over
        restart must not be left running behind another screen (clicker
        review, 2026-10-06)."""
        if self._running:
            from ui.confirm import confirm_leave
            confirm_leave(tr("Not yet"), tr("The Heltec Wireless Tracker is being "
                                            "set up. Wait for it to finish."))
            return True
        return False

    handle_home = handle_back

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
                         failure=self._failure,
                         checking=getattr(self, "_check_only", False),
                         owns_radio=_medic_owns_radio(),
                         after_check=getattr(self, "_check_only", False),
                         no_image=_tracker_image_missing())

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
        # grows: "Ready to set up the Heltec Wireless Tracker" is two lines
        grow_to_text(title)
        self.add_widget(title)

        # Show the board on the stages that ask the keeper to handle it, and in
        # the celebration — a picture, not just the name.
        if view.stage in (ff.NEED_TRACKER, ff.READY, ff.DONE):
            img = _tracker_image(150 if view.stage == ff.DONE else 130)
            if img is not None:
                self.add_widget(img)

        if view.stage == ff.DONE:
            plate = Label(text=tr("★  this medic's radio and position finder  ★"), bold=True,
                          font_size=theme.font_sp("16sp"),
                          color=theme.hex_to_rgba(theme.COLORS["green"]),
                          size_hint_y=None, height=dp(26))
            self.add_widget(plate)
            burst = _JoyBurst(size_hint_y=None, height=dp(120))
            self.add_widget(burst)
            Clock.schedule_once(lambda dt: burst.celebrate(), 0.1)
            if self._proof:
                self.add_widget(_label(self._proof, color="text_primary",
                                       size="14sp"))

        body = _label(view.body, size="15sp")
        self.add_widget(body)

        if self._running and self._progress:
            import time as _t
            m, sec = divmod(int(_t.monotonic() - getattr(self, "_t0", _t.monotonic())), 60)
            self.add_widget(_label(f"{self._progress}   {m}m {sec:02d}s",
                                   color="text_primary", size="14sp"))
            pulse = _label(tr("●  working — you don't need to press anything"),
                           color="accent", size="13sp")
            self.add_widget(pulse)
            from kivy.animation import Animation
            anim = Animation(opacity=0.25, duration=0.8) + Animation(opacity=1, duration=0.8)
            anim.repeat = True
            anim.start(pulse)

        self.add_widget(Widget())      # spring

        # A keeper with no Tracker (or one that already has GPS) needs an
        # obvious way onward from the screen itself, not just the back-swipe.
        if view.stage == ff.ALREADY and self._on_home:
            on = Button(text=tr("Carry on  →"), size_hint_y=None,
                        height=dp(52), font_size=theme.font_sp("17sp"), bold=True,
                        background_normal="",
                        background_color=theme.hex_to_rgba(theme.COLORS["green"]),
                        color=theme.hex_to_rgba(theme.COLORS["background"]))
            on.bind(on_release=lambda *_: self._on_home())
            self.add_widget(on)
        if view.stage == ff.NEED_TRACKER and self._on_home:
            # the quiet road: small text, never the prominent button (keeper)
            skip = Button(text=tr("Do this later"), size_hint_y=None,
                          height=dp(40), font_size=theme.font_sp("14sp"),
                          background_normal="", opacity=0.75,
                          background_color=theme.hex_to_rgba(
                              theme.COLORS["surface"]),
                          color=theme.hex_to_rgba(theme.COLORS["text_secondary"]))
            skip.bind(on_release=lambda *_: self._on_home())
            self.add_widget(skip)
        if view.stage == ff.FAILED and getattr(view, "after_check", False):
            # the check after the restart failed: never a trap (a pending check
            # used to send every power-up back here with Try again only)
            from workflows import medic_radio as _mr
            again = Button(text=tr("Set it up again from the start"),
                           size_hint_y=None, height=dp(46),
                           font_size=theme.font_sp("15sp"), background_normal="",
                           background_color=theme.hex_to_rgba(theme.COLORS["surface"]),
                           color=theme.hex_to_rgba(theme.COLORS["text_primary"]))

            def _again(*_):
                _mr.clear_check_pending()
                _mr.unlock_own_radio()
                self._check_only = False
                self._result = None
                self._render(force=True)
            again.bind(on_release=_again)
            self.add_widget(again)
            later = Button(text=tr("Not now"), size_hint_y=None, height=dp(40),
                           font_size=theme.font_sp("14sp"), background_normal="",
                           opacity=0.75,
                           background_color=theme.hex_to_rgba(theme.COLORS["surface"]),
                           color=theme.hex_to_rgba(theme.COLORS["text_secondary"]))

            def _later(*_):
                _mr.clear_check_pending()
                if self._on_home:
                    self._on_home()
            later.bind(on_release=_later)
            self.add_widget(later)

        if view.can_begin:
            begin = Button(
                text=(tr("Try again  →") if view.stage == ff.FAILED
                      else tr("Begin — set up the Heltec Wireless Tracker  →")),
                size_hint_y=None, height=dp(58), font_size=theme.font_sp("18sp"),
                background_normal="",
                background_color=theme.hex_to_rgba(theme.COLORS["green"]),
                color=theme.hex_to_rgba(theme.COLORS["background"]))
            begin.bind(on_release=lambda *_: self._begin())
            # a yellow border: the "go" action stands out (keeper, 2026-10-06)
            from kivy.graphics import Color, Line
            with begin.canvas.after:
                Color(*theme.hex_to_rgba("#ffd600"))
                _border = Line(rounded_rectangle=(0, 0, 1, 1, dp(8)), width=dp(2))

            def _redraw(i, *_a, _b=_border):
                _b.rounded_rectangle = (i.x + dp(1), i.y + dp(1), i.width - dp(2),
                                        i.height - dp(2), dp(8))
            begin.bind(pos=_redraw, size=_redraw)
            # the full board name wraps instead of running off both edges
            begin.halign = "center"
            begin.bind(width=lambda i, w: setattr(i, "text_size", (w - dp(20), None)))
            begin.bind(texture_size=lambda i, ts: setattr(
                i, "height", max(dp(58), ts[1] + dp(16))))
            self.add_widget(begin)

        if view.stage == ff.DONE and self._on_home:
            from ui import setup_flow as _sf
            done = Button(text=(tr("Continue the walkthrough  →") if _sf.peek_resume()
                                else tr("Wonderful — carry on  →")), size_hint_y=None,
                          height=dp(50), font_size=theme.font_sp("16sp"),
                          background_normal="",
                          background_color=theme.hex_to_rgba(
                              theme.COLORS["surface"]),
                          color=theme.hex_to_rgba(theme.COLORS["text_primary"]))
            done.bind(on_release=lambda *_: self._on_home())
            self.add_widget(done)

    # -- the birth -----------------------------------------------------------
    def _begin(self, check_only=False):
        if self._running:
            return
        from workflows import medic_radio as _mr
        # after the hand-over the board IS this medic's radio (and guarded):
        # Try again re-runs the check, never a second flash
        check_only = check_only or _mr.check_pending()
        self._running = True
        self._result = None
        self._failure = ""
        self._check_only = check_only
        self._progress = (tr("Checking the radio and position finder…") if check_only
                          else tr("Starting…"))
        import time as _t
        self._t0 = _t.monotonic()
        try:
            from kivy.app import App
            App.get_running_app().begin_activity(
                tr("Setting up the radio — keep the Tracker plugged in"))
        except Exception:                                  # noqa: BLE001
            pass
        self._render(force=True)
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        def on_progress(result):
            msg = getattr(result, "message", str(result))
            Clock.schedule_once(lambda dt: self._show_progress(msg), 0)
        ok = False
        failure = ""
        try:
            if getattr(self, "_check_only", False):
                from transport.connection import LocalConnection
                from workflows.medic_radio import MedicRadioCheck
                workflow = MedicRadioCheck(LocalConnection())
            else:
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
            failure = tr("Setting up the Heltec Wireless Tracker hit a snag: {err}").format(err=exc)
        Clock.schedule_once(lambda dt: self._finish(ok, failure), 0)

    def _show_progress(self, msg):
        self._progress = msg
        self._render(force=True)

    def _finish(self, ok, failure):
        from workflows import medic_radio as _mr
        try:
            from kivy.app import App
            App.get_running_app().end_activity()
        except Exception:                                  # noqa: BLE001
            pass
        if getattr(self, "_check_only", False):
            if ok:
                _mr.clear_check_pending()      # proven: never re-check at boot
        elif ok and _mr.check_pending():
            # the hand-over restart is seconds away: say so, keep the spinner
            self._progress = tr("The screen goes dark for about half a minute "
                                "while Node Medic restarts with its new radio. "
                                "Unplug nothing — this page comes back by itself.")
            self._render(force=True)
            return
        self._running = False
        self._result = ok
        self._failure = failure
        self._gps_live = ok or self._gps_live
        self._proof = _fix_proof() if ok else ""
        self._render(force=True)


# -- default (real-hardware) seams ------------------------------------------

def _default_ports():
    from ui.hw_factories import local_board_ports
    return local_board_ports()


def _fix_proof() -> str:
    """The Tracker's first real fix, shown as honest proof in the celebration —
    the medic states what it actually saw, not a claim. Empty if no fix is
    readable yet (gpsd may still be acquiring), which is fine: the birth still
    succeeded, we just don't fake coordinates."""
    try:
        from monitor.geo import read_splitter_fix
        fix = read_splitter_fix()
        if fix is None:
            return ""
        lat = getattr(fix, "lat", None)
        lon = getattr(fix, "lon", None)
        sats = getattr(fix, "sats", None)
        if lat is None or lon is None:
            return ""
        tail = tr(" · {n} satellites").format(n=sats) if sats else ""
        return tr("First fix: {lat}, {lon}{tail}").format(
            lat=f"{lat:.4f}", lon=f"{lon:.4f}", tail=tail)
    except Exception:                  # noqa: BLE001
        return ""


def _medic_has_gps() -> bool:
    """Best-effort: does the medic already have a live GPS fix? Any failure
    (no gpsd, no Tracker, import error) means 'no', so the ceremony offers
    itself rather than assuming a fix exists."""
    try:
        from monitor.geo import read_splitter_fix
        return read_splitter_fix() is not None
    except Exception:                  # noqa: BLE001
        return False


def _tracker_image_missing() -> bool:
    """True when this medic has no Tracker radio-software image to write
    (a clone from a GitHub-built parent). Checked once; it cannot change
    while the page is open."""
    global _IMG_MISSING
    if _IMG_MISSING is None:
        try:
            import os
            from workflows.rnode_boards import get_board
            from workflows.rnode_flash import fork_image_for
            _IMG_MISSING = not os.path.isfile(os.path.expanduser(
                fork_image_for(get_board("heltec_wireless_tracker"), "bin")))
        except Exception:                  # noqa: BLE001
            _IMG_MISSING = False
    return _IMG_MISSING


_IMG_MISSING = None


def _medic_owns_radio() -> bool:
    """Does this medic already have its own radio? Its roster names one, or
    its radio service is bound to one — no satellite fix needed to know that."""
    try:
        from ui.onboard_roster import onboard_serials, service_bound_serials
        return bool(onboard_serials()) or bool(service_bound_serials())
    except Exception:                  # noqa: BLE001
        return False


def _default_setup_factory():
    """The medic's OWN radio and GPS, on one Heltec Wireless Tracker — the same
    firmware and wiring as the original medic's Jonesey (workflows.medic_radio).
    The old GPS-only sketch left a clone with no LoRa radio at all."""
    from transport.connection import LocalConnection
    from workflows.medic_radio import MedicRadioSetup
    from workflows.rnode_boards import get_board
    from ui.hw_factories import make_rnode_flash, local_board_ports
    conn = LocalConnection()
    board = get_board("heltec_wireless_tracker")

    def flash():
        return make_rnode_flash(board, lambda _b: None, connection=conn,
                                ports_fn=local_board_ports)
    return MedicRadioSetup(conn, flash)
