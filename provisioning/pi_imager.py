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

import os
import shlex
from typing import Callable, Dict, List, Optional, Tuple

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
        if tran == "usb" or rm == "1":                # removable / USB only
            disks.append({"name": name, "path": f"/dev/{name}", "size": size,
                          "model": model.strip(), "removable": True})
    return disks


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
                      wifi_ssid: str = "", wifi_password: str = "",
                      wifi_country: str = "AU", enable_ssh: bool = True,
                      timezone: str = "", pw_hasher: Callable[[str], str] = None,
                      authorized_keys: "Optional[List[str]]" = None) -> str:
    """The Raspberry Pi OS ``custom.toml`` firstboot config (schema config_version=1).
    The user password is stored SHA-512-crypted; WiFi is included only when an SSID
    is given. Written to the card's boot partition."""
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
    if wifi_ssid:
        lines += ["[wlan]", f'ssid = "{q(wifi_ssid)}"',
                  f'password = "{q(wifi_password)}"', "password_encrypted = false",
                  f'country = "{q(wifi_country)}"', ""]
    if timezone:
        lines += ["[locale]", f'timezone = "{q(timezone)}"', ""]
    return "\n".join(lines).rstrip() + "\n"


def write_image_command(image_path: str, device_path: str) -> str:
    """The shell command that decompresses *image_path* and writes it to the card,
    with fsync. Meant for a runner that executes a shell string with sudo."""
    img, dev = shlex.quote(image_path), shlex.quote(device_path)
    return (f"xzcat {img} | sudo dd of={dev} bs=4M conv=fsync status=progress "
            f"&& sync")


def apply_config_commands(device_path: str, custom_toml: str) -> List[str]:
    """Shell commands to mount the card's boot partition and drop the firstboot
    config (custom.toml) + an empty ``ssh`` flag. Boot partition = first FAT part."""
    dev = shlex.quote(device_path)
    # boot partition is p1 (mmcblk-style 'p1') or '1' (sdX1)
    part = f"{device_path}p1" if device_path[-1].isdigit() else f"{device_path}1"
    part = shlex.quote(part)
    mnt = "/tmp/rnm-piboot"
    toml_b64 = None
    import base64
    toml_b64 = base64.b64encode(custom_toml.encode()).decode()
    return [
        "sudo partprobe " + dev + " 2>/dev/null; sleep 1",
        f"sudo mkdir -p {mnt} && sudo mount {part} {mnt}",
        f"echo {shlex.quote(toml_b64)} | base64 -d | sudo tee {mnt}/custom.toml >/dev/null",
        f"sudo touch {mnt}/ssh",
        f"sudo sync && sudo umount {mnt}",
    ]


def flash(device_path: str, hostname: str, username: str, password: str,
          wifi_ssid: str = "", wifi_password: str = "", wifi_country: str = "AU",
          enable_ssh: bool = True, image_path: Optional[str] = None,
          run: Runner = _run,
          run_shell: Optional[Callable[[str], Tuple[int, str]]] = None,
          pw_hasher: Callable[[str], str] = None,
          authorized_keys: Optional[List[str]] = None,
          cable_link: bool = True) -> Tuple[bool, str]:
    """Image + configure a Pi SD card. HARD SAFETY: refuses unless *device_path* is
    a present removable USB disk (never the medic's system disk). Returns (ok, msg).
    ``run_shell`` executes the dd/mount shell strings (injected in tests)."""
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
    toml = build_custom_toml(hostname, username, password, wifi_ssid, wifi_password,
                             wifi_country, enable_ssh, pw_hasher=pw_hasher,
                             authorized_keys=authorized_keys)
    for cmd in apply_config_commands(device_path, toml):
        code, out = run_shell(cmd)
        if code != 0:
            return (False, f"Image written, but applying the config failed: {out[-160:]}")
    # Bake the USB-cable link in as well, so this card can be birthed with the
    # Pi plugged straight into the medic — no WiFi, and no powered hub to let
    # an under-powered Pi feed its own radio (operator's design, 2026-08-01).
    # Never fatal: a card that boots and joins WiFi is still a usable card.
    cable_msg = ""
    if cable_link:
        from provisioning.cable_birth import bake_commands
        for cmd in bake_commands(device_path):
            code, out = run_shell(cmd)
            if code != 0:
                cable_msg = (" The USB-cable link could not be baked in "
                             f"({out.strip()[-120:]}) — birth this one over WiFi.")
                break
        else:
            cable_msg = (" It can also be birthed over a USB cable straight into "
                         "Node Medic, with no WiFi at all.")
    # Don't promise WiFi we were never given — a card imaged for the cable path
    # has no PSK on it at all, and saying otherwise sends the operator hunting
    # for a node that was never going to appear on their network.
    reach = ("and power on — it will join WiFi and be reachable over SSH."
             if wifi_ssid else
             "and power on. No WiFi was configured, so reach it over the USB "
             "cable to Node Medic.")
    return (True, f"SD card imaged and configured as '{hostname}'. Put it in the Pi "
                  f"{reach}" + cable_msg)
