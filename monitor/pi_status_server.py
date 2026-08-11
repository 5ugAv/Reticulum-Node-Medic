"""HTTP ``/status`` for a **Pi + RNode propagation node**.

An RTNode-2400 serves this endpoint from its own C++ firmware, and the medic
already knows how to find it (:mod:`monitor.discovery`), read it
(:mod:`monitor.http_status`) and fold it into VITALS. A Pi propagation node
served NOTHING over HTTP, and that is the whole reason a birthed Pi could never
show more than a LoRa chip.

Read ``registry._capabilities`` for why: the WIFI and INTERNET states come ONLY
from an HTTP ``/status`` poll or a decoded health beacon. The beacon is twenty
bytes on a six-hour heartbeat, so until one lands there is no evidence at all
and the chips stay grey — which is the tool being honest, not the tool being
broken. The fix for grey is never to assume; it is to give the medic something
to read. This is that something, and unlike the beacon it costs no airtime.

SAME CONTRACT, NOT A SECOND ONE. Every key here is a key an RTNode-2400 already
sends, so ``http_status.parse_status`` remains the only parser either kind of
node needs.

THE ONE DIFFERENCE IS WHAT HAPPENS WHEN A READING CANNOT BE TAKEN. This server
OMITS the key instead of sending a plausible false. An omitted key arrives at
the far end as "not reported" (``NodeStatus.wifi_known`` and friends) and draws
grey; a ``false`` arrives as "the node says this is down" and draws amber, which
sends an operator to fix something that may be perfectly well. Those are two
different sentences and this module never says the second when it means the
first.

Shape mirrors :mod:`monitor.pi_health_reporter` — a PURE core (``build_status``
plus the ``/proc`` and tool-output parsers) that takes injected readings and is
fully unit-testable with no hardware, and a thin transport layer whose imports
are lazy so the module stays importable in CI with no Pi under it.

Runs as its own systemd service, deliberately apart from the health reporter.
The reporter needs RNS and stops when RNS does; the endpoint whose job is to
answer "what is wrong with you" has to survive exactly the moments when the mesh
side is unhappy.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

from monitor.http_status import PI_FORK, STATUS_PATH, STATUS_PORT
from monitor.pi_health_reporter import (
    DISK_FAULT_PCT, disk_used_percent, parse_proc_uptime)

#: Version of THIS reporter, sent as ``fw_version``. A Pi node has no RNode
#: firmware version of its own to quote, and quoting the radio's would be a
#: claim about a board that may not even be attached yet.
STATUS_VERSION = "1.0.0"

#: A Pi propagation node runs rnsd, which owns the radio itself — there is no
#: KISS TCP server for a client to attach to, the way an RTNode-2400 has. False
#: is a complete answer here, not a default standing in for a missing reading.
LOCAL_TCP_SERVER_UP = False


@dataclass
class PiStatusInputs:
    """Everything ``build_status`` needs, all injected so the builder is pure.

    ``None`` means THE READING COULD NOT BE TAKEN and the key is left out of the
    JSON entirely. It does not mean "off". Anything that has a real value at the
    moment the request is served carries that value, whatever it is.
    """
    node_name: str = ""
    board: str = ""
    firmware_version: str = STATUS_VERSION
    uptime_s: Optional[int] = None
    wifi_connected: Optional[bool] = None
    wifi_rssi_dbm: Optional[int] = None
    wifi_ip: str = ""
    lora_online: Optional[bool] = None
    tcp_backbone_connected: Optional[bool] = None
    wdt_armed: Optional[bool] = None
    faults: List[str] = field(default_factory=list)


def build_status(inp: PiStatusInputs) -> dict:
    """The ``/status`` dict a Pi propagation node serves. Pure — no OS, no RNS.

    Keys whose reading is ``None`` are ABSENT rather than false. See the module
    docstring: an omitted key is "I could not tell you", a false one is "it is
    down", and the medic renders them differently because they are different.
    """
    d: dict = {
        "fork": PI_FORK,
        "fw_version": inp.firmware_version or STATUS_VERSION,
        "board": inp.board,
        "node_name": inp.node_name,
        "local_tcp_server_up": LOCAL_TCP_SERVER_UP,
        "faults": list(inp.faults),
    }
    if inp.uptime_s is not None:
        d["uptime_ms"] = max(0, int(inp.uptime_s)) * 1000
    if inp.wifi_connected is not None:
        d["wifi_connected"] = bool(inp.wifi_connected)
    if inp.wifi_rssi_dbm is not None:
        d["wifi_rssi"] = int(inp.wifi_rssi_dbm)
    if inp.wifi_ip:
        d["wifi_ip"] = inp.wifi_ip
    if inp.lora_online is not None:
        d["lora_online"] = bool(inp.lora_online)
    if inp.tcp_backbone_connected is not None:
        d["tcp_backbone_connected"] = bool(inp.tcp_backbone_connected)
    if inp.wdt_armed is not None:
        d["wdt_armed"] = bool(inp.wdt_armed)
    return d


# ---- pure parsers (unit-tested, no hardware) -------------------------------


def parse_proc_net_wireless(text: str) -> Dict[str, Optional[int]]:
    """``/proc/net/wireless`` -> ``{iface: signal_dbm or None}``.

    Two header lines, then one row per registered wireless netdev::

        wlan0: 0000   62.  -48.  -256        0      0 ...

    The columns after the interface are status, link, level, noise; ``level`` is
    the RSSI. Old WEXT drivers report it as an unsigned 0-255 byte instead of
    dBm, and there is no way to tell which from the file — so a level that is
    not negative is returned as ``None`` rather than passed off as a signal.
    A positive "RSSI" is how VITALS once drew a colossal 0 dBm for a node with
    no reading at all (see ``NodeRecord.signal_dbm``), and this is the same trap
    one file earlier.
    """
    out: Dict[str, Optional[int]] = {}
    for line in (text or "").splitlines():
        if ":" not in line:
            continue
        name, _, rest = line.partition(":")
        iface = name.strip()
        # The header rows contain a colon too ("Inter-|", "face |"): a real row
        # is named like a netdev and followed by numbers.
        if not iface or " " in iface or "|" in iface:
            continue
        fields = rest.split()
        level: Optional[int] = None
        if len(fields) >= 3:
            try:
                value = int(float(fields[2].rstrip(".")))
                level = value if value < 0 else None
            except ValueError:
                level = None
        out[iface] = level
    return out


def parse_ipv4_addresses(text: str) -> Dict[str, str]:
    """``ip -4 -o addr show`` -> ``{iface: first IPv4}`` (no prefix length).

    Row shape::

        3: wlan0    inet 192.168.1.42/24 brd 192.168.1.255 scope global ...
    """
    out: Dict[str, str] = {}
    for line in (text or "").splitlines():
        fields = line.split()
        if len(fields) < 4 or "inet" not in fields:
            continue
        iface = fields[1].strip()
        idx = fields.index("inet")
        if idx + 1 >= len(fields):
            continue
        addr = fields[idx + 1].split("/")[0]
        if iface and addr and iface not in out:
            out[iface] = addr
    return out


def rns_interface_state(rnstatus_json: str) -> Tuple[Optional[bool], Optional[bool]]:
    """``(lora_online, tcp_backbone_connected)`` read from ``rnstatus --json``.

    ``rnstatus --json`` is ``{"interfaces": [{"type": ..., "status": bool}, ...]}``
    and each interface carries its own boolean ``status`` — verified against RNS
    1.3.7 on a live node, and already relied on by ``diagnostics.base``. Scraping
    the human output for "Up" is meaningless when the TCP interface is up and the
    radio is not, which is precisely the case worth reporting.

    Both values are ``None`` when rnstatus could not be run or its output could
    not be parsed: rnsd's state is then UNKNOWN, which is not the same as down.
    That distinction cost PROBE its credibility once — it announced the mesh
    stack dead on a medic that was hearing announces at the time, because it had
    only failed to find the tool on PATH (2026-08-11).

    ``tcp_backbone_connected`` is ``None`` when the node has NO TCP interface at
    all. A node that was never configured to reach a backbone is not a node
    whose backbone is down, and reporting false would have every field node in
    the fleet show a permanent amber "Internet: down — the node says so" for a
    thing nobody asked it to have.
    """
    try:
        data = json.loads(rnstatus_json or "")
    except (ValueError, TypeError):
        return None, None
    if not isinstance(data, dict):
        return None, None
    interfaces = data.get("interfaces")
    if not isinstance(interfaces, list):
        return None, None

    lora = False
    tcp: Optional[bool] = None
    for iface in interfaces:
        if not isinstance(iface, dict):
            continue
        kind = str(iface.get("type", ""))
        up = iface.get("status") is True
        if kind == "RNodeInterface":
            lora = lora or up
        elif "TCP" in kind and "Server" not in kind:
            tcp = bool(tcp) or up
    return lora, tcp


def collect_faults(disk_used_pct: Optional[int]) -> List[str]:
    """The faults a Pi propagation node can state from its own readings.

    Deliberately the SAME condition the health beacon raises its fault bit on
    (``pi_health_reporter.DISK_FAULT_PCT``), so a node polled over HTTP and the
    same node heard over the mesh never disagree about whether it is faulty.
    A propagation node that fills its card stops storing-and-forwarding and
    starts corrupting itself, which is the one thing worth going red over.
    """
    if disk_used_pct is not None and disk_used_pct >= DISK_FAULT_PCT:
        return [f"Disk {disk_used_pct}% full — store-and-forward will stop."]
    return []


# ---- thin OS transport (lazy; not imported in tests) -----------------------

#: Where pip --user puts the RNS console scripts. NOT on a systemd service's
#: PATH, and a check that cannot find its tool reports "unknown", never "down"
#: — the scar is recorded in ``self_diagnose.check_rns_responding``.
_TOOL_DIRS = ("~/.local/bin", "/usr/local/bin", "/usr/bin", "/bin")


def tool_path(name: str, dirs=_TOOL_DIRS) -> str:
    """Absolute path to *name*, or "" if it is nowhere we know to look."""
    import os
    for d in dirs:
        p = os.path.join(os.path.expanduser(d), name)
        if os.path.exists(p):
            return p
    return ""


def _read_text(path: str) -> Optional[str]:
    """File contents, or ``None`` if it could not be read (which is a reading in
    its own right — see PiStatusInputs)."""
    try:
        with open(path) as f:
            return f.read()
    except OSError:
        return None


def _run(argv: List[str], timeout: float = 8.0) -> Optional[str]:
    """Run a fixed argv (NO shell) and return stdout, or ``None`` on any failure.

    ``None`` rather than "" on purpose: an empty answer and no answer at all lead
    to different keys in the JSON, and conflating them is how a node ends up
    stating something it never measured.
    """
    import subprocess
    try:
        done = subprocess.run(argv, capture_output=True, text=True,
                              timeout=timeout)
    except Exception:                                          # noqa: BLE001
        return None
    return done.stdout if done.returncode == 0 else None


def read_board() -> str:
    """The Pi's own description of itself, e.g. "Raspberry Pi 3 Model A Plus"."""
    text = _read_text("/proc/device-tree/model") or ""
    return text.replace("\x00", "").strip()


def read_node_name() -> str:
    """The node's hostname, read LIVE on every request.

    Not baked in at install: the build sets the hostname in a later step than
    the one that installs this service, and a node that is renamed later should
    answer to its new name without anyone remembering to reinstall a unit.
    """
    text = _read_text("/etc/hostname") or ""
    return text.strip()


def read_wifi(read_text=_read_text, run=_run):
    """``(connected, rssi_dbm, ip)`` for this node's wireless interface.

    ``connected`` is ``None`` when the node has no wireless stack to ask —
    ``/proc/net/wireless`` absent means the question cannot be answered here.
    When the file IS readable and lists an interface, "connected" means the
    netdev is up AND holds an IPv4: a wlan0 that is up with no address has
    associated with nothing, and a radio that is soft-blocked by rfkill sits at
    operstate down. Both are honestly "not connected", and both are states this
    fleet has actually shipped in.
    """
    wireless = read_text("/proc/net/wireless")
    if wireless is None:
        return None, None, ""
    levels = parse_proc_net_wireless(wireless)
    if not levels:
        # A wireless stack with no registered device: the node genuinely has no
        # wifi up. That is a reading, and it is "no".
        return False, None, ""
    addresses = parse_ipv4_addresses(run(["ip", "-4", "-o", "addr", "show"]) or "")
    for iface, level in levels.items():
        state = (read_text(f"/sys/class/net/{iface}/operstate") or "").strip()
        ip = addresses.get(iface, "")
        if state == "up" and ip:
            return True, level, ip
    return False, None, ""


def read_watchdog(run=_run) -> Optional[bool]:
    """Is the hardware watchdog daemon running? ``None`` if systemd would not say.

    The health beacon hardcodes this True with a note that the node runs under
    systemd with Restart=always. That reasoning is fine for a flag bit; here
    there is a real answer available, so take it instead of repeating a defence.
    """
    out = run(["systemctl", "is-active", "watchdog"])
    if out is None:
        return None
    return out.strip() == "active"


def read_status_inputs(read_text=_read_text, run=_run) -> PiStatusInputs:
    """Take every reading this node can honestly take, right now.

    Best-effort AND honest: a reading that fails leaves its field ``None``, which
    keeps the key out of the JSON rather than filling it with a comfortable
    default. A partial answer is worth serving; an invented one is not.
    """
    import shutil

    uptime_text = read_text("/proc/uptime")
    uptime_s = parse_proc_uptime(uptime_text) if uptime_text is not None else None

    try:
        du = shutil.disk_usage("/")
        disk_pct: Optional[int] = disk_used_percent(du.used, du.total)
    except OSError:
        disk_pct = None

    connected, rssi, ip = read_wifi(read_text, run)

    rnstatus = tool_path("rnstatus")
    lora = backbone = None
    if rnstatus:
        lora, backbone = rns_interface_state(run([rnstatus, "--json"]) or "")

    return PiStatusInputs(
        node_name=read_node_name(),
        board=read_board(),
        firmware_version=STATUS_VERSION,
        uptime_s=uptime_s,
        wifi_connected=connected,
        wifi_rssi_dbm=rssi,
        wifi_ip=ip,
        lora_online=lora,
        tcp_backbone_connected=backbone,
        wdt_armed=read_watchdog(run),
        faults=collect_faults(disk_pct),
    )


def current_status() -> dict:
    """The live ``/status`` dict for this node."""
    return build_status(read_status_inputs())


#: How often the background reading is retaken. Slow enough to leave a frugal
#: node alone, fast enough that the answer is about now.
REFRESH_S = 15.0


class Snapshot:
    """The last reading taken, so a request never waits on a subprocess.

    THE MEDIC FINDS NODES WITH A ``curl -m3`` ACROSS A WHOLE /24. Taking the
    readings inside the request means every discovery sweep pays for an
    ``rnstatus`` round trip on a Pi 3 A+, and a node that answers in four
    seconds is a node the sweep never sees — it looks exactly like a node that
    is not there. That is the failure this whole endpoint exists to end, so it
    must not be reintroduced by the endpoint's own slowness.

    The cost of this is that an answer can be up to ``interval`` seconds old,
    which is true and small and worth saying: nothing here changes in less time
    than that except by something else breaking.

    A refresh that throws keeps the PREVIOUS reading rather than blanking it. A
    momentary failure to run a tool is not news about the node, and serving
    "everything unknown" for one cycle would flicker every chip in VITALS grey.
    """

    def __init__(self, collect: Callable[[], dict] = current_status,
                 interval: float = REFRESH_S):
        import threading
        self._collect = collect
        self._interval = interval
        self._lock = threading.Lock()
        self._value: dict = {}
        self.refresh()                  # never serve before the first reading

    def refresh(self) -> None:
        try:
            value = self._collect()
        except Exception:                                      # noqa: BLE001
            return
        with self._lock:
            self._value = value

    def value(self) -> dict:
        # DEEP copy, not dict(): the reading contains a ``faults`` list, and a
        # shallow copy would hand every caller the same list to append to. A copy
        # that only looks like one is worse than none, because it stops anybody
        # looking for the bug here.
        import copy
        with self._lock:
            return copy.deepcopy(self._value)

    def start(self):
        """Refresh in the background, forever. Returns the daemon thread."""
        import threading
        import time

        def loop():
            while True:
                time.sleep(self._interval)
                self.refresh()

        thread = threading.Thread(target=loop, name="rnm-status-refresh",
                                  daemon=True)
        thread.start()
        return thread


# ---- the server itself -----------------------------------------------------


def make_handler(collect: Callable[[], dict]):
    """A BaseHTTPRequestHandler class serving *collect()* at ``/status``.

    *collect* is injected so the request plumbing can be exercised without a Pi
    underneath it, and so a failure to take a reading cannot take the server
    down with it.

    NOTHING IDENTIFYING GOES OUT OF HERE. No Reticulum identity, no destination
    hash, no coordinates — this answers an unauthenticated request from anyone
    on the LAN, and a wild node must not be traceable to a person or a place
    ([[anonymity-ethos]]). The node's name, board and link states are what an
    RTNode-2400 already publishes and what the operator needs to see.
    """
    from http.server import BaseHTTPRequestHandler

    class StatusHandler(BaseHTTPRequestHandler):
        server_version = "rnm-status/" + STATUS_VERSION
        protocol_version = "HTTP/1.0"      # answer and close; no keep-alive state

        def do_GET(self):                                      # noqa: N802
            if self.path.split("?")[0] != STATUS_PATH:
                self.send_error(404, "Not found")
                return
            try:
                body = json.dumps(collect()).encode()
            except Exception as exc:                           # noqa: BLE001
                # A reading that blew up must not look like a node that is gone:
                # 500 says "I am here and I could not answer", which is a
                # different thing from an unreachable host and reads that way in
                # the journal.
                self.send_error(500, f"Could not read node state: {exc}")
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt, *args):                     # noqa: A003
            # Silence per-request logging. The medic sweeps a whole /24 looking
            # for nodes, so every node on the LAN gets probed on every sweep —
            # logging each one fills the journal of a node whose scarcest
            # resource after airtime is its SD card.
            return

    return StatusHandler


def serve(port: int = STATUS_PORT, collect: Optional[Callable[[], dict]] = None,
          bind: str = "0.0.0.0") -> None:
    """Serve ``/status`` until killed (blocks).

    Binds port 80 by default so ONE contract, ONE LAN sweep and ONE parser cover
    both an RTNode-2400 and a Pi. That is a privileged port and this service does
    not run as root: the unit grants ``AmbientCapabilities=CAP_NET_BIND_SERVICE``
    instead, which is the documented way to bind low without handing a
    network-facing server the whole machine.

    NO SILENT FALLBACK PORT. If the bind fails this raises and the unit lands in
    ``failed``, where systemd and the birth's own check will both say so. A
    quiet retreat to :8080 would leave a node that looks fine to itself and is
    invisible to every sweep the medic makes — which is the failure this whole
    module exists to end, wearing a different hat.
    """
    from http.server import ThreadingHTTPServer

    if collect is None:
        snapshot = Snapshot()
        snapshot.start()
        collect = snapshot.value
    httpd = ThreadingHTTPServer((bind, port), make_handler(collect))
    httpd.serve_forever()


if __name__ == "__main__":          # pragma: no cover
    import argparse
    ap = argparse.ArgumentParser(
        description="Pi propagation-node /status endpoint")
    ap.add_argument("--port", type=int, default=STATUS_PORT)
    ap.add_argument("--bind", default="0.0.0.0")
    a = ap.parse_args()
    serve(port=a.port, bind=a.bind)
