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
#: VERIFIED on a RAK4631, 2026-08-05. rnodeconf flashes nRF52 boards with
#: ``adafruit-nrfutil dfu serial ... -t 1200`` — the ``-t 1200`` IS the touch, so
#: the flasher drops the port to 1200 baud and puts the board into its
#: bootloader itself. The operator does not have to press anything.
#:
#: And NO USB drive appears. This bootloader is serial DFU only: in DFU mode the
#: board (239a:002a) exposes just CDC interfaces — class 02 and 0a, both bound to
#: cdc_acm, nothing on usb-storage and no block device. The old wording promised
#: a drive that never shows up, which left the operator waiting for a sign that
#: could not come.
_NRF52_BOOTLOADER = (
    "The tool puts this board into its bootloader by itself over USB — no "
    "button to press. Nothing appears as a USB drive: this bootloader talks "
    "over the serial port only. If a flash does fail, double-tap RESET and "
    "run it again.")


@dataclass
class RNodeBoard:
    key: str
    display_name: str
    #: "autoinstall"  rnodeconf drives the whole flash from the offline cache
    #: "arduino_cli"  we build the image here, then arduino-cli uploads it
    #: "serial_dfu"   we build the image here, then adafruit-nrfutil pushes the
    #:                signed .zip over the nRF52 serial bootloader. Needed for
    #:                boards whose firmware is not in upstream RNode at all, so
    #:                rnodeconf has no menu entry to answer, AND whose upload
    #:                cannot go through arduino-cli because the board must be
    #:                put into DFU first (1200-baud touch) and the port MOVES
    #:                when it gets there.
    flash_method: str = "autoinstall"  # autoinstall | arduino_cli | serial_dfu
    platform: str = ""                       # ESP32 | ESP32-S3 | nRF52 | AVR
    modem: str = ""                          # SX1262 / SX1276 / ...
    bands: str = ""                          # human band-coverage label
    autoinstall_index: int = 0               # rnodeconf device-menu number
    #: band (MHz) -> the board's band-submenu choice in rnodeconf autoinstall.
    #: Only set for boards whose sequence is transcribed + intended for use; an
    #: empty map means "flash sequence not yet verified for this board".
    autoinstall_bands: Dict[int, int] = field(default_factory=dict)
    #: Why the band menu CANNOT be answered from here, when that is the truth:
    #: rnodeconf selects some products by RADIO CHIP (SX1276 vs SX1262 variants
    #: of one board), which nothing on this side of the USB cable can see.
    #: Set -> autoinstall_answers refuses with this reason instead of the
    #: generic "not yet verified" (which reads as a to-do, not a fact).
    band_ambiguity: str = ""
    #: HOW an RNode birth of this board was proven on real hardware through
    #: Node Medic — "" means never (readiness ledger #49: the picker showed 18
    #: boards identically while four had ever been birthed). The source of
    #: truth is docs/BOARD_COVERAGE.md; keep the two in step.
    proven: str = ""

    def cannot_flash_reason(self, band_mhz: int = 915) -> str:
        """Why Node Medic must NOT start flashing this board for *band_mhz* —
        or "" when it can. Asked BEFORE any step touches the board: the
        T-Beam/T3S3 refusal used to surface after three green steps and,
        worse, after a real chip erase (readiness sweep, 2026-10-03)."""
        if self.flash_method != "autoinstall":
            return ""
        if band_mhz in self.autoinstall_bands:
            return ""
        if self.band_ambiguity:
            return (f"Node Medic can't flash a {self.display_name} yet: "
                    f"{self.band_ambiguity}")
        return (f"Node Medic hasn't verified the {band_mhz} MHz flash sequence "
                f"for the {self.display_name} yet, so it won't guess.")
    recovery_key: str = ""                   # key into ui.safety.recovery_text
    bootloader_instructions: str = ""
    notes: str = ""
    # --- arduino-cli (custom) boards only ---
    board_model: int = 0                     # -DBOARD_MODEL byte
    fqbn: str = ""                           # arduino-cli fully-qualified board name
    provision: Dict[str, str] = field(default_factory=dict)
    build_properties: List[str] = field(default_factory=list)
    carried_script: str = ""
    # --- serial_dfu (nRF52) boards only -----------------------------------
    #: Where this board's built artefact lives on the medic, and what it is
    #: called. Carried per-board rather than as another module constant: the
    #: Tracker's path is already a hardcoded TRACKER_BUILD_DIR, and a second
    #: hardcoded path is how the wrong board's binary gets flashed.
    build_dir: str = ""
    #: The artefact base name arduino-cli gave this board's build — the sketch
    #: folder's name plus ".ino" ("RNode_Firmware_CE.ino" in a CE tree). ""
    #: means the upstream tree's "RNode_Firmware.ino". Found 2026-10-03: the
    #: EoRa-S3's proven image was on the medic all along under the CE name,
    #: and the flasher only ever looked for the Tracker's.
    sketch: str = ""
    dfu_package: str = ""                    # signed .zip for adafruit-nrfutil

    @property
    def recovery_instructions(self) -> str:
        """Abort-recovery button sequence (shared with the safety panel). Uses
        an explicit recovery_key when set, else falls back to the display name
        (ui.safety returns generic guidance for anything unknown)."""
        return recovery_text(self.recovery_key or self.display_name)

    @property
    def picker_label(self) -> str:
        """The row text in the board picker — the ONE moment the operator
        commits to a firmware image, so the disambiguation has to live here and
        not in a note nobody opens (operator, 2026-08-06: "lillygo naming is
        confusing"). Both picker screens render this, so there is one string to
        keep short enough for the 5" panel and one string to test."""
        return f"{self.display_name}  [{self.platform}]"

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
            if self.band_ambiguity:
                raise ValueError(
                    f"Cannot pick the {band_mhz} MHz menu answer for "
                    f"{self.key}: {self.band_ambiguity}")
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
        # --platform is OMITTED when the board does not set one, rather than
        # defaulted. The MeshPocket birth that was verified end-to-end on
        # hardware ran without it, and the resulting EEPROM is the one proven
        # to validate; adding a byte that run never wrote would provision
        # something we have not actually tested.
        p = self.provision
        parts = [f"rnodeconf {port} -r",
                 f"--product {p['product']}", f"--model {p['model']}"]
        if p.get("platform"):
            parts.append(f"--platform {p['platform']}")
        parts.append(f"--hwrev {p['hwrev']}")
        return [f"rnodeconf {port} --eeprom-wipe", " ".join(parts)]


def _official(key, name, index, platform, modem, bands, recovery_key="",
              notes="", bootloader=None, band_map=None, band_ambiguity="",
              proven=""):
    if bootloader is None:
        bootloader = _NRF52_BOOTLOADER if platform == "nRF52" else _ESP32_BOOTLOADER
    return RNodeBoard(
        key=key, display_name=name, flash_method="autoinstall",
        platform=platform, modem=modem, bands=bands, autoinstall_index=index,
        autoinstall_bands=band_map or {}, recovery_key=recovery_key,
        bootloader_instructions=bootloader, notes=notes,
        band_ambiguity=band_ambiguity, proven=proven)


# Official RNode targets — index = rnodeconf's "What kind of device is this?"
# device-menu number (1.3.7). Sub-GHz boards cover a 410-525 MHz and an 850-950 MHz variant; the
# band is chosen during flashing (AU builds use 850-950 / 915.125 MHz).
# band_map: band (MHz) -> rnodeconf band-submenu choice, transcribed from the
# firmware's autoinstall menu. Heltec V4 is HARDWARE-VERIFIED (flashed a real
# board offline: 9 -> enter -> 2 -> y). Others are transcribed from source and
# verified per board as hardware becomes available; ambiguous multi-chip boards
# (T-Beam) and un-read menus (Heltec V2, RAK4631, T-Echo) are left blank so
# autoinstall_answers() refuses rather than guess.
_OFFICIAL = [
    # The board is SOLD as "LoRa32 v2.1" but that string is printed NOWHERE on
    # it: the white label reads MODEL: T3 V1.6.1 and the back is silkscreened
    # T3_V1.6 (verified 2026-08-02 against LilyGO's wiki + their own two-sided
    # product photo, and against the board on the bench). An operator holding a
    # board marked 1.6.1 reads it as far closer to "v1.0" than to "v2.1" — and
    # picking v1.0 flashes lora32v10.zip onto v2.1 hardware. So the silkscreen
    # number goes in the NAME, where the choice is actually made. rnodeconf's
    # own menu carries the same alias ("aka T3 v1.6 / T3 v1.6.1"); ours is the
    # short form because the row has to fit an 800x480 panel.
    _official("lora32_v21", "LilyGO LoRa32 v2.1 (T3 v1.6.1)", 3, "ESP32",
              "SX1276/78", "410-525 / 850-950 MHz",
              band_map={433: 1, 868: 2, 915: 2, 923: 2},
              proven="RNode birth proven on the bench (docs/BOARD_COVERAGE.md)"),
    # v2.0 / v1.0 carry no parenthetical because nobody here has read their
    # silkscreens — a marking we haven't seen is a guess, and a guess printed as
    # instruction is worse than silence (same rule as board_images.HOW_TO_TELL).
    # Naming the T3 revision on v2.1 alone still resolves the reported trap:
    # it is the only entry that mentions a T3 number.
    _official("lora32_v20", "LilyGO LoRa32 v2.0", 4, "ESP32", "SX1276/78",
              "410-525 / 850-950 MHz",
              band_map={433: 1, 868: 2, 915: 3, 923: 4}),
    _official("lora32_v10", "LilyGO LoRa32 v1.0", 5, "ESP32", "SX1276/78",
              "410-525 / 850-950 MHz",
              band_map={433: 1, 868: 2, 915: 3, 923: 4},
              notes="Known faulty battery-charging circuit — avoid if possible."),
    # rnodeconf 2.5.0 (read on the medic 2026-08-14) asks the T-Beam band
    # question BY CHIP: 868/915/923 is menu 2 on an SX1276 board but menu 4 on
    # an SX1262 board, and the two ship under the same product name. Wrong
    # choice = wrong model byte in the EEPROM. Until the flow can ask the
    # operator which chip their board carries, refusing is the honest answer.
    _official("tbeam", "LilyGO T-Beam", 6, "ESP32", "SX1276/78/62/68",
              "410-525 / 850-950 MHz", recovery_key="LilyGO T-Beam v1.1",
              band_ambiguity=(
                  "the T-Beam ships with either an SX1276 or an SX1262 radio "
                  "chip under the same name, the firmware differs by chip, and "
                  "nothing on this side of the USB cable can see which chip "
                  "this board carries. Support for it is on the way.")),
    # Band menu transcribed from the rnodeconf 2.5.0 source on the medic
    # (RNS/Utilities/rnodeconf.py, PRODUCT_H32_V2, read 2026-08-14): plain
    # "[1] 433 [2] 868 [3] 915 [4] 923", single chip, no variant question.
    _official("heltec32_v2", "Heltec LoRa32 v2", 7, "ESP32", "SX1276/78",
              "410-525 / 850-950 MHz", recovery_key="Heltec V2",
              band_map={433: 1, 868: 2, 915: 3, 923: 4}),
    # V3 vs V4 is the other trap on this list — mixing their images BOOT-LOOPS
    # the board — but it is NOT a naming problem: the two are told apart by a
    # photo the operator can hold the board against (assets/boards/heltec_v3.png
    # + heltec_v4.png, both present) and, medic-side, by chip + USB kind (V2 =
    # classic ESP32; V3 = CP2102 bridge -> ttyUSB, V4 = native USB -> ttyACM,
    # both bench-proven — see ui.board_detect._USB_KIND). Nobody here has read
    # the two silkscreens side by side, so no marking is claimed in the name.
    # V3 + V4 are ESP32-S3 chips (verified live with esptool on both) — they
    # were mislabelled "ESP32" here, which kept them OUT of the detect
    # shortlist for every S3 chip read (the blank board pick, 2026-07-31).
    _official("heltec32_v3", "Heltec LoRa32 v3", 8, "ESP32-S3", "SX1262/68",
              "410-525 / 850-950 MHz", recovery_key="Heltec V3",
              band_map={433: 1, 868: 2, 915: 3, 923: 4}),
    _official("heltec32_v4", "Heltec LoRa32 v4", 9, "ESP32-S3", "SX1262",
              "850-950 MHz", recovery_key="Heltec V4",
              band_map={868: 1, 915: 2, 923: 3},           # verified on hardware
              proven="RNode birth proven on the bench, stock and RGB builds, "
                     "and as the radio of a Pi+RNode node (docs/BOARD_COVERAGE.md)"),
    # Same chip-variant trap as the T-Beam (rnodeconf 2.5.0, read 2026-08-14):
    # the T3S3 menu spans SX1278/SX1276/SX1268/SX1262/SX1280 variants of one
    # product; 868/915/923 is choice 2 or 4 depending on the chip.
    _official("t3s3", "LilyGO LoRa T3S3", 10, "ESP32-S3", "SX1262/68, SX127x, SX1280",
              "410-525 / 850-950 MHz / 2.4 GHz", recovery_key="T3S3",
              band_ambiguity=(
                  "the T3S3 ships with SX1276, SX1262 or SX1280 radio chips "
                  "under the same name, the firmware differs by chip, and "
                  "nothing on this side of the USB cable can see which chip "
                  "this board carries. Support for it is on the way.")),
    # The three nRF52 boards share one band menu, transcribed from the
    # rnodeconf 2.5.0 source on the medic (RNS/Utilities/rnodeconf.py) rather
    # than guessed: "[1] 433  [2] 868  [3] 915  [4] 923".
    #
    # Without a band_map, autoinstall_answers() raises "band 915 MHz not yet
    # verified" and birth_flash returns that as a failure BEFORE any flashing
    # tool runs. That is exactly what a RAK4631 birth did on 2026-08-05: the
    # operator saw "Build didn't finish" and nothing had been attempted.
    #
    # Note rnodeconf collapses 868/915/923 onto ONE model byte (MODEL_12 here,
    # MODEL_C7 for the T114) — `elif c_model > 1`. So there is no 915-specific
    # model, and none is needed: the firmware has no frequency gate at all, and
    # TX power is clamped by MODEM (SX1262 -> 22 dBm), not by model. 915.125 at
    # 17 dBm is within both.
    _official("rak4631", "RAK4631", 11, "nRF52", "SX1262",
              "430-510 / 779-928 MHz", recovery_key="RAK4631",
              band_map={433: 1, 868: 2, 915: 3, 923: 4}),
    _official("techo", "LilyGO T-Echo", 12, "nRF52", "SX1262",
              "430-510 / 779-928 MHz", recovery_key="T-Echo",
              band_map={433: 1, 868: 2, 915: 3, 923: 4}),
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
              band_map={433: 1, 868: 2, 915: 2, 923: 2},
              proven="RNode birth proven on the bench, two-way (docs/BOARD_COVERAGE.md)"),
]


_CUSTOM = [
    RNodeBoard(
        key="heltec_wireless_tracker",
        proven="this medic's own radio runs the Tracker RNode build; birth proven (docs/BOARD_COVERAGE.md)",
        display_name="Heltec Wireless Tracker",
        flash_method="arduino_cli",
        platform="ESP32-S3",
        modem="SX1262",
        bands="850-950 MHz",
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
    RNodeBoard(
        key="heltec_meshpocket",
        display_name="Heltec MeshPocket",
        flash_method="serial_dfu",
        platform="nRF52",
        modem="SX1262",
        bands="863-928 MHz",
        # Not in upstream RNode at all: this is @TheBeadster's port (PR #87 on
        # RNode_Firmware_CE), cleaned up and published at
        # 5ugAv/HELTEC-MeshPocket-RNode. So there is no rnodeconf menu entry to
        # answer, and the image is built here.
        board_model=0x46,               # BOARD_HELTEC_MESHP, read from Boards.h
        fqbn="Heltec_nRF52:Heltec_nRF52:HT-n5262",
        build_properties=[
            "build.partitions=no_ota",
            "upload.maximum_size=2097152",
        ],
        # EXACTLY the birth verified on hardware 2026-09-01: no --platform.
        provision={"product": "d2", "model": "ce", "hwrev": "1"},
        build_dir=("~/MeshPocket/RNode_Firmware_CE/build/"
                   "Heltec_nRF52.Heltec_nRF52.HT-n5262"),
        dfu_package="RNode_Firmware_CE.ino.zip",
        bootloader_instructions=(
            "Nothing to press. The tool puts this board into its bootloader "
            "itself, over USB. Its USB-C socket is CHARGE-ONLY — the magnetic "
            "pogo cable is the only data path, so use that, and check it is "
            "seated. If a flash does fail, press RST twice quickly and run it "
            "again."),
        recovery_key="MeshPocket",
        notes=(
            "nRF52840 + SX1262 with a 2.13\" e-ink screen, built into a "
            "10000mAh powerbank that magnet-mounts to a phone. It reports the "
            "SAME USB identity as the Mesh Node T114 (HT-n5262), so if the "
            "board has not been flashed as an RNode yet the tool CANNOT tell "
            "them apart and will ask you which it is. Flashing the wrong one "
            "of the pair boot-loops the board."),
    ),
    RNodeBoard(
        key="eora_s3",
        display_name="Ebyte EoRa-S3",
        flash_method="arduino_cli",
        platform="ESP32-S3",
        modem="SX1262",
        bands="863-928 MHz",
        # CUSTOM board — not in upstream RNode at all. The port lives in
        # ~/EoRa-S3/RNode_Firmware_CE (a clean CE baseline plus a board block).
        #
        # PIN MAP PROVENANCE, because getting this wrong produces a board that
        # flashes perfectly and never talks to its radio: taken from the
        # Meshtastic variant CDEBYTE_EoRa-S3, and independently confirmed
        # against Tech500/EoRa-PI-Foundation's working sketch — every pin
        # agrees (CS 7, SCK 5, MOSI 6, MISO 3, BUSY 34, DIO1 33, RST 8).
        # Ebyte's own product page disagrees on three of them, and also calls
        # this a 433 MHz board, so it was discarded as misread.
        #
        # NOT the EBYTE_ESP32-S3 Meshtastic variant — that is a hand-wired E22
        # on a generic WROOM board and shares none of these pins.
        board_model=0x47,
        # CDCOnBoot=cdc is NOT optional. The generic esp32s3 FQBN leaves it
        # disabled, which maps the firmware's Serial to UART0 on GPIO 43/44 —
        # the board then flashes cleanly and is silent to rnodeconf over USB.
        # FlashSize=4M matches the ESP32-S3FH4R2's in-package 4 MB; a wrong
        # flash size is what boot-loops an ESP32 (see the V4 RGB note in
        # ui/safety.py).
        fqbn="esp32:esp32:esp32s3:FlashSize=4M,CDCOnBoot=cdc",
        build_properties=[
            "build.partitions=no_ota",
            "upload.maximum_size=2097152",
        ],
        provision={"product": "d3", "model": "cf", "hwrev": "1"},
        build_dir="~/EoRa-S3/RNode_Firmware_CE/build/esp32.esp32.esp32s3",
        sketch="RNode_Firmware_CE.ino",
        bootloader_instructions=(
            "Attach the 915 MHz antenna FIRST — running the radio without one "
            "can damage it. Native ESP32-S3 USB, no UART chip: if flashing "
            "fails, hold BOOT, tap RST once, then release BOOT and retry."),
        recovery_key="EoRa-S3",
        notes=(
            "ESP32-S3 + E22-900MM22S (a raw SX1262), 0.96\" OLED, 22 dBm. "
            "Unflashed it looks like every other ESP32-S3 on USB (303a:1001), "
            "so the medic narrows it by MEASURED traits instead: this part is "
            "the ESP32-S3FH4R2, expected to read 4 MB flash with PSRAM "
            "present — distinct from the XIAO S3 (8 MB, no PSRAM) and the "
            "Heltec V4 (16 MB). Those figures are a PREDICTION until a real "
            "board is read; ui.board_traits learns the truth on first birth."),
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
    """Non-official boards: firmware we build here rather than pull from the
    rnodeconf cache, whatever pushes the image afterwards.

    Defined as "not autoinstall" and NOT as "arduino_cli", because the two are
    not the same thing and assuming they were hid a board completely: adding
    the serial_dfu MeshPocket left it in neither official_boards() nor here,
    so it existed in the catalogue and appeared in no picker."""
    return sorted((b for b in RNODE_BOARDS.values()
                   if b.flash_method != "autoinstall"),
                  key=lambda b: b.display_name)


def key_for_board_name(name) -> str:
    """The catalogue key for a board, however the caller happens to spell it.

    The same physical board carries at least four names in this codebase, and
    they were never meant to be compared as strings:

    ==========================  ==================  ==================
    source                      V3                  V4
    ==========================  ==================  ==================
    catalogue key               heltec32_v3         heltec32_v4
    catalogue ``display_name``  Heltec LoRa32 v3    Heltec LoRa32 v4
    health beacon / birth cert  Heltec32 V3         Heltec32 V4
    ``node_profile`` value      Heltec LoRa32 V3    Heltec LoRa32 V4
    ==========================  ==================  ==================

    Note the last two differ from the second by vocabulary and by CASE. Builds
    write the BEACON spelling onto birth certificates (verified on the medic,
    2026-08-18: ``board = 'Heltec32 V3'``), so any caller matching a certificate
    against keys or display names alone finds nothing — which is how the guided
    flow lost the board photo and started calling a known board "this radio".

    Returns "" when the name matches no board we stock.
    """
    raw = (name or "").strip()
    if not raw:
        return ""
    if raw in RNODE_BOARDS:
        return raw
    low = raw.lower()
    for key, b in RNODE_BOARDS.items():
        if key.lower() == low or (b.display_name or "").lower() == low:
            return key
    # Beacon / profile spellings: "Heltec32 V3" -> heltec32_v3.
    squashed = low.replace(" ", "_")
    if squashed in RNODE_BOARDS:
        return squashed
    return ""


def get_board(key: str) -> Optional[RNodeBoard]:
    return RNODE_BOARDS.get(key)
