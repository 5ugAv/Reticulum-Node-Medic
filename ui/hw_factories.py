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
    """A stand-in workflow that reports a real problem instead of faking a
    flash. On the medic, a fake 'Done!' (the old EmulatedConnection demo) is
    dangerous — the operator ships a board that was never touched. When no
    flashable board is free, BIRTH must say so plainly; the birth screen turns
    a failed step into its red 'Something didn't finish' banner + retry hint."""

    def __init__(self, step_name: str, message: str):
        self._step = step_name
        self._message = message
        self.results = []

    def run_all(self, on_progress=None):
        r = StepResult(self._step, False, self._message)
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


def local_board_ports(busy_fn: Callable[[str], bool] = _port_busy) -> list:
    """FREE USB serial devices that are safe to flash — every ttyACM/ttyUSB that
    is NOT currently held by another process (so the medic's own radio, held by
    the splitter, is excluded). A freshly-plugged board is free, hence flashable."""
    candidates = sorted(glob.glob("/dev/ttyACM*") + glob.glob("/dev/ttyUSB*"))
    return [p for p in candidates if not busy_fn(p)]


def hardware_present(ports_fn: Callable[[], list] = local_board_ports) -> bool:
    """True only on a real medic (Linux) with a FREE board on USB — the gate
    between a genuine LocalConnection flash and the explorable emulated demo.
    A medic whose only port is its own busy radio reads as no free board."""
    return platform.system() == "Linux" and bool(ports_fn())


def make_rnode_flash(board: RNodeBoard, demo_factory: Callable,
                     connection=None, ports_fn: Callable[[], list] = local_board_ports):
    """Flash a board attached to the medic. Targets a FREE port only (never the
    medic's own busy radio — see local_board_ports). A Heltec V4 is forced to the
    RGB NeoPixel firmware (never stock). Falls back to *demo_factory(board)* when
    no free board is attached."""
    free = ports_fn()
    if not free:
        # No FREE port. On a dev box (or when RNM_DEMO is set) the explorable
        # demo is the point. On the real medic, NEVER fake a flash — say why.
        if platform.system() != "Linux" or os.environ.get("RNM_DEMO"):
            return demo_factory(board)
        attached = all_serial_ports()
        if len(attached) > 1:              # Jonesey + a plugged board that's busy
            msg = ("A board is connected but its port is busy — a previous "
                   "flash may still be holding it. Unplug and replug the board "
                   "(or power-cycle it), wait a few seconds, then retry.")
        else:                              # only the medic's own radio, or nothing
            msg = ("No board detected on USB. Plug the RNode in with a short, "
                   "known-good USB DATA cable (many USB-C cables are charge-only), "
                   "then retry. If it's plugged and dead, hold BOOT, tap RST, "
                   "release BOOT to force download mode.")
        return _HonestFailWorkflow("detect_port", msg)
    if connection is None:
        if platform.system() != "Linux":
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
            return demo_factory()
        connection = LocalConnection()
    return RTNodeBuildWorkflow(connection, NodeProfile())


def make_repair_workflow(demo_factory: Callable, connection=None,
                         ports_fn: Callable[[], list] = local_board_ports):
    """PROBE the medic itself + its attached board over a real LocalConnection."""
    if connection is None:
        if not hardware_present(ports_fn):
            return demo_factory()
        connection = LocalConnection()
    return RepairWorkflow(connection, NodeProfile())
