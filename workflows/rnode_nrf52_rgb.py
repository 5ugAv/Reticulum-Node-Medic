"""Build the RNode firmware with the NeoPixel status LED enabled, for nRF52.

The Heltec V4 path (``workflows/rnode_v4_rgb.py``) is ESP32-shaped all the way
down: arduino-cli with the esp32 core, then esptool writing four images at fixed
offsets. None of that applies to an nRF52840. A RAK4631 is compiled against the
RAKwireless core, the output is a single ``.hex``, and it reaches the board as a
DFU package over serial — the bootloader, not a chip-level flasher.

So this is a sibling module rather than a flag on the V4 one.

WHY THE RAK NEEDS THIS AT ALL. The board has no screen. RX/TX go to the
WisBlock base's green and blue LEDs (the firmware's own ``pin_led_rx`` /
``pin_led_tx``), and those two can say "receiving" and "transmitting" — but they
cannot say interference, fault, or idle, because saying those apart needs
colour. A single NeoPixel on a free pin carries the whole vocabulary the V4 and
the Tracker already speak.

EVERY COMMAND HERE IS TRANSCRIBED FROM THE FIRMWARE'S OWN Makefile
(``release-rak4631`` and ``prep-nrf``), not derived. That matters: the build
properties are not decoration. ``build.partitions=no_ota`` and
``upload.maximum_size=2097152`` are what let the image use the whole 2 MB
without an OTA partition, and ``-DBOARD_MODEL=0x51`` is what makes the firmware
compile the RAK's own block in Boards.h — the block this build then patches.

WHAT IS NOT PROVEN. Nothing here has been run against a RAK4631 yet, and no
NeoPixel has been soldered to one. The flashing half IS proven (a RAK4631 was
birthed as an RNode on 2026-08-05, DFU touch and all); it is the compile and the
package that are new. Treat a first run as a bench task, not a field one.
"""

from __future__ import annotations

import os

#: Upstream firmware, same clone the V4 path builds from.
FIRMWARE_DIR = "~/RNode_Firmware"

#: The idempotent Boards.h patcher, carried onto the builder before compiling.
#: Shared with the V4 path — it is board-aware and refuses a pin it has not
#: verified for the board it is given.
LOCAL_PATCH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "assets", "scripts", "apply_neopixel_patch.py")
REMOTE_PATCH = "/tmp/apply_neopixel_patch.py"


class NRF52Target:
    """One nRF52 board's build recipe, as the firmware's Makefile states it."""

    def __init__(self, key, board_macro, fqbn, build_subdir, dev_type,
                 release_name, big_image=True):
        self.key = key                      #: our board key (rnode_boards)
        self.board_macro = board_macro      #: Boards.h macro, e.g. BOARD_RAK4631
        self.fqbn = fqbn
        self.build_subdir = build_subdir
        self.dev_type = dev_type            #: adafruit-nrfutil --dev-type
        self.release_name = release_name
        self.big_image = big_image          #: the no_ota / 2 MB build properties

    @property
    def hex_path(self) -> str:
        return f"{FIRMWARE_DIR}/build/{self.build_subdir}/RNode_Firmware.ino.hex"

    @property
    def named_hex(self) -> str:
        return f"{FIRMWARE_DIR}/build/{self.release_name}.hex"

    @property
    def package(self) -> str:
        return f"{FIRMWARE_DIR}/Release/{self.release_name}.zip"


#: Only boards whose NeoPixel pin has actually been researched belong here.
#: The T-Echo and Heltec T114 are nRF52 too and build the same way, but no pin
#: has been verified free on either, so they are deliberately absent — an
#: unresearched board must fail to find a recipe rather than inherit the RAK's
#: pin. That is the same rule the patcher enforces one layer down.
TARGETS = {
    "rak4631": NRF52Target(
        key="rak4631",
        board_macro="BOARD_RAK4631",
        fqbn="rakwireless:nrf52:WisCoreRAK4631Board",
        build_subdir="rakwireless.nrf52.WisCoreRAK4631Board",
        dev_type="0x0052",
        release_name="rnode_firmware_rak4631",
    ),
}


class UnsupportedTarget(ValueError):
    """No verified RGB recipe for this board."""


def target_for(board_key: str) -> NRF52Target:
    """The recipe for *board_key*, or raise with what is actually missing."""
    t = TARGETS.get((board_key or "").strip().lower())
    if t is None:
        raise UnsupportedTarget(
            f"No verified NeoPixel build for '{board_key}'. Research the "
            f"board's free pins, add it to apply_neopixel_patch.VERIFIED_NP_PIN "
            f"and to TARGETS here — in that order.")
    return t


# --- the toolchain -------------------------------------------------------

#: `prep-nrf` in the firmware Makefile, reduced to the RAK. Installing all three
#: nRF52 cores (rakwireless / Heltec / adafruit) is what upstream does to build
#: every board; the medic only needs the one it is about to compile for.
def prep_commands(fqbn_core: str = "rakwireless:nrf52",
                  firmware_dir: str = FIRMWARE_DIR) -> list:
    """arduino-cli / nrfutil setup, in order. Run once per medic."""
    return [
        f"cd {firmware_dir} && arduino-cli core update-index "
        f"--config-file arduino-cli.yaml",
        f"cd {firmware_dir} && arduino-cli core install {fqbn_core} "
        f"--config-file arduino-cli.yaml",
        # The NeoPixel library is what HAS_NP compiles against. Upstream installs
        # it in `prep` for every platform, so an ESP32-only medic already has it.
        'arduino-cli lib install "Adafruit NeoPixel"',
        # rnodeconf already shells out to this for every nRF52 flash; the DFU
        # package below is built with the same binary.
        "pip install --upgrade adafruit-nrfutil",
    ]


def patch_command(target: NRF52Target, firmware_dir: str = FIRMWARE_DIR,
                  patcher: str = REMOTE_PATCH) -> str:
    """Enable HAS_NP for this board in Boards.h.

    Deliberately passes NO ``--pin``. The patcher defaults to the pin it has
    verified for the given board, so the pin has exactly one home and this
    module cannot drift from the guard that refuses a claimed one."""
    return (f"python3 {patcher} {firmware_dir}/Boards.h "
            f"--board {target.board_macro}")


def compile_command(target: NRF52Target, firmware_dir: str = FIRMWARE_DIR) -> str:
    """`release-rak4631`, verbatim from the firmware Makefile."""
    # The macro NAME and its numeric value are not derivable from each other —
    # BOARD_RAK4631 is 0x51 because Boards.h says so. Look it up, never munge it.
    props = [f'--build-property "compiler.cpp.extra_flags='
             f'\\"-DBOARD_MODEL={_model_id(target)}\\""']
    if target.big_image:
        props = ['--build-property "build.partitions=no_ota"',
                 '--build-property "upload.maximum_size=2097152"'] + props
    return (f"cd {firmware_dir} && arduino-cli compile --fqbn {target.fqbn} -e "
            + " ".join(props))


#: BOARD_MODEL values, from Boards.h. Kept beside the targets rather than parsed,
#: because a wrong one here compiles a DIFFERENT board's pin block into the image
#: and the result flashes cleanly and behaves like a hardware fault.
MODEL_IDS = {"BOARD_RAK4631": "0x51"}


def _model_id(target: NRF52Target) -> str:
    try:
        return MODEL_IDS[target.board_macro]
    except KeyError:                                  # pragma: no cover
        raise UnsupportedTarget(
            f"No BOARD_MODEL id recorded for {target.board_macro}")


def package_commands(target: NRF52Target, firmware_dir: str = FIRMWARE_DIR) -> list:
    """Name the .hex and wrap it as a DFU package the bootloader will accept."""
    return [
        f"cd {firmware_dir} && cp {target.hex_path} {target.named_hex}",
        f"cd {firmware_dir} && mkdir -p {firmware_dir}/Release && "
        f"adafruit-nrfutil dfu genpkg --dev-type {target.dev_type} "
        f"--application {target.named_hex} {target.package}",
    ]


def build_commands(board_key: str, firmware_dir: str = FIRMWARE_DIR,
                   patcher: str = REMOTE_PATCH) -> list:
    """Every command to go from a clean clone to a flashable DFU package."""
    target = target_for(board_key)
    return ([patch_command(target, firmware_dir, patcher),
             compile_command(target, firmware_dir)]
            + package_commands(target, firmware_dir))


def dfu_flash_command(target: NRF52Target, port: str,
                      firmware_dir: str = FIRMWARE_DIR) -> str:
    """Write the package to a board ALREADY sitting in its bootloader.

    Getting it there is not this module's job: ``workflows.rnode_flash`` has the
    proven 1200-baud touch, and the port CHANGES when the board re-enumerates
    into DFU (239a:8029 -> 239a:002a). Pass the post-touch port."""
    return (f"adafruit-nrfutil --verbose dfu serial "
            f"--package {target.package.replace('~', os.path.expanduser('~'))} "
            f"-p {port} -b 115200 --singlebank --touch 1200")


def rgb_package_available(board_key: str, firmware_dir: str = FIRMWARE_DIR) -> bool:
    """Has this board's RGB package already been built on this machine?"""
    try:
        target = target_for(board_key)
    except UnsupportedTarget:
        return False
    return os.path.isfile(os.path.expanduser(
        target.package.replace(FIRMWARE_DIR, firmware_dir)))
