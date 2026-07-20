"""Node registry — the VITALS mode backend.

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


def _capabilities(members) -> dict:
    """{lora, wifi, bluetooth, internet}: True = seen working, False = the
    node itself reports it down, None = unknowable from here (renders grey)."""
    lora = wifi = internet = None
    for r in members:
        iface = r.mesh_interface or ""
        if "RNode" in iface:
            lora = True                       # heard over the radio: proof
        if "TCPInterface" in iface and internet is None:
            internet = True                   # reached via an internet link
        http, beacon = r.latest_http, r.latest_beacon
        if http is not None and http.reachable:
            if http.lora_online:               # the node self-reports its LoRa —
                lora = True                     # honest LIVE detection, not a guess
            wifi = bool(http.wifi_connected)
            internet = bool(http.tcp_backbone_connected) or (internet is True)
        elif beacon is not None and wifi is None:
            wifi = bool(beacon.wifi_up)
            if internet is None:
                internet = bool(beacon.tcp_backbone_up)
    from monitor.kin_roster import DEFAULT_LINKS
    caps = {"lora": lora, "wifi": wifi, "bluetooth": None, "internet": internet}
    # KIN nodes declare the interfaces they physically have — a Pi 3A+ propagation
    # node has wifi + bluetooth (internet rides that wifi); an RTNode-2400 is always
    # a LoRa node. The medic only HEARS one interface, so without this they'd read
    # single-transport. Sources: an explicit roster ``links`` entry, then the
    # node-type default (kin only — never guess for an anonymous neighbour). A live
    # reading (True/False above) always wins over a declared capability.
    for r in members:
        declared = dict(getattr(r, "links", None) or {})
        if r.provenance == "kin":
            for k, v in DEFAULT_LINKS.get(r.node_type, {}).items():
                declared.setdefault(k, v)
        for k, v in declared.items():
            if v and caps.get(k) is None:
                caps[k] = True
    return caps


def _printable_name(app_data) -> str:
    """A human display name from announce app_data, if one is legible (LXMF
    prefixes a length byte before a UTF-8 name). Empty string otherwise."""
    if not app_data:
        return ""
    try:
        text = bytes(app_data).decode("utf-8", "ignore")
    except Exception:
        return ""
    clean = "".join(c for c in text if c.isprintable()).strip()
    return clean if 2 <= len(clean) <= 32 else ""


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
    mesh_hops: Optional[int] = None            # reachable via the LoRa mesh
    mesh_interface: str = ""
    last_seen: Optional[float] = None       # epoch seconds
    lat: Optional[float] = None             # exact coords (from birth cert)
    lon: Optional[float] = None
    identity_hash: Optional[str] = None     # groups aspect-destinations per DEVICE
    announced_name: str = ""                # name a neighbour announces (e.g. LXMF)
    links: Optional[dict] = None            # KIN-declared interfaces the node HAS
                                            # ({lora,wifi,bluetooth,internet}: True)
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

    def signal_dbm(self) -> Optional[int]:
        """Best available WiFi signal — HTTP /status first, then the beacon."""
        if self.latest_http is not None and self.latest_http.wifi_rssi_dbm is not None:
            return self.latest_http.wifi_rssi_dbm
        if self.latest_beacon is not None:
            return self.latest_beacon.wifi_rssi_dbm
        return None

    @property
    def provenance(self) -> str:
        """Interim provenance (full tiers are #54): a record that has spoken our
        protocols (beacon / HTTP status) or was named/located by an operator is
        KIN; a bare mesh-heard destination hash is a NEIGHBOUR."""
        ours = (self.latest_beacon is not None or self.latest_http is not None
                or bool(self.name) or self.lat is not None)
        return "kin" if ours else "neighbour"

    def to_dashboard(self, now: float) -> dict:
        """The node dict the VITALS screen (ui.screens.vitals_screen) renders.
        Pure + testable; the Kivy view just reads these keys. Honest: no
        invented numbers — unknown signal/battery stay None and the screen
        hides them; a bare mesh destination renders as a grey Neighbour, not a
        healthy green RTNode."""
        lsh = self.last_seen_hours(now)
        sig = self.signal_dbm()
        neighbour = self.provenance == "neighbour"
        status = self.status(now)
        if neighbour and status == "ok":
            status = "unknown"           # heard != healthy; we know nothing yet
        return {
            "name": self.name or (
                (self.announced_name or f"Neighbour {self.dst_hash[:8]}")
                if neighbour else "(unnamed)"),
            "location": self.location or ("heard on the mesh" if neighbour else ""),
            "status": status,
            "type": self.node_type,
            "provenance": self.provenance,
            "signal_dbm": sig,                      # None = never measured
            "last_seen_hours": lsh if lsh is not None else 0.0,
            "battery_pct": None,          # no node type reports battery yet
            "powered_by": "battery",
        }

    def status(self, now: float) -> str:
        if self.last_seen is None:
            return "unknown"
        if (now - self.last_seen) / 3600.0 > STALE_ALERT_HOURS:
            return "alert"                  # not heard -> red, regardless
        # Prefer the richer HTTP /status (has an explicit faults array) when a
        # node is LAN-reachable; then the mesh beacon; then bare mesh
        # reachability (in the path table = reachable, health unknown -> ok).
        if self.latest_http is not None and self.latest_http.reachable:
            return self.latest_http.status
        if self.latest_beacon is not None:
            return beacon_status(self.latest_beacon)
        if self.mesh_hops is not None:
            return "ok"                     # reachable over the mesh
        return "unknown"


class NodeRegistry:
    def __init__(self):
        self.nodes: Dict[str, NodeRecord] = {}
        from monitor.history import NodeHistory
        self.history = NodeHistory()    # per-node time series (VITALS "History")
        #: The medic's own fleet, keyed by RNS hash (monitor.kin_roster). Any
        #: record whose hash is in here is authoritatively named/typed/located as
        #: KIN — even a plain propagation Pi the medic can't hear directly.
        self.kin_roster: Dict[str, dict] = {}

    def set_kin_roster(self, roster: dict) -> None:
        """Load the medic's fleet roster. Seeds a NAMED, LOCATED record for every
        entry (so each fleet node shows in VITALS as kin and on the map at its
        deployed spot, even before it's heard) and re-applies it to any record
        already present."""
        self.kin_roster = dict(roster or {})
        for h in self.kin_roster:
            rec = self.nodes.get(h) or self.register(h)
            self._apply_kin(rec)

    def _apply_kin(self, rec: NodeRecord) -> None:
        """If this record is one of the medic's own nodes, stamp its roster name,
        type, and deployed location — making it kin (named) and map-visible."""
        entry = self.kin_roster.get(rec.dst_hash)
        if not entry:
            return
        if entry.get("name"):
            rec.name = entry["name"]
        if entry.get("type"):
            rec.node_type = entry["type"]
        if entry.get("lat") is not None:
            rec.lat = entry["lat"]
        if entry.get("lon") is not None:
            rec.lon = entry["lon"]
        if entry.get("links"):
            rec.links = entry["links"]

    def ingest_relay(self, via_hash: str, interface: str, now: float) -> NodeRecord:
        """Surface the medic's DIRECT next-hop relay (a ``via`` in the path table)
        as a node. A via is the medic's 1-hop LoRa neighbour that the whole mesh
        routes through — e.g. EVERYWHERE — yet it's never a destination in rnpath,
        so without this it stays invisible. Marks it reachable (1 hop) and applies
        the kin roster (names it if it's ours)."""
        rec = self.nodes.get(via_hash) or self.register(via_hash)
        rec.mesh_hops = 1
        if interface:
            rec.mesh_interface = interface
        rec.last_seen = now
        self._apply_kin(rec)
        return rec

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
        self._apply_kin(rec)
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
        from monitor.history import HistoryPoint
        self.history.append(dst_hash, HistoryPoint(
            t=now, rssi=beacon.wifi_rssi_dbm, uptime_s=beacon.uptime_s))
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
                        now: float,
                        identity_hash: Optional[str] = None) -> Optional[NodeRecord]:
        """Adapter for an RNS announce handler. A live handler does:

            def received_announce(self, destination_hash, identity, app_data):
                registry.ingest_announce(destination_hash, app_data, time.time())

        *dst_hash* is the raw destination-hash bytes RNS provides. Every
        announce marks the node HEARD (honest last-seen for neighbours) and
        records the announced identity (groups a device's aspect-destinations)
        and any announced display name. Beacon payloads additionally ingest
        as health data.
        """
        h = dst_hash.hex() if isinstance(dst_hash, (bytes, bytearray)) else str(dst_hash)
        try:
            beacon = decode(app_data)
        except (ValueError, TypeError):
            beacon = None
        if beacon is not None:
            rec = self.ingest(h, beacon, now)
        else:
            rec = self.nodes.get(h) or self.register(h)
            rec.last_seen = now
        if identity_hash:
            rec.identity_hash = identity_hash
        name = _printable_name(app_data)
        if name and not rec.announced_name:
            rec.announced_name = name
        return rec

    def ingest_mesh(self, node, now: float) -> NodeRecord:
        """Fold a mesh path (a monitor.mesh.MeshNode) into the registry, keyed by
        its destination hash — the same key birthed/HTTP nodes use. Records
        reachability (hops, interface) and refreshes last_seen; auto-registers an
        unknown destination (its name stays the hash until a birth cert names it).
        """
        rec = self.nodes.get(node.dst_hash) or self.register(node.dst_hash)
        rec.mesh_hops = node.hops
        rec.mesh_interface = node.interface
        rec.last_seen = now
        return rec

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
        """Every node: the operator's OWN nodes first (kin above neighbours),
        alert-first within each group, then by name."""
        return sorted(
            self.nodes.values(),
            key=lambda r: (r.provenance != "kin",
                           _STATUS_RANK.get(r.status(now), 3), r.name.lower()))

    def devices(self, now: float) -> List[dict]:
        """The CONSOLIDATED dashboard: one row per physical device. Destinations
        that announced the same identity collapse into one entry (a phone's
        chat + files aspects are one phone), led by its best-known record.
        Each row adds ``aspects`` (how many destinations merged) and
        ``capabilities``: {lora, wifi, bluetooth, internet} — True (seen
        working), False (reported down), None (no way to know yet)."""
        groups: Dict[str, List[NodeRecord]] = {}
        for rec in self.nodes.values():
            groups.setdefault(rec.identity_hash or rec.dst_hash, []).append(rec)
        out = []
        for members in groups.values():
            primary = sorted(
                members,
                key=lambda r: (r.provenance != "kin", not r.name,
                               _STATUS_RANK.get(r.status(now), 3)))[0]
            d = primary.to_dashboard(now)
            seen = [r.last_seen for r in members if r.last_seen is not None]
            if seen:
                d["last_seen_hours"] = max(0.0, (now - max(seen)) / 3600.0)
            d["aspects"] = len(members)
            d["capabilities"] = _capabilities(members)
            out.append(d)
        return sorted(out, key=lambda d: (d["provenance"] != "kin",
                                          _STATUS_RANK.get(d["status"], 3),
                                          d["name"].lower()))

    def located_nodes(self, now: float) -> List[dict]:
        """Every node with a known location, for SCAN mode — each as
        ``{lat, lon, name, status}``. Nodes without birth-cert coordinates are
        omitted (nothing to plot). Sorted by name for stable rendering."""
        out = []
        for rec in sorted(self.nodes.values(), key=lambda r: r.name.lower()):
            if rec.has_location():
                out.append({"lat": rec.lat, "lon": rec.lon,
                            "name": rec.name or "(unnamed)",
                            "status": rec.status(now)})
        return out

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

    # -- persistence (the monitoring DB MITOSIS copies) -------------

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
                "identity_hash": r.identity_hash,
                "announced_name": r.announced_name,
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
        ], "history": self.history.to_dict()}

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
                identity_hash=n.get("identity_hash"),
                announced_name=n.get("announced_name", ""),
            )
            rec.notes = list(n.get("notes", []))
            rec.events = [CommissionEvent(**e) for e in n.get("events", [])]
            lb = n.get("latest_beacon")
            if lb:
                rec.latest_beacon = decode(bytes.fromhex(lb))
            reg.nodes[rec.dst_hash] = rec
        from monitor.history import NodeHistory
        reg.history = NodeHistory.from_dict(data.get("history", {}))
        return reg
