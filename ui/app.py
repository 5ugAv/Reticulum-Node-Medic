"""Application shell — sidebar navigation + screen manager.

Wires the five operating modes to screens. Only Monitor is fully built here;
the other modes are placeholders that later phases replace. Back/Home nav and
the safety panel live at this level so every screen inherits them.
"""

from __future__ import annotations

from kivy.app import App
from kivy.core.window import Window
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.label import Label
from kivy.uix.screenmanager import Screen, ScreenManager

from ui import theme
from ui.widgets.sidebar import Sidebar
from ui.screens.monitor_screen import MonitorScreen
from ui.screens.repair_screen import RepairScreen
from ui.screens.build_screen import BuildScreen
from node_profile import NodeProfile
from transport.connection import EmulatedConnection
from workflows.repair import RepairWorkflow
from workflows.build import BuildWorkflow
from workflows.rtnode_build import RTNodeBuildWorkflow
from workflows.rnode_flash import RNodeFlashWorkflow


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
        Window.size = (1280, 720)

        root = BoxLayout(orientation="horizontal")
        self.sm = ScreenManager()

        monitor = Screen(name="monitor")
        monitor.add_widget(MonitorScreen(nodes=DEMO_NODES))
        self.sm.add_widget(monitor)

        diagnose = Screen(name="diagnose")
        diagnose.add_widget(RepairScreen(workflow_factory=_demo_repair_workflow))
        self.sm.add_widget(diagnose)

        birth = Screen(name="birth")
        birth.add_widget(BuildScreen(
            workflow_factories={"rtnode2400": _demo_rtnode_build,
                                "pi_rnode": _demo_pi_build},
            rnode_flash_factory=_demo_rnode_flash))
        self.sm.add_widget(birth)

        for name, title in (("map", "Map"), ("clone", "Clone Tool")):
            scr = Screen(name=name)
            scr.add_widget(_placeholder(title))
            self.sm.add_widget(scr)

        self.sm.current = "monitor"
        root.add_widget(Sidebar(on_select=self.switch_mode))
        root.add_widget(self.sm)
        return root

    def switch_mode(self, mode_name):
        if mode_name in [s.name for s in self.sm.screens]:
            self.sm.current = mode_name
