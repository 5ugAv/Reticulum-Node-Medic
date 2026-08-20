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
)

#: ESP32 boards that NAME THEMSELVES over USB, exactly like the nRF52 family
#: above. Seeed's board definition ships a CDC product string of
#: "seeed-xiao-s3" (read live off the stock board, 2026-08-20), and our
#: RTNode-2400 image sets "XIAO-S3 RTNode-2400" explicitly — so the board is
#: identifiable by name BEFORE and AFTER birth. A generic Espressif identity
#: ("USB JTAG/serial debug unit" — the chip's own bootloader, every native S3
#: presents it in download mode) deliberately names NOTHING: it proves the
#: chip, never the board.
_ESP32_PRODUCT_KEYS = (
    ("seeed-xiao-s3", "xiao_esp32s3"),
    ("xiao-s3 rtnode-2400", "xiao_esp32s3"),
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
        # A named board wins; otherwise offer every nRF52 board we stock rather
        # than guessing one. Never an empty list — that is the bug being fixed.
        shortlist = named or nrf
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
    mac = parse_mac(out)
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
            "mac": mac, "flash_size": parse_flash_size(out),
            "firmware": firmware_options(chip), "boards": shortlist,
            "board_key": shortlist[0].key if len(shortlist) == 1 else None})


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
    # flash_id, not chip_id: it prints everything chip_id does (chip, revision,
    # features, MAC) AND the detected flash size, in one connection. Both are
    # read-only. The extra line is what narrows six identical-looking S3 boards
    # and what lets the medic remember this chip — see narrow_by_flash_size and
    # ui.board_memory.
    argv += ["--chip", "auto", "--port", port, "--before", "default_reset", "flash_id"]
    r = subprocess.run(argv, capture_output=True, text=True, timeout=40)
    return (r.stdout or "") + (r.stderr or "")
