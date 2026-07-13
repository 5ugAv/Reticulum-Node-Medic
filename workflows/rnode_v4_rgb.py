"""Build & flash the custom Heltec V4 + NeoPixel RNode firmware.

Every Heltec V4 the medic flashes as an RNode gets THIS build rather than stock
RNode firmware: plain ``markqvist/RNode_Firmware`` with a 2-line ``Boards.h``
patch that enables the firmware's built-in NeoPixel status LED on GPIO47 (RGB
state chart: solid blue = RX, solid amber = TX, slow white pulse = idle, solid
white = boot error, ...). The board still identifies as a proper Heltec32 V4
(BOARD_MODEL 0x3F); only the status-LED support is added.

Codifies the two hand-proven scripts (setup_rnode_tools.sh + flash_heltec_v4.sh)
NON-interactively:

* **build** (one-time on the medic): install the arduino-cli ESP32 toolchain,
  clone the firmware, apply the NeoPixel patch, compile -> RNode_Firmware.ino.bin
* **flash** (per board): rnodeconf --autoinstall provisions the V4 EEPROM (V4 IS
  an official RNode target, so autoinstall writes the correct identity + radio
  config), then esptool overwrites the app partition with the NeoPixel firmware,
  then rnodeconf --firmware-hash restamps the stored hash so the device
  signature validates against the new firmware. Finally verify --info.

The exact same ``flash`` sequence is the Repair action for a Heltec V4 whose
EEPROM is invalid / stuck in the solid-white boot-error state: it reprovisions
the EEPROM and restores the known-good RGB firmware in one pass.
"""

from __future__ import annotations

import os
import shlex
from typing import Callable, List, Optional

from transport.connection import Connection
from workflows.build import StepResult, detect_rnode_port
from workflows.rnode_flash import (
    FIRMWARE_VERSION, SUCCESS_MARKER, ALREADY_PROVISIONED_MARKER, flash_command)
from workflows.rnode_boards import get_board

# -- build recipe (setup_rnode_tools.sh) -----------------------------------
FIRMWARE_REPO = "https://github.com/markqvist/RNode_Firmware.git"
FIRMWARE_DIR = "~/RNode_Firmware"
BUILD_SUBDIR = "build/esp32.esp32.esp32s3"
BUILD_BIN = f"{FIRMWARE_DIR}/{BUILD_SUBDIR}/RNode_Firmware.ino.bin"
PARTITION_HASHES = f"{FIRMWARE_DIR}/partition_hashes"
FQBN = "esp32:esp32:esp32s3:CDCOnBoot=cdc"
ESP32_CORE = "esp32:esp32@2.0.17"
#: Arduino libraries RNode_Firmware needs to compile for ESP32 — transcribed
#: from the firmware's own Makefile `prep-esp32` target (Crypto provides the
#: Ed25519/SHA headers; setup_rnode_tools.sh omitted these because the author's
#: build box already had them from prior RNode work — a real hardware gap).
ARDUINO_LIBS = [
    "Adafruit SSD1306",
    "Adafruit SH110X",
    "Adafruit ST7735 and ST7789 Library",
    "Adafruit NeoPixel",
    "XPowersLib",
    "Crypto",
]
#: Heltec32 V4 board id (matches the health-beacon board_id 0x3F "Heltec32 V4").
BOARD_MODEL = 0x3F
#: GPIO the NeoPixel data line sits on (V4 free J2 header pin).
NEOPIXEL_PIN = 47

#: The idempotent Boards.h patcher, carried onto the node before compiling.
LOCAL_PATCH = os.path.join(
    os.path.dirname(__file__), os.pardir, "assets", "scripts",
    "apply_neopixel_patch.py")
REMOTE_PATCH = "/tmp/apply_neopixel_patch.py"

#: The Heltec V4 official autoinstall board (drives the EEPROM provisioning).
V4_BOARD_KEY = "heltec32_v4"


def esptool_path(version: str = FIRMWARE_VERSION) -> str:
    """rnodeconf caches esptool alongside the firmware it downloads; the
    --autoinstall step (run first) guarantees it is present at this path."""
    return f"~/.config/rnodeconf/update/{version}/esptool.py"


def compile_command(firmware_dir: str = FIRMWARE_DIR,
                    board_model: int = BOARD_MODEL) -> str:
    """arduino-cli compile line, transcribed verbatim from setup_rnode_tools.sh
    (no_ota partitions, 2 MB app, -DBOARD_MODEL). ``-e`` exports the binaries so
    they land at BUILD_BIN."""
    return (
        f"cd {firmware_dir} && arduino-cli compile --fqbn {FQBN} -e "
        f'--build-property "build.partitions=no_ota" '
        f'--build-property "upload.maximum_size=2097152" '
        f'--build-property "compiler.cpp.extra_flags=-DBOARD_MODEL=0x{board_model:02X}"')


def esptool_flash_command(port: str, bin_path: str = BUILD_BIN,
                          version: str = FIRMWARE_VERSION,
                          esptool: Optional[str] = None) -> str:
    """esptool write_flash line (verbatim from flash_heltec_v4.sh) that overlays
    the NeoPixel firmware onto the app partition at 0x10000."""
    tool = esptool or esptool_path(version)
    return (
        f"python3 {tool} --port {port} --chip esp32s3 --baud 921600 "
        f"--before default_reset --after hard_reset write_flash "
        f"-z --flash_mode dio --flash_freq 80m --flash_size 16MB "
        f"0x10000 {bin_path}")


def firmware_hash_command(port: str, bin_path: str = BUILD_BIN,
                          partition_hashes: str = PARTITION_HASHES) -> str:
    """Compute the firmware's partition hash from the .bin and stamp it into the
    EEPROM so the device signature validates against the custom firmware."""
    return (
        f"HASH=$(python3 {partition_hashes} {bin_path}) && "
        f'test -n "$HASH" && rnodeconf {port} --firmware-hash "$HASH"')


class HeltecV4RGBWorkflow:
    """Build the NeoPixel firmware (once) and flash a Heltec V4 with it."""

    def __init__(self, connection: Connection, port: Optional[str] = None,
                 band_mhz: int = 915, version: str = FIRMWARE_VERSION,
                 firmware_dir: str = FIRMWARE_DIR,
                 neopixel_pin: int = NEOPIXEL_PIN, board_model: int = BOARD_MODEL,
                 build_timeout: int = 600, flash_timeout: int = 400):
        self.connection = connection
        self.port = port
        self.band_mhz = band_mhz
        self.version = version
        self.firmware_dir = firmware_dir
        self.neopixel_pin = neopixel_pin
        self.board_model = board_model
        self.build_timeout = build_timeout
        self.flash_timeout = flash_timeout
        self.results: List[StepResult] = []

    @property
    def bin_path(self) -> str:
        return f"{self.firmware_dir}/{BUILD_SUBDIR}/RNode_Firmware.ino.bin"

    # -- build steps (one-time firmware compile) ---------------------------

    def _ensure_toolchain(self) -> StepResult:
        # Install arduino-cli itself if missing (Linux install script -> the
        # BINDIR we can reach; ~/.local/bin is already on the wrapped PATH).
        if self.connection.run("command -v arduino-cli")[0] != 0:
            code, out, err = self.connection.run(
                "mkdir -p ~/.local/bin && curl -fsSL "
                "https://raw.githubusercontent.com/arduino/arduino-cli/master/"
                "install.sh | BINDIR=$HOME/.local/bin sh",
                timeout=self.build_timeout)
            if code != 0:
                return StepResult("ensure_toolchain", False,
                                  f"arduino-cli install failed: {(err or out)[-200:]}")
        # arduino-cli core/lib installs are idempotent (no-op when present).
        cmds = [f"arduino-cli core install {ESP32_CORE}"]
        cmds += [f'arduino-cli lib install "{lib}"' for lib in ARDUINO_LIBS]
        for cmd in cmds:
            code, out, err = self.connection.run(cmd, timeout=self.build_timeout)
            if code != 0:
                return StepResult("ensure_toolchain", False,
                                  f"'{cmd}' failed: {(err or out)[-200:]}")
        return StepResult("ensure_toolchain", True,
                          "arduino-cli ESP32 core + NeoPixel/SSD1306 libs ready.")

    def _ensure_source(self) -> StepResult:
        # Clone once; then carry the patcher onto the node and apply it (the
        # patch is idempotent, so re-running is safe).
        if self.connection.run(f"test -d {self.firmware_dir}")[0] != 0:
            code, out, err = self.connection.run(
                f"git clone {FIRMWARE_REPO} {self.firmware_dir}",
                timeout=self.build_timeout)
            if code != 0:
                return StepResult("ensure_source", False,
                                  f"Clone failed: {(err or out)[-200:]}")
        if not self.connection.push_file(LOCAL_PATCH, REMOTE_PATCH):
            return StepResult("ensure_source", False,
                              "Could not carry the NeoPixel patcher to the node.")
        # Reset Boards.h to pristine first so the block-scoped patch always
        # applies against a known anchor (guards against a prior bad patch).
        self.connection.run(
            f"git -C {self.firmware_dir} checkout -- Boards.h")
        code, out, err = self.connection.run(
            f"python3 {REMOTE_PATCH} {self.firmware_dir}/Boards.h "
            f"--pin {self.neopixel_pin}")
        if code != 0:
            return StepResult("ensure_source", False,
                              f"Boards.h patch failed: {(err or out)[-200:]}")
        return StepResult("ensure_source", True,
                          f"Firmware cloned + NeoPixel patch applied ({out.strip()}).")

    def _build_firmware(self) -> StepResult:
        code, out, err = self.connection.run(
            compile_command(self.firmware_dir, self.board_model),
            timeout=self.build_timeout)
        if code != 0:
            return StepResult("build_firmware", False,
                              f"Compile failed: {(err or out)[-300:]}")
        if self.connection.run(f"test -f {self.bin_path}")[0] != 0:
            return StepResult("build_firmware", False,
                              "Compile reported success but no .bin was produced.")
        return StepResult("build_firmware", True,
                          "Built RNode_Firmware.ino.bin with NeoPixel support.")

    # -- flash steps (per board) -------------------------------------------

    def _detect_port(self) -> StepResult:
        port = self.port or detect_rnode_port(self.connection)
        if not port:
            return StepResult("detect_port", False,
                              "No board found — plug in the Heltec V4 (some "
                              "USB-C cables are charge-only).")
        self.port = port
        return StepResult("detect_port", True, f"Board on {port}.")

    def _provision(self) -> StepResult:
        # rnodeconf --autoinstall writes the correct V4 identity + radio config
        # (9 -> enter -> band -> y). We overwrite the firmware next, so an
        # already-provisioned board is fine too.
        board = get_board(V4_BOARD_KEY)
        try:
            cmd = flash_command(board, self.port, self.band_mhz, self.version)
        except ValueError as exc:
            return StepResult("provision", False, str(exc))
        code, out, err = self.connection.run(cmd, timeout=self.flash_timeout)
        out_l = out.lower()
        ok = code == 0 and (SUCCESS_MARKER in out_l
                            or ALREADY_PROVISIONED_MARKER in out_l)
        return StepResult(
            "provision", ok,
            "EEPROM provisioned via autoinstall." if ok
            else f"Provision failed (exit {code}): {(err or out)[-200:]}")

    def _flash_custom(self) -> StepResult:
        if self.connection.run(f"test -f {self.bin_path}")[0] != 0:
            return StepResult("flash_custom", False,
                              "NeoPixel firmware not built yet — run build() first.")
        code, out, err = self.connection.run(
            esptool_flash_command(self.port, self.bin_path, self.version),
            timeout=self.flash_timeout)
        ok = code == 0
        return StepResult(
            "flash_custom", ok,
            "Flashed the NeoPixel firmware over the app partition." if ok
            else f"esptool flash failed (exit {code}): {(err or out)[-200:]}")

    def _set_hash(self) -> StepResult:
        code, out, err = self.connection.run(
            firmware_hash_command(self.port, self.bin_path),
            timeout=self.flash_timeout)
        ok = code == 0
        return StepResult(
            "set_hash", ok,
            "Firmware hash stamped into the EEPROM." if ok
            else f"Could not set firmware hash (exit {code}): {(err or out)[-200:]}")

    def _verify(self) -> StepResult:
        out = self.connection.run(f"rnodeconf {self.port} --info")[1]
        ok = ("EEPROM is invalid" not in out
              and "firmware version" in out.lower())
        return StepResult(
            "verify", ok,
            "Board verified: valid EEPROM + NeoPixel firmware present." if ok
            else "Board did not report a valid RNode after flashing.")

    # -- drivers -----------------------------------------------------------

    _BUILD = ("_ensure_toolchain", "_ensure_source", "_build_firmware")
    _FLASH = ("_detect_port", "_provision", "_flash_custom", "_set_hash",
              "_verify")

    def _run_steps(self, step_names, on_progress):
        emit = on_progress or (lambda r: None)
        for name in step_names:
            result = getattr(self, name)()
            self.results.append(result)
            emit(result)
            if not result.success:
                break
        return self.results

    def build(self, on_progress: Optional[Callable[[StepResult], None]] = None):
        """One-time: compile the NeoPixel firmware on the node."""
        return self._run_steps(self._BUILD, on_progress)

    def flash(self, on_progress: Optional[Callable[[StepResult], None]] = None):
        """Per board: provision + overlay the NeoPixel firmware + verify.
        Assumes build() has produced the .bin. This is also the Repair action."""
        return self._run_steps(self._FLASH, on_progress)

    def run_all(self, on_progress: Optional[Callable[[StepResult], None]] = None):
        self.build(on_progress)
        if self.results and not self.results[-1].success:
            return self.results
        return self.flash(on_progress)
