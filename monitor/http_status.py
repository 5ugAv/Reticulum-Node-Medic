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
import math
import urllib.request
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Tuple

from monitor.health_beacon import WIFI_WARN_DBM

STATUS_PATH = "/status"
STATUS_PORT = 80

#: What a Pi propagation node puts in ``fork``. It is NOT RTNode firmware and
#: must not claim to be — it is a Python service (:mod:`monitor.pi_status_server`)
#: speaking the same JSON so one parser serves both kinds of node.
PI_FORK = "RNM-Pi"

#: Strings that mark a ``/status`` body as belonging to a node this tool knows.
#: The LAN sweep (:mod:`monitor.discovery`) greps for these, so a new kind of
#: node becomes discoverable by adding its fork here and nowhere else.
DISCOVERY_MARKERS = ("RTNode", PI_FORK)

#: (status_code, body) — an injected HTTP GET so tests need no network.
Getter = Callable[[str, float], Tuple[int, str]]

#: Hard cap on a ``/status`` body. A real RTNode reply is a few hundred bytes;
#: anything past this is a broken or hostile node on the LAN trying to make the
#: medic buffer megabytes off a single poll. Read is truncated here, not trusted.
MAX_STATUS_BYTES = 65536
#: Bounds on the ``faults`` array — a node on the untrusted LAN does not get to
#: hand the sweep an unbounded list of unbounded strings to carry per cycle.
MAX_FAULTS = 32
MAX_FAULT_LEN = 200


def _safe_int(value, default: int = 0) -> int:
    """``int()`` that never raises on a hostile ``/status`` field.

    The body is JSON off the LAN, so ``uptime_ms`` may arrive as a string, a
    list, or null. int() on any of those throws — and this feeds a sweep that
    must survive one bad node — so coerce defensively and fall back instead.

    Non-finite floats are the sharp edge: Python's json.loads accepts ``Infinity``
    and ``NaN`` by default, and ``int(float('inf'))`` raises OverflowError while
    ``int(float('nan'))`` raises ValueError. A "never raises" contract has to
    cover them explicitly, so reject anything that isn't finite."""
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if math.isfinite(value) else default
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return default
    return default


def _safe_str(value) -> str:
    """A str field, or ``""`` if the node sent something that isn't one."""
    return value if isinstance(value, str) else ""


def _safe_faults(value) -> List[str]:
    """The ``faults`` array as a bounded list of bounded strings.

    Only a genuine list of strings counts. A non-list, or list of dicts/ints,
    yields no faults rather than raising or smuggling junk onto the dashboard."""
    if not isinstance(value, list):
        return []
    out: List[str] = []
    for item in value:
        if isinstance(item, str):
            out.append(item[:MAX_FAULT_LEN])
            if len(out) >= MAX_FAULTS:
                break
    return out


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
    #: Did the node actually SAY anything about each link, or is the False above
    #: just this dataclass's default?
    #:
    #: An RTNode-2400 sends all three keys on every request, so for it these are
    #: always True and nothing changes. A Pi propagation node OMITS a key it
    #: could not read (see monitor.pi_status_server), and the difference matters
    #: at the far end: ``registry._capabilities`` turns a False into "the node
    #: reports it down", which VITALS draws in amber and an operator reads as
    #: something to go and fix. A reading nobody could take is not a fault, and
    #: it must render grey. Default True so every existing construction of this
    #: dataclass keeps the meaning it had.
    lora_known: bool = True
    wifi_known: bool = True
    backbone_known: bool = True


def status_colour(d: dict) -> str:
    """Map a ``/status`` dict to a Monitor colour (ok / warn / alert).

    Mirrors ``health_beacon.beacon_status`` but uses the endpoint's explicit
    ``faults`` array. Missing fields default to the healthy interpretation so a
    firmware that omits a key isn't falsely alarmed. Weak WiFi RSSI only ever
    escalates to WARN — never alert; RED is reserved for faults / LoRa down.
    """
    if _safe_faults(d.get("faults")):
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
    """Parse a decoded ``/status`` dict into a NodeStatus.

    Every field is coerced defensively: this dict is JSON straight off the LAN,
    so a node — broken or hostile — can put a string where a number belongs, a
    dict where a string belongs, or an oversize ``faults`` list. None of that may
    raise here, because :func:`poll_status` feeds a sweep of many nodes and one
    bad reply must become "couldn't read this node", never abort the others.
    """
    rssi = d.get("wifi_rssi")
    return NodeStatus(
        reachable=True,
        status=status_colour(d),
        node_name=_safe_str(d.get("node_name")),
        board=_safe_str(d.get("board")),
        firmware_version=_safe_str(d.get("fw_version")),
        wifi_connected=bool(d.get("wifi_connected", False)),
        wifi_rssi_dbm=int(rssi) if isinstance(rssi, (int, float))
                      and not isinstance(rssi, bool) and math.isfinite(rssi)
                      else None,   # isfinite: json accepts Infinity/NaN; int() on them raises
        wifi_ip=_safe_str(d.get("wifi_ip")),
        lora_online=bool(d.get("lora_online", False)),
        local_tcp_server_up=bool(d.get("local_tcp_server_up", False)),
        tcp_backbone_connected=bool(d.get("tcp_backbone_connected", False)),
        wdt_armed=bool(d.get("wdt_armed", False)),
        uptime_s=_safe_int(d.get("uptime_ms", 0)) // 1000,
        reset_reason=_safe_str(d.get("reset_reason")),
        faults=_safe_faults(d.get("faults")),
        raw=d,
        lora_known="lora_online" in d,
        wifi_known="wifi_connected" in d,
        backbone_known="tcp_backbone_connected" in d,
    )


# A node's /status is always a direct LAN request — never route it through a
# proxy. macOS's default opener can apply a system proxy even when getproxies()
# looks empty (verified: default urlopen failed, ProxyHandler({}) succeeded).
_NO_PROXY_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _default_get(url: str, timeout: float) -> Tuple[int, str]:
    with _NO_PROXY_OPENER.open(url, timeout=timeout) as resp:
        # Cap the read: an unbounded resp.read() lets one node on the LAN stream
        # megabytes into the medic's memory off a single poll. A real /status is
        # a few hundred bytes; MAX_STATUS_BYTES is already far more than enough,
        # and a body that overruns it parses as junk and is treated unreachable.
        return (resp.status,
                resp.read(MAX_STATUS_BYTES).decode("utf-8", "replace"))


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
    # parse_status is defensive, but it stays INSIDE the guard: it is the last
    # place untrusted LAN bytes are shaped, and the whole contract of this
    # function is "never raise — a bad node is unreachable, not a crash". One
    # hostile /status must never propagate past the sweep in service.poll_cycle.
    try:
        return parse_status(data)
    except Exception:                          # noqa: BLE001 - see above
        return _UNREACHABLE


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
