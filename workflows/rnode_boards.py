"""Registry of boards the tool can flash as an RNode.

Each entry holds everything needed to flash and provision a board as a stock
RNode, plus the plain-English button instructions (bootloader entry + abort
recovery) in the same format as the rest of the tool. Recovery text is reused
from ``ui.safety`` so there is one source of truth.

The ``compile_command`` / ``upload_command`` / ``provision_commands`` generate
the exact arduino-cli / rnodeconf invocations the carried standalone flasher
uses (assets/scripts/), so the tool can drive the flash programmatically later.

Adding a board: research its bootloader buttons, its FQBN + build flags, and its
RNode provisioning codes (product/model/platform/hwrev), then add an entry.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from ui.safety import recovery_text

DEFAULT_FIRMWARE_DIR = "~/RNode_Firmware"


@dataclass
class RNodeBoard:
    key: str
    display_name: str
    board_model: int                    # -DBOARD_MODEL byte
    fqbn: str                           # arduino-cli fully-qualified board name
    provision: Dict[str, str]           # rnodeconf: product/model/platform/hwrev
    bootloader_instructions: str        # how to enter download mode (buttons)
    recovery_key: str                   # key into ui.safety.recovery_text
    build_properties: List[str] = field(default_factory=list)
    carried_script: str = ""            # standalone flasher in assets/scripts/
    notes: str = ""

    @property
    def recovery_instructions(self) -> str:
        """Abort-recovery button sequence (shared with the safety panel)."""
        return recovery_text(self.recovery_key)

    def compile_command(self, firmware_dir: str = DEFAULT_FIRMWARE_DIR) -> str:
        props = list(self.build_properties)
        props.append(
            f"compiler.cpp.extra_flags=-DBOARD_MODEL=0x{self.board_model:02X}")
        prop_args = " ".join(f'--build-property "{p}"' for p in props)
        return f"arduino-cli compile --fqbn {self.fqbn} -e {prop_args}"

    def upload_command(self, port: str,
                       firmware_dir: str = DEFAULT_FIRMWARE_DIR) -> str:
        # Same FQBN as compile — a mismatch makes arduino-cli look in the wrong
        # build dir and fail to find the binary.
        return f"arduino-cli upload -p {port} --fqbn {self.fqbn} {firmware_dir}"

    def provision_commands(self, port: str) -> List[str]:
        p = self.provision
        return [
            f"rnodeconf {port} --eeprom-wipe",
            (f"rnodeconf {port} -r --product {p['product']} "
             f"--model {p['model']} --platform {p['platform']} "
             f"--hwrev {p['hwrev']}"),
        ]


RNODE_BOARDS: Dict[str, RNodeBoard] = {
    "heltec_wireless_tracker": RNodeBoard(
        key="heltec_wireless_tracker",
        display_name="Heltec Wireless Tracker",
        # NOTE: 0x52 is what the flasher compiles with. In the shared RNode
        # board-id enum (docs/RTNODE2400_INTEGRATION.md) 0x52 == "XIAO nRF", so
        # if a flashed board later reports id 0x52 the tool would mislabel it.
        # Confirm against the patched RNode_Firmware/Boards.h and reconcile.
        board_model=0x52,
        fqbn="esp32:esp32:esp32s3:CDCOnBoot=cdc",
        build_properties=[
            "build.partitions=no_ota",
            "upload.maximum_size=2097152",
        ],
        provision={"product": "cb", "model": "ca", "platform": "0x80",
                   "hwrev": "1"},
        bootloader_instructions=(
            "Attach the 915 MHz antenna FIRST (running the radio without an "
            "antenna can damage it). The board has two buttons: USER (also "
            "printed PRG) and RST. To enter bootloader/download mode: hold "
            "USER (PRG), press and release RST once, then release USER. This "
            "board uses the ESP32-S3 native USB (no UART chip), so if flashing "
            "fails, unplug it, hold USER, plug it back in, then release USER "
            "and retry."),
        recovery_key="Wireless Tracker",
        carried_script="flash_heltec_wireless_tracker.sh",
        notes=(
            "ESP32-S3 + SX1262 + GPS. Flashing ERASES/re-provisions the EEPROM, "
            "so unplug every other USB board first to avoid flashing the wrong "
            "one."),
    ),
}


def available_boards() -> List[RNodeBoard]:
    """All boards the tool can flash as an RNode, by display name."""
    return sorted(RNODE_BOARDS.values(), key=lambda b: b.display_name)


def get_board(key: str) -> Optional[RNodeBoard]:
    return RNODE_BOARDS.get(key)
