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

import re
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
    # nRF52 boards are never read by esptool — see _NRF52_VENDORS below.
    "nrf52840": "nRF52",
}

#: USB vendors that mean "this is an nRF52 board, and esptool can NEVER read
#: it". 239a is Adafruit's, used by the Adafruit nRF52 bootloader that RAK,
#: LilyGO and Heltec all ship; 1915 is Nordic's own.
#:
#: Why this exists (operator, 2026-08-05): a RAK4631 was plugged in and the
#: board picker offered NOTHING. Detection ran esptool unconditionally, esptool
#: timed out against an nRF52840 — as it always must — and with no chip there
#: were no candidates. Worse, the failure advised "hold BOOT, tap RST", which
#: this board family does not have: it double-taps RESET into a UF2 bootloader.
#: Everything else was already in place (the vendor was classified, the boards
#: were in the catalogue with art and power profiles). Only the identify step
#: assumed every board is an ESP32.
_NRF52_VENDORS = {"239a", "1915"}

#: Fragments of the USB product string that name a board outright. The string
#: is far better evidence than esptool ever gives for these: the RAK reports
#: "WisCore RAK4631 Board", which IS the model, with no probing at all.
_NRF52_PRODUCT_KEYS = (
    ("rak4631", "rak4631"),
    ("wiscore", "rak4631"),
    ("t-echo", "techo"),
    ("techo", "techo"),
    ("t114", "heltec_t114"),
    # "HT-n5262" DELIBERATELY DOES NOT NAME A BOARD, and must not be re-added.
    # It is Heltec's MODULE name, shared by at least four products: the Mesh
    # Node T114, Mesh Node T1, Mesh Solar and the MeshPocket. They present the
    # same USB product string, the same DFU PID (0x0071) and build with the
    # same FQBN; only the per-unit USB serial tells them apart.
    #
    # It used to map to heltec_t114 (added 2026-08-20, when the T114 was the
    # only one of the four on this bench). On 2026-09-01 a MeshPocket was
    # converted here, so that inference is now wrong and dangerous: it would
    # offer T114 firmware for a MeshPocket, which is exactly the wrong-board
    # flash that left two MeshPockets boot-looping on Heltec's own forum.
    # Falling through means the operator gets the nRF52 picker and CHOOSES,
    # which is the honest answer to an ambiguous identity. This is the same
    # rule the "nrf52840 dk" note below anticipated: when a second board
    # starts presenting an identity, the identity stops being evidence.
    # A stock-RNode T-Echo goes ANONYMOUS: Mark's build uses the pca10056
    # defaults, so the board presents Nordic's generic "nRF52840 DK" — and
    # the RTNode option vanished for a board we had proven an hour earlier
    # (observed live 2026-08-20). Within the STOCKED catalogue that generic
    # identity can only be the T-Echo — the RAK and T114 stock builds name
    # themselves — so infer it, the same only-candidate logic as the
    # V3-behind-a-bridge rule. If another generic-presenting nRF board is
    # ever stocked, REMOVE this line and rely on the bootloader-name ladder
    # (the confirm gate still stands either way).
    ("nrf52840 dk", "techo"),
)

#: ESP32 boards that NAME THEMSELVES over USB, exactly like the nRF52 family
#: above. Seeed's FACTORY firmware ships TinyUSB descriptors reading
#: "seeed-xiao-s3" (read live off the stock board, 2026-08-20) — so a fresh
#: XIAO identifies outright. AFTER birth the name is gone: our image runs
#: ARDUINO_USB_MODE=1 (hardware CDC/JTAG), whose descriptors are burned into
#: the ESP32-S3 silicon and cannot be renamed — the birthed board presents
#: the generic "USB JTAG/serial debug unit" (verified on the first XIAO
#: birth, 2026-08-20; a USB_PRODUCT define proved to be a no-op in mode 1).
#: The generic identity deliberately names NOTHING: it proves the chip,
#: never the board.
_ESP32_PRODUCT_KEYS = (
    ("seeed-xiao-s3", "xiao_esp32s3"),
)


def esp32_self_named_key(product: str) -> Optional[str]:
    """The board key an ESP32 USB product string names, or None. None is the
    common case — most ESP32 boards present the chip's generic identity."""
    low = (product or "").lower()
    for needle, key in _ESP32_PRODUCT_KEYS:
        if needle in low:
            return key
    return None


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


def parse_mac(esptool_output: str) -> Optional[str]:
    """The chip's eFuse MAC from esptool's "MAC: ..." line, or None.

    Unique to the silicon and unchanged by any reflash, which is what makes it
    a usable name for "this exact board" — see ui.board_memory. Local use only;
    it is never announced or written into a certificate.
    """
    for line in (esptool_output or "").splitlines():
        low = line.strip()
        if low.lower().startswith("mac:"):
            mac = low.split(":", 1)[1].strip()
            return mac or None
    return None


def parse_flash_size(esptool_output: str) -> Optional[str]:
    """The flash size esptool DETECTED ("16MB"), or None.

    Detected, not declared. The Heltec V4's own datasheet says ESP32-S3FN8 —
    8MB — and the V4 on this bench measured 16MB (2026-08-09). Vendor documents
    describe a design; boards ship with whatever flash the factory had.
    """
    for line in (esptool_output or "").splitlines():
        low = line.strip().lower()
        if low.startswith("detected flash size:"):
            return low.split(":", 1)[1].strip().upper() or None
    return None


#: Flash sizes MEASURED on boards in hand, per board key. Never copied from a
#: datasheet — see parse_flash_size for why that would be worse than useless.
#: A board with no entry here is never excluded by size: we do not know it yet,
#: and a wrong exclusion hides the board the operator is actually holding.
#:
#: Fill this in as boards pass across the bench. Every entry added permanently
#: shortens the "which radio board is this?" list for everyone after.
_FLASH_SIZE = {
    "heltec32_v4": ("16MB",),      # measured 2026-08-09, Boya 68/4018
    "xiao_esp32s3": ("8MB",),      # PlatformIO board JSON + Seeed spec, 2026-08-20
}


def parse_psram(esptool_output: str) -> Optional[str]:
    """PSRAM as esptool REPORTS it on the Features line — "8MB", "2MB", or
    "none" when the line exists and mentions no PSRAM. None means we never
    saw a Features line at all (say nothing rather than guess).

    This is the discriminator the S3 gallery was missing: a Tracker, a XIAO,
    a T3S3 and a T-Deck are one chip to esptool, but they do not carry the
    same PSRAM (operator, 2026-08-30: "more information gatherable to
    distinguish it from other possible boards?"). What each model actually
    has is LEARNED from confirmed boards — see ui.board_traits — never
    copied from a datasheet.
    """
    for line in (esptool_output or "").splitlines():
        low = line.strip().lower()
        if not low.startswith("features:"):
            continue
        if "psram" not in low:
            return "none"
        # e.g. "Features: WiFi, BLE, Embedded PSRAM 8MB (AP_3v3)"
        after = low.split("psram", 1)[1]
        for token in after.replace("(", " ").replace(",", " ").split():
            t = token.strip().upper()
            if t.endswith("MB") and t[:-2].isdigit():
                return t
        return "yes"          # PSRAM present, size not stated
    return None


def narrow_by_flash_size(shortlist, size: Optional[str]):
    """Boards whose MEASURED flash size matches *size*.

    Same contract as narrow_by_variant: unknown size, or no measured board
    matching it, leaves the list exactly as it was.
    """
    if not size:
        return shortlist
    hits = [b for b in shortlist if size in _FLASH_SIZE.get(b.key, ())]
    unmeasured = [b for b in shortlist if b.key not in _FLASH_SIZE]
    # A board we have never measured cannot be ruled out by a measurement.
    return (hits + unmeasured) if hits else shortlist


def firmware_options(chip: Optional[str],
                     board_key: Optional[str] = None) -> List[str]:
    """Firmware the board can take, best-first.

    The chip decides for ESP32-S3 (every S3 we stock has an RTNode-2400 build).
    Elsewhere the chip is NOT enough: "nrf52840" covers the T-Echo, which HAS a
    proven RTNode-2400 build (2026-08-19), and the RAK4631/T114, which do not —
    so a positively identified board is asked against the build registry
    itself. An unidentified nRF52 stays RNode-only: offering a build that three
    of four candidates cannot take is the wrong kind of fail-open.
    """
    if chip == "esp32s3":
        return ["rtnode2400", "rnode"]
    if board_key:
        from workflows.rtnode_build import target_for_board_key
        if target_for_board_key(board_key):
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


def port_usb_product_id(port: str) -> str:
    """The USB product id behind a serial *port* ("8029"), or "".

    Same bounded sysfs walk as ``port_usb_vendor``. Needed to tell an nRF52
    board's two personalities apart: the RAK4631 runs as 239a:8029 and its
    bootloader as 239a:002a, and knowing which one is present decides whether
    the board still has to be reset into DFU before it can be flashed.
    """
    import os
    name = os.path.basename((port or "").strip())
    if not name:
        return ""
    try:
        node = os.path.realpath(f"/sys/class/tty/{name}/device")
        for _ in range(8):                       # bounded walk to the USB node
            cand = os.path.join(node, "idProduct")
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


def port_usb_product(port: str) -> str:
    """The USB product string behind a serial *port* ("WisCore RAK4631 Board"),
    or "". Same bounded sysfs walk as ``port_usb_vendor``."""
    import os
    name = os.path.basename((port or "").strip())
    if not name:
        return ""
    try:
        node = os.path.realpath(f"/sys/class/tty/{name}/device")
        for _ in range(8):
            cand = os.path.join(node, "product")
            if os.path.isfile(cand):
                with open(cand) as fh:
                    return fh.read().strip()
            parent = os.path.dirname(node)
            if parent == node:
                break
            node = parent
    except Exception:                            # noqa: BLE001
        pass
    return ""


#: USB product fragment -> every board that presents it. The counterpart to
#: _NRF52_PRODUCT_KEYS: that maps an identity to ONE board and must stay silent
#: when the identity is shared, which left the operator picking from every nRF52
#: board we stock. This narrows a shared identity to the boards that actually
#: claim it, which is a real answer without being a guess.
_NRF52_FAMILY_KEYS = (
    # Heltec's module name, not a product name. At least four boards ship it:
    # Mesh Node T114, Mesh Node T1, Mesh Solar and the MeshPocket. Same product
    # string, same DFU PID 0x0071, same bootloader Model and Board-ID (verified
    # from a MeshPocket's own INFO_UF2: "Model: HT-n5262"), same FQBN. Only the
    # per-unit serial differs, and that says WHICH board, never WHAT board.
    # Only the two we stock are listed; flashing either image onto the other
    # boot-loops the board, which is why this asks rather than assumes.
    ("ht-n5262", ("heltec_t114", "heltec_meshpocket")),
)

#: RNode board byte -> our board key. An already-flashed RNode reports this in
#: its product line ("... (d2:ce:46)"), and unlike anything on the USB bus it is
#: the FIRMWARE's own statement of what board it is running on — so it settles
#: the HT-n5262 ambiguity outright, for re-flashes and adoptions.
_RNODE_BOARD_BYTE = {
    0x3C: "heltec_t114",
    0x46: "heltec_meshpocket",
    0x51: "rak4631",
    0x44: "techo",
}


def rnode_board_key(info_output: str) -> Optional[str]:
    """The board key an ``rnodeconf -i`` transcript names, or None.

    Reads the third byte of the product triple: ``(d2:ce:46)`` -> 0x46 ->
    MeshPocket. None whenever the board is not a provisioned RNode, the output
    is unreadable, or the byte is one we do not stock — all of which mean
    "cannot tell", never "wrong board"."""
    m = re.search(r"\(([0-9a-fA-F]{2}):([0-9a-fA-F]{2}):([0-9a-fA-F]{2})\)",
                  info_output or "")
    if not m:
        return None
    return _RNODE_BOARD_BYTE.get(int(m.group(3), 16))


def nrf52_family_keys(product: str) -> tuple:
    """Every board key that could be behind this USB product string. Empty when
    the string is not a known shared identity."""
    low = (product or "").lower()
    for needle, keys in _NRF52_FAMILY_KEYS:
        if needle in low:
            return keys
    return ()


def nrf52_board_key(product: str) -> Optional[str]:
    """The board key a USB product string names, or None if it names none we
    stock. None is not a failure — the caller then offers every nRF52 board
    rather than guessing, because a wrong board costs a bad flash."""
    low = (product or "").lower()
    for needle, key in _NRF52_PRODUCT_KEYS:
        if needle in low:
            return key
    return None


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


def sample_ports(ports_fn: Callable[[], List[str]], attempts: int = 6,
                 sleep_fn: Optional[Callable[[float], None]] = None):
    """Look for work-board ports over a few seconds. Returns ``(ports, flapping)``.

    A single snapshot is the wrong instrument for a board that is REBOOTING IN A
    LOOP. A Heltec V4 flashed with a bad bootloader header re-enumerates about
    every two seconds, so /dev/ttyACM* exists most of the time and is missing the
    rest — and a one-shot look that lands in a gap reports "no work board on the
    medic's USB", i.e. "you didn't plug it in". The operator had it plugged in
    the whole time (live, 2026-08-09); the board was the fault, and the medic
    named the cable instead.

    So: take the first look; if it is empty, keep looking once a second. The
    normal case (board present) still costs one snapshot and no delay. If a port
    turns up only after an empty look, the port is FLAPPING — that is a finding
    in its own right, not noise, and the caller says so.
    """
    if sleep_fn is None:
        import time
        sleep_fn = time.sleep
    ports = list(ports_fn() or [])
    if ports:
        return ports, False
    for _ in range(max(0, attempts - 1)):
        sleep_fn(1.0)
        ports = list(ports_fn() or [])
        if ports:
            return ports, True
    return [], False


#: Shown when a board is present but keeps re-enumerating. It is not a warning
#: about the cable — the medic has SEEN the board, repeatedly.
FLAPPING_REASON = (
    "This board keeps disconnecting and reappearing — it is rebooting in a "
    "loop, which is what a bad firmware image looks like. Leave it plugged in: "
    "flashing it is the repair.")


def detect_board(boards, ports_fn: Optional[Callable[[], List[str]]] = None,
                 reader: Optional[Callable[[str], str]] = None,
                 vendor_fn: Optional[Callable[[str], str]] = None,
                 product_fn: Optional[Callable[[str], str]] = None,
                 rnode_fn: Optional[Callable[[str], str]] = None,
                 attempts: int = 6,
                 sleep_fn: Optional[Callable[[float], None]] = None,
                 use_memory: bool = True) -> dict:
    """Detect the connected work board. Returns a result dict:
    ``{found, port?, chip?, platform?, firmware?, boards?, board_key?, reason?}``.
    ``boards`` is the full board catalogue (to shortlist); ``ports_fn`` returns the
    connected WORK-board ports (onboard excluded); ``reader`` reads a chip on a port.
    Both are injected in tests and default to the medic's real hardware.

    A found board carries ``unstable``: True when the port had to be waited for
    (see :func:`sample_ports`)."""
    ports, flapping = sample_ports(ports_fn or _default_ports, attempts, sleep_fn)
    if not ports:
        return {"found": False, "reason":
                "No work board on the medic's USB — plug the board in with a "
                "known-good data cable (its own onboard board doesn't count)."}
    port = ports[0]

    def _out(res: dict) -> dict:
        # Carried on FAILURES too: "couldn't read the chip" on a board that is
        # rebooting under the reader is the boot loop talking, and the operator
        # must not be sent hunting for another cable.
        if flapping:
            res["unstable"] = True
            res["unstable_reason"] = FLAPPING_REASON
        return res

    # nRF52 boards FIRST, before esptool is ever reached. esptool cannot read an
    # nRF52840 — it will always time out — so running it here produced an empty
    # picker and told the operator to hold a BOOT button this family does not
    # have (RAK4631, 2026-08-05). Identify from the USB product string instead,
    # which for these boards literally names the model.
    if (vendor_fn or port_usb_vendor)(port) in _NRF52_VENDORS:
        product = (product_fn or port_usb_product)(port)
        key = nrf52_board_key(product)
        nrf = [b for b in boards
               if _platform_key(getattr(b, "platform", "")) == "nrf52"]
        named = [b for b in nrf if b.key == key] if key else []

        # Boards that share a USB identity (HT-n5262: T114 and MeshPocket) can
        # still be settled by ASKING THE BOARD, when it is already an RNode:
        # its firmware reports the board byte it was built for, which is a
        # statement rather than an inference. Only consulted when the product
        # string was ambiguous, so a board that names itself is never probed,
        # and any failure just falls through to asking the operator.
        family = nrf52_family_keys(product) if not named else ()
        if family:
            try:
                probed = rnode_board_key((rnode_fn or _default_rnode_info)(port))
            except Exception:                        # noqa: BLE001
                probed = None
            if probed and probed in family:
                named = [b for b in nrf if b.key == probed]

        # A named board wins; then the boards that actually claim this shared
        # identity; then every nRF52 board we stock rather than guessing one.
        # Never an empty list — that is the bug being fixed.
        shortlist = named or [b for b in nrf if b.key in family] or nrf
        nrf_key = shortlist[0].key if len(shortlist) == 1 else None
        return _out({"found": True, "port": port, "chip": "nrf52840",
                "platform": "nRF52", "product": product,
                "firmware": firmware_options("nrf52840", nrf_key),
                "boards": shortlist,
                "board_key": nrf_key})

    # Self-naming ESP32 boards SECOND, still before esptool: the XIAO S3's
    # CDC product string literally names the board (and after birth our image
    # names it "XIAO-S3 RTNode-2400"), which outranks anything inferable from
    # silicon — a V4 and a XIAO are the same chip to esptool. Only the named
    # hit short-circuits; the generic JTAG identity falls through to the
    # esptool ladder as before.
    named_key = esp32_self_named_key((product_fn or port_usb_product)(port))
    if named_key:
        hit = [b for b in boards if b.key == named_key]
        if hit:
            return _out({"found": True, "port": port, "chip": "esp32s3",
                    "platform": "ESP32-S3",
                    "firmware": firmware_options("esp32s3", named_key),
                    "boards": hit, "board_key": named_key})

    try:
        out = (reader or _default_reader)(port)
    except Exception as e:
        return _out({"found": False, "port": port, "reason": f"Couldn't talk to the "
                f"board on {port}: {e}. Try another data cable, or hold BOOT while "
                "plugging it in."})
    chip = parse_chip(out)
    if not chip:
        return _out({"found": False, "port": port, "raw": out, "reason":
                "Reached the port but couldn't read the chip — hold BOOT, tap RST, "
                "release BOOT, then Detect again (S3 boards need download mode)."})
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
    # Fourth cut: the flash size actually on the board. Only measured boards
    # count, so today this mostly re-orders; every board measured onto the bench
    # makes it cut deeper.
    shortlist = narrow_by_flash_size(shortlist, parse_flash_size(out))
    # Fourth-and-a-half cut: traits LEARNED from boards the operator has
    # already confirmed (PSRAM + measured flash). Starts inert on a fresh
    # medic and cuts deeper with every birth — the bench teaches the tool
    # (2026-08-30). A model with nothing recorded is never excluded.
    psram = parse_psram(out)
    measured = {"psram": psram, "flash_size": parse_flash_size(out)}
    try:
        from ui.board_traits import narrow_by_traits
        shortlist = narrow_by_traits(shortlist, measured)
    except Exception:                                # noqa: BLE001
        pass
    mac = parse_mac(out)
    # MAC-prefix evidence RANKS the survivors (never cuts them): boards of one
    # model cluster in a MAC block, so the likely one leads the grid and is
    # named as a suggestion. See ui.board_traits — evidence, not proof.
    likely_key = None
    try:
        from ui.board_traits import likely_from_mac, rank_by_mac
        if mac:
            hit = likely_from_mac(shortlist, mac)
            likely_key = getattr(hit, "key", None) if hit else None
            shortlist = rank_by_mac(shortlist, mac)
    except Exception:                                # noqa: BLE001
        pass
    # Fifth, and the one that ends the question for good: what the operator
    # already told us THIS chip is. Their answer beats every inference we can
    # make from silicon, because they can see the board and we cannot.
    # use_memory=False is how a caller says "the operator has just told me the
    # remembered answer is WRONG — show them everything". It is deliberately a
    # read-time switch rather than an erase: a navigation gesture must never
    # destroy learned state. Whatever they pick next overwrites the memory
    # anyway, which is the correction, and it costs nothing if they change
    # their mind again on the way (live, 2026-08-09: backing out of the
    # confirmation wiped the file, so the grid came straight back).
    if mac and use_memory:
        try:
            from ui.board_memory import recall
            known = recall(mac)
        except Exception:                            # noqa: BLE001
            known = None
        if known:
            hit = [b for b in shortlist if b.key == known]
            if hit:
                return _out({"found": True, "port": port, "chip": chip,
                             "platform": platform, "mac": mac,
                             "flash_size": parse_flash_size(out),
                             "firmware": firmware_options(chip),
                             "boards": hit, "board_key": known,
                             "remembered": True})
    return _out({"found": True, "port": port, "chip": chip, "platform": platform,
            "mac": mac, "flash_size": parse_flash_size(out), "psram": psram,
            "likely_key": likely_key,
            "firmware": firmware_options(chip), "boards": shortlist,
            "board_key": shortlist[0].key if len(shortlist) == 1 else None})


def _default_ports() -> List[str]:
    from ui.hw_factories import local_board_ports
    return list(local_board_ports())


def _default_rnode_info(port: str) -> str:
    """``rnodeconf -i`` output for *port*, or "" if it says nothing useful.

    Read-only: -i asks the board what it is and writes nothing. Short timeout
    and every failure swallowed, because this runs only to break a tie — a
    board that is not an RNode, or is busy (a phone holding it over BLE stops
    the firmware reading USB at all), simply does not answer, and the operator
    is asked instead."""
    import os
    import subprocess
    try:
        return subprocess.run(
            [os.path.expanduser("~/.local/bin/rnodeconf"), port, "-i"],
            capture_output=True, text=True, timeout=25,
        ).stdout or ""
    except Exception:                                # noqa: BLE001
        return ""


def _default_reader(port: str, esptool: str = DEFAULT_ESPTOOL) -> str:
    import os
    import shlex
    import subprocess
    # Build an argv list so the port (and any future interpolated value) is a
    # distinct argument that can never be shell-parsed. ``esptool`` is a static
    # literal, safe to tokenise; ``~`` is expanded here (no shell to do it).
    argv = [os.path.expanduser(tok) for tok in shlex.split(esptool)]
    # flash_id, not chip_id: it prints everything chip_id does (chip, revision,
    # features, MAC) AND the detected flash size, in one connection. Both are
    # read-only. The extra line is what narrows six identical-looking S3 boards
    # and what lets the medic remember this chip — see narrow_by_flash_size and
    # ui.board_memory.
    argv += ["--chip", "auto", "--port", port, "--before", "default_reset", "flash_id"]
    r = subprocess.run(argv, capture_output=True, text=True, timeout=40)
    return (r.stdout or "") + (r.stderr or "")
