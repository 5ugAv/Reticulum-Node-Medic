"""Real-hardware workflow factories, with an emulated fallback.

The UI screens were wired to ``EmulatedConnection`` demos, so on-medic flashing/
building/diagnosing was FAKED (the on-screen ``[ok]`` never touched a board). This
module makes the LOCAL paths real: when a board is attached to the medic's own USB
(``LocalConnection``), it runs the genuine workflow; when nothing is attached (a
dev box, or the medic with no board), it returns the explorable demo so the UI
still works.

Local (self-contained on the medic) → real here:
  * RNode flash — and a Heltec V4 is ALWAYS the NeoPixel/RGB build (a boxed node's
    only RX/TX signal is the LED, so stock is not shippable).
  * RTNode-2400 build.
  * PROBE (diagnose/repair the medic + its attached board).

Remote targets (Pi + RNode, Mitosis) need a selected host + credentials — that
target-selection flow is separate; those stay on the demo until it's wired.
"""

from __future__ import annotations

import glob
import os
import platform
import subprocess
from typing import Callable

from node_profile import NodeProfile
from transport.connection import LocalConnection
from workflows.build import StepResult
from workflows.repair import RepairWorkflow
from workflows.rnode_boards import RNodeBoard
from workflows.rnode_flash import RNodeFlashWorkflow
from workflows.rnode_v4_rgb import (
    V4_BOARD_KEY, HeltecV4RGBWorkflow, rgb_firmware_available)
from workflows.rtnode_build import RTNodeBuildWorkflow


class _HonestFailWorkflow:
    """A stand-in for a workflow that CAN'T run — because the required hardware
    isn't attached, or that path isn't wired to real hardware yet. On the medic a
    fake 'Done!' (the old EmulatedConnection demo) is dangerous: the operator
    trusts a board/node that was never touched. Screens detect ``is_blocked`` and
    show a plain requirement popup ('No board attached — plug one in') instead of
    running anything or faking success."""

    #: Screens check this to pop a requirement dialog rather than run the workflow.
    is_blocked = True
    #: Some screens read ``.steps`` before running; keep it safe (empty).
    steps: list = []

    def __init__(self, step_name: str, message: str, title: str = "Heads up",
                 under_construction: bool = False):
        self._step = step_name
        self.message = message
        self.title = title            # popup heading, tailored to the process
        # True = a feature that's simply not built yet (vs a hardware requirement).
        # Hitting one is logged for the developer (ui.construction_log).
        self.under_construction = under_construction
        self.results = []

    def run_all(self, on_progress=None):
        r = StepResult(self._step, False, self.message)
        self.results = [r]
        if on_progress:
            on_progress(r)
        return self.results


def all_serial_ports() -> list:
    """Every ttyACM/ttyUSB present, free or busy — used to tell 'no board
    plugged' (only the medic's own radio, or nothing) from 'a board is here but
    its port is held' (a wedged previous flash)."""
    return sorted(glob.glob("/dev/ttyACM*") + glob.glob("/dev/ttyUSB*"))


def _port_busy(port: str, runner: Callable = None) -> bool:
    """Is *port* already held open by another process? The medic's OWN radio
    (Jonesey, held by rnsd/the splitter) MUST never be a flash target — writing
    it would corrupt/brick the medic. ``fuser <port>`` exits 0 iff something
    holds it. Fail CLOSED: if we can't tell, treat it as busy, so we never risk
    the medic's radio for the sake of convenience."""
    run = runner or (lambda argv: subprocess.run(
        argv, capture_output=True, timeout=5).returncode)
    try:
        return run(["fuser", port]) == 0
    except Exception:
        return True                       # uncertain -> exclude (safe)


def local_board_ports(busy_fn: Callable[[str], bool] = _port_busy,
                      onboard_fn: Callable[[str], bool] = None) -> list:
    """FREE USB serial devices that are safe to flash/PROBE — WORK boards, not the
    medic's own infrastructure. A port is excluded if it is (a) held by another
    process (busy) OR (b) one of the medic's OWN permanent boards by USB serial
    identity (Jonesey's LoRa radio, the GPS Tracker — see ui.onboard_roster). The
    identity check is the robust one: busy alone fails dangerously if rnsd is
    stopped for maintenance (the medic's radio would look free/flashable)."""
    from ui.onboard_roster import is_onboard, service_bound_serials
    if onboard_fn is None:
        # Two-layer onboard exclusion: the identity roster (persistent — survives
        # rnsd being stopped) OR the boards the medic's own services are bound to
        # (the live "operating like Jonesey => it's mine" signal).
        svc = service_bound_serials()
        onboard_fn = lambda p: is_onboard(p, service_serials=svc)
    candidates = sorted(glob.glob("/dev/ttyACM*") + glob.glob("/dev/ttyUSB*"))
    return [p for p in candidates if not busy_fn(p) and not onboard_fn(p)]


def hardware_present(ports_fn: Callable[[], list] = local_board_ports) -> bool:
    """True only on a real medic (Linux) with a FREE board on USB — the gate
    between a genuine LocalConnection flash and the explorable emulated demo.
    A medic whose only port is its own busy radio reads as no free board."""
    return platform.system() == "Linux" and bool(ports_fn())


def demo_allowed() -> bool:
    """Whether an EMULATED demo may stand in for real hardware. The rule: NEVER on
    the deployed medic (Linux) unless explicitly opted in. A fake 'Done!' there is
    dangerous — the operator trusts a board/node that was never touched (the
    ok/ok/ok birth-certificate trap). A non-Linux dev box has no real hardware to
    fool anyone with, so demos keep the UI explorable there; ``RNM_DEMO=1`` forces
    them on anywhere. On the medic without the flag, every screen does the real
    thing or honestly says it can't — see _HonestFailWorkflow."""
    return platform.system() != "Linux" or bool(os.environ.get("RNM_DEMO"))


def make_rnode_flash(board: RNodeBoard, demo_factory: Callable,
                     connection=None, ports_fn: Callable[[], list] = local_board_ports):
    """Flash a board attached to the medic. Targets a FREE port only (never the
    medic's own busy radio — see local_board_ports). A Heltec V4 is forced to the
    RGB NeoPixel firmware (never stock). Falls back to *demo_factory(board)* when
    no free board is attached."""
    free = ports_fn()
    if not free:
        # No FREE port. Only an explicit opt-in (RNM_DEMO on a dev box) may show
        # the explorable demo. On the real medic, NEVER fake a flash — say why.
        if demo_allowed():
            return demo_factory(board)
        attached = all_serial_ports()
        if len(attached) > 1:              # Jonesey + a plugged board that's busy
            msg = ("A board is connected, but its USB port is busy — a previous "
                   "flash may still be holding it. Unplug and replug the board "
                   "(or power-cycle it), wait a few seconds, then try again.")
            title = "Board port is busy"
        else:                              # only the medic's own radio, or nothing
            msg = ("There's no RNode to flash. Plug it into the medic with a "
                   "short, known-good USB DATA cable (many USB-C cables are "
                   "charge-only). If it's plugged in but dead: hold BOOT, tap "
                   "RST, release BOOT to force download mode, then try again.")
            title = "No board attached"
        return _HonestFailWorkflow("detect_port", msg, title)
    if connection is None:
        if demo_allowed():
            return demo_factory(board)
        connection = LocalConnection()
    port = free[0]                         # the freshly-plugged board, not Jonesey
    if board.key == V4_BOARD_KEY and rgb_firmware_available():
        # RGB is imperative for a boxed V4 — the dedicated build+flash workflow
        # (run_all skips the compile when the firmware is already built).
        return HeltecV4RGBWorkflow(connection, port=port)
    return RNodeFlashWorkflow(connection, board, port=port)


def make_rtnode_build(demo_factory: Callable, connection=None,
                      ports_fn: Callable[[], list] = local_board_ports):
    """Build an RTNode-2400 on an ESP32 board attached to the medic."""
    if connection is None:
        if not hardware_present(ports_fn):
            if demo_allowed():
                return demo_factory()
            return _HonestFailWorkflow("detect_board",
                "Building an RTNode-2400 needs its ESP32 board plugged into the "
                "medic. Connect it with a known-good USB DATA cable, then start "
                "the build again.", "No board attached")
        connection = LocalConnection()
    return RTNodeBuildWorkflow(connection, NodeProfile())


def make_repair_workflow(demo_factory: Callable, connection=None,
                         ports_fn: Callable[[], list] = local_board_ports):
    """PROBE the attached WORK board over a real LocalConnection. The profile's
    serial port is pinned to the free work board (never the medic's own radio),
    so PROBE diagnoses the plugged-in board directly instead of auto-detecting
    onto — and gating on — the medic's own live rnsd radio (Jonesey)."""
    free = ports_fn()
    if connection is None:
        if not free or platform.system() != "Linux":
            if demo_allowed():
                return demo_factory()
            return _HonestFailWorkflow("detect_board",
                "PROBE checks a real board's firmware and radio, so it needs one "
                "attached. Plug the RNode/node board into the medic with a "
                "known-good USB DATA cable, then run PROBE again.",
                "No board to PROBE")
        connection = LocalConnection()
    profile = NodeProfile()
    if free:
        profile.radio.serial_port = free[0]      # the attached work board
    return RepairWorkflow(connection, profile)
