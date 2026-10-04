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
  signature validates against the new firmware, then the canonical radio params
  are baked in AT BIRTH (workflows.radio_params) so the board leaves provisioning
  host-controlled and on the deployment config — without this it keeps
  autoinstall's stale 250/SF11 default and rnsd aborts with "Radio state
  mismatch" (a fault once mis-blamed on this firmware; the RGB build itself runs
  clean under rnsd, verified on the medic's own RNode). Finally verify --info.

The exact same ``flash`` sequence is the Repair action for a Heltec V4 whose
EEPROM is invalid / stuck in the solid-white boot-error state: it reprovisions
the EEPROM and restores the known-good RGB firmware in one pass.
"""

from __future__ import annotations

import os
import re
import shlex
from typing import Callable, List, Optional

from transport.connection import Connection
from workflows.build import StepResult, detect_rnode_port
from workflows.rnode_flash import FIRMWARE_VERSION, birth_flash
from workflows.rnode_boards import get_board
from workflows.radio_params import set_params_at_birth
from node_profile import RadioConfig

# -- build recipe (setup_rnode_tools.sh) -----------------------------------
FIRMWARE_REPO = "https://github.com/markqvist/RNode_Firmware.git"
FIRMWARE_DIR = "~/RNode_Firmware"
#: ONE BUILD DIRECTORY PER BOARD MODEL, and never arduino-cli's default.
#:
#: arduino-cli keys its build directory by FQBN alone — every ESP32-S3 target
#: in this sketch shares `build/esp32.esp32.esp32s3`, whatever -DBOARD_MODEL
#: says. So a T-Deck build (0x3B) done in this tree on 2026-08-27 silently
#: overwrote the Heltec V4 artifact, and because the flash path skips
#: compiling "when the firmware is already built" — a test that a FILE EXISTS,
#: not that it is OUR file — every V4 birthed afterwards was flashed with
#: T-Deck firmware.
#:
#: It ran. Both boards carry an SX1262, so the radio was perfect and the board
#: validated; the T-Deck's display is a different driver entirely, so the OLED
#: stayed dark and CONF_DSET was never written. Found 2026-09-11 by comparing
#: a working V4 (COBE, built 2026-07-31, reports c3:c8:3F) against one birthed
#: that morning (c3:c8:3B = BOARD_TDECK).
#:
#: So the path carries the board model. This constant is the Heltec V4's, the
#: model this recipe is for; build_dir_for() builds the same string for any
#: other model, and they can no longer collide.
BUILD_SUBDIR = "build/rnm-board-0x3F"
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

#: The boot-error LED patcher: recolour the stuck-white fault indicator to dim
#: red so a boot-errored board draws little current and can still be reflashed.
LOCAL_BOOT_ERR = os.path.join(
    os.path.dirname(__file__), os.pardir, "assets", "scripts",
    "apply_boot_error_color.py")
REMOTE_BOOT_ERR = "/tmp/apply_boot_error_color.py"

#: The birth-cry patcher: the operator-tuned ~13 s first-flash celebration
#: (identical choreography to the RTNode-2400 cry), ending in the firmware's
#: own white standby breathe. Once per build (NVS stamp).
LOCAL_BIRTH_CRY = os.path.join(
    os.path.dirname(__file__), os.pardir, "assets", "scripts",
    "apply_birth_cry.py")
REMOTE_BIRTH_CRY = "/tmp/apply_birth_cry.py"
#: Red channel (pre-NP_M) for the boot-error LED — dim but visible, low current.
BOOT_ERROR_RED = 0x40

#: The Heltec V4 official autoinstall board (drives the EEPROM provisioning).
V4_BOARD_KEY = "heltec32_v4"

# -- carried RGB flash (Pi+RNode nodes) ------------------------------------
#: Tool-host paths to the compiled RGB artifacts that build() produces on the
#: medic. A target Pi has no arduino toolchain, so instead of rebuilding there
#: the medic CARRIES these to the target and overlays them.
RGB_LOCAL_BIN = os.path.expanduser(BUILD_BIN)
RGB_LOCAL_HASHER = os.path.expanduser(PARTITION_HASHES)
#: Where the carried artifacts are staged on the target before the overlay.
REMOTE_RGB_BIN = "/tmp/rnm_rgb_firmware.bin"
REMOTE_RGB_HASHER = "/tmp/rnm_partition_hashes"

#: Flash baud for the esptool overlay. 921600 is fast but the more common cause
#: of "serial noise / stream stopped" mid-write on marginal cables/hubs; 460800
#: is a well-supported, markedly more reliable default. Overridable per call.
DEFAULT_FLASH_BAUD = 460800

# -- PROVEN full-image birth recipe (radio comes ONLINE) -------------------
# Validated end-to-end on a clean V4 2026-07-20: erase -> flash the COMPLETE RGB
# image (--flash_size detect) -> provision as the VENDOR Heltec V4 model (c3/c8)
# -> set the firmware hash from the image's embedded trailing 32 bytes -> params.
# See memory rgb-custom-firmware-needs-homebrew-provision for the full root cause.
BUILD_DIR = f"{FIRMWARE_DIR}/{BUILD_SUBDIR}"
BUILD_BOOTLOADER = f"{BUILD_DIR}/RNode_Firmware.ino.bootloader.bin"
BUILD_PARTITIONS = f"{BUILD_DIR}/RNode_Firmware.ino.partitions.bin"
#: boot_app0 (OTA-data init) — the arduino-esp32 core ships it; identical for all
#: ESP32 Arduino builds. Same version pin as ESP32_CORE.
BOOT_APP0 = ("~/.arduino15/packages/esp32/hardware/esp32/2.0.17/"
             "tools/partitions/boot_app0.bin")
from workflows.rnode_flash import esptool_cmd as _esptool_cmd
#: Derived, never literal — see rnode_flash.esptool_path.
ESPTOOL = _esptool_cmd()
ESP_CHIP = "esp32s3"
#: Standard ESP32-S3 Arduino flash offsets.
OFF_BOOTLOADER, OFF_PARTITIONS, OFF_BOOT_APP0, OFF_APP = 0x0, 0x8000, 0xe000, 0x10000
#: Vendor Heltec V4 provisioning codes. CRITICAL: model C8 declares Max TX 28 dBm,
#: so the canonical 17 dBm is valid and the radio comes ONLINE. Provisioning as
#: generic homebrew (f0/ff, Max TX 14 dBm) kept the radio OFFLINE ("Radio state
#: mismatch") because 17 dBm exceeded the model cap. Signed with the LOCAL
#: signing.key -> "Validated - Local signature" (honest: it IS a V4).
VENDOR_PRODUCT = "c3"   # ROM.PRODUCT_H32_V4
VENDOR_MODEL = "c8"     # ROM.MODEL_C8 (868/915/923 MHz with PA)
VENDOR_HWREV = 1


def erase_command(port: str) -> str:
    """esptool full chip erase (clean slate; unbrickable — ROM bootloader)."""
    return f"{ESPTOOL} --chip {ESP_CHIP} --port {port} --before default_reset erase_flash"


def full_flash_command(port: str, build_dir: str = BUILD_DIR,
                       boot_app0: str = BOOT_APP0) -> str:
    """Flash the COMPLETE RGB image with --flash_size detect (patches the
    bootloader header to the board's real flash size — `keep` boot-loops the V4)."""
    return (f"{ESPTOOL} --chip {ESP_CHIP} --port {port} "
            f"--before default_reset --after hard_reset write_flash -z "
            f"--flash_size detect "
            f"0x{OFF_BOOTLOADER:x} {build_dir}/RNode_Firmware.ino.bootloader.bin "
            f"0x{OFF_PARTITIONS:x} {build_dir}/RNode_Firmware.ino.partitions.bin "
            f"0x{OFF_BOOT_APP0:x} {boot_app0} "
            f"0x{OFF_APP:x} {build_dir}/RNode_Firmware.ino.bin")


def vendor_provision_command(port: str) -> str:
    """rnodeconf ROM bootstrap as the vendor Heltec V4 (c3/c8) — non-interactive,
    no reflash, signs hardware-info with the local key."""
    return (f"rnodeconf {port} -r --product {VENDOR_PRODUCT} "
            f"--model {VENDOR_MODEL} --hwrev {VENDOR_HWREV}")


def embedded_hash_command(port: str, bin_path: str = BUILD_BIN) -> str:
    """Set the firmware hash to the app image's embedded trailing 32 bytes (the
    ESP32 image's own SHA256, == sha256(data[:-32])) so it validates, not 'corrupt'."""
    return (f"HASH=$(python3 -c \"import sys,os;"
            f"d=open(os.path.expanduser('{bin_path}'),'rb').read();"
            f"sys.stdout.write(d[-32:].hex())\") "
            f"&& test -n \"$HASH\" && rnodeconf {port} -H \"$HASH\"")


def rgb_firmware_available(bin_path: str = RGB_LOCAL_BIN,
                           hasher_path: str = RGB_LOCAL_HASHER) -> bool:
    """True when the compiled RGB firmware + hasher exist on the TOOL HOST,
    ready to carry to a target. Only the medic (which ran build()) has them, so
    this is how the Pi build decides RGB-overlay vs. plain stock."""
    return os.path.isfile(bin_path) and os.path.isfile(hasher_path)


#: The arduino-cli ESP32 core the build needs (carry.py audits the same dir).
ARDUINO_ESP32_CORE_DIR = "~/.arduino15/packages/esp32"


#: Where the medic's own arduino-cli lives when it is not on the app's PATH
#: (checked live 2026-10-04: ~/.local/bin/arduino-cli).
ARDUINO_CLI_PATHS = ("~/.local/bin/arduino-cli", "~/bin/arduino-cli")


def rgb_build_possible(firmware_dir: str = FIRMWARE_DIR,
                       core_dir: str = ARDUINO_ESP32_CORE_DIR,
                       which=None, online=None,
                       cli_paths=ARDUINO_CLI_PATHS) -> bool:
    """True when this host can COMPILE the RGB firmware: arduino-cli, its ESP32
    core and the firmware source are all aboard — or *online* says the medic
    has internet, in which case HeltecV4RGBWorkflow fetches what it lacks
    itself (_ensure_toolchain / _ensure_source). The factory asks this when the
    firmware isn't built yet, instead of quietly sending a boxed V4 to the
    stock flash (readiness ledger #44)."""
    import shutil
    which = which or shutil.which
    cli = bool(which("arduino-cli")) or any(
        os.path.isfile(os.path.expanduser(p)) for p in cli_paths)
    aboard = (cli and os.path.isdir(os.path.expanduser(core_dir))
              and os.path.isdir(os.path.expanduser(firmware_dir)))
    if aboard:
        return True
    try:
        return bool(online()) if online is not None else False
    except Exception:
        return False


def rgb_firmware_staleness(bin_path: str = RGB_LOCAL_BIN,
                           firmware_dir: str = FIRMWARE_DIR,
                           run=None) -> Optional[str]:
    """Why the compiled image may NOT be what the source tree says — or None.

    The image birth flashes lives in ONE build directory; a `make` in the
    same tree writes to arduino-cli's default and changes nothing birth
    reads. On 2026-10-03 the NODE MEDIC strip was committed, compiled twice
    that way, and a V4 was born with the old face: the binary was three
    weeks older than the tree and nothing said so. This compares the tree's
    last commit time (and its dirty files) with the binary's mtime.
    """
    import subprocess
    bp = os.path.expanduser(bin_path); fd = os.path.expanduser(firmware_dir)
    if not os.path.isfile(bp):
        return "no compiled image"
    def _run(cmd):
        if run is not None:
            return run(cmd)
        return subprocess.run(["bash", "-lc", cmd], capture_output=True,
                              text=True, timeout=20).stdout
    try:
        commit_ts = int((_run(f"git -C {fd} log -1 --format=%ct") or "0").strip() or 0)
        dirty = [l for l in (_run(f"git -C {fd} status --short") or "").splitlines()
                 if l.strip() and not l.strip().startswith("?? build")]
    except Exception:                                                  # noqa: BLE001
        return None                                  # cannot tell; do not alarm
    bin_ts = os.path.getmtime(bp)
    if commit_ts and commit_ts > bin_ts:
        return ("source committed %s after the image was compiled"
                % _ago(commit_ts - bin_ts))
    if dirty:
        return "uncommitted source changes: " + ", ".join(d.split()[-1] for d in dirty[:3])
    return None


def _ago(seconds: float) -> str:
    seconds = int(seconds)
    if seconds < 3600: return "%d min" % (seconds // 60)
    if seconds < 86400: return "%d h" % (seconds // 3600)
    return "%d d" % (seconds // 86400)


def _board_usb_serial(connection: Connection, port: str):
    """The ESP32 USB serial (a MAC, e.g. A1:B2:C3:D4:E5:F6) for the board on
    *port*, so RobustFlasher can target its USB hub port for a uhubctl power-
    cycle. None if it can't be resolved (RobustFlasher then uses a soft reset)."""
    out = connection.run(
        f'for l in /dev/serial/by-id/*; do t=$(readlink -f "$l" 2>/dev/null); '
        f'[ "$t" = "{port}" ] && basename "$l"; done 2>/dev/null')[1]
    m = re.search(r"([0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5})", out or "")
    return m.group(1) if m else None


def _robust_rgb_overlay(connection: Connection, port: str, remote_bin: str,
                        sleep=None) -> "tuple[bool, str]":
    """Write the RGB app at 0x10000 through RobustFlasher — per-chunk
    verify_flash + a baud-dropping ladder + (when uhubctl is available) a USB
    power-cycle to recover a WEDGED board autonomously. This replaces the single
    raw esptool write that was the fragile step. Returns (ok, tier_or_reason)."""
    from workflows.robust_flash import RobustFlasher, Region, find_hub_port
    serial = _board_usb_serial(connection, port)
    hub, hub_port = find_hub_port(connection, serial) if serial else (None, None)
    kw = {"sleep": sleep} if sleep is not None else {}
    flasher = RobustFlasher(connection, port, hub=hub, hub_port=hub_port,
                            chip="esp32s3", **kw)
    result = flasher.flash(fixed=[], app=Region(0x10000, remote_bin))
    return result.success, (result.tier or result.diagnosis or "")


def flash_rgb_carried(connection: Connection, port: str, band_mhz: int = 915,
                      version: str = FIRMWARE_VERSION,
                      bin_path: str = RGB_LOCAL_BIN,
                      hasher_path: str = RGB_LOCAL_HASHER,
                      robust_sleep=None):
    """Birth a blank V4 on a REMOTE target, giving it the NeoPixel status LED —
    but NEVER leaving it worse than a working radio.

    The status LED is an ENHANCEMENT, not a requirement, so a failed overlay must
    not scare the operator with a bricked-looking board. Sequence:

    1. stock-provision the EEPROM + firmware (rnodeconf autoinstall — robust);
    2. try the NeoPixel overlay (retried at a safe baud);
    3. if the overlay still fails, RE-FLASH stock so the board is a clean, fully
       working RNode again — reported as success, just without the LED.

    Returns ``(ok, message, rgb_applied)``. ``ok`` means "a working RNode is on
    the board"; ``rgb_applied`` says whether the status LED made it on.
    """
    board = get_board(V4_BOARD_KEY)
    prov_ok, prov_msg, _already = birth_flash(connection, board, port,
                                              band_mhz, version)
    if not prov_ok:
        return False, f"stock provision failed: {prov_msg}", False

    if not connection.push_file(bin_path, REMOTE_RGB_BIN) \
            or not connection.push_file(hasher_path, REMOTE_RGB_HASHER):
        # Couldn't even stage the LED firmware — the stock radio is fine, ship it.
        return True, ("flashed as a working RNode (couldn't stage the status-LED "
                      "firmware; radio is fully functional)."), False

    # Robust overlay: verify_flash per chunk + a baud-dropping / power-cycle
    # ladder, instead of a single raw esptool write (the old fragile step).
    ok, tier = _robust_rgb_overlay(connection, port, REMOTE_RGB_BIN,
                                   sleep=robust_sleep)
    last = tier
    if ok:
        code, out, err = connection.run(
            firmware_hash_command(port, REMOTE_RGB_BIN, REMOTE_RGB_HASHER),
            timeout=400)
        if code == 0:
            return True, f"flashed with the NeoPixel status LED ({tier}).", True
        last = (err or out)[-160:]

    # Overlay failed — try to restore a clean working radio so the board is
    # never left with a corrupt app. CAREFUL: the EEPROM was provisioned in
    # step 1, and rnodeconf --autoinstall REFUSES an already-provisioned board
    # (returns already=True having written NOTHING) — reporting that as
    # "fully functional" was a false green over a possibly half-written app
    # partition (2026-08-01 bug hunt). Only a restore that actually WROTE
    # counts as a recovery.
    restore_ok, restore_msg, restore_already = birth_flash(
        connection, board, port, band_mhz, version)
    if restore_ok and not restore_already:
        return True, ("flashed as a working RNode — the status-LED firmware "
                      "couldn't be applied this time, but the radio is fully "
                      "functional."), False
    if restore_ok and restore_already:
        return False, ("the status-LED overlay failed and the board could NOT "
                       "be restored (it is already provisioned, so autoinstall "
                       "refused to rewrite it) — its firmware may be "
                       f"incomplete ({last}). Wipe the EEPROM and reflash: "
                       "hold BOOT, tap RST, release BOOT, then retry."), False
    return False, ("status-LED overlay failed and stock restore also failed "
                   f"({last}); the board needs a manual bootloader flash "
                   "(hold BOOT, tap RST, release BOOT, then retry)."), False


def esptool_path(version: str = FIRMWARE_VERSION) -> str:
    """rnodeconf caches esptool alongside the firmware it downloads; the
    --autoinstall step (run first) guarantees it is present at this path."""
    return f"~/.config/rnodeconf/update/{version}/esptool.py"


def build_dir_for(board_model: int = BOARD_MODEL,
                  firmware_dir: str = FIRMWARE_DIR) -> str:
    """Where THIS board model's artifacts live. See BUILD_SUBDIR for why they
    cannot be allowed to share arduino-cli's default."""
    return f"{firmware_dir}/build/rnm-board-0x{board_model:02X}"


def bin_for(board_model: int = BOARD_MODEL,
            firmware_dir: str = FIRMWARE_DIR) -> str:
    return f"{build_dir_for(board_model, firmware_dir)}/RNode_Firmware.ino.bin"


def compile_command(firmware_dir: str = FIRMWARE_DIR,
                    board_model: int = BOARD_MODEL) -> str:
    """arduino-cli compile line, transcribed verbatim from setup_rnode_tools.sh
    (no_ota partitions, 2 MB app, -DBOARD_MODEL), with one addition:
    --build-path, so this board model's binaries cannot be overwritten by a
    build for a different board in the same tree (see BUILD_SUBDIR)."""
    # ABSOLUTE paths: a quoted "~/..." does not expand under bash, and the
    # compile then lands in a literal ./~/ directory inside the tree while
    # birth keeps flashing the old image (2026-10-03 — the V4 born with the
    # stock face after the NODE MEDIC strip was committed).
    fd = os.path.expanduser(firmware_dir)
    return (
        f"cd {fd} && arduino-cli compile --fqbn {FQBN} -e "
        f'--build-path "{os.path.expanduser(build_dir_for(board_model, firmware_dir))}" '
        f'--build-property "build.partitions=no_ota" '
        f'--build-property "upload.maximum_size=2097152" '
        f'--build-property "compiler.cpp.extra_flags=-DBOARD_MODEL=0x{board_model:02X}"')


def esptool_flash_command(port: str, bin_path: str = BUILD_BIN,
                          version: str = FIRMWARE_VERSION,
                          esptool: Optional[str] = None,
                          baud: int = DEFAULT_FLASH_BAUD) -> str:
    """esptool write_flash line that overlays the NeoPixel firmware onto the app
    partition at 0x10000. *baud* defaults to the safer DEFAULT_FLASH_BAUD (was a
    hard-coded 921600, the usual culprit for mid-write serial corruption)."""
    tool = esptool or esptool_path(version)
    return (
        f"python3 {tool} --port {port} --chip esp32s3 --baud {baud} "
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


def _reported_board_model(info_out: str):
    """The firmware's OWN compiled board model, from rnodeconf --info.

    The product line ends in a parenthesised triple — product:model:board —
    e.g. ``(c3:c8:3f)``. The first two come from the EEPROM provisioning (what
    we TOLD the board it is); the third is what the running firmware was
    compiled as, which is the one that cannot be faked by provisioning.

    Returns None when the line is absent or unparseable: a check that cannot
    read the evidence must not fail a build on a guess.
    """
    m = re.search(r"\(([0-9a-fA-F]{2}):([0-9a-fA-F]{2}):([0-9a-fA-F]{2})\)",
                  info_out or "")
    return int(m.group(3), 16) if m else None


class HeltecV4RGBWorkflow:
    """Build the NeoPixel firmware (once) and flash a Heltec V4 with it."""

    def __init__(self, connection: Connection, port: Optional[str] = None,
                 band_mhz: int = 915, version: str = FIRMWARE_VERSION,
                 firmware_dir: str = FIRMWARE_DIR,
                 neopixel_pin: int = NEOPIXEL_PIN, board_model: int = BOARD_MODEL,
                 boot_error_red: int = BOOT_ERROR_RED,
                 build_timeout: int = 600, flash_timeout: int = 400,
                 flash_sleep=None, radio: Optional[RadioConfig] = None,
                 radio_mode: str = "host"):
        self.connection = connection
        self.port = port
        self.band_mhz = band_mhz
        # Radio params baked into the EEPROM at birth. Defaults to the SAVED
        # tool-wide defaults (Settings ▸ Default radio parameters) so regional
        # settings reach every flash; the BIRTH screen overrides from the form.
        if radio is None:
            try:
                from provisioning.radio_defaults import load_radio_config
                radio = load_radio_config()
            except Exception:
                radio = RadioConfig()
        self.radio = radio
        # 'tnc' = standalone active radio (pocket RNode / LED signals on boot);
        # 'host' = host-controlled, a Pi running rnsd drives the radio.
        self.radio_mode = radio_mode
        self.version = version
        self.firmware_dir = firmware_dir
        self.neopixel_pin = neopixel_pin
        self.board_model = board_model
        # Per BOARD MODEL, not per FQBN — see BUILD_SUBDIR for the T-Deck
        # image that was flashed onto Heltec V4s for a fortnight.
        self.build_dir = build_dir_for(board_model, firmware_dir)
        self.boot_error_red = boot_error_red
        self.build_timeout = build_timeout
        self.flash_timeout = flash_timeout
        self.flash_sleep = flash_sleep          # injected into RobustFlasher (tests)
        self.results: List[StepResult] = []

    @property
    def bin_path(self) -> str:
        return bin_for(self.board_model, self.firmware_dir)

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
        # Apply the two firmware patches. Each file is reset to pristine first so
        # the scoped patch always applies against a known anchor (guards against
        # a prior bad patch): Boards.h enables the NeoPixel on the V4 block, and
        # Utilities.h recolours the boot-error LED from stuck-white to dim red.
        patches = (
            ("Boards.h", LOCAL_PATCH, REMOTE_PATCH,
             f"--pin {self.neopixel_pin}"),
            ("Utilities.h", LOCAL_BOOT_ERR, REMOTE_BOOT_ERR,
             f"--red 0x{self.boot_error_red:02X}"),
            ("RNode_Firmware.ino", LOCAL_BIRTH_CRY, REMOTE_BIRTH_CRY, ""),
        )
        for fname, local, remote, extra in patches:
            if not self.connection.push_file(local, remote):
                return StepResult(
                    "ensure_source", False,
                    f"Could not carry {os.path.basename(local)} to the node.")
            self.connection.run(
                f"git -C {self.firmware_dir} checkout -- {fname}")
            code, out, err = self.connection.run(
                f"python3 {remote} {self.firmware_dir}/{fname} {extra}")
            if code != 0:
                return StepResult("ensure_source", False,
                                  f"{fname} patch failed: {(err or out)[-200:]}")
        return StepResult(
            "ensure_source", True,
            "Firmware cloned + NeoPixel (GPIO47), dim-red boot-error and "
            "birth-cry patches applied.")

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
        # Fingerprint the board the moment we first see it — same as
        # RNodeFlashWorkflow, and for the same two readers: the tty number
        # does not survive a reset, and the guided hand-back carries this
        # serial to the Pi build so /dev/rnode can be pinned to THIS radio.
        # This path never set it, so node 'soon' shipped with the five-vendor
        # net (2026-08-12).
        from workflows.rnode_flash import by_id_serial, usb_id_for_port
        self._usb_serial = by_id_serial(usb_id_for_port(self.connection, port))
        # Engraved-hole translation, real local connections only (an emulated
        # run's pinned port isn't in any hole).
        from ui.usb_ports import connection_is_local, describe_port
        return StepResult(
            "detect_port", True,
            f"Board on {describe_port(port, local=connection_is_local(self.connection))}.")

    def _guard(self) -> "str | None":
        """HARD GATE for this workflow's write boundaries — a FULL CHIP erase
        of the medic's own radio is the catastrophic foot-gun (hole found by
        the 2026-08-01 bug hunt: this whole workflow had no gate). Returns an
        error string when the port must not be written, else None."""
        try:
            from ui.onboard_roster import assert_flashable, guard_is_active
            if guard_is_active():
                assert_flashable(self.port)
        except Exception as e:        # noqa: BLE001
            return f"Refusing to write to {self.port}: {e}"
        return None

    def _erase(self) -> StepResult:
        # Clean slate before the full-image write (clears any partial/boot-looping
        # firmware + stale EEPROM). Unbrickable — esptool always reconnects.
        blocked = self._guard()
        if blocked:
            return StepResult("erase", False, blocked)
        if self.connection.run(f"test -f {self.bin_path}")[0] != 0:
            return StepResult("erase", False,
                              "NeoPixel firmware not built yet — run build() first.")
        code, out, err = self.connection.run(erase_command(self.port),
                                             timeout=self.flash_timeout)
        ok = code == 0 or "erase completed" in (out or "").lower()
        return StepResult("erase", ok,
                          "Flash erased — clean slate." if ok
                          else f"Erase failed: {(err or out)[-160:]}")

    def _flash_firmware(self) -> StepResult:
        blocked = self._guard()
        if blocked:
            return StepResult("flash_firmware", False, blocked)
        # Flash the COMPLETE RGB image (bootloader+partitions+boot_app0+app) with
        # --flash_size detect. `detect` patches the bootloader header to the real
        # flash size; `keep` boot-loops the V4 (~2.5s USB re-enum). This is a
        # single write, NOT an overlay onto vendor firmware (the old broken path).
        code, out, err = self.connection.run(
            full_flash_command(self.port, self.build_dir), timeout=self.flash_timeout)
        ok = code == 0 or "hash of data verified" in (out or "").lower()
        return StepResult("flash_firmware", ok,
                          "Flashed the complete NeoPixel firmware image." if ok
                          else f"Flash failed: {(err or out)[-200:]}")

    def _provision(self) -> StepResult:
        blocked = self._guard()
        if blocked:
            return StepResult("provision", False, blocked)
        # ROM-bootstrap the EEPROM as the VENDOR Heltec V4 (c3/c8). Model C8
        # declares Max TX 28 dBm, so the canonical 17 dBm is valid and the radio
        # comes ONLINE — generic homebrew (f0/ff, 14 dBm cap) kept it OFFLINE
        # ("Radio state mismatch"). Non-interactive; signed with the local key.
        code, out, err = self.connection.run(vendor_provision_command(self.port),
                                             timeout=self.flash_timeout)
        low = (out or "").lower()
        ok = "bootstrapping successful" in low or "signature validated" in low
        return StepResult(
            "provision", ok,
            "Provisioned as Heltec V4 (vendor model, local signature)." if ok
            else f"Provision failed: {(err or out)[-200:]}")

    def _set_hash(self) -> StepResult:
        blocked = self._guard()
        if blocked:
            return StepResult("set_hash", False, blocked)
        # Firmware hash = the app image's embedded trailing 32 bytes, so the
        # firmware's own integrity check passes (not "firmware corrupt").
        code, out, err = self.connection.run(
            embedded_hash_command(self.port, self.bin_path),
            timeout=self.flash_timeout)
        ok = code == 0 and "firmware hash set" in (out or "").lower()
        return StepResult(
            "set_hash", ok,
            "Firmware hash set — validates, not corrupt." if ok
            else f"Could not set firmware hash: {(err or out)[-200:]}")

    def _set_params(self) -> StepResult:
        # Bake the radio params. mode='tnc' saves them so the radio boots active
        # standalone (pocket RNode / LED signals); mode='host' leaves it
        # host-controlled for a Pi running rnsd to drive.
        ok, detail = set_params_at_birth(self.connection, self.port,
                                         cfg=self.radio, mode=self.radio_mode,
                                         timeout=self.flash_timeout)
        return StepResult("set_params", ok, detail)

    def _verify(self) -> StepResult:
        out = self.connection.run(f"rnodeconf {self.port} --info")[1] or ""
        low = out.lower()
        ok = ("eeprom is invalid" not in low and "corrupt" not in low
              and "firmware version" in low)
        # AND THAT IT IS THE BOARD WE BUILT FOR. Until 2026-09-11 this step
        # asked only whether SOMETHING valid answered, so it passed a Heltec
        # V4 running T-Deck firmware with "Board verified" — for a fortnight,
        # on every V4 birthed. rnodeconf prints the firmware's own compiled
        # board model as the third byte of the product triple, e.g.
        #   Product : Heltec LoRa32 v4 850 - 950 MHz (c3:c8:3f)
        # 0x3F is BOARD_HELTEC32_V4; the mis-flashed boards said 0x3B, which
        # is BOARD_TDECK. The radio was perfect either way — both are SX1262 —
        # so nothing else in the flow could notice.
        got = _reported_board_model(out)
        if ok and got is not None and got != self.board_model:
            return StepResult(
                "verify", False,
                f"The board answered as model 0x{got:02X}, but this build is "
                f"for 0x{self.board_model:02X}. That firmware is for a "
                "different board — it may run, and its screen and pins will "
                "be wrong. Rebuild before flashing again.")
        if ok:
            # Identity-less RNode -> the birth record carries the board's USB
            # fingerprint so the medic can recognise it as kin later.
            from workflows.rnode_flash import usb_id_for_port
            r = self.radio
            self.birth_certificate = {
                "node_type": "rnode",
                "board": "Heltec LoRa32 v4 (RGB NeoPixel)",
                "serial_port": self.port,
                "usb_serial": usb_id_for_port(self.connection, self.port),
                "radio": (f"{r.frequency_mhz:g} MHz / BW{r.bandwidth_khz:g} / "
                          f"SF{r.spreading_factor} / CR{r.coding_rate} / "
                          f"{r.tx_power_dbm} dBm") if r else "tool defaults",
            }
        return StepResult(
            "verify", ok,
            "Board verified: valid RNode + NeoPixel firmware." if ok
            else "Board did not report a valid RNode after flashing.")

    # -- drivers -----------------------------------------------------------

    def _birth_cry(self) -> StepResult:
        # The finale: command the newborn to SING (KISS FEND 0xB5 0xF8 FEND —
        # the trigger patched into the firmware) AFTER verify, so the ~13 s
        # light show coincides with the green flashed-successfully
        # confirmation instead of firing mid-provision (operator spec
        # 2026-08-01; rnodeconf's hard-resets made a boot-time cry land
        # mid-flow). Cosmetic: a failed trigger never fails a VERIFIED birth.
        cmd = ("python3 -c \"import serial; "
               f"s=serial.Serial('{self.port}',115200,timeout=2); "
               "s.write(bytes([0xC0,0xB5,0xF8,0xC0])); s.flush(); s.close()\"")
        code, out, err = self.connection.run(cmd, timeout=30)
        if code == 0:
            # The cry opens with a ~9 s near-invisible ember dawn — hold the
            # step through it so the green confirmation lands right at the
            # IGNITION (the bright part), not after the whole song
            # (operator timing note 2026-08-01).
            self.connection.run("sleep 9", timeout=20)
            msg = "Birth cry commanded — watch the node's light show."
        else:
            msg = ("Couldn't trigger the birth cry (cosmetic — the node "
                   f"itself verified OK): {(err or out)[-120:]}")
        return StepResult("birth_cry", True, msg)

    _BUILD = ("_ensure_toolchain", "_ensure_source", "_build_firmware")
    _FLASH = ("_detect_port", "_erase", "_flash_firmware", "_provision",
              "_set_hash", "_set_params", "_verify", "_birth_cry")

    def planned_step_names(self):
        """Ordered StepResult names run_all will produce (the compile steps are
        skipped when the firmware is already built) — lets the UI pre-list the
        whole checklist before the build starts."""
        flash = [n.lstrip("_") for n in self._FLASH]
        try:
            if self.connection.run(f"test -f {self.bin_path}")[0] == 0:
                return flash
        except Exception:
            pass
        return [n.lstrip("_") for n in self._BUILD] + flash

    def _run_steps(self, step_names, on_progress):
        emit = on_progress or (lambda r: None)
        for name in step_names:
            result = getattr(self, name)()
            self.results.append(result)
            from workflows.step_log import log_step
            log_step(result)
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
        # Skip the (multi-minute) compile when the NeoPixel firmware is already
        # built on this host — a medic that has run build() once just flashes.
        if self.connection.run(f"test -f {self.bin_path}")[0] == 0:
            return self.flash(on_progress)
        self.build(on_progress)
        if self.results and not self.results[-1].success:
            return self.results
        return self.flash(on_progress)
