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
from kivy.uix.label import Label
from kivy.uix.screenmanager import Screen, ScreenManager

from ui import theme
from ui.widgets.sidebar import Sidebar
from ui.screens.vitals_screen import VitalsScreen
from ui.screens.scan_screen import ScanScreen
from ui.screens.probe_screen import ProbeScreen
from ui.screens.birth_screen import BirthScreen
from ui.screens.triage_screen import TriageScreen
from ui.screens.mitosis_screen import MitosisScreen
from ui.screens.home_screen import HomeScreen
from ui.screens.credits_screen import CreditsScreen
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


class ReticulumNodeMedicApp(App):
    title = "Reticulum Node Medic"

    def build(self):
        Window.clearcolor = theme.hex_to_rgba(theme.COLORS["background"])
        # On the medic's touchscreen, fill the native display (which may be
        # portrait, e.g. 720x1280). RNM_WINDOWED=1 gives a 1280x720 dev window.
        if os.environ.get("RNM_WINDOWED"):
            Window.size = (1280, 720)
        else:
            Window.fullscreen = "auto"

        root = BoxLayout(orientation="horizontal")
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
        self.vitals_screen = VitalsScreen(nodes=seed)
        vitals.add_widget(self.vitals_screen)
        self.sm.add_widget(vitals)
        self.monitor_service = MonitorService(run=_local_run)
        self._start_monitor_polling()

        scan = Screen(name="scan")
        self.scan_screen = ScanScreen(nodes=self.monitor_service.located_nodes())
        scan.add_widget(self.scan_screen)
        self.sm.add_widget(scan)

        birth = Screen(name="birth")
        birth.add_widget(BirthScreen(
            workflow_factories={"rtnode2400": _demo_rtnode_build,
                                "pi_rnode": _demo_pi_build},
            rnode_flash_factory=_demo_rnode_flash,
            on_mitosis=lambda: self.switch_mode("mitosis")))
        self.sm.add_widget(birth)

        triage = Screen(name="triage")
        self.triage_screen = TriageScreen(feed_factory=_triage_feed)
        triage.add_widget(self.triage_screen)
        self.sm.add_widget(triage)

        probe = Screen(name="probe")
        probe.add_widget(ProbeScreen(workflow_factory=_demo_repair_workflow))
        self.sm.add_widget(probe)

        mitosis = Screen(name="mitosis")
        mitosis.add_widget(MitosisScreen(workflow_factory=_demo_clone_workflow))
        self.sm.add_widget(mitosis)

        self.sm.current = os.environ.get("RNM_START", "home")
        root.add_widget(Sidebar(on_select=self.switch_mode))
        root.add_widget(self.sm)
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
                except Exception:
                    pass  # never let a poll error kill the loop
                i += 1
                stop.wait(interval)

        threading.Thread(target=loop, daemon=True).start()

    def on_stop(self):
        stop = getattr(self, "_monitor_stop", None)
        if stop is not None:
            stop.set()

    def switch_mode(self, mode_name):
        if mode_name in [s.name for s in self.sm.screens]:
            self.sm.current = mode_name
