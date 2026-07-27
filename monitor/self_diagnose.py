"""Self Diagnose — the medic checks (and heals) its OWN onboard radio/GPS board.

Born from the 2026-07-22 incident where a build flashed the medic's own radio
(Jonesey, the Heltec Wireless Tracker) instead of a work board and corrupted it.
The medic should be able to notice that and walk its way back — this is the pure,
tested diagnostic core the PROBE ▸ Self Diagnose screen drives.

Every check is a small pure function over injected command output (so it's unit
tested with no hardware); the runtime wires the real shell. A check returns a
``Finding`` with a severity, a human explanation, and — when we know a safe,
proven repair — a ``fix`` key the UI can offer.

Targets the ONBOARD board only (identified by its service-bound serial), NEVER a
work board — the whole reason the incident happened was a naive "first tty".
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Callable, List, Optional

#: Jonesey — the medic's onboard Heltec Wireless Tracker (radio + GPS via splitter).
ONBOARD_SERIAL = "A1:B2:C3:D4:E5:F6"

SEV_OK = "ok"
SEV_WARN = "warning"
SEV_CRIT = "critical"


@dataclass
class Finding:
    check: str
    severity: str                 # ok | warning | critical
    detail: str
    fix: Optional[str] = None     # a repair key the UI can run, or None
    data: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.severity == SEV_OK


def check_usb_present(by_id_listing: str, serial: str = ONBOARD_SERIAL) -> Finding:
    """Is the onboard board enumerating on USB at all? (absent = unplugged, dead,
    or brown-out drop.) ``by_id_listing`` = the text of ``ls /dev/serial/by-id``."""
    if serial.lower() in (by_id_listing or "").lower():
        return Finding("usb_present", SEV_OK, f"Onboard radio present on USB ({serial}).")
    return Finding("usb_present", SEV_CRIT,
                   f"Onboard radio ({serial}) is NOT on USB — it dropped off the bus. "
                   "Re-seat it / power-cycle the medic.", fix="usb_recover")


def check_chip_alive(esptool_output: str) -> Finding:
    """Even with dead firmware, the ESP32-S3 ROM bootloader answers esptool. If it
    does, the HARDWARE is fine and it's recoverable; if not, it's a cabling/power
    problem. ``esptool_output`` = stdout of an esptool chip_id/flash_id."""
    low = (esptool_output or "").lower()
    if "chip is esp32-s3" in low or ("mac:" in low and "esp32-s3" in low):
        return Finding("chip_alive", SEV_OK,
                       "Chip responds to esptool — hardware is fine, recoverable.")
    return Finding("chip_alive", SEV_CRIT,
                   "Chip did not answer esptool — check the USB DATA cable / port, "
                   "or hold BOOT while (re)plugging.", fix="usb_recover")


def check_firmware_provisioned(rnodeconf_i_output: str) -> Finding:
    """A provisioned RNode answers rnodeconf; a corrupt/unprovisioned one says
    'RNode did not respond' (often after a bogus 'Radio reporting frequency').
    ``rnodeconf_i_output`` = output of ``rnodeconf <port> -i``."""
    low = (rnodeconf_i_output or "").lower()
    if "did not respond" in low or "invalid response" in low or "no answer" in low:
        return Finding("firmware", SEV_CRIT,
                       "Firmware not answering as an RNode — corrupt or unprovisioned. "
                       "Reflash + re-provision the Tracker firmware.", fix="reflash_provision")
    if "reticulum" in low or "firmware version" in low or "device signature" in low:
        return Finding("firmware", SEV_OK, "RNode firmware present and provisioned.")
    return Finding("firmware", SEV_WARN,
                   "Couldn't confirm firmware state — re-check with the board settled.")


def check_splitter(is_active: bool, cpu_seconds: float, uptime_seconds: float,
                   recent_log: str = "") -> Finding:
    """The rnode-splitter feeds Jonesey's serial to rnsd + extracts GPS. A crash,
    or spinning at high CPU (reading garbage from dead firmware), means the radio
    path is broken. High CPU = cpu_seconds close to uptime."""
    if not is_active:
        return Finding("splitter", SEV_CRIT,
                       "rnode-splitter is not running — the radio path is down.",
                       fix="restart_splitter")
    busy = uptime_seconds > 0 and (cpu_seconds / uptime_seconds) > 0.5
    if busy:
        return Finding("splitter", SEV_WARN,
                       f"rnode-splitter is spinning hot ({cpu_seconds:.0f}s CPU in "
                       f"{uptime_seconds:.0f}s) — usually the board sending garbage "
                       "(dead firmware). Fix the firmware, then restart it.",
                       fix="restart_splitter")
    if re.search(r"traceback|SerialException|readiness to read", recent_log or "", re.I):
        return Finding("splitter", SEV_WARN,
                       "rnode-splitter logged a serial error recently — restart it.",
                       fix="restart_splitter")
    return Finding("splitter", SEV_OK, "rnode-splitter healthy.")


def check_rns_link(rns_recent_output: str) -> Finding:
    """The app's RNS loops 'Opening … Could not detect device' when the RNode
    interface can't sync — the classic dead-radio symptom. ``rns_recent_output`` =
    a recent slice of the app's RNS log/stdout."""
    if re.search(r"could not detect device", rns_recent_output or "", re.I):
        return Finding("rns_link", SEV_CRIT,
                       "RNS can't detect the RNode interface (retry loop) — the radio "
                       "isn't answering. Recover the firmware.", fix="reflash_provision")
    return Finding("rns_link", SEV_OK, "RNS radio interface is not erroring.")


def check_gps_fresh(gps_state_text: str, now: float, max_age_s: float = 600.0) -> Finding:
    """The splitter writes gps_state.json continuously while it reads valid frames
    from the board. A stale file (old 'updated') means nothing's coming through —
    but GPS is also legitimately null indoors, so this is a WARNING, not critical."""
    try:
        st = json.loads(gps_state_text) if gps_state_text.strip() else {}
    except (ValueError, TypeError):
        return Finding("gps", SEV_WARN, "gps_state.json unreadable.")
    updated = st.get("updated") or 0
    age = now - updated
    if age > max_age_s:
        return Finding("gps", SEV_WARN,
                       f"GPS/radio telemetry is stale ({age/60:.0f} min old) — the "
                       "splitter isn't getting fresh frames from the board.",
                       data={"age_s": age})
    return Finding("gps", SEV_OK, f"Telemetry fresh ({age:.0f}s).", data={"age_s": age})


def check_disk_space(df_output: str, warn_pct: int = 85, crit_pct: int = 95) -> Finding:
    """SD/root filesystem fullness from ``df -P /``. A full card fails writes and can
    corrupt the SD; parses the Use% column of the last data line."""
    pct = None
    for line in (df_output or "").splitlines():
        m = re.search(r"(\d+)%", line)
        if m:
            pct = int(m.group(1))
    if pct is None:
        return Finding("disk", SEV_OK, "Disk usage unavailable.")
    if pct >= crit_pct:
        return Finding("disk", SEV_CRIT,
                       f"Storage almost full ({pct}%) — writes may fail or corrupt the "
                       "SD card. Free space now.", fix="free_space", data={"pct": pct})
    if pct >= warn_pct:
        return Finding("disk", SEV_WARN,
                       f"Storage getting full ({pct}%). Clear some space soon.",
                       fix="free_space", data={"pct": pct})
    return Finding("disk", SEV_OK, f"Storage OK ({pct}% used).", data={"pct": pct})


def check_service(name: str, is_active: bool, critical: bool = True) -> Finding:
    """A core systemd service on the medic. rnsd down = the whole mesh stack is down."""
    if is_active:
        return Finding(f"service_{name}", SEV_OK, f"{name} is running.")
    sev = SEV_CRIT if critical else SEV_WARN
    return Finding(f"service_{name}", sev, f"{name} is NOT running — restart it.",
                   fix=f"restart_{name}")


def check_cpu_temp(temp_output: str, warn_c: float = 75.0, crit_c: float = 82.0) -> Finding:
    """Pi SoC temperature from ``vcgencmd measure_temp`` ('temp=48.3''C'). The Pi
    throttles around 80-85C; sustained heat drops performance and ages the board."""
    m = re.search(r"temp=([\d.]+)", temp_output or "")
    if not m:
        return Finding("cpu_temp", SEV_OK, "CPU temperature unavailable.")
    t = float(m.group(1))
    if t >= crit_c:
        return Finding("cpu_temp", SEV_CRIT,
                       f"CPU running hot ({t:.0f}C) — it will throttle. Improve airflow "
                       "/ cooling.", data={"temp_c": t})
    if t >= warn_c:
        return Finding("cpu_temp", SEV_WARN, f"CPU warm ({t:.0f}C) — watch cooling.",
                       data={"temp_c": t})
    return Finding("cpu_temp", SEV_OK, f"CPU temperature fine ({t:.0f}C).",
                   data={"temp_c": t})


def check_throttled(throttled_output: str) -> Finding:
    """``vcgencmd get_throttled`` bits. Active (now) under-voltage/throttle is a power
    or heat problem happening NOW; the 'occurred' bits mean it happened since boot —
    important for a battery/UPS-powered medic."""
    m = re.search(r"0x([0-9a-fA-F]+)", throttled_output or "")
    if not m:
        return Finding("power", SEV_OK, "Power/throttle status unavailable.")
    bits = int(m.group(1), 16)
    if bits & 0x1 or bits & 0x4:                 # under-voltage now / throttled now
        why = "under-voltage" if bits & 0x1 else "throttling"
        return Finding("power", SEV_CRIT,
                       f"Active {why} — the 5V supply can't keep up. Use a stronger "
                       "supply / better cable.", data={"throttled": hex(bits)})
    if bits & 0x10000 or bits & 0x40000:         # occurred since boot
        return Finding("power", SEV_WARN,
                       "Under-voltage/throttling happened earlier — keep an eye on the "
                       "power supply.", data={"throttled": hex(bits)})
    return Finding("power", SEV_OK, "Power stable (no under-voltage/throttle).",
                   data={"throttled": hex(bits)})


def check_wifi(nmcli_output: str, warn_pct: int = 40) -> Finding:
    """The medic's own WiFi from ``nmcli -t -f IN-USE,SIGNAL,SSID dev wifi`` — the
    active AP (line starting ``*``) carries a 0-100 SIGNAL percentage. Weak WiFi
    slows updates + serving apps; NEVER critical (the medic works offline)."""
    active = next((ln for ln in (nmcli_output or "").splitlines()
                   if ln.startswith("*")), None)
    if not active:
        return Finding("wifi", SEV_OK, "WiFi not connected (offline is fine).")
    parts = active.split(":", 2)
    try:
        pct = int(parts[1])
    except (IndexError, ValueError):
        return Finding("wifi", SEV_OK, "WiFi signal unavailable.")
    ssid = parts[2] if len(parts) > 2 else ""
    label = f" to {ssid}" if ssid else ""
    if pct <= warn_pct:
        return Finding("wifi", SEV_WARN,
                       f"WiFi is weak ({pct}%{label}) — updates and serving apps may "
                       "be slow.", data={"signal_pct": pct})
    return Finding("wifi", SEV_OK, f"WiFi OK ({pct}%{label}).", data={"signal_pct": pct})


def summarize(findings: List[Finding]) -> dict:
    """Roll up findings for the screen: worst severity + the ordered fix list."""
    crit = [f for f in findings if f.severity == SEV_CRIT]
    warn = [f for f in findings if f.severity == SEV_WARN]
    fixes = []
    for f in findings:                       # de-duped, in check order
        if f.fix and f.fix not in fixes:
            fixes.append(f.fix)
    worst = SEV_CRIT if crit else (SEV_WARN if warn else SEV_OK)
    return {"worst": worst, "critical": len(crit), "warning": len(warn),
            "fixes": fixes, "healthy": worst == SEV_OK}
