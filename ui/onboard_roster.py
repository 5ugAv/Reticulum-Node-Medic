"""The medic's own permanent hardware, identified by USB serial — so the tool
never confuses *its own* radio/GPS with a work board it's servicing.

Node Medic carries permanent infrastructure: its own LoRa RNode (Jonesey, the
mesh vantage) and its GPS board (a Heltec Wireless Tracker). Everything ELSE on
USB is a work board to flash / PROBE / birth. Telling them apart by "is the port
busy?" is fragile: stop rnsd for maintenance and Jonesey's port goes free, so a
busy-check would suddenly see the medic's own radio as flashable — a foot-gun
that could erase the medic's radio. And a Heltec Tracker plugged in *to be
serviced* looks identical to the medic's *own* GPS Tracker under a busy-check.

So the medic records its own boards by **USB serial** (a stable identity) in a
small roster file, and target-selection excludes them by identity. A cloned medic
registers ITS own boards at setup — self-knowledge that travels with the fleet.
"""

from __future__ import annotations

import glob
import json
import os
import re

#: Per-medic roster: {role: usb_serial}, e.g. {"jonesey_lora": "3C:0F:02:EB:2E:18"}.
ROSTER_PATH = os.path.expanduser("~/.reticulum-node-medic/onboard.json")

_SERIAL_RE = re.compile(r"([0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5})")


def serial_for_port(port: str) -> str | None:
    """The USB serial (an ESP32 MAC, e.g. ``3C:0F:02:EB:2E:18``) for a
    ``/dev/ttyACM*`` / ``/dev/ttyUSB*`` port, resolved from the stable
    ``/dev/serial/by-id`` symlink. None if it can't be resolved."""
    target = os.path.realpath(port)
    for link in glob.glob("/dev/serial/by-id/*"):
        try:
            if os.path.realpath(link) == target:
                base = os.path.basename(link)
                m = _SERIAL_RE.search(base)
                return m.group(1) if m else base
        except OSError:
            continue
    return None


def load_roster(path: str = ROSTER_PATH) -> dict:
    """The medic's onboard roster ({role: serial}); empty if none recorded yet."""
    try:
        with open(path) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def onboard_serials(path: str = ROSTER_PATH) -> set:
    """The USB serials of the medic's OWN permanent boards (never work targets)."""
    return {v for v in load_roster(path).values() if v}


def is_onboard(port: str, path: str = ROSTER_PATH, service_serials=None) -> bool:
    """True when *port* is one of the medic's own permanent boards, so flash /
    PROBE / birth must never target it. Onboard if EITHER:
      * its USB serial is in the roster (persistent self-knowledge — survives rnsd
        being stopped for maintenance, when the port would otherwise look free), OR
      * it's a board the medic's own services are bound to (*service_serials* — the
        live "it's operating like Jonesey, so it's mine" signal; see
        service_bound_serials).
    """
    serial = serial_for_port(port)
    if not serial:
        return False               # can't identify here; callers fail closed
    if serial in onboard_serials(path):
        return True
    return bool(service_serials and serial in set(service_serials))


def is_flashable_work_board(port: str, path: str = ROSTER_PATH,
                            service_serials=None) -> bool:
    """FAIL-CLOSED work-board test. A port is a flashable work board ONLY if we can
    positively resolve its USB serial AND it is not onboard. If the serial can't be
    resolved we do NOT know the board is safe to erase, so we refuse it. Rationale:
    flashing the medic's OWN radio is catastrophic; refusing to flash a genuine
    work board is a mild, recoverable annoyance. Use this (not a bare ``not
    is_onboard``) to pick flash/PROBE targets."""
    if not serial_for_port(port):
        return False
    return not is_onboard(port, path, service_serials)


def register(role: str, serial: str, path: str = ROSTER_PATH) -> dict:
    """Record one of the medic's own permanent boards. Idempotent; returns the
    updated roster. (A cloned medic calls this for each of its boards at setup.)"""
    roster = load_roster(path)
    roster[role] = serial
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(roster, f, indent=2, sort_keys=True)
    return roster


def register_port(role: str, port: str, path: str = ROSTER_PATH) -> dict | None:
    """Register the board currently on *port* by resolving its USB serial. None
    if the serial can't be resolved (nothing recorded)."""
    serial = serial_for_port(port)
    if not serial:
        return None
    return register(role, serial, path)


# ---- self-enrollment: a clone learns its OWN boards by identity --------------

def attached_serial_ports(glob_fn=glob.glob) -> list:
    """Every serial device on USB right now (free or busy)."""
    return sorted(glob_fn("/dev/ttyACM*") + glob_fn("/dev/ttyUSB*"))


def _probe_role(port: str) -> "str | None":
    """Real best-effort role probe: an RNode answers ``rnodeconf -i``; a GPS emits
    NMEA ``$G..`` sentences. None if neither is clear (still adopted, just labelled
    generically)."""
    import subprocess
    try:
        out = subprocess.run(["rnodeconf", port, "-i"], capture_output=True,
                             text=True, timeout=12).stdout or ""
        if "RNode" in out or "Device signature" in out:
            return "rnode"
    except Exception:
        pass
    try:
        import serial as _pyserial
        with _pyserial.Serial(port, 9600, timeout=2) as s:
            for _ in range(25):
                line = s.readline().decode("ascii", "ignore")
                if line.startswith("$G") and "," in line:
                    return "gps"
    except Exception:
        pass
    return None


def identify_role(port: str, probe=None) -> str:
    """Best-effort role LABEL for an onboard board: ``rnode`` | ``gps`` | ``board``.
    The label is cosmetic — a board is protected once its serial is in the roster,
    whatever its role. *probe* is injected in tests."""
    try:
        return (probe or _probe_role)(port) or "board"
    except Exception:
        return "board"


def _label_for(role: str, serial: str) -> str:
    """Stable, unique roster key for an adopted board: role + last 4 serial chars."""
    tail = "".join(c for c in serial if c.isalnum())[-4:].lower() or "xxxx"
    return f"{role}_{tail}"


def commission_attached(ports=None, probe=None, path: str = ROSTER_PATH,
                        serial_fn=None) -> dict:
    """ADOPT every currently-attached serial board as the medic's OWN onboard
    hardware, by USB serial. THE COMMISSIONING CONTRACT: run this only when the
    medic's permanent boards are attached and no work board is (a fresh clone's
    first boot; an operator "adopt my hardware" action). This is how a clone gains
    self-knowledge of ITS OWN radio/GPS — whose serials differ from the parent's —
    so it never flashes them, even when rnsd is stopped and the port looks free.
    Idempotent. Returns ``{serial: role}`` adopted."""
    ports = attached_serial_ports() if ports is None else ports
    resolve = serial_fn or serial_for_port
    adopted = {}
    for p in ports:
        serial = resolve(p)
        if not serial:
            continue
        role = identify_role(p, probe)
        register(_label_for(role, serial), serial, path)
        adopted[serial] = role
    return adopted


# ---- functional "operating like Jonesey => it's mine" signal ----------------

_DEV_RE = re.compile(r"(/dev/serial/by-id/[^\s'\";,]+|/dev/tty(?:ACM|USB)\d+)")


def service_device_paths(read_unit=None,
                         units=("rnode-splitter", "rnsd", "gpsd", "gps-splitter"),
                         config_texts=None) -> set:
    """Physical serial device paths the medic's OWN services are configured to use
    — the serial splitter that feeds rnsd its RNode, gpsd, and the like. A board on
    one of these paths is functioning as the medic's infrastructure, so it's the
    medic's even if the service is momentarily stopped (the CONFIG still claims it).
    Best-effort + injectable (``read_unit`` reads a systemd unit; ``config_texts``
    supplies extra config blobs). Returns the referenced device paths."""
    texts = list(config_texts or [])
    if read_unit is None:
        import subprocess
        def read_unit(u):
            try:
                return subprocess.run(["systemctl", "cat", u], capture_output=True,
                                     text=True, timeout=6).stdout or ""
            except Exception:
                return ""
    for u in units:
        texts.append(read_unit(u))
    paths = set()
    for t in texts:
        for m in _DEV_RE.findall(t or ""):
            paths.add(m)
    return paths


def service_bound_serials(device_paths=None, serial_fn=None, **kw) -> set:
    """USB serials of the boards the medic's own services are bound to (resolved
    from service_device_paths). The functional half of the two-layer onboard check
    in is_onboard: a board 'operating like Jonesey' is the medic's."""
    paths = service_device_paths(**kw) if device_paths is None else device_paths
    resolve = serial_fn or serial_for_port
    return {s for s in (resolve(p) for p in paths) if s}
