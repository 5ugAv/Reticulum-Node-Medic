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
from monitor.mesh import discover_mesh
from monitor.kin_roster import load_roster


class MonitorService:
    def __init__(self, registry: Optional[NodeRegistry] = None,
                 run: Optional[Runner] = None,
                 poll: Callable[[str], NodeStatus] = poll_status,
                 subnet: Optional[str] = None,
                 now: Callable[[], float] = time.time,
                 kin_roster: Optional[dict] = None):
        self.registry = registry or NodeRegistry()
        self._run = run                       # shell runner for discovery
        self._poll = poll
        self.subnet = subnet
        self._now = now
        self.hosts: Dict[str, str] = {}       # node key -> current host/IP
        # The medic's own fleet — so its built nodes show as named kin and on the
        # map, even the propagation relays it can't hear directly. Loaded from disk
        # by default; injectable for tests. When disk-backed, it's re-read each
        # rediscover so a freshly-BIRTHed node (or an edited location) appears
        # without restarting the touchscreen app.
        self._roster_from_disk = kin_roster is None
        roster = load_roster() if self._roster_from_disk else kin_roster
        self.registry.set_kin_roster(roster)

    def _refresh_roster(self) -> None:
        """Re-read the fleet roster from disk (no-op for an injected roster)."""
        if self._roster_from_disk:
            self.registry.set_kin_roster(load_roster())

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

    def discover_mesh(self) -> int:
        """Fold the medic's LoRa mesh path table (rnpath) into the registry, so
        LoRa-only / non-HTTP nodes appear on the dashboard. Returns the count."""
        if self._run is None:
            return 0
        count = 0
        now = self._now()
        for node in discover_mesh(self._run):
            self.registry.ingest_mesh(node, now)
            count += 1
            # The path's ``via`` is the medic's DIRECT 1-hop relay — the LoRa
            # neighbour the whole mesh routes through (e.g. EVERYWHERE). rnpath
            # never lists it as a destination, so surface it here or it stays
            # invisible. Skip a self-via and local-interface hops.
            via = getattr(node, "via", "")
            if via and via != node.dst_hash and "Local" not in node.interface:
                self.registry.ingest_relay(via, node.interface, now)
        return count

    def poll_cycle(self) -> None:
        """Re-poll every known host and fold the result into the registry."""
        for key, host in list(self.hosts.items()):
            self.registry.record_http_status(key, self._poll(host), self._now())

    def cycle(self, rediscover: bool = False) -> None:
        """One monitor tick: optionally rediscover (HTTP + mesh), then poll known
        hosts."""
        if rediscover:
            self._refresh_roster()
            self.discover()
            self.discover_mesh()
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

    def dashboard_dicts(self) -> List[dict]:
        """The dashboard as plain dicts the VITALS screen consumes — one row
        per physical DEVICE (aspect-destinations consolidated), kin first."""
        return self.registry.devices(self._now())

    def located_nodes(self) -> List[dict]:
        """Located-node dicts ({lat, lon, name, status}) for the Map screen."""
        return self.registry.located_nodes(self._now())
