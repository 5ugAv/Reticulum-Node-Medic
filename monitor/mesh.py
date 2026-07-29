"""Mesh discovery — enumerate reachable Reticulum nodes from ``rnpath``.

The medic's own dedicated RNode gives it a LoRa mesh vantage: ``rnpath -t --json``
lists every reachable destination (hash, hops, interface, via, expires). This is
the half of the Monitor that HTTP ``/status`` can't reach — Pi transport nodes,
phone+RNode apps (Columba), and LoRa-only nodes. Each is keyed by its destination
hash, which is exactly the key the ``NodeRegistry`` uses, so mesh nodes fold
straight into the same dashboard as birthed / HTTP-polled nodes.

The shell runner is injected (local on the medic, or SSH), so this is
unit-testable without a radio.
"""

from __future__ import annotations

import json
import re
import time as _time
from dataclasses import dataclass
from typing import Callable, List, Optional, Tuple

Runner = Callable[[str], str]   # run(command) -> stdout


def is_hex_hash(value: str, length: int = 32) -> bool:
    """True if *value* is a Reticulum destination hash: exactly *length* hex
    characters. Guards rnpath (which errors on non-hex, e.g. an HTTP-discovery
    key like ``rtnode:FAITH RTnode``) and any other place that must not feed a
    display key to a path request."""
    s = (value or "").strip()
    if len(s) != length:
        return False
    try:
        int(s, 16)
        return True
    except ValueError:
        return False


def parse_path_probe(output: str) -> Tuple[bool, Optional[int]]:
    """Parse the text of an on-demand ``rnpath -w <sec> <hash>`` path REQUEST
    (not the ``-t`` table): returns (reachable, hops).

    This is the correct 'is it answering right now' check — after dropping a
    (possibly stale) cached path, you must REQUEST a fresh one and WAIT; reading
    the table immediately always misses it. rnpath prints, on success,
    ``Path found, destination <hash> is N hop(s) away ...`` and, on failure,
    ``Path not found``.
    """
    text = output or ""
    if "Path found" in text:
        m = re.search(r"is\s+(\d+)\s+hop", text)
        return True, (int(m.group(1)) if m else None)
    return False, None


def rns_already_initialised(exc: BaseException) -> bool:
    """True if *exc* is RNS's "Attempt to reinitialise Reticulum, when it was
    already running" OSError — i.e. Reticulum is ALREADY up in this process.

    For an attach step that means SUCCESS, not failure: the instance exists, so
    the caller should proceed to register its handlers. Treating it as a failure
    is the 2026-07-30 deaf-app bug: another component won the in-process init
    race, every retry re-raised this, and the listener never attached — for the
    whole session — while everything subprocess-based looked healthy."""
    return isinstance(exc, OSError) and "reinitialise" in str(exc).lower()


def attach_with_retry(attach: Callable[[], None],
                      sleep: Callable[[float], None] = _time.sleep,
                      log: Optional[Callable[[str], None]] = None,
                      max_attempts: int = 30,
                      base_delay: float = 2.0,
                      max_delay: float = 20.0) -> bool:
    """Call *attach* (which raises on failure) with exponential backoff until it
    succeeds; return True on success, False once the attempt budget is spent.

    The medic's touchscreen app autostarts on boot and can win the race against
    ``rnsd`` — a one-shot ``RNS.Reticulum()`` then throws and, if swallowed,
    leaves the app permanently DEAF to mesh/health announces for the whole
    session (the 2026-07-29 'gray after power-cycle' incident). Retrying rides
    out the boot race; a true dev box with no rnsd simply exhausts the budget
    and returns False (caller stays offline, as before)."""
    delay = base_delay
    for attempt in range(1, max_attempts + 1):
        try:
            attach()
            if log and attempt > 1:
                log("mesh listener attached on attempt %d" % attempt)
            return True
        except Exception as e:                      # rnsd not ready yet (or ever)
            if log:
                log("mesh listener attach failed (attempt %d/%d): %s"
                    % (attempt, max_attempts, e))
            if attempt < max_attempts:
                sleep(delay)
                delay = min(delay * 1.5, max_delay)
    return False


@dataclass
class MeshNode:
    dst_hash: str
    hops: int
    interface: str
    via: str = ""
    expires: float = 0.0

    @property
    def local(self) -> bool:
        """A node's own local destinations (rns/default etc.) — not other nodes."""
        return self.interface.startswith("LocalInterface")


def parse_rnpath(json_text: str) -> List[MeshNode]:
    """Parse ``rnpath -t --json`` output (a list of path dicts)."""
    try:
        data = json.loads(json_text)
    except (ValueError, TypeError):
        return []
    if not isinstance(data, list):
        return []
    out = []
    for p in data:
        if not isinstance(p, dict):
            continue
        h = p.get("hash")
        if not h:
            continue
        try:
            hops = int(p.get("hops", 0))
        except (TypeError, ValueError):
            hops = 0
        try:
            expires = float(p.get("expires", 0) or 0)
        except (TypeError, ValueError):
            expires = 0.0
        out.append(MeshNode(dst_hash=h, hops=hops,
                            interface=p.get("interface", ""),
                            via=p.get("via", ""), expires=expires))
    return out


def discover_mesh(run: Runner, include_local: bool = False) -> List[MeshNode]:
    """Reachable mesh nodes from the local path table. Excludes the medic's own
    LocalInterface destinations by default (those aren't other nodes)."""
    nodes = parse_rnpath(run("rnpath -t --json 2>/dev/null"))
    return [n for n in nodes if include_local or not n.local]
