"""Find the Pi the medic just imaged — so nobody has to know what an IP is.

The operator asked, reasonably: *"it's asking for a Pi address and no user is
going to know what that is"*. They shouldn't have to. The medic **chose the
Pi's hostname itself** when it wrote the card, and Raspberry Pi OS announces
that name on the local network over mDNS — so the address is something the tool
already knows, not something to interrogate the operator about.

Three strategies, cheapest first:

1. **Remembered hostname** — whatever the imager wrote, resolved as
   ``<hostname>.local``. Verified working on the medic (``getent hosts`` has
   mDNS via nss-mdns; ``nodemedic.local`` resolves).
2. **Any name the operator types** — same resolution, so ``mypi`` and
   ``mypi.local`` and a raw IP all behave.
3. **Neighbour sweep** — read the kernel's ARP/neighbour table and pick out
   Raspberry Pi hardware by its registered MAC prefixes. This is a *hint*, not
   proof: it finds Pis on the LAN, including ones that aren't ours.

Pure stdlib + two shell reads; no new dependencies, and every function fails
soft (returns empty) so a discovery hiccup can never block a birth.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from typing import Dict, List, Optional

#: Where the imager notes what it last wrote, so BIRTH can pre-fill the address.
STATE_PATH = os.path.expanduser("~/.reticulum-node-medic/last_imaged_pi.json")

#: MAC prefixes registered to the Raspberry Pi Foundation / Trading. Used only
#: to RANK candidates on the LAN — never to claim certainty.
PI_MAC_PREFIXES = ("b8:27:eb", "dc:a6:32", "e4:5f:01", "28:cd:c1", "d8:3a:dd",
                   "2c:cf:67")

_MAC_RE = re.compile(r"([0-9a-f]{2}(?::[0-9a-f]{2}){5})", re.I)
_IP_RE = re.compile(r"^(\d{1,3}\.){3}\d{1,3}$")


def _run(argv: List[str], timeout: int = 8) -> str:
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        return (p.stdout or "") + (p.stderr or "")
    except Exception:                      # noqa: BLE001
        return ""


# --------------------------------------------------------------------------- #
# What the imager last wrote
# --------------------------------------------------------------------------- #

def record_imaged_pi(hostname: str, username: str = "pi",
                     model: str = "", path: str = STATE_PATH) -> bool:
    """Remember the card we just wrote, so the birth screen can offer its
    address instead of asking. Best-effort."""
    if not hostname:
        return False
    try:
        from monitor.atomic_json import write_json
        return write_json(path, {"hostname": hostname, "username": username,
                                 "model": model}, indent=2)
    except Exception:                      # noqa: BLE001
        return False


def last_imaged_pi(path: str = STATE_PATH) -> Dict[str, str]:
    """The last card this medic imaged ({hostname, username, model}), or {}."""
    try:
        with open(path) as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:                      # noqa: BLE001
        return {}


def suggested_address(path: str = STATE_PATH) -> str:
    """The address BIRTH should pre-fill: the imaged hostname as an mDNS name."""
    host = (last_imaged_pi(path).get("hostname") or "").strip()
    return f"{host}.local" if host else ""


# --------------------------------------------------------------------------- #
# Resolving / sweeping
# --------------------------------------------------------------------------- #

def resolve(name: str) -> Optional[str]:
    """The IP for *name* — accepts a bare hostname, an mDNS ``x.local`` name, or
    an IP (returned unchanged). None if it can't be resolved right now."""
    name = (name or "").strip()
    if not name:
        return None
    if _IP_RE.match(name):
        return name
    tries = [name] if "." in name else [f"{name}.local", name]
    for candidate in tries:
        out = _run(["getent", "hosts", candidate])
        for line in out.splitlines():
            ip = line.split()[0] if line.split() else ""
            if _IP_RE.match(ip):
                return ip
    return None


def neighbours() -> List[Dict[str, str]]:
    """Hosts in the kernel's neighbour table that look like Raspberry Pis.
    A HINT for the operator to choose from — other people's Pis can appear."""
    out = _run(["ip", "neigh"])
    found = []
    for line in out.splitlines():
        parts = line.split()
        if not parts or not _IP_RE.match(parts[0]):
            continue
        m = _MAC_RE.search(line)
        if not m:
            continue
        mac = m.group(1).lower()
        if not mac.startswith(PI_MAC_PREFIXES):
            continue
        if "FAILED" in line or "INCOMPLETE" in line:
            continue
        found.append({"ip": parts[0], "mac": mac})
    return found


def cable_address(timeout: float = 6.0) -> str:
    """``10.55.0.1`` if a Pi is answering over the USB cable, else "".

    The cable beats every other route and should be tried first: it needs no
    network, no WiFi, no name resolution and no operator input, and it is the
    route the medic itself set up when it imaged the card. Asking someone to
    type an address for a Pi that is physically plugged into the tool was the
    complaint that started this whole path (2026-08-01).
    """
    try:
        from provisioning.link import discover_peer
        return discover_peer(timeout=timeout, poll=2.0) or ""
    except Exception:                      # noqa: BLE001 — never block a birth
        return ""


def find_pi(hostname: str = "", path: str = STATE_PATH) -> Dict[str, str]:
    """Best effort at locating the Pi.

    Returns ``{address, ip, how, confirmed}``. ``confirmed`` is the important
    field: True only when the answer can be tied to THIS build — the USB cable,
    or the mDNS name the medic itself gave the Pi when it imaged the card. A
    network sweep can only prove that *a* Raspberry Pi exists, never that it is
    ours, so it returns ``confirmed=False`` and an empty address. Callers must
    not auto-fill an unconfirmed answer: the build rewrites the target's
    services and config.
    """
    # The cable first — no network, no name, nothing to type.
    if not hostname:
        cable = cable_address()
        if cable:
            return {"address": cable, "ip": cable, "confirmed": True,
                    "how": "plugged into Node Medic by cable"}
    host = (hostname or last_imaged_pi(path).get("hostname") or "").strip()
    if host:
        name = host if host.endswith(".local") else f"{host}.local"
        ip = resolve(name)
        if ip:
            return {"address": name, "ip": ip, "confirmed": True,
                    "how": "answered to the name Node Medic gave it"}
    # A neighbour sweep finds RASPBERRY PIS, not OUR Pi. On 2026-08-02 it
    # offered 192.168.1.42 — a real, unrelated Pi on the operator's LAN —
    # as the build target, while the Pi actually being built sat on USB in
    # card-reader mode with no network address at all. Provisioning rewrites
    # a machine's services and config, so handing over an address we cannot
    # tie to THIS build is the most destructive thing this module could do.
    #
    # So a sweep result is never `confirmed`, and callers must not act on it
    # without the operator explicitly choosing it.
    nb = neighbours()
    if nb:
        return {"address": "", "ip": "", "confirmed": False,
                "how": (f"Found {len(nb)} Raspberry Pi"
                        f"{'s' if len(nb) != 1 else ''} on your network, but "
                        f"Node Medic can't tell if any of them is the one "
                        f"you're building. Pick one only if you're sure."),
                "candidates": ", ".join(n["ip"] for n in nb)}
    return {}
