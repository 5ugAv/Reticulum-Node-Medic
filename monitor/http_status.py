"""HTTP ``/status`` poller for LAN-reachable Type-B (RTNode-2400) nodes.

RTNode-2400 firmware serves a rich JSON health endpoint at ``GET /status`` on
port 80 (verified on live nodes MEDIC-TEST and FAITH). When a node is reachable
over the LAN this is a far better signal than the 14-byte mesh health beacon
(``monitor.health_beacon``): it is instant, costs no LoRa airtime, and carries an
explicit ``faults`` array plus wifi_ip and heap detail. The mesh beacon remains
the fallback for LoRa-only / not-on-the-LAN nodes.

The status colour (ok / warn / alert) mirrors ``health_beacon.beacon_status`` so
a node reported via HTTP looks the same as one reported via the mesh beacon. All
HTTP I/O is injected, so this is unit-testable without a live node.

Real capture (healthy node), for reference:
    {"fork":"RTNode","fw_version":"0.6.2","board":"heltec_v4","board_model":63,
     "node_name":"MEDIC-TEST","wifi_connected":true,"wifi_rssi":-64,
     "wifi_ip":"192.168.1.180","lora_online":true,"local_tcp_server_up":true,
     "tcp_backbone_connected":false,"wdt_armed":true,"uptime_ms":83973029,
     "reset_reason":"unknown","faults":[]}
"""

from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Tuple

from monitor.health_beacon import WIFI_WARN_DBM

STATUS_PATH = "/status"
STATUS_PORT = 80

#: (status_code, body) — an injected HTTP GET so tests need no network.
Getter = Callable[[str, float], Tuple[int, str]]


@dataclass
class NodeStatus:
    reachable: bool
    status: str                       # ok | warn | alert | unreachable
    node_name: str = ""
    board: str = ""
    firmware_version: str = ""
    wifi_connected: bool = False
    wifi_rssi_dbm: Optional[int] = None
    wifi_ip: str = ""
    lora_online: bool = False
    local_tcp_server_up: bool = False
    tcp_backbone_connected: bool = False
    wdt_armed: bool = False
    uptime_s: int = 0
    reset_reason: str = ""
    faults: List[str] = field(default_factory=list)
    raw: dict = field(default_factory=dict)


def status_colour(d: dict) -> str:
    """Map a ``/status`` dict to a Monitor colour (ok / warn / alert).

    Mirrors ``health_beacon.beacon_status`` but uses the endpoint's explicit
    ``faults`` array. Missing fields default to the healthy interpretation so a
    firmware that omits a key isn't falsely alarmed. Weak WiFi RSSI only ever
    escalates to WARN — never alert; RED is reserved for faults / LoRa down.
    """
    if d.get("faults"):
        return "alert"
    if not d.get("lora_online", True):
        return "alert"
    status = "ok"
    if d.get("wifi_connected"):
        rssi = d.get("wifi_rssi")
        if isinstance(rssi, (int, float)) and rssi <= WIFI_WARN_DBM:
            status = "warn"
    if not d.get("wdt_armed", True) and status == "ok":
        status = "warn"
    return status


def parse_status(d: dict) -> NodeStatus:
    """Parse a decoded ``/status`` dict into a NodeStatus."""
    rssi = d.get("wifi_rssi")
    return NodeStatus(
        reachable=True,
        status=status_colour(d),
        node_name=d.get("node_name", ""),
        board=d.get("board", ""),
        firmware_version=d.get("fw_version", ""),
        wifi_connected=bool(d.get("wifi_connected", False)),
        wifi_rssi_dbm=int(rssi) if isinstance(rssi, (int, float)) else None,
        wifi_ip=d.get("wifi_ip", ""),
        lora_online=bool(d.get("lora_online", False)),
        local_tcp_server_up=bool(d.get("local_tcp_server_up", False)),
        tcp_backbone_connected=bool(d.get("tcp_backbone_connected", False)),
        wdt_armed=bool(d.get("wdt_armed", False)),
        uptime_s=int(d.get("uptime_ms", 0)) // 1000,
        reset_reason=d.get("reset_reason", ""),
        faults=list(d.get("faults", []) or []),
        raw=d,
    )


# A node's /status is always a direct LAN request — never route it through a
# proxy. macOS's default opener can apply a system proxy even when getproxies()
# looks empty (verified: default urlopen failed, ProxyHandler({}) succeeded).
_NO_PROXY_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _default_get(url: str, timeout: float) -> Tuple[int, str]:
    with _NO_PROXY_OPENER.open(url, timeout=timeout) as resp:
        return (resp.status, resp.read().decode("utf-8", "replace"))


_UNREACHABLE = NodeStatus(reachable=False, status="unreachable")


def poll_status(host: str, get: Getter = _default_get,
                port: int = STATUS_PORT, timeout: float = 6.0) -> NodeStatus:
    """Fetch + parse a node's ``/status``. Any failure (unreachable, non-200,
    bad JSON) returns an ``unreachable`` NodeStatus rather than raising."""
    hostport = host if port == 80 else f"{host}:{port}"
    url = f"http://{hostport}{STATUS_PATH}"
    try:
        code, body = get(url, timeout)
    except Exception:
        return _UNREACHABLE
    if code != 200:
        return _UNREACHABLE
    try:
        data = json.loads(body)
    except ValueError:
        return _UNREACHABLE
    if not isinstance(data, dict):
        return _UNREACHABLE
    return parse_status(data)


def to_monitor_node(ns: NodeStatus, location: str = "") -> dict:
    """Adapt a NodeStatus to the Monitor screen's node dict shape."""
    return {
        "name": ns.node_name or "(unnamed)",
        "location": location,
        "status": ns.status if ns.reachable else "alert",
        "type": "rtnode2400",
        "signal_dbm": ns.wifi_rssi_dbm if ns.wifi_rssi_dbm is not None else -100,
        "last_seen_hours": 0.0 if ns.reachable else 999.0,
        "powered_by": "unknown",
    }
