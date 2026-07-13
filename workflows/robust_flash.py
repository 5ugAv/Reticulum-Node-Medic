"""Robust ESP32 flashing that survives marginal USB power / brownout.

Some boards (verified on a real Heltec V4) drop mid-write with a USB over-current
event, corrupting the image — the exact fault that leaves a board boot-looping on
``SHA-256 comparison failed``. Plain ``esptool write_flash`` is one long
continuous write, so a single drop wastes the whole flash and can silently
corrupt it.

This flasher climbs an escalation ladder, doing the least work that succeeds::

    full image  ->  256 KB chunks  ->  64 KB chunks  ->  32 KB chunks
    (baud lowers as chunks shrink; both shrink the continuous-stress window)

At every step it READS BACK and verifies (never trust an unverified flash), and
between failures it POWER-CYCLES the board's USB port via ``uhubctl`` — a true
cold start that also clears a latched stuck-white status LED — all autonomously,
with no operator action. Only when the whole ladder is exhausted does it stop and
report a genuine hardware limit, classified from HOW it failed (brownout vs a
flaky data link). When the port can't be power-switched it falls back to an
esptool DTR/RTS soft reset.

Verified on hardware: a Heltec V4 that plain full-flash could NOT program was
flashed + verified at the 256 KB-chunk tier, each chunk recovering via an
autonomous port power-cycle.
"""

from __future__ import annotations

import math
import re
import time as _time
from dataclasses import dataclass
from typing import Callable, List, Optional, Tuple

from transport.connection import Connection

#: esptool bundled in the rnodeconf firmware cache on the node.
DEFAULT_ESPTOOL = "python3 ~/.config/rnodeconf/update/1.86/esptool.py"
#: Scratch file the app is sliced into, one chunk at a time.
CHUNK_FILE = "/tmp/rf_chunk"


def find_hub_port(connection: Connection,
                  serial: str) -> Tuple[Optional[str], Optional[int]]:
    """Locate the ``(hub, port)`` the USB device with *serial* sits on by parsing
    ``uhubctl``, so the flasher can power-cycle exactly that port autonomously
    (verified: a Heltec V4 with serial F8:5B:1B:A6:85:00 was found on hub 3,
    port 1). Returns ``(None, None)`` if the device or a power-switchable port
    isn't found."""
    out = connection.run("sudo -n uhubctl")[1]
    hub = None
    for raw in out.splitlines():
        line = raw.strip()
        m = re.match(r"Current status for hub (\S+)", line)
        if m:
            hub = m.group(1)
            continue
        pm = re.match(r"Port (\d+):", line)
        if pm and serial and serial in line:
            return hub, int(pm.group(1))
    return None, None


@dataclass
class Region:
    """A file to write at a flash offset."""
    offset: int
    path: str


@dataclass
class FlashTier:
    name: str
    chunk_bytes: Optional[int]     # None -> one continuous whole-image write
    baud: int
    chunk_retries: int = 4


#: Least-work-first ladder. Baud drops as chunks shrink (see module docstring).
DEFAULT_LADDER: List[FlashTier] = [
    FlashTier("full @460800", None, 460800),
    FlashTier("full @460800 retry", None, 460800),
    FlashTier("256KB chunks @460800", 256 * 1024, 460800),
    FlashTier("64KB chunks @230400", 64 * 1024, 230400),
    FlashTier("32KB chunks @115200", 32 * 1024, 115200),
]


@dataclass
class FlashProgress:
    kind: str          # tier_start|chunk_ok|chunk_retry|power_cycle|tier_ok|tier_fail|done
    tier: str = ""
    detail: str = ""


@dataclass
class RobustFlashResult:
    success: bool
    tier: Optional[str] = None
    failed_offset: Optional[int] = None
    diagnosis: str = ""


class RobustFlasher:
    def __init__(self, connection: Connection, port: str,
                 hub: Optional[str] = None, hub_port: Optional[int] = None,
                 chip: str = "esp32s3", esptool: str = DEFAULT_ESPTOOL,
                 flash_mode: str = "dio", flash_freq: str = "80m",
                 flash_size: str = "16MB",
                 sleep: Optional[Callable[[float], None]] = None,
                 write_timeout: int = 400, verify_timeout: int = 200):
        self.c = connection
        self.port = port
        self.hub = hub
        self.hub_port = hub_port
        self.chip = chip
        self.esptool = esptool
        self.flash_mode = flash_mode
        self.flash_freq = flash_freq
        self.flash_size = flash_size
        self.sleep = sleep or _time.sleep
        self.write_timeout = write_timeout
        self.verify_timeout = verify_timeout

    # -- USB power-cycle (autonomous hard reset) ---------------------------

    @property
    def can_power_cycle(self) -> bool:
        return self.hub is not None and self.hub_port is not None

    def power_cycle(self, off_secs: float = 3.0, settle: float = 6.0) -> bool:
        """Hard-reset the board by cutting its USB port power (uhubctl), then wait
        for it to re-enumerate. Falls back to an esptool DTR/RTS soft reset when
        the port can't be power-switched."""
        if self.can_power_cycle:
            self.c.run(f"sudo -n uhubctl -l {self.hub} -p {self.hub_port} -a off")
            self.sleep(off_secs)
            self.c.run(f"sudo -n uhubctl -l {self.hub} -p {self.hub_port} -a on")
        else:
            self.c.run(f"{self.esptool} --chip {self.chip} --port {self.port} "
                       f"--before default_reset --after hard_reset read_mac", 30)
        self.sleep(settle)
        return self._wait_port()

    def _wait_port(self, tries: int = 12) -> bool:
        for _ in range(tries):
            if self.c.run(f"test -e {self.port}")[0] == 0:
                return True
            self.sleep(1)
        return False

    # -- write / verify ----------------------------------------------------

    def _write(self, offset: int, path: str, baud: int,
               patch_header: bool = False) -> bool:
        # Header flash params only matter for the bootloader at 0x0; elsewhere
        # keep the existing header so a chunk write can't corrupt it.
        size = (f"--flash_mode {self.flash_mode} --flash_freq {self.flash_freq} "
                f"--flash_size {self.flash_size}") if patch_header else \
               "--flash_size keep"
        cmd = (f"{self.esptool} --chip {self.chip} --port {self.port} "
               f"--baud {baud} --before default_reset --after no_reset "
               f"write_flash -z {size} 0x{offset:x} {path}")
        return self.c.run(cmd, self.write_timeout)[0] == 0

    def _verify(self, offset: int, path: str, baud: int) -> bool:
        cmd = (f"{self.esptool} --chip {self.chip} --port {self.port} "
               f"--baud {baud} --before default_reset --after no_reset "
               f"verify_flash 0x{offset:x} {path}")
        return self.c.run(cmd, self.verify_timeout)[0] == 0

    def _write_verified(self, offset: int, path: str, baud: int) -> bool:
        return self._write(offset, path, baud, patch_header=(offset == 0)) \
            and self._verify(offset, path, baud)

    # -- app strategies ----------------------------------------------------

    def _flash_fixed(self, fixed: List[Region], baud: int) -> Optional[int]:
        """Write the small fixed regions (bootloader/partitions/boot_app0), each
        with a few power-cycle retries. Returns the offset of the first region
        that could NOT be written+verified (a board that can't even take these
        tiny writes is strongly hardware-damaged), or None if all succeeded."""
        for r in fixed:
            for _ in range(3):
                if self._write_verified(r.offset, r.path, baud):
                    break
                self.power_cycle()
            else:
                return r.offset
        return None

    def _app_size(self, path: str) -> int:
        out = self.c.run(f"stat -c %s {path}")[1].strip()
        return int(out) if out.isdigit() else 0

    def _flash_whole(self, app: Region, baud: int) -> bool:
        return self._write_verified(app.offset, app.path, baud)

    def _flash_chunked(self, app: Region, chunk_bytes: int, baud: int,
                       retries: int, emit) -> Tuple[bool, Optional[int]]:
        total = self._app_size(app.path)
        if total <= 0:
            return False, app.offset
        n = math.ceil(total / chunk_bytes)
        for i in range(n):
            offset = app.offset + i * chunk_bytes
            self.c.run(f"dd if={app.path} of={CHUNK_FILE} bs={chunk_bytes} "
                       f"skip={i} count=1 2>/dev/null")
            for attempt in range(1, retries + 1):
                if self._write_verified(offset, CHUNK_FILE, baud):
                    emit(FlashProgress("chunk_ok", detail=f"{i + 1}/{n} @0x{offset:x}"))
                    break
                emit(FlashProgress(
                    "chunk_retry",
                    detail=f"{i + 1}/{n} @0x{offset:x} attempt {attempt}"))
                self.power_cycle()
            else:
                return False, offset
        return True, None

    # -- the ladder --------------------------------------------------------

    def flash(self, fixed: List[Region], app: Region,
              ladder: Optional[List[FlashTier]] = None,
              on_progress: Optional[Callable[[FlashProgress], None]] = None
              ) -> RobustFlashResult:
        """Flash the small *fixed* regions + the big *app* region, climbing the
        escalation *ladder* until every region verifies or the ladder is spent."""
        emit = on_progress or (lambda p: None)
        ladder = ladder or DEFAULT_LADDER
        self.power_cycle()                       # clean cold start
        last_offset = app.offset
        reached_app = False
        for tier in ladder:
            emit(FlashProgress("tier_start", tier=tier.name))
            failed_fixed = self._flash_fixed(fixed, tier.baud)
            if failed_fixed is not None:
                last_offset = failed_fixed
                emit(FlashProgress("tier_fail", tier=tier.name,
                                   detail=f"fixed region @0x{failed_fixed:x}"))
                self.power_cycle()
                continue
            reached_app = True
            if tier.chunk_bytes is None:
                ok = self._flash_whole(app, tier.baud)
                failed = None if ok else app.offset
            else:
                ok, failed = self._flash_chunked(
                    app, tier.chunk_bytes, tier.baud, tier.chunk_retries, emit)
            if ok:
                emit(FlashProgress("tier_ok", tier=tier.name))
                emit(FlashProgress("done", tier=tier.name))
                return RobustFlashResult(True, tier.name)
            last_offset = failed or last_offset
            emit(FlashProgress("tier_fail", tier=tier.name,
                               detail=f"@0x{last_offset:x}"))
            self.power_cycle()
        diagnosis = self._classify(reached_app)
        emit(FlashProgress("done", detail="all tiers failed"))
        return RobustFlashResult(False, None, last_offset, diagnosis)

    # -- failure classification -------------------------------------------

    def _classify(self, reached_app: bool = True) -> str:
        """After the ladder is exhausted, explain the hardware limit from the
        kernel's own signal (over-current) so the operator gets one concrete
        action instead of a guess. Failing on the tiny fixed regions — never even
        reaching the app — is a far stronger damage signal than dropping deep in
        the app write, so say so."""
        oc = self.c.run("dmesg 2>/dev/null | grep -c over-current")[1].strip()
        severe = "" if reached_app else (
            "The board browned out on even the tiny bootloader write — a strong "
            "sign of hardware damage, not just a marginal link. ")
        if oc.isdigit() and int(oc) > 0:
            return (severe + "Repeated USB over-current during flash — the board "
                    "browns out mid-write faster than the ladder can recover. If "
                    "its status LED is stuck white, disconnect the LED's positive "
                    "wire; otherwise use a powered USB hub. The board may be "
                    "hardware-damaged.")
        return (severe + "Flash writes kept failing with no over-current — likely "
                "an intermittent USB data link. Try a known-good short USB-C data "
                "cable or a different port.")
