"""Monitor service — the running loop behind the dashboard.

Ties LAN discovery + HTTP ``/status`` polling into the ``NodeRegistry`` so the
Monitor shows live nodes. Discovery (a /24 sweep) is comparatively heavy, so it
runs on demand / occasionally; re-polling the already-known hosts is cheap and
runs every cycle. A node that goes unreachable (e.g. an RTNode deep-sleeping)
simply stops refreshing ``last_seen`` and the registry's staleness rule takes it
red on its own.

Clock, shell runner and poller are all injected, so this is deterministic and
unit-testable, and it drives a live registry when wired to real transports.
"""

from __future__ import annotations

import time
from typing import Callable, Dict, List, Optional

from monitor.registry import NodeRegistry, NodeRecord
from monitor.http_status import poll_status, NodeStatus
from monitor.discovery import discover_nodes, Runner


class MonitorService:
    def __init__(self, registry: Optional[NodeRegistry] = None,
                 run: Optional[Runner] = None,
                 poll: Callable[[str], NodeStatus] = poll_status,
                 subnet: Optional[str] = None,
                 now: Callable[[], float] = time.time):
        self.registry = registry or NodeRegistry()
        self._run = run                       # shell runner for discovery
        self._poll = poll
        self.subnet = subnet
        self._now = now
        self.hosts: Dict[str, str] = {}       # node key -> current host/IP

    @staticmethod
    def node_key(ns: NodeStatus, host: str) -> str:
        """Stable identity for a LAN node: the operator-set node_name (the IP is
        DHCP-dynamic), falling back to the host when unnamed."""
        return f"rtnode:{ns.node_name}" if ns.node_name else f"host:{host}"

    def discover(self) -> int:
        """Sweep the LAN, register every RTNode found, and remember its host.
        Returns the count discovered this pass."""
        if self._run is None:
            return 0
        count = 0
        for host, ns in discover_nodes(self._run, self.subnet, self._poll):
            if ns.reachable:
                key = self.node_key(ns, host)
                self.registry.record_http_status(key, ns, self._now())
                self.hosts[key] = host
                count += 1
        return count

    def poll_cycle(self) -> None:
        """Re-poll every known host and fold the result into the registry."""
        for key, host in list(self.hosts.items()):
            self.registry.record_http_status(key, self._poll(host), self._now())

    def cycle(self, rediscover: bool = False) -> None:
        """One monitor tick: optionally rediscover, then poll known hosts."""
        if rediscover:
            self.discover()
        self.poll_cycle()

    def run(self, cycles: int, interval: float = 30.0,
            discover_every: int = 10,
            sleep: Callable[[float], None] = time.sleep) -> None:
        """Run *cycles* ticks, rediscovering every *discover_every* ticks. The
        sleep is injected so tests don't wait; a live caller runs this on a
        background thread."""
        for i in range(cycles):
            self.cycle(rediscover=(i % max(1, discover_every) == 0))
            if i < cycles - 1:
                sleep(interval)

    def dashboard(self) -> List[NodeRecord]:
        """Nodes for the Monitor screen, alert-first then by name."""
        return self.registry.all(self._now())
