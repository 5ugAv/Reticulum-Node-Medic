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
from dataclasses import dataclass
from typing import Callable, List

Runner = Callable[[str], str]   # run(command) -> stdout


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
