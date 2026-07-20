"""Flash a supported board as a stock RNode — the RNode option under Birth.

Verified end-to-end on a real Heltec V4: a fresh board is flashed AND provisioned
entirely from the OFFLINE firmware cache by pre-feeding rnodeconf --autoinstall's
answers via stdin (device index -> enter -> band -> confirm). No interactive
terminal and no internet needed once the firmware cache is seeded.

Flow: detect the port -> refuse to guess between multiple boards -> ensure the
firmware is cached (sync when online, else use the carried cache) -> flash ->
verify the board reports as a provisioned RNode.
"""

from __future__ import annotations

import shlex
from typing import Callable, List, Optional

from transport.connection import Connection
from workflows.build import StepResult, detect_rnode_port
from workflows.rnode_boards import RNodeBoard
from workflows.radio_params import set_params_at_birth
from workflows.updater import (
    autoinstall_command, sync_firmware, has_connectivity, RNODE_UPDATE_DIR)

#: Firmware bundle version the tool carries / targets.
FIRMWARE_VERSION = "1.86"
#: rnodeconf prints this once a device is flashed AND provisioned.
SUCCESS_MARKER = "autoinstallation complete"
#: rnodeconf refuses (exit 0) to re-flash an already-provisioned RNode.
ALREADY_PROVISIONED_MARKER = "already installed and provisioned"


def flash_command(board: RNodeBoard, port: str, band_mhz: int = 915,
                  version: str = FIRMWARE_VERSION) -> str:
    """The exact non-interactive offline flash command (verified on hardware):
    pre-feed the autoinstall answers via stdin into rnodeconf --autoinstall
    --nocheck. Raises ValueError if the board's sequence isn't verified for the
    requested band."""
    answers = board.autoinstall_answers(band_mhz)
    ans = " ".join(shlex.quote(a) for a in answers)
    return (f"printf '%s\\n' {ans} | "
            + autoinstall_command(port, version=version, offline=True))


#: Prompt patterns rnodeconf --autoinstall shows, paired with the answer index
#: in board.autoinstall_answers() ([device_index, '', band, 'y']). The confirm
#: and "hit enter" prompts read a keypress from the TERMINAL (not stdin), so they
#: are driven through a PTY (connection.run_interactive), never a stdin pipe.
_AUTOINSTALL_PROMPTS = ("matches your device type", "Hit enter to continue",
                        "What band", "Is the above correct")


def autoinstall_interactions(board: RNodeBoard, band_mhz: int = 915):
    """``(regex, response)`` pairs that drive rnodeconf --autoinstall through its
    terminal prompts for *board*. Raises ValueError (via autoinstall_answers) if
    the board's flash sequence isn't verified for *band_mhz*."""
    answers = board.autoinstall_answers(band_mhz)
    return list(zip(_AUTOINSTALL_PROMPTS, answers))


def _autoinstall_ok(code: int, out: str) -> bool:
    low = out.lower()
    return (ALREADY_PROVISIONED_MARKER in low) or (SUCCESS_MARKER in low)


def birth_flash(connection: Connection, board: RNodeBoard, port: str,
                band_mhz: int = 915, version: str = FIRMWARE_VERSION,
                timeout: int = 400):
    """Flash + provision a board as an RNode, with the fresh-board second pass.

    A BRAND-NEW (never-flashed) ESP32 re-enumerates onto its RNode USB identity
    after the first firmware write, so a single ``--autoinstall`` typically
    flashes the firmware but cannot finish writing the EEPROM (identity, hash,
    signature) before the port changes underneath it. nodemedic makes the second
    pass part of the birth: run autoinstall once, and if the board was not
    already an RNode, run it a second time — now that firmware is present, the
    provisioning completes reliably. An already-provisioned board births in a
    single pass (no needless reflash).

    On a real local board (a connection exposing ``run_interactive``) the
    autoinstall is driven through a PTY, because rnodeconf's confirm/continue
    prompts read a keypress from the terminal — a plain ``printf | rnodeconf``
    pipe hangs there forever and wedges the USB port. The emulated/remote path
    keeps the pre-fed-stdin command.

    Returns ``(ok, message, already_provisioned)``.
    """
    try:
        interactions = autoinstall_interactions(board, band_mhz)   # validates band
    except ValueError as exc:
        return False, str(exc), False

    if hasattr(connection, "run_interactive"):
        cmd = board.autoinstall_command(port, version=version, offline=True)
        code, out, _ = connection.run_interactive(cmd, interactions, timeout)
        if ALREADY_PROVISIONED_MARKER in out.lower():
            return True, "already a provisioned RNode", True
        if not _autoinstall_ok(code, out):
            # Second pass after the fresh-board re-enumeration finishes the EEPROM.
            code, out, _ = connection.run_interactive(cmd, interactions, timeout)
        ok = _autoinstall_ok(code, out)
        return (ok,
                "flashed + provisioned via autoinstall (PTY-driven)" if ok
                else f"autoinstall did not complete: {out[-200:]}",
                False)

    # Emulated / remote fallback: pre-feed the answers via stdin.
    cmd = flash_command(board, port, band_mhz, version)
    code, out, err = connection.run(cmd, timeout=timeout)
    if ALREADY_PROVISIONED_MARKER in out.lower():
        return True, "already a provisioned RNode", True
    code, out, err = connection.run(cmd, timeout=timeout)
    out_l = out.lower()
    ok = code == 0 and (SUCCESS_MARKER in out_l
                        or ALREADY_PROVISIONED_MARKER in out_l)
    return (ok,
            "flashed + provisioned over two passes (new board)" if ok
            else f"flash failed (exit {code}): {(err or out)[-200:]}",
            False)


class RNodeFlashWorkflow:
    def __init__(self, connection: Connection, board: RNodeBoard,
                 port: Optional[str] = None, band_mhz: int = 915,
                 version: str = FIRMWARE_VERSION, flash_timeout: int = 400):
        self.connection = connection
        self.board = board
        self.port = port
        self.band_mhz = band_mhz
        self.version = version
        self.flash_timeout = flash_timeout
        self.results: List[StepResult] = []

    # -- steps -------------------------------------------------------------

    def _detect_port(self) -> StepResult:
        port = self.port or detect_rnode_port(self.connection)
        if not port:
            return StepResult("detect_port", False,
                              "No board found — plug it in (some USB-C cables "
                              "are charge-only).")
        self.port = port
        return StepResult("detect_port", True, f"Board on {port}.")

    def _ensure_single_board(self) -> StepResult:
        # Flashing erases/re-provisions the EEPROM; never guess between boards.
        out = self.connection.run("ls /dev/ttyACM* /dev/ttyUSB* 2>/dev/null")[1]
        ports = [p for p in out.split() if p.startswith("/dev/")]
        if len(ports) > 1:
            return StepResult(
                "ensure_single_board", False,
                f"{len(ports)} boards connected ({', '.join(ports)}). Unplug "
                f"all but the one you want to flash.")
        return StepResult("ensure_single_board", True, "One board connected.")

    def _ensure_firmware(self) -> StepResult:
        if has_connectivity(self.connection):
            res = sync_firmware(self.connection)
            if res.failed:
                return StepResult("ensure_firmware", False,
                                  f"Firmware sync failed for "
                                  f"{', '.join(res.failed[:3])}.")
            return StepResult("ensure_firmware", True,
                              f"Firmware ready ({res.message}).")
        # Offline: the carried cache must already hold this version.
        if self.connection.run(f"ls {RNODE_UPDATE_DIR}/{self.version}/*.zip")[0] != 0:
            return StepResult("ensure_firmware", False,
                              f"Offline and no firmware {self.version} cached. "
                              f"Connect WiFi once to seed the cache.")
        return StepResult("ensure_firmware", True,
                          f"Offline — using cached firmware {self.version}.")

    def _flash(self) -> StepResult:
        if self.board.flash_method != "autoinstall":
            return StepResult("flash", False,
                              f"{self.board.key} is a custom board — flash it "
                              f"with its arduino-cli flasher.")
        ok, msg, already = birth_flash(self.connection, self.board, self.port,
                                       self.band_mhz, self.version,
                                       self.flash_timeout)
        if already:
            # Re-inserted an already-flashed board — birthing is already done.
            return StepResult(
                "flash", True,
                f"{self.board.display_name} is already a provisioned RNode — "
                f"no flash needed (wipe the EEPROM first to force a reflash).",
                skipped=True)
        return StepResult(
            "flash", ok,
            f"Flashed {self.board.display_name} from the offline cache — {msg}."
            if ok else f"Flash failed: {msg}")

    def _set_params(self) -> StepResult:
        # Bake the canonical radio params into the EEPROM AT BIRTH and leave the
        # board host-controlled, so a Pi's rnsd never aborts on a stale
        # 250/SF11 default ("Radio state mismatch").
        ok, detail = set_params_at_birth(self.connection, self.port,
                                         timeout=self.flash_timeout)
        return StepResult("set_params", ok, detail)

    def _verify(self) -> StepResult:
        out = self.connection.run(f"rnodeconf {self.port} --info")[1]
        ok = "Device signature" in out and "Firmware version" in out
        return StepResult(
            "verify", ok,
            "Board verified as a provisioned RNode." if ok
            else "Board did not report as a valid RNode after flashing.")

    # -- driver ------------------------------------------------------------

    def run_all(self, on_progress: Optional[Callable[[StepResult], None]] = None):
        emit = on_progress or (lambda r: None)
        for step in (self._detect_port, self._ensure_single_board,
                     self._ensure_firmware, self._flash, self._set_params,
                     self._verify):
            result = step()
            self.results.append(result)
            emit(result)
            if not result.success:
                break
        return self.results
