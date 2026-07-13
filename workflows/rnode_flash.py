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
        try:
            cmd = flash_command(self.board, self.port, self.band_mhz, self.version)
        except ValueError as exc:
            return StepResult("flash", False, str(exc))
        code, out, err = self.connection.run(cmd, timeout=self.flash_timeout)
        out_l = out.lower()
        if ALREADY_PROVISIONED_MARKER in out_l:
            # Re-inserted an already-flashed board — birthing is already done.
            return StepResult(
                "flash", True,
                f"{self.board.display_name} is already a provisioned RNode — "
                f"no flash needed (wipe the EEPROM first to force a reflash).",
                skipped=True)
        ok = code == 0 and SUCCESS_MARKER in out_l
        return StepResult(
            "flash", ok,
            f"Flashed {self.board.display_name} from the offline cache." if ok
            else f"Flash failed (exit {code}): {(err or out)[-300:]}")

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
