"""MITOSIS phase 1 — image the NEW medic's SD card in this medic's reader.

The one birth route (decision 2026-08-06): every card is written by the
MEDIC'S OWN SD reader, then carried to the target. For a medic card that
means: the carried Pi OS Lite image + (baked by the root helper, straight
onto the card — custom.toml and cloud-init are inert on this image):

- the hostname, on the rootfs;
- this medic's SSH public key, so first contact needs no password;
- a login account with a WRITE-IT-DOWN password (generated memorable, shown
  once on the screen — the B-build plan's password UX);
- i2c + usb_max_current in config.txt (the UPS gauge; GPIO power has no
  USB-PD so without the flag the Pi 5 caps USB at 600 mA);
- the direct-cable static-IP service (10.55.0.1/29 on the wired NIC), so an
  ethernet patch lead reaches the fresh medic from FIRST boot;
- this medic's own Wi-Fi (read live from NetworkManager, best-effort), so
  the clone also appears on the household network like any sibling.
"""

from __future__ import annotations

import secrets
import subprocess
from typing import Callable, Optional, Tuple

from provisioning import pi_imager

#: Small, unambiguous word pool for the write-it-down password. All lowercase,
#: no homoglyphs (no l/1, O/0 confusion), 5-6 letters each.
_WORDS = ("ember", "falcon", "harbor", "cedar", "quartz", "meadow", "signal",
          "copper", "lantern", "summit", "willow", "granite", "beacon",
          "timber", "harvest", "walnut", "prairie", "anchor")


def memorable_password() -> str:
    """word-word-NN — easy to write on paper, hard enough for a LAN box."""
    a, b = secrets.choice(_WORDS), secrets.choice(_WORDS)
    while b == a:
        b = secrets.choice(_WORDS)
    return f"{a}-{b}-{secrets.randbelow(90) + 10}"


def medic_wifi_credentials(runner: Optional[Callable] = None) -> Tuple[str, str]:
    """This medic's ACTIVE Wi-Fi (ssid, psk) via nmcli, so the clone joins the
    same network. Best-effort: any failure returns ("", "") and the card is
    still fully usable over the cable."""
    def _run(argv):
        p = subprocess.run(argv, capture_output=True, text=True, timeout=10)
        return p.returncode, p.stdout
    runner = runner or _run
    try:
        code, out = runner(["nmcli", "-t", "-f", "ACTIVE,SSID",
                            "dev", "wifi"])
        if code != 0:
            return "", ""
        ssid = next((ln.split(":", 1)[1] for ln in out.splitlines()
                     if ln.startswith("yes:")), "")
        if not ssid:
            return "", ""
        code, out = runner(["nmcli", "-s", "-g", "802-11-wireless-security.psk",
                            "connection", "show", ssid])
        return (ssid, out.strip()) if code == 0 and out.strip() else (ssid, "")
    except Exception:                                          # noqa: BLE001
        return "", ""


def image_medic_card(device_path: str, display_name: str,
                     username: str = "pi",
                     password: Optional[str] = None,
                     flash: Callable = pi_imager.flash,
                     wifi: Optional[Tuple[str, str]] = None,
                     helper_check: Callable = pi_imager.helper_out_of_date,
                     ) -> Tuple[bool, str, str]:
    """Write + configure the new medic's card. Returns (ok, message, password).

    The password is generated here when not given and RETURNED so the screen
    can show it ONCE, big, with 'write this down' — it is the new medic's
    login until its own hardening runs."""
    # The installed ROOT helper does the medic bake; an out-of-date copy
    # parses the config fine and silently ignores the medic keys — the
    # card reports written and the clone boots nameless with no cable
    # address (adversarial review 2026-08-25). Refuse loudly, like the
    # node imaging screen always has.
    stale = helper_check()
    if stale:
        return False, stale, ""
    hostname = pi_imager.hostnameify(display_name)
    if not hostname:
        return False, "That name doesn't reduce to a usable hostname.", ""
    pw = password or memorable_password()
    ssid, psk = wifi if wifi is not None else medic_wifi_credentials()
    ok, msg = flash(device_path, hostname, username, pw,
                    wifi_ssid=ssid, wifi_password=psk,
                    cable_link=False, medic=True)
    return ok, msg, pw


#: The ONLY mountpoints the medic's sudo policy allows (C2 scoping). Mounting
#: anywhere else is refused, which is why an earlier read-only check appeared to
#: prove the card unreadable when it was the mountpoint that was wrong.
_MNT = "/tmp/rnm-piboot"


def _read_card(device_path: str, part: int, paths, run_shell=None):
    """Mount one partition of the card, read *paths*, unmount. Returns a dict of
    path -> contents (missing files simply absent).

    Always unmounts, including on failure: a card left mounted cannot be pulled
    out safely, and the operator is about to be told to pull it out."""
    import subprocess, shlex
    if run_shell is None:
        def run_shell(cmd):
            p = subprocess.run(["bash", "-c", cmd], capture_output=True,
                               text=True, timeout=120)
            return p.returncode, (p.stdout + p.stderr)
    dev = f"{device_path}{part}"
    marker = "---RNMFILE---"
    reads = " ; ".join(
        f'echo "{marker}{p}"; cat {shlex.quote(_MNT + p)} 2>/dev/null'
        for p in paths)
    code, out = run_shell(
        f"sudo -n mkdir -p {_MNT} && sudo -n mount {shlex.quote(dev)} {_MNT} "
        f"&& {{ {reads} ; }} ; sudo -n sync ; sudo -n umount {_MNT} 2>/dev/null")
    found = {}
    for chunk in out.split(marker)[1:]:
        line, _, body = chunk.partition("\n")
        found[line.strip()] = body
    return found


def verify_medic_card(device_path: str, hostname: str, expect_wifi: bool = True,
                      run_shell=None):
    """Read the written card back and confirm the bake actually landed.

    Returns (ok, [(label, passed, detail), ...]).

    Worth doing because the failure it catches is otherwise invisible until the
    new medic is closed up, carried away and powered on: the image writes fine
    while the configuration silently does not, and the screen has already said
    'done'. Reads only - it never writes to the card.

    Checked on the ROOTFS, not the boot partition: custom.toml and cloud-init
    are inert on this image, so the bake writes to the root filesystem directly
    and anything looking at custom.toml would 'verify' a file nothing reads.
    """
    root = _read_card(device_path, 2, [
        "/etc/hostname",
        "/home/pi/.ssh/authorized_keys",
    ], run_shell=run_shell)
    boot = _read_card(device_path, 1, ["/config.txt"], run_shell=run_shell)

    got_host = (root.get("/etc/hostname", "") or "").strip()
    keys = [l for l in (root.get("/home/pi/.ssh/authorized_keys", "") or
                        "").splitlines() if l.strip().startswith("ssh-")]
    cfg = boot.get("/config.txt", "") or ""

    checks = [
        ("Name", got_host == hostname,
         f"it will boot as '{got_host}'" if got_host
         else "no name was written - it would boot nameless"),
        ("Key", bool(keys),
         "this medic can log in without a password" if keys
         else "this medic's key is missing - you would need the password"),
        ("Power", "usb_max_current_enable=1" in cfg,
         "USB power is unlocked for the Pi 5" if
         "usb_max_current_enable=1" in cfg
         else "USB would be capped at 600 mA"),
        ("Battery gauge", "dtparam=i2c_arm=on" in cfg,
         "the battery gauge will be readable" if "dtparam=i2c_arm=on" in cfg
         else "i2c is off - no battery reading"),
    ]
    return all(p for _, p, _ in checks), checks
