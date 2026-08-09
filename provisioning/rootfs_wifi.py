"""Put the node's Wi-Fi on the card directly — no first-boot magic.

THE OTHER HALF OF rootfs_user. That module exists because both boot-time config
mechanisms are silent no-ops on the carried image: ``custom.toml`` is inert (the
image has no ``raspberrypi-sys-mods/firstboot``) and cloud-init ``user-data`` is
inert too. The ACCOUNT was given a direct-to-rootfs fallback because of that.
The WI-FI never was — it is still only asked for through the two mechanisms
already known not to work.

So it never worked. Two cards in a row, 2026-08-09, both reported by the medic's
own card diagnosis as "Wi-Fi details are on the card but were NEVER APPLIED",
and a first boot on a clean supply changed nothing, because there was nothing to
change: no agent on that image ever reads the request. An evening went into
power supplies — a genuine and separate fault — while this sat underneath it.

Same remedy, same reasoning as rootfs_user: the medic holds the card, so do the
thing rather than write a request some boot-time agent may or may not honour.
A NetworkManager connection file is just a file, and NetworkManager picks up
whatever is in that directory at boot.

TWO THINGS THAT SILENTLY VOID IT, both encoded here:

  * PERMISSIONS. NetworkManager REFUSES to load a connection file that is not
    0600 and root-owned, and says so only in its own log. A world-readable file
    is the same as no file, which is exactly the failure being fixed.
  * THE REGULATORY DOMAIN. 5 GHz channels are unusable until the country is
    set, so a card aimed at a 5 GHz-only SSID joins nothing at all without it.
    The operator's own network is ``..._5g``, so this is not hypothetical.

Everything here builds strings; the caller runs them against a mounted rootfs.
"""

from __future__ import annotations

import shlex
import uuid
from typing import List

#: Where NetworkManager reads system connections from.
NM_DIR = "/etc/NetworkManager/system-connections"

#: The long-standing Raspberry Pi home for the wireless country code. wpa_
#: supplicant reads it, and NetworkManager uses wpa_supplicant underneath, so
#: it is still how the regulatory domain gets set on a card that has never
#: booted. Harmless where it is unused.
WPA_CONF = "/etc/wpa_supplicant/wpa_supplicant.conf"

#: NetworkManager ignores a connection file with looser permissions than this.
CONNECTION_MODE = "0600"


def connection_uuid(ssid: str) -> str:
    """A stable UUID for this SSID.

    Derived, not random: the same card written twice should not accumulate two
    connections for one network, and a deterministic value keeps the generated
    file byte-identical so it can be compared and tested.
    """
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, f"nodemedic-wifi-{ssid}"))


def connection_file(ssid: str, psk: str, autoconnect: bool = True) -> str:
    """A NetworkManager keyfile for *ssid*.

    Written for wlan0 with DHCP, which is what a node on someone's home network
    wants. The PSK is stored in the clear, as NetworkManager's keyfile format
    requires — which is precisely why the file must be 0600 and why the card
    itself carries a secret worth thinking about (see the birth notes on the
    PSK crossing onto every card).
    """
    return "\n".join([
        "[connection]",
        f"id={ssid}",
        f"uuid={connection_uuid(ssid)}",
        "type=wifi",
        f"autoconnect={'true' if autoconnect else 'false'}",
        "",
        "[wifi]",
        "mode=infrastructure",
        f"ssid={ssid}",
        "",
        "[wifi-security]",
        "key-mgmt=wpa-psk",
        f"psk={psk}",
        "",
        "[ipv4]",
        "method=auto",
        "",
        "[ipv6]",
        "method=auto",
        "addr-gen-mode=default",
        "",
    ])


def wpa_country_file(country: str) -> str:
    """Minimal wpa_supplicant.conf carrying only the regulatory country.

    Deliberately not a network definition — NetworkManager owns the networks.
    This exists so the radio is allowed to use the band the operator's SSID is
    actually on.
    """
    return "\n".join([
        "ctrl_interface=DIR=/var/run/wpa_supplicant GROUP=netdev",
        "update_config=1",
        f"country={country.upper()}",
        "",
    ])


def _write(mnt: str, path: str, content: str, mode: str) -> List[str]:
    """Commands that put *content* at *path* under the mounted rootfs.

    base64 through a pipe, never a heredoc: an SSID or PSK is operator text and
    a heredoc would let it terminate the document (the same injection that was
    fixed for the card-prepare path).
    """
    import base64
    target = shlex.quote(f"{mnt}{path}")
    b64 = base64.b64encode(content.encode()).decode()
    return [
        f"sudo mkdir -p {shlex.quote(mnt + path.rsplit('/', 1)[0])}",
        f"echo {shlex.quote(b64)} | base64 -d | sudo tee {target} >/dev/null",
        f"sudo chmod {mode} {target}",
        f"sudo chown 0:0 {target}",
    ]


def activate_commands(mnt: str, ssid: str, psk: str,
                      country: str = "AU") -> List[str]:
    """Everything needed for this card to join *ssid* on its first boot.

    Returns [] for an empty SSID — the cable-birth path deliberately puts no
    PSK on the card at all, and that choice must survive this.
    """
    if not ssid:
        return []
    cmds: List[str] = []
    safe = ssid.replace("/", "_")
    cmds += _write(mnt, f"{NM_DIR}/{safe}.nmconnection",
                   connection_file(ssid, psk), CONNECTION_MODE)
    if country:
        cmds += _write(mnt, WPA_CONF, wpa_country_file(country), "0600")
    return cmds


def verify_commands(mnt: str, ssid: str) -> List[str]:
    """Read back what was written, so the medic can prove it before the card
    leaves — the same standard rootfs_user holds itself to."""
    safe = ssid.replace("/", "_")
    target = shlex.quote(f"{mnt}{NM_DIR}/{safe}.nmconnection")
    return [f"test -f {target} && stat -c '%a %U:%G %n' {target}"]
