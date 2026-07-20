"""Bake medic-reachability directly onto a node's SD card. SAFETY FIRST: never
touch the medic's own disk; only edit an inserted card that is really a Pi boot."""

import json

from node_profile import NodeHardware
from provisioning import sd_edit


# ---- medic-disk detection (the untouchable disk) --------------------------

def test_medic_root_disk_strips_partition_suffix():
    assert sd_edit.medic_root_disk(lambda c: "/dev/mmcblk0p2\n") == "mmcblk0"
    assert sd_edit.medic_root_disk(lambda c: "/dev/nvme0n1p2\n") == "nvme0n1"
    assert sd_edit.medic_root_disk(lambda c: "/dev/sda2\n") == "sda"


def test_medic_root_disk_none_when_unknown():
    assert sd_edit.medic_root_disk(lambda c: "overlay\n") is None


# ---- finding the inserted SD's boot partition (excluding the medic) --------

def _lsblk_json(disks):
    return json.dumps({"blockdevices": disks})


def _run(findmnt="/dev/mmcblk0p2\n", lsblk_disks=None):
    disks = lsblk_disks if lsblk_disks is not None else []
    def run(cmd):
        if "findmnt" in cmd:
            return findmnt
        if "lsblk" in cmd:
            return _lsblk_json(disks)
        return ""
    return run


MEDIC = {"name": "mmcblk0", "type": "disk",
         "children": [{"name": "mmcblk0p1", "fstype": "vfat", "path": "/dev/mmcblk0p1"},
                      {"name": "mmcblk0p2", "fstype": "ext4", "path": "/dev/mmcblk0p2"}]}
SD_CARD = {"name": "sda", "type": "disk",
           "children": [{"name": "sda1", "fstype": "vfat", "path": "/dev/sda1"},
                        {"name": "sda2", "fstype": "ext4", "path": "/dev/sda2"}]}


def test_finds_inserted_sd_boot_partition_not_the_medic():
    run = _run(lsblk_disks=[MEDIC, SD_CARD])
    part, disk = sd_edit.find_pi_boot_partition(run)
    assert part == "/dev/sda1" and disk == "sda"        # the SD, NOT mmcblk0p1


def test_refuses_when_only_the_medic_disk_is_present():
    # No inserted card -> must NOT offer the medic's own vfat boot partition.
    run = _run(lsblk_disks=[MEDIC])
    assert sd_edit.find_pi_boot_partition(run) == (None, None)


def test_refuses_when_medic_disk_undetermined():
    run = _run(findmnt="weird\n", lsblk_disks=[MEDIC, SD_CARD])
    assert sd_edit.find_pi_boot_partition(run) == (None, None)   # fail-safe


# ---- boot-file transforms per board class ---------------------------------

def test_apply_uart_transforms_for_3aplus():
    cfg, cmd, changed = sd_edit.apply_reachability_text(
        "dtparam=audio=on\n", "console=tty1 rootwait\n", NodeHardware.PI_3A_PLUS)
    assert changed
    assert "enable_uart=1" in cfg and "console=serial0" in cmd


def test_apply_gadget_transforms_for_pi5():
    cfg, cmd, changed = sd_edit.apply_reachability_text(
        "dtparam=audio=on\n", "rootwait\n", NodeHardware.PI_5)
    assert changed
    assert "dtoverlay=dwc2" in cfg and "modules-load=dwc2,g_ether" in cmd


# ---- full SD-edit orchestration (mocked mount/write) ----------------------

def _codes(rules):
    """Fake run_code: match command substrings to (code, out); default (0, '')."""
    def run_code(_run, cmd):
        for sub, res in rules:
            if sub in cmd:
                return res
        return (0, "")
    return run_code


def test_bake_via_sd_writes_uart_to_the_card():
    reads = {"config.txt": "dtparam=audio=on\n", "cmdline.txt": "console=tty1 rootwait\n"}
    def run(cmd):
        if "findmnt" in cmd:
            return "/dev/mmcblk0p2\n"
        if "lsblk" in cmd:
            return _lsblk_json([MEDIC, SD_CARD])
        if "cat" in cmd and "config.txt" in cmd:
            return reads["config.txt"]
        if "cat" in cmd and "cmdline.txt" in cmd:
            return reads["cmdline.txt"]
        return ""
    writes = []
    def run_code(_run, cmd):
        if "tee" in cmd:
            writes.append(cmd)
        return (0, "")
    res = sd_edit.bake_reachability_via_sd(
        NodeHardware.PI_3A_PLUS, run=run, run_code=run_code)
    assert res.ok and res.changed and res.device == "sda"
    assert any("config.txt" in w and "enable_uart=1" in w for w in writes)
    assert any("cmdline.txt" in w and "console=serial0" in w for w in writes)


def test_bake_via_sd_refuses_non_pi_boot_partition():
    run = _run(lsblk_disks=[MEDIC, SD_CARD])
    # mount ok, but the config.txt/cmdline.txt existence check fails -> refuse
    run_code = _codes([("test -f", (1, "")), ("umount", (0, ""))])
    res = sd_edit.bake_reachability_via_sd(
        NodeHardware.PI_3A_PLUS, run=run, run_code=run_code)
    assert res.ok is False and "not a Pi boot partition" in res.message


def test_bake_via_sd_no_card():
    run = _run(lsblk_disks=[MEDIC])              # only the medic's disk
    res = sd_edit.bake_reachability_via_sd(NodeHardware.PI_3A_PLUS, run=run)
    assert res.ok is False and "No inserted SD card" in res.message
