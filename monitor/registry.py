"""Node registry — the Monitor mode backend.

Holds known nodes keyed by their ``rtnode.health`` destination hash (the stable
identity the firmware persists in LittleFS). Static metadata — name, location,
type — is set at build time (the "birth certificate"); volatile health arrives
as decoded beacons. Status is the beacon's traffic-light, overridden to red
once a node hasn't been heard for longer than the staleness window.

Timestamps are passed in (epoch seconds) rather than read from the clock, so
the backend is deterministic and unit-testable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Optional

from monitor.health_beacon import HealthBeacon, beacon_status, decode
from monitor.health_poll import PollResult
from ui import theme

#: Not heard for longer than this -> red (matches the Monitor spec).
STALE_ALERT_HOURS = theme.NOT_HEARD_ALERT_HOURS  # 6

_BEACON_RE = re.compile(
    r"\[HealthBeacon\][^\n]*dst=([0-9a-fA-F]+)[^\n]*data=([0-9a-fA-F]+)")

_STATUS_RANK = {"alert": 0, "warn": 1, "ok": 2, "unknown": 3}


@dataclass
class NodeRecord:
    dst_hash: str
    name: str = ""
    location: str = ""
    node_type: str = "rtnode2400"          # "rtnode2400" | "pi"
    latest_beacon: Optional[HealthBeacon] = None
    last_seen: Optional[float] = None       # epoch seconds

    def last_seen_hours(self, now: float) -> Optional[float]:
        if self.last_seen is None:
            return None
        return (now - self.last_seen) / 3600.0

    def status(self, now: float) -> str:
        if self.last_seen is None:
            return "unknown"
        if (now - self.last_seen) / 3600.0 > STALE_ALERT_HOURS:
            return "alert"                  # not heard -> red, regardless
        if self.latest_beacon is not None:
            return beacon_status(self.latest_beacon)
        return "unknown"


class NodeRegistry:
    def __init__(self):
        self.nodes: Dict[str, NodeRecord] = {}

    def register(self, dst_hash: str, name: str = "", location: str = "",
                 node_type: str = "rtnode2400") -> NodeRecord:
        """Create or update a node's static metadata (from the birth cert)."""
        rec = self.nodes.get(dst_hash)
        if rec is None:
            rec = NodeRecord(dst_hash=dst_hash, name=name, location=location,
                             node_type=node_type)
            self.nodes[dst_hash] = rec
        else:
            if name:
                rec.name = name
            if location:
                rec.location = location
            rec.node_type = node_type
        return rec

    def get(self, dst_hash: str) -> Optional[NodeRecord]:
        return self.nodes.get(dst_hash)

    def ingest(self, dst_hash: str, beacon: HealthBeacon,
               now: float) -> NodeRecord:
        """Record a decoded beacon; auto-registers a never-seen node."""
        rec = self.nodes.get(dst_hash)
        if rec is None:
            rec = self.register(dst_hash)
        rec.latest_beacon = beacon
        rec.last_seen = now
        return rec

    def ingest_line(self, text: str, now: float) -> Optional[NodeRecord]:
        """Parse a serial/announce ``[HealthBeacon]`` line and ingest it.
        Returns ``None`` for non-beacon or undecodable lines."""
        m = _BEACON_RE.search(text)
        if not m:
            return None
        try:
            beacon = decode(bytes.fromhex(m.group(2)))
        except ValueError:
            return None
        return self.ingest(m.group(1), beacon, now)

    def ingest_announce(self, dst_hash: bytes, app_data: bytes,
                        now: float) -> Optional[NodeRecord]:
        """Adapter for an RNS announce handler. A live handler does:

            def received_announce(self, destination_hash, identity, app_data):
                registry.ingest_announce(destination_hash, app_data, time.time())

        *dst_hash* is the raw destination-hash bytes RNS provides. Returns
        ``None`` if the payload isn't a decodable beacon.
        """
        try:
            beacon = decode(app_data)
        except (ValueError, TypeError):
            return None
        return self.ingest(dst_hash.hex(), beacon, now)

    def record_poll(self, dst_hash: str, result: PollResult,
                    now: float) -> Optional[NodeRecord]:
        """Fold an on-demand poll result in: a fresh reply updates the node
        (clearing red/orange to green if clean); silence changes nothing (the
        staleness rule will take it red on its own)."""
        if result.reachable and result.beacon is not None:
            return self.ingest(dst_hash, result.beacon, now)
        return self.nodes.get(dst_hash)

    # -- dashboard views ---------------------------------------------------

    def all(self, now: float) -> List[NodeRecord]:
        """Every node, sorted alert-first then by name."""
        return sorted(
            self.nodes.values(),
            key=lambda r: (_STATUS_RANK.get(r.status(now), 3), r.name.lower()))

    def visible(self, now: float, status: Optional[str] = None,
                search: str = "") -> List[NodeRecord]:
        result = []
        for rec in self.all(now):
            if status and rec.status(now) != status:
                continue
            if search and search.lower() not in rec.name.lower():
                continue
            result.append(rec)
        return result

    def summary(self, now: float) -> Dict[str, int]:
        counts = {"ok": 0, "warn": 0, "alert": 0, "unknown": 0}
        for rec in self.nodes.values():
            counts[rec.status(now)] = counts.get(rec.status(now), 0) + 1
        return counts
