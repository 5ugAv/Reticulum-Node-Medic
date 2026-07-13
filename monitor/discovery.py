"""LAN discovery of RTNode-2400 nodes serving the HTTP ``/status`` endpoint.

So the Monitor can find nodes without the operator typing IPs. A parallel
``/status`` probe across the local /24 reliably finds them (verified on live
hardware; mDNS from the Pi's avahi was flaky). The shell runner is injected, so
this is unit-testable and can run either locally on the medic or over SSH.

Pair with ``monitor.http_status.poll_status`` for the per-node detail and
``NodeRegistry.record_http_status`` to fold results into the dashboard.
"""

from __future__ import annotations

import re
from typing import Callable, List, Optional, Tuple

from monitor.http_status import STATUS_PATH, poll_status, NodeStatus

#: run(command) -> stdout. Injected shell executor (local subprocess or SSH).
Runner = Callable[[str], str]

_IPV4_RE = re.compile(r"^\d{1,3}(?:\.\d{1,3}){3}$")


def local_subnet(run: Runner) -> Optional[str]:
    """Best-effort local /24 prefix (e.g. "192.168.1") from the host's IPv4."""
    out = run("ip -4 -o addr show scope global 2>/dev/null || "
              "hostname -I 2>/dev/null")
    for tok in out.replace("/", " ").split():
        if _IPV4_RE.match(tok) and not tok.startswith("127."):
            return tok.rsplit(".", 1)[0]
    return None


def discover_hosts(run: Runner, subnet: str, timeout: int = 3,
                   concurrency: int = 24) -> List[str]:
    """IPs on ``<subnet>.0/24`` whose ``/status`` identifies an RTNode (its JSON
    contains ``RTNode``). Uses BOUNDED parallelism (``xargs -P``): probing all
    254 at once swamps the Pi and makes weak-signal nodes time out (verified —
    a -71 dBm node was missed by an unbounded sweep)."""
    cmd = (
        f"seq 1 254 | xargs -P {concurrency} -I@ sh -c "
        f"'curl -fsS -m{timeout} http://{subnet}.@{STATUS_PATH} 2>/dev/null "
        f"| grep -q RTNode && echo {subnet}.@'"
    )
    out = run(cmd)
    hosts = {l.strip() for l in out.splitlines()
             if _IPV4_RE.match(l.strip())}
    return sorted(hosts, key=lambda ip: int(ip.split(".")[-1]))


def discover_nodes(run: Runner, subnet: Optional[str] = None,
                   poll: Callable[[str], NodeStatus] = poll_status
                   ) -> List[Tuple[str, NodeStatus]]:
    """``(host, NodeStatus)`` for every RTNode found on the LAN. Resolves the
    subnet from the host if not given."""
    subnet = subnet or local_subnet(run)
    if not subnet:
        return []
    return [(h, poll(h)) for h in discover_hosts(run, subnet)]
