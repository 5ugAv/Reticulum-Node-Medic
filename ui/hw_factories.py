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
import re
import subprocess
from typing import Callable

from node_profile import NodeProfile
from transport.connection import LocalConnection
from workflows.build import StepResult
from workflows.repair import BOARD_MODULES, RTNODE_MODULES, RepairWorkflow
from workflows.rnode_boards import RNodeBoard
from workflows.rnode_flash import RNodeFlashWorkflow
from workflows.rnode_v4_rgb import (
    V4_BOARD_KEY, HeltecV4RGBWorkflow, rgb_firmware_available,
    rgb_build_possible)
from workflows.rtnode_build import RTNodeBuildWorkflow
from ui.i18n import tr  # i18n: PROBE's refusals (ledger #93)


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
    # FAIL CLOSED, like the roster documents: a port whose USB serial can't be
    # resolved is NOT provably a work board, and flashing the medic's own radio
    # is catastrophic while refusing a genuine work board is a mild annoyance
    # (2026-08-01 bug hunt: this list was fail-OPEN, unlike assert_flashable).
    from ui.onboard_roster import serial_for_port, guard_is_active
    strict = guard_is_active()            # real medic (udev/roster present)
    out = []
    for p in candidates:
        if busy_fn(p) or onboard_fn(p):
            continue
        if strict:
            try:
                if not serial_for_port(p):
                    continue              # unidentifiable -> never a target
            except Exception:
                continue
        out.append(p)
    return out


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
    RGB NeoPixel firmware (never stock) — built first on a medic that hasn't
    yet, or an honest refusal when it can't be built here. Falls back to
    *demo_factory(board)* when no free board is attached."""
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
    if len(free) > 1:
        # TWO work boards: the V4 colour build and the RTNode build took
        # free[0] with no count, so a V3 beside a V4 was a coin toss — and
        # mixing their images boot-loops the board (readiness sweep, 2026-10-03).
        return _HonestFailWorkflow(
            "detect_port",
            "More than one board is plugged into the medic. Unplug all but the "
            "one you want to flash, then start again.", "Two boards plugged in")
    port = free[0]                         # the freshly-plugged board, not Jonesey
    if board.key == V4_BOARD_KEY:
        # RGB is imperative for a boxed V4 — never stock. The dedicated
        # workflow flashes the built firmware, or COMPILES it first when the
        # toolchain and source are aboard or the medic is online to fetch them
        # (run_all skips the compile once the .bin exists). A fresh medic used
        # to be sent to the stock flash here, because the only workflow that
        # builds the colour firmware was only ever chosen once it was already
        # built (readiness ledger #44).
        if rgb_firmware_available():
            return HeltecV4RGBWorkflow(connection, port=port)
        from workflows.updater import has_connectivity
        if rgb_build_possible(online=lambda: has_connectivity(connection)):
            return HeltecV4RGBWorkflow(connection, port=port)
        return _HonestFailWorkflow(
            "build_firmware",
            "This medic hasn't built the Heltec V4 colour firmware yet, and "
            "the tools to build it aren't aboard (arduino-cli, its ESP32 core "
            "and the RNode firmware source) — with no internet to fetch them. "
            "Put the medic on Wi-Fi and try again (the first build fetches "
            "them), or clone this medic from one that has them.",
            "V4 colour firmware not built")
    return RNodeFlashWorkflow(connection, board, port=port,
                              work_ports_fn=ports_fn)


def make_rtnode_build(demo_factory: Callable, connection=None,
                      ports_fn: Callable[[], list] = local_board_ports,
                      target=None, node_name: str = ""):
    """Build an RTNode-2400 on an ESP32-S3 board attached to the medic. *target*
    selects the RTNode-2400 variant (Heltec V4 / T-Beam Supreme); None uses the
    default. *node_name* names the node — it flows into the portal /save form so
    the board takes the name and joins the medic's WiFi automatically."""
    from workflows.rtnode_build import DEFAULT_TARGET
    board_port = None
    real = connection is None
    if connection is None:
        ports = list(ports_fn())          # WORK boards only (excludes Jonesey)
        if not ports:
            if demo_allowed():
                return demo_factory()
            return _HonestFailWorkflow("detect_board",
                "Building an RTNode-2400 needs the board plugged into the "
                "medic. Connect it with a known-good USB DATA cable, then start "
                "the build again.", "No board attached")
        if len(ports) > 1:
            return _HonestFailWorkflow(
                "detect_board",
                "More than one board is plugged into the medic. Unplug all but "
                "the one you want to build on, then start again.",
                "Two boards plugged in")
        board_port = ports[0]             # pin to the work board, never the radio
        connection = LocalConnection()
    # On a REAL medic build, auto-provision over the RTNode-Setup AP with live
    # nmcli/HTTP functions; a bare/emulated workflow leaves it off (no WiFi hop).
    kw = {}
    if real:
        from workflows import rtnode_portal as rp
        kw = dict(auto_provision=True, node_name=node_name,
                  provision=rp.provision_node,
                  wifi_credentials=rp.medic_wifi_credentials,
                  join_ap=rp._default_join_ap, post=rp._default_post,
                  rejoin=rp.rejoin_medic_wifi)
    # The profile carries the SAVED tool-wide radio defaults (Settings ▸
    # Default radio parameters) — an operator in another region sets them
    # once and every birth, including the RTNode portal pre-fill, follows.
    profile = NodeProfile()
    try:
        from provisioning.radio_defaults import load_radio_config
        profile.radio = load_radio_config()
    except Exception:
        pass                               # canonical fallback stays baked in
    return RTNodeBuildWorkflow(connection, profile,
                               target=target or DEFAULT_TARGET,
                               board_port=board_port, **kw)


def probe_target(ports_fn: Callable[[], list] = local_board_ports) -> tuple:
    """What PROBE would be pointed at RIGHT NOW: ``("none" | "one" | "many",
    ports)``. Two free boards is a state of its own — PROBE must say so and
    refuse, never check whichever happens to sort first (operator,
    2026-10-04: "perhaps the user might have two boards plugged in at once")."""
    free = list(ports_fn() or [])
    state = "none" if not free else ("one" if len(free) == 1 else "many")
    return state, free


def port_label(port: str) -> str:
    """A human name for a USB serial port — the product string the device
    announces (from its /dev/serial/by-id link), then the port itself:
    "Espressif USB JTAG serial debug unit on /dev/ttyACM1". The device's
    serial number is dropped; the port alone when there is no by-id link."""
    try:
        for link in glob.glob("/dev/serial/by-id/*"):
            if os.path.realpath(link) != os.path.realpath(port):
                continue
            base = os.path.basename(link)
            m = re.match(r"usb-(.+?)-if\d+", base)
            words = (m.group(1) if m else base).split("_")
            if len(words) > 1 and re.fullmatch(r"[0-9A-Fa-f:]+", words[-1]):
                words = words[:-1]
            return " ".join(w for w in words if w) + f" on {port}"
    except Exception:                                              # noqa: BLE001
        pass
    return port


def board_identity(port: str) -> dict:
    """What the medic knows about the board on *port* from its own records:
    ``{"hw_serial", "name", "type", "known"}``. Birth writes a board's USB
    serial into the kin roster, so a node this medic built is recognised the
    moment it is plugged back in."""
    from ui.onboard_roster import serial_for_port
    from monitor.kin_roster import node_for_hw_serial
    out = {"hw_serial": None, "name": "", "type": "", "known": False}
    hit = None
    try:
        out["hw_serial"] = serial_for_port(port)
        if out["hw_serial"]:
            hit = node_for_hw_serial(out["hw_serial"])
    except Exception:                                              # noqa: BLE001
        hit = None
    if hit:
        out.update(name=str(hit.get("name") or ""), type=str(hit.get("type") or ""),
                   known=True)
    return out


_KIND_WORDS = {"rtnode2400": "RTNode-2400", "pi_propagation": "Pi propagation node",
               "pi": "Pi node"}

#: How long PROBE listens to an RTNode's serial after the reset pulse: the
#: boot log comes within seconds and the first health beacon within a minute
#: (6 s on the bench, 2026-10-04; the firmware's documented ~30 s elsewhere).
RTNODE_LISTEN_S = 40


def board_label(port: str) -> str:
    """The PROBE header's name for the board on *port*: the node's own name
    when the roster knows it ("5A59 — RTNode-2400 built by this medic, on
    Port 3 (/dev/ttyACM1)"), else the chip's product string and the port."""
    ident = board_identity(port)
    if ident["known"] and ident["name"]:
        try:
            from ui.usb_ports import describe_port
            where = describe_port(port)
        except Exception:                                          # noqa: BLE001
            where = port
        kind = _KIND_WORDS.get(ident["type"], ident["type"] or "node")
        return f"{ident['name']} — {kind} built by this medic, on {where}"
    return port_label(port)


def probe_target_label(ports_fn: Callable[[], list] = local_board_ports) -> tuple:
    """``(state, label)`` for the PROBE header: the named board when there is
    exactly one, else an empty label and the state for the screen to word."""
    state, ports = probe_target(ports_fn)
    return state, (board_label(ports[0]) if state == "one" else "")


def make_repair_workflow(demo_factory: Callable, connection=None,
                         ports_fn: Callable[[], list] = local_board_ports):
    """PROBE the attached WORK board over a real LocalConnection. The profile's
    serial port is pinned to the free work board (never the medic's own radio),
    so PROBE diagnoses the plugged-in board directly instead of auto-detecting
    onto — and gating on — the medic's own live rnsd radio (Jonesey).

    Pointed at a board on the medic's own USB, the workflow runs the BOARD
    module only (workflows.repair.BOARD_MODULES): the other six inspect the
    host they run on, which over LocalConnection is the medic itself, and on
    2026-10-04 that charged an RTNode with fourteen "faults" that were the
    medic's own. Two free boards is a refusal, not a guess. The workflow
    carries ``target_label`` so the screen can say which board it is on."""
    state, free = probe_target(ports_fn)
    board_only = False
    if connection is None:
        if state == "many" and platform.system() == "Linux":
            return _HonestFailWorkflow("detect_board",
                tr("Two boards are plugged in, so PROBE cannot tell which one you "
                   "mean. Leave just the one you want checked on the medic, then "
                   "run PROBE again."),
                tr("Which board?"))
        if not free or platform.system() != "Linux":
            if demo_allowed():
                return demo_factory()
            return _HonestFailWorkflow("detect_board",
                tr("PROBE checks a real board's firmware and radio, so it needs one "
                   "attached. Plug the RNode/node board into the medic with a "
                   "known-good USB DATA cable, then run PROBE again."),
                tr("No board to PROBE"))
        connection = LocalConnection()
        board_only = True
    profile = NodeProfile()
    # PROBE compares the board's params against this profile and its auto-fix
    # REWRITES the board to match — so it must carry the operator's SAVED
    # defaults, not the factory 915.125 (2026-08-01 bug hunt: on a medic set
    # to an EU preset, PROBE flagged correct boards and 'fixed' them onto the
    # wrong — possibly unlicensed — frequency).
    try:
        from provisioning.radio_defaults import load_radio_config
        saved = load_radio_config()
        saved.serial_port = profile.radio.serial_port
        profile.radio = saved
    except Exception:
        pass
    if free:
        profile.radio.serial_port = free[0]      # the attached work board
    modules, capture = None, None
    if board_only:
        modules = BOARD_MODULES
        if board_identity(free[0])["type"] == "rtnode2400":
            # The board's own birth record says RTNode-2400: its USB serial
            # carries a log, never KISS, so the RNode radio check (rnodeconf)
            # can only ever say "couldn't read it". Listen instead.
            from workflows.rtnode_build import serial_capture_cmd
            modules = RTNODE_MODULES
            capture = serial_capture_cmd(free[0], seconds=RTNODE_LISTEN_S)
    wf = RepairWorkflow(connection, profile, modules=modules)
    if capture:
        for m in wf.modules:
            if hasattr(m, "capture_cmd"):
                m.capture_cmd = capture
    wf.target_label = board_label(free[0]) if free else ""
    return wf
