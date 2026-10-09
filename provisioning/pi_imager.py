"""On-medic Raspberry Pi SD imaging — write Pi OS to a card in a USB reader and
pre-configure it (hostname, WiFi, SSH, user) so it boots headless and reachable.

The medic has NO native card slot, so imaging targets a USB card reader (a
removable ``/dev/sdX``). The single most important job here is SAFETY: the target
is ALWAYS a removable USB disk, and NEVER the medic's own system disk (the device
holding ``/`` — e.g. ``mmcblk0``). Every write path re-checks this.

Config is written as the modern Raspberry Pi OS ``custom.toml`` firstboot file on
the card's boot partition (Bookworm applies it on first boot). Pure/injectable —
the destructive ``dd`` is behind a runner and never runs in tests.
"""

from __future__ import annotations

import base64
import os
import shlex
import time as _time
from typing import Callable, Dict, List, Optional, Tuple

from provisioning import card_mount

Runner = Callable[[list], Tuple[int, str]]

#: Where a carried, ready-to-flash Pi OS image lives (xz-compressed).
IMAGE_CANDIDATES = [
    os.path.expanduser("~/pi_os_lite.img.xz"),
    os.path.expanduser("~/pi_os.img.xz"),
]


def _run(argv: list) -> Tuple[int, str]:
    import subprocess
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=30)
        return p.returncode, (p.stdout + p.stderr)
    except Exception as e:
        return 1, str(e)


def system_disk(run: Runner = _run) -> str:
    """The base name of the medic's OWN system disk (holds ``/``) — e.g. 'mmcblk0'.
    This device must NEVER be an imaging target."""
    _, src = run(["findmnt", "-no", "SOURCE", "/"])
    src = (src or "").strip()
    if not src:
        return ""
    _, pk = run(["lsblk", "-no", "PKNAME", src])
    pk = (pk or "").strip().splitlines()
    return (pk[-1].strip() if pk and pk[-1].strip()
            else os.path.basename(src).rstrip("0123456789p"))


def list_target_disks(run: Runner = _run) -> List[Dict]:
    """Removable USB disks that are SAFE to image — every entry excludes the
    system disk, loop and zram devices. Each: {name, path, size, model, removable}."""
    sysd = system_disk(run)
    _, out = run(["lsblk", "-dno", "NAME,SIZE,TYPE,TRAN,RM,MODEL"])
    disks = []
    for line in (out or "").splitlines():
        parts = line.split(None, 5)
        if len(parts) < 5:
            continue
        name, size, dtype, tran, rm = parts[0], parts[1], parts[2], parts[3], parts[4]
        model = parts[5] if len(parts) > 5 else ""
        if dtype != "disk":
            continue
        if name == sysd or name.startswith("loop") or name.startswith("zram"):
            continue                                  # never the system/virtual disks
        if size.strip() in ("0B", "0", "0K", "0M"):
            # An EMPTY slot of a multi-card reader enumerates as a 0-byte disk
            # (seen live: a Genesys reader offering /dev/sda 0B beside the real
            # card at /dev/sdb). Offering it as a target invites writing an OS
            # to a slot with no card in it.
            continue
        if tran == "usb" or rm == "1":                # removable / USB only
            disks.append({"name": name, "path": f"/dev/{name}", "size": size,
                          "model": model.strip(), "removable": True})
    return disks


def disk_serial(device_path: str, run: Runner = _run) -> str:
    """The USB serial of one disk, or "" if it can't be read.

    Deliberately a SEPARATE call rather than another column on
    ``list_target_disks``. That function's lsblk parse is positional, MODEL is
    last because it contains spaces, and it feeds ``is_safe_target`` — the guard
    that stops the medic writing an OS over its own system disk. Widening its
    column list to carry a serial would shift every field for the sake of a
    nice-to-have, so it stays exactly as it is.
    """
    code, out = run(["lsblk", "-dno", "SERIAL", device_path])
    return (out or "").strip() if code == 0 else ""


def card_status(run: Runner = _run) -> Dict:
    """What is in the medic's card reader, right now, and may we act on it?

    Returns {state, path, label, detail} where state is one of:
      "none"    — nothing to write to yet
      "one"     — exactly one removable card; ``path`` is it
      "several" — more than one; the operator must decide, we must NOT guess

    WHY THIS EXISTS
    The operator asked for the medic to notice the card by itself instead of
    waiting on a button (2026-08-06). Noticing is safe and welcome. ACTING
    automatically is not: writing a card is destructive and irreversible, and
    on this very bench a card that looked blank turned out to hold the previous
    night's evidence — caught only because someone checked before writing.

    So this reports, and the write stays behind a deliberate press.

    "several" is not an edge case to smooth over. A medic that silently picks
    one of two cards will eventually pick the wrong one, and the operator will
    have no idea it made a choice at all.

    Pure copy + data (no Kivy), same as ``next_steps_after_imaging``.
    """
    disks = list_target_disks(run)
    if not disks:
        return {"state": "none", "path": "", "label": "",
                "detail": "No card yet — put one in your card reader and plug the reader into a USB socket."}
    if len(disks) > 1:
        names = ", ".join(f"{d['size']} {d['model']}".strip() for d in disks)
        return {"state": "several", "path": "", "label": names,
                "detail": ("More than one card is plugged in — take out the ones "
                           "you don't want written, so there's no doubt which "
                           "this is about.")}
    d = disks[0]
    label = f"{d['size']} {d['model']}".strip()
    return {"state": "one", "path": d["path"], "label": label,
            "detail": f"Found {label}. Everything on it will be replaced."}


def is_safe_target(device_path: str, run: Runner = _run) -> bool:
    """True only if *device_path* is a currently-present removable USB disk (and
    thus NOT the system disk). The guard every write must pass."""
    name = os.path.basename((device_path or "").rstrip("/"))
    if not name or name == system_disk(run):
        return False
    return any(d["name"] == name for d in list_target_disks(run))


def carried_image(candidates: Optional[List[str]] = None) -> Optional[str]:
    for p in (candidates or IMAGE_CANDIDATES):
        if os.path.exists(p):
            return p
    return None


def password_hash(password: str, run: Runner = _run) -> str:
    """A SHA-512 crypt of *password* (for custom.toml's encrypted user password)."""
    code, out = run(["openssl", "passwd", "-6", password])
    return out.strip() if code == 0 else ""


#: Minimum login-password length accepted at imaging. Deliberately modest — the
#: operator has to type it on a touchscreen and WRITE IT DOWN, and SSH key auth is
#: the primary access path; this password is the human-held recovery credential.
#: Operator's call, 2026-08-09: eight was too long to thumb in on a touchscreen
#: at a bench. Six is the floor, not a recommendation.
#:
#: BE HONEST ABOUT WHAT THIS GUARDS. The medic itself never uses this password —
#: the card bakes its SSH key in, and every later connection is key auth. But a
#: birthed node does NOT get the medic's own SSH hardening: build.py's
#: apply_system_hardening installs Log2Ram, logrotate and a watchdog and touches
#: sshd not at all, so the node keeps Raspberry Pi OS's default
#: `PasswordAuthentication yes` — and the same card puts it on WiFi. Until that
#: is closed, this string is a live network credential, and six characters of it
#: is guessable.
#:
#: The fix is not a longer password, it is turning password auth off once key
#: auth is proven; the length is the operator's convenience either way.
MIN_PASSWORD_LEN = 6


#: The three refusals, as TEMPLATES the screen can translate: tr() needs the
#: exact English text as its key, and the old f-string ("Use at least 8
#: characters.") could never be a catalog key (readiness ledger #207).
PASSWORD_EMPTY = "Enter a login password."
PASSWORD_MISMATCH = "The two passwords don't match — retype them."
PASSWORD_SHORT = "Use at least {n} characters."


def password_problem(pw1: str, pw2: Optional[str] = None,
                     min_len: int = MIN_PASSWORD_LEN) -> Optional[str]:
    """The unformatted template naming what is wrong with *pw1* (one of the
    PASSWORD_* constants, ``{n}`` = *min_len*), or None when it is acceptable.
    The screen does ``tr(template).format(n=MIN_PASSWORD_LEN)``."""
    if not pw1:
        return PASSWORD_EMPTY
    if pw2 is not None and pw1 != pw2:
        return PASSWORD_MISMATCH
    if len(pw1) < min_len:
        return PASSWORD_SHORT
    return None


def validate_new_password(pw1: str, pw2: Optional[str] = None,
                          min_len: int = MIN_PASSWORD_LEN) -> Tuple[bool, str]:
    """Gate for the imaging screen's login-password field. Returns (ok, message);
    *message* is empty on success and a user-facing reason on failure. Pure — no
    Kivy — so the rules are unit-tested without a display. The UI must refuse to
    write the card unless this returns ok.

    Why it matters: a mistyped password becomes an *unknown* password baked into
    the card (exactly how Medic A lost its login), and there is no way to recover
    it short of re-imaging.

    *pw2* is the confirmation field where one exists. The imaging screen answers
    the same risk with a Show/Hide reveal instead of a second field, so it calls
    this with pw2 omitted and only the empty/length rules bite. Pass both when a
    confirm field is in play and the match is checked too."""
    problem = password_problem(pw1, pw2, min_len)
    if problem is None:
        return (True, "")
    return (False, problem.format(n=min_len))


def _toml_escape(s: str) -> str:
    return (s or "").replace("\\", "\\\\").replace('"', '\\"')


def medic_public_key(path: str = "~/.ssh/id_ed25519.pub") -> str:
    """The medic's own SSH public key, so a Pi it images will accept it later.
    Empty string if there is no keypair yet."""
    try:
        with open(os.path.expanduser(path)) as f:
            return f.read().strip()
    except OSError:
        return ""


def build_custom_toml(hostname: str, username: str, password: str,
                      wifi_country: str = "AU", enable_ssh: bool = True,
                      timezone: str = "", pw_hasher: Callable[[str], str] = None,
                      authorized_keys: "Optional[List[str]]" = None) -> str:
    """The Raspberry Pi OS ``custom.toml`` firstboot config (schema config_version=1).
    The user password is stored SHA-512-crypted. Written to the card's boot
    partition.

    NO WI-FI HERE, DELIBERATELY. This file used to carry a ``[wlan]`` block with
    ``password_encrypted = false`` — the home PSK in clear text on a FAT
    partition that mounts on any computer, in a card that lives on a roof and
    may be pulled out by anyone. It bought nothing: the carried image has no
    ``raspberrypi-sys-mods/firstboot`` hook, so the block was never read (proven
    twice on the bench, 2026-08-09), and the Wi-Fi that actually works is a
    NetworkManager connection written straight onto the rootfs by
    ``provisioning.rootfs_wifi`` — 0600, root-owned, on ext4.

    The country code stays: it is not a secret, and the regulatory domain is
    what makes a 5 GHz network joinable at all.
    """
    hashed = (pw_hasher or password_hash)(password) if password else ""
    q = _toml_escape
    lines = ["config_version = 1", "", "[system]", f'hostname = "{q(hostname)}"', ""]
    lines += ["[user]", f'name = "{q(username)}"',
              f'password = "{q(hashed)}"', "password_encrypted = true", ""]
    lines += ["[ssh]", f"enabled = {str(bool(enable_ssh)).lower()}",
              "password_authentication = true"]
    # The medic's own public key goes in at IMAGING time — the propagation
    # BuildWorkflow authenticates by KEY only, so without this the medic could
    # image a Pi and then be unable to log into it (2026-08-01).
    keys = [k.strip() for k in (authorized_keys or []) if k and k.strip()]
    if keys:
        joined = ", ".join(f'"{q(k)}"' for k in keys)
        lines.append(f"authorized_keys = [ {joined} ]")
    lines.append("")
    if wifi_country:
        lines += ["[wlan]", f'country = "{q(wifi_country)}"', ""]
    if timezone:
        lines += ["[locale]", f'timezone = "{q(timezone)}"', ""]
    return "\n".join(lines).rstrip() + "\n"


# --------------------------------------------------------------------------- #
# How big to make a node's root partition (2026-09-06)
#
# MEASURED from the carried image's own ext4 superblock: the rootfs is 2.43 GB
# total with only 0.33 GB FREE, and nothing in this repo ever expands it — no
# resize2fs, no growpart, no parted. (The medic's own card looks expanded only
# because it was written by Raspberry Pi Imager, whose firstboot did it;
# `ds=nocloud;i=rpi-imager-…` is still in its cmdline.)
#
# 0.33 GB is not enough. workflows/node_mode.py turns on the LXMF propagation
# node with `enable_node = yes` and nothing sets `message_storage_limit`, so the
# node inherits LXMF's 500 MB default — a store larger than the disk it has to
# live on, with `autopeer = yes` willing to pull other nodes' messages in to
# fill it.
#
# So a node card must be GROWN. But not to fill the card: leaving a large
# unallocated tail gives the card's controller spare blocks to wear-level
# across, which is the cheapest endurance there is on hardware meant to outlive
# the person who installed it ([[sd-reliability-overlayfs]]).
# --------------------------------------------------------------------------- #

#: What a node actually needs: the 2.1 GB image, ~12 MB of Python (RNS, LXMF,
#: cryptography, pyserial — measured), a bounded LXMF store, logs and room for
#: an apt upgrade. 6 GB is generous for that and still leaves most of a 16 GB
#: card unallocated.
#: Read off the carried image itself (its MBR and ext4 superblock), not
#: guessed: the rootfs starts at sector 1064960 and is 2.43 GB with 0.33 GB
#: free. If the carried image is ever replaced, re-measure these.
IMAGE_BOOT_END_BYTES = 1064960 * 512
IMAGE_ROOTFS_BYTES = 2_430_000_000

NODE_ROOTFS_BYTES = 6 * 1000 ** 3

#: Never hand more than this fraction of the usable space to the filesystem.
#: The remainder is left UNALLOCATED on purpose — it is the controller's spare
#: pool, not wasted space.
MAX_USED_FRACTION = 0.5


def _node_rootfs_bytes(device_path: str, run: Runner = _run) -> int:
    """Bytes to grow a NODE card's rootfs to, or 0 to leave it as written.

    0 when the card cannot be measured, because guessing a partition size on a
    device whose capacity is unknown is how a card gets destroyed.
    """
    total = disk_bytes(device_path, run)
    if not total:
        return 0
    plan = rootfs_plan(total, IMAGE_ROOTFS_BYTES, IMAGE_BOOT_END_BYTES)
    return int(plan["target_bytes"]) if plan.get("grow") else 0


def disk_bytes(device_path: str, run: Runner = _run) -> int:
    """Exact capacity of *device_path* in bytes, or 0 when it cannot be read.

    ``list_target_disks`` reports a human string ("59.5G") for the picker; the
    partition arithmetic needs the real number.
    """
    _code, out = run(["lsblk", "-bdno", "SIZE", device_path])
    try:
        return int((out or "").strip().splitlines()[0])
    except (ValueError, IndexError):
        return 0


def rootfs_plan(card_bytes: int, current_root_bytes: int,
                boot_end_bytes: int,
                want_bytes: int = NODE_ROOTFS_BYTES,
                max_used_fraction: float = MAX_USED_FRACTION) -> Dict:
    """How big to make the root partition, and why. Pure — no device touched.

    Returns ``{target_bytes, spare_bytes, grow, reason, warning}``.

    THREE RULES, in order of how badly they bite:

    1. NEVER SHRINK. Shrinking a filesystem below its data destroys it, and a
       card handed back smaller than the image on it will not boot. If the
       arithmetic ever asks for less than what is already there, the plan is to
       leave it alone.
    2. Never exceed the card.
    3. Otherwise take the smaller of what a node needs and half the usable
       space, so there is always a spare pool to wear-level across.
    """
    usable = max(0, int(card_bytes) - int(boot_end_bytes))
    current = int(current_root_bytes)
    warning = ""

    if usable <= 0:
        return {"target_bytes": current, "spare_bytes": 0, "grow": False,
                "reason": "the card is smaller than its own boot partition",
                "warning": "this card cannot hold a node"}

    target = int(min(want_bytes, usable * max_used_fraction))
    if target <= current:
        # A small card: give the filesystem what is there rather than a share
        # of it, because a node that cannot fit is worse than one with no spare
        # pool — but say so.
        target = min(usable, max(current, want_bytes))
        if target <= current:
            return {"target_bytes": current, "spare_bytes": usable - current,
                    "grow": False,
                    "reason": "the card is too small to grow the filesystem",
                    "warning": ("this card leaves the node about "
                                f"{(current - 2.1e9) / 1e9:.1f} GB to work in — "
                                "16 GB or larger is the sane minimum")}
        warning = ("small card: the filesystem takes most of it, so there is "
                   "little spare left for wear levelling")

    spare = usable - target
    return {
        "target_bytes": target,
        "spare_bytes": spare,
        "grow": target > current,
        "reason": (f"{target / 1e9:.1f} GB for the node, "
                   f"{spare / 1e9:.1f} GB left unallocated as the card's "
                   f"spare pool ({100 * spare / max(1, usable):.0f}%)"),
        "warning": warning,
    }


def lxmf_storage_limit_mb(available_bytes: int,
                          reserve_bytes: int = 1_000_000_000) -> int:
    """Megabytes to allow the LXMF message store, from the space ACTUALLY FREE.

    MEASURED AT BUILD TIME, on the node, because operators use whatever card
    they have — 8 GB, 16 GB, 64 GB, second-hand, whatever was in the drawer
    (operator, 2026-09-06). A number pinned here would be wrong for most of
    them.

    LXMF defaults to 500 MB and nothing in this tool ever overrode it, so a node
    built on the unexpanded image was configured to hold more messages than its
    disk had room for — with ``autopeer = yes`` willing to pull other nodes'
    stores in to fill it.

    *reserve_bytes* is what must stay free underneath: logs, an apt upgrade, and
    enough headroom that a FULL message store still never fills the disk. A node
    that fills its root filesystem stops forwarding, stops reporting, and looks
    dead from every screen that watches it.
    """
    spare = int(available_bytes) - int(reserve_bytes)
    if spare <= 0:
        # No room to store, but a node with no mailbox still RELAYS — so the
        # floor is a token store, never zero.
        return 32
    return max(32, min(500, int(spare * 0.6 / 1_000_000)))


def write_image_command(image_path: str, device_path: str) -> str:
    """The shell command that decompresses *image_path* and writes it to the card,
    with fsync. Meant for a runner that executes a shell string with sudo."""
    img, dev = shlex.quote(image_path), shlex.quote(device_path)
    return (f"xzcat {img} | sudo dd of={dev} bs=4M conv=fsync status=progress "
            f"&& sync")


def uncompressed_image_size(image_path: str) -> int:
    """Bytes the image expands to, or 0 if it cannot be read.

    ``xz --robot --list`` reports this from the stream footer, so it costs a
    seek rather than a full decompression - which matters because this is
    wanted BEFORE the write starts, to turn a progress bar into a real one.
    """
    import subprocess
    try:
        out = subprocess.run(["xz", "--robot", "--list", image_path],
                             capture_output=True, text=True, timeout=30).stdout
    except Exception:                                    # noqa: BLE001
        return 0
    for line in out.splitlines():
        f = line.split("\t")
        if f and f[0] == "totals" and len(f) > 4:
            try:
                return int(f[4])
            except ValueError:
                return 0
    return 0


def device_bytes_written(device_path: str) -> int:
    """Bytes written to *device_path* since boot, from the kernel's own counter.

    Field 7 of /sys/block/<dev>/stat is sectors written; sectors are 512 bytes
    by definition of that file regardless of the device's real sector size.

    This is the honest alternative to timing the write and hoping. It needs no
    root, does not touch the card, and does not alter the write path - the
    caller samples a baseline before starting and subtracts it, so a card that
    has been written to earlier in the session still reports from zero.

    Returns None - NOT 0 - when the counter cannot be read, because the
    two mean opposite things. /sys/block/<dev>/stat disappears the moment
    the card is pulled or the reader re-enumerates, and returning 0 there
    made the ring fall back to 0% and restart its narration at 'Prepping
    the card' - so at the instant a write was doomed the screen looked as
    though it had cheerfully started again. The caller holds its last
    value when this returns None.
    """
    import os
    name = os.path.basename((device_path or "").strip())
    if not name:
        return None
    try:
        with open(f"/sys/block/{name}/stat") as fh:
            fields = fh.read().split()
        return int(fields[6]) * 512
    except Exception:                                    # noqa: BLE001
        return None


#: Groups a Raspberry Pi OS "pi" user normally belongs to. Without these the
#: account exists but can't reach the serial port (dialout) or GPIO — which is
#: everything a node does.
PI_USER_GROUPS = ["adm", "dialout", "cdrom", "sudo", "audio", "video",
                  "plugdev", "games", "users", "input", "netdev", "gpio",
                  "i2c", "spi"]


def _yaml_str(s: str) -> str:
    """A double-quoted YAML scalar. Password hashes are full of ``$`` and ``/``
    and keys contain ``+``; quoting them is not optional."""
    return '"' + (s or "").replace("\\", "\\\\").replace('"', '\\"') + '"'


def build_cloud_init_user_data(hostname: str, username: str, password: str,
                               enable_ssh: bool = True,
                               pw_hasher: Callable[[str], str] = None,
                               authorized_keys: "Optional[List[str]]" = None) -> str:
    """The ``user-data`` cloud-config this image ACTUALLY reads on first boot.

    Verified on the carried image (Raspberry Pi OS Trixie, pi-gen 2026-06-18):
    ``/etc/cloud/cloud.cfg.d/99_raspberry-pi.cfg`` sets
    ``datasource_list: [NoCloud, None]`` with ``seedfrom: file:///boot/firmware``,
    and ``raspberrypi-sys-mods`` ships NO ``firstboot`` script — so the
    ``custom.toml`` we also write is inert here. A card configured only by
    custom.toml boots with no user at all: sshd answers, but every login is
    refused ("SSH may not work until a valid user has been set up"). Found the
    hard way birthing HOPE, 2026-08-01.

    Defining ``users:`` replaces cloud-init's default user, which is what we
    want — exactly one account, ours.
    """
    hasher = pw_hasher or password_hash
    pw = hasher(password) if password else ""
    keys = [k for k in (authorized_keys or []) if k.strip()]
    lines = ["#cloud-config", ""]
    if hostname:
        lines += [f"hostname: {hostname}", "manage_etc_hosts: true", ""]
    lines += ["users:", f"  - name: {username}"]
    if pw:
        lines += ["    lock_passwd: false", f"    passwd: {_yaml_str(pw)}"]
    lines += ["    shell: /bin/bash",
              '    sudo: "ALL=(ALL) NOPASSWD:ALL"',
              f"    groups: [{', '.join(PI_USER_GROUPS)}]"]
    if keys:
        lines.append("    ssh_authorized_keys:")
        lines += [f"      - {_yaml_str(k)}" for k in keys]
    lines += ["", f"ssh_pwauth: {'true' if enable_ssh else 'false'}", ""]
    return "\n".join(lines)


def build_cloud_init_network_config(wifi_ssid: str = "",
                                    wifi_password: str = "") -> str:
    """Always "" — the card gets no Wi-Fi PSK on its boot partition.

    THIS USED TO RETURN A netplan BLOCK with the PSK in clear text, written to
    ``network-config`` on the FAT boot partition. Two things were wrong with it.
    It never worked: cloud-init on the carried image reads its seed from
    /boot/firmware but the medic's own diagnosis reported, twice on 2026-08-09,
    "Wi-Fi details are on the card but were NEVER APPLIED". And it put the
    operator's home PSK somewhere any computer can read by inserting the card —
    the one partition on it that is not ext4 and not root-owned.

    The Wi-Fi that works is written onto the ROOTFS by
    ``provisioning.rootfs_wifi`` as a 0600 root-owned NetworkManager connection.
    The signature is kept so callers do not silently change meaning, and so this
    docstring is what a reader finds when they come looking for the PSK.
    """
    return ""


def apply_config_commands(device_path: str, custom_toml: str,
                          user_data: str = "",
                          network_config: str = "") -> List[str]:
    """Shell commands to mount the card's boot partition and drop the firstboot
    config (custom.toml) + an empty ``ssh`` flag. Boot partition = first FAT part."""
    dev = shlex.quote(device_path)
    # boot partition is p1 (mmcblk-style 'p1') or '1' (sdX1)
    part = f"{device_path}p1" if device_path[-1].isdigit() else f"{device_path}1"
    # root-owned, mounted with the policy's pinned options (card_mount), so the
    # tee and touch below land on the card and nowhere else
    mnt = card_mount.PIBOOT
    toml_b64 = base64.b64encode(custom_toml.encode()).decode()
    cmds = [
        "sudo partprobe " + dev + " 2>/dev/null; sleep 1",
        f"sudo {card_mount.make_dir(mnt)} && sudo {card_mount.mount(part, mnt)}",
        f"echo {shlex.quote(toml_b64)} | base64 -d | sudo tee {mnt}/custom.toml >/dev/null",
        f"sudo touch {mnt}/ssh",
    ]
    # Write the cloud-init seed too. The two mechanisms are mutually inert — an
    # image without cloud-init ignores user-data, an image without the firstboot
    # hook ignores custom.toml — so writing both makes one card work on either,
    # rather than betting on which the carried image happens to be.
    for name, content in (("user-data", user_data),
                          ("network-config", network_config)):
        if not content:
            continue
        b64 = base64.b64encode(content.encode()).decode()
        cmds.append(f"echo {shlex.quote(b64)} | base64 -d | sudo tee "
                    f"{mnt}/{name} >/dev/null")
    cmds.append(f"sudo sync && sudo umount {mnt}")
    return cmds


def build_cloud_init_meta_data(instance_id: str) -> str:
    """``meta-data`` for the NoCloud datasource.

    cloud-init caches the instance_id and skips first-boot setup when it sees
    the same one again — the stock image ships a FIXED ``rpios-image``. So
    re-seeding a card that already booted needs a NEW id, or the new user-data
    is read and ignored.
    """
    return "\n".join(["dsmode: local", f"instance_id: {instance_id}", ""])


def reseed_commands(device_path: str, user_data: str, instance_id: str,
                    network_config: str = "", mnt: str = card_mount.RESEED
                    ) -> List[str]:
    """Rewrite a card's cloud-init seed WITHOUT re-imaging it.

    For a card that booted unconfigured — the HOPE case, 2026-08-01: the image
    was fine, only its first-boot config was in a format the image doesn't read.
    Re-imaging costs ~9 minutes; replacing two small files on the FAT boot
    partition costs seconds, and the next boot applies them.
    """
    part = f"{device_path}p1" if device_path[-1].isdigit() else f"{device_path}1"
    q = shlex.quote(mnt)
    cmds = [f"sudo partprobe {shlex.quote(device_path)} 2>/dev/null; sleep 1",
            f"sudo {card_mount.make_dir(mnt)} && sudo {card_mount.mount(part, mnt)}",
            f"test -f {q}/config.txt && test -f {q}/cmdline.txt"]
    for name, content in (("user-data", user_data),
                          ("meta-data", build_cloud_init_meta_data(instance_id)),
                          ("network-config", network_config)):
        if not content:
            continue
        b64 = base64.b64encode(content.encode()).decode()
        cmds.append(f"echo {shlex.quote(b64)} | base64 -d | sudo tee "
                    f"{q}/{name} >/dev/null")
    cmds += [f"sudo touch {q}/ssh", f"sudo sync && sudo umount {q}"]
    return cmds


def reseed(device_path: str, hostname: str, username: str, password: str,
           instance_id: str, wifi_ssid: str = "", wifi_password: str = "",
           enable_ssh: bool = True, run: Runner = _run,
           run_shell: "Optional[Callable[[str], Tuple[int, str]]]" = None,
           pw_hasher: Callable[[str], str] = None,
           authorized_keys: "Optional[List[str]]" = None) -> Tuple[bool, str]:
    """Repair an already-imaged card's first-boot config in place. Same hard
    safety guard as ``flash`` — removable USB targets only, never the medic."""
    if not is_safe_target(device_path, run):
        return (False, f"Refusing to touch {device_path}: it isn't a removable "
                       "USB card (or it's the medic's own system disk).")
    if authorized_keys is None:
        mk = medic_public_key()
        authorized_keys = [mk] if mk else []
    if run_shell is None:
        def run_shell(cmd):
            import subprocess
            p = subprocess.run(["bash", "-c", cmd], capture_output=True,
                               text=True, timeout=300)
            return p.returncode, (p.stdout + p.stderr)
    ud = build_cloud_init_user_data(hostname, username, password, enable_ssh,
                                    pw_hasher=pw_hasher,
                                    authorized_keys=authorized_keys)
    net = build_cloud_init_network_config(wifi_ssid, wifi_password)
    for cmd in reseed_commands(device_path, ud, instance_id, net):
        code, out = run_shell(cmd)
        if code != 0:
            return (False, f"Re-seeding failed: {out.strip()[-160:]}")
    return (True, f"Re-seeded the card as '{hostname}'. Put it back in the Pi "
                  "and power it on — first-boot setup runs again.")


def activate_account_commands(device_path: str, username: str,
                              password_hash: str,
                              authorized_keys: "Optional[List[str]]" = None,
                              mnt: str = card_mount.PIROOT_USER) -> List[str]:
    """Mount the card's rootfs and turn its shipped-but-DISABLED account into a
    working login, then read back proof.

    This is the step that actually makes a card reachable. Both boot-time
    mechanisms we tried (custom.toml, cloud-init user-data) were silent no-ops
    on the carried image — see provisioning.rootfs_user for why. Doing it here,
    while the card is in our hands, means the medic can verify it BEFORE the
    operator walks away with it.
    """
    from provisioning.rootfs_user import activate_commands
    part = f"{device_path}p2" if device_path[-1].isdigit() else f"{device_path}2"
    q = shlex.quote(mnt)
    cmds = [f"sudo {card_mount.make_dir(mnt)} && sudo {card_mount.mount(part, mnt)}",
            f"test -f {q}/etc/passwd && test -f {q}/etc/shadow"]
    cmds += activate_commands(mnt, username, password_hash, authorized_keys)
    cmds.append(f"sudo sync && sudo umount {q}")
    return cmds


#: The root-owned card-preparation helper. Everything that must happen to a
#: card AS ROOT lives there in one program, granted a single narrow NOPASSWD
#: entry — instead of a dozen separate sudo calls, one of which was
#: `sudo python3 -` with a script piped in (which is unrestricted root wearing
#: a hat, and would have gutted the medic's scoped allow-list).
PREPARE_CARD = "/usr/local/lib/nodemedic/prepare-card"

#: The repo's own copy of the helper — the source the installed one is built
#: from. Compared byte-for-byte before every card write (helper_out_of_date).
PREPARE_CARD_SOURCE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "assets", "scripts", "prepare_card.py")


def helper_out_of_date(installed: str = PREPARE_CARD,
                       repo_copy: str = PREPARE_CARD_SOURCE) -> str:
    """"" when the installed root helper matches the repo's copy, else WHY the
    imager must refuse to write.

    Found live 2026-08-14 (skyfinger): the installed helper predated the
    birth-token bake, so every card it wrote carried no token, and the
    connect-Pi step then rightly refused to advance for a machine that could
    not prove itself. Deploys cannot refresh the installed copy — sudoers
    grants it one narrow NOPASSWD entry and updating it takes the real
    password — so drift is possible, and a stale ROOT helper writing cards
    is a mute-node factory. Refusing loudly beats writing quietly; an
    unreadable copy is "could not check", which also refuses.
    """
    import hashlib
    try:
        with open(installed, "rb") as fh:
            have = hashlib.sha256(fh.read()).hexdigest()
        with open(repo_copy, "rb") as fh:
            want = hashlib.sha256(fh.read()).hexdigest()
    except OSError as e:
        return (f"Could not check the card writer against this build: {e}. "
                f"Not writing a card on an unverified root helper.")
    if have != want:
        return ("The card writer installed on this medic is out of date — "
                "cards it writes would be missing pieces of the birth "
                "(the last miss was the birth token, which stalls the "
                "walkthrough at the connect step). Update it, then retry:"
                f"\nsudo install -m 755 {repo_copy} {installed}")
    return ""


def write_card_config(config: dict,
                      config_path: str = "/tmp/nm-card-config.json") -> str:
    """Write the card config to a 0600 file, from PYTHON — never through a shell.

    THE CONFIG CARRIES THE WI-FI PSK AND A PASSWORD HASH. It has always gone to
    the helper as a file for that reason, because argv is world-readable through
    /proc. But the file itself was created by a shell command that carried the
    whole blob in its own argv:

        echo <base64 of the config> | base64 -d > /tmp/nm-card-config.json

    which put the very thing the file was protecting into `ps` output and
    /proc/<pid>/cmdline for the life of that command — on a medic that other
    people are meant to be able to hand around. os.open with O_CREAT|O_EXCL and
    mode 0600 gives no window where the file exists world-readable either.
    """
    import json as _json
    import os as _os
    data = _json.dumps(config).encode()
    try:
        _os.unlink(config_path)
    except OSError:
        pass
    fd = _os.open(config_path, _os.O_WRONLY | _os.O_CREAT | _os.O_EXCL, 0o600)
    try:
        _os.write(fd, data)
    finally:
        _os.close(fd)
    return config_path


def prepare_card_commands(device_path: str,
                          config_path: str = "/tmp/nm-card-config.json") -> List[str]:
    """Hand the whole card preparation to the root helper.

    Takes the PATH of a config already written by ``write_card_config`` — the
    contents never appear in any command line. Shredded afterwards.
    """
    q = shlex.quote(config_path)
    return [
        f"sudo -n {PREPARE_CARD} --device {shlex.quote(device_path)} "
        f"--config {q}",
        f"shred -u {q} 2>/dev/null || rm -f {q}",
    ]


def flash(device_path: str, hostname: str, username: str, password: str,
          wifi_ssid: str = "", wifi_password: str = "",
          wifi_country: Optional[str] = None,
          enable_ssh: bool = True, image_path: Optional[str] = None,
          run: Runner = _run,
          run_shell: Optional[Callable[[str], Tuple[int, str]]] = None,
          pw_hasher: Callable[[str], str] = None,
          authorized_keys: Optional[List[str]] = None,
          cable_link: bool = True, pi_key: str = "",
          medic: bool = False, timezone: str = "") -> Tuple[bool, str]:
    """Image + configure a Pi SD card. HARD SAFETY: refuses unless *device_path* is
    a present removable USB disk (never the medic's system disk). Returns (ok, msg).
    ``run_shell`` executes the dd/mount shell strings (injected in tests).
    ``wifi_country`` None = the MEDIC'S own regulatory country (provisioning.
    wifi.medic_country); when that can't be read the card gets the old default
    and the result message says so."""
    if not is_safe_target(device_path, run):
        return (False, f"Refusing to write to {device_path}: it isn't a removable "
                       "USB card (or it's the medic's own system disk).")
    # Default to the medic's OWN key: the propagation birth logs in by key,
    # so a card imaged without it produces a Pi the medic can't reach.
    if authorized_keys is None:
        mk = medic_public_key()
        authorized_keys = [mk] if mk else []
    image = image_path or carried_image()
    if not image:
        return (False, "No Pi OS image found to write (expected ~/pi_os_lite.img.xz).")
    if run_shell is None:
        def run_shell(cmd):
            import subprocess
            p = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True,
                               timeout=1800)
            return p.returncode, (p.stdout + p.stderr)
    code, out = run_shell(write_image_command(image, device_path))
    if code != 0:
        return (False, f"Writing the image failed: {out[-200:]}")
    # THE WI-FI COUNTRY: the medic's own regulatory domain, so a node lives
    # under the rules of the place that built it — not the developer's (every
    # card was baked AU with no detection, readiness ledger #123). When the
    # medic's can't be read the old default stands and the result SAYS so.
    country_note = ""
    if wifi_country is None:
        from provisioning.wifi import medic_country, DEFAULT_COUNTRY
        wifi_country = medic_country()
        if not wifi_country:
            wifi_country = DEFAULT_COUNTRY
            country_note = (f"Wi-Fi country set to {DEFAULT_COUNTRY} because the "
                            "medic's own couldn't be read — if the node lives "
                            "elsewhere, set it on the Pi with raspi-config.")
    # The MEDIC'S timezone for first boot (2026-09-23): a stock image is
    # Europe/London, and NTP fixes the clock but never the zone.
    toml = build_custom_toml(hostname, username, password,
                             wifi_country, enable_ssh, timezone=timezone,
                             pw_hasher=pw_hasher, authorized_keys=authorized_keys)
    hasher = pw_hasher or password_hash
    pw_hash = hasher(password) if password else ""
    user_data = build_cloud_init_user_data(
        hostname, username, password, enable_ssh, pw_hasher=pw_hasher,
        authorized_keys=authorized_keys)
    net_cfg = build_cloud_init_network_config(wifi_ssid, wifi_password)
    card_cfg = {
        "custom_toml": toml,
        "user_data": user_data,
        "meta_data": build_cloud_init_meta_data(f"{hostname}-{int(_time.time())}"),
        "network_config": net_cfg,
        "cable_link": bool(cable_link),
        # which Pi this card is FOR — picks the dwc2 dr_mode (a 3A+ must be
        # told "peripheral"; its USB-A socket has no ID pin to infer from)
        "pi_key": pi_key or "",
        # THE WI-FI, PUT ON THE CARD RATHER THAN ASKED OF IT. custom.toml's
        # [wlan] block and cloud-init's network-config are both inert on this
        # image, exactly like the two account mechanisms rootfs_user replaced —
        # so the details rode along on every card and were never applied
        # (proven twice, 2026-08-09). The helper writes a NetworkManager
        # connection onto the rootfs instead.
        "wifi_ssid": wifi_ssid or "",
        "wifi_psk": wifi_password or "",
        "wifi_country": wifi_country or "",
        "user": username,
        # The card's one-time birth token — recorded by record_imaged_pi so
        # first contact can PROVE it reached the machine this card made.
        "birth_token": __import__("uuid").uuid4().hex,
        "pwhash": pw_hash,
        "keys": list(authorized_keys or []),
        # MITOSIS: a MEDIC card. The helper bakes i2c + usb_max_current into
        # config.txt, the hostname onto the rootfs, and the direct-cable
        # static-IP service — so the fresh medic answers on an ethernet lead
        # from first boot. Medic cards carry no dwc2 gadget (cable_link is
        # passed False by the mitosis driver; a Pi 5's USB-C is power-in).
        "medic": bool(medic),
        "hostname": hostname,
        # THE TIMEZONE, ONTO THE ROOTFS (readiness ledger #130). The [locale]
        # block above goes into custom.toml, which this image never reads —
        # so the 2026-09-23 fix set no zone on any card and every node booted
        # as Europe/London. The helper writes /etc/timezone and relinks
        # /etc/localtime, after checking the name against the card's zoneinfo.
        "timezone": timezone or "",
        # HOW BIG TO MAKE THE ROOT FILESYSTEM. The carried image is a 2.43 GB
        # rootfs with 0.33 GB free and nothing here has ever expanded it
        # (measured 2026-09-06). Left alone, a node has no room for its message
        # store and a medic clone has no room for anything at all.
        #
        # A MEDIC card fills the card: a medic carries toolchains, a Pi OS
        # image, maps and a wheelhouse — 21 GB on the live one — so a bounded
        # rootfs would strand its clone at the first build.
        #
        # A NODE card grows to a working size and STOPS, leaving the rest
        # unallocated as the card controller's spare pool for wear levelling.
        # A node is meant to outlive whoever installed it.
        "rootfs_fill": bool(medic),
        "rootfs_bytes": (0 if medic else _node_rootfs_bytes(device_path)),
    }
    # The helper's WARN lines are the only account of the two steps it is
    # allowed to skip (the cable link and the gadget service). They are
    # deliberately non-fatal — a card that still boots and joins WiFi is worth
    # keeping — but discarding them, as this loop used to, produces the worst
    # possible outcome: a card reported as fully configured whose cable link was
    # never baked, and a screen that then promises a USB-cable birth that cannot
    # happen. Diagnosing that from the far end costs a bench night (2026-08-06).
    warnings: List[str] = []
    if country_note:
        warnings.append(country_note)
    # Record hostname AND the card's birth token HERE, where both are in
    # scope — the imaging choke point. First contact proves identity against
    # this record (operator, 2026-08-14).
    try:
        from provisioning.pi_discover import record_imaged_pi
        record_imaged_pi(hostname, username,
                         birth_token=card_cfg.get("birth_token", ""))
    except Exception:                                              # noqa: BLE001
        pass
    cfg_path = write_card_config(card_cfg)
    for cmd in prepare_card_commands(device_path, cfg_path):
        code, out = run_shell(cmd)
        if code != 0:
            return (False, "Image written, but preparing the card failed: "
                           f"{out.strip()[-180:]}")
        warnings += [line.split("PREPARE_WARN:", 1)[1].strip()
                     for line in (out or "").splitlines()
                     if "PREPARE_WARN:" in line]
    cable_failed = any("cable link" in w for w in warnings)
    # Bake the USB-cable link in as well, so this card can be birthed with the
    # Pi plugged straight into the medic — no WiFi, and no powered hub to let
    # an under-powered Pi feed its own radio (operator's design, 2026-08-01).
    # Never fatal: a card that boots and joins WiFi is still a usable card.
    # Account activation and the cable-link bake are BOTH the helper's job now
    # (one root operation, one sudoers entry). It fails loudly, so reaching here
    # means the card is genuinely ready.
    cable_msg = (" It can also be birthed over a USB cable straight into "
                 "Node Medic, with no WiFi at all."
                 if cable_link and not cable_failed else "")
    # Don't promise WiFi we were never given — a card imaged for the cable path
    # has no PSK on it at all, and saying otherwise sends the operator hunting
    # for a node that was never going to appear on their network.
    reach = ("and power on — it will join WiFi and be reachable over SSH."
             if wifi_ssid else
             "and power on. No WiFi was configured, so reach it over the USB "
             "cable to Node Medic.")
    caveat = ("  Note: " + "  ".join(warnings)) if warnings else ""
    return (True, f"SD card imaged and configured as '{hostname}'. Put it in the Pi "
                  f"{reach}" + cable_msg + caveat)


def hostnameify(name: str) -> str:
    """A node name -> a valid hostname (lowercase, dashes, trimmed, <=32).

    Lives here, not in the screen, so it is testable where Kivy is not
    importable — CI has no Kivy and the suite installs process-global stubs that
    only cover the submodules already in use.
    """
    import re
    return re.sub(r"[^a-z0-9-]+", "-", (name or "").lower()).strip("-")[:32]


def next_steps_after_imaging(via_pi_reader: bool, hostname: str = "",
                             pi_name: str = "", wifi_ssid: str = ""):
    """What the operator does NEXT, once a card is written.

    A finished card is the MIDDLE of building a Pi node, not the end of a job.
    The screen used to offer only "Image another card", which is the one thing
    the operator almost never wants and which reads as "you're done" (operator,
    2026-08-02). These are the physical actions that carry the same node
    forward.

    The steps genuinely differ by route, which is why this takes a flag rather
    than printing one generic list:

    * ``via_pi_reader`` — the card was opened THROUGH the Pi over rpiboot, so
      it is already inside the Pi. Telling someone to "put the card in the Pi"
      here sends them opening a machine that needs nothing done to it. The card
      must not be removed; the Pi just needs a power cycle to boot what we
      wrote.
    * a USB card reader — the card is in the reader and has to be moved.

    Pure copy (no Kivy) so the wording is testable, same split as
    ``power_compat.warning_lines`` and :func:`hostnameify`.
    """
    pi = pi_name or "the Raspberry Pi"
    steps = []
    if via_pi_reader:
        # ONE action. The card is already in the Pi, so "leave the card where it
        # is" tells the operator not to do a thing they were not doing — and
        # "it boots the card we just wrote" describes what the medic is about to
        # watch for anyway. Both were noise around the single physical act
        # (operator, 2026-08-02). Numbering a one-item list is noise too.
        # TEN SECONDS, not a quick in-and-out. A fast replug can leave enough
        # charge in the Pi's capacitors that it never fully powers down, so it
        # resumes in whatever half-state it was in instead of cold-booting the
        # new card — and the medic then waits for a gadget link that will never
        # come up (operator, 2026-08-06).
        steps.append(f"Unplug {pi} from Node Medic, wait ten seconds, "
                     f"then plug it back in.")
    else:
        # THE ROUTE FOR EVERY BOARD from 2026-08-06 (operator decision). The
        # rpiboot branch above is kept only for card RECOVERY — reading a card
        # out of a Pi when no reader is to hand — because it cannot work on a
        # 3A+ at all (OTG ID hardwired to host) and fails silently when it
        # can't.
        steps.append("1.  Take the microSD out of the card reader.")
        steps.append(f"2.  Put it into {pi}.")
        # A NUMBERED STEP IS AN INSTRUCTION, NOT A PARAGRAPH. The cable warning
        # first went inline here and ran so long the line was clipped mid
        # sentence on the 5" screen — the operator saw "...a USB cable that
        # carries DATA — a charge-only" and nothing more (2026-08-07). The
        # underlying clipping is fixed too, but the lesson stands: keep each
        # step short enough to scan while holding hardware, and put the
        # reasoning on its own line underneath.
        steps.append(f"3.  Plug {pi} into Node Medic with a DATA cable.")
        # THE TRAP, on its own line. Three separate faults in one bench session
        # were cables, and every one first looked like a software bug: a
        # charge-only lead powers a Pi perfectly, boots it to a login prompt,
        # and never enumerates — the node's own USB controller reports "not
        # attached" while the operator stares at a healthy green LED.
        steps.append("     A charge-only lead will power it and never appear "
                     "here.")
    where = (f"It joins {wifi_ssid} and answers to '{hostname}'."
             if wifi_ssid and hostname else
             "It answers over the USB cable — no WiFi needed.")
    return {
        "title": "Next: bring this Pi to life",
        "steps": steps,
        # Nothing to add on the in-the-Pi path: the medic is already watching
        # for it to come back and says so live, so a static promise here is one
        # more line to read for no new information.
        "note": "" if via_pi_reader else where,
        "cta": "Continue building this node  →",
    }


#: The operation, as the operator sees it. Each entry is one glowing "organ" the
#: Pi-doctor implants into the card, with the progress fraction it lands at.
#:
#: These are NOT decorative timings invented to fill the wait — they follow the
#: real shape of flash(): the compressed image write is the long middle of the
#: job (bootloader and kernel come off the front of the image, the root
#: filesystem is the bulk of it), and everything Node Medic adds of its own —
#: identity, cable link, the login account — happens afterwards when the card is
#: mounted. If flash() is ever reordered, reorder these with it; an animation
#: that narrates the wrong operation is worse than no animation, because the
#: operator uses it to judge whether a stall is normal.
IMAGING_STAGES = (
    {"at": 0.02, "organ": "bootloader",
     "label": "Bootloader in — it knows how to wake up."},
    {"at": 0.18, "organ": "kernel",
     "label": "Kernel in — the beating heart."},
    {"at": 0.45, "organ": "filesystem",
     "label": "Filesystem in — somewhere to keep things."},
    {"at": 0.88, "organ": "reticulum",
     "label": "Reticulum in — it can find the mesh now."},
    {"at": 0.96, "organ": "identity",
     "label": "Name, keys and cable link — it knows who it is."},
)


def stages_upto(fraction: float):
    """Every stage implanted at or before *fraction*, in order."""
    f = 0.0 if fraction is None else float(fraction)
    return [s for s in IMAGING_STAGES if s["at"] <= f]


def current_stage_label(fraction: float) -> str:
    """The caption to show right now — the most recent organ, or the opening line."""
    done = stages_upto(fraction)
    if not done:
        return "Prepping the card…"
    if fraction >= 1.0:
        return "Done — the card is alive."
    return done[-1]["label"]
