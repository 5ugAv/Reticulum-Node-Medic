"""Registry of boards the tool can flash as an RNode.

Two kinds of board:

* **Official boards** — every device ``rnodeconf --autoinstall`` supports. These
  are flashed from the offline firmware cache (see ``workflows.updater``) by
  driving autoinstall; each entry records the autoinstall device-menu index,
  platform, modem and band coverage (transcribed from rnodeconf's own device
  menu + models table, verified 2026-07-11).
* **Custom boards** — e.g. the self-developed Heltec Wireless Tracker, which is
  NOT in official RNode firmware and is built from patched RNode_Firmware with
  arduino-cli + explicit rnodeconf provisioning.

Recovery button text is reused from ``ui.safety`` so there is one source of
truth. ``compile_command`` / ``upload_command`` / ``provision_commands`` apply
only to arduino-cli (custom) boards; ``autoinstall_command`` applies to official
boards.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from ui.safety import recovery_text

DEFAULT_FIRMWARE_DIR = "~/RNode_Firmware"

#: Generic, TRUE bootloader guidance by platform (per-board notes override).
_ESP32_BOOTLOADER = (
    "This board enters flash mode automatically over USB. If flashing fails, "
    "hold BOOT (labelled PRG or USER on some boards), tap RST/RESET once, then "
    "release BOOT and retry.")
_NRF52_BOOTLOADER = (
    "Double-tap the RESET button to enter the UF2 bootloader — a USB drive "
    "appears, and the tool flashes into it.")


@dataclass
class RNodeBoard:
    key: str
    display_name: str
    flash_method: str = "autoinstall"       # "autoinstall" | "arduino_cli"
    platform: str = ""                       # ESP32 | ESP32-S3 | nRF52 | AVR
    modem: str = ""                          # SX1262 / SX1276 / ...
    bands: str = ""                          # human band-coverage label
    autoinstall_index: int = 0               # rnodeconf device-menu number
    #: band (MHz) -> the board's band-submenu choice in rnodeconf autoinstall.
    #: Only set for boards whose sequence is transcribed + intended for use; an
    #: empty map means "flash sequence not yet verified for this board".
    autoinstall_bands: Dict[int, int] = field(default_factory=dict)
    experimental: bool = True                # upstream marks dev-board installs so
    recovery_key: str = ""                   # key into ui.safety.recovery_text
    bootloader_instructions: str = ""
    notes: str = ""
    # --- arduino-cli (custom) boards only ---
    board_model: int = 0                     # -DBOARD_MODEL byte
    fqbn: str = ""                           # arduino-cli fully-qualified board name
    provision: Dict[str, str] = field(default_factory=dict)
    build_properties: List[str] = field(default_factory=list)
    carried_script: str = ""

    @property
    def recovery_instructions(self) -> str:
        """Abort-recovery button sequence (shared with the safety panel). Uses
        an explicit recovery_key when set, else falls back to the display name
        (ui.safety returns generic guidance for anything unknown)."""
        return recovery_text(self.recovery_key or self.display_name)

    # -- official (autoinstall) boards -------------------------------------

    def autoinstall_command(self, port: str, version: Optional[str] = None,
                            offline: bool = True) -> str:
        """The ``rnodeconf --autoinstall`` command for this board. Offline
        (default) flashes purely from the local firmware cache."""
        from workflows.updater import autoinstall_command
        return autoinstall_command(port, version=version, offline=offline)

    def autoinstall_answers(self, band_mhz: int = 915) -> List[str]:
        """The stdin answers that drive rnodeconf --autoinstall non-interactively
        for this board: device-menu index, <enter> past the board blurb, the
        band choice, then 'y' at the final confirmation. Verified end-to-end on
        a real Heltec V4 (9 -> enter -> 2 -> y). Raises if the board's flash
        sequence hasn't been transcribed for *band_mhz* yet."""
        if self.flash_method != "autoinstall":
            raise ValueError(f"{self.key} is not an autoinstall board.")
        if band_mhz not in self.autoinstall_bands:
            raise ValueError(
                f"Autoinstall band {band_mhz} MHz not yet verified for "
                f"{self.key}.")
        return [str(self.autoinstall_index), "",
                str(self.autoinstall_bands[band_mhz]), "y"]

    # -- custom (arduino-cli) boards ---------------------------------------

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


def _official(key, name, index, platform, modem, bands, recovery_key="",
              notes="", bootloader=None, band_map=None):
    if bootloader is None:
        bootloader = _NRF52_BOOTLOADER if platform == "nRF52" else _ESP32_BOOTLOADER
    return RNodeBoard(
        key=key, display_name=name, flash_method="autoinstall",
        platform=platform, modem=modem, bands=bands, autoinstall_index=index,
        autoinstall_bands=band_map or {}, recovery_key=recovery_key,
        bootloader_instructions=bootloader, notes=notes)


# Official RNode targets — index = rnodeconf's "What kind of device is this?"
# device-menu number (1.3.7). Dev-board installs are flagged experimental
# upstream. Sub-GHz boards cover a 410-525 MHz and an 850-950 MHz variant; the
# band is chosen during flashing (AU builds use 850-950 / 915.125 MHz).
# band_map: band (MHz) -> rnodeconf band-submenu choice, transcribed from the
# firmware's autoinstall menu. Heltec V4 is HARDWARE-VERIFIED (flashed a real
# board offline: 9 -> enter -> 2 -> y). Others are transcribed from source and
# verified per board as hardware becomes available; ambiguous multi-chip boards
# (T-Beam) and un-read menus (Heltec V2, RAK4631, T-Echo) are left blank so
# autoinstall_answers() refuses rather than guess.
_OFFICIAL = [
    _official("lora32_v21", "LilyGO LoRa32 v2.1", 3, "ESP32", "SX1276/78",
              "410-525 / 850-950 MHz",
              band_map={433: 1, 868: 2, 915: 2, 923: 2}),
    _official("lora32_v20", "LilyGO LoRa32 v2.0", 4, "ESP32", "SX1276/78",
              "410-525 / 850-950 MHz",
              band_map={433: 1, 868: 2, 915: 3, 923: 4}),
    _official("lora32_v10", "LilyGO LoRa32 v1.0", 5, "ESP32", "SX1276/78",
              "410-525 / 850-950 MHz",
              band_map={433: 1, 868: 2, 915: 3, 923: 4},
              notes="Known faulty battery-charging circuit — avoid if possible."),
    _official("tbeam", "LilyGO T-Beam", 6, "ESP32", "SX1276/78/62/68",
              "410-525 / 850-950 MHz", recovery_key="LilyGO T-Beam v1.1"),
    _official("heltec32_v2", "Heltec LoRa32 v2", 7, "ESP32", "SX1276/78",
              "410-525 / 850-950 MHz", recovery_key="Heltec V2"),
    _official("heltec32_v3", "Heltec LoRa32 v3", 8, "ESP32", "SX1262/68",
              "410-525 / 850-950 MHz", recovery_key="Heltec V3",
              band_map={433: 1, 868: 2, 915: 3, 923: 4}),
    _official("heltec32_v4", "Heltec LoRa32 v4", 9, "ESP32", "SX1262",
              "850-950 MHz", recovery_key="Heltec V4",
              band_map={868: 1, 915: 2, 923: 3}),          # verified on hardware
    _official("t3s3", "LilyGO LoRa T3S3", 10, "ESP32-S3", "SX1262/68, SX127x, SX1280",
              "410-525 / 850-950 MHz / 2.4 GHz", recovery_key="T3S3"),
    _official("rak4631", "RAK4631", 11, "nRF52", "SX1262",
              "430-510 / 779-928 MHz", recovery_key="RAK4631"),
    _official("techo", "LilyGO T-Echo", 12, "nRF52", "SX1262",
              "430-510 / 779-928 MHz", recovery_key="T-Echo"),
    _official("tbeam_supreme", "LilyGO T-Beam Supreme", 13, "ESP32-S3", "SX1262/68",
              "410-525 / 850-950 MHz", recovery_key="LilyGO T-Beam Supreme",
              band_map={433: 1, 868: 2, 915: 2, 923: 2}),
    _official("tdeck", "LilyGO T-Deck", 14, "ESP32-S3", "SX1262/68",
              "410-525 / 850-950 MHz",
              band_map={433: 1, 868: 2, 915: 2, 923: 2}),
    _official("heltec_t114", "Heltec Mesh Node T114", 15, "nRF52", "SX1262/68",
              "410-525 / 850-950 MHz", recovery_key="T114",
              band_map={433: 1, 868: 2, 915: 3, 923: 4}),
    _official("xiao_esp32s3", "Seeed XIAO ESP32S3 (Wio-SX1262)", 16, "ESP32-S3",
              "SX1262", "410-525 / 850-950 MHz",
              band_map={433: 1, 868: 2, 915: 2, 923: 2}),
]


_CUSTOM = [
    RNodeBoard(
        key="heltec_wireless_tracker",
        display_name="Heltec Wireless Tracker",
        flash_method="arduino_cli",
        platform="ESP32-S3",
        modem="SX1262",
        bands="850-950 MHz",
        experimental=True,
        # CUSTOM board — user-developed, deliberately NOT in official RNode
        # firmware. Flashed from patched RNode_Firmware via arduino-cli.
        # CAVEAT: board_model 0x52 collides with BOARD_XIAO_NRF upstream, so a
        # flashed Tracker identifies as "XIAO nRF" to stock tooling; the tool
        # special-cases this custom id.
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
]


RNODE_BOARDS: Dict[str, RNodeBoard] = {
    b.key: b for b in (_OFFICIAL + _CUSTOM)
}


def available_boards() -> List[RNodeBoard]:
    """All boards the tool can flash as an RNode, by display name."""
    return sorted(RNODE_BOARDS.values(), key=lambda b: b.display_name)


def official_boards() -> List[RNodeBoard]:
    """Boards flashed via rnodeconf --autoinstall (offline cache), by menu order."""
    return sorted((b for b in RNODE_BOARDS.values()
                   if b.flash_method == "autoinstall"),
                  key=lambda b: b.autoinstall_index)


def custom_boards() -> List[RNodeBoard]:
    """Non-official boards flashed from patched firmware via arduino-cli."""
    return sorted((b for b in RNODE_BOARDS.values()
                   if b.flash_method == "arduino_cli"),
                  key=lambda b: b.display_name)


def get_board(key: str) -> Optional[RNodeBoard]:
    return RNODE_BOARDS.get(key)
