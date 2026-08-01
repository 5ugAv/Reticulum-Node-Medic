"""Wipe a node's card so it can be born again — over the wire, no card reader.

The awkward asymmetry found birthing HOPE (2026-08-01): a Pi with a BLANK card
falls into USB boot-ROM mode and hands the medic its SD slot, but once the card
is bootable the Pi boots instead, and the medic can never see that card again.
Re-imaging then means pulling the card and finding a reader — the exact step the
whole cable flow exists to avoid.

But the medic can still log in. And a card only boots because of a few kilobytes
at the very front of it: the partition table and the start of the FAT boot
partition. Zero those and the ROM finds nothing bootable on the next power-up,
so the Pi falls back into device mode and offers its card again. From there the
normal path applies — rpiboot, then image it.

The node stays running while this happens (Linux is in RAM and page cache), so
nothing crashes mid-wipe; the card is simply dead on the next boot. That is the
point, and it is why this is called decommission and not repair.

SAFETY. This destroys a disk, so every guard here is about aiming it correctly:

  * it runs on the NODE, over that node's connection — never on the medic;
  * it refuses if the target looks like the medic itself;
  * it requires the caller to name the node it believes it is talking to, and
    checks that against the node's own hostname before writing anything.

The last one matters more than it looks. Every other guard protects against a
wrong DEVICE; this one protects against a wrong MACHINE, which is the mistake
that actually happens when several nodes are on the bench.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

#: Bytes to zero at the front of the card. The MBR is 512 B and the first
#: partition starts at 4 MiB on a Pi image, so 8 MiB destroys the partition
#: table and the head of the boot partition together, with room to spare.
WIPE_MB = 8

#: Hostnames that are never a decommission target. The medic runs the same OS
#: and has the same kind of disk; the only thing telling them apart is identity.
PROTECTED_HOSTNAMES = {"nodemedic", "node-medic", "localhost"}

#: Where a Pi's own card appears when it is running from it.
DEFAULT_NODE_DISK = "/dev/mmcblk0"


@dataclass
class WipeResult:
    ok: bool
    message: str
    device: str = ""
    hostname: str = ""


def is_protected(hostname: str) -> bool:
    """Would wiping this machine destroy the medic itself?"""
    return (hostname or "").strip().lower().split(".")[0] in PROTECTED_HOSTNAMES


def check_target(expected_hostname: str, actual_hostname: str) -> Optional[str]:
    """Reason to refuse, or None to proceed.

    Aiming at the wrong MACHINE is the realistic bench mistake — several nodes,
    one terminal — and no device-level guard can catch it.
    """
    actual = (actual_hostname or "").strip()
    if not actual:
        return "Could not read the node's hostname — refusing to wipe blind."
    if is_protected(actual):
        return (f"'{actual}' is the medic itself (or localhost). Refusing — "
                "this would destroy Node Medic's own system disk.")
    expected = (expected_hostname or "").strip()
    if expected and expected.lower() != actual.lower():
        return (f"Expected to be talking to '{expected}' but this node calls "
                f"itself '{actual}'. Refusing in case the wrong node is "
                f"plugged in.")
    return None


def wipe_commands(device: str = DEFAULT_NODE_DISK, megabytes: int = WIPE_MB
                  ) -> List[str]:
    """Zero the front of *device* so the ROM finds nothing bootable.

    ``sync`` afterwards matters: without it the zeros can sit in the node's
    write cache and never reach the card before it loses power, leaving a card
    that still boots and a wipe that appears to have worked.
    """
    return [
        f"sudo -n dd if=/dev/zero of={device} bs=1M count={int(megabytes)} "
        f"conv=fsync status=none",
        "sudo -n sync",
    ]


def verify_commands(device: str = DEFAULT_NODE_DISK, megabytes: int = WIPE_MB
                   ) -> List[str]:
    """Read the wiped region back and report how many NON-zero bytes remain.

    Deliberately NOT ``od``: od collapses runs of identical lines into a single
    ``*``, so a perfectly zeroed region dumps as zeros-plus-an-asterisk and any
    "is it all zeros?" test on that output fails on its own formatting. That bug
    reported a good wipe as a failure the first time this ran (2026-08-02).
    Stripping NULs and counting what is left has no such trap: 0 means clean.
    """
    return [f"sudo -n dd if={device} bs=1M count={int(megabytes)} status=none "
            f"| tr -d '\\000' | wc -c"]


def looks_wiped(nonzero_count: str) -> bool:
    """True when the read-back region contained no non-zero bytes.

    Takes the output of ``verify_commands`` — a count, not a dump. An empty or
    unparseable answer is NOT treated as success: no evidence is not evidence.
    """
    text = (nonzero_count or "").strip().split()
    if not text:
        return False
    try:
        return int(text[0]) == 0
    except ValueError:
        return False


def decommission(connection, expected_hostname: str = "",
                 device: str = DEFAULT_NODE_DISK,
                 megabytes: int = WIPE_MB) -> WipeResult:
    """Make the node's card unbootable, over *connection* (an SSH connection to
    the NODE). Returns a WipeResult; never raises on a refusal."""
    code, out, err = connection.run("hostname")
    actual = (out or "").strip().splitlines()
    actual = actual[0].strip() if actual else ""
    if code != 0:
        return WipeResult(False, f"Could not reach the node: {err or out}")

    refusal = check_target(expected_hostname, actual)
    if refusal:
        return WipeResult(False, refusal, device, actual)

    for cmd in wipe_commands(device, megabytes):
        code, out, err = connection.run(cmd, timeout=180)
        if code != 0:
            return WipeResult(False, f"Wipe failed on {device}: {err or out}",
                              device, actual)

    code, out, _ = connection.run(verify_commands(device)[0], timeout=120)
    if code == 0 and not looks_wiped(out):
        return WipeResult(False,
                          f"Wrote the wipe but {device} still has data at the "
                          f"front — it would still boot.", device, actual)
    return WipeResult(
        True,
        f"Wiped the first {megabytes} MB of {device} on '{actual}'. It is "
        f"still running from RAM, but the card will not boot again — power it "
        f"off and on and it will offer its card to Node Medic.",
        device, actual)
