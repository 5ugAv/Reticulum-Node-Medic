"""Field WiFi — connect the medic to a phone hotspot or venue AP via nmcli.

The medic is offline-first, but online access is handy when it's reachable: address
geocoding (monitor.geo.geocode_address), firmware refresh, and map top-ups all light
up once there's a link. NetworkManager (nmcli) does the work; everything routes
through an injected *run* so the parsing is unit-tested without hardware.
"""

from __future__ import annotations

import subprocess
from typing import Callable, List, Optional, Tuple

Runner = Callable[[list], Tuple[int, str]]


def _default_run(argv: list) -> Tuple[int, str]:
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=45)
        return p.returncode, (p.stdout + p.stderr)
    except Exception as e:
        return 1, str(e)


def _split_escaped(line: str) -> List[str]:
    """Split an ``nmcli -t`` line on ``:`` while honouring its ``\\:`` escaping
    (SSIDs and security fields can contain colons)."""
    out, cur, i = [], "", 0
    while i < len(line):
        c = line[i]
        if c == "\\" and i + 1 < len(line):
            cur += line[i + 1]
            i += 2
            continue
        if c == ":":
            out.append(cur)
            cur = ""
            i += 1
            continue
        cur += c
        i += 1
    out.append(cur)
    return out


def scan_networks(run: Runner = _default_run) -> List[dict]:
    """Visible WiFi networks as ``{ssid, signal, secure, active}``, strongest first
    (the currently-connected one pinned to the top), de-duplicated by SSID."""
    code, out = run(["nmcli", "-t", "-f", "IN-USE,SIGNAL,SECURITY,SSID",
                     "device", "wifi", "list", "--rescan", "yes"])
    nets: dict = {}
    for line in out.splitlines():
        parts = _split_escaped(line)
        if len(parts) < 4:
            continue
        inuse, signal, security = parts[0], parts[1], parts[2]
        ssid = ":".join(parts[3:]).strip()
        if not ssid:
            continue                                  # hidden network — skip
        try:
            sig = int(signal)
        except ValueError:
            sig = 0
        sec = security.strip()
        active = inuse.strip() in ("*", "yes")
        secure = bool(sec) and sec not in ("", "--")
        entry = nets.get(ssid)
        if entry is None:
            nets[ssid] = {"ssid": ssid, "signal": sig,
                          "secure": secure, "active": active}
        else:                                         # merge duplicate SSIDs
            entry["signal"] = max(entry["signal"], sig)
            entry["active"] = entry["active"] or active
            entry["secure"] = entry["secure"] or secure
    return sorted(nets.values(), key=lambda n: (not n["active"], -n["signal"]))


def connect(ssid: str, password: str = "", autoconnect: bool = True,
            run: Runner = _default_run) -> Tuple[bool, str]:
    """Join *ssid* (with *password* if secured). ``autoconnect`` controls whether
    the medic rejoins this network automatically after a reboot/power loss — on for
    home + field hotspots you want it to come back to, off for one-off networks.
    Returns ``(ok, message)``."""
    if not ssid:
        return False, "No network selected."
    argv = ["nmcli", "device", "wifi", "connect", ssid]
    if password:
        argv += ["password", password]
    code, out = run(argv)
    if code == 0 and "successfully" in out.lower():
        set_autoconnect(ssid, autoconnect, run=run)      # honour the toggle
        note = "" if autoconnect else "  (won't auto-reconnect)"
        return True, f"Connected to {ssid}.{note}"
    tail = (out.strip().splitlines() or ["connection failed"])[-1]
    return False, tail.strip()


def set_autoconnect(ssid: str, enabled: bool = True, priority: Optional[int] = None,
                    run: Runner = _default_run) -> Tuple[bool, str]:
    """Turn NetworkManager auto-reconnect on/off for *ssid* (and optionally set its
    priority, higher = preferred 'home'). Privileged, so it goes through ``sudo -n``
    (the medic is configured for passwordless sudo). Best-effort: returns (ok, out)."""
    argv = ["sudo", "-n", "nmcli", "connection", "modify", ssid,
            "connection.autoconnect", "yes" if enabled else "no"]
    if priority is not None:
        argv += ["connection.autoconnect-priority", str(priority)]
    code, out = run(argv)
    return code == 0, out


def current_connection(run: Runner = _default_run) -> Optional[dict]:
    """The active WiFi link as ``{ssid, ip}``, or None if not connected."""
    code, out = run(["nmcli", "-t", "-f", "GENERAL.CONNECTION,IP4.ADDRESS",
                     "device", "show", "wlan0"])
    ssid, ip = "", ""
    for line in out.splitlines():
        if line.startswith("GENERAL.CONNECTION:"):
            ssid = line.split(":", 1)[1].strip()
        elif line.startswith("IP4.ADDRESS"):
            ip = line.split(":", 1)[1].strip().split("/")[0]
    if not ssid or ssid in ("", "--"):
        return None
    return {"ssid": ssid, "ip": ip}
