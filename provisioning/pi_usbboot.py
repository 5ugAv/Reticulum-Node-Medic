"""Use the Pi itself as the card reader — no USB SD reader in the process.

The operator asked whether the card reader can go away entirely (2026-08-01).
It can, for the case that matters: a **new or blank card**.

Every Pi SoC has a ROM fallback. If it can't find a bootable card it enumerates
as a USB *device* and waits to be told what to run. ``rpiboot`` pushes it a tiny
mass-storage payload, and from that moment the Pi presents its own SD slot to
Node Medic as an ordinary removable USB disk — which is exactly the kind of
target ``pi_imager`` already knows how to write safely. So the Pi becomes its
own reader, over the same single cable that later carries the provisioning link.

The catch is the same fallback: a card that ALREADY boots wins, and the Pi will
just start Raspberry Pi OS instead of waiting. That is not a defect — it makes
the flow self-selecting, and it is why this module reports a *state* rather than
a yes/no:

    blank card      -> BOOTROM      -> run rpiboot -> CARD_READER -> image it
    imaged card     -> GADGET       -> already a node -> provision it over the link
    nothing yet     -> ABSENT       -> ask the operator to plug in

One cable, one port, two outcomes, and the medic can tell which by looking at
what enumerated. Classification is pure (parses ``lsusb``) so it is fully
unit-testable; only ``run_rpiboot`` touches hardware.

NOT YET PROVEN ON A ZERO 2 W. The mechanism and the payload are confirmed
present on the medic (rpiboot 20260603, ``/usr/share/rpiboot/msd``), but no Pi
has been booted this way here — see ``BENCH_CHECK``.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from typing import Callable, List, Optional

#: Broadcom USB IDs a Pi presents while sitting in ROM device-boot mode, waiting
#: for rpiboot. Keyed by SoC so the UI can name the board it found.
BOOTROM_IDS = {
    "0a5c:2763": "BCM2835 (Pi 1 / Pi Zero / CM1)",
    "0a5c:2764": "BCM2836/2837 (Pi 2 / Pi 3 / Pi Zero 2 W / CM3)",
    "0a5c:2711": "BCM2711 (Pi 4 / CM4)",
    "0a5c:2712": "BCM2712 (Pi 5 / CM5)",
}

#: The Linux USB-gadget ethernet IDs an ALREADY-provisioned node shows up as —
#: i.e. a card we imaged, now booting normally with the cable link up.
GADGET_IDS = {
    "0525:a4a2": "Linux g_ether / CDC gadget",
    "0525:a4a1": "Linux gadget",
    "1d6b:0104": "Linux multifunction gadget",
}

#: rpiboot's default (no ``-d``) payload is the 32-bit BCM283x mass-storage one
#: at /usr/share/rpiboot/msd — the right one for a Zero 2 W. BCM2711/2712 have
#: their own 64-bit payload, selected explicitly.
MSD_PAYLOAD_64 = "mass-storage-gadget64"

#: What still has to be shown on real hardware before this path is trusted.
BENCH_CHECK = (
    "Put a BLANK card in the Pi, plug the Pi's DATA port into Node Medic, and "
    "run `lsusb`. A line with 0a5c:2764 means the Pi is in boot-ROM mode and "
    "this path works. If Raspberry Pi OS boots instead, the card was already "
    "bootable — that card needs the reader, or blank its first megabyte first."
)

# --------------------------------------------------------------------------- #

ABSENT = "absent"            # no Pi seen on USB
BOOTROM = "bootrom"          # in device-boot mode, waiting for rpiboot
CARD_READER = "card_reader"  # rpiboot done: its SD slot is a disk on the medic
GADGET = "gadget"            # already imaged and booting as a node


@dataclass
class PiUsbState:
    state: str
    detail: str = ""
    usb_id: str = ""

    @property
    def can_image_without_a_reader(self) -> bool:
        return self.state in (BOOTROM, CARD_READER)

    @property
    def needs_rpiboot(self) -> bool:
        return self.state == BOOTROM


_ID_RE = re.compile(r"ID\s+([0-9a-f]{4}:[0-9a-f]{4})", re.I)


def classify(lsusb_output: str, disk_appeared: bool = False) -> PiUsbState:
    """What is plugged in, judged from ``lsusb``.

    *disk_appeared* is the caller's answer to "is there a new removable USB disk
    that wasn't there before?" — after rpiboot the Pi stops advertising a
    Broadcom boot ID and simply becomes a disk, so the ID alone can't see it.
    """
    ids = [m.lower() for m in _ID_RE.findall(lsusb_output or "")]
    for usb_id in ids:
        if usb_id in BOOTROM_IDS:
            return PiUsbState(BOOTROM, BOOTROM_IDS[usb_id], usb_id)
    if disk_appeared:
        return PiUsbState(CARD_READER, "The Pi is presenting its SD card.", "")
    for usb_id in ids:
        if usb_id in GADGET_IDS:
            return PiUsbState(GADGET, GADGET_IDS[usb_id], usb_id)
    return PiUsbState(ABSENT, "No Pi seen on USB.")


def rpiboot_command(soc_usb_id: str = "") -> List[str]:
    """The command that turns a waiting Pi into a card reader.

    Bare ``rpiboot`` uses the 32-bit ``msd`` payload, which is the correct one
    for BCM283x (a Zero 2 W). BCM2711/2712 need their own 64-bit payload, so
    those are selected explicitly rather than left to the default.
    """
    if soc_usb_id.lower() in ("0a5c:2711", "0a5c:2712"):
        return ["sudo", "-n", "rpiboot", "-d", MSD_PAYLOAD_64]
    return ["sudo", "-n", "rpiboot"]


def rpiboot_available(runner: Optional[Callable] = None) -> bool:
    """Is rpiboot installed? It ships the payloads too, so this is the whole
    dependency. Cached as a .deb on the medic for offline use."""
    run = runner or _default_runner
    rc, _ = run(["which", "rpiboot"])
    return rc == 0


def _default_runner(argv: List[str], timeout: int = 60):
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        return (p.returncode, (p.stdout or "") + (p.stderr or ""))
    except Exception as exc:                            # noqa: BLE001
        return (255, str(exc))


def run_rpiboot(soc_usb_id: str = "", runner: Optional[Callable] = None,
                timeout: int = 90) -> tuple:
    """Push the mass-storage payload to a waiting Pi. Returns ``(ok, message)``.

    Touches hardware. Safe to re-run: a Pi that has already been converted is
    no longer in boot-ROM mode, so rpiboot simply finds nothing.
    """
    run = runner or _default_runner
    if not rpiboot_available(run):
        return (False, "rpiboot isn't installed — `sudo apt install rpiboot` "
                       "(the medic carries the .deb for offline use).")
    rc, out = run(rpiboot_command(soc_usb_id), timeout=timeout)
    if rc != 0:
        return (False, f"rpiboot failed: {out.strip()[-160:]}")
    return (True, "The Pi is now presenting its SD card to Node Medic.")


def guidance(state: PiUsbState) -> str:
    """What to tell the operator, in their terms."""
    if state.state == BOOTROM:
        return ("This Pi is waiting with a blank card — Node Medic can write to "
                "it directly, no card reader needed.")
    if state.state == CARD_READER:
        return "The Pi's card is ready to be written."
    if state.state == GADGET:
        return ("This Pi already has an operating system and has come up as a "
                "node. It doesn't need imaging — it's ready to be set up.")
    return ("Plug the Pi into Node Medic using its DATA USB port (marked USB, "
            "not PWR IN), with the blank card already inserted.")
