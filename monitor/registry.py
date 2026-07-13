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
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from monitor.health_beacon import HealthBeacon, beacon_status, decode
from monitor.health_poll import PollResult
from monitor.http_status import NodeStatus
from monitor.geo import navigation_links
from ui import theme

#: Not heard for longer than this -> red (matches the Monitor spec).
STALE_ALERT_HOURS = theme.NOT_HEARD_ALERT_HOURS  # 6

_BEACON_RE = re.compile(
    r"\[HealthBeacon\][^\n]*dst=([0-9a-fA-F]+)[^\n]*data=([0-9a-fA-F]+)")

_STATUS_RANK = {"alert": 0, "warn": 1, "ok": 2, "unknown": 3}


def version_tuple(v: str):
    """Parse a dotted version ("0.6.2") into a comparable int tuple."""
    out = []
    for part in str(v).split("."):
        m = re.match(r"\d+", part)
        out.append(int(m.group()) if m else 0)
    return tuple(out)


@dataclass
class CommissionEvent:
    """One entry in a node's provisioning history / field log."""
    at: float          # epoch seconds
    kind: str          # build | repair | fix | onboard | note | ...
    summary: str
    operator: str = "operator"


@dataclass
class NodeRecord:
    dst_hash: str
    name: str = ""
    location: str = ""
    node_type: str = "rtnode2400"          # "rtnode2400" | "pi"
    latest_beacon: Optional[HealthBeacon] = None
    latest_http: Optional[NodeStatus] = None   # last HTTP /status poll (LAN)
    last_seen: Optional[float] = None       # epoch seconds
    lat: Optional[float] = None             # exact coords (from birth cert)
    lon: Optional[float] = None
    notes: List[str] = field(default_factory=list)
    events: List[CommissionEvent] = field(default_factory=list)

    @property
    def firmware_version(self) -> Optional[str]:
        if self.latest_http and self.latest_http.firmware_version:
            return self.latest_http.firmware_version
        return self.latest_beacon.firmware_version if self.latest_beacon else None

    def has_location(self) -> bool:
        return self.lat is not None and self.lon is not None

    def navigation(self) -> Optional[dict]:
        """Turn-by-turn deep links to the node (from its exact birth-cert
        coordinates), or ``None`` if no location is on file."""
        if not self.has_location():
            return None
        return navigation_links(self.lat, self.lon)

    def needs_firmware_update(self, latest: str) -> bool:
        fw = self.firmware_version
        if fw is None:
            return False
        return version_tuple(fw) < version_tuple(latest)

    def last_seen_hours(self, now: float) -> Optional[float]:
        if self.last_seen is None:
            return None
        return (now - self.last_seen) / 3600.0

    def status(self, now: float) -> str:
        if self.last_seen is None:
            return "unknown"
        if (now - self.last_seen) / 3600.0 > STALE_ALERT_HOURS:
            return "alert"                  # not heard -> red, regardless
        # Prefer the richer HTTP /status (has an explicit faults array) when a
        # node is LAN-reachable; fall back to the mesh beacon.
        if self.latest_http is not None and self.latest_http.reachable:
            return self.latest_http.status
        if self.latest_beacon is not None:
            return beacon_status(self.latest_beacon)
        return "unknown"


class NodeRegistry:
    def __init__(self):
        self.nodes: Dict[str, NodeRecord] = {}

    def register(self, dst_hash: str, name: str = "", location: str = "",
                 node_type: str = "rtnode2400", lat: Optional[float] = None,
                 lon: Optional[float] = None) -> NodeRecord:
        """Create or update a node's static metadata (from the birth cert)."""
        rec = self.nodes.get(dst_hash)
        if rec is None:
            rec = NodeRecord(dst_hash=dst_hash, name=name, location=location,
                             node_type=node_type, lat=lat, lon=lon)
            self.nodes[dst_hash] = rec
        else:
            if name:
                rec.name = name
            if location:
                rec.location = location
            rec.node_type = node_type
            if lat is not None:
                rec.lat = lat
            if lon is not None:
                rec.lon = lon
        return rec

    def register_from_birth_certificate(self, cert: dict, name: str = "",
                                        now: float = 0.0,
                                        operator: str = "operator"
                                        ) -> Optional[NodeRecord]:
        """Register a freshly-built node from its birth certificate: exact
        coordinates, and a 'build' entry in the commissioning log."""
        dst = cert.get("identity_hash")
        if not dst:
            return None
        loc = cert.get("location") or {}
        rec = self.register(dst, name=name, node_type="rtnode2400",
                            lat=loc.get("lat"), lon=loc.get("lon"))
        self.log_event(
            dst, "build",
            f"Provisioned {cert.get('board', '')} fw {cert.get('firmware', '')}",
            now, operator)
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

    def record_http_status(self, key: str, status: NodeStatus,
                           now: float) -> NodeRecord:
        """Fold in an HTTP ``/status`` poll for a LAN-reachable node, keyed by
        *key* (the node's dst_hash for a known node, or a synthetic id for a
        discovered one). A reachable poll refreshes last_seen + the health;
        an unreachable one changes nothing (staleness takes it red on its own).
        Auto-registers a never-seen node and adopts its node_name."""
        rec = self.nodes.get(key) or self.register(key)
        if status.reachable:
            rec.latest_http = status
            rec.last_seen = now
            if status.node_name and not rec.name:
                rec.name = status.node_name
        return rec

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

    # -- commissioning log / field notes / firmware ------------------------

    def add_note(self, dst_hash: str, note: str, now: float,
                 operator: str = "operator") -> NodeRecord:
        rec = self.nodes.get(dst_hash) or self.register(dst_hash)
        rec.notes.append(note)
        rec.events.append(CommissionEvent(now, "note", note, operator))
        return rec

    def log_event(self, dst_hash: str, kind: str, summary: str, now: float,
                  operator: str = "operator") -> NodeRecord:
        rec = self.nodes.get(dst_hash) or self.register(dst_hash)
        rec.events.append(CommissionEvent(now, kind, summary, operator))
        return rec

    def nodes_needing_update(self, latest: str) -> List[NodeRecord]:
        return [r for r in self.nodes.values()
                if r.needs_firmware_update(latest)]

    # -- persistence (the monitoring DB the Clone Tool copies) -------------

    def to_dict(self) -> dict:
        return {"nodes": [
            {
                "dst_hash": r.dst_hash,
                "name": r.name,
                "location": r.location,
                "node_type": r.node_type,
                "last_seen": r.last_seen,
                "lat": r.lat,
                "lon": r.lon,
                "notes": list(r.notes),
                "events": [
                    {"at": e.at, "kind": e.kind, "summary": e.summary,
                     "operator": e.operator}
                    for e in r.events
                ],
                "latest_beacon": (r.latest_beacon.to_bytes().hex()
                                  if r.latest_beacon else None),
            }
            for r in self.nodes.values()
        ]}

    @classmethod
    def from_dict(cls, data: dict) -> "NodeRegistry":
        reg = cls()
        for n in data.get("nodes", []):
            rec = NodeRecord(
                dst_hash=n["dst_hash"],
                name=n.get("name", ""),
                location=n.get("location", ""),
                node_type=n.get("node_type", "rtnode2400"),
                last_seen=n.get("last_seen"),
                lat=n.get("lat"),
                lon=n.get("lon"),
            )
            rec.notes = list(n.get("notes", []))
            rec.events = [CommissionEvent(**e) for e in n.get("events", [])]
            lb = n.get("latest_beacon")
            if lb:
                rec.latest_beacon = decode(bytes.fromhex(lb))
            reg.nodes[rec.dst_hash] = rec
        return reg
