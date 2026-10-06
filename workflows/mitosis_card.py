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


def _mount_source(mnt: str) -> str:
    """The block device behind a mount point, via findmnt (argv, no shell)."""
    import subprocess
    return subprocess.run(["findmnt", "-n", "-o", "SOURCE", mnt],
                          capture_output=True, text=True, timeout=10).stdout


def holds_vault_key(device_path: str, mounts: Optional[Callable] = None,
                    key_exists: Optional[Callable] = None,
                    source_of: Optional[Callable] = None) -> bool:
    """True when the disk at *device_path* is the one carrying this medic's
    vault key file (provisioning.usb_key) — the stick the setup wizard told
    the keeper to plug in. The clone flow must never erase it (2026-10-04:
    it erased whichever single disk was present, unnamed)."""
    from provisioning import usb_key
    mounts = mounts or usb_key.mount_points
    key_exists = key_exists or (lambda d: usb_key.existing_key(d) is not None)
    source_of = source_of or _mount_source

    def base(dev: str) -> str:
        return (dev or "").rstrip("0123456789").rstrip("p")

    want = base(device_path)
    if not want:
        return False
    for mnt in mounts() or []:
        try:
            if not key_exists(mnt):
                continue
            src = (source_of(mnt) or "").strip()
        except Exception:                                          # noqa: BLE001
            continue
        if src and base(src) == want:
            return True
    return False


def debs_missing() -> str:
    """'' when the clone's carried packages are complete on this medic, else
    the sentence that stops the write before the card is erased: a new medic
    without them cannot finish (readiness ledger #115). Checks the PLANNED
    lists (screen and radio sets, planned against the fresh card), not just
    that some .deb files exist — a half cache failed mid-clone and told a
    touchscreen user to run a terminal command (adversarial review, 2026-10-06)."""
    try:
        from workflows.wheelhouse import (DEB_CACHE, DISPLAY_PACKAGES, APT_PACKAGES,
                                          debs_for)
        if debs_for(DISPLAY_PACKAGES, DEB_CACHE) and debs_for(APT_PACKAGES, DEB_CACHE):
            return ""
    except Exception:                                              # noqa: BLE001
        return ""                       # cannot tell — never refuse on a guess
    return ("This medic is missing a piece the new medic needs. Connect this "
            "medic to Wi-Fi, open Settings ▸ Field readiness ▸ Prepare for the "
            "field, wait for it to finish, then come back here.")


def medic_timezone() -> str:
    """This medic's own IANA timezone, or "" when it cannot be read. The clone
    boots in the zone of the medic that made it (readiness ledger #130); a
    stock image is Europe/London and NTP never fixes a zone."""
    try:
        from provisioning.tool_datetime import current_timezone
        return current_timezone() or ""
    except Exception:                                              # noqa: BLE001
        return ""


def image_medic_card(device_path: str, display_name: str,
                     username: str = "pi",
                     password: Optional[str] = None,
                     flash: Callable = pi_imager.flash,
                     wifi: Optional[Tuple[str, str]] = None,
                     helper_check: Callable = pi_imager.helper_out_of_date,
                     deb_check: Optional[Callable] = None,
                     timezone: Optional[str] = None,
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
    if deb_check is not None:
        missing = deb_check()
        if missing:
            return False, missing, ""
    hostname = pi_imager.hostnameify(display_name)
    if not hostname:
        return False, "That name doesn't reduce to a usable hostname.", ""
    pw = password or memorable_password()
    ssid, psk = wifi if wifi is not None else medic_wifi_credentials()
    tz = timezone if timezone is not None else medic_timezone()
    ok, msg = flash(device_path, hostname, username, pw,
                    wifi_ssid=ssid, wifi_password=psk,
                    cable_link=False, medic=True, timezone=tz)
    return ok, msg, pw


#: The mountpoints the medic's sudo policy allows (C2 scoping). Mounting
#: anywhere else is refused - which is why an earlier read-only check appeared
#: to prove the card unreadable when it was the mountpoint that was wrong.
#: TWO are allowlisted, and both are used: reusing ONE for both partitions
#: meant that if the first umount failed (busy), the second mount stacked on
#: top of it and the single umount popped only the upper one, leaving a
#: filesystem mounted while the next screen told the operator to pull the card
#: out.
_MNT_ROOT = "/tmp/rnm-piboot"
_MNT_BOOT = "/tmp/nm_sd_boot"


def _read_card(device_path: str, part: int, paths, mnt: str, run_shell=None):
    """Mount one partition, read *paths*, unmount. Returns (ok, {path: text}).

    ``ok`` is False when the MOUNT ITSELF failed, which is a different thing
    from the files being absent and must not be confused with it: the earlier
    version discarded the exit code, so a refused sudo or a busy mountpoint
    produced an empty dict, every check "failed", and a perfectly good card was
    condemned to a nine-minute rewrite for an environmental reason.

    NOT read-only, and the docstring no longer pretends otherwise: the sudo
    policy allows `mount <dev> <point>` and nothing else, so `-o ro` is refused
    outright. Mounting ext4 read-write replays the journal, which is a write.
    Hence the explicit sync and the checked unmount below - the card must be
    genuinely quiescent before the next screen invites the operator to pull it.
    """
    import subprocess, shlex
    if run_shell is None:
        def run_shell(cmd):
            p = subprocess.run(["bash", "-c", cmd], capture_output=True,
                               text=True, timeout=120)
            return p.returncode, (p.stdout + p.stderr)
    dev = f"{device_path}{part}"
    marker = "---RNMFILE---"
    reads = " ; ".join(
        f'echo "{marker}{p}"; cat {shlex.quote(mnt + p)} 2>/dev/null'
        for p in paths)
    code, out = run_shell(
        # mkdir WITHOUT sudo: /tmp is user-writable, and only ONE of the two
        # allowlisted mountpoints has a matching sudo mkdir rule - so the
        # sudo form silently failed the && chain for the other one and
        # reported a present, perfectly good card as unreadable.
        f"mkdir -p {mnt} && sudo -n mount {shlex.quote(dev)} {mnt} "
        f"&& {{ {reads} ; }} ; rc=$? ; sudo -n sync ; "
        f"sudo -n umount {mnt} && echo '{marker}__UMOUNT_OK__' ; exit $rc")
    found = {}
    for chunk in out.split(marker)[1:]:
        line, _, body = chunk.partition("\n")
        found[line.strip()] = body
    mounted = any(k != "__UMOUNT_OK__" for k in found)
    clean = "__UMOUNT_OK__" in found
    return (code == 0 and mounted and clean), found


def verify_medic_card(device_path: str, hostname: str, run_shell=None):
    """Read the written card back. Returns (verdict, [(label, state, detail)]).

    ``verdict`` is one of "good", "bad" or "unknown", and the third value is
    why this returns three outcomes rather than a bool: a card that could not
    be READ is not the same as a card that is WRONG, and the previous version
    collapsed them. Reporting an unreadable card as four failed checks sent a
    good card back for a nine-minute rewrite; reporting an unrun check as a
    green pass was the same lie in the other direction.

    ``state`` is True / False / None, where None means "not checked".

    Read from the ROOTFS, not custom.toml: custom.toml and cloud-init are inert
    on this image, so the bake writes to the root filesystem directly and
    anything reading custom.toml would 'verify' a file nothing ever opens.
    """
    ok_root, root = _read_card(device_path, 2, [
        "/etc/hostname",
        "/home/pi/.ssh/authorized_keys",
    ], _MNT_ROOT, run_shell=run_shell)
    ok_boot, boot = _read_card(device_path, 1, ["/config.txt"], _MNT_BOOT,
                               run_shell=run_shell)

    if not (ok_root and ok_boot):
        return "unknown", [(
            "Not checked", None,
            "the card could not be read back, so it is unproven - the "
            "firmware is written, but nothing here confirms the settings")]

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
    return ("good" if all(c[1] for c in checks) else "bad"), checks
