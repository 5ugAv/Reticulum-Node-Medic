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
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.label import Label
from kivy.uix.screenmanager import Screen, ScreenManager
from kivy.metrics import dp

from ui import theme
from ui.screens.vitals_screen import VitalsScreen
from ui.screens.scan_screen import ScanScreen
from ui.screens.probe_screen import ProbeScreen
from ui.screens.birth_screen import BirthScreen
from ui.screens.triage_screen import TriageScreen
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
    return BuildWorkflow(conn, NodeProfile())


def _pi_rnode_factory():
    """Pi propagation birth. The real remote-provision path isn't wired yet, so
    outside opt-in demo mode this HONESTLY fails instead of faking an ok/ok/ok
    birth certificate (the trap that shipped 'built' nodes that were never
    touched)."""
    from ui.hw_factories import demo_allowed, _HonestFailWorkflow
    if demo_allowed():
        return _demo_pi_build()
    return _HonestFailWorkflow(
        "provision_pi",
        "This process is still under construction. Your Pi is detected fine — the "
        "medic just can't auto-provision a Pi propagation node through this button "
        "yet (and it won't fake it). For now: flash the RNode on its own (pick the "
        "board, leave Host Pi empty), then set the Pi up over the wire by hand.\n\n"
        "Noted for the developers to build.",
        "Pi birth — under construction", under_construction=True)


def _mitosis_factory():
    """Clone THIS medic onto a fresh Pi. The real target-Pi SSH flow isn't wired
    yet, so outside opt-in demo mode this honestly fails rather than faking it."""
    from ui.hw_factories import demo_allowed, _HonestFailWorkflow
    if demo_allowed():
        return _demo_clone_workflow()
    return _HonestFailWorkflow(
        "select_target",
        "This process is still under construction. Cloning the medic onto a fresh "
        "Pi through this button isn't built yet (and it won't fake a clone). Coming "
        "soon: pick the new Pi, then clone over the wire.\n\n"
        "Noted for the developers to build.",
        "Mitosis — under construction", under_construction=True)


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
    from monitor.triage_feed import live_triage_feed
    from monitor.geo import read_splitter_state
    mode = os.environ.get("RNM_TRIAGE", "")
    if mode == "demo":
        return _demo_triage_feed()
    # live only when the splitter is actually feeding NOW (a stale file left
    # over from an old run must not select a frozen live feed)
    if mode == "live" or read_splitter_state() is not None:
        return live_triage_feed()
    return _demo_triage_feed()


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


class _BackSwipeWrap(FloatLayout):
    """Wraps a mode screen. A swipe IN from the LEFT EDGE goes back — replacing a
    corner BACK button that overlapped screen controls (and never having to
    choreograph controls around it again). A thin translucent chevron marks the
    zone. Only touches that START within the narrow edge strip are claimed for the
    back gesture; everything else passes straight through, so map panning, buttons
    and text fields all still work (a pan starts mid-screen, not at the border)."""

    EDGE_DP = 26           # width of the left-edge back zone
    TRIGGER_DP = 55        # rightward travel that fires 'back'

    def __init__(self, on_back, **kwargs):
        super().__init__(**kwargs)
        self._on_back = on_back
        self._edge = None                       # (touch, start_x) mid back-swipe
        # '‹' (U+2039) renders in the default font (unlike the ⚠ emoji); a faint
        # handle telling the operator where the back gesture lives.
        self._chevron = Label(text="‹", font_size="40sp", bold=True,
                              size_hint=(None, None), size=(dp(22), dp(64)),
                              pos_hint={"x": 0.0, "center_y": 0.5},
                              color=theme.hex_to_rgba(theme.COLORS["text_secondary"], 0.55))

    def add_content(self, widget):
        widget.size_hint = (1, 1)
        self.add_widget(widget)
        self.add_widget(self._chevron)          # keep the handle on top

    def on_touch_down(self, touch):
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


class ReticulumNodeMedicApp(App):
    title = "Reticulum Node Medic"

    def _with_back(self, widget):
        """A mode screen that goes back on a LEFT-EDGE SWIPE (faint chevron handle,
        no corner BACK button to overlap controls). For a MULTI-PAGE flow the swipe
        steps back ONE page first: if the wrapped screen has ``handle_back()`` and
        it returns True (it stepped back internally), we stop there; only at the
        flow's root (or a plain single-page screen) does it fall through to home."""
        def on_back():
            h = getattr(widget, "handle_back", None)
            if callable(h):
                try:
                    if h():
                        return              # the screen stepped back a page
                except Exception:
                    pass
            self.switch_mode("home")
        wrap = _BackSwipeWrap(on_back=on_back)
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
            busy = ((sm is not None and sm.current in self._NO_SAVER_SCREENS)
                    or getattr(self, "_activity", 0) > 0)
            if busy:
                self._reset_idle()            # defer — don't cover an active process
                return
            if not self._screensaver.active:
                self._screensaver.show(ss.style())
        except Exception:
            pass

    def begin_activity(self, label="Working — please wait"):
        """Mark a long, touch-free process running (a flash/build) so the
        screensaver can't cover it AND a persistent banner warns the operator not
        to power off / unplug. Balanced by end_activity(). The flash itself runs on
        a daemon thread + child process, so navigating away never interrupts it —
        this just keeps the operator from making it unsafe."""
        self._activity = getattr(self, "_activity", 0) + 1
        if self._activity == 1:
            self._show_activity_banner(label)

    def end_activity(self):
        self._activity = max(0, getattr(self, "_activity", 0) - 1)
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
            bar = getattr(self, "_activity_banner", None)
            if bar is not None:
                bar.text = label
                return
            bar = Label(text=label, bold=True, font_size="13sp", color=(1, 1, 1, 1),
                        halign="center", valign="middle", size_hint=(None, None),
                        height=dp(34))
            with bar.canvas.before:
                Color(*theme.hex_to_rgba(theme.COLORS["red"]))
                rect = Rectangle()

            def _sync(*_):
                bar.width = Window.width
                bar.pos = (0, Window.height - bar.height)
                bar.text_size = bar.size
                rect.pos, rect.size = bar.pos, bar.size
            bar.bind(pos=_sync, size=_sync)
            Window.bind(size=lambda *_a: _sync())
            Window.add_widget(bar)
            _sync()
            self._activity_banner = bar
        except Exception as e:
            print(f"[activity] banner skipped: {e}")

    def _hide_activity_banner(self):
        try:
            from kivy.core.window import Window
            bar = getattr(self, "_activity_banner", None)
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
                                           commission_attached)
            if load_roster() or not attached_serial_ports():
                return                                 # already done, or nothing to adopt
            adopted = commission_attached(probe=lambda _p: None)   # fast, no rnodeconf
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
        # modes); every mode screen carries a BACK button bottom-right.
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
        from monitor.node_watch import NodeWatcher
        self._node_watcher = NodeWatcher()    # escalate nodes down past the grace window
        self._WATCH_FILE = os.path.expanduser("~/.reticulum-node-medic/node_watch.json")
        try:
            import json
            with open(self._WATCH_FILE) as _f:
                self._node_watcher.load_state(json.load(_f))
        except Exception:
            pass

        credits = Screen(name="credits")
        credits.add_widget(CreditsScreen(
            on_select=self.switch_mode,
            on_back=lambda: self.switch_mode("home")))
        self.sm.add_widget(credits)

        # Final confirmed modes, registered in sidebar order:
        # 1 VITALS · 2 SCAN · 3 BIRTH · 4 TRIAGE · 5 PROBE · 6 MITOSIS
        vitals = Screen(name="vitals")
        # Live discovery fills the dashboard; RNM_DEMO=1 seeds the fake showcase
        # nodes instead (they confused a real deployment, so default off).
        seed = DEMO_NODES if os.environ.get("RNM_DEMO") else []
        self.vitals_screen = VitalsScreen(nodes=seed, on_open=self._open_node_detail)
        vitals.add_widget(self._with_back(self.vitals_screen))
        self.sm.add_widget(vitals)
        # Load the persisted registry so the node history / activity series carries
        # over from past sessions (it's saved periodically + on stop below).
        from monitor.registry import NodeRegistry
        self.monitor_service = MonitorService(
            run=_local_run, registry=NodeRegistry.load(self._REGISTRY_FILE))
        self._apply_retention(None)                  # honour the saved retention window
        self._start_monitor_polling()
        self._start_announce_listener()

        # SCAN is now the SINGLE map: coverage + offline caching + node placement.
        # A stationary tap (or the live GPS fix) sets a spot; "Use this position"
        # stamps it and jumps into BIRTH. The fix-trust badge guards against a
        # HELD/stale fix pinning a node far from where it actually is — the job the
        # old separate gps_confirm page used to do.
        scan = Screen(name="scan")
        from monitor.geo import splitter_gps_reader, read_splitter_fix
        from ui.screens.scan_screen import link_segments, suggestion_markers
        from monitor.placement import suggest
        self._scan_topo = None                    # rebuilt each poll cycle (rnpath)
        self.scan_screen = ScanScreen(
            nodes=self.monitor_service.located_nodes(),
            gps_reader=splitter_gps_reader(),     # the Tracker's live "you are here"
            fix_reader=read_splitter_fix,         # full fix -> live/held/none badge
            on_place=self._on_gps_confirmed,      # "Use this position" -> BIRTH
            on_node_pick=self._open_node_cert,    # tap a node dot -> its certificate
            # mesh-lines toggle + "add a node here" gap markers (empty until topology)
            links_provider=lambda: link_segments(self._scan_topo) if self._scan_topo else [],
            suggestions_provider=lambda: (suggestion_markers(suggest(self._scan_topo))
                                          if self._scan_topo else []))
        scan.add_widget(self._with_back(self.scan_screen))
        self.sm.add_widget(scan)

        # Certificate viewer — a persistent host screen whose content is rebuilt for
        # whichever node the operator taps (VITALS row or SCAN map dot).
        self.sm.add_widget(Screen(name="cert_view"))

        # Settings hub (the home gear) — WiFi to start, more to come.
        settings_scr = Screen(name="settings")
        from ui.screens.settings_screen import SettingsScreen
        settings_scr.add_widget(self._with_back(SettingsScreen(
            on_open=self.switch_mode,
            on_retention_change=self._apply_retention,
            node_count_provider=self._monitor_node_count,
            on_preview_screensaver=self._show_screensaver,
            on_home_profile_change=self._on_home_profile_change)))
        self.sm.add_widget(settings_scr)

        notif_scr = Screen(name="notifications")
        from ui.screens.notifications_screen import NotificationsScreen
        notif_scr.add_widget(self._with_back(NotificationsScreen()))
        self.sm.add_widget(notif_scr)

        self.sm.add_widget(Screen(name="node_detail"))   # filled on a VITALS tap

        # Language — pick the UI language (applies on next app start).
        language_scr = Screen(name="language")
        from ui.screens.language_screen import LanguageScreen
        language_scr.add_widget(self._with_back(LanguageScreen()))
        self.sm.add_widget(language_scr)

        # Communication apps — hand Columba/Sideband to a phone over Wi-Fi + QR.
        comms_scr = Screen(name="comms")
        from ui.screens.comms_screen import CommsScreen
        self.comms_screen = CommsScreen()
        comms_scr.add_widget(self._with_back(self.comms_screen))
        comms_scr.bind(on_enter=lambda *_: self.comms_screen.enter())
        self.sm.add_widget(comms_scr)

        # WiFi connect — join a hotspot / venue AP so online features work afield.
        wifi_scr = Screen(name="wifi")
        from ui.screens.wifi_screen import WifiScreen
        self.wifi_screen = WifiScreen()
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
        datetime_scr.add_widget(self._with_back(DateTimeScreen()))
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
            on_guide=self._open_birth_guide)
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

        pi_imager_scr = Screen(name="pi_imager")
        from ui.screens.pi_imager_screen import PiImagerScreen
        from workflows.rtnode_portal import medic_wifi_credentials
        pi_imager_scr.add_widget(self._with_back(
            PiImagerScreen(wifi_credentials=medic_wifi_credentials)))
        self.sm.add_widget(pi_imager_scr)

        triage = Screen(name="triage")
        self.triage_screen = TriageScreen(
            feed_factory=_triage_feed, lighthouse=self._lighthouse,
            on_build=lambda: self.switch_mode("birth"),
            on_home=lambda: self.switch_mode("home"))
        triage.add_widget(self._with_back(self.triage_screen))
        # opening Triage auto-activates the beacon; leaving it stops it
        triage.bind(on_enter=lambda *a: self.triage_screen.enter_triage(),
                    on_leave=lambda *a: self.triage_screen.stop_lighthouse())
        self.sm.add_widget(triage)

        probe = Screen(name="probe")
        _probe_real = hw.hardware_present()
        probe.add_widget(self._with_back(ProbeScreen(
            workflow_factory=lambda: hw.make_repair_workflow(_demo_repair_workflow),
            target_name="This node + attached board" if _probe_real
                        else ("Demo node - emulated" if hw.demo_allowed()
                              else "No board — plug one in to PROBE"),
            on_self_diagnose=lambda: self.switch_mode("self_diagnose"))))
        self.sm.add_widget(probe)

        # Self Diagnose — the medic checks & heals its OWN onboard radio/GPS board.
        from ui.screens.self_diagnose_screen import SelfDiagnoseScreen
        self_dx = Screen(name="self_diagnose")
        self_dx.add_widget(self._with_back(SelfDiagnoseScreen()))
        self.sm.add_widget(self_dx)

        mitosis = Screen(name="mitosis")
        mitosis.add_widget(self._with_back(MitosisScreen(workflow_factory=_mitosis_factory)))
        self.sm.add_widget(mitosis)

        self.sm.current = os.environ.get("RNM_START", "home")
        self._install_screensaver()

        # The on-screen keyboard floats above every screen (the touchscreen has
        # no physical keys). Fields call ui.onscreen_keyboard.bind_field(...) and
        # it reveals itself, panning the ScreenManager up so the field stays clear.
        root = FloatLayout()
        root.add_widget(self.sm)
        self.keyboard = OnScreenKeyboard(pan_target=self.sm,
                                         pos_hint={"x": 0, "y": 0})
        root.add_widget(self.keyboard)
        return root

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
                    if dicts:
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
                except Exception:
                    pass  # never let a poll error kill the loop
                i += 1
                stop.wait(interval)

        threading.Thread(target=loop, daemon=True).start()

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
            return build_topology(self.monitor_service.registry, paths, time.time())
        except Exception:
            return None

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
                        registry.ingest_announce(
                            destination_hash, app_data or b"",
                            _t.time(), identity_hash=ih)
                    except Exception:
                        pass

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
                RNS.Reticulum()          # attach to the shared instance
                RNS.Transport.register_announce_handler(_Handler())
                RNS.Transport.register_announce_handler(_HealthHandler())

            def _log(msg):
                try:
                    RNS.log("Node Medic: " + msg)   # visible in the rnsd log
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
        names = [(reg.nodes.get(h).name if (reg.nodes.get(h)
                  and reg.nodes.get(h).name) else f"node {h[:8]}")
                 for h in targets]
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
            threading.Thread(target=self._beacon_loop, daemon=True).start()
            names = self._target_names(targets)
            return {"state": "active", "names": names,
                    "text": f"Beacon on - commanding {names} to transmit. Aim "
                            "the antenna and watch the triangle."}
        reg = self.monitor_service.registry
        rtnodes = [r.name for r in reg.nodes.values()
                   if r.node_type == "rtnode2400" and r.provenance == "kin"
                   and r.name]
        if rtnodes:
            nm = ", ".join(rtnodes)
            return {"state": "need_power", "names": nm,
                    "text": f"Power on your beacon node ({nm}) so Triage can "
                            "command it to transmit for aiming."}
        return {"state": "need_build",
                "text": "Triage needs a distant RTNode to aim against. Build one "
                        "to pair as your lighthouse beacon."}

    def _beacon_loop(self):
        import time as _t
        try:
            import RNS
        except Exception:
            return
        while getattr(self, "_lighthouse_on", False):
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
        print(f"[gps] location for node: {lat:.6f}, {lon:.6f} ({source}) -> BIRTH")
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
        watch_line = None
        w = getattr(self, "_node_watcher", None)
        if w is not None and w.is_watching(node):
            rem = w.watch_remaining_hours(node) or 0.0
            days = max(1, round(rem / 24.0))
            watch_line = ("Unreachable — the medic is watching it. If it's still down "
                          f"in about {days} day{'s' if days != 1 else ''}, you'll be "
                          "told to go and check it.")
        # activity rhythm + history insights from the persisted per-node time series
        activity_text, by_hour, insights = None, None, None
        try:
            from monitor.history import activity_profile, describe_activity, analyse
            pts = self.monitor_service.registry.history.series(rec.dst_hash)
            profile = activity_profile(pts, now, self._local_tz_offset_hours())
            activity_text = describe_activity(profile)
            by_hour = profile.get("by_hour")
            insights = analyse(pts, now)
        except Exception:
            pass
        from ui.screens.node_detail_screen import NodeDetailScreen
        scr = self.sm.get_screen("node_detail")
        scr.clear_widgets()
        scr.add_widget(self._with_back(NodeDetailScreen(
            rec, now, on_poll=self._ping_node, on_navigate=self._navigate_to_node,
            watch_line=watch_line, activity_text=activity_text, by_hour=by_hour,
            insights=insights)))
        self.switch_mode("node_detail")

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

    def _ping_node(self, dst_hash, report):
        """Live mesh reachability check. Drop the (possibly stale) cached path —
        cached paths lie — then REQUEST a fresh one and WAIT for it. The old code
        read the path table immediately after dropping, before any fresh path
        could resolve, so it reported 'not answering' for every node, healthy or
        not. rnpath -w does the request+wait; monitor.mesh.parse_path_probe reads
        the result. Off-thread; reports back to the detail screen."""
        import threading
        from monitor.mesh import parse_path_probe

        def work():
            # The row's dst_hash can be a non-hex display key (an HTTP-discovery
            # 'rtnode:<name>' record leading a merged device). rnpath errors on
            # that -> looks unreachable. Resolve a real hex mesh dest for the same
            # device first (its health/mesh aspect).
            probe = self.monitor_service.registry.probe_hash_for(dst_hash or "")
            if not probe:
                Clock.schedule_once(lambda dt: report(
                    "No mesh address on record — can't probe this one.", False), 0)
                return
            _local_run(f"rnpath --drop {probe} 2>/dev/null")
            out = _local_run(f"rnpath -w 20 {probe} 2>/dev/null")
            reachable, hops = parse_path_probe(out)
            if reachable:
                msg = "Reachable now" + (f" — {hops} hop(s) away." if hops else ".")
                Clock.schedule_once(lambda dt: report(msg, True), 0)
            else:
                Clock.schedule_once(lambda dt: report(
                    "Not answering right now — it may be down or out of range. "
                    "The medic keeps watching it.", False), 0)

        threading.Thread(target=work, daemon=True).start()

    def _navigate_to_node(self, record):
        """Show a node on the SCAN map at its recorded location."""
        lat = getattr(record, "lat", None)
        lon = getattr(record, "lon", None)
        if lat is not None and lon is not None:
            self.switch_mode("scan")
            Clock.schedule_once(lambda dt: self.scan_screen.show_location(lat, lon), 0)

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
        if ll is None:
            ll = (-37.8136, 144.9631)          # Sampleton fallback centre

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

        ConfirmLocationPopup(ll[0], ll[1], node_name=cert.get("node_name", ""),
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

        def work():
            res = set_mode(new_mode, LocalConnection())

            def done(dt):
                tog.set_state(res.mode)
                msg = res.message
                if auto and res.ok and res.mode == "backpack":
                    msg = ("On the move — switched to Backpack automatically so this "
                           "node won't disturb the mesh while it travels. Tap the "
                           "home icon to resume Home mode once you've settled.")
                self._mode_toast(msg, ok=res.ok)
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
        try:
            from workflows.node_mode import load_auto_backpack
            if not load_auto_backpack():
                return
            from monitor.geo import read_splitter_fix
            fix = read_splitter_fix()
            if fix is None or fix.lat is None or fix.lon is None:
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
            from monitor.geo import read_splitter_fix
            fix = read_splitter_fix()
            if fix is not None and fix.lat is not None and fix.lon is not None:
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

    def _check_node_watch(self, devices):
        """Runs on the monitor thread each cycle: escalate any node that has stayed
        continuously unreachable past its grace window (solar recharge grace).
        Autonomous — no user action; state persists across restarts."""
        w = getattr(self, "_node_watcher", None)
        if w is None or not devices:
            return
        try:
            for d in w.tick(devices):
                Clock.schedule_once(lambda dt, dv=d: self._escalate_node(dv), 0)
        except Exception:
            pass

    def _escalate_node(self, device):
        """A node has been down long enough to warrant a physical visit: alert on
        the medic, and — if the operator saved a Reticulum address in Settings —
        push it to their Sideband/Columba too (best-effort, off-thread)."""
        from monitor.node_watch import grace_hours
        w = getattr(self, "_node_watcher", None)
        gh = grace_hours(device.get("powered_by"), getattr(w, "grace_override_h", None))
        days = max(1, round(gh / 24.0))
        name = device.get("name") or "A node"
        loc = device.get("location") or ""
        where = f" at {loc}" if loc and loc != "heard on the mesh" else ""
        msg = (f"{name}{where} has been unreachable for {days} "
               f"day{'s' if days != 1 else ''} — it likely needs a physical check.")
        self._mode_toast(msg, ok=False)
        import threading
        from monitor.operator_alert import send_operator_alert
        threading.Thread(target=lambda: send_operator_alert("Node Medic — " + msg),
                         daemon=True).start()

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

    def _mode_toast(self, message, ok=True):
        """A brief, auto-dismissing message after a mode switch."""
        from kivy.uix.popup import Popup
        from kivy.uix.label import Label
        lbl = Label(text=message, halign="center", valign="middle",
                    padding=(dp(16), dp(16)))
        lbl.bind(size=lambda i, v: setattr(i, "text_size", v))
        p = Popup(title=("Home mode" if ok else "Mode change"), content=lbl,
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

    def _guided_birth_complete(self, path, name=""):
        """The guide's steps are done — hand off to the real BIRTH screen,
        pre-scoped to the chosen kind with the node name (collected in the guide)
        prefilled and detection already running."""
        bs = getattr(self, "birth_screen", None)
        if bs is not None:
            if name and hasattr(bs, "prefill_name"):
                bs.prefill_name(name)
            if hasattr(bs, "begin_guided"):
                bs.begin_guided(path)
        self.switch_mode("birth")

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
            self.sm.current = mode_name
