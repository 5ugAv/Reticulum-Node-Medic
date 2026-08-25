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
from provisioning.link import IP_BIN

#: B's end of the cable once the static service is installed (same /29 as the
#: gadget link — see the module docstring for why we reuse it).
PEER_ETH_IP = GADGET_USB_IP
#: A's (the medic's) end. Whitelisted verbatim in the medic's sudoers.
MEDIC_ETH_IP = HOST_USB_IP
ETH_PREFIX = USB_PREFIX

#: The mDNS name a bone-stock Raspberry Pi OS answers to before anything sets a
#: hostname. A card the medic imaged has its chosen name, but a Pi we did NOT
#: image (an adopted board, or one whose hostname write didn't take) still
#: answers here — so it is always worth a try.
STOCK_MDNS = "raspberrypi.local"

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
ExecStart=/bin/sh -c 'for t in 1 2 3 4 5 6; do \
for i in eth0 end0 enp1s0 eno1; do \
if ip link show "$i" >/dev/null 2>&1; then \
ip addr add {PEER_ETH_IP}/{ETH_PREFIX} dev "$i" 2>/dev/null; \
ip link set "$i" up; exit 0; fi; done; sleep 2; done; exit 0'

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
    """True if *host* accepts TCP on *port*. Uses getaddrinfo so a scoped IPv6
    link-local address (``fe80::1%eth0``) resolves correctly — the plain
    create_connection path mishandles the ``%iface`` scope. Tries each resolved
    family until one connects."""
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except OSError:
        return False
    for family, socktype, proto, _canon, sockaddr in infos:
        s = None
        try:
            s = socket.socket(family, socktype, proto)
            s.settimeout(timeout)
            s.connect(sockaddr)
            return True
        except OSError:
            continue
        finally:
            if s is not None:
                s.close()
    return False


def eth_subnet_hosts() -> List[str]:
    """Every host address in the cable /29, PEER first. A medic B that installed
    ETH_LINK_SERVICE answers at PEER_ETH_IP, but a Pi imaged by a DIFFERENT medic
    build (or mid-migration) may have claimed another host in the same /29; sweep
    them all rather than assume .1."""
    base = PEER_ETH_IP.rsplit(".", 1)[0]           # e.g. "10.55.0"
    first = int(PEER_ETH_IP.rsplit(".", 1)[1])
    # /29 usable hosts are .1-.6; lead with PEER, then the rest in order.
    hosts = [PEER_ETH_IP]
    for n in range(1, 7):
        addr = f"{base}.{n}"
        if addr != PEER_ETH_IP and addr != MEDIC_ETH_IP:
            hosts.append(addr)
    return hosts


def candidate_targets(hostname: str = "") -> List[str]:
    """Addresses to try for B, best-first, de-duplicated. mDNS leads because it
    needs nothing baked onto the card; the stock name catches a Pi we did not
    image; the /29 sweep is deterministic once a static address exists.

    Deliberately address-agnostic: a fresh Pi 5 can come up with an address we
    never chose (a leftover DHCP lease, a self-assigned link-local, a hostname
    that didn't take). mDNS + the stock name + the full subnet cover the cases
    a fixed single address misses; :func:`discover_peer` adds live neighbour and
    IPv6 link-local discovery on top, which need no addressing assumptions at
    all."""
    targets: List[str] = []
    name = mdns_name(hostname)
    if name:
        targets.append(name)
    if STOCK_MDNS not in targets:
        targets.append(STOCK_MDNS)
    for host in eth_subnet_hosts():
        if host not in targets:
            targets.append(host)
    return targets


def parse_neighbour_targets(neigh_output: str, iface: str = "") -> List[str]:
    """Live peers from ``ip neigh show`` — ANY address that ARP/ND resolved to a
    MAC, which is the surest sign something is actually on the wire. This is what
    makes discovery address-agnostic: whatever address B took (DHCP, link-local,
    static), the moment we exchange one packet it shows up here and becomes a
    probe target.

    IPv6 link-local (``fe80::``) results get the scope suffix ``%iface`` appended
    so both the port probe and ssh can reach them — a link-local address is
    meaningless without knowing which interface it lives on. FAILED/INCOMPLETE
    entries (a MAC we asked about but never heard back from) are skipped."""
    out: List[str] = []
    for line in neigh_output.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        addr = parts[0]
        state = parts[-1].upper()
        if state in ("FAILED", "INCOMPLETE"):
            continue
        if "lladdr" not in parts and state not in ("REACHABLE", "STALE",
                                                   "DELAY", "PROBE"):
            continue
        if addr in (MEDIC_ETH_IP,):
            continue
        if addr.lower().startswith("fe80:") and iface:
            addr = f"{addr}%{iface}"
        if addr not in out:
            out.append(addr)
    return out


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
        wired = parse_wired_interfaces(out)
        for ifc in wired:
            # Absolute path — see provisioning.link.IP_BIN. A bare `ip` resolves
            # through sudo's secure_path to /usr/sbin/ip, which does not match
            # the sudoers rule naming /usr/bin/ip, so the call is refused and
            # the medic silently never claims its end of the link.
            runner(["sudo", "-n", IP_BIN, "addr", "add",
                    f"{MEDIC_ETH_IP}/{ETH_PREFIX}", "dev", ifc], timeout=5)
            runner(["sudo", "-n", IP_BIN, "link", "set", ifc, "up"], timeout=5)
            # Nudge the neighbour tables so an unknown-address B reveals itself:
            # an all-hosts IPv6 ping fills the ND cache with every fe80:: on the
            # wire (no addressing assumptions — every Pi auto-configures one),
            # and pinging the /29 broadcast fills the ARP cache for an
            # IPv4-configured B. Both are best-effort; failure just means the
            # named/subnet candidates carry the search this lap.
            runner(["ping", "-6", "-c", "1", "-W", "1", f"ff02::1%{ifc}"],
                   timeout=5)
            runner(["ping", "-c", "1", "-W", "1", "-b", _eth_broadcast()],
                   timeout=5)
        for target in _all_targets(hostname, wired, runner):
            if probe(target, 22):
                return target
        sleep(poll)
    return None


def _eth_broadcast() -> str:
    """Directed-broadcast address of the cable /29 (…the .7 of a .0/29)."""
    base = PEER_ETH_IP.rsplit(".", 1)[0]
    return f"{base}.7"


def _all_targets(hostname: str, wired: List[str], runner: Runner) -> List[str]:
    """The full best-first probe list this lap: named + subnet candidates, then
    whatever is live in the neighbour tables (address-agnostic catch-all)."""
    targets = list(candidate_targets(hostname))
    for ifc in wired:
        for fam in ("-4", "-6"):
            _rc, out, _e = runner([IP_BIN, fam, "neigh", "show", "dev", ifc],
                                  timeout=5)
            for t in parse_neighbour_targets(out or "", ifc):
                if t not in targets:
                    targets.append(t)
    return targets


def _default_runner(argv: List[str], input: Optional[str] = None,
                    timeout: int = 30) -> Tuple[int, str, str]:
    import subprocess
    p = subprocess.run(argv, input=input, capture_output=True, text=True,
                       timeout=timeout)
    return p.returncode, p.stdout, p.stderr
