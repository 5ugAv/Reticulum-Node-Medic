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


def _dbm_to_pct(dbm: float) -> int:
    """RSSI (dBm) → a 0–100 bar. -100 dBm ≈ 0%, -50 dBm ≈ 100% (2·(dbm+100))."""
    return max(0, min(100, int(round(2 * (dbm + 100)))))


def wifi_iface(run: Runner = _default_run) -> str:
    """The wifi device name (``wlan0`` on the Pi), resolved from nmcli when possible
    so we don't hard-code it on other hosts. Falls back to ``wlan0``."""
    code, out = run(["nmcli", "-t", "-f", "DEVICE,TYPE", "device", "status"])
    if code == 0:
        for line in out.splitlines():
            parts = _split_escaped(line)
            if len(parts) >= 2 and parts[1].strip() == "wifi":
                return parts[0].strip()
    return "wlan0"


def connected_ssid(run: Runner = _default_run, iface: Optional[str] = None) -> str:
    """SSID of the associated network, or "" if not connected. Asks the driver
    (``iw dev <if> link``) first, then falls back to nmcli's ACTIVE flag."""
    iface = iface or wifi_iface(run)
    code, out = run(["iw", "dev", iface, "link"])
    if code == 0:
        for line in out.splitlines():
            s = line.strip()
            if s.startswith("SSID:"):
                return s[len("SSID:"):].strip()
    code, out = run(["nmcli", "-t", "-f", "ACTIVE,SSID", "device", "wifi", "list"])
    for line in out.splitlines():
        parts = _split_escaped(line)
        if len(parts) >= 2 and parts[0].strip() in ("yes", "*"):
            return ":".join(parts[1:]).strip()
    return ""


def _parse_iw_scan(out: str) -> List[dict]:
    """Parse ``iw dev <if> scan`` into ``[{ssid, signal, secure}]`` (one per BSS).
    ``signal`` is a 0–100%; ``secure`` is set when the BSS advertises an RSN/WPA IE.
    Hidden APs come through with an empty ssid and are dropped by the caller."""
    blocks: List[dict] = []
    cur: Optional[dict] = None
    for raw in out.splitlines():
        if raw.startswith("BSS "):                    # new access point block
            if cur is not None:
                blocks.append(cur)
            cur = {"ssid": "", "signal": 0, "secure": False}
            continue
        if cur is None:
            continue
        line = raw.strip()
        if line.startswith("signal:"):
            try:
                cur["signal"] = _dbm_to_pct(float(line.split()[1]))
            except (IndexError, ValueError):
                pass
        elif line.startswith("SSID:") and not line.startswith("SSID List"):
            cur["ssid"] = line[len("SSID:"):].strip()
        elif line.startswith("RSN:") or line.startswith("WPA:"):
            cur["secure"] = True
    if cur is not None:
        blocks.append(cur)
    return blocks


def scan_networks(run: Runner = _default_run) -> List[dict]:
    """Visible WiFi networks as ``{ssid, signal, secure, active}``, strongest first
    (the currently-connected one pinned to the top), de-duplicated by SSID.

    Source is the *driver* via ``sudo iw dev <if> scan``: on the Pi's adapter,
    NetworkManager's scan list wedges to only the connected AP while associated,
    but ``iw`` still reports every nearby BSS. If the ``iw`` scan is unavailable
    (no sudo / not a Pi / empty), we fall back to nmcli so dev hosts still work."""
    iface = wifi_iface(run)
    code, out = run(["sudo", "-n", "iw", "dev", iface, "scan"])
    parsed = _parse_iw_scan(out) if code == 0 else []
    if not any((b["ssid"] or "").strip() for b in parsed):
        return _scan_networks_nmcli(run)              # iw unusable — fall back
    connected = connected_ssid(run, iface)
    nets: dict = {}
    for b in parsed:
        ssid = (b["ssid"] or "").strip()
        if not ssid:
            continue                                  # hidden network — skip
        active = ssid == connected
        entry = nets.get(ssid)
        if entry is None:
            nets[ssid] = {"ssid": ssid, "signal": b["signal"],
                          "secure": b["secure"], "active": active}
        else:                                         # merge duplicate BSSIDs
            entry["signal"] = max(entry["signal"], b["signal"])
            entry["active"] = entry["active"] or active
            entry["secure"] = entry["secure"] or b["secure"]
    return sorted(nets.values(), key=lambda n: (not n["active"], -n["signal"]))


def _scan_networks_nmcli(run: Runner = _default_run) -> List[dict]:
    """Fallback scan via nmcli (used when ``iw`` is unavailable). ``--rescan auto``
    reads NetworkManager's cache, refreshing only when stale."""
    code, out = run(["nmcli", "-t", "-f", "IN-USE,SIGNAL,SECURITY,SSID",
                     "device", "wifi", "list", "--rescan", "auto"])
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
