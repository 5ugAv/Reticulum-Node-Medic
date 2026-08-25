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
- the direct-cable static-IP service (10.55.0.2/29 on the wired NIC), so an
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
                     wifi: Optional[Tuple[str, str]] = None) -> Tuple[bool, str, str]:
    """Write + configure the new medic's card. Returns (ok, message, password).

    The password is generated here when not given and RETURNED so the screen
    can show it ONCE, big, with 'write this down' — it is the new medic's
    login until its own hardening runs."""
    hostname = pi_imager.hostnameify(display_name)
    if not hostname:
        return False, "That name doesn't reduce to a usable hostname.", ""
    pw = password or memorable_password()
    ssid, psk = wifi if wifi is not None else medic_wifi_credentials()
    ok, msg = flash(device_path, hostname, username, pw,
                    wifi_ssid=ssid, wifi_password=psk,
                    cable_link=False, medic=True)
    return ok, msg, pw
