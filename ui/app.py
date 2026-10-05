"""Application shell — sidebar navigation + screen manager.

Wires the six operating modes to screens, in sidebar order:
1 VITALS (monitor dashboard) · 2 SCAN (topology + map) · 3 BIRTH (provision)
· 4 TRIAGE (site assessment) · 5 PROBE (diagnose + repair) · 6 MITOSIS (clone).
Back/Home nav and the safety panel live at this level so every screen inherits
them.
"""

from __future__ import annotations

import os
import subprocess
import threading

from kivy.app import App
from kivy.clock import Clock
from kivy.core.window import Window
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.label import Label
from kivy.uix.screenmanager import Screen, ScreenManager
from kivy.uix.widget import Widget
from kivy.metrics import dp

from ui import theme
from ui.screens.vitals_screen import VitalsScreen
from ui.screens.scan_screen import ScanScreen
from ui.screens.probe_screen import ProbeScreen
from ui.screens.birth_screen import BirthScreen
from ui.screens.triage_screen import TriageScreen
from ui.screens.firstborn_screen import FirstbornScreen
from ui.screens.mitosis_screen import MitosisScreen
from ui.screens.home_screen import HomeScreen
from ui.screens.credits_screen import CreditsScreen
from ui.onscreen_keyboard import OnScreenKeyboard
from node_profile import NodeProfile
from transport.connection import EmulatedConnection
from workflows.repair import RepairWorkflow
from workflows.build import BuildWorkflow
from workflows.rtnode_build import RTNodeBuildWorkflow
from workflows.rnode_flash import RNodeFlashWorkflow
from monitor.service import MonitorService
# Pure, RNS-free delivery-honesty helpers (2026-08-21/22 lessons). Imported at
# module scope ON PURPOSE: the outcome constants are compared even when
# ``import RNS`` fails inside the ping worker (dev box), and a try-local import
# left them unbound there — the comparison raised, the thread died, and the
# detail screen said "Probing over the mesh…" forever.
from monitor.health_reply import (PendingPolls, announce_wait_s,
                                  build_fallback_request, build_request_to,
                                  load_or_create_identity,
                                  unicast_wait_s, uptime_is_fresh,
                                  verify_reply,
                                  REPLY_ANNOUNCE_EVERY_S,
                                  REPLY_ANNOUNCE_QUIET_AFTER_POLL_S,
                                  REPLY_APP, REPLY_ASPECTS)
from monitor.health_poll import (DELIVERY_ANSWERED, DELIVERY_NO_ROUTE,
                                 DELIVERY_UNANSWERED, build_request,
                                 heard_since, warm_and_send, warm_path)
# Time over the mesh (docs/HEALTH_REPLY_UNICAST.md, 2026-09-23): the medic's
# whole side lives in monitor.time_service; this file only wires it.
from monitor.medic_clock import disciplined as _clock_disciplined
from monitor.time_ledger import TimeLedger
from monitor.time_service import TimeService

# EVERY PRESSABLE THING GETS ROUNDED CORNERS, applied to the Button class once,
# here, before any screen is built (operator, 2026-09-28: "anywhere there's
# something to be pressed, let's make sure the corners are rounded so it looks
# like a button"). 196 call sites keep working untouched, and the 197th is
# rounded the day it is written — see ui/rounded.py for what it leaves alone.
from ui import rounded as _rounded

_rounded.enable()


def _local_run(command: str) -> str:
    """Run a shell command on the medic itself (LAN + mesh discovery). Login
    shell so ~/.local/bin (rnpath, rnstatus, curl) is on PATH."""
    try:
        return subprocess.run(["bash", "-lc", command], capture_output=True,
                              text=True, timeout=120).stdout
    except Exception:
        return ""


def _demo_repair_workflow():
    """A RepairWorkflow over an emulated node with a couple of injected faults,
    so the Diagnose screen is explorable without hardware."""
    conn = EmulatedConnection(default_code=0, default_stdout="ok")
    conn.rules.insert(0, ("^systemctl is-active rnsd", 3, "inactive", ""))
    conn.rules.insert(0, ("thermal_zone0/temp", 0, "82000", ""))
    conn.rule("^systemctl start rnsd", 0, "")
    return RepairWorkflow(conn, NodeProfile())


def _demo_rtnode_build():
    conn = EmulatedConnection(default_code=0, default_stdout="ok")
    conn.rules.insert(0, ("^ls /dev/cu", 0, "/dev/cu.usbmodem2101", ""))
    conn.rules.insert(0, ("pio run", 0, "SUCCESS", ""))
    conn.rules.insert(0, ("rnm-serial-capture", 0,
                          "[HealthBeacon] announce dst=11223344556677889900aabbccddeeff "
                          "data=010000002400c7cc053b3f000602", ""))
    return RTNodeBuildWorkflow(conn, NodeProfile())


def _demo_pi_build():
    conn = EmulatedConnection(default_code=0, default_stdout="ok")
    conn.rules.insert(0, ("/proc/cpuinfo", 0, "Model : Raspberry Pi 5 Model B", ""))
    conn.rules.insert(0, ("--info", 0, "[Device] RNode\nFirmware version: 1.80", ""))
    # The build now reads its own writes back and asks systemd what is really
    # running — a demo node answers like a healthy one.
    conn.rules.insert(0, ("systemctl is-active", 0, "active", ""))
    conn.rules.insert(0, ("systemctl is-enabled", 0, "enabled", ""))
    conn.rules.insert(0, ("cat ~/.reticulum/config", 0, "[reticulum]\n", ""))
    conn.rules.insert(0, ("cat /etc/udev/rules.d/60-rnode.rules", 0,
                          'ATTRS{idVendor}=="303a", SYMLINK+="rnode"', ""))
    return BuildWorkflow(conn, NodeProfile())


def _pi_rnode_factory(address: str = "", user: str = "pi", node_name: str = ""):
    """Pi propagation birth — REAL (2026-07-30): SSH to the target Pi and run
    the full BuildWorkflow (config, RNS/LXMF stack, services, the health
    reporter, hardening, hostname, certificate). Key-auth only: the Pi must be
    reachable with the medic's SSH key (the SD-imaging step bakes it in; or
    install it by hand). No address -> honest guidance, never a fake run."""
    from ui.hw_factories import demo_allowed, _HonestFailWorkflow
    address = (address or "").strip()
    user = (user or "pi").strip() or "pi"
    if not address:
        if demo_allowed():
            return _demo_pi_build()
        return _HonestFailWorkflow(
            "detect_hardware",
            "Enter the Pi's network address first (e.g. raspberrypi.local or "
            "192.168.1.50).\n\nThe Pi must be powered, on your WiFi, with SSH "
            "enabled and the medic's key authorised — the SD-imaging step sets "
            "all of that up, or add the key by hand to ~/.ssh/authorized_keys "
            "on the Pi.",
            "Pi address needed")
    from transport.connection import SSHConnection
    from node_profile import NodeRole
    conn = SSHConnection(address, user=user)
    profile = NodeProfile(role=NodeRole.PROPAGATION, ssh_user=user)
    from provisioning.pi_imager import hostnameify
    hn = hostnameify(node_name)
    if hn:
        profile.hostname = hn
    return BuildWorkflow(conn, profile)


def _mitosis_factory(hostname: str = ""):
    """Clone THIS medic onto a fresh Pi — the REAL flow (wired 2026-08-25).

    The workflow's own first step discovers the new medic (<hostname>.local
    over WiFi/mDNS, then the baked static /29 over the patch cable), pins its
    host key on first contact and logs in with this medic's own key — the one
    the MITOSIS card writer put on the card. Demo mode still gets the paced
    emulated ladder."""
    from ui.hw_factories import demo_allowed
    if demo_allowed():
        return _demo_clone_workflow()
    from workflows.clone import make_discovering_workflow
    from provisioning.pi_imager import hostnameify
    from monitor.registry import NodeRegistry
    try:
        from kivy.app import App
        _app = App.get_running_app()
        reg = _app.monitor_service.registry if _app else NodeRegistry()
    except Exception:                                          # noqa: BLE001
        reg = NodeRegistry()
    return make_discovering_workflow(reg, hostname=hostnameify(hostname),
                                     username="pi")


def _demo_rnode_flash(board):
    """Explorable RNode flash over an emulated board (no hardware needed).
    On a real medic this factory would target the locally attached board."""
    conn = EmulatedConnection(default_code=0, default_stdout="ok")
    conn.rules.insert(0, ("curl -fsI", 7, "", ""))                 # offline
    conn.rules.insert(0, ("ls /dev/ttyACM", 0, "/dev/ttyACM0", ""))
    conn.rules.insert(0, ("ls ~/.config/rnodeconf/update/1.86/*.zip", 0, "fw.zip", ""))
    conn.rules.insert(0, ("--autoinstall", 0,
                          "RNode Firmware autoinstallation complete!", ""))
    conn.rules.insert(0, ("--info", 0,
                          "Device signature   : Validated\nFirmware version   : 1.86", ""))
    return RNodeFlashWorkflow(conn, board, port="/dev/ttyACM0")

DEMO_NODES = [
    {"name": "Wrenhill Hill", "location": "Wrenhill", "status": "ok",
     "battery_pct": 82, "signal_dbm": -78, "last_seen_hours": 0.2,
     "powered_by": "solar", "type": "pi"},
    {"name": "Ironbark Water Tower", "location": "Ironbark", "status": "warn",
     "battery_pct": 18, "signal_dbm": -112, "last_seen_hours": 1.5,
     "powered_by": "battery", "type": "pi"},
    {"name": "CBD Rooftop RTNode", "location": "Sampleton CBD", "status": "alert",
     "signal_dbm": -121, "last_seen_hours": 7.0, "type": "rtnode2400"},
]


def _demo_clone_workflow():
    """A CloneWorkflow over an emulated target Pi 5 so the Clone screen is
    explorable without a second Pi. On a real medic this factory would open an
    SSH connection to the fresh Pi."""
    import time
    from workflows.clone import CloneWorkflow
    from monitor.registry import NodeRegistry

    conn = EmulatedConnection(default_code=0, default_stdout="ok")
    conn.rules.insert(0, ("/proc/cpuinfo", 0, "Model : Raspberry Pi 5 Model B", ""))
    conn.rules.insert(0, ("id -un", 0, "nodemedic", ""))
    conn.rules.insert(0, ("rnid --generate", 0,
                          "New identity <2233445566778899aabbccddeeff0011> written", ""))
    wf = CloneWorkflow(conn, NodeRegistry())
    # pace the emulated steps so the streaming UI is visible in the demo
    real_run_all = wf.run_all

    def paced(on_progress=None):
        emit = on_progress or (lambda r: None)

        def spaced(r):
            time.sleep(0.6)
            emit(r)
        return real_run_all(on_progress=spaced)
    wf.run_all = paced
    return wf


def _triage_feed():
    """Live splitter feed when the medic's radio state file exists (real
    RSSI/SNR/noise recorded by monitor.serial_splitter), else the demo feed.
    RNM_TRIAGE=demo|live overrides the choice."""
    from monitor.triage_feed import live_triage_feed, feed_choice
    from monitor.geo import read_splitter_state
    from ui.hw_factories import demo_allowed
    mode = os.environ.get("RNM_TRIAGE", "")
    # The rule lives in feed_choice (tested): on the medic, LIVE whatever the
    # state file says — the live feed's None is the honest NOT READING.
    if feed_choice(mode, demo_allowed(), read_splitter_state() is not None) == "demo":
        return _demo_triage_feed()
    return live_triage_feed()


def _demo_triage_feed():
    """A wandering signal (good -> bad -> good) so the Triage bullseye is
    explorable without hardware. Replaced by the live splitter feed on the medic."""
    import math
    import random
    state = {"t": 0.0}

    def reader():
        state["t"] += 1.0
        phase = state["t"] * 0.12
        return {
            "snr": 3.0 + 6.0 * math.sin(phase) + random.uniform(-1.5, 1.5),
            "rssi": -95.0 + 15.0 * math.sin(phase) + random.uniform(-5.0, 5.0),
            "noise": -108.0 + random.uniform(-3.0, 3.0),
            "peers": 2 + int(state["t"] // 20) % 3,
        }
    return reader


def _placeholder(title):
    screen = BoxLayout()
    screen.add_widget(Label(
        text=f"{title}\n(coming soon)", halign="center",
        color=theme.hex_to_rgba(theme.COLORS["text_secondary"])))
    return screen


def _glyph_font_name():
    """The face the bar's '←' and '⌂' are drawn in, or None to use the default.

    Both glyphs are in DejaVuSans (checked against the bundled file with PIL,
    2026-09-22); the app's global font is DejaVu for every language but
    Japanese, where it is Noto Sans JP, whose coverage of U+2302 HOUSE was not
    checked. Naming the face keeps the two glyphs the same in every language;
    the word 'Home' next to them stays in the language's own font. None when
    Kivy's data dir cannot be found (the test suite stubs the package), so a
    missing path degrades to the default font instead of a dead bar."""
    try:
        from ui import fonts
        path = fonts.dejavu_path()
        return path if os.path.exists(path) else None
    except Exception:                                              # noqa: BLE001
        return None


class _NavBar(BoxLayout):
    """The sliver at the foot of every mode screen: '←' far left, 'Home' centre.

    WHY (operator, 2026-09-22, after walking a stranger through the medic from
    scratch): "we need a dedicated back and home button — a tiny little sliver
    at the bottom of every screen". The left-edge swipe stays, but a gesture
    nobody is told about is not a way out (the lesson the birth guide paid
    for on 2026-08-06/07 when its swipe-only Back read as a trap, twice).

    A LAYOUT ROW, not an overlay: many screens end in a button on the bottom
    edge (node_detail's Delete, the walk's Stop & save) and a strip painted
    over them would cover exactly the control the operator is reaching for.
    Both controls are at least dp(44) WIDE although the bar is dp(28) tall —
    the generous hit area is horizontal, because a taller invisible target
    would take touches from those same bottom-edge buttons.
    """

    # dp(28) on the first night; "works, but could be a fraction bigger"
    # (operator, on the glass, 2026-09-23) — dp(34), fonts up one step.
    HEIGHT_DP = 34
    BACK_W_DP = 64        # the arrow's touch target; the bar is too short to be tall
    HOME_W_DP = 132

    def __init__(self, on_back, on_home, **kwargs):
        kwargs.setdefault("orientation", "horizontal")
        kwargs.setdefault("size_hint_y", None)
        kwargs.setdefault("height", dp(self.HEIGHT_DP))
        super().__init__(**kwargs)
        from ui.i18n import tr
        from kivy.graphics import Color, Rectangle
        with self.canvas.before:
            Color(*theme.hex_to_rgba(theme.COLORS["sidebar"]))
            self._bg = Rectangle(pos=self.pos, size=self.size)
        self.bind(pos=self._sync_bg, size=self._sync_bg)
        glyph_font = _glyph_font_name()
        flat = dict(background_normal="", background_down="",
                    background_color=(0, 0, 0, 0), size_hint=(None, 1),
                    color=theme.hex_to_rgba(theme.COLORS["accent"]))
        self.back_button = Button(text="←", font_size="22sp",
                                  width=dp(self.BACK_W_DP), **flat)
        if glyph_font:
            self.back_button.font_name = glyph_font
        self.back_button.bind(on_release=lambda *_: on_back())
        # The house glyph is set in its own face by markup so the WORD keeps the
        # language's font (Japanese would otherwise lose its own characters).
        house = f"[font={glyph_font}]⌂[/font]  " if glyph_font else ""
        self.home_button = Button(text=house + tr("Home"), markup=True,
                                  font_size="15.5sp", bold=True,
                                  width=dp(self.HOME_W_DP), **flat)
        self.home_button.bind(on_release=lambda *_: on_home())
        self.add_widget(self.back_button)
        self.add_widget(Widget(size_hint=(1, 1)))              # spacer
        self.add_widget(self.home_button)
        self.add_widget(Widget(size_hint=(1, 1)))              # spacer
        # a blank the arrow's width, so the two spacers are equal and Home sits
        # on the bar's centre line rather than a half-arrow to the right of it
        self.add_widget(Widget(size_hint=(None, 1), width=dp(self.BACK_W_DP)))

    def _sync_bg(self, *_):
        self._bg.pos, self._bg.size = self.pos, self.size


class _BackSwipeWrap(FloatLayout):
    """Wraps a mode screen: the content above, the _NavBar sliver below, and a
    swipe IN from the LEFT EDGE that goes back — the same action as the bar's
    arrow. A thin translucent chevron marks the swipe zone. Only touches that
    START within the narrow edge strip (and above the bar) are claimed for the
    gesture; everything else passes straight through, so map panning, buttons
    and text fields all still work (a pan starts mid-screen, not at the border).
    """

    EDGE_DP = 26           # width of the left-edge back zone
    TRIGGER_DP = 55        # rightward travel that fires 'back'

    def __init__(self, on_back, on_home, **kwargs):
        super().__init__(**kwargs)
        self._on_back = on_back
        self._edge = None                       # (touch, start_x) mid back-swipe
        # '‹' (U+2039) renders in the default font (unlike the ⚠ emoji); a faint
        # handle telling the operator where the back gesture lives.
        self._chevron = Label(text="‹", font_size="40sp", bold=True,
                              size_hint=(None, None), size=(dp(22), dp(64)),
                              pos_hint={"x": 0.0, "center_y": 0.5},
                              color=theme.hex_to_rgba(theme.COLORS["text_secondary"], 0.55))
        self._bar = _NavBar(on_back=on_back, on_home=on_home)
        self._column = BoxLayout(orientation="vertical", size_hint=(1, 1))

    def add_content(self, widget):
        widget.size_hint = (1, 1)
        self._column.add_widget(widget)
        self._column.add_widget(self._bar)      # vertical box: last added = bottom row
        self.add_widget(self._column)
        self.add_widget(self._chevron)          # keep the handle on top

    def on_touch_down(self, touch):
        if touch.y - self.y < self._bar.height:
            # The arrow sits inside the far-left strip the gesture claims, and
            # a claimed touch never reaches a button — the bar is exempt.
            return super().on_touch_down(touch)
        if touch.x - self.x <= dp(self.EDGE_DP):
            self._edge = (touch, touch.x)
            return True                         # claim the edge strip
        return super().on_touch_down(touch)

    def on_touch_move(self, touch):
        if self._edge and touch is self._edge[0]:
            if touch.x - self._edge[1] >= dp(self.TRIGGER_DP):
                self._edge = None
                self._on_back()
            return True
        return super().on_touch_move(touch)

    def on_touch_up(self, touch):
        if self._edge and touch is self._edge[0]:
            self._edge = None
            return True
        return super().on_touch_up(touch)


NO_FIX_NOTE = ("No GPS fix and no placed nodes yet — type an address, or tap the map "
               "to put the pin where the node is.")


class ReticulumNodeMedicApp(App):
    title = "Reticulum Node Medic"

    _comms_back = "home"

    def _open_comms_from_chat(self):
        """Chat's 'Put Columba or Sideband on a phone' button: Back from the
        apps screen must land on Chat, not Home (readiness ledger #83)."""
        self._comms_back = "chat"
        self.switch_mode("comms")

    def _comms_back_target(self):
        back, self._comms_back = self._comms_back, "home"     # one-shot
        return back

    def _with_back(self, widget, back_to="home"):
        """A mode screen with the bottom sliver ('←' left, 'Home' centre) and the
        LEFT-EDGE SWIPE. Arrow and swipe are ONE action: for a MULTI-PAGE flow
        it steps back ONE page first — if the wrapped screen has
        ``handle_back()`` and it returns True (it stepped back internally), we
        stop there; only at the flow's root (or a plain single-page screen) does
        it fall through to home. Home is a plain switch for a plain screen:
        leaving never interrupts a flash (the activity counter and the busy
        marker keep the work alive), so there is nothing to confirm. A screen
        that defines ``handle_home()`` owns its own leaving — the birth guide's
        Exit cancels its late-resume hook and kills its polls before going,
        and warns mid-build (2026-08-14); a bare switch would leave those
        armed, and a card write landing late would pull the operator back
        into a walkthrough they had left (found in review, 2026-09-22).
        SCAN owns its leaving the same way since 2026-09-23: with a boundary
        walk live (or its GPS gate up) both controls used to switch straight
        home and the walk kept pinging off-glass with no way back — the
        operator lost a walk that way; now ScanScreen.handle_back/handle_home
        ask first (see ScanScreen.handle_back for the fact as checked)."""
        def on_back():
            h = getattr(widget, "handle_back", None)
            if callable(h):
                try:
                    if h():
                        return              # the screen stepped back a page
                except Exception:
                    pass
            # WHERE THE ARROW GOES. It used to be home, always. From a node's
            # detail page — reached by tapping a row in VITALS — that threw
            # the operator out of the list they were reading (2026-09-29:
            # "when I press back, it takes me back to the home page instead of
            # back to the list of nodes that I just came from"). A screen
            # opened FROM another screen now names it as back_to — a callable
            # answers at press time (Communication apps opened from Chat, #83).
            self.switch_mode(back_to() if callable(back_to) else back_to)

        def on_home():
            h = getattr(widget, "handle_home", None)
            if callable(h):
                # A screen's own exit road that RAISES leaves the operator
                # where they are: on this tool that screen is guarding a
                # build or a walk, and "could not ask" must never become
                # "left without asking" (review, 2026-09-23). on_back has
                # the same guard; it was missing here.
                try:
                    h()                     # the screen's own exit road
                except Exception as e:                             # noqa: BLE001
                    print(f"[nav] NOTICE: {type(widget).__name__}.handle_home "
                          f"raised {e!r}; staying put", flush=True)
                return
            self.switch_mode("home")
        wrap = _BackSwipeWrap(on_back=on_back, on_home=on_home)
        wrap.add_content(widget)
        return wrap

    def _apply_retention(self, days):
        """Prune the running beacon history to the retention window. days=None uses
        the saved setting (startup); a number applies a live change from Settings."""
        try:
            import time
            from monitor import retention
            secs = (days * retention.DAY_S) if days else retention.retention_seconds()
            self.monitor_service.registry.history.set_retention(secs, time.time())
        except Exception as e:
            print(f"[retention] apply skipped: {e}")

    def _monitor_node_count(self):
        try:
            return len(self.monitor_service.dashboard_dicts())
        except Exception:
            return 0

    def _history_bytes(self):
        """Live in-memory beacon-history size (it isn't persisted to disk yet), for
        the Storage-usage breakdown."""
        try:
            import json
            return len(json.dumps(self.monitor_service.registry.history.to_dict()))
        except Exception:
            return 0

    def _install_screensaver(self):
        """Show a moving screensaver after a spell of no touches (burn-in guard on
        the always-on panel); any touch dismisses it and resets the idle timer."""
        try:
            from kivy.core.window import Window
            from ui.widgets.screensaver import Screensaver
            self._screensaver = Screensaver(on_dismiss=self._dismiss_screensaver)
            self._idle_ev = None
            Window.bind(on_touch_down=lambda *_a: self._reset_idle())
            self._reset_idle()
            # The busy marker (monitor/busy_marker.py): written every 30 s
            # while the UI is mid-task, so nothing stops the process under a
            # birth (2026-09-22).
            self._busy_clock = Clock.schedule_interval(self._busy_heartbeat, 30.0)
            self._busy_heartbeat()
        except Exception as e:
            print(f"[screensaver] install skipped: {e}")

    def _reset_idle(self):
        saver = getattr(self, "_screensaver", None)
        if saver is None:
            return
        if getattr(self, "_idle_ev", None) is not None:
            self._idle_ev.cancel()
            self._idle_ev = None
        try:
            from provisioning import screensaver as ss
            if ss.is_enabled():
                self._idle_ev = Clock.schedule_once(
                    lambda dt: self._show_screensaver(), ss.idle_delay_s())
        except Exception:
            pass

    #: Screens where the operator is mid-task (flashing, imaging, aiming an
    #: antenna) — the screensaver must NOT cover the action / progress. On these it
    #: re-arms the idle timer instead of showing, so it never hides a live process.
    _NO_SAVER_SCREENS = {"birth", "birth_guide", "pi_imager", "triage", "probe",
                         "mitosis", "self_diagnose"}

    def _show_screensaver(self):
        try:
            from provisioning import screensaver as ss
            sm = getattr(self, "sm", None)
            busy = self._busy_reason() is not None
            if busy:
                self._reset_idle()            # defer — don't cover an active process
                return
            if not self._screensaver.active:
                self._screensaver.show(ss.style())
        except Exception:
            pass

    def _busy_reason(self):
        """Why the UI must not be stopped or covered right now, or None. ONE
        predicate for the screensaver AND the busy marker (monitor/
        busy_marker.py): a mid-task screen (birth, imaging, triage, probe,
        clone, self-check), a counted activity (a flash/build), or a
        boundary walk or its gates."""
        sm = getattr(self, "sm", None)
        if sm is not None and sm.current in self._NO_SAVER_SCREENS:
            return "the %s screen is mid-task" % sm.current
        if getattr(self, "_activity", 0) > 0:
            return getattr(self, "_activity_label", "a build")
        scan = getattr(self, "scan_screen", None)
        if getattr(scan, "_walk_session", None) is not None:
            return "a boundary walk"
        if getattr(scan, "_walk_gate", None) is not None:
            return "a boundary walk (waiting at the gate)"
        return None

    def _busy_heartbeat(self, *_):
        """Every 30 s: write the marker while busy, clear it when not. A
        Pi birth over SSH was killed by a shell-side UI stop that had no
        way to know (2026-09-22); now the UI says so on disk."""
        try:
            from monitor import busy_marker
            why = self._busy_reason()
            if why:
                busy_marker.write(why)
            else:
                busy_marker.clear()
        except Exception:                                          # noqa: BLE001
            pass

    def begin_activity(self, label="Working — please wait"):
        """Mark a long, touch-free process running (a flash/build) so the
        screensaver can't cover it AND a persistent banner warns the operator not
        to power off / unplug. Balanced by end_activity(). The flash itself runs on
        a daemon thread + child process, so navigating away never interrupts it —
        this just keeps the operator from making it unsafe."""
        import time as _t
        self._activity = getattr(self, "_activity", 0) + 1
        self._activity_label = label
        self._activity_started = _t.time()
        if self._activity == 1:
            self._show_activity_banner(label)
        self._busy_heartbeat()

    def activity_info(self):
        """(label, seconds_running) of the current activity — for telling the
        operator WHICH build is running when they tap Build again (a doubled
        touch produced 'phantom flash' warnings all night 2026-07-30)."""
        import time as _t
        return (getattr(self, "_activity_label", "a build"),
                _t.time() - getattr(self, "_activity_started", _t.time()))

    def end_activity(self):
        self._activity = max(0, getattr(self, "_activity", 0) - 1)
        self._busy_heartbeat()
        if self._activity == 0:
            self._hide_activity_banner()

    def flash_in_progress(self):
        """True while a flash/build is running — power-off and a second build are
        blocked while this holds."""
        return getattr(self, "_activity", 0) > 0

    def _show_activity_banner(self, label):
        """A persistent red top strip, shown over every screen while a flash runs,
        so a background flash is never invisible (and never accidentally cut)."""
        try:
            from kivy.core.window import Window
            from kivy.metrics import dp
            from kivy.uix.label import Label
            from kivy.graphics import Color, Rectangle
            def _mk(text):
                # Two-line labels render as hierarchy: first line large and
                # bold, the rest smaller (briefing Task 7) — so "Flashing
                # RNode" reads at a glance and a long board name can never
                # shrink it.
                if "\n" in text:
                    from kivy.metrics import sp as _sp
                    first, rest = text.split("\n", 1)
                    return (f"[b][size={int(_sp(19))}]{first}[/size][/b]\n"
                            f"[size={int(_sp(14))}]{rest}[/size]")
                return text
            bar = getattr(self, "_activity_banner", None)
            if bar is not None:
                bar.markup = True
                bar.text = _mk(label)
                return
            # Operator-spec (2026-07-30): LARGER, YELLOW with a RED outline —
            # the thin red strip was too easy to miss while a flash ran.
            from kivy.graphics import Line
            bar = Label(text=_mk(label), markup=True, bold=True,
                        font_size="16sp",
                        color=theme.hex_to_rgba(theme.COLORS["red"]),
                        halign="center", valign="middle", size_hint=(None, None),
                        height=dp(52))
            with bar.canvas.before:
                Color(*theme.hex_to_rgba(theme.COLORS["warning_yellow"]))
                rect = Rectangle()
                Color(*theme.hex_to_rgba(theme.COLORS["red"]))
                border = Line(width=dp(2))

            def _sync(*_):
                bar.width = Window.width
                bar.pos = (0, Window.height - bar.height)
                bar.text_size = bar.size
                rect.pos, rect.size = bar.pos, bar.size
                border.rectangle = (bar.x + dp(2), bar.y + dp(2),
                                    bar.width - dp(4), bar.height - dp(4))
            bar.bind(pos=_sync, size=_sync)
            # Keep a reference so _hide_activity_banner can UNBIND it — every
            # build used to leak a permanent Window size callback that kept
            # syncing a removed widget (2026-08-01 bug hunt).
            self._activity_banner_sync = lambda *_a: _sync()
            Window.bind(size=self._activity_banner_sync)
            Window.add_widget(bar)
            _sync()
            self._activity_banner = bar
        except Exception as e:
            print(f"[activity] banner skipped: {e}")

    def _hide_activity_banner(self):
        try:
            from kivy.core.window import Window
            bar = getattr(self, "_activity_banner", None)
            sync = getattr(self, "_activity_banner_sync", None)
            if sync is not None:
                try:
                    Window.unbind(size=sync)
                except Exception:
                    pass
                self._activity_banner_sync = None
            if bar is not None:
                Window.remove_widget(bar)
                self._activity_banner = None
        except Exception:
            pass

    def _dismiss_screensaver(self):
        self._screensaver.hide()
        self._reset_idle()

    def _stamp_born(self):
        """Stamp this unit's born date once (from its RNS identity's mtime), so
        Settings ▸ Tool identity can show it. Best-effort."""
        try:
            import time
            from provisioning import tool_identity
            tool_identity.ensure_born(time.time())
        except Exception as e:
            print(f"[identity] born-stamp skipped: {e}")

    def _register_self_unit(self):
        """Record this medic's own unit in the trust store (always trusted) so
        Settings ▸ Trusted operators shows the family tree. Off-thread — the RNS
        identity read is a subprocess. Best-effort."""
        import platform
        if platform.system() != "Linux":
            return

        def work():
            try:
                from provisioning import tool_identity as ti
                from monitor import trust
                h = ti.identity_hash()
                if h:
                    par = ti.parent() or {}
                    trust.set_self(h, ti.tool_name(), parent=par.get("hash"))
            except Exception as e:
                print(f"[trust] self-unit register skipped: {e}")
        import threading
        threading.Thread(target=work, daemon=True).start()

    def _restore_brightness(self):
        """Re-apply the saved screen brightness at boot (the backlight resets to
        default on reboot). Linux-only, off-thread, best-effort."""
        import platform
        if platform.system() != "Linux":
            return
        try:
            import threading
            from provisioning import brightness
            threading.Thread(target=brightness.restore, daemon=True).start()
        except Exception as e:
            print(f"[brightness] restore skipped: {e}")

    def _self_commission_onboard(self):
        """A freshly-cloned medic boots with an EMPTY onboard roster and its OWN
        boards attached (different serials from the parent). Adopt whatever's
        attached NOW as the medic's own hardware, by USB serial, so it never
        flashes its own radio/GPS — even when rnsd is stopped and the port looks
        free. No-op once commissioned (roster non-empty). Fast: labels boards
        generically (protection is by serial, not role); services refine roles when
        they bind a board. See #82 / [[medic-standard-onboard-config]]."""
        import platform
        if platform.system() != "Linux":
            return
        try:
            from ui.onboard_roster import (load_roster, attached_serial_ports,
                                           commission_attached, onboard_serials)
            if not attached_serial_ports():
                return                                              # nothing to adopt
            # NOTE: this used to bail out entirely once the roster held anything,
            # so the FIRST board commissioned was the only one ever protected. On
            # this medic that meant Jonesey was covered and a GPS Tracker added
            # later never could be — it could not be registered by any code path
            # (audit 2026-08-05). Now each run adopts any service-bound board not
            # already known, so a second piece of the medic's own hardware is
            # protected the moment its service claims it.
            already = onboard_serials()
            # Adopt ONLY boards the medic's own services already hold. A work
            # board attached during a clone's first boot would otherwise be
            # recorded as the medic's own hardware — permanently unflashable
            # (2026-08-01 bug hunt). If nothing is service-bound, don't guess:
            # leave it to the deliberate binding ceremony.
            from ui.onboard_roster import (service_bound_serials,
                                             serial_for_port)
            bound = set(service_bound_serials() or ())
            if not bound:
                print("[onboard] self-commission skipped: no board is bound to "
                      "the medic's own services yet — bind deliberately instead")
                return
            ports = [p for p in attached_serial_ports()
                     if serial_for_port(p) in bound
                     and serial_for_port(p) not in already]
            if not ports:
                return                       # every service-bound board is known
            adopted = commission_attached(ports=ports, probe=lambda _p: None)
            print(f"[onboard] self-commissioned own hardware: {adopted}")
        except Exception as e:
            print(f"[onboard] self-commission skipped: {e}")

    def build(self):
        # At-rest hardening: the SD card holds private mesh keys + a location trail,
        # and RNS/LXMF write some world-readable. Clamp them to owner-only on start.
        try:
            from provisioning.harden import harden_permissions
            harden_permissions()
        except Exception:
            pass
        Window.clearcolor = theme.hex_to_rgba(theme.COLORS["background"])
        # On the medic's touchscreen, fill the native display (which may be
        # portrait, e.g. 720x1280). RNM_WINDOWED=1 gives a 1280x720 dev window.
        if os.environ.get("RNM_WINDOWED"):
            Window.size = (1280, 720)
        else:
            Window.fullscreen = "auto"

        self._self_commission_onboard()
        self._restore_brightness()
        self._stamp_born()
        self._register_self_unit()

        # No sidebar: the front page IS the navigation (its cards open the
        # modes); every mode screen is wrapped by _with_back, which gives it
        # the bottom sliver ('←' / 'Home') and the left-edge back swipe.
        self.sm = ScreenManager()

        # HOME: the designed front page — the poster's cards open the modes.
        home = Screen(name="home")
        self.home_screen = HomeScreen(on_select=self._home_select,
                                      on_mode=self._set_node_mode)
        home.add_widget(self.home_screen)
        self.sm.add_widget(home)
        self._refresh_node_mode()          # read the real mode + update the toggle
        from monitor.movement import MovementDetector
        self._movement = MovementDetector()   # auto-backpack when the medic moves
        from monitor.ups import BatteryGuard
        self._battery_guard = BatteryGuard()  # low-battery safe-shutdown (UPS HAT)
        self._battery_shutting_down = False
        from monitor.dual_supply import DualSupplyGuard
        self._dual_guard = DualSupplyGuard()  # HAT + USB-C at once = danger
        self._dual_alarm = None               # the red modal while tripped
        self._start_dual_supply_watch()
        from monitor.node_watch import NodeWatcher
        self._node_watcher = NodeWatcher()    # escalate nodes down past the grace window
        self._WATCH_FILE = os.path.expanduser("~/.reticulum-node-medic/node_watch.json")
        try:
            import json
            with open(self._WATCH_FILE) as _f:
                self._node_watcher.load_state(json.load(_f))
        except Exception:
            pass
        # GPS clock discipline (offline time authority — the Pi 5 has no RTC
        # battery). No persisted floor: plausibility is a FIXED window in
        # monitor.gps_clock, which needs no seed and can't lock the tool out.
        from monitor.gps_clock import GpsClockDisciplinarian
        self._gps_disciplinarian = GpsClockDisciplinarian()
        self._gps_last_persist = 0.0          # rate-limit the last-sync config write
        self._time_ledger = TimeLedger()
        self._time_service = TimeService(
            self._time_ledger, rns=self._rns_module,
            reply_ident=lambda: self._reply_ident,
            registry=lambda: self.monitor_service.registry,
            disciplined=self._medic_clock_disciplined, log=self._time_log)

        credits = Screen(name="credits")
        # Wrapped like every other mode (operator, 2026-09-22: "every screen").
        # Its tap-anywhere-goes-home stays; the bar sits below it, outside its
        # bounds, so the bar's own taps are the bar's.
        credits.add_widget(self._with_back(CreditsScreen(
            on_select=self.switch_mode,
            on_back=lambda: self.switch_mode("home"))))
        self.sm.add_widget(credits)

        # Final confirmed modes, registered in sidebar order:
        # 1 VITALS · 2 SCAN · 3 BIRTH · 4 TRIAGE · 5 PROBE · 6 MITOSIS
        vitals = Screen(name="vitals")
        # Live discovery fills the dashboard; RNM_DEMO=1 seeds the fake showcase
        # nodes instead (they confused a real deployment, so default off).
        seed = DEMO_NODES if os.environ.get("RNM_DEMO") else []
        self.vitals_screen = VitalsScreen(
            nodes=seed, on_open=self._open_node_detail,
            on_self_diagnose=lambda: self.switch_mode("self_diagnose"))
        vitals.add_widget(self._with_back(self.vitals_screen))
        self.sm.add_widget(vitals)
        # Load the persisted registry so the node history / activity series carries
        # over from past sessions (it's saved periodically + on stop below).
        from monitor.registry import NodeRegistry
        self.monitor_service = MonitorService(
            run=_local_run, registry=NodeRegistry.load(self._REGISTRY_FILE))
        # Teach the registry THIS medic's own identity hashes so it never lists
        # the medic's own destinations as neighbours (2026-08-22: the medic's
        # own lxmd propagation dest showed up in VITALS as a "Propagation
        # relay"). Best-effort and resolved at runtime — no RNS / no files just
        # yields an empty set, which filters nobody.
        try:
            from provisioning import tool_identity as _ti
            self.monitor_service.registry.set_own_identities(
                _ti.own_identity_hashes())
        except Exception as _e:
            print(f"[identity] own-identity filter skipped: {_e}")
        self._apply_retention(None)                  # honour the saved retention window
        self._start_monitor_polling()
        self._start_announce_listener()
        self._start_board_disconnect_watch()

        # Tombstones: identities the operator (or a rebirth) deleted. Pushed
        # into the registry so EVERY ingest path suppresses them while they
        # live (7 days) — without this, the mesh's replayed announces of a
        # dead identity re-created the row (seen live, 2026-08-25).
        try:
            from monitor import tombstones as _tomb
            self.monitor_service.registry.set_tombstones(_tomb.load())
        except Exception:
            pass

        # SCAN is now the SINGLE map: coverage + offline caching + node placement.
        # A stationary tap (or the live GPS fix) sets a spot; "Use this position"
        # stamps it and jumps into BIRTH. The fix-trust badge guards against a
        # HELD/stale fix pinning a node far from where it actually is — the job the
        # old separate gps_confirm page used to do.
        scan = Screen(name="scan")
        from monitor.geo import splitter_gps_reader, read_splitter_fix
        from ui.screens.scan_screen import link_segments, recommendation_markers
        from monitor.synapse_recommend import scan_recommendations
        self._scan_topo = None                    # rebuilt each poll cycle (rnpath)
        # ONE spine end to end: the map pins and the Build next panel read the
        # SAME recommender feed (SYNAPSE, LoRa-first), so they cannot disagree.
        # Cached per topology object — the topo rebuilds each poll cycle, but
        # the map redraws ~20x/s while panning and must not re-run the engine.
        self._scan_recs_cache = (None, [])

        def _scan_markers():
            topo = self._scan_topo
            if not topo:
                return []
            if self._scan_recs_cache[0] is topo:
                return self._scan_recs_cache[1]
            # The boundary walk's banked evidence feeds the same spine
            # (first live producer, 2026-09-15): sightings widen the store,
            # losses bound the tail — placement plans on what was WALKED.
            store = fails = None
            try:
                from monitor.boundary_walk import (load_walk_failures,
                                                   load_walk_observations)
                from monitor.synapse_links import LinkObservationStore
                wobs = load_walk_observations()
                if wobs:
                    store = LinkObservationStore()
                    for o in wobs:
                        store.add(o)
                fails = load_walk_failures() or None
            except Exception:                                      # noqa: BLE001
                store = fails = None
            kw = {}
            if store is not None:
                kw["store"] = store
            if fails:
                # recommend() has no failures input — negative evidence
                # enters through the range estimate, per synapse_range.
                from monitor.synapse_range import estimate_range
                from monitor.synapse_links import LinkObservationStore
                kw["range_estimate"] = estimate_range(
                    store or LinkObservationStore(), failures=fails)
            recs = scan_recommendations(topo,
                                        registry=self.monitor_service.registry,
                                        **kw)
            positions = {n.id: (n.lat, n.lon) for n in topo.nodes
                         if n.lat is not None and n.lon is not None}
            markers = recommendation_markers(recs, positions)
            self._scan_recs_cache = (topo, markers)
            return markers

        def _boundary_provider():
            # The boundary-walk ring (operator, 2026-09-24; restructured on
            # 2026-09-24 review): the real logic is monitor.boundary_rings
            # .boundary_rings_for — pure, no Kivy, unit-tested directly —
            # one ring per PHYSICAL DEVICE, centred on that device's own
            # stamped lat/lon, never a fuzzed beacon. This closure is a
            # thin shim supplying the live registry, the banked failures
            # and the clock, matching the house rule that SCAN's other
            # providers (_scan_markers above) keep their pure core
            # separate from the Kivy wiring.
            import time as _t
            from monitor.boundary_rings import boundary_rings_for
            from monitor.boundary_walk import load_walk_failures
            try:
                fails = load_walk_failures()
            except Exception:                                      # noqa: BLE001
                fails = []
            return boundary_rings_for(self.monitor_service.registry, fails,
                                      _t.time())

        self.scan_screen = ScanScreen(
            nodes=self.monitor_service.located_nodes(),
            gps_reader=splitter_gps_reader(),     # the Tracker's live "you are here"
            fix_reader=read_splitter_fix,         # full fix -> live/held/none badge
            on_place=self._on_gps_confirmed,      # "Use this position" -> BIRTH
            on_node_pick=self._open_node_cert,    # tap a node dot -> its certificate
            # mesh-lines toggle + recommender pins (empty until topology)
            links_provider=lambda: (link_segments(
                self._scan_topo,
                transports=self.scan_screen.visible_transports())
                if self._scan_topo else []),
            suggestions_provider=_scan_markers,
            recommendations_provider=_scan_markers,
            boundary_provider=_boundary_provider)
        scan.add_widget(self._with_back(self.scan_screen))
        self.sm.add_widget(scan)

        # Certificate viewer — a persistent host screen whose content is rebuilt for
        # whichever node the operator taps (VITALS row or SCAN map dot).
        self.sm.add_widget(Screen(name="cert_view"))

        # Settings hub (the home gear) — WiFi to start, more to come.
        settings_scr = Screen(name="settings")
        from ui.screens.settings_screen import SettingsScreen
        self.settings_screen = SettingsScreen(
            on_open=self.switch_mode,
            on_retention_change=self._apply_retention,
            node_count_provider=self._monitor_node_count,
            on_preview_screensaver=self._show_screensaver,
            on_home_profile_change=self._on_home_profile_change)
        settings_scr.add_widget(self._with_back(self.settings_screen))
        self.sm.add_widget(settings_scr)

        notif_scr = Screen(name="notifications")
        from ui.screens.notifications_screen import NotificationsScreen
        # CHAT's known addresses are offered as a picker; the chat store is
        # built further down, so they are looked up when the screen opens.
        self.notifications_screen = NotificationsScreen(
            contacts=lambda: self._chat_contacts())
        notif_scr.add_widget(self._with_back(self.notifications_screen))
        notif_scr.bind(on_enter=lambda *_: self.notifications_screen.enter())
        self.sm.add_widget(notif_scr)

        self.sm.add_widget(Screen(name="node_detail"))   # filled on a VITALS tap

        # Language — pick the UI language (applies on next app start).
        language_scr = Screen(name="language")
        from ui.screens.language_screen import LanguageScreen
        language_scr.add_widget(self._with_back(LanguageScreen()))
        self.sm.add_widget(language_scr)

        # Security preview — walk the encrypt-at-rest ceremony end to end
        # (write-down key -> unlock screen -> three strikes -> recovery ->
        # slide-to-reset) with NO real vault behind it, so the look can be
        # judged before any of it is wired to cryptsetup (2026-08-02).
        sec_scr = Screen(name="security_preview")
        self._security_host = BoxLayout(orientation="vertical")
        sec_scr.add_widget(self._with_back(self._security_host))
        self.sm.add_widget(sec_scr)

        # Communication apps — hand Columba/Sideband to a phone over Wi-Fi + QR.
        comms_scr = Screen(name="comms")
        from ui.screens.comms_screen import CommsScreen
        self.comms_screen = CommsScreen()
        comms_scr.add_widget(self._with_back(self.comms_screen,
                                             back_to=self._comms_back_target))
        comms_scr.bind(on_enter=lambda *_: self.comms_screen.enter())
        self.sm.add_widget(comms_scr)
        # CHAT — the medic's own messenger (docs/CHAT.md, 2026-09-29). The
        # store is the on-disk truth; the service comes up on the mesh
        # listener thread once RNS is attached (see _start_announce_listener).
        from monitor.lxmf_chat import MessageStore
        from monitor.chat_service import ChatService
        from provisioning.tool_identity import tool_name
        from ui.screens.chat_screen import ChatScreen
        self.chat_store = MessageStore()
        self._chat = ChatService(self.chat_store, display_name=tool_name())
        chat_scr = Screen(name="chat")
        self.chat_screen = ChatScreen(self.chat_store, lambda: self._chat,
                                      open_phone_apps=self._open_comms_from_chat,
                                      retry=self.retry_chat)
        chat_scr.add_widget(self._with_back(self.chat_screen))
        chat_scr.bind(on_enter=lambda *_: self.chat_screen.enter(),
                      on_leave=lambda *_: self.chat_screen.leave())
        self.sm.add_widget(chat_scr)
        # NOT a bare lambda around tick(): Kivy CANCELS an interval whose
        # callback returns False, and tick() returns False whenever it is
        # rate-limited — so the propagation node was asked once, a minute after
        # start, and never again ("checked 30 Sep 17:11" a day later).
        Clock.schedule_interval(self._chat_tick, 60)
        self._chat_poll_ev = Clock.schedule_interval(self._chat_start_poll, 3)
        Clock.schedule_interval(self._refresh_chat_badge, 3)

        # Field readiness — the caller workflows.carry has never had. It shipped
        # complete and tested (d9b29ca) and reachable by nobody, which is the
        # same shape of bug as the phone-app downloader before it. enter() runs
        # audit() ONLY: local reads, no network, safe on every open. The
        # downloads happen behind an explicit tap and nowhere else.
        carry_scr = Screen(name="carry")
        from ui.screens.carry_screen import CarryScreen
        self.carry_screen = CarryScreen()
        carry_scr.add_widget(self._with_back(self.carry_screen))
        carry_scr.bind(on_enter=lambda *_: self.carry_screen.enter())
        self.sm.add_widget(carry_scr)

        # WiFi connect — join a hotspot / venue AP so online features work afield.
        wifi_scr = Screen(name="wifi")
        from ui.screens.wifi_screen import WifiScreen
        self.wifi_screen = WifiScreen(
            on_done=lambda: setattr(self.sm, "current", "settings"))
        wifi_scr.add_widget(self._with_back(self.wifi_screen))
        wifi_scr.bind(on_enter=lambda *_: self.wifi_screen.enter())   # auto-search
        self.sm.add_widget(wifi_scr)

        radio_scr = Screen(name="radio_defaults")
        from ui.screens.radio_defaults_screen import RadioDefaultsScreen
        radio_scr.add_widget(self._with_back(RadioDefaultsScreen()))
        self.sm.add_widget(radio_scr)

        identity_scr = Screen(name="tool_identity")
        from ui.screens.tool_identity_screen import ToolIdentityScreen
        identity_scr.add_widget(self._with_back(ToolIdentityScreen()))
        self.sm.add_widget(identity_scr)

        salv_scr = Screen(name="salvage")
        from ui.screens.salvage_screen import SalvageScreen
        self.salvage_screen = SalvageScreen()
        salv_scr.add_widget(self._with_back(self.salvage_screen))
        self.sm.add_widget(salv_scr)

        enc_scr = Screen(name="encryption")
        from ui.screens.encryption_screen import EncryptionScreen
        self.encryption_screen = EncryptionScreen()
        enc_scr.add_widget(self._with_back(self.encryption_screen))
        self.sm.add_widget(enc_scr)

        storage_scr = Screen(name="storage")
        from ui.screens.storage_screen import StorageScreen
        storage_scr.add_widget(self._with_back(
            StorageScreen(history_bytes=self._history_bytes)))
        self.sm.add_widget(storage_scr)

        trust_scr = Screen(name="trusted_operators")
        from ui.screens.trusted_operators_screen import TrustedOperatorsScreen
        trust_scr.add_widget(self._with_back(TrustedOperatorsScreen()))
        self.sm.add_widget(trust_scr)

        datetime_scr = Screen(name="datetime")
        from ui.screens.datetime_screen import DateTimeScreen
        self.datetime_screen = DateTimeScreen()
        datetime_scr.add_widget(self._with_back(self.datetime_screen))
        datetime_scr.bind(on_enter=lambda *_: self.datetime_screen.enter())
        self.sm.add_widget(datetime_scr)

        about_scr = Screen(name="about")
        from ui.screens.about_screen import AboutScreen
        about_scr.add_widget(self._with_back(AboutScreen()))
        self.sm.add_widget(about_scr)

        guide_scr = Screen(name="guide")
        from ui.screens.guide_screen import GuideScreen
        guide_scr.add_widget(self._with_back(GuideScreen()))
        self.sm.add_widget(guide_scr)

        birth = Screen(name="birth")
        # Real hardware when a board is attached to the medic's USB; the emulated
        # demos only when nothing is (dev box / no board) — see ui.hw_factories.
        from ui import hw_factories as hw
        self.birth_screen = BirthScreen(
            workflow_factories={
                "rtnode2400": lambda target=None, node_name="":
                    hw.make_rtnode_build(_demo_rtnode_build, target=target,
                                         node_name=node_name),
                "pi_rnode": _pi_rnode_factory},   # honest-fail until the real flow lands
            rnode_flash_factory=lambda board:
                hw.make_rnode_flash(board, _demo_rnode_flash),
            on_mitosis=lambda: self.switch_mode("mitosis"),
            on_guide=self._open_birth_guide,
            on_salvage=lambda: self.switch_mode("salvage"))
        birth.add_widget(self._with_back(self.birth_screen))
        self.sm.add_widget(birth)

        # Guided birth — one instruction per screen with animations, for a
        # first-time operator. Its physical-prep steps hand off to the BIRTH
        # screen above (detect / name / flash).
        birth_guide = Screen(name="birth_guide")
        from ui.screens.birth_guide_screen import BirthGuideScreen
        self.birth_guide_screen = BirthGuideScreen(
            on_complete=self._guided_birth_complete,
            on_navigate=self.switch_mode,
            heard_fn=self._heard_mesh_candidates,
            adopt_air_fn=self._adopt_over_air)
        birth_guide.add_widget(self._with_back(self.birth_guide_screen))
        self.sm.add_widget(birth_guide)

        # First-use setup — the security ceremony, then a screen per mode.
        # Registered like any other screen so Settings can re-open it (a medic
        # handed to somebody else has to be able to start over) and so the
        # boot-time route below is a plain switch_mode rather than a special
        # case in the screen manager.
        #
        # NO _with_back WRAPPER, deliberately. That chrome's Back goes HOME, and
        # home is the one place this walkthrough must not offer as an escape
        # from its first screen — the whole point is that the operator has not
        # seen the front page yet and would not know what they were skipping.
        # The wizard carries its own Back on every step and hands home the way
        # out from its own end (on_finish).
        setup = Screen(name="setup")
        from ui.screens.setup_wizard_screen import SetupWizardScreen
        self.setup_screen = SetupWizardScreen(
            on_finish=lambda: self.switch_mode("home"),
            on_navigate=self.switch_mode,
            # enrol_fn is NOT supplied: the encrypt-at-rest container has never
            # been created on a real medic, and the summary says so out loud
            # rather than claiming records are protected. Wiring the real one is
            # task #34, gated on the boot-unlock decision.
            vault_exists_fn=self._vault_exists)
        setup.add_widget(self.setup_screen)
        self.sm.add_widget(setup)

        pi_imager_scr = Screen(name="pi_imager")
        from ui.screens.pi_imager_screen import PiImagerScreen
        from workflows.rtnode_portal import medic_wifi_credentials
        # Kept on the app so BIRTH can hand it the node's name rather than
        # making the operator type it twice (2026-08-02).
        self.pi_imager_screen = PiImagerScreen(
            wifi_credentials=medic_wifi_credentials)
        pi_imager_scr.add_widget(self._with_back(self.pi_imager_screen))
        self.sm.add_widget(pi_imager_scr)

        triage = Screen(name="triage")
        self.triage_screen = TriageScreen(
            feed_factory=_triage_feed, lighthouse=self._lighthouse,
            on_build=lambda: self.switch_mode("birth"),
            on_home=lambda: self.switch_mode("home"),
            on_antenna_test=lambda: self.switch_mode("antenna_test"),
            on_boundary_walk=self._pick_node_for_walk)
        triage.add_widget(self._with_back(self.triage_screen))
        # Opening Triage auto-activates the beacon; leaving it stops the beacon
        # AND the 2 Hz sampling tick.
        #
        # stop() existed and nothing called it (2026-09-05). on_leave stopped
        # only the lighthouse, so after one visit to TRIAGE the screen kept
        # feeding its calibrator twice a second for the life of the process —
        # for a screen nobody was looking at. A profile of the live medic put
        # 85% of the app's CPU in monitor.triage; the medic sat at ~30% of a
        # core while idle and every button felt slow. The comment above said
        # "leaving it stops it" and it was half true, which is why nobody
        # looked here.
        triage.bind(on_enter=lambda *a: (self.triage_screen.start(),
                                         self.triage_screen.enter_triage()),
                    on_leave=lambda *a: (self.triage_screen.stop_lighthouse(),
                                         self.triage_screen.stop()))
        self.sm.add_widget(triage)

        # Antenna test — TRIAGE's A/B ear comparison for real antennas.
        # Dormant until shown (the hidden-screen poll trap must not recur).
        from ui.screens.antenna_test_screen import AntennaTestScreen
        ant = Screen(name="antenna_test")
        _ant = AntennaTestScreen(on_home=lambda: self.switch_mode("triage"))
        ant.add_widget(self._with_back(_ant))
        ant.bind(on_enter=lambda *a: _ant.begin_screen(),
                 on_leave=lambda *a: _ant.sleep())
        self.sm.add_widget(ant)

        probe = Screen(name="probe")
        # The header names the board on USB at the moment the screen opens
        # and again when a run starts — never a label frozen at app start.
        _probe_screen = ProbeScreen(
            workflow_factory=lambda: hw.make_repair_workflow(_demo_repair_workflow),
            target_fn=hw.probe_target_label,
            on_self_diagnose=lambda: self.switch_mode("self_diagnose"),
            on_birth_tracker=lambda: self.switch_mode("firstborn"))
        self.probe_screen = _probe_screen          # the control socket's "probe"
        probe.add_widget(self._with_back(_probe_screen))
        probe.bind(on_pre_enter=lambda *_: _probe_screen.enter(),
                   on_leave=lambda *_: _probe_screen.leave())
        self.sm.add_widget(probe)

        # Self Diagnose — the medic checks & heals its OWN onboard radio/GPS board.
        from ui.screens.self_diagnose_screen import SelfDiagnoseScreen
        self_dx = Screen(name="self_diagnose")
        self_dx.add_widget(self._with_back(SelfDiagnoseScreen()))
        self.sm.add_widget(self_dx)

        mitosis = Screen(name="mitosis")
        _mit = MitosisScreen(workflow_factory=_mitosis_factory)
        mitosis.add_widget(self._with_back(_mit))
        # DORMANT until shown: the card poll ran from app launch and any card
        # inserted during an ordinary BIRTH dragged the hidden MITOSIS screen
        # through its stages (adversarial review 2026-08-25).
        mitosis.bind(on_pre_enter=lambda *_: _mit.begin(),
                     on_leave=lambda *_: _mit.sleep())
        self.sm.add_widget(mitosis)

        # THE FIRSTBORN — the medic births its own Heltec Tracker (its GPS/time
        # source) as part of the first tour. Dormant until shown, like MITOSIS:
        # its plug-state poll must not run while hidden.
        firstborn = Screen(name="firstborn")
        # on_home -> HOME, not "setup": opening a mode from the tour already
        # marked the walkthrough done (_leave_for), and switch_mode("setup")
        # re-runs reset() — which would dump a new keeper who JUST finished the
        # firstborn back at the security-welcome screen, re-running all of setup
        # (2026-08-27). Home is where every other tour "open" lands.
        _fb = FirstbornScreen(on_home=lambda: self.switch_mode("home"))
        firstborn.add_widget(self._with_back(_fb))
        firstborn.bind(on_pre_enter=lambda *_: _fb.begin_screen(),
                       on_leave=lambda *_: _fb.sleep())
        self.sm.add_widget(firstborn)

        self.sm.current = self._opening_screen()
        self._install_screensaver()

        # The on-screen keyboard floats above every screen (the touchscreen has
        # no physical keys). Fields call ui.onscreen_keyboard.bind_field(...) and
        # it reveals itself, panning the ScreenManager up so the field stays clear.
        root = FloatLayout()
        root.add_widget(self.sm)
        self.keyboard = OnScreenKeyboard(pan_target=self.sm,
                                         pos_hint={"x": 0, "y": 0})
        root.add_widget(self.keyboard)
        # THE CONTROL SOCKET (ui/remote.py, 2026-09-29): open any screen by name
        # from a shell, on this app, on the main thread — so a walkthrough of
        # every screen is a loop with grim, not a person at the panel.
        try:
            from ui.remote import ControlServer
            self._control = ControlServer(self)
            self._control.start()
        except Exception:                                              # noqa: BLE001
            self._control = None          # a missing socket must never cost the UI
        return root

    def _opening_screen(self):
        """Where a boot lands: the setup walkthrough on a medic that has never
        finished it, otherwise the front page.

        RNM_START STILL WINS. It is how every bench session, screenshot and
        preview reaches a screen directly, and a first-use check that overrode
        it would silently swallow `RNM_START=vitals` on any developer machine
        whose home directory has no marker — which is all of them.

        Best-effort: a medic that cannot read its own config directory boots to
        the front page. Refusing to start because a marker file is unreadable
        would be a field tool bricking itself over a settings file.
        """
        forced = os.environ.get("RNM_START")
        if forced:
            return forced
        try:
            from provisioning.first_use import is_first_use
            return "setup" if is_first_use() else "home"
        except Exception:
            return "home"

    def _vault_exists(self) -> bool:
        """Are this medic's records encrypted, right now? Asked of the disk:
        the per-file records vault that Settings ▸ Encrypt actually builds.
        This used to look for the dead LUKS container, so the wizard's
        summary said NOT ENCRYPTED over encrypted records (readiness sweep,
        2026-10-03)."""
        try:
            from provisioning.records_vault import is_vault, records_root
            return bool(is_vault(records_root()))
        except Exception:                                          # noqa: BLE001
            return False

    def _start_monitor_polling(self, interval: float = 30.0):
        """Poll the LAN on a background thread; push live nodes to the screen
        via the Kivy Clock (UI updates must happen on the main thread)."""
        stop = threading.Event()
        self._monitor_stop = stop

        def loop():
            i = 0
            while not stop.is_set():
                try:
                    self.monitor_service.cycle(rediscover=(i % 10 == 0))
                    self._resolve_identities()   # collapse a device's dests to 1 row
                    dicts = self.monitor_service.dashboard_dicts()
                    # an EMPTY list is a valid dashboard: with "if dicts" the
                    # last deleted node stayed on VITALS forever (2026-10-03)
                    Clock.schedule_once(
                        lambda dt, d=dicts: self.vitals_screen.set_nodes(d), 0)
                    located = self.monitor_service.located_nodes()
                    Clock.schedule_once(
                        lambda dt, n=located: self.scan_screen.set_nodes(n), 0)
                    # topology for the SCAN mesh-lines + gap markers (rnpath is the
                    # only source of located<->located edges; cheap, once per cycle)
                    self._scan_topo = self._build_scan_topology()
                    # persist every ~10 cycles (≈5 min) so history/activity survive
                    # a restart without hammering the SD card each 30 s tick
                    if i % 10 == 0:
                        self.monitor_service.registry.save(self._REGISTRY_FILE)
                        self._save_node_watch()
                    self._check_movement()       # auto-backpack if we're on the move
                    self._check_battery()        # UPS gauge + low-battery shutdown
                    self._check_node_watch(dicts)  # escalate long-unreachable nodes
                    self._check_gps_clock()      # discipline the clock from GPS UTC
                except Exception:
                    pass  # never let a poll error kill the loop
                i += 1
                stop.wait(interval)

        threading.Thread(target=loop, daemon=True).start()
        # THE MEDIC STOCKS ITS OWN APP SHELF (operator, 2026-09-13: "the user
        # shouldn't have to download these communication apps"). A medic
        # online with an empty APK shelf fetches Columba + Sideband itself —
        # same sha256-verified downloader the Comms screen uses — and
        # freshens weekly, so a clone always inherits a stocked shelf.
        # Quiet by design: one ui.log line either way, never a dialog.
        def _stock_apps():
            import os as _os, time as _t
            try:
                _t.sleep(120)              # let boot, rnsd and the UI settle
                from workflows.phone_apps import (cached_apps, should_autosync,
                                                  sync_all)
                from workflows.updater import has_connectivity
                from ui.hw_factories import LocalConnection
                conn = LocalConnection()
                stamp = _os.path.expanduser(
                    "~/.reticulum-node-medic/apps_last_sync")
                try:
                    age_d = (_t.time() - _os.path.getmtime(stamp)) / 86400.0
                except OSError:
                    age_d = 1e9
                carried = all(a.get("carried") for a in cached_apps(conn))
                ok, why = should_autosync(carried, age_d,
                                          has_connectivity(conn))
                if not ok:
                    return
                # Never on a phone hotspot or other METERED link: ~100 MB of
                # APKs two minutes after boot, on someone's data plan, with no
                # dialog (readiness ledger #160). NetworkManager knows.
                metered = conn.run("nmcli -g GENERAL.METERED connection show "
                                   "--active 2>/dev/null")[1]
                if any(line.strip().lower().startswith("yes")
                       for line in (metered or "").splitlines()):
                    print("[apps] shelf sync skipped: the connection is metered",
                          flush=True)
                    return
                print(f"[apps] self-stocking the phone-app shelf: {why}",
                      flush=True)
                res = sync_all(conn)
                _os.makedirs(_os.path.dirname(stamp), exist_ok=True)
                with open(stamp, "w") as fh:
                    fh.write(str(_t.time()))
                print(f"[apps] shelf sync finished: {res}", flush=True)
            except Exception as e:                                # noqa: BLE001
                print(f"[apps] shelf sync skipped: {e}", flush=True)
        threading.Thread(target=_stock_apps, daemon=True).start()

    def _build_scan_topology(self):
        """The mesh topology (registry + rnpath path table) that drives SCAN's
        mesh-lines + gap markers. Best-effort; None on any failure."""
        try:
            import json
            import time
            from monitor.topology import build_topology
            raw = _local_run("rnpath -t --json 2>/dev/null") or "[]"
            paths = json.loads(raw)
            if not isinstance(paths, list):
                paths = []
            topo = build_topology(self.monitor_service.registry, paths,
                                  time.time(),
                                  exclude=self._forgotten_hashes())
            # THE MEDIC PLACES ITSELF TOO: a mesh line needs both ends
            # located, and the medic node was born coordinate-less — so no
            # medic<->node line could EVER draw (found 2026-08-27 wiring
            # thickness=strength). Its position: the live splitter fix when
            # Jonesey sees sky, else the freshest self-reported kin position
            # is NOT a stand-in (that would be a lie) — instead fall back to
            # the operator's stamped home position if one exists on record.
            try:
                from monitor.geo import read_splitter_fix
                fix = read_splitter_fix()
                if fix is not None:
                    for n in topo.nodes:
                        if n.is_medic:
                            n.lat, n.lon = fix.lat, fix.lng
                            n.self_located = True
                            break
            except Exception:
                pass
            return topo
        except Exception:
            return None

    def refresh_radio_badge(self):
        """Re-check the home screen's 'radio params changed' badge (called by
        Settings ▸ Default radio parameters after any save/revert)."""
        try:
            scr = getattr(self, "home_screen", None)
            if scr is not None and hasattr(scr, "refresh_radio_badge"):
                scr.refresh_radio_badge()
        except Exception:
            pass

    def quiet_board_watch(self, seconds=40):
        """Silence the board-disconnect watch for a window. Called before any
        DELIBERATE board reset (the detect step's chip-id read, the banner
        read) — those re-enumerate USB for several seconds, which is not a
        disconnect (operator hit the false alarm at the start of the guide,
        2026-07-31)."""
        import time as _t
        self._bd_quiet_until = _t.time() + seconds

    def _start_board_disconnect_watch(self):
        """Warn LOUDLY when a work board vanishes from USB mid-birth (operator
        spec 2026-07-31: 'board disconnected!'). Watches only on the birth
        screens, and debounces 5 s so a flash/reboot's normal USB re-enumeration
        blip never false-alarms. Re-arms when a board returns."""
        from kivy.clock import Clock

        self._bd_seen = False
        self._bd_gone_at = None
        self._bd_warned = False
        self._bd_popup = None

        def clear_warning():
            # The board came back (or a flash owns it) — a lingering
            # 'disconnected!' card is now a lie; take it down ourselves.
            pop = self._bd_popup
            self._bd_popup = None
            self._bd_warned = False
            self._bd_gone_at = None
            if pop is not None:
                try:
                    pop.dismiss()
                except Exception:
                    pass

        def tick(_dt):
            try:
                current = self.sm.current if hasattr(self, "sm") else ""
                if current not in ("birth", "birth_guide"):
                    self._bd_seen = False
                    clear_warning()
                    return
                # A running flash resets and re-enumerates the board ON
                # PURPOSE (esptool's hard reset, the birth-cry boot) — the
                # port vanishing for many seconds is the flash WORKING, and
                # the build verifies its own outcome. Stay silent
                # (operator report 2026-07-31: false alarm mid-flash).
                if self.flash_in_progress():
                    clear_warning()
                    return
                # A STEP MAY ASK FOR THE BOARD TO GO. The guided birth's
                # "Take the radio out of Node Medic" does exactly that, and
                # this watcher fired five seconds later telling the operator
                # the board "has vanished from USB — plug it back in before
                # continuing" (reported live, 2026-08-09). The tool told them
                # off for following its own instruction, on the step that gave
                # it. Every step after that one also expects it gone: the radio
                # is on the bench until it goes onto the Pi at the end.
                if getattr(self, "_board_absence_expected", False):
                    clear_warning()
                    return
                import time as _t
                # Inside a quiet window (a deliberate detect/banner reset).
                if _t.time() < getattr(self, "_bd_quiet_until", 0):
                    clear_warning()
                    return
                from ui.hw_factories import local_board_ports
                if local_board_ports():
                    self._bd_seen = True
                    clear_warning()
                    return
                if not self._bd_seen or self._bd_warned:
                    return
                if self._bd_gone_at is None:
                    self._bd_gone_at = _t.time()
                elif _t.time() - self._bd_gone_at > 5:
                    self._bd_warned = True
                    from ui.requirement_popup import requirement_popup
                    self._bd_popup = requirement_popup(
                        "Board disconnected!\n\nThe board that was plugged in "
                        "has vanished from USB. Check the cable and plug it "
                        "back in before continuing.",
                        "Board disconnected", False)
            except Exception:
                pass

        Clock.schedule_interval(tick, 2)

    #: set by the mesh listener once RNS is attached (see _start_chat_if_ready)
    _rns_attached = False
    _chat_starting = False

    def _start_chat_if_ready(self, log=None):
        """Start the chat service exactly once, on a thread, when BOTH the
        service exists and RNS is attached — from whichever side got there
        last. start() announces and asks the propagation node, so never on the
        Kivy main thread."""
        chat = getattr(self, "_chat", None)
        if chat is None or not self._rns_attached or chat.running or self._chat_starting:
            return False
        self._chat_starting = True
        _log = log or (lambda m: None)

        def _go():
            try:
                chat._log = _log
                chat.start()
            except Exception as e:                                     # noqa: BLE001
                _log("chat service failed to start: %r" % (e,))
            finally:
                self._chat_starting = False

        threading.Thread(target=_go, daemon=True).start()
        return True

    def _chat_start_poll(self, dt):
        """build()'s side of the race: poll until the listener has attached,
        then stop polling (returning False cancels a Clock interval)."""
        chat = getattr(self, "_chat", None)
        if chat is not None and chat.running:
            return False
        if chat is not None and chat.last_error:
            # It tried and failed; more goes (a minute — rnsd may be settling),
            # then stop — the screen shows last_error in words. Not a
            # 3-second retry storm in the log (live, 2026-09-29 23:58).
            self._chat_attempts = getattr(self, "_chat_attempts", 0) + 1
            if self._chat_attempts > 20:
                self._chat_log("chat: giving up after %d attempts: %s"
                               % (self._chat_attempts, chat.last_error))
                # the screen shows last_error and offers Try again (ledger #187)
                return False
        # Waiting for rnsd that never comes: after ~2 minutes say so and stop,
        # instead of "waiting…" for ever over a dead Send (ledger #78); the
        # screen's Try again starts this poll afresh.
        self._chat_waits = getattr(self, "_chat_waits", 0) + 1
        if chat is not None and not self._rns_attached and self._chat_waits > 40:
            chat.last_error = "the mesh service (rnsd) did not come up"
            self._chat_log("chat: gave up waiting for the mesh service (rnsd)")
            return False
        self._start_chat_if_ready(self._chat_log)
        return True

    def retry_chat(self):
        """The chat screen's Try again: forget the give-up and poll afresh
        (readiness ledger #187 — Send was a dead button with no door)."""
        self._chat_attempts = 0
        self._chat_waits = 0
        chat = getattr(self, "_chat", None)
        if chat is not None and not chat.running:
            chat.last_error = ""
        ev = getattr(self, "_chat_poll_ev", None)
        if ev is not None:
            try:
                ev.cancel()
            except Exception:                                      # noqa: BLE001
                pass
        self._chat_poll_ev = Clock.schedule_interval(self._chat_start_poll, 3)
        self._start_chat_if_ready(self._chat_log)

    _chat_badge_version = -1

    def _chat_tick(self, dt):
        """Once a minute: re-announce when due, ask the propagation node every 20
        min. Returns None on purpose — see the schedule_interval note."""
        try:
            self._chat.tick()
        except Exception as e:                                         # noqa: BLE001
            self._chat_log("chat tick failed: %r" % (e,))

    def _refresh_chat_badge(self, dt):
        """The unread count on the front-page CHAT card follows the store."""
        store = getattr(self, "chat_store", None)
        if store is None:
            return
        v = store.poll()                       # looks at the disk first
        if v == self._chat_badge_version:
            return
        self._chat_badge_version = v
        try:
            self.home_screen.set_unread(store.unread_total())
        except Exception:                                              # noqa: BLE001
            pass
        # The names chat heard become VITALS' names for the same hashes.
        try:
            self.monitor_service.registry.set_peer_names(
                {p["hash"]: p["name"] for p in store.peers() if p.get("name")})
        except Exception:                                              # noqa: BLE001
            pass

    def _chat_log(self, msg):
        try:
            import RNS
            RNS.log("Node Medic: " + msg)
        except Exception:                                              # noqa: BLE001
            print("Node Medic: " + msg, flush=True)

    def _start_announce_listener(self):
        """Hear announces live (via the shared rnsd): each carries the device
        IDENTITY (collapses its aspect-destinations into one VITALS row) and
        often a display name. A second handler collects rtnode.health nodes as
        beacon targets for the Triage lighthouse."""
        registry = self.monitor_service.registry
        self._beacon_targets = {}          # dst_hash -> RNS identity (rtnode.health)

        def listen():
            try:
                import time as _t
                import RNS
            except Exception:
                return                   # no RNS lib at all (dev box) -> nothing to do

            app = self

            class _Handler:
                aspect_filter = None

                def received_announce(_h, destination_hash,
                                      announced_identity, app_data):
                    ih = None
                    try:
                        ih = announced_identity.hash.hex()
                    except Exception:
                        pass
                    try:
                        rec = registry.ingest_announce(
                            destination_hash, app_data or b"",
                            _t.time(), identity_hash=ih)
                        # One line per announce heard (LoRa is quiet, a few/hr).
                        # THE ground truth for "is the app deaf?" — the exact
                        # question the 2026-07-30 incident took hours to answer.
                        _log("announce %s len=%d beacon=%s" % (
                            destination_hash.hex()[:8],
                            len(app_data or b""),
                            "yes" if (rec is not None and rec.latest_beacon)
                            else "no"))
                    except Exception as e:
                        _log("announce ingest FAILED: %r" % (e,))

            class _HealthHandler:
                aspect_filter = "rtnode.health"

                def received_announce(_h, destination_hash,
                                      announced_identity, app_data):
                    # a kin RTNode we can COMMAND to beacon (verified live:
                    # a 0x01 packet to rtnode.health -> immediate reply)
                    try:
                        h = destination_hash.hex()
                        app._beacon_targets[h] = announced_identity
                        app._save_beacon_hashes([h])   # remember across restarts
                    except Exception:
                        pass

            def _attach():
                from monitor.mesh import (rns_already_initialised,
                                          rns_thread_signal_error)
                try:
                    RNS.Reticulum()      # attach to the shared instance
                except Exception as e:
                    # Two errors here mean ATTACHED, not failed (both from the
                    # 2026-07-30 deaf-app incident):
                    #  * "already running"  — another component won the
                    #    in-process init race; the instance exists.
                    #  * "signal only works in main thread" — we're on a
                    #    thread, so RNS couldn't install its SIGINT/SIGTERM
                    #    hooks; that's the LAST step of init, everything
                    #    functional is already up (and Kivy owns our signals).
                    # Anything else (rnsd not up yet) re-raises so the retry
                    # loop rides out the boot race.
                    if not (rns_already_initialised(e)
                            or rns_thread_signal_error(e)):
                        raise
                RNS.Transport.register_announce_handler(_Handler())
                RNS.Transport.register_announce_handler(_HealthHandler())
                app._setup_health_reply(RNS, _log)
                # CHAT comes up on top of this attach — but the listener starts
                # (build(), ~line 879) BEFORE the chat service exists (~1039),
                # and on the first live restart the attach won the race and
                # the hook found nothing (2026-09-29 23:49). So: flag here,
                # start from whichever side is last.
                app._rns_attached = True
                app._start_chat_if_ready(_log)
                # Positive proof of registration (first-try attach was silent
                # before, making "attached" and "thread died" indistinguishable).
                _log("mesh listener attached — announce handlers registered")

            def _log(msg):
                try:
                    RNS.log("Node Medic: " + msg)   # visible in the rnsd log
                except Exception:
                    # RNS.log can itself fail (partially-inited RNS) — fall
                    # back to a flushed print so diagnosis lines NEVER vanish
                    # (unflushed stdout hid an entire night of evidence,
                    # 2026-07-30).
                    try:
                        print("Node Medic: " + msg, flush=True)
                    except Exception:
                        pass

            # RETRY, don't give up on the first failure: on a power-cycle the app
            # can start before rnsd is up, and a one-shot attach would leave us
            # silently deaf to every health beacon for the whole session (the
            # 'gray after power-cycle' bug). See monitor.mesh.attach_with_retry.
            from monitor.mesh import attach_with_retry
            attach_with_retry(_attach, log=_log)

        threading.Thread(target=listen, daemon=True).start()

    _BEACON_FILE = os.path.expanduser("~/.reticulum-node-medic/beacon_targets.json")
    #: The unicast health reply (docs/HEALTH_REPLY_UNICAST.md, 2026-09-21):
    #: the medic's own reply destination, the polls still waiting for a
    #: word, and when the last poll went out (announces stay quiet after).
    _reply_dest = None
    _reply_ident = None
    _pending_polls = PendingPolls()
    _last_poll_at = 0.0
    #: Time over the mesh (same doc, 2026-09-23): the per-node ledger the
    #: node page reads its one clock line from, and the service that owns
    #: the medic's side. Both are built in build(), never at class body —
    #: a class-body TimeLedger() read the real ~/.reticulum-node-medic on
    #: import, in every test that imported this module (review).
    _time_ledger = None
    _time_service = None
    #: Persisted registry (nodes + heard-event history) — so activity accumulates
    #: across restarts. Saved every ~5 min by the monitor loop + on stop.
    _REGISTRY_FILE = os.path.expanduser("~/.reticulum-node-medic/registry.json")

    def _load_beacon_hashes(self):
        try:
            import json
            with open(self._BEACON_FILE) as f:
                return set(json.load(f))
        except Exception:
            return set()

    def _save_beacon_hashes(self, hashes):
        try:
            import json
            os.makedirs(os.path.dirname(self._BEACON_FILE), exist_ok=True)
            with open(self._BEACON_FILE, "w") as f:
                json.dump(sorted(set(hashes) | self._load_beacon_hashes()), f)
        except Exception:
            pass

    def _gather_beacon_targets(self):
        """dst_hash -> identity for every kin RTNode we can command as a
        lighthouse. Sources: live-captured announces, the registry, and a
        persisted list of nodes heard in past sessions — each identity recalled
        from RNS (works after a power-cycle and across restarts, not only right
        after a fresh announce). Newly confirmed nodes are remembered."""
        targets = dict(getattr(self, "_beacon_targets", {}))
        try:
            import RNS
            candidates = (set(self.monitor_service.registry.nodes)
                          | self._load_beacon_hashes())
            for h in candidates:
                if h in targets:
                    continue
                try:
                    ident = RNS.Identity.recall(bytes.fromhex(h))
                    if ident is None:
                        continue
                    d = RNS.Destination(ident, RNS.Destination.OUT,
                                        RNS.Destination.SINGLE, "rtnode", "health")
                    if d.hash.hex() == h:
                        targets[h] = ident
                except Exception:
                    pass
        except Exception:
            pass
        if targets:
            self._save_beacon_hashes(targets.keys())
        return targets

    def _target_names(self, targets):
        reg = self.monitor_service.registry
        # NAMES, NEVER HASHES (the keeper's rule, 2026-10-04): a nameless
        # device gets the same "RAK4631 · 5a110011" label VITALS gives it,
        # not "node 5a110011"
        names = []
        for h in targets:
            rec = reg.nodes.get(h)
            if rec is None:
                names.append(f"node {h[:8]}")
            else:
                names.append(rec.name or rec._nameless_label())
        if len(names) > 2:
            # five names made a three-line sentence on ANTENNA (2026-10-06)
            from ui.i18n import tr
            return tr("{a}, {b} and {n} more").format(a=names[0], b=names[1],
                                                       n=len(names) - 2)
        return ", ".join(names) if names else "a node"

    def _lighthouse(self, active):
        """TRIAGE beacon control, auto-called when the screen opens. active=True
        commands every known kin RTNode to transmit (a FAST ~2 s cadence — see
        _beacon_loop) so a node's antenna can be aimed against a real distant
        signal, and returns a status
        dict {state, text, names}: 'active' (a beacon is known/commanded),
        'need_power' (a kin RTNode is registered but not known to RNS), or
        'need_build' (no lighthouse RTNode exists yet). active=False stops.
        RNS-guarded, so it's a harmless no-op on a dev box."""
        if not active:
            self._lighthouse_on = False
            return {}
        targets = self._gather_beacon_targets()
        if not targets:
            # rnpath may already list a kin RTNode we haven't recalled — nudge
            # a mesh discovery once, then look again before giving up
            try:
                self.monitor_service.discover_mesh()
            except Exception:
                pass
            targets = self._gather_beacon_targets()
        if targets:
            self._lighthouse_on = True
            self._active_targets = targets
            # Generation token: re-entering TRIAGE used to start a SECOND
            # beacon thread while the first still ran, doubling LoRa airtime
            # (2026-08-01 bug hunt). Only the newest generation transmits.
            self._beacon_gen = getattr(self, "_beacon_gen", 0) + 1
            gen = self._beacon_gen
            threading.Thread(target=self._beacon_loop, args=(gen,),
                             daemon=True).start()
            names = self._target_names(targets)
            from ui.i18n import tr
            return {"state": "active", "names": names,
                    "text": tr("Beacon on - commanding {names} to transmit. Aim "
                               "the antenna and watch the triangle.").format(names=names)}
        reg = self.monitor_service.registry
        rtnodes = [r.name for r in reg.nodes.values()
                   if r.node_type == "rtnode2400" and r.provenance == "kin"
                   and r.name]
        if rtnodes:
            nm = ", ".join(rtnodes)
            from ui.i18n import tr
            return {"state": "need_power", "names": nm,
                    # This text shows ON the antenna screen itself, so it says
                    # "the medic", not the screen's own painted word
                    # (repaint 2026-09-13, docs/FRONT_PAGE_BRIEF.md).
                    "text": tr("Power on your beacon node ({nm}) so the medic can "
                               "command it to transmit for aiming.").format(nm=nm)}
        from ui.i18n import tr
        return {"state": "need_build",
                "text": tr("Aiming needs a distant RTNode to work against. Build "
                           "one to pair as your lighthouse beacon.")}

    def _beacon_loop(self, gen=None):
        import time as _t
        try:
            import RNS
        except Exception:
            return
        while (getattr(self, "_lighthouse_on", False)
               and (gen is None or gen == getattr(self, "_beacon_gen", gen))):
            for _dh, ident in list(getattr(self, "_active_targets", {}).items()):
                try:
                    dest = RNS.Destination(ident, RNS.Destination.OUT,
                                           RNS.Destination.SINGLE,
                                           "rtnode", "health")
                    RNS.Packet(dest, bytes([0x01])).send()
                except Exception:
                    pass
            # DELIBERATE fast 2 s cadence: antenna aiming needs responsive
            # feedback, and Triage is only open in short bursts, so the airtime is
            # bounded and worth it. Do NOT slow this for "economy" (operator call,
            # 2026-07-25). The bandwidth-economy ethos is honoured elsewhere.
            _t.sleep(2)

    def on_stop(self):
        try:
            ctl = getattr(self, "_control", None)
            if ctl is not None:
                ctl.stop()
        except Exception:                                              # noqa: BLE001
            pass
        try:
            self.scan_screen.end_walk()       # bank a walk the exit would lose
        except Exception:                                          # noqa: BLE001
            pass
        try:
            from monitor import busy_marker
            busy_marker.clear()               # a clean exit is not busy
        except Exception:                                          # noqa: BLE001
            pass
        self._lighthouse_on = False
        stop = getattr(self, "_monitor_stop", None)
        if stop is not None:
            stop.set()
        # final save so the last few minutes of history aren't lost on a clean exit
        try:
            self.monitor_service.registry.save(self._REGISTRY_FILE)
        except Exception:
            pass

    def _search_known_nodes(self, query):
        """Nodes the medic already knows on the mesh (kin roster + discovered),
        matching *query* by name — so 'use existing node' finds e.g. FAITH even
        though it wasn't birthed through this medic. Shaped like a certificate so
        the picker can hand it to Triage."""
        q = (query or "").strip().lower()
        if not q:
            return []
        out = []
        try:
            for rec in self.monitor_service.dashboard():
                name = getattr(rec, "name", "") or ""
                if not name or q not in name.lower():
                    continue
                cert = {"node_name": name, "_source": "mesh"}
                if getattr(rec, "dst_hash", None):
                    cert["reticulum_address"] = rec.dst_hash
                if getattr(rec, "has_location", lambda: False)():
                    cert["location"] = f"{rec.lat:.6f}, {rec.lon:.6f} (known)"
                out.append(cert)
        except Exception as e:
            print(f"[birth] known-node search failed: {e}")
        return out

    def _use_existing_node(self, cert):
        """An already-birthed node was picked in BIRTH's search — it's provisioned,
        so go to Triage to adjust its antenna where it's being mounted."""
        self._mounting_node = cert
        name = cert.get("node_name") or cert.get("hostname") or "node"
        print(f"[birth] mounting existing node: {name} -> Triage")
        self.switch_mode("triage")

    def _on_gps_confirmed(self, lat, lon, source):
        """"Use this position" — a location was confirmed on the map. Stamp it onto
        BIRTH and drop the operator into the build flow (Name the node, or search an
        existing one), where it rides onto the birth certificate."""
        self._confirmed_location = (lat, lon, source)
        # the source only — a confirmed position is a house, and ~/ui.log is
        # not a place for one (readiness ledger #162)
        print(f"[gps] location confirmed ({source}) -> BIRTH")
        bs = getattr(self, "birth_screen", None)
        if bs is not None:
            bs.set_prefill_location(lat, lon, source)
        self.switch_mode("birth")

    def _open_node_cert(self, node):
        """Tapping a node (a VITALS row dict, or a SCAN map dot passed as its name)
        opens its STORED birth certificate. If the medic never birthed it, say so
        and offer to birth it here (the health-reporting / remote-repair nudge)."""
        from ui.cert_store import search_certs
        name = node.get("name") if isinstance(node, dict) else str(node or "")
        name = (name or "").strip()
        if not name:
            return
        hits = search_certs(name)
        exact = [c for c in hits
                 if (c.get("node_name") or c.get("hostname") or "").strip().lower()
                 == name.lower()]
        cert = (exact or hits or [None])[0]
        if cert is not None:
            self._open_cert(cert)
            return
        # No stored certificate. A node tapped in VITALS/SCAN is one we HEAR on the
        # mesh — you can't plug it in, so route to OVER-THE-AIR adopt (write a cert
        # + enrol it as kin) rather than the USB birth form. Falls back to the old
        # prompt only if we can't identify it.
        ident = node.get("identity") if isinstance(node, dict) else None
        if ident:
            self._adopt_over_air_node(node, ident)
        else:
            self._no_cert_popup(name)

    def _adopt_over_air_node(self, node, ident):
        """Adopt a heard node (from a VITALS/SCAN tap) over the air — jump to the
        over-the-air confirm pre-filled with its identity + name."""
        cand = {"name": node.get("name", "") if isinstance(node, dict) else "",
                "key": ident, "identity": ident,
                "node_type": node.get("type", "rtnode2400") if isinstance(node, dict)
                else "rtnode2400",
                "provenance": node.get("provenance", "") if isinstance(node, dict) else "",
                "board": None, "firmware": None}
        g = getattr(self, "birth_guide_screen", None)
        if g is None:
            self._no_cert_popup(cand["name"])
            return
        self.switch_mode("birth_guide")
        from kivy.clock import Clock
        Clock.schedule_once(lambda *_: g._render_over_air_confirm(cand), 0)

    def _forgotten_hashes(self):
        """Hashes of deleted nodes still inside the path table's 7-day memory
        — the map must not resurrect them from stale rnpath rows."""
        try:
            import json as _json, time as _t
            p = os.path.expanduser("~/.reticulum-node-medic/forgotten.json")
            if not os.path.exists(p):
                return set()
            tombs = _json.load(open(p))
            now_t = _t.time()
            return {h for h, t in tombs.items() if now_t - t < 7 * 86400}
        except Exception:                                          # noqa: BLE001
            return set()

    def _forget_node(self, rec):
        """Delete the medic's whole memory of one node (operator, 2026-08-13).

        All of the machine's registry rows and history (placeholder and
        aspect siblings included), its certificates, its kin-roster entry,
        its beacon target. Then back to VITALS, where the row is gone. The
        node itself is never touched — if it is alive and announcing it will
        reappear as an anonymous neighbour, which is the truth.
        """
        name = rec.name or ""
        try:
            reg = self.monitor_service.registry
            # THE SAME MACHINE, by the registry's one rule — never "every row
            # with my name" computed here: for an unnamed neighbour that
            # matched every unnamed row and buried them all (2026-10-03).
            hashes = sorted(reg.machine_hashes(name or rec.dst_hash)) or [rec.dst_hash]
        except Exception:                                          # noqa: BLE001
            reg, hashes = None, [rec.dst_hash]
        # The walk anchor's keys are read BEFORE the rows go: candidate_keys
        # walks the registry's device fold, which forgets this machine the
        # moment forget_node runs below (2026-09-23).
        anchor_keys = set(hashes) | {rec.dst_hash}
        try:
            from monitor.walk_anchor import candidate_keys
            anchor_keys |= set(candidate_keys(rec, registry=reg))
            for h in hashes:
                row = reg.nodes.get(h) if reg is not None else None
                if row is not None:
                    anchor_keys |= set(candidate_keys(row, registry=reg))
        except Exception:                                          # noqa: BLE001
            pass
        removed = 0
        if reg is not None:
            try:
                removed = reg.forget_node(name or rec.dst_hash)
                self.monitor_service.registry.save(self._REGISTRY_FILE)
            except Exception:                                      # noqa: BLE001
                pass
        try:
            from ui.cert_store import delete_by_name
            removed += delete_by_name(name)
        except Exception:                                          # noqa: BLE001
            pass
        try:                              # the walk anchor: by every device key
            # Where the operator stood to start a boundary walk against
            # this node (monitor/walk_anchor.py, 2026-09-23). A reborn node
            # under the same name is a new machine in a new place — a kept
            # anchor would measure its first walk from the old one. Keys
            # only, never a name (the anchor file holds none): the tapped
            # record's device keys plus every row swept above, and each
            # row's own identity/device ids — collected before the rows went.
            from monitor.walk_anchor import forget_anchor
            forget_anchor(anchor_keys)
        except Exception:                                          # noqa: BLE001
            pass
        try:                              # kin roster: by hash and by name
            from monitor import kin_roster
            ro = kin_roster.load_roster()
            doomed = [k for k, v in ro.items()
                      if k in hashes or (isinstance(v, dict) and
                                         (v.get("name") or "").lower()
                                         == name.lower())]
            for k in doomed:
                del ro[k]
            if doomed:
                kin_roster._save(ro, kin_roster.KIN_ROSTER_PATH)
        except Exception:                                          # noqa: BLE001
            pass
        try:                              # beacon targets, memory + file
            import json as _json
            for h in hashes:
                self._beacon_targets.pop(h, None)
            if os.path.exists(self._BEACON_FILE):
                stored = _json.load(open(self._BEACON_FILE))
                kept = [h for h in stored if h not in hashes]
                _json.dump(kept, open(self._BEACON_FILE, "w"))
        except Exception:                                          # noqa: BLE001
            pass
        # TOMBSTONE for the map: RNS's path table remembers this node for up
        # to seven days, and the SCAN topology would resurrect it from stale
        # rnpath rows as an anonymous ghost. The tombstone suppresses exactly
        # that memory — a real announce heard AFTER the delete still returns
        # the node, because being heard is a sighting and a cached path is not.
        try:
            import json as _json, time as _t
            tomb_path = os.path.expanduser(
                "~/.reticulum-node-medic/forgotten.json")
            tombs = {}
            if os.path.exists(tomb_path):
                tombs = _json.load(open(tomb_path))
            now_t = _t.time()
            for h in hashes:
                tombs[h] = now_t
            tombs = {h: t for h, t in tombs.items()
                     if now_t - t < 7 * 86400}    # the path table's own lifetime
            _json.dump(tombs, open(tomb_path, "w"))
            self.monitor_service.registry.set_tombstones(tombs)
        except Exception:                                          # noqa: BLE001
            pass
        try:
            self.vitals_screen.set_nodes(self.monitor_service.dashboard_dicts())
        except Exception:                                          # noqa: BLE001
            pass
        self.switch_mode("vitals")
        print("[vitals] forgot node %r: %d records removed" % (name, removed))

    def _open_node_detail(self, node):
        """Tap a VITALS node -> its live detail (health, battery, outage-watch)
        with Probe + Certificate. Falls back to the cert/adopt flow when we have no
        live record for it."""
        import time
        ident = node.get("identity") if isinstance(node, dict) else None
        rec = self.monitor_service.registry.nodes.get(ident) if ident else None
        if rec is None:
            self._open_node_cert(node)
            return
        now = time.time()
        # The tapped row is one physical DEVICE reached via several destinations;
        # the record keyed by `ident` (the row's primary) may be the beacon-less
        # HTTP `rtnode:<name>` aspect. Consolidate so the detail's hexagon + Health
        # text read the device's ACTUAL latest health, matching the VITALS dot.
        rec = self.monitor_service.registry.consolidated_record(ident, now) or rec
        watch_line = None
        w = getattr(self, "_node_watcher", None)
        if w is not None and w.is_watching(node):
            rem = w.watch_remaining_hours(node) or 0.0
            days = max(1, round(rem / 24.0))
            from ui.i18n import tr
            watch_line = tr("Unreachable — the medic is watching it. If it's still "
                            "down in about {days} more day(s), you'll be told to go "
                            "and check it.").format(days=days)
        # activity rhythm + history insights from the persisted per-node time series
        activity_text, by_hour, insights = None, None, None
        try:
            from monitor.history import activity_profile, describe_activity, analyse
            # History accrues under the DEVICE's real mesh/beacon dest, which may
            # differ from the consolidated record's display key — resolve it.
            hist_key = (self.monitor_service.registry.probe_hash_for(ident)
                        or rec.dst_hash)
            pts = self.monitor_service.registry.history.series(hist_key)
            profile = activity_profile(pts, now, self._local_tz_offset_hours())
            activity_text = describe_activity(profile)
            by_hour = profile.get("by_hour")
            insights = analyse(pts, now)
        except Exception:
            pass
        # THE NODE'S OWN ACCOUNT OF ITS INTERFACES. Computed by the registry
        # from what the node has actually said — heard on an interface, or
        # self-reported — and passed in rather than looked up from its board
        # type. See monitor/registry.py _capabilities for why that distinction
        # is the whole point.
        caps = None
        try:
            for d in self.monitor_service.registry.devices(now=now):
                # the dashboard dict's key is "identity" — this compared a key
                # it never carried, so the Connections section never drew
                # (readiness sweep, 2026-10-03)
                if d.get("identity") == rec.dst_hash:
                    caps = d.get("capabilities")
                    break
        except Exception:                                      # noqa: BLE001
            caps = None
        # The clock line (2026-09-23): the ledger is keyed by the node's
        # health destination; the tapped row may be led by another aspect
        # of the same device, so every destination of its group is tried.
        clock_entry = None
        try:
            if self._time_service is not None:
                clock_entry = self._time_service.clock_entry_for(ident or rec.dst_hash, now)
        except Exception:                                      # noqa: BLE001
            clock_entry = None
        from ui.screens.node_detail_screen import NodeDetailScreen
        scr = self.sm.get_screen("node_detail")
        scr.clear_widgets()
        # Back returns to whatever opened this page (VITALS' list, or the map's
        # node pick) — never to home over the operator's head. Home is still
        # one tap away in the bar. Fall back to vitals when the origin is a
        # transient screen that will not exist to return to.
        origin = self.sm.current if self.sm.current not in ("node_detail", "home", "") else "vitals"
        scr.add_widget(self._with_back(back_to=origin, widget=NodeDetailScreen(
            rec, now, on_poll=self._ping_node,
            on_forget=self._forget_node, on_walk=self._start_boundary_walk,
            # PROBE's door (readiness ledger #144): the brief puts PROBE
            # behind a node in VITALS, so the node page opens it.
            on_probe=lambda rec: self.switch_mode("probe"),
            watch_line=watch_line, activity_text=activity_text, by_hour=by_hour,
            insights=insights, capabilities=caps, clock_entry=clock_entry)))
        self.switch_mode("node_detail")

    def _chat_contacts(self):
        """Addresses CHAT already knows, for Settings ▸ Notifications' picker —
        minus the medic's own address, which is never the operator's phone."""
        store = getattr(self, "chat_store", None)
        if store is None:
            return []
        try:
            own = self._chat.address if getattr(self, "_chat", None) else ""
        except Exception:                                      # noqa: BLE001
            own = ""
        try:
            return store.contacts(exclude=(own,))
        except Exception:                                      # noqa: BLE001
            return []

    def _local_tz_offset_hours(self):
        """The medic's UTC offset in hours, so 'usually active 6pm-11pm' reads in
        local time. 0.0 if it can't be determined."""
        import time as _t
        try:
            if _t.daylight and _t.localtime().tm_isdst:
                return -_t.altzone / 3600.0
            return -_t.timezone / 3600.0
        except Exception:
            return 0.0

    def _pick_node_for_walk(self):
        """ANTENNA's way in: choose WHICH node to walk against, from the ones
        the medic has actually heard lately (operator, 2026-09-19). The
        node's own VITALS page keeps its direct button — this is the other
        door, for the operator standing on a candidate site thinking about
        placement rather than about one node."""
        import time as _t
        from kivy.uix.popup import Popup
        from kivy.uix.boxlayout import BoxLayout
        from kivy.uix.button import Button
        from kivy.uix.label import Label
        from kivy.uix.scrollview import ScrollView
        from kivy.metrics import dp
        from monitor.boundary_walk import (WALK_CANDIDATE_MAX_AGE_H,
                                           WALK_PROBE_LIMIT, answers_now,
                                           walkable_nodes)
        from ui.i18n import tr
        from ui import theme
        now = _t.time()
        try:
            cands = walkable_nodes(self.monitor_service.registry, now)
        except Exception:                                          # noqa: BLE001
            cands = []
        body = BoxLayout(orientation="vertical", spacing=dp(8), padding=dp(10))
        # SAY WHAT THIS IS BEFORE ASKING THEM TO CHOOSE (operator, 2026-09-21,
        # testing it as a first-time user). Picking a node here does not open a
        # page — it moves the operator to MAPS and starts a task they perform
        # with their legs. A stranger should not discover that by arriving
        # there. It is also the one place the two names for this thing meet:
        # ANTENNA calls the button "Boundary test", everything downstream calls
        # it a boundary walk.
        intro = Label(
            text=tr("A range test finds how far a node really reaches: you "
                    "walk away from it on foot while Node Medic pings it, and "
                    "it flashes when the mesh drops. Pick the node you will "
                    "walk away from — you need to be standing at it."),
            size_hint_y=None, halign="center", valign="top", font_size="14sp",
            color=theme.hex_to_rgba(theme.COLORS["text_secondary"]))

        def _fit_intro(*_):
            # Grows to its text: this sentence is half again as long in German
            # and a fixed height would cut the last line off in silence.
            intro.text_size = (intro.width, None)
            intro.texture_update()
            intro.height = intro.texture_size[1]
        intro.bind(width=_fit_intro, text=_fit_intro)
        body.add_widget(intro)
        if not cands:
            # Honest empty state: say WHY there is nothing to offer, and what
            # would change it — never an empty list with no explanation.
            msg = Label(
                text=tr("No node has been heard in the last {h:.0f} hours, so "
                        "there is nothing to walk against yet. Power a node "
                        "(or your range probe) and wait for it to "
                        "announce.").format(h=WALK_CANDIDATE_MAX_AGE_H),
                halign="center", valign="middle",
                color=theme.hex_to_rgba(theme.COLORS["text_secondary"]))
            msg.bind(size=lambda i, v: setattr(i, "text_size", v))
            body.add_widget(msg)
        else:
            # PING NOW, DO NOT REMEMBER (operator, 2026-09-19). The registry
            # nominates; a live probe decides. Each answer appears as it
            # lands, so the operator watches the medic work instead of
            # waiting at a blank box — and a node that does not answer is
            # never offered, because walking away from a dead node teaches
            # nothing and costs a trip.
            head = Label(
                text=tr("Pinging your nodes to see which are reachable…"),
                bold=True, size_hint_y=None, height=dp(52), halign="center",
                valign="middle",
                color=theme.hex_to_rgba(theme.COLORS["accent"]))
            head.bind(size=lambda i, v: setattr(i, "text_size", v))
            body.add_widget(head)
            scroll = ScrollView()
            col = BoxLayout(orientation="vertical", size_hint_y=None,
                            spacing=dp(8))
            col.bind(minimum_height=col.setter("height"))
            scroll.add_widget(col)
            body.add_widget(scroll)

            def _offer(node):
                b = Button(text=tr("{name}  —  answering now").format(
                               name=node["name"]),
                           size_hint_y=None, height=dp(56), bold=True,
                           font_size="16sp", background_normal="",
                           background_color=theme.hex_to_rgba(
                               theme.COLORS["green"]),
                           color=theme.hex_to_rgba(theme.COLORS["background"]))
                b.bind(on_release=lambda _b, n=node: (
                    pop.dismiss(), self._walk_from_pick(n)))
                col.add_widget(b)

            def _sweep():
                import threading as _th
                live = []
                lock = _th.Lock()

                def one(node):
                    # the rule lives in monitor.boundary_walk (pure, tested);
                    # this thread only supplies the probe and the parallelism
                    confirmed = answers_now(
                        node,
                        probe=lambda d: self._mesh_reachable(d, wait=10))
                    if confirmed is None:
                        return
                    with lock:
                        live.append(confirmed)
                    Clock.schedule_once(
                        lambda _d, n=confirmed: _offer(n), 0)

                threads = [_th.Thread(target=one, args=(n,), daemon=True)
                           for n in cands[:WALK_PROBE_LIMIT]]
                for t in threads:
                    t.start()
                for t in threads:
                    t.join(timeout=25)
                checked = len(cands[:WALK_PROBE_LIMIT])

                def _done(_dt):
                    if live:
                        head.text = tr("{n} of {m} answering — pick one.").format(
                            n=len(live), m=checked)
                        head.color = theme.hex_to_rgba(theme.COLORS["green"])
                    else:
                        head.text = tr(
                            "None of your {m} nodes answered just now, so "
                            "there is nothing to walk against. Check the node "
                            "is powered and in range, then try again.").format(
                                m=checked)
                        head.color = theme.hex_to_rgba(theme.COLORS["amber"])
                Clock.schedule_once(_done, 0)

            import threading as _threading
            _threading.Thread(target=_sweep, daemon=True).start()
        close = Button(text=tr("Cancel"), size_hint_y=None, height=dp(48),
                       background_normal="",
                       background_color=theme.hex_to_rgba(
                           theme.COLORS["surface"]),
                       color=theme.hex_to_rgba(theme.COLORS["text_primary"]))
        body.add_widget(close)
        pop = Popup(title=tr("Range test"), content=body,
                    size_hint=(0.92, 0.8), auto_dismiss=True)
        close.bind(on_release=lambda *_: pop.dismiss())
        pop.open()

    def _walk_from_pick(self, node):
        """Start the walk against a picked candidate. The picker's dict names
        a destination the registry knows; the walk is handed the registry's
        OWN record for it (2026-09-23), so this door carries the same
        identity_hash / device_id as the VITALS door and the anchor lands
        under the same device key. A destination the registry has since
        forgotten (pruned between the sweep and the tap) falls back to a
        record-shaped stand-in, as before — keyed by its destination."""
        import copy
        import types
        rec = None
        try:
            rec = self.monitor_service.registry.get(node["dst_hash"])
        except Exception:                                          # noqa: BLE001
            rec = None
        if rec is None:
            rec = types.SimpleNamespace(
                dst_hash=node["dst_hash"], name=node["name"],
                lat=node.get("lat"), lon=node.get("lon"))
        elif not getattr(rec, "name", "") and node.get("name"):
            # The picker's address is the destination the node was last
            # heard speaking on, which may be a nameless aspect row of a
            # named device; the walk's header must still say the node's
            # name, not a hash prefix (operator, 2026-10-04). A copy — the
            # registry's own record is never renamed from here.
            rec = copy.copy(rec)
            rec.name = node["name"]
        self._start_boundary_walk(rec)

    def _start_boundary_walk(self, record):
        """The ONE way into a walk — both doors (a node's own VITALS page and
        ANTENNA's picker) arrive here, so a walk starts the same way whichever
        button was pressed. Node detail -> MAPS (spec 2026-08-13: fold it into
        the map view — no seventh mode).

        Nothing is pinged yet: the screen runs the two gates, node first then
        sky (see ScanScreen.begin_walk). ``_mesh_reachable`` is handed over as
        the node check rather than reimplemented there — it is the same
        primitive the picker's sweep uses, and it drops the cached path first,
        because a cached path is not a sighting.
        """
        try:
            # The PING goes to a mesh address. A VITALS row can be keyed
            # "rtnode:<name>"; pings resolve it, but the walk engine is
            # handed the hex destination outright. Rewritten the same way
            # for BOTH doors — a shallow copy, whatever shape the record
            # is (the old dataclass-only rewrite silently skipped the
            # picker's stand-in; review, 2026-09-23).
            import copy
            import time as _t
            from monitor.walk_anchor import candidate_keys
            reg = self.monitor_service.registry
            probe = reg.probe_hash_for(getattr(record, "dst_hash", "") or "")
            if probe and probe != getattr(record, "dst_hash", None):
                record = copy.copy(record)
                record.dst_hash = probe
            # The ANCHOR (and the banked evidence) are keyed by the DEVICE:
            # every key the registry's one device fold knows this machine by,
            # primary first — so the two doors converge on one anchor.
            keys = candidate_keys(record, registry=reg, now=_t.time())
            self.switch_mode("scan")
            self.scan_screen.begin_walk(
                record, self._walk_probe,
                reach_probe=lambda d: self._mesh_reachable(d, wait=10),
                anchor_keys=keys)
        except Exception as e:                                     # noqa: BLE001
            print(f"[walk] could not start: {e}", flush=True)

    def _mesh_reachable(self, dst_hash, wait=15):
        """Is there a mesh road to *dst_hash* RIGHT NOW? Blocking; call it
        off-thread. Drops the cached path first — a cached path is not a
        sighting, the project's oldest law — then asks for a fresh one and
        waits for the answer."""
        from monitor.mesh import parse_path_probe
        try:
            probe = self.monitor_service.registry.probe_hash_for(dst_hash or "")
            if not probe:
                return False
            _local_run(f"rnpath --drop {probe} 2>/dev/null")
            out = _local_run(f"rnpath -w {int(wait)} {probe} 2>/dev/null")
            if not (out or "").strip():
                return None     # the probe could not run — not "no path"
            ok, _hops = parse_path_probe(out)
            return bool(ok)
        except Exception:                                          # noqa: BLE001
            return None

    def _walk_probe(self, dst_hash, report):
        """One boundary-walk ping: drop the cached path (cached paths lie),
        request a fresh one, wait up to 15 s — inside the 20 s cadence. Same
        honesty rules as _ping_node below, without the health-pull weight:
        the walk asks ONE question, "is the mesh road there right now"."""
        import threading
        from monitor.mesh import parse_path_probe

        def work():
            import time as _t
            from monitor.mesh import path_interface
            t0 = _t.time()
            ok, hops, direct = False, None, None
            try:
                probe = self.monitor_service.registry.probe_hash_for(
                    dst_hash or "")
                if probe:
                    _local_run(f"rnpath --drop {probe} 2>/dev/null")
                    out = _local_run(f"rnpath -w 15 {probe} 2>/dev/null")
                    ok, hops = parse_path_probe(out)
                    if ok:
                        # Direct = one hop over this radio. Via the LAN or a
                        # relay is the mesh's reach, not the radio's.
                        iface = path_interface(out) or ""
                        direct = bool(hops == 1
                                      and iface.startswith("RNodeInterface"))
            except Exception:                                      # noqa: BLE001
                ok = False
            # The 20 s cadence budget was asserted, never measured (audit,
            # 2026-09-21): one line per ping so the log can say.
            print(f"[walk] probe {(dst_hash or '')[:8]} ok={bool(ok)} "
                  f"hops={hops} direct={direct} "
                  f"{int((_t.time() - t0) * 1000)}ms", flush=True)
            report(bool(ok), hops=hops, direct=direct)
        threading.Thread(target=work, daemon=True).start()

    def _setup_health_reply(self, RNS, _log):
        """The medic's reply destination for the unicast health reply
        (docs/HEALTH_REPLY_UNICAST.md, 2026-09-21). Called on EVERY
        successful attach: an rnsd restart drops the hops-0 entry that makes
        rnsd hand inbound packets to this process, and nothing else would
        put it back for ten minutes (review finding 3). Announced with empty
        app_data — a labelled persistent identity is a leak the transport
        announce does not already make."""
        import time
        try:
            if self._reply_ident is None:
                self._reply_ident = load_or_create_identity(RNS)
            ident = self._reply_ident
            dest = RNS.Destination(ident, RNS.Destination.IN,
                                   RNS.Destination.SINGLE, REPLY_APP, *REPLY_ASPECTS)
            dest.accepts_links(False)
            dest.set_packet_callback(self._on_health_reply)
            self._reply_dest = dest
            reg = self.monitor_service.registry
            reg.set_own_destinations(set(reg.own_destinations) | {dest.hash.hex()})
            reg.set_own_identities(set(reg.own_identities) | {ident.hash.hex()})
            self._announce_reply_dest(RNS, _log, why="attach")
            if not getattr(self, "_reply_keeper_started", False):
                self._reply_keeper_started = True
                threading.Thread(target=self._keep_reply_dest_announced,
                                 args=(RNS, _log), daemon=True).start()
        except Exception as e:                                     # noqa: BLE001
            _log("health reply destination NOT set up: %s" % e)

    def _announce_reply_dest(self, RNS, _log, why=""):
        dest = self._reply_dest
        if dest is None:
            return
        try:
            dest.announce()                         # empty app_data, deliberately
            _log("health reply destination %s announced (%s)" % (
                dest.hash.hex()[:8], why))
        except Exception as e:                                     # noqa: BLE001
            _log("health reply announce failed: %s" % e)

    def _reply_dest_known_to_rnsd(self, RNS):
        """The check that can fail: is our reply destination in rnsd's table
        at 0 hops? After an rnsd restart it is not, and replies would be
        dropped at the daemon. Read from rnpath, never assumed."""
        dest = self._reply_dest
        if dest is None:
            return False
        try:
            import json as _json
            out = _local_run("rnpath -t --json 2>/dev/null")
            rows = _json.loads(out or "[]")
            rows = rows if isinstance(rows, list) else rows.get("paths", [])
            mine = dest.hash.hex()
            return any(str(r.get("hash", "")).lower() == mine
                       and (r.get("hops") or 0) == 0 for r in rows)
        except Exception:                                          # noqa: BLE001
            return False

    def _keep_reply_dest_announced(self, RNS, _log):
        """Every 10 minutes — and at once whenever rnsd has forgotten us —
        but never within 60 s of a poll (our announce is the likeliest thing
        to fill a relay's announce cap just when a fallback reply needs it)."""
        import time
        last = time.time()
        while True:
            time.sleep(60)
            try:
                if not self._reply_dest_known_to_rnsd(RNS):
                    self._announce_reply_dest(RNS, _log, why="rnsd had forgotten it")
                    last = time.time()
                    continue
                if time.time() - self._last_poll_at < REPLY_ANNOUNCE_QUIET_AFTER_POLL_S:
                    continue
                if time.time() - last >= REPLY_ANNOUNCE_EVERY_S:
                    self._announce_reply_dest(RNS, _log, why="periodic")
                    last = time.time()
            except Exception:                                      # noqa: BLE001
                pass

    def _on_health_reply(self, data, packet):
        """A packet landed on the reply destination (RNS inbound thread).
        Told apart BEFORE any verification by the time service
        (docs/HEALTH_REPLY_UNICAST.md, "Time over the mesh", 2026-09-23):
        a TIME_REQ, a TIME_ACK, an ack nobody remembers and a packet too
        short to be a reply are its to answer and log; everything else is
        a health reply — verified under the named node's recalled
        identity, refused if its uptime ran backwards, ingested as the
        node's own word with source "reply", the poll it answers claimed —
        late or not — and then a TIME pushed unasked (Pi kin only, six-hour
        cadence, opportunistic: replies only come from an operator's ping)."""
        import time
        try:
            import RNS
            data = bytes(data or b"")
            if self._time_service is not None and self._time_service.inbound(data):
                return
            got = verify_reply(data, recall=RNS.Identity.recall)
            if got is None:
                self._reply_reject_log("unverifiable health reply dropped")
                return
            dest, nonce, beacon_bytes = got
            from monitor.health_beacon import decode
            beacon = decode(beacon_bytes)
            reg = self.monitor_service.registry
            now = time.time()
            dest_hex = dest.hex()
            rec = reg.nodes.get(dest_hex)
            last = rec.latest_beacon if rec is not None else None
            rebooted = bool(last is not None and getattr(last, "reset_reason", None)
                            != getattr(beacon, "reset_reason", None))
            if not uptime_is_fresh(getattr(beacon, "uptime_s", None),
                                   getattr(last, "uptime_s", None), rebooted):
                self._reply_reject_log("stale health reply refused (uptime ran backwards)")
                return
            reg.ingest(dest_hex, beacon, now, source="reply")
            if self._time_service is not None:
                self._time_service.maybe_push_time(dest)
            claimed = self._pending_polls.claim(nonce, dest)
            if claimed is None:
                return                              # fresh health, no question pending
            reg.record_probe(dest_hex, ok=True, now=now)
            after = claimed.get("answered_after_s") or 0.0
            hops = claimed.get("hops") or 1
            note = ("Answered %.0f s after the request%s — health heard by "
                    "unicast reply." % (after, " via relay (%d hops)" % hops
                                         if hops >= 2 else ""))
            if after > unicast_wait_s(hops):
                # The popup has given up; keep the sentence on the record so
                # the node's page can still say it (review finding 8).
                if rec is not None:
                    rec.late_reply_note = note
                print("[health] late reply: %s %s" % (dest_hex[:8], note), flush=True)
        except Exception as e:                                     # noqa: BLE001
            self._reply_reject_log("health reply handler error: %s" % e)

    _reply_reject_last = 0.0

    def _reply_reject_log(self, msg):
        """Rate-limited: the packet is free for a stranger to send."""
        import time
        now = time.time()
        if now - self._reply_reject_last >= 10.0:
            self._reply_reject_last = now
            print("[health] " + msg, flush=True)

    # -- time over the mesh (docs/HEALTH_REPLY_UNICAST.md, 2026-09-23) -------

    def _time_log(self, msg):
        """Every time sent, answered or acked, in ui.log AND the RNS log at
        NOTICE — the evidence a bench proof reads back (journald drops
        VERBOSE, 2026-09-22)."""
        print("[time] " + msg, flush=True)
        try:
            import RNS
            RNS.log("Node Medic time: " + msg, RNS.LOG_NOTICE)
        except Exception:                                          # noqa: BLE001
            pass

    @staticmethod
    def _rns_module():
        """The RNS module, imported when first needed (the dev box has none)."""
        import RNS
        return RNS

    def _medic_clock_disciplined(self):
        """Is this medic's own clock worth signing? GPS's last discipline
        (monitor.gps_clock stamps it) or timedatectl's NTPSynchronized —
        read NOW, on the sender thread, never assumed (monitor.medic_clock).
        A medic that could not read either refuses to sign, and says so."""
        import time
        from monitor import gps_clock
        ntp = None
        try:
            import provisioning.tool_datetime as td
            ntp = bool(td.ntp_synchronized())
        except Exception:                                          # noqa: BLE001
            ntp = None
        return _clock_disciplined(time.time(), gps_clock.last_disciplined_at, ntp)

    def _ping_node(self, dst_hash, report):
        """Live mesh reachability check. Drop the (possibly stale) cached path —
        cached paths lie — then REQUEST a fresh one and WAIT for it. The old code
        read the path table immediately after dropping, before any fresh path
        could resolve, so it reported 'not answering' for every node, healthy or
        not. rnpath -w does the request+wait; monitor.mesh.parse_path_probe reads
        the result. The 0x01 health pull then goes through
        health_poll.warm_and_send: warm THIS stack's path (a send without
        has_path drops silently, 2026-08-21), and report "answered" only when
        the listener heard the reply (the firmware throttles repeat replies,
        2026-08-22). Off-thread; reports back to the detail screen."""
        import threading
        import time
        from monitor.mesh import parse_path_probe
        from ui.i18n import tr  # i18n: wrapped — every report() sentence below

        def _ago(seconds):
            s = max(0.0, float(seconds))
            if s < 90:
                return tr("a minute ago")
            if s < 7200:
                return tr("{n} min ago").format(n=round(s / 60))
            if s < 172800:
                return tr("{h} h ago").format(h="%.1f" % (s / 3600))
            return tr("{n} days ago").format(n=round(s / 86400))

        def _silence_context(now):
            """What the medic DOES know about a node it cannot raise: when it
            last heard the node itself over the mesh (an announce or a
            beacon — a route in the path table is not a sighting), and
            whether the node answered over Wi-Fi just now (then it is on,
            and the radio road is what is missing)."""
            try:
                reg = self.monitor_service.registry
                rec = (reg.consolidated_record(dst_hash or "", now)
                       or reg.get(dst_hash or ""))
            except Exception:                                      # noqa: BLE001
                rec = None
            silent = tr("The medic is not hearing it from here — too far, in a "
                        "radio shadow, or asleep or off.")
            if rec is None:
                return silent
            spoke = []
            for attr in ("last_heard_announce_at_obs", "last_direct_obs"):
                o = getattr(rec, attr, None)
                if o is not None and getattr(o, "source", "") != "http":
                    spoke.append(o.observed_at)
            if spoke:
                parts = [tr("It last spoke over the mesh {when}.").format(
                    when=_ago(now - max(spoke)))]
            else:
                parts = [tr("The medic has never heard it speak over the mesh — "
                            "it only knew a route to it, learned from another "
                            "node.")]
            seen = getattr(rec, "seen", None)
            if (seen is not None and getattr(seen, "source", "") == "http"
                    and now - seen.observed_at < 900):
                parts.append(tr("It answered over Wi-Fi {when}, so it is on — the "
                                "medic just cannot hear it over the radio from "
                                "here.").format(when=_ago(now - seen.observed_at)))
            else:
                parts.append(silent)
            return " ".join(parts)

        def work():
            # The row's dst_hash can be a non-hex display key (an HTTP-discovery
            # 'rtnode:<name>' record leading a merged device), or — just as
            # fatal — a hex destination the node has never been heard
            # announcing on (skyfinger, 2026-10-04: "No route" to a healthy
            # node heard 90 s earlier). The registry ranks the device's
            # destinations by the node's own word; the best is asked first.
            registry = self.monitor_service.registry
            try:
                targets = [t for t in registry.probe_targets_for(dst_hash or "")
                           if t]
            except Exception:                                      # noqa: BLE001
                targets = []
            probe = targets[0] if targets else None
            if not probe:
                Clock.schedule_once(lambda dt: report(
                    tr("No mesh address on record — can't probe this one."),
                    False), 0)
                return
            _local_run(f"rnpath --drop {probe} 2>/dev/null")
            out = _local_run(f"rnpath -w 20 {probe} 2>/dev/null")
            reachable, hops = parse_path_probe(out)
            other_answered = None
            if not reachable:
                # ONE lost broadcast is not a verdict. Ask once more — the
                # same destination again and, alongside it, the device's
                # next-best one (a Pi's health reporter can be quiet while
                # its rnsd still answers; either answer is worth knowing).
                others = [t for t in targets if t != probe][:1]
                Clock.schedule_once(lambda dt: report(
                    tr("No answer to the path request in 20 s — asking once "
                       "more…"), None), 0)
                answers = {}

                def _ask(t):
                    answers[t] = parse_path_probe(
                        _local_run(f"rnpath -w 20 {t} 2>/dev/null"))

                for t in others:
                    _local_run(f"rnpath --drop {t} 2>/dev/null")
                asks = [threading.Thread(target=_ask, args=(t,), daemon=True)
                        for t in [probe] + others]
                for th in asks:
                    th.start()
                for th in asks:
                    th.join(timeout=45)
                reachable, hops = answers.get(probe, (False, None))
                if not reachable:
                    for t in others:
                        ok2, hops2 = answers.get(t, (False, None))
                        if ok2:
                            other_answered = (t, hops2)
                            break
            if reachable:
                # PING = INSTANT HEALTH READOUT (the vision, task #26): with a
                # fresh path proven at the daemon, command a health beacon
                # (0x01). Two 2026-08-21/22 field lessons keep what follows
                # honest: a packet sent while THIS process's Transport holds no
                # path dies silently (the daemon's table is not ours — warm our
                # own first), and the firmware THROTTLES repeat replies to save
                # airtime — so nothing green is claimed until the reply is
                # actually HEARD by the listener.
                hops_txt = (tr(" — {hops} hop(s) away").format(hops=hops)
                            if hops else "")
                outcome = None
                try:
                    import RNS
                    dh = bytes.fromhex(probe)
                    ident = RNS.Identity.recall(dh)
                    if ident is not None:
                        dest = RNS.Destination(ident, RNS.Destination.OUT,
                                               RNS.Destination.SINGLE,
                                               "rtnode", "health")
                        registry = self.monitor_service.registry
                        # The reply announce lands on the rtnode.health dest,
                        # which can differ from the row's probe hash — watch
                        # both records for freshness.
                        watch = {probe, dest.hash.hex()}
                        sent_at = [None]
                        nonce = [None]

                        def _send(_d):
                            # TWO PHASES (docs/HEALTH_REPLY_UNICAST.md).
                            # First "speak to me": 0x04 + our reply
                            # destination + a nonce, answered by a signed
                            # unicast packet routed back through the mesh —
                            # the reply a medic that cannot hear this node
                            # directly can still receive. The 0x01 fallback
                            # is sent by _heard only after W1, never sooner:
                            # the node's 30 s rate limiter would swallow it.
                            sent_at[0] = time.time()
                            self._last_poll_at = sent_at[0]
                            rd = self._reply_dest
                            if rd is not None:
                                req = build_request_to(bytes(rd.hash))
                                nonce[0] = req[-8:]
                                self._pending_polls.add(
                                    nonce[0], bytes(dest.hash), hops)
                                RNS.Packet(dest, req).send()
                            else:
                                RNS.Packet(dest, build_request()).send()
                            Clock.schedule_once(lambda dt: report(
                                tr("Path found{hops}. Health requested — waiting "
                                   "for the reply…").format(hops=hops_txt),
                                None), 0)

                        def _answered_unicast():
                            return (nonce[0] is not None
                                    and not self._pending_polls.is_pending(nonce[0]))

                        def _heard(_d, _wait_s):
                            # Phase 1: a verified unicast reply claims the
                            # nonce, OR a fresh announce stamps
                            # last_heard_announce_at (either is the node's
                            # own word). Phase 2: the old request, waited
                            # for as an announce with time for each relay
                            # to rebroadcast.
                            w1 = unicast_wait_s(hops)
                            deadline = time.monotonic() + w1
                            while time.monotonic() < deadline:
                                if _answered_unicast():
                                    return True
                                if heard_since(registry.nodes.get, watch,
                                               sent_at[0], 1.0):
                                    return True
                            if _answered_unicast():
                                return True
                            if nonce[0] is None:
                                return False        # already sent 0x01
                            Clock.schedule_once(lambda dt: report(
                                tr("No unicast reply in {s} s — asking it to "
                                   "announce instead…").format(s="%.0f" % w1),
                                None), 0)
                            RNS.Packet(dest, build_fallback_request()).send()
                            w2 = announce_wait_s(hops)
                            if heard_since(registry.nodes.get, watch,
                                           sent_at[0], w2):
                                return True
                            return _answered_unicast()

                        outcome = warm_and_send(
                            dh, RNS.Transport.has_path,
                            RNS.Transport.request_path, _send, _heard)
                except Exception:
                    outcome = None
                if outcome == DELIVERY_ANSWERED:
                    self.monitor_service.registry.record_probe(
                        probe, ok=True, now=time.time())
                    # PER-LINK SNR MARGIN (operator, 2026-08-27): the factor
                    # that matters most for the node's antenna — the RF of the
                    # reply itself, as the medic's own radio heard it. The
                    # splitter stamps every heard packet; anything heard since
                    # sent_at is the reply (or its last hop — still the RF
                    # arriving from that direction). No packet -> no claim.
                    from monitor.link_margin import link_margin
                    lm = link_margin(sent_at[0]) if sent_at[0] else None
                    sig = ""
                    if lm is not None:
                        words = {
                            "strong": tr("strong link"),
                            "ok": tr("workable link"),
                            "thin": tr("thin link — antenna or placement "
                                       "deserves a look"),
                            "edge": tr("AT THE EDGE — barely decoding; expect "
                                       "drop-outs"),
                        }
                        parts = ["%d dBm" % lm.rssi_dbm]
                        if lm.snr_db is not None:
                            parts.append("SNR %g dB" % lm.snr_db)
                        if lm.headroom_db is not None:
                            parts.append(tr("{db} dB headroom").format(
                                db="%g" % lm.headroom_db))
                        # Relayed: the RF the medic heard is the RELAY's
                        # transmission, not this node's — say so, or the
                        # operator re-aims the wrong antenna (review, 2026-09-21).
                        whose = (tr("Signal of the relay's transmission as heard "
                                    "by the medic: ") if (hops or 1) >= 2
                                 else tr("Signal as heard by the medic: "))
                        sig = ("\n" + whose + ", ".join(parts) + " — "
                               + words[lm.verdict] + ".")
                    Clock.schedule_once(lambda dt: report(
                        tr("Answered{hops}. Fresh health heard — this page shows "
                           "the new readings when you open it again.{sig}"
                           ).format(hops=hops_txt, sig=sig),
                        True), 0)
                elif outcome == DELIVERY_NO_ROUTE:
                    # rnpath saw a path but OUR stack never resolved one, so
                    # the request was NEVER sent — sending anyway would have
                    # been a silent ephemeral drop (2026-08-21). Worded apart
                    # from "did not answer", and the node's record is left
                    # untouched: nothing reached the node, so this measured
                    # the medic's own stack, not the node.
                    Clock.schedule_once(lambda dt: report(
                        tr("No route from this medic's own stack right now — "
                           "the health request was not sent, so this says "
                           "nothing new about the node."), False), 0)
                elif outcome is None:
                    # Couldn't attempt the 0x01 at all (no RNS lib on a dev
                    # box, or no identity on record for the health dest). The
                    # drop-then-rewait rnpath probe above is still direct
                    # evidence of a live path — say exactly that much and no
                    # more ("couldn't check" is its own answer).
                    self.monitor_service.registry.record_probe(
                        probe, ok=True, now=time.time())
                    Clock.schedule_once(lambda dt: report(
                        tr("Reachable now{hops}. Couldn't request health from it "
                           "(no health destination on record).").format(
                               hops=hops_txt),
                        True), 0)
                elif outcome == DELIVERY_UNANSWERED:
                    # Sent, then silence. NOT proof it is down: the firmware
                    # throttles repeat poll replies (2026-08-22), so a healthy
                    # node polled again soon goes quiet on purpose. Recorded
                    # as an unanswered probe all the same — amber, not a
                    # verdict, until the node itself speaks.
                    self.monitor_service.registry.record_probe(
                        probe, ok=False, now=time.time())
                    if hops and hops >= 3:
                        # Three hops or more is a road through OTHER nodes;
                        # the two-hop bench hint below (a radio within a
                        # couple of metres) is not a sensible thing to say
                        # about it (ELSEWHERE at 3 hops, 2026-10-04).
                        why = tr("Health requested, but the medic did not hear "
                                 "a reply. The road to it runs through {n} other "
                                 "nodes ({hops} hops), so the medic is not hearing "
                                 "this node directly and the reply had to come "
                                 "back the same way — a relay may have dropped "
                                 "or delayed it. Not proof it is down. Amber "
                                 "until it is heard.").format(n=hops - 1, hops=hops)
                    elif hops and hops >= 2:
                        # The road ran through a relay and the reply never
                        # reached us: the medic is not hearing this node
                        # DIRECTLY. Too far — or too close: on the bench
                        # (2026-09-21, ROOFRAK a foot from the medic) a radio
                        # that near is too loud to decode, and the mesh
                        # routed around the link via another node.
                        why = tr("Health requested, but the medic did not hear "
                                 "a reply. The road to it runs through another "
                                 "node ({hops} hops), so the medic is not hearing "
                                 "this node directly — too far, or too close: a "
                                 "radio within a couple of metres is too loud to "
                                 "decode. Move it a few metres away and ping once "
                                 "more. Amber until it is heard.").format(hops=hops)
                    else:
                        why = tr("Health requested, but it did not answer. Not "
                                 "proof it is down — nodes throttle repeat "
                                 "replies to save airtime. Amber until it is "
                                 "heard again.")
                    Clock.schedule_once(lambda dt: report(why, False), 0)
                else:
                    # A value warm_and_send does not return today. Matched
                    # EXPLICITLY above so a future fourth outcome lands here
                    # and is refused a verdict, instead of silently reading
                    # as "did not answer". Nothing is recorded.
                    Clock.schedule_once(lambda dt: report(
                        tr("Ping finished with an unrecognised result ({result}) "
                           "— a Node Medic bug, not a node state.").format(
                               result=repr(outcome)),
                        None), 0)
            elif other_answered is not None:
                # The node's OTHER destination answered — the machine is on
                # the mesh, but the address its health beacons come from did
                # not raise a path, so a health request would go nowhere (a
                # Pi's reporter keeps its own identity). Said as exactly
                # that; the answering destination is recorded as live.
                other, other_hops = other_answered
                registry.record_probe(other, ok=True, now=time.time())
                where = (tr(" ({hops} hop(s) away)").format(hops=other_hops)
                         if other_hops else "")
                Clock.schedule_once(lambda dt: report(
                    tr("Reachable — its other mesh address answered{where}, but "
                       "the address its health beacons come from did not, so no "
                       "health was requested. The node is up; its health "
                       "reporter may be quiet, or that address is not announced "
                       "where the medic can hear it.").format(where=where),
                    True), 0)
            else:
                # The registry hears about the SILENCE too — an unanswered
                # probe is the freshest evidence there is, and it demotes the
                # node's green face to amber until the node itself speaks
                # (seed: powered off but green, 2026-08-20). What the medic
                # DOES know is said beside the silence: on 2026-10-04 a node
                # heard 90 s earlier was called "off".
                now = time.time()
                registry.record_probe(probe, ok=False, now=now)
                context = _silence_context(now)
                Clock.schedule_once(lambda dt: report(
                    tr("No road to it over the mesh right now: two path requests "
                       "in 40 s went unanswered, so no health request was sent. "
                       "{context} Amber until it is heard again.").format(
                           context=context), False), 0)

        threading.Thread(target=work, daemon=True).start()

    def _open_cert(self, cert):
        from ui.screens.cert_view_screen import CertViewScreen
        scr = self.sm.get_screen("cert_view")
        scr.clear_widgets()
        scr.add_widget(self._with_back(
            CertViewScreen(cert, on_show_location=self._show_node_on_map,
                           on_triage=self._use_existing_node,
                           on_edit_location=self._edit_cert_location)))
        self.switch_mode("cert_view")

    def _edit_cert_location(self, cert):
        """Fix / set a node's location from its cert — opens the map placement popup
        and writes the confirmed spot back to the cert AND the kin roster (so a
        wrong pin, e.g. from a failed address lookup during adoption, is fixable)."""
        from ui.screens.cert_view_screen import cert_latlon
        from ui.widgets.confirm_location import ConfirmLocationPopup
        from monitor.geo import splitter_gps_reader
        ll = cert_latlon(cert)
        if ll is None:
            try:
                from monitor.geo import read_gps
                f = read_gps()
                ll = (f.lat, f.lon) if f else None
            except Exception:
                ll = None
        start_note = ""
        if ll is None:
            from monitor.geo import default_map_centre
            ll = default_map_centre(getattr(self.monitor_service, "registry", None))
        if ll is None:
            # nothing known: the picker starts at 0,0 and SAYS why — never the
            # developer's city (readiness ledger #113)
            ll = (0.0, 0.0)
            start_note = NO_FIX_NOTE

        def _ok(lat, lon):
            cert["location"] = f"{lat:.6f}, {lon:.6f} (confirmed)"
            try:
                from ui.cert_store import save_cert
                cert["_id"] = save_cert(cert)
            except Exception:
                pass
            try:
                from monitor import kin_roster
                h = cert.get("identity_hash") or cert.get("reticulum_address")
                if h:
                    kin_roster.set_location(h, lat, lon)
                    self.monitor_service.registry.set_kin_roster(
                        kin_roster.load_roster())
            except Exception:
                pass
            self._open_cert(cert)              # reopen so the new location shows

        ConfirmLocationPopup(ll[0], ll[1], start_note=start_note, node_name=cert.get("node_name", ""),
                             on_confirm=_ok, on_cancel=lambda: None,
                             gps_reader=splitter_gps_reader()).open()

    def _show_node_on_map(self, lat, lon, name=""):
        """"See on map" from a certificate — open SCAN centred on the node so the
        operator sees it in context (its own status dot is already drawn there),
        with the medic's live position for navigation reference."""
        sc = getattr(self, "scan_screen", None)
        if sc is not None:
            sc.show_location(lat, lon)
        self.switch_mode("scan")

    def _no_cert_popup(self, name):
        """No stored certificate — it wasn't birthed by this medic. Offer to birth
        it here so it reports health back and can be repaired remotely."""
        from kivy.uix.boxlayout import BoxLayout
        from kivy.uix.button import Button
        from kivy.uix.label import Label
        from kivy.uix.popup import Popup
        if getattr(self, "_active_popup", None) is not None:
            return                              # never stack a second prompt
        box = BoxLayout(orientation="vertical", spacing=dp(10), padding=dp(12))
        msg = Label(text=(f"No certificate stored for \"{name}\".\n\nThis node wasn't "
                          "birthed by this Node Medic, so there's nothing saved to "
                          "open. Birth it here and it will report health back and "
                          "become remotely repairable."),
                    halign="center", valign="middle")
        msg.bind(size=lambda i, v: setattr(i, "text_size", v))
        box.add_widget(msg)
        row = BoxLayout(orientation="horizontal", size_hint_y=None, height=dp(52),
                        spacing=dp(8))
        popup = Popup(title="Not birthed here", content=box,
                      size_hint=(0.86, 0.5))
        self._active_popup = popup
        popup.bind(on_dismiss=lambda *_: setattr(self, "_active_popup", None))
        close = Button(text="Close", background_normal="",
                       background_color=theme.hex_to_rgba(theme.COLORS["surface"]))
        close.bind(on_release=lambda *_: popup.dismiss())
        birth = Button(text="Birth it here", background_normal="", bold=True,
                       background_color=theme.hex_to_rgba(theme.COLORS["accent"]),
                       color=theme.hex_to_rgba(theme.COLORS["background"]))

        def _go_birth(*_):
            # Dismiss NOW, then switch a frame later. Doing the heavy screen switch
            # synchronously in the same touch left the popup lingering until a second
            # tap — deferring lets the modal fully tear down first.
            popup.dismiss()
            from kivy.clock import Clock
            Clock.schedule_once(lambda dt: self._enter_birth_named(name), 0)
        birth.bind(on_release=_go_birth)
        row.add_widget(close)
        row.add_widget(birth)
        box.add_widget(row)
        popup.open()

    def _enter_birth_named(self, name):
        bs = getattr(self, "birth_screen", None)
        if bs is not None and hasattr(bs, "prefill_name"):
            bs.prefill_name(name)
        self.switch_mode("birth")

    def _home_select(self, mode):
        """Front-page card dispatch. BIRTH lands straight on the 'what are you
        building?' chooser (the old name/detect intro screen is skipped — that
        work still happens after a build kind is chosen). Everything else opens
        its mode directly."""
        if mode == "birth":
            self._open_birth_guide()
        else:
            self.switch_mode(mode)

    # -- home / backpack network-role toggle --------------------------------
    def _refresh_node_mode(self):
        """Read the medic's actual mode (home/backpack) off-thread and set the
        front-page toggle to match — so it's right after a restart."""
        import threading
        from transport.connection import LocalConnection
        from workflows.node_mode import current_mode

        def work():
            try:
                m = current_mode(LocalConnection())
            except Exception:
                # un-dim the toggle even when the mode cannot be read (#40)
                Clock.schedule_once(
                    lambda dt: self.home_screen.mode_toggle.set_busy(False), 0)
                return
            Clock.schedule_once(
                lambda dt: self.home_screen.mode_toggle.set_state(m), 0)
        threading.Thread(target=work, daemon=True).start()

    def _set_node_mode(self, new_mode, auto=False):
        """Flip the medic between HOME (propagation node) and BACKPACK (mobile
        leaf). Restarts rnsd/lxmd, so it runs off the UI thread; the toggle shows
        '…' while it works, then settles to the new state with a brief toast.

        ``auto`` marks a switch the medic made itself (movement detected) — it gets
        a movement-specific toast. A MANUAL switch re-settles the movement anchor at
        the current spot, so auto-detect measures the next trip from here and won't
        immediately fight a deliberate choice."""
        tog = self.home_screen.mode_toggle
        tog.set_busy(True)
        if not auto:
            self._reanchor_movement()
        import threading
        from transport.connection import LocalConnection
        from workflows.node_mode import set_mode

        prev = getattr(tog, "mode", None)

        def work():
            try:
                res = set_mode(new_mode, LocalConnection())
            except Exception as e:                                 # noqa: BLE001
                # the toggle must never stay dimmed and deaf (ledger #40)
                def failed(_dt):
                    tog.set_busy(False)
                    if prev:
                        tog.set_state(prev)
                    try:
                        self._mode_toast(f"Couldn't switch: {e}", ok=False)
                    except Exception:                              # noqa: BLE001
                        pass
                Clock.schedule_once(failed, 0)
                return

            def done(dt):
                if res.ok:
                    tog.set_state(res.mode)
                else:
                    # a failed switch must not light the mode it failed to
                    # reach: show what the medic is actually in (2026-10-03)
                    self._refresh_node_mode()
                msg = res.message
                if auto and res.ok and res.mode == "backpack":
                    msg = ("On the move — switched to Backpack automatically so this "
                           "node won't disturb the mesh while it travels. Tap the "
                           "home icon to resume Home mode once you've settled.")
                self._mode_toast(msg, ok=res.ok, mode=res.mode if res.ok else None)
            Clock.schedule_once(done, 0)
        threading.Thread(target=work, daemon=True).start()

    def _check_movement(self):
        """Runs on the monitor thread each cycle: if auto-backpack is on and the
        medic's GPS shows it has moved off its settled spot, drop it to backpack.
        One-way — it never auto-returns to Home (that stays a deliberate choice).
        No-ops silently with no GPS fix (can't sense movement without one)."""
        det = getattr(self, "_movement", None)
        if det is None:
            return
        scan = getattr(self, "scan_screen", None)
        if (getattr(scan, "_walk_session", None) is not None
                or getattr(scan, "_walk_gate", None) is not None):
            # A walk IS movement. Switching to Backpack restarts rnsd, and
            # the medic's own outage would be banked as the node's edge
            # 150 m out (audit, 2026-09-21).
            return
        try:
            from workflows.node_mode import load_auto_backpack
            if not load_auto_backpack():
                return
            from monitor.geo import classify_fix, read_splitter_fix
            fix = read_splitter_fix()
            if fix is None or fix.lat is None or fix.lon is None:
                return
            # A HELD fix is the receiver COASTING on an old lock: the position
            # is frozen, not measured. Feeding that to a movement detector is
            # worse than feeding it nothing — a frozen reading looks like
            # "definitely stationary" while the medic could be in a car, and the
            # re-acquire afterwards lands as one enormous jump that reads as
            # movement nothing actually observed.
            # Proven live 2026-08-07: patch turned to face the ground, 10 sats
            # -> 0 inside a minute, fix stayed 1, position kept being served.
            if classify_fix(fix) != "live":
                return
            if det.update(fix.lat, fix.lon):
                Clock.schedule_once(lambda dt: self._auto_backpack(), 0)
        except Exception:
            pass

    def _auto_backpack(self):
        """Movement confirmed → switch to Backpack (UI thread), unless already there.
        Only ever home→backpack, so a unit that's already a mobile leaf is untouched."""
        tog = getattr(self.home_screen, "mode_toggle", None)
        if tog is None or tog.mode == "backpack":
            return
        self._set_node_mode("backpack", auto=True)

    def _reanchor_movement(self):
        """Re-settle the movement anchor at the current fix (on a manual mode
        change), so auto-detect measures the next trip from here."""
        det = getattr(self, "_movement", None)
        if det is None:
            return
        try:
            from monitor.geo import classify_fix, read_splitter_fix
            fix = read_splitter_fix()
            # Only a MEASURED position may become the anchor. Anchoring on a
            # coasting fix pins the trip's origin to wherever the receiver last
            # saw sky, which may be streets away from here.
            if (fix is not None and fix.lat is not None and fix.lon is not None
                    and classify_fix(fix) == "live"):
                det.reset(fix.lat, fix.lon)
            else:
                det.reset()
        except Exception:
            pass

    def _check_battery(self):
        """Runs on the monitor thread: read the UPS (if a HAT is present), update
        the home-page gauge, and act on the safe-shutdown guard. No-op when there's
        no UPS (read_ups -> present=False). Voltage-gated, so it can't fire while
        plugged in (see monitor.ups)."""
        guard = getattr(self, "_battery_guard", None)
        if guard is None or self._battery_shutting_down:
            return
        try:
            from monitor.ups import read_ups
            st = read_ups()
            Clock.schedule_once(lambda dt: self._update_battery_ui(st), 0)
            action = guard.evaluate(st)
            if action == "warn":
                Clock.schedule_once(lambda dt: self._mode_toast(
                    "Battery low — plug Node Medic in soon, or it will shut down "
                    "safely to protect the SD card.", ok=False), 0)
            elif action == "shutdown":
                self._battery_shutting_down = True
                Clock.schedule_once(lambda dt: self._battery_shutdown(), 0)
        except Exception:
            pass

    def _update_battery_ui(self, st):
        gauge = getattr(getattr(self, "home_screen", None), "battery_gauge", None)
        if gauge is not None:
            gauge.update(st)

    def _battery_shutdown(self):
        """Critically-low battery: warn, then clean-power-off to save the SD card."""
        self._mode_toast("Battery critically low — shutting down now to protect "
                          "the SD card.", ok=False)
        import threading
        from provisioning.power import power_off
        threading.Thread(target=lambda: power_off(), daemon=True).start()

    def _start_dual_supply_watch(self):
        """A 5 s tripwire thread, separate from the 30 s monitor lap because a
        keeper plugging USB-C power into a running HAT-powered medic deserves a
        warning in seconds, not minutes. Inert without the HAT (see
        monitor.dual_supply arming rule)."""
        import threading

        def loop():
            import time
            while True:
                try:
                    from monitor.dual_supply import read_ext5v
                    from monitor.ups import read_ups
                    verdict = self._dual_guard.evaluate(
                        read_ups().present, read_ext5v())
                    if verdict == "danger":
                        Clock.schedule_once(lambda dt: self._dual_supply_alarm(), 0)
                    elif verdict == "clear":
                        Clock.schedule_once(lambda dt: self._dual_supply_clear(), 0)
                except Exception:
                    pass
                time.sleep(5)

        threading.Thread(target=loop, daemon=True).start()

    def _dual_supply_alarm(self):
        """TWO SUPPLIES: flash red, beg for the cable, power down if ignored.
        The countdown lives on the UI clock; the tripwire thread keeps
        re-reading the PMIC, and the modal dismisses itself the moment the
        second supply vanishes (guard "clear")."""
        if self._dual_alarm is not None or getattr(self, "_battery_shutting_down", False):
            return
        from kivy.uix.modalview import ModalView
        from kivy.uix.boxlayout import BoxLayout
        from kivy.uix.label import Label
        from monitor.dual_supply import GRACE_SECONDS
        view = ModalView(size_hint=(1, 1), auto_dismiss=False,
                         background="", background_color=(0.55, 0.05, 0.05, 1))
        box = BoxLayout(orientation="vertical", padding=dp(28), spacing=dp(12))
        title = Label(text="TWO POWER SUPPLIES", font_size="34sp", bold=True,
                      color=(1, 1, 1, 1))
        body = Label(
            text=("This Node Medic is running on its battery HAT and power "
                  "just arrived on the USB-C socket. Two supplies fight each "
                  "other and can damage both machines.\n\n"
                  "UNPLUG THE USB-C CABLE NOW."),
            font_size="20sp", color=(1, 1, 1, 1), halign="center")
        body.bind(size=lambda w, sz: setattr(w, "text_size", (sz[0], None)))
        count = Label(text="", font_size="26sp", bold=True, color=(1, 0.85, 0.3, 1))
        box.add_widget(title); box.add_widget(body); box.add_widget(count)
        view.add_widget(box)
        self._dual_alarm = view
        view.open()
        remaining = [GRACE_SECONDS]

        def tick(dt):
            if self._dual_alarm is not view:      # cleared / superseded
                return False
            # flash: alternate the red each second so it reads as an alarm
            view.background_color = ((0.75, 0.08, 0.08, 1)
                                     if remaining[0] % 2 else (0.45, 0.02, 0.02, 1))
            count.text = ("Shutting down in %d s to protect the boards"
                          % remaining[0])
            if remaining[0] <= 0:
                self._dual_alarm = None
                view.dismiss()
                self._battery_shutting_down = True
                self._mode_toast("Two power supplies — shutting down safely.",
                                 ok=False)
                import threading
                from provisioning.power import power_off
                threading.Thread(target=lambda: power_off(), daemon=True).start()
                return False
            remaining[0] -= 1
            return True
        Clock.schedule_interval(tick, 1)

    def _dual_supply_clear(self):
        view = self._dual_alarm
        if view is None:
            return
        self._dual_alarm = None
        view.dismiss()
        self._mode_toast("Second supply removed — all safe.", ok=True)

    def _check_node_watch(self, devices):
        """Runs on the monitor thread each cycle: escalate any node that has stayed
        continuously unreachable past its grace window (solar recharge grace).
        Autonomous — no user action; state persists across restarts."""
        w = getattr(self, "_node_watcher", None)
        if w is None or not devices:
            return
        try:
            down = list(w.tick(devices))
        except Exception:
            return
        if down:
            # ONE notice for everything that crossed the line this tick, held
            # until tapped — not a 4-second toast per node (ledger #138)
            Clock.schedule_once(lambda dt, ds=down: self._escalate_nodes(ds), 0)

    def _check_gps_clock(self):
        """Runs on the monitor thread each cycle: discipline the system clock from
        the Tracker's satellite UTC. The Pi 5's RTC is NOT battery-backed and a
        field medic has no NTP, so a good GPS fix is the offline time authority.

        INERT until the firmware ships GPS_CMD_UTC (0x03): old firmware never sends
        it, so gps_utc stays None and we return immediately — a safe no-op.

        Guards, in order (each honours a house rule or a review finding):
          * flash/SD-write in progress -> stand down (never disturb a busy medic).
          * operator turned auto-sync OFF -> stand down (manual control is theirs).
          * GpsClockDisciplinarian applies the fixed-window + median-ring
            corroboration policy; it refuses weak/stale/out-of-window/uncorroborated
            fixes, and surfaces WHY via its status line.
        On a MEANINGFUL step (delta beyond the SEEN deadband) we REBASE every
        stored wall-clock stamp by the same delta (so nodes don't look silent for
        the step size) and give node_watch a post-step cooldown; a sub-deadband
        correction just sets the clock (no rebase / no config churn). The
        datetime sync stamp is persisted rate-limited. Everything is wrapped so no
        failure can kill the loop."""
        try:
            # Never step the clock mid-flash / mid-SD-write (matches the
            # board-disconnect watcher's guard on the same flag).
            if self.flash_in_progress():
                return
            import provisioning.tool_datetime as td
            # The operator can turn auto-sync OFF ("set by hand") on the datetime
            # screen; GPS must then stand down or the screen would be lying.
            if not td.is_autosync():
                return
            from monitor.geo import read_splitter_state
            from monitor.gps_clock import (apply_clock, rebase_needed,
                                           PERSIST_MIN_INTERVAL_S,
                                           DECISION_STEP, DECISION_CONFIRM)
            st = read_splitter_state()
            import time
            sys_now = time.time()
            if not st:
                from monitor.gps_clock import REASON_NO_FIX
                self._gps_disciplinarian.last_reason = REASON_NO_FIX
                return
            # Even when gps_utc is None (inert, pre-firmware), let evaluate set the
            # status ("No GPS fix yet") so the screen is honest; it returns None.
            ntp = td.ntp_synchronized()           # "couldn't check" -> False -> GPS may act
            decision = self._gps_disciplinarian.evaluate(
                st.get("gps_utc"), st.get("gps_utc_recv"),
                st.get("sats"), st.get("fix"),
                sys_now=sys_now, ntp_synced=ntp, recv_now=sys_now)
            if decision.kind == DECISION_CONFIRM:
                # GPS has a SOLID, FRESH fix that AGREES with the clock — the
                # normal steady state. Record a CONFIRMATION so the operator can
                # tell "GPS checked, clock is right" from "GPS never worked"
                # (proven live 2026-08-22: an 8-sat fix with an already-correct
                # clock left datetime.json empty, so the screen read never-synced).
                # NO step, NO rebase; the disciplinarian already rate-limited this
                # to at most once per PERSIST_MIN_INTERVAL_S, so no config churn.
                td.mark_synced(decision.target, "GPS")
                # GPS is holding the clock, so NTP is not the source — clear any
                # "using internet time" status left by an earlier no-sky spell.
                self._gps_disciplinarian.ntp_fallback = False
                self._gps_disciplinarian.ntp_synced = False
                return
            if decision.kind != DECISION_STEP:
                # GPS did NOT act this tick (no fix / stale / awaiting / etc). If
                # it genuinely CAN'T help, restore NTP as the fallback so an indoors
                # medic stops drifting once a network returns. No-op in every other
                # case (see _maybe_restore_ntp / ntp_fallback_decision).
                self._maybe_restore_ntp(td, gps_acted=False)
                return
            target = decision.target
            # argv runner matching the scoped sudoers (NM_CLOCK). subprocess with a
            # LIST (no shell) so nothing is re-parsed; sudo -n never prompts.
            def _run(argv):
                p = subprocess.run(argv, capture_output=True, text=True, timeout=15)
                return p.returncode, p.stdout, p.stderr
            if apply_clock(target, _run):
                # apply_clock just disabled NTP (GPS is authority) — GPS is the
                # source now, so the fallback status no longer applies.
                self._gps_disciplinarian.ntp_fallback = False
                self._gps_disciplinarian.ntp_synced = False
                delta = target - sys_now          # new_epoch - old_epoch
                # GATE the expensive rebase + persist on a meaningful delta. A
                # sub-deadband correction is absorbed by the SEEN deadband and
                # node_watch cooldown, so it needs no history rebase and no config
                # churn — stops a small-step / alternating-±59s path from thrashing
                # the SD. Above the deadband, rebase FIRST, then commit as clean;
                # if rebase somehow fails, do NOT record_success/mark_synced.
                if rebase_needed(delta):
                    try:
                        self.monitor_service.registry.rebase_wall_clock(delta)
                    except Exception:
                        return
                    w = getattr(self, "_node_watcher", None)
                    if w is not None:
                        w.note_clock_step()       # don't mass-escalate on the step
                self._gps_disciplinarian.record_success(target)
                # Persist the "GPS-synced" stamp rate-limited: always on a
                # meaningful step, otherwise at most once per interval, so a
                # frequently-confirmed clock doesn't fsync every tick.
                last_persist = getattr(self, "_gps_last_persist", 0.0)
                if rebase_needed(delta) or (target - last_persist) >= PERSIST_MIN_INTERVAL_S:
                    td.mark_synced(target, "GPS")
                    self._gps_last_persist = target
            else:
                self._gps_disciplinarian.record_failure(sys_now)
        except Exception:
            pass

    def _maybe_restore_ntp(self, td, gps_acted):
        """The ONLINE-DETECTOR half of the clock discipline (called from the same
        monitor tick as _check_gps_clock, only when GPS did NOT set the clock).

        GPS discipline turns NTP OFF so satellite UTC is the sole authority offline
        — correct in the field, but it means a medic that GPS-synced once and now
        sits INDOORS (no sky -> no GPS) with WiFi back would keep NTP off and slowly
        drift, having discarded the one time source it can now reach. This restores
        NTP (re-enables timesyncd) whenever GPS can't help, and lets timesyncd
        itself decide reachability — there is NO network probe (an offline-first
        field device must not phone home every tick, and enabling NTP is harmless
        offline: it just coasts and syncs when a network returns).

        Stable against the GPS toggle because the two fire on OPPOSITE conditions
        (GPS present -> NTP off; GPS absent -> NTP on), and each fires only when the
        state actually needs changing — so no thrash. Everything is wrapped by
        _check_gps_clock's try/except, so a failed toggle can't break the loop."""
        from monitor.gps_clock import ntp_fallback_decision, enable_ntp
        ntp_on = td.ntp_enabled()
        gps_fresh = not self._gps_disciplinarian.gps_absent()
        if ntp_fallback_decision(
                autosync=True,                   # caller already gated on is_autosync
                ntp_enabled=ntp_on,
                gps_fresh=gps_fresh,
                gps_acted=gps_acted):
            def _run(argv):
                p = subprocess.run(argv, capture_output=True, text=True, timeout=15)
                return p.returncode, p.stdout, p.stderr
            if enable_ntp(_run):
                ntp_on = True
        # HONEST status. Fallback applies only while GPS is genuinely absent AND
        # NTP is on to cover it. Then distinguish "on but still coasting" from
        # "actually synced": ntp_synchronized() is the truth — the screen must not
        # claim internet time before timesyncd has really reached a server (a
        # captive portal may never let it), mirroring the CONFIRM branch's rule of
        # never claiming a sync we don't have.
        if ntp_on and not gps_fresh:
            self._gps_disciplinarian.ntp_fallback = True
            self._gps_disciplinarian.ntp_synced = td.ntp_synchronized()
        else:
            self._gps_disciplinarian.ntp_fallback = False
            self._gps_disciplinarian.ntp_synced = False

    def _escalate_nodes(self, devices):
        """Several nodes crossed their grace line on one tick: one notice that
        stays on screen until tapped, naming each with its real silence; the
        operator alert still goes per node."""
        lines = [self._outage_sentence(d) for d in devices]
        if not lines:
            return
        self._outage_notice(lines)
        for line in lines:
            self._push_outage_alert(line)

    def _escalate_node(self, device):
        """One node — the same notice and push as a batch of one."""
        self._escalate_nodes([device])

    def _outage_notice(self, lines):
        from kivy.uix.popup import Popup
        from kivy.uix.label import Label
        lbl = Label(text="\n\n".join(lines), halign="center", valign="middle",
                    padding=(dp(16), dp(16)))
        lbl.bind(size=lambda i, v: setattr(i, "text_size", v))
        p = Popup(title="Node down" if len(lines) == 1 else f"{len(lines)} nodes down",
                  content=lbl, size_hint=(0.86, 0.42 if len(lines) == 1 else 0.6),
                  auto_dismiss=True)                 # stays until tapped
        p.open()

    def _push_outage_alert(self, msg):
        import threading
        from monitor.operator_alert import send_operator_alert
        chat = getattr(self, "_chat", None)
        sender = chat.send_plain if (chat is not None and chat.running) else None
        threading.Thread(target=lambda: send_operator_alert("Node Medic — " + msg,
                                                            sender=sender),
                         daemon=True).start()

    def _outage_sentence(self, device):
        """'<name> at <place> has been unreachable for N days' — N from the
        node's own silence, falling back to the grace window only when the
        row carries no age (it always said 'for 3 days', ledger #138)."""
        from monitor.node_watch import grace_hours
        w = getattr(self, "_node_watcher", None)
        lsh = device.get("last_seen_hours")
        if isinstance(lsh, (int, float)) and lsh > 0:
            days = max(1, int(round(lsh / 24.0)))
        else:
            gh = grace_hours(device.get("powered_by"), getattr(w, "grace_override_h", None))
            days = max(1, round(gh / 24.0))
        name = device.get("name") or "A node"
        loc = device.get("location") or ""
        # Placeholder subtitles are not places — neither of these may become
        # "...at heard on the mesh" / "...at LXMF propagation announces".
        # (Belt-and-braces: node_watch only escalates non-neighbour rows
        # today, so these strings should never reach here — kept anyway so a
        # loosened watcher can't produce the false sentence.)
        from monitor.registry import HEARD_ON_MESH, PROPAGATION_SUBTITLE
        placeholder = loc in (HEARD_ON_MESH, PROPAGATION_SUBTITLE)
        where = f" at {loc}" if loc and not placeholder else ""
        return (f"{name}{where} has been unreachable for {days} "
                f"day{'s' if days != 1 else ''} — it likely needs a physical check.")

    def _save_node_watch(self):
        w = getattr(self, "_node_watcher", None)
        if w is None:
            return
        try:
            import json
            os.makedirs(os.path.dirname(self._WATCH_FILE), exist_ok=True)
            tmp = self._WATCH_FILE + ".tmp"
            with open(tmp, "w") as f:
                json.dump(w.to_state(), f)
            os.replace(tmp, self._WATCH_FILE)
        except Exception:
            pass

    def _on_home_profile_change(self, profile):
        """The Home-mode profile (propagation vs transport) changed in Settings. If
        the medic is currently in HOME, re-apply so it takes effect now; otherwise
        it applies next time the front-page toggle is set to Home."""
        tog = getattr(self.home_screen, "mode_toggle", None)
        if tog is not None and tog.mode == "home":
            self._set_node_mode("home")            # re-applies with the new profile

    def _mode_toast(self, message, ok=True, mode=None):
        """A brief, auto-dismissing message after a mode switch."""
        from kivy.uix.popup import Popup
        from kivy.uix.label import Label
        lbl = Label(text=message, halign="center", valign="middle",
                    padding=(dp(16), dp(16)))
        lbl.bind(size=lambda i, v: setattr(i, "text_size", v))
        from monitor.formatting import toast_title
        p = Popup(title=toast_title(message, ok, mode), content=lbl,
                  size_hint=(0.82, 0.32), auto_dismiss=True)
        p.open()
        Clock.schedule_once(lambda dt: p.dismiss(), 4.0)

    def _open_birth_guide(self):
        """Enter the step-by-step guide at its start (the 'what are you building?'
        chooser), not wherever it was left last time."""
        g = getattr(self, "birth_guide_screen", None)
        if g is not None:
            g.reset()
        self.switch_mode("birth_guide")

    def _resolve_identities(self):
        """Link a device's multiple mesh destinations by their shared RNS identity,
        so a neighbour heard on several destinations collapses to ONE VITALS row
        (never inflating the apparent mesh size). Best-effort — needs RNS attached
        (the announce listener does that); a dest RNS has no announce for stays
        unlinked, since we genuinely can't prove it's the same device."""
        try:
            import RNS
        except Exception:
            return
        for rec in list(self.monitor_service.registry.nodes.values()):
            if rec.identity_hash:
                continue
            try:
                ident = RNS.Identity.recall(bytes.fromhex(rec.dst_hash))
            except Exception:
                ident = None
            if ident is not None:
                try:
                    rec.identity_hash = ident.hash.hex()
                except Exception:
                    pass

    def _heard_mesh_candidates(self):
        """Nodes the medic hears over the mesh, for over-the-air adoption."""
        import time as _t
        from ui.adopt_live import heard_candidates
        return heard_candidates(self.monitor_service.registry, _t.time())

    def _adopt_over_air(self, key, name, node_type, board=None, firmware=None,
                        location=None):
        """Enrol a heard node as kin (no USB); refresh VITALS + the SCAN map."""
        from ui.adopt_live import over_air_adopt
        cert = over_air_adopt(key, name, node_type, board=board, firmware=firmware,
                              location=location)
        try:
            from monitor.kin_roster import load_roster
            self.monitor_service.registry.set_kin_roster(load_roster())
        except Exception:
            pass
        return cert

    def expect_board_absence(self, expected=True):
        """Tell the disconnect watcher that an unplugged board is INTENDED.

        Set while a walkthrough step asks for the radio to come off the medic —
        and left set for the rest of that walkthrough, because the radio stays
        off until it goes onto the Pi at the very end. Cleared when the guide
        resets, so a later session gets the warning back.
        """
        self._board_absence_expected = bool(expected)

    def resume_guided_birth(self, result=None):
        """Hand control back to the walkthrough after a screen has done its job.

        Returns True if a walkthrough was actually waiting. Screens call this
        instead of leaving the operator on a finished page — before this existed
        a hand-off was one-way, so the guide's remaining steps were unreachable
        and each screen had to narrate them itself in plain text.

        *result* records what the screen achieved (e.g. ``{"radio_verified":
        True}``), which is what lets a later step's gate consult it.
        """
        g = getattr(self, "birth_guide_screen", None)
        if g is None or not g.has_pending_resume():
            return False
        # Only from the hand-off screen itself. A flash finishing while the
        # operator had gone Home used to pull them back into the walkthrough
        # (readiness ledger #66, #171); the result stays pending for their
        # next deliberate visit instead.
        if getattr(self.sm, "current", "") not in ("birth", "pi_imager"):
            return False
        self.switch_mode("birth_guide")
        g.resume(result or {})
        return True

    def guided_birth_pending(self) -> bool:
        """Is a walkthrough mid-flight, waiting for a screen to hand back?
        Screens ask before offering to continue, so the offer never appears for
        work started directly from the BIRTH screen."""
        g = getattr(self, "birth_guide_screen", None)
        return bool(g is not None and g.has_pending_resume())

    def _guided_birth_complete(self, path, name="", share_location=None,
                               location=None):
        """The guide's steps are done — hand off to the real BIRTH screen,
        pre-scoped to the chosen kind with the node name (collected in the guide)
        prefilled and detection already running.

        *share_location* / *location* are the guide's map answer and pin —
        asked on the 'radio' path and previously DROPPED here, so a "Show on
        map" with a placed pin became a hidden node the moment this hand-off
        ran (breaker audit, 2026-09-13). None means never asked, and
        begin_guided's own hygiene keeps that hidden."""
        bs = getattr(self, "birth_screen", None)
        if bs is not None:
            # One call: begin_guided applies the name itself, so a second reset
            # can't undo the scoping it just set (see BirthScreen.begin_guided).
            if hasattr(bs, "begin_guided"):
                bs.begin_guided(path, name=name or None,
                                share_location=share_location,
                                location=location)
            elif name and hasattr(bs, "prefill_name"):
                bs.prefill_name(name)
        self.switch_mode("birth")

    def _start_security_preview(self):
        """Walk the whole encrypt-at-rest ceremony with NOTHING real behind it:
        a demo recovery key, a demo passphrase, and a reset that erases only a
        message. Lets the look be judged before any of it touches cryptsetup."""
        host = getattr(self, "_security_host", None)
        if host is None:
            return
        host.clear_widgets()
        from ui.screens.recovery_key_screen import RecoveryKeyScreen
        from ui.screens.vault_unlock_screen import VaultUnlockScreen

        DEMO_PASS = "medic"

        def to_unlock(key):
            host.clear_widgets()

            def unlock(pw):
                return ((True, "") if pw == DEMO_PASS
                        else (False, "That password didn't open it."))

            def recover(k):
                from provisioning import recovery_key as rk
                return ((True, "") if rk.normalize(k) == rk.normalize(key)
                        else (False, "That recovery key didn't open it."))

            host.add_widget(VaultUnlockScreen(
                unlock_fn=unlock, recover_fn=recover,
                reset_fn=lambda: (True, "Reset — starting fresh. (preview)"),
                on_unlocked=lambda: self.switch_mode("settings")))
            # The KEY is deliberately not printed. This goes to ~/ui.log on an
            # unencrypted card, and `key` comes from a real recovery_key.generate()
            # - harmless while it protects nothing, a full compromise the day this
            # ceremony is wired to a real container.
            print(f"[preview] demo password is '{DEMO_PASS}'", flush=True)

        host.add_widget(RecoveryKeyScreen(on_done=to_unlock))

    def switch_mode(self, mode_name):
        kb = getattr(self, "keyboard", None)
        if kb is not None:
            kb.hide()                     # dismiss the keyboard when leaving a screen
        if mode_name in [s.name for s in self.sm.screens]:
            # Forward (home -> a mode): the new screen enters from the RIGHT
            # (Kivy direction="left"). Back (-> home): home slides in from the LEFT
            # (direction="right") — the REVERSE, so back reads as back, not another
            # forward push. Matches the left-edge back swipe.
            self.sm.transition.direction = "right" if mode_name == "home" else "left"
            if mode_name == "pi_imager":
                # Rebuild BEFORE the transition, not after. Resetting once the
                # slide had started meant the previous card's panel was on
                # screen for a beat and then snapped to the form — read as the
                # screen flashing (operator, 2026-08-02).
                scr = getattr(self, "pi_imager_screen", None)
                if scr is not None and hasattr(scr, "reset_for_new_card"):
                    scr.reset_for_new_card()
            if mode_name == "birth":
                # THE choke point for BIRTH. Five callers reach this screen and
                # only the guided one used to clear the previous lap, so the
                # others carried a stale _sel_board in — which skips the
                # board-confirm gate (a V3 nearly flashed as a 'Heltec V4',
                # 2026-08-01). Callers that prefill a name or location reset
                # themselves and mark the lap prepared, so this is a no-op for
                # them; it catches the plain entries, and any added later.
                scr = getattr(self, "birth_screen", None)
                if scr is not None and hasattr(scr, "enter_birth"):
                    scr.enter_birth()
            if mode_name == "setup":
                # A fresh walkthrough every time it is opened. The re-run entry
                # in Settings exists for a medic being handed to somebody else,
                # and showing THEM a summary of the previous operator's choices
                # is the one thing that entry must never do. Reset before the
                # transition, like the imager, so the old screen is never on
                # screen for a beat mid-slide.
                scr = getattr(self, "setup_screen", None)
                if scr is not None and hasattr(scr, "reset"):
                    scr.reset()
            self.sm.current = mode_name
            if mode_name == "home":
                self.refresh_radio_badge()   # keep the changed-params badge honest
            if mode_name == "security_preview":
                self._start_security_preview()
            # Both of these REPORT the encryption state, and the operator changes
            # it on one and reads it on the other. Built once at startup, they
            # would go on showing whatever was true at boot.
            if mode_name == "salvage":
                scr = getattr(self, "salvage_screen", None)
                if scr is not None:
                    scr.show_start()
            if mode_name == "encryption":
                scr = getattr(self, "encryption_screen", None)
                if scr is not None:
                    scr.show_overview()
            if mode_name == "settings":
                scr = getattr(self, "settings_screen", None)
                if scr is not None and hasattr(scr, "refresh_encryption_row"):
                    scr.refresh_encryption_row()
