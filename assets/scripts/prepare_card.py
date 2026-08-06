#!/usr/bin/env python3
"""Prepare a freshly-imaged Raspberry Pi card. RUNS AS ROOT.

Installed to /usr/local/lib/nodemedic/prepare-card, owned by root, and granted
a single narrow NOPASSWD entry. Everything the birth flow needs to do to a card
happens HERE, in one root operation, instead of a dozen separate sudo calls.

WHY THIS EXISTS
---------------
The medic's sudo policy is a scoped allow-list — specific mounts, `tee` to named
files, `dd of=/dev/*`. Card preparation had grown past it: baking the USB-gadget
link, writing cloud-init seeds and activating the shipped-but-disabled account
all ran as `sudo python3 -` with a script piped in. Permitting THAT would mean
granting NOPASSWD for python3, which is unrestricted root by another name and
would quietly dismantle the allow-list. So: one root-owned program, one entry.

WHY IT DUPLICATES LOGIC FROM THE REPO
-------------------------------------
It deliberately imports NOTHING from ~/reticulum-tool. That tree is writable by
the `nodemedic` user, and a root program importing user-writable code is a
privilege escalation with extra steps — anyone who could edit the repo could
execute as root. The duplication IS the security boundary. Keep this file
self-contained and stdlib-only.

THE TRUST BOUNDARY IS HERE
--------------------------
Because sudo must allow arbitrary arguments, this script cannot trust its
caller. It validates the target device itself: refuses the medic's own system
disk, refuses anything that is not a present removable USB disk. A caller that
asks for /dev/mmcblk0 gets refused, not obeyed.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time

BOOT_MNT = "/tmp/nm-prep-boot"
ROOT_MNT = "/tmp/nm-prep-root"

GADGET_MODULES = "modules-load=dwc2,g_ether"
DWC2_OVERLAY = "dtoverlay=dwc2"

# Boards whose OTG port cannot work out on its own that it should be a DEVICE.
# Bare dwc2 leaves dr_mode=otg, which means "read the ID pin". A Pi Zero's
# micro-USB and a Pi 4/5's USB-C both have one. A Pi 3A+ exposes its OTG
# controller on a full-size USB-A socket, which has NO ID pin, so otg resolves
# to host every time. Watched live 2026-08-07: 3A+ powered and booting off a
# freshly written card, in a self-powered hub, rail steady at 4.92 V, and the
# medic logged not one USB event. dr_mode=peripheral overrides the pin.
# Kept in step with provisioning.gadget.DR_MODE_BY_BOARD (a test pins them
# together — this file is a standalone root helper and cannot import it).
DR_MODE_BY_BOARD = {
    "pi_3a_plus": "peripheral",
}


def dwc2_overlay_for(pi_key=""):
    mode = DR_MODE_BY_BOARD.get((pi_key or "").strip())
    return "%s,dr_mode=%s" % (DWC2_OVERLAY, mode) if mode else DWC2_OVERLAY
GADGET_UNIT_PATH = "/etc/systemd/system/nodemedic-gadget-ip.service"
WANTS_DIR = "/etc/systemd/system/multi-user.target.wants"
GADGET_USB_IP = "10.55.0.1"
USB_PREFIX = 29

GADGET_UNIT = f"""\
[Unit]
Description=USB gadget link static IP (Node Medic provisioning)
After=network-pre.target
Wants=network-pre.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStartPre=/bin/sh -c 'for i in $(seq 1 20); do ip link show usb0 && exit 0; sleep 0.5; done; exit 0'
ExecStart=/sbin/ip addr replace {GADGET_USB_IP}/{USB_PREFIX} dev usb0
ExecStart=/sbin/ip link set usb0 up

[Install]
WantedBy=multi-user.target
"""

#: NetworkManager must not claim usb0. It manages every ethernet device it sees,
#: and claiming one FLUSHES the addresses already on it — so the static address
#: above is set and then silently wiped, leaving a link that never answers and a
#: card with nothing wrong-looking on it. Observed live 2026-08-06.
#: Duplicated from provisioning.gadget on purpose: this file imports nothing
#: from the user-writable repo (see the module docstring).
NM_CONF_DIR = "/etc/NetworkManager/conf.d"
NM_UNMANAGED_PATH = f"{NM_CONF_DIR}/99-nodemedic-usb0.conf"
NM_UNMANAGED_CONF = """\
# Node Medic provisioning link. usb0 carries a fixed point-to-point address set
# by nodemedic-gadget-ip.service; NetworkManager must not claim it, because
# claiming it flushes that address and the medic then waits for a link that
# will never answer.
[keyfile]
unmanaged-devices=interface-name:usb0
"""

PI_USER_GROUPS = ["adm", "dialout", "cdrom", "sudo", "audio", "video",
                  "plugdev", "games", "users", "input", "netdev", "gpio",
                  "i2c", "spi"]


def run(argv, **kw):
    return subprocess.run(argv, capture_output=True, text=True, **kw)


def fail(msg, code=2):
    print(f"PREPARE_FAIL: {msg}")
    sys.exit(code)


def say(msg):
    print(f"PREPARE: {msg}", flush=True)


# --------------------------------------------------------------------------- #
# Target validation — the whole reason this runs as root and not the caller
# --------------------------------------------------------------------------- #

def system_disk() -> str:
    p = run(["findmnt", "-no", "SOURCE", "/"])
    src = (p.stdout or "").strip()
    if not src:
        return ""
    q = run(["lsblk", "-no", "PKNAME", src])
    pk = [l.strip() for l in (q.stdout or "").splitlines() if l.strip()]
    return pk[-1] if pk else os.path.basename(src).rstrip("0123456789p")


def assert_safe_target(device: str) -> None:
    """Refuse anything that isn't a present, removable USB disk."""
    name = os.path.basename((device or "").rstrip("/"))
    if not name:
        fail("no device given")
    sysd = system_disk()
    if name == sysd:
        fail(f"{device} is Node Medic's own system disk")
    p = run(["lsblk", "-dno", "NAME,TYPE,TRAN,RM", f"/dev/{name}"])
    if p.returncode != 0:
        fail(f"{device} is not a present block device")
    parts = (p.stdout or "").split()
    if len(parts) < 4:
        fail(f"could not read {device}")
    _n, dtype, tran, rm = parts[0], parts[1], parts[2], parts[3]
    if dtype != "disk":
        fail(f"{device} is a {dtype}, not a whole disk")
    if tran != "usb" and rm != "1":
        fail(f"{device} is neither USB nor removable — refusing")


def partition(device: str, n: int) -> str:
    return f"{device}p{n}" if device[-1].isdigit() else f"{device}{n}"


# --------------------------------------------------------------------------- #
# Boot partition: firstboot config + the USB-gadget link
# --------------------------------------------------------------------------- #

def cmdline_with_gadget(text: str) -> str:
    trailing = "\n" if text.endswith("\n") else ""
    tokens = text.split()
    if GADGET_MODULES in tokens:
        return text
    for i, tok in enumerate(tokens):
        if tok.startswith("modules-load="):
            have = tok[len("modules-load="):].split(",")
            for mod in ("dwc2", "g_ether"):
                if mod not in have:
                    have.append(mod)
            tokens[i] = "modules-load=" + ",".join(have)
            return " ".join(tokens) + trailing
    out, done = [], False
    for tok in tokens:
        out.append(tok)
        if tok == "rootwait" and not done:
            out.append(GADGET_MODULES)
            done = True
    if not done:
        out.append(GADGET_MODULES)
    return " ".join(out) + trailing


def config_txt_with_gadget(text: str, pi_key: str = "") -> str:
    overlay = dwc2_overlay_for(pi_key)
    for line in text.splitlines():
        s = line.strip()
        if s == DWC2_OVERLAY or s.startswith(DWC2_OVERLAY + ","):
            return text
    sep = "" if text.endswith("\n") or text == "" else "\n"
    # [all] re-opened deliberately: config.txt is sectioned, and an append that
    # lands under [cm5]/[pi5] silently applies to nothing on other boards.
    return (f"{text}{sep}\n# USB gadget ethernet (Node Medic provisioning link)\n"
            f"[all]\n{overlay}\n")


def write_boot(mnt: str, cfg: dict) -> None:
    for name in ("config.txt", "cmdline.txt"):
        if not os.path.isfile(os.path.join(mnt, name)):
            fail(f"{mnt} has no {name} — not a Pi boot partition")

    if cfg.get("custom_toml"):
        _write(os.path.join(mnt, "custom.toml"), cfg["custom_toml"])
    if cfg.get("user_data"):
        _write(os.path.join(mnt, "user-data"), cfg["user_data"])
    if cfg.get("meta_data"):
        _write(os.path.join(mnt, "meta-data"), cfg["meta_data"])
    if cfg.get("network_config"):
        _write(os.path.join(mnt, "network-config"), cfg["network_config"])
    open(os.path.join(mnt, "ssh"), "a").close()      # enable sshd on first boot
    say("wrote first-boot config")

    if cfg.get("cable_link", True):
        # NOT fatal. A card without the cable link still boots and joins WiFi,
        # so a failure here degrades to "birth this one over WiFi" rather than
        # throwing away a good card. Account activation below IS fatal, because
        # without it the Pi boots and refuses every login.
        try:
            pi_key = cfg.get("pi_key", "")
            for name, fn in (("cmdline.txt", cmdline_with_gadget),
                             ("config.txt", config_txt_with_gadget)):
                q = os.path.join(mnt, name)
                before = open(q).read()
                after = (fn(before, pi_key) if name == "config.txt"
                         else fn(before))
                if after != before:
                    _write(q, after)
            say("baked the USB-cable link")
        except Exception as exc:                        # noqa: BLE001
            print(f"PREPARE_WARN: cable link not baked ({exc}) — "
                  f"this card can still be birthed over WiFi")


def _write(path: str, text: str) -> None:
    with open(path, "w") as fh:
        fh.write(text)


# --------------------------------------------------------------------------- #
# Root partition: the gadget service + turning the shipped account back on
# --------------------------------------------------------------------------- #

def write_rootfs(mnt: str, cfg: dict) -> None:
    if not os.path.isdir(os.path.join(mnt, "etc")):
        fail(f"{mnt} has no /etc — not a Pi root partition")

    if cfg.get("cable_link", True):
        try:
            _write(os.path.join(mnt, GADGET_UNIT_PATH.lstrip("/")), GADGET_UNIT)
            wants = os.path.join(mnt, WANTS_DIR.lstrip("/"))
            os.makedirs(wants, exist_ok=True)
            link = os.path.join(wants, "nodemedic-gadget-ip.service")
            if os.path.islink(link) or os.path.exists(link):
                os.remove(link)
            # a cold filesystem has no systemd to `enable`, so make the
            # wants-link by hand — exactly what enable would have done
            os.symlink(GADGET_UNIT_PATH, link)
            say("installed the gadget link service")
        except Exception as exc:                        # noqa: BLE001
            print(f"PREPARE_WARN: gadget service not installed ({exc})")

        # Without this the service above is pointless: NetworkManager claims
        # usb0 the moment it appears and flushes the address the unit just set.
        try:
            os.makedirs(os.path.join(mnt, NM_CONF_DIR.lstrip("/")), exist_ok=True)
            _write(os.path.join(mnt, NM_UNMANAGED_PATH.lstrip("/")),
                   NM_UNMANAGED_CONF)
            say("told NetworkManager to leave the gadget link alone")
        except Exception as exc:                        # noqa: BLE001
            print(f"PREPARE_WARN: NetworkManager may claim usb0 and wipe the "
                  f"link address ({exc})")

    user = cfg.get("user") or ""
    if not user:
        return
    activate_account(mnt, user, cfg.get("pwhash", ""), cfg.get("keys") or [])


def activate_account(mnt: str, user: str, pwhash: str, keys: list) -> None:
    """Turn the image's shipped-but-DISABLED account into a usable login.

    Raspberry Pi OS ships `pi` with a nologin shell and a locked password; the
    first-boot mechanisms that would enable it are inert on this image. Without
    this the Pi boots and refuses every login.
    """
    pw_file = os.path.join(mnt, "etc/passwd")
    lines = open(pw_file).read().splitlines(True)
    uid = gid = None
    found = False
    for i, line in enumerate(lines):
        parts = line.rstrip("\n").split(":")
        if len(parts) < 7 or parts[0] != user:
            continue
        found, uid, gid = True, parts[2], parts[3]
        if parts[6] != "/bin/bash":
            parts[6] = "/bin/bash"
            lines[i] = ":".join(parts) + "\n"
        break
    if not found:
        fail(f"no '{user}' account in the image")
    _write(pw_file, "".join(lines))

    if pwhash:
        sh_file = os.path.join(mnt, "etc/shadow")
        lines = open(sh_file).read().splitlines(True)
        days = str(int(time.time() // 86400))
        seen = False
        for i, line in enumerate(lines):
            parts = line.rstrip("\n").split(":")
            if not parts or parts[0] != user:
                continue
            seen = True
            parts[1], parts[2] = pwhash, days
            lines[i] = ":".join(parts) + "\n"
            break
        if not seen:
            lines.append(f"{user}:{pwhash}:{days}:0:99999:7:::\n")
        _write(sh_file, "".join(lines))
        os.chmod(sh_file, 0o640)

    if keys:
        home = os.path.join(mnt, "home", user)
        ssh_dir = os.path.join(home, ".ssh")
        os.makedirs(ssh_dir, exist_ok=True)
        ak = os.path.join(ssh_dir, "authorized_keys")
        existing = open(ak).read().splitlines() if os.path.exists(ak) else []
        merged = list(existing)
        for k in keys:
            if k.strip() and k.strip() not in [e.strip() for e in merged]:
                merged.append(k.strip())
        _write(ak, "\n".join(merged) + "\n")
        os.chmod(ssh_dir, 0o700)
        os.chmod(ak, 0o600)
        if uid is not None:
            # sshd ignores an authorized_keys the account does not own
            for p in (home, ssh_dir, ak):
                os.chown(p, int(uid), int(gid))

    # BuildWorkflow runs every privileged step with `sudo -n`; Pi OS writes this
    # during the first-boot setup we are replacing.
    sd = os.path.join(mnt, "etc/sudoers.d")
    if os.path.isdir(sd):
        f = os.path.join(sd, f"010_{user}-nopasswd")
        _write(f, f"{user} ALL=(ALL) NOPASSWD: ALL\n")
        os.chmod(f, 0o440)          # sudo refuses to run at all if group-writable
        os.chown(f, 0, 0)
    say(f"activated the '{user}' account")


# --------------------------------------------------------------------------- #

def mount(part: str, mnt: str) -> None:
    os.makedirs(mnt, exist_ok=True)
    p = run(["mount", part, mnt])
    if p.returncode != 0:
        fail(f"could not mount {part}: {(p.stderr or p.stdout).strip()[:120]}")


def umount(mnt: str) -> None:
    run(["sync"])
    run(["umount", mnt])


def main() -> int:
    ap = argparse.ArgumentParser(description="Prepare an imaged Pi card (root)")
    ap.add_argument("--device", required=True)
    ap.add_argument("--config", required=True,
                    help="path to a JSON file with the card configuration")
    a = ap.parse_args()

    if os.geteuid() != 0:
        fail("must run as root")

    assert_safe_target(a.device)
    try:
        cfg = json.load(open(a.config))
    except Exception as exc:                            # noqa: BLE001
        fail(f"unreadable config: {exc}")

    run(["partprobe", a.device])
    time.sleep(1)

    boot, root = partition(a.device, 1), partition(a.device, 2)
    mount(boot, BOOT_MNT)
    try:
        write_boot(BOOT_MNT, cfg)
    finally:
        umount(BOOT_MNT)

    mount(root, ROOT_MNT)
    try:
        write_rootfs(ROOT_MNT, cfg)
    finally:
        umount(ROOT_MNT)

    print("PREPARE_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
