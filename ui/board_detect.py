"""Auto-detect the board plugged into the medic, so BIRTH can pre-select it.

The operator shouldn't have to know whether the thing on their desk is a Heltec
V4 or a XIAO S3 — the medic can read the chip over USB (esptool) and narrow it
down. What a chip read gives us reliably is the CHIP FAMILY (ESP32 / ESP32-S3 /
…); that maps to a firmware suggestion and a shortlist of boards (a single chip
family covers several boards, so it isn't always one answer — we pre-select when
it is, and filter the picker when it isn't).

Pure + injectable: ``parse_chip`` and the mappings are unit-tested; the actual
esptool call and the connected-port lookup are injected (defaults wired for the
medic). Onboard boards (Jonesey) are already excluded by local_board_ports.
"""

from __future__ import annotations

from typing import Callable, List, Optional

#: esptool bundled with the rnodeconf firmware cache on the medic (same as the
#: flash workflows use). ``chip_id`` with ``--chip auto`` prints "Chip is <X>".
from workflows.rnode_flash import esptool_cmd as _esptool_cmd
#: Derived, never literal — see rnode_flash.esptool_path.
DEFAULT_ESPTOOL = _esptool_cmd()

#: Order matters — the specific S3/C3/S2 needles are tried before plain esp32,
#: since "esp32-s3" also contains "esp32".
_CHIP_PATTERNS = [
    ("esp32s3", ("esp32-s3", "esp32s3")),
    ("esp32c3", ("esp32-c3", "esp32c3")),
    ("esp32s2", ("esp32-s2", "esp32s2")),
    ("esp32c6", ("esp32-c6", "esp32c6")),
    ("esp32", ("esp32",)),
]

_PLATFORM_BY_CHIP = {
    "esp32s3": "ESP32-S3", "esp32": "ESP32", "esp32c3": "ESP32-C3",
    "esp32s2": "ESP32-S2", "esp32c6": "ESP32-C6",
}


def parse_chip(esptool_output: str) -> Optional[str]:
    """The chip family from esptool's stdout ("Chip is ESP32-S3 …"), or None."""
    low = (esptool_output or "").lower()
    for chip, needles in _CHIP_PATTERNS:
        if any(n in low for n in needles):
            return chip
    return None


#: Chip VARIANT fragments that identify a specific board among boards that share
#: a chip FAMILY. The classic-ESP32 group (LoRa32 v2.1/v2.0/v1.0, T-Beam, Heltec
#: V2) all read as plain "esp32", so family alone leaves a five-way guess.
#:
#: Measured on the bench 2026-08-02, a LilyGO LoRa32 v2.1 (silkscreen reads
#: "T3 V1.6.1"; unsigned.io Pocket Node build):
#:     Chip is ESP32-PICO-D4 (revision v1.1)
#:     Features: ... Embedded Flash ...   Detected flash size: 4MB
#: The PICO-D4 is a system-in-package with the flash on the die, which is what
#: distinguishes the T3 v1.6 from its siblings — Heltec V2 and T-Beam use a bare
#: ESP32-D0WDQ6 with external flash.
#:
#: This NARROWS; it never overrides. Board revisions vary, so a variant we
#: haven't seen simply leaves the shortlist as it was.
_CHIP_VARIANT = {
    "lora32_v21": ("pico-d4",),
}


def parse_chip_variant(esptool_output: str) -> Optional[str]:
    """The specific package from esptool's "Chip is ..." line, lowercased
    ("esp32-pico-d4"), or None."""
    for line in (esptool_output or "").splitlines():
        low = line.strip().lower()
        if low.startswith("chip is "):
            return low[len("chip is "):].split("(")[0].strip() or None
    return None


def narrow_by_variant(shortlist, variant: Optional[str]):
    """Boards whose known chip variant matches *variant*.

    Returns the narrowed list, or the original when the variant is unknown or
    matches nothing — a wrong exclusion costs one tap, so this only ever helps.
    """
    if not variant:
        return shortlist
    hits = [b for b in shortlist
            if any(frag in variant for frag in _CHIP_VARIANT.get(b.key, ()))]
    return hits or shortlist


def firmware_options(chip: Optional[str]) -> List[str]:
    """Firmware the chip can take, best-first. ESP32-S3 boards are the RTNode-2400
    targets (Grey Hat's standalone transport node — health beacon + remote repair),
    so RTNode-2400 leads there; everything can run RNode."""
    if chip == "esp32s3":
        return ["rtnode2400", "rnode"]
    return ["rnode"]


def _platform_key(p: str) -> str:
    return (p or "").replace("-", "").replace(" ", "").lower()


#: How each board reaches USB: "bridge" (external UART chip -> /dev/ttyUSB*)
#: or "native" (the MCU's own USB-CDC -> /dev/ttyACM*). This splits boards
#: the chip read alone can't tell apart (operator spec 2026-08-01: only show
#: boards the medic genuinely can't distinguish). Chip-level FACTS: classic
#: ESP32 has no USB controller (always a bridge); nRF52840 is always native.
#: S3 boards are per-board: V3 = CP2102 bridge (bench-proven ttyUSB), V4 /
#: Wireless Tracker = native (bench-proven ttyACM), XIAO = bare module, no
#: bridge chip exists; T-Deck / T-Beam Supreme / T3S3 = native per vendor
#: docs (if a hardware revision proves otherwise, the full-board-list
#: fallback still reaches them — wrong exclusion costs one tap, not a brick).
_USB_KIND = {
    # classic ESP32 -> always bridge
    "lora32_v21": "bridge", "lora32_v20": "bridge", "lora32_v10": "bridge",
    "tbeam": "bridge", "heltec32_v2": "bridge",
    # nRF52 -> always native
    "rak4631": "native", "techo": "native", "heltec_t114": "native",
    # ESP32-S3, per-board
    "heltec32_v3": "bridge",
    "heltec32_v4": "native",
    "heltec_wireless_tracker": "native",
    "xiao_esp32s3": "native",
    "tdeck": "native",
    "tbeam_supreme": "native",
    "t3s3": "native",
}


#: USB vendor IDs belonging to USB-SERIAL BRIDGE chips. A port behind one of
#: these is bridged no matter what the device node is called.
_BRIDGE_VENDORS = {
    "10c4",   # Silicon Labs  CP210x
    "1a86",   # QinHeng/WCH   CH340 / CH343 / CH9102
    "0403",   # FTDI          FT232 etc
    "067b",   # Prolific      PL2303
    "04d8",   # Microchip     MCP2221
}

#: USB vendor IDs of MCUs whose own controller presents the port (native USB).
_NATIVE_VENDORS = {
    "303a",   # Espressif  (ESP32-S3 USB-JTAG/serial)
    "239a",   # Adafruit   (nRF52840 CDC)
    "1915",   # Nordic
    "2e8a",   # Raspberry Pi Ltd
}


def port_usb_vendor(port: str) -> str:
    """The USB vendor id behind a serial *port* ("1a86"), or "".

    Walks sysfs from the tty up to the USB device node that owns it.
    """
    import os
    name = os.path.basename((port or "").strip())
    if not name:
        return ""
    try:
        node = os.path.realpath(f"/sys/class/tty/{name}/device")
        for _ in range(8):                       # bounded walk to the USB node
            cand = os.path.join(node, "idVendor")
            if os.path.isfile(cand):
                with open(cand) as fh:
                    return fh.read().strip().lower()
            parent = os.path.dirname(node)
            if parent == node:
                break
            node = parent
    except Exception:                            # noqa: BLE001
        pass
    return ""


def _port_usb_kind(port: str):
    """bridge / native / None for *port*.

    Decided by the USB VENDOR, not the device-node name. A CH9102 (1a86) is a
    bridge chip that enumerates as CDC — so it appears as ttyACM — and the old
    name-based rule called it "native", inverting the answer for every recent
    LilyGO board. Seen live on the bench (2026-08-02): a LoRa32 on ttyACM1
    behind vendor 1a86. The name rule stays as a fallback for anything whose
    vendor can't be read.
    """
    vendor = port_usb_vendor(port)
    if vendor in _BRIDGE_VENDORS:
        return "bridge"
    if vendor in _NATIVE_VENDORS:
        return "native"
    if "ttyUSB" in (port or ""):                 # fallback: only ever a bridge
        return "bridge"
    if "ttyACM" in (port or ""):
        return "native"
    return None


def detect_board(boards, ports_fn: Optional[Callable[[], List[str]]] = None,
                 reader: Optional[Callable[[str], str]] = None) -> dict:
    """Detect the connected work board. Returns a result dict:
    ``{found, port?, chip?, platform?, firmware?, boards?, board_key?, reason?}``.
    ``boards`` is the full board catalogue (to shortlist); ``ports_fn`` returns the
    connected WORK-board ports (onboard excluded); ``reader`` reads a chip on a port.
    Both are injected in tests and default to the medic's real hardware."""
    ports = (ports_fn or _default_ports)()
    if not ports:
        return {"found": False, "reason":
                "No work board on the medic's USB — plug the board in with a "
                "known-good data cable (its own onboard board doesn't count)."}
    port = ports[0]
    try:
        out = (reader or _default_reader)(port)
    except Exception as e:
        return {"found": False, "port": port, "reason": f"Couldn't talk to the "
                f"board on {port}: {e}. Try another data cable, or hold BOOT while "
                "plugging it in."}
    chip = parse_chip(out)
    if not chip:
        return {"found": False, "port": port, "raw": out, "reason":
                "Reached the port but couldn't read the chip — hold BOOT, tap RST, "
                "release BOOT, then Detect again (S3 boards need download mode)."}
    platform = _PLATFORM_BY_CHIP.get(chip)
    shortlist = [b for b in boards
                 if _platform_key(getattr(b, "platform", "")) == _platform_key(platform)]
    # Second cut: the PORT TYPE (bridge ttyUSB vs native ttyACM) rules out
    # boards whose USB wiring can't produce this port. A lone survivor is
    # auto-picked (e.g. an S3 on ttyUSB can only be the CP2102-bridged V3).
    pk = _port_usb_kind(port)
    if pk:
        refined = [b for b in shortlist
                   if _USB_KIND.get(b.key) in (pk, None)]
        if refined:
            shortlist = refined
    # Third cut: the chip PACKAGE. "esp32" covers five boards we stock; the
    # exact variant separates them where we know it (bench-measured).
    shortlist = narrow_by_variant(shortlist, parse_chip_variant(out))
    return {"found": True, "port": port, "chip": chip, "platform": platform,
            "firmware": firmware_options(chip), "boards": shortlist,
            "board_key": shortlist[0].key if len(shortlist) == 1 else None}


def _default_ports() -> List[str]:
    from ui.hw_factories import local_board_ports
    return list(local_board_ports())


def _default_reader(port: str, esptool: str = DEFAULT_ESPTOOL) -> str:
    import os
    import shlex
    import subprocess
    # Build an argv list so the port (and any future interpolated value) is a
    # distinct argument that can never be shell-parsed. ``esptool`` is a static
    # literal, safe to tokenise; ``~`` is expanded here (no shell to do it).
    argv = [os.path.expanduser(tok) for tok in shlex.split(esptool)]
    argv += ["--chip", "auto", "--port", port, "--before", "default_reset", "chip_id"]
    r = subprocess.run(argv, capture_output=True, text=True, timeout=40)
    return (r.stdout or "") + (r.stderr or "")
