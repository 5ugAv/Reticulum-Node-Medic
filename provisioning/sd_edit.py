"""Bake medic-reachability into a node's SD card directly — the most offline path.

The most robust way to make a node reachable is to edit its SD card BEFORE it
even boots: mount the boot partition, apply the same config.txt / cmdline.txt
transforms the on-device path uses, done. No link to the node, no network, no
running node at all — prep the card (or retrofit an existing node's card) on the
bench.

SAFETY IS THE WHOLE GAME HERE. Node Medic is itself a Pi running from its OWN
disk. Editing the wrong block device would brick the medic. So this module:
  * finds the medic's OWN system disk and treats it as untouchable;
  * only ever considers OTHER disks (the inserted USB SD reader);
  * refuses to write unless the mounted partition really is a Pi boot partition
    (has BOTH config.txt and cmdline.txt).
Detection/orchestration go through a ``run`` callable so the logic is unit-tested
with mocked ``lsblk`` / ``findmnt`` output; the real mounts need root + hardware.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from typing import Callable, List, Optional, Tuple

from node_profile import NodeHardware
from provisioning.reachability import link_kind
from provisioning.uart_console import config_txt_with_uart, cmdline_with_uart
from provisioning.gadget import config_txt_with_gadget, cmdline_with_gadget

#: Where we mount a candidate boot partition to inspect/edit it.
SD_MOUNT = "/tmp/nm_sd_boot"

Runner = Callable[[str], str]


def _default_run(command: str) -> str:
    try:
        return subprocess.run(["bash", "-lc", command], capture_output=True,
                              text=True, timeout=60).stdout
    except Exception:
        return ""


def _run_code(run_raw, command: str):
    """(exit_code, stdout) — for the write/mount steps where failure matters."""
    proc = subprocess.run(["bash", "-lc", command], capture_output=True,
                          text=True, timeout=60)
    return proc.returncode, proc.stdout + proc.stderr


def medic_root_disk(run: Runner = _default_run) -> Optional[str]:
    """The medic's OWN system disk name (e.g. ``mmcblk0`` / ``nvme0n1`` / ``sda``)
    — the disk backing ``/``. This disk is UNTOUCHABLE. None if undetermined
    (in which case callers must refuse to write anything, fail-safe)."""
    src = (run("findmnt -n -o SOURCE /") or "").strip()   # e.g. /dev/mmcblk0p2
    if not src.startswith("/dev/"):
        return None
    name = src[len("/dev/"):]
    # strip a partition suffix: mmcblk0p2 -> mmcblk0, nvme0n1p2 -> nvme0n1, sda2 -> sda
    if "mmcblk" in name or "nvme" in name:
        return name.split("p")[0]
    return name.rstrip("0123456789")


def _lsblk(run: Runner) -> List[dict]:
    out = run("lsblk -J -O 2>/dev/null || lsblk -J -o NAME,TYPE,RM,FSTYPE,PATH,SIZE,LABEL")
    try:
        return json.loads(out).get("blockdevices", [])
    except Exception:
        return []


def find_pi_boot_partition(run: Runner = _default_run
                           ) -> Tuple[Optional[str], Optional[str]]:
    """Return ``(partition_path, disk_name)`` for the inserted SD's Pi boot
    partition — a FAT partition on a disk that is NOT the medic's own. Returns
    ``(None, None)`` if there's no such card (or the medic disk can't be
    determined — fail-safe). Picks the first FAT partition on the first non-medic
    disk (the boot partition is FAT; the medic-disk exclusion is the safety net)."""
    root = medic_root_disk(run)
    if root is None:
        return (None, None)                       # can't be sure what's ours: refuse
    for disk in _lsblk(run):
        if disk.get("type") != "disk" or disk.get("name") == root:
            continue                              # skip non-disks and the medic's own
        for child in disk.get("children", []) or []:
            fst = (child.get("fstype") or "").lower()
            if fst in ("vfat", "fat", "fat32", "msdos"):
                path = child.get("path") or f"/dev/{child.get('name')}"
                return (path, disk.get("name"))
    return (None, None)


def apply_reachability_text(config_text: str, cmdline_text: str,
                            hardware: NodeHardware) -> Tuple[str, str, bool]:
    """Apply the board-class boot-file transforms. Returns
    ``(new_config, new_cmdline, changed)``. Pure — the same transforms the
    on-device path uses. (The gadget static-IP service lives on the rootfs, not
    the boot partition — a full gadget-via-SD also needs the ext4 side; UART is
    complete from the boot partition alone.)"""
    kind = link_kind(hardware)
    if kind == "uart":
        new_cfg = config_txt_with_uart(config_text)
        new_cmd = cmdline_with_uart(cmdline_text)
    elif kind == "gadget":
        new_cfg = config_txt_with_gadget(config_text)
        new_cmd = cmdline_with_gadget(cmdline_text)
    else:
        return (config_text, cmdline_text, False)
    return (new_cfg, new_cmd, new_cfg != config_text or new_cmd != cmdline_text)


@dataclass
class SdEditResult:
    ok: bool
    message: str
    changed: bool = False
    device: Optional[str] = None


def bake_reachability_via_sd(hardware: NodeHardware,
                             run: Runner = _default_run,
                             run_code=_run_code,
                             mount: str = SD_MOUNT) -> SdEditResult:
    """Find the inserted node SD, mount its boot partition, and bake the wired
    link for *hardware* into config.txt / cmdline.txt. Never touches the medic's
    own disk. Requires root for mount/write. Idempotent."""
    kind = link_kind(hardware)
    if kind is None:
        return SdEditResult(False, f"No wired-link profile for "
                            f"{getattr(hardware, 'value', hardware)}.")

    part, disk = find_pi_boot_partition(run)
    if not part:
        return SdEditResult(
            False, "No inserted SD card with a Pi boot partition found (and the "
                   "medic's own disk is never touched). Insert the node's card "
                   "via a USB reader and retry.")

    # Safety belt: the chosen disk must not be the medic's own.
    if disk == medic_root_disk(run):
        return SdEditResult(False, "Refusing to edit the medic's own disk.")

    code, out = run_code(run, f"mkdir -p {mount} && sudo -n mount {part} {mount}")
    if code != 0:
        return SdEditResult(False, f"Could not mount {part}: {out[-160:]}",
                            device=disk)
    try:
        # Confirm it's really a Pi boot partition BEFORE writing anything.
        code, _ = run_code(run, f"test -f {mount}/config.txt && test -f {mount}/cmdline.txt")
        if code != 0:
            return SdEditResult(
                False, f"{part} mounted but has no config.txt/cmdline.txt — not a "
                       "Pi boot partition; refusing to edit.", device=disk)

        cfg = run(f"cat {mount}/config.txt")
        cmd = run(f"cat {mount}/cmdline.txt")
        new_cfg, new_cmd, changed = apply_reachability_text(cfg, cmd, hardware)
        if not changed:
            return SdEditResult(True, f"{kind} link already baked into the SD card.",
                                False, disk)

        for path, content in ((f"{mount}/config.txt", new_cfg),
                              (f"{mount}/cmdline.txt", new_cmd)):
            code, out = run_code(run, _tee(path, content))
            if code != 0:
                return SdEditResult(False, f"Write failed on {path}: {out[-160:]}",
                                    True, disk)
        run_code(run, "sync")
        return SdEditResult(
            True, f"Baked the {kind} wired link into the node's SD card ({disk}). "
                  "It will be medic-reachable on first boot.", True, disk)
    finally:
        run_code(run, f"sudo -n umount {mount}")


def _tee(path: str, content: str) -> str:
    marker = "NM_SD_EOF"
    return f"sudo -n tee {path} > /dev/null <<'{marker}'\n{content}\n{marker}"
