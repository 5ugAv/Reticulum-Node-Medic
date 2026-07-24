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
                          "[HealthBeacon] announce dst=eabdd142596bcae888242ec1b172d566 "
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
    {"name": "Northcote Hill", "location": "Northcote", "status": "ok",
     "battery_pct": 82, "signal_dbm": -78, "last_seen_hours": 0.2,
     "powered_by": "solar", "type": "pi"},
    {"name": "Thornbury Water Tower", "location": "Thornbury", "status": "warn",
     "battery_pct": 18, "signal_dbm": -112, "last_seen_hours": 1.5,
     "powered_by": "battery", "type": "pi"},
    {"name": "CBD Rooftop RTNode", "location": "Melbourne CBD", "status": "alert",
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
                          "New identity <45ada7a3c6c8809fa815e5790d2b3b62> written", ""))
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
        """A mode screen that goes back to the front page on a LEFT-EDGE SWIPE
        (with a faint chevron handle) — no corner BACK button to overlap controls."""
        wrap = _BackSwipeWrap(on_back=lambda: self.switch_mode("home"))
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

    def _show_screensaver(self):
        try:
            from provisioning import screensaver as ss
            if not self._screensaver.active:
                self._screensaver.show(ss.style())
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
        home.add_widget(HomeScreen(on_select=self.switch_mode))
        self.sm.add_widget(home)

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
        self.vitals_screen = VitalsScreen(nodes=seed, on_open=self._open_node_cert)
        vitals.add_widget(self._with_back(self.vitals_screen))
        self.sm.add_widget(vitals)
        self.monitor_service = MonitorService(run=_local_run)
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
            on_preview_screensaver=self._show_screensaver)))
        self.sm.add_widget(settings_scr)

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
            on_use_existing=self._use_existing_node,
            on_guide=self._open_birth_guide,
            node_source=self._search_known_nodes)
        birth.add_widget(self._with_back(self.birth_screen))
        self.sm.add_widget(birth)

        # Guided birth — one instruction per screen with animations, for a
        # first-time operator. Its physical-prep steps hand off to the BIRTH
        # screen above (detect / name / flash).
        birth_guide = Screen(name="birth_guide")
        from ui.screens.birth_guide_screen import BirthGuideScreen
        self.birth_guide_screen = BirthGuideScreen(
            on_complete=self._guided_birth_complete,
            on_navigate=self.switch_mode)
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

                RNS.Reticulum()          # attach to the shared instance
                RNS.Transport.register_announce_handler(_Handler())
                RNS.Transport.register_announce_handler(_HealthHandler())
            except Exception:
                pass                     # no rnsd (dev box): silently offline

        threading.Thread(target=listen, daemon=True).start()

    _BEACON_FILE = os.path.expanduser("~/.reticulum-node-medic/beacon_targets.json")

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
        commands every known kin RTNode to transmit (~every 9 s) so a node's
        antenna can be aimed against a real distant signal, and returns a status
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
            _t.sleep(2)          # fast cadence so the glow tracks antenna movement

    def on_stop(self):
        self._lighthouse_on = False
        stop = getattr(self, "_monitor_stop", None)
        if stop is not None:
            stop.set()

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
        else:
            self._no_cert_popup(name)

    def _open_cert(self, cert):
        from ui.screens.cert_view_screen import CertViewScreen
        scr = self.sm.get_screen("cert_view")
        scr.clear_widgets()
        scr.add_widget(self._with_back(
            CertViewScreen(cert, on_show_location=self._show_node_on_map)))
        self.switch_mode("cert_view")

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

    def _open_birth_guide(self):
        """Enter the step-by-step guide at its start (the 'what are you building?'
        chooser), not wherever it was left last time."""
        g = getattr(self, "birth_guide_screen", None)
        if g is not None:
            g.reset()
        self.switch_mode("birth_guide")

    def _guided_birth_complete(self, path):
        """The guide's physical-prep steps are done — hand off to the real BIRTH
        screen, pre-scoped to the chosen kind with detection already running."""
        bs = getattr(self, "birth_screen", None)
        if bs is not None and hasattr(bs, "begin_guided"):
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
