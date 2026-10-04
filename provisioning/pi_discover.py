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
                     model: str = "", path: Optional[str] = None,
                     birth_token: str = "") -> bool:
    """Remember the card we just wrote, so the birth screen can offer its
    address instead of asking. Best-effort."""
    path = path or STATE_PATH
    if not hostname:
        return False
    # The card just written IS a new identity — forget the old one's host
    # keys now, at the choke point, or the next gate probe fails
    # verification against a node that is behaving perfectly.
    try:
        forget_node_keys(hostname)
    except Exception:                      # noqa: BLE001
        pass
    try:
        from monitor.atomic_json import write_json
        if not birth_token:
            # A later caller (the imager screen re-records for the address
            # book) must not wipe the token the imaging run just recorded.
            birth_token = (last_imaged_pi(path).get("birth_token") or ""
                           if last_imaged_pi(path).get("hostname") == hostname
                           else "")
        return write_json(path, {"hostname": hostname, "username": username,
                                 "model": model,
                                 "birth_token": birth_token}, indent=2)
    except Exception:                      # noqa: BLE001
        return False


def last_imaged_pi(path: Optional[str] = None) -> Dict[str, str]:
    """The last card this medic imaged ({hostname, username, model}), or {}."""
    path = path or STATE_PATH
    try:
        with open(path) as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:                      # noqa: BLE001
        return {}


def suggested_address(path: Optional[str] = None) -> str:
    """The address BIRTH should pre-fill: the imaged hostname as an mDNS name."""
    path = path or STATE_PATH
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


def name_for_ip(ip: str) -> str:
    """The hostname behind an IP, via mDNS/DNS, or "".

    ``getent hosts <ip>`` does the reverse lookup, and nss-mdns answers for
    ``.local`` names on this LAN — verified on the medic against a node whose
    name it already knew.
    """
    out = _run(["getent", "hosts", (ip or "").strip()])
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 2:
            return parts[1].split(".")[0]
    return ""


def known_kin_names(path: str = "") -> Dict[str, str]:
    """``{lowercased name: display name}`` for every node this medic built.

    Used to recognise our OWN nodes on the network so they are never offered as
    something to build over — the medic knows their names, it just wasn't
    looking (2026-08-02).
    """
    path = path or STATE_PATH
    try:
        from monitor.kin_roster import load_roster
        roster = load_roster(path) if path else load_roster()
    except Exception:                                  # noqa: BLE001
        return {}
    out = {}
    for entry in (roster or {}).values():
        name = (entry or {}).get("name") or ""
        if name:
            out[name.strip().lower()] = name.strip()
            out[name.strip().lower().replace(" ", "-")] = name.strip()
    return out


def identify(ip: str, kin: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """What is at *ip*? ``{ip, name, kin, label}``.

    ``kin`` is the display name when this is one of the medic's own nodes —
    which also means it must never be offered as a build target.
    """
    kin = known_kin_names() if kin is None else kin
    name = name_for_ip(ip)
    mine = kin.get((name or "").strip().lower(), "")
    if mine:
        label = f"{ip} — {mine} (already one of your nodes)"
    elif name:
        label = f"{ip} — {name}"
    else:
        label = ip
    return {"ip": ip, "name": name, "kin": mine, "label": label}


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


def find_pi(hostname: str = "", path: Optional[str] = None) -> Dict[str, str]:
    """Best effort at locating the Pi.

    Returns ``{address, ip, how, confirmed}``. ``confirmed`` is the important
    field: True only when the answer can be tied to THIS build — the USB cable,
    or the mDNS name the medic itself gave the Pi when it imaged the card. A
    network sweep can only prove that *a* Raspberry Pi exists, never that it is
    ours, so it returns ``confirmed=False`` and an empty address. Callers must
    not auto-fill an unconfirmed answer: the build rewrites the target's
    services and config.
    """
    path = path or STATE_PATH
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
        # Name them. The medic knows what its own nodes are called, so a sweep
        # hit that IS one of them can be called out rather than offered as
        # something to build over — on 2026-08-02 the sweep offered an
        # address that was already one of the medic's own nodes.
        kin = known_kin_names()
        seen = [identify(n["ip"], kin) for n in nb]
        mine = [d for d in seen if d["kin"]]
        if mine and len(mine) == len(seen):
            names = ", ".join(d["kin"] for d in mine)
            return {"address": "", "ip": "", "confirmed": False,
                    "how": (f"The only Raspberry Pi{'s' if len(mine) != 1 else ''} "
                            f"on your network {'are' if len(mine) != 1 else 'is'} "
                            f"{names} — already yours. Nothing here to build."),
                    "candidates": ", ".join(d["label"] for d in seen)}
        return {"address": "", "ip": "", "confirmed": False,
                "how": (f"Found {len(nb)} Raspberry Pi"
                        f"{'s' if len(nb) != 1 else ''} on your network, but "
                        f"Node Medic can't tell if any of them is the one "
                        f"you're building. Pick one only if you're sure."),
                "candidates": ", ".join(d["label"] for d in seen)}
    return {}


def addresses_for(addr: str) -> list:
    """Every address *addr* currently resolves to, best first.

    A NODE WITH TWO ROADS HAS ONE NAME. Plugged into the medic and joined to
    Wi-Fi, a Pi advertises the same mDNS name on both — so ``<name>.local``
    resolves to 10.55.0.1 or to its LAN address depending on which it announced
    most recently. skyfinger.local answered on Wi-Fi when the medic probed it,
    and handed back the (dead) cable when the build asked a minute later. The
    probe and the build were talking about different machines under one name
    (2026-08-11).

    So a name is expanded to ALL of its addresses and each is tried. The name
    itself is kept at the end: if resolution fails entirely, the caller is no
    worse off than before.

    The cable address sorts LAST among equals. It is the road that dies — it
    depends on a USB gadget, a cable and a socket the radio also wants — and
    when both answer, the network one is the one that will still be there when
    the operator walks away.
    """
    if not addr:
        return []
    out = []
    try:
        import socket
        for fam, _t, _p, _c, sa in socket.getaddrinfo(addr, 22,
                                                      proto=socket.IPPROTO_TCP):
            ip = sa[0]
            if ip and ip not in out:
                out.append(ip)
    except Exception:                                          # noqa: BLE001
        pass
    out.sort(key=lambda a: a.startswith("10.55.0."))
    if addr not in out:
        out.append(addr)
    return out


def _probe_argv(addr: str, user: str = "pi") -> List[str]:
    """The liveness probe's exact ssh command — HOST-KEY-BLIND, deliberately.

    This asks "are you up?", never "are you who you were?". A re-imaged card
    legitimately rotates its host key, and the stale entry blocked the gate
    twice in one evening (nodes 'soon' and 'ttt', 2026-08-12) — the
    walkthrough sat at "looking for the Pi" until the keys were cleared by
    hand over SSH. The build's own connection still verifies and pins; a
    probe that reads one number from /proc makes no trust decision worth
    failing the walkthrough over.
    """
    return ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=6",
            "-o", "UserKnownHostsFile=/dev/null",
            "-o", "StrictHostKeyChecking=no",
            "-o", "LogLevel=ERROR",
            f"{user}@{addr}", "cat /proc/uptime"]


def uptime_seconds(addr: str, user: str = "pi", _conn=None) -> "Optional[float]":
    """The node's own /proc/uptime, or None when it cannot be read.

    The node's clock of record for "has it finished rebooting itself": a
    fresh card's first boot applies its baked config and reboots once, and
    any TCP-level probe can catch the doomed first boot (2026-08-12, node
    'soon' — three builds died mid-detect against it). *_conn* is injectable
    for tests; the default is the host-key-blind probe above.
    """
    try:
        if _conn is not None:
            out = _conn.run("cat /proc/uptime", timeout=8)[1] or ""
        else:
            out = _run(_probe_argv(addr, user), timeout=12)
        return float(out.split()[0])
    except Exception:                                              # noqa: BLE001
        return None


def forget_node_keys(hostname: str, _run=None) -> None:
    """Drop every stored host key for a node that has just been RE-IMAGED.

    Writing a card MAKES a new identity — keeping the old key only schedules
    a verification failure for later (it stranded two walkthroughs on
    2026-08-12). Same shape as cert_store.save_cert retiring the old
    certificate: one choke point every imaging goes through, so the rule
    holds wherever a card comes from. Clears both stores: the user's
    known_hosts and the medic's pinned file.
    """
    if not hostname:
        return
    import os as _os
    runner = _run or (lambda argv, timeout=8: _run_keygen(argv, timeout))
    hosts = [hostname, f"{hostname}.local", "10.55.0.1"]
    pinned = _os.path.expanduser("~/.reticulum-node-medic/known_hosts")
    for h in hosts:
        runner(["ssh-keygen", "-R", h], timeout=8)
        if _os.path.exists(pinned) or _run is not None:
            runner(["ssh-keygen", "-R", h, "-f", pinned], timeout=8)


def _run_keygen(argv, timeout=8):
    return _run(argv, timeout=timeout)

def read_birth_token(addr: str, user: str = "pi",
                     _run_probe=None) -> "Optional[str]":
    """The birth token baked on the card at *addr* — "" when the machine
    answers but has no token file (an old card), None when it does not
    answer at all. Host-key-blind like the uptime probe, and for the same
    reason: this asks "which card are you?", and the answer is verified
    against the medic's own record, not against a host key."""
    argv = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=6",
            "-o", "UserKnownHostsFile=/dev/null",
            "-o", "StrictHostKeyChecking=no",
            "-o", "LogLevel=ERROR",
            f"{user}@{addr}",
            "cat /boot/firmware/nodemedic-birth-token 2>/dev/null || echo"]
    try:
        out = (_run_probe or _run)(argv, timeout=12)
        if out is None:
            return None
        return out.strip()
    except Exception:                                              # noqa: BLE001
        return None


def imaged_pi_answers(hostname: str, path: Optional[str] = None,
                      _token_at=None, _cable=None, _resolve=None):
    """Walk both roads to *hostname*'s Pi and ask each answering machine for
    the birth token of the card the medic just wrote. ``(proven, imposter)``:
    *proven* only when a machine quotes the recorded token back; *imposter*
    is the address of a machine that ANSWERED with the wrong token (or none)
    — during a rebirth that is usually the very node being replaced, still
    plugged in (EVERYWHERE, 2026-08-14, the skipped-instruction morning).

    Presence is not proof: any Pi on the cable or any machine holding the
    name would pass a ping. Only the machine booted from THIS card can quote
    the token, because the imager baked it there and recorded it nowhere
    else. No recorded token for this hostname -> (False, "", "") — the
    walkthrough then simply waits for the operator instead of guessing.

    Returns ``(proven, imposter_addr, why)`` — *why* is "" or, when an
    answering machine failed the proof, "no-token" (its card carries none:
    an old card, or one written by a stale card-writer — the skyfinger
    stall) or "wrong-token" (a different card entirely: usually the node
    being replaced, still powered). The screen narrates it; a silent
    refusal reads as a hang (operator, 2026-08-14).
    """
    path = path or STATE_PATH
    rec = last_imaged_pi(path)
    want = (hostname or "").strip().lower()
    expected = ""
    if want and (rec.get("hostname") or "").strip().lower() == want:
        expected = (rec.get("birth_token") or "").strip()
    if not expected:
        return (False, "", "")
    token_at = _token_at or read_birth_token
    addrs = []
    cable = (_cable or cable_address)(timeout=2.0) if _cable is None else _cable()
    if cable:
        addrs.append(cable)
    lan = (_resolve or resolve)(hostname)
    if lan and lan not in addrs:
        addrs.append(lan)
    imposter, why = "", ""
    for addr in addrs:
        tok = token_at(addr)
        if tok is None:
            continue                       # silence: not proof, not imposter
        if tok == expected:
            return (True, "", "")
        if not imposter:
            imposter, why = addr, ("no-token" if not tok else "wrong-token")
    return (False, imposter, why)
