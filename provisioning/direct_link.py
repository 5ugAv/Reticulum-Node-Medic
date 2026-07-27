"""Direct-cable link between two Node Medics — MITOSIS with no WiFi.

Cloning medic A onto medic B must work in the field, where there is no WiFi and
no DHCP. It must also avoid the imaging path's worst habit: seeding WiFi creds
writes the **plaintext PSK** onto the card's FAT boot partition, readable by
anyone who pops the card out.

Why ethernet and not the USB gadget: ``provisioning.gadget`` gives *nodes* a
plug-in link via ``dtoverlay=dwc2`` + ``g_ether``. That is a Pi 4 / Zero / CM
mechanism — on a Pi 5 the USB-C port is a power input and USB sits behind RP1,
so OTG peripheral mode is not available the same way. Medic-to-medic is Pi 5 to
Pi 5, so the link is the onboard gigabit NIC and an ordinary patch cable (both
ends are auto-MDIX; no crossover cable needed).

Addressing, in the order tried:

1. **mDNS** (``<hostname>.local``) — the bone-stock path. A freshly imaged B has
   no static address, but Pi OS ships avahi and ``custom.toml`` already sets the
   hostname, so B is resolvable over the cable the moment it boots. Nothing extra
   is baked onto the card.
2. **The static /29** — deterministic, once :data:`ETH_LINK_SERVICE` has been
   installed on B (rootfs, so it can only be done after first contact — the SD
   editor only touches the boot partition).

The /29 deliberately REUSES ``provisioning.gadget``'s addresses rather than
picking a fresh subnet: the medic's sudoers whitelist pins ``ip addr add
10.55.0.2/29 dev *`` (any device), so reusing it keeps a cable link inside the
existing privilege grant. A new subnet would mean widening the sudoers policy for
no benefit — the gadget and cable links are never up to the same peer at once.

Pure/injectable: parsing and unit text are plain transforms; only the runner
touches the system.
"""

from __future__ import annotations

import socket
import time
from typing import Callable, List, Optional, Tuple

from provisioning.gadget import GADGET_USB_IP, HOST_USB_IP, USB_PREFIX

#: B's end of the cable once the static service is installed (same /29 as the
#: gadget link — see the module docstring for why we reuse it).
PEER_ETH_IP = GADGET_USB_IP
#: A's (the medic's) end. Whitelisted verbatim in the medic's sudoers.
MEDIC_ETH_IP = HOST_USB_IP
ETH_PREFIX = USB_PREFIX

Runner = Callable[..., Tuple[int, str, str]]

#: Wired NIC name prefixes. Pi OS may present the onboard NIC as ``eth0`` or, with
#: predictable naming, ``end0``/``enp*``/``eno*``. ``enx*`` is EXCLUDED on purpose:
#: that is the USB-CDC gadget, which provisioning.link already owns.
_WIRED_PREFIXES = ("eth", "end", "enp", "eno")

#: Installed on B so the cable link comes up at a known address on every boot.
#: Mirrors gadget.GADGET_USB0_SERVICE: set the address directly with ``ip`` rather
#: than through NetworkManager/dhcpcd, which differ across Pi OS releases.
ETH_LINK_SERVICE = f"""\
[Unit]
Description=Direct-cable link static IP (Node Medic MITOSIS)
After=network-pre.target
Wants=network-pre.target

[Service]
Type=oneshot
RemainAfterExit=yes
# The NIC name varies (eth0/end0/enp*), so bind to the first wired one present.
ExecStart=/bin/sh -c 'for i in eth0 end0 enp1s0 eno1; do \
if ip link show "$i" >/dev/null 2>&1; then \
ip addr add {PEER_ETH_IP}/{ETH_PREFIX} dev "$i" 2>/dev/null; \
ip link set "$i" up; exit 0; fi; done; exit 0'

[Install]
WantedBy=multi-user.target
"""

ETH_LINK_SERVICE_PATH = "/etc/systemd/system/nodemedic-cable-ip.service"


def parse_wired_interfaces(ip_link_output: str) -> List[str]:
    """Wired NIC names from ``ip -o link``, excluding loopback, wireless and the
    USB-gadget CDC devices (``enx*``) that :mod:`provisioning.link` handles."""
    names: List[str] = []
    for line in ip_link_output.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        name = parts[1].rstrip(":").split("@")[0]
        if name.startswith("enx"):          # USB gadget — not our cable
            continue
        if name.startswith(_WIRED_PREFIXES):
            names.append(name)
    return names


def mdns_name(hostname: str) -> str:
    """``<hostname>.local`` — how a bone-stock B is reachable over the cable
    before any static address exists. Accepts a name already ending in .local."""
    host = (hostname or "").strip().rstrip(".")
    if not host:
        return ""
    return host if host.endswith(".local") else f"{host}.local"


def _port_open(host: str, port: int = 22, timeout: float = 3.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def candidate_targets(hostname: str = "") -> List[str]:
    """Addresses to try for B, best-first. mDNS leads because it needs nothing
    baked onto the card; the static /29 only answers once ETH_LINK_SERVICE is in
    place."""
    targets: List[str] = []
    name = mdns_name(hostname)
    if name:
        targets.append(name)
    targets.append(PEER_ETH_IP)
    return targets


def discover_peer(hostname: str = "", runner: Optional[Runner] = None,
                  timeout: float = 90.0, poll: float = 2.0, sleep=time.sleep,
                  now=time.monotonic, probe=_port_open) -> Optional[str]:
    """Wait (up to *timeout* s) for medic B to answer on the cable; return the
    address that worked, or None.

    Claims A's end of the /29 on each wired NIC as it appears (harmless if already
    assigned, so this is safe to re-run), then probes each candidate target. The
    address is returned rather than assumed so the caller can hand it straight to
    an SSHConnection — the MITOSIS workflow itself needs no change to run over a
    cable instead of WiFi."""
    runner = runner or _default_runner
    deadline = now() + timeout
    while now() < deadline:
        rc, out, _ = runner(["ip", "-o", "link"], timeout=5)
        for ifc in parse_wired_interfaces(out):
            runner(["sudo", "-n", "ip", "addr", "add",
                    f"{MEDIC_ETH_IP}/{ETH_PREFIX}", "dev", ifc], timeout=5)
            runner(["sudo", "-n", "ip", "link", "set", ifc, "up"], timeout=5)
        for target in candidate_targets(hostname):
            if probe(target, 22):
                return target
        sleep(poll)
    return None


def _default_runner(argv: List[str], input: Optional[str] = None,
                    timeout: int = 30) -> Tuple[int, str, str]:
    import subprocess
    p = subprocess.run(argv, input=input, capture_output=True, text=True,
                       timeout=timeout)
    return p.returncode, p.stdout, p.stderr
