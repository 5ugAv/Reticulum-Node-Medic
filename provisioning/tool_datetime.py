"""Settings ▸ Date, time & timezone (item 8).

Reads and sets the medic's system clock and timezone, and — because a field
medic is usually OFFLINE with no NTP — can sync the clock straight from GPS
(the Tracker's GNSS via gpsd, which carries satellite UTC time).

Everything here is pure + runner-injectable so it's unit-tested without touching
the real clock or hardware:
  * ``run`` is a shell runner ``(cmd) -> (returncode, output)`` (as in
    ``provisioning.tool_identity``); the default shells out.
  * the auto-sync flag and last-sync stamp live in a small JSON store.

System changes go through ``sudo -n`` (the medic has passwordless sudo).
"""

from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timezone
from typing import Callable, Optional, Tuple, Union

CONFIG = os.path.expanduser("~/.reticulum-node-medic/datetime.json")

#: Format ``timedatectl set-time`` (and our display) uses.
FMT = "%Y-%m-%d %H:%M:%S"

ShellRunner = Callable[[str], Tuple[int, str]]


def _default_run(cmd: str) -> Tuple[int, str]:
    try:
        p = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=15)
        return p.returncode, (p.stdout + p.stderr)
    except Exception as e:  # pragma: no cover - defensive
        return 1, str(e)


# --- small JSON store -------------------------------------------------------

def load(path: str = CONFIG) -> dict:
    try:
        with open(path) as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def save(d: dict, path: str = CONFIG) -> dict:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(d, f, indent=2, sort_keys=True)
    return d


def is_autosync(path: str = CONFIG) -> bool:
    """Whether the medic keeps its clock synced from GPS. Defaults ON — GPS is the
    only reliable time source when the unit is offline in the field."""
    v = load(path).get("autosync")
    return True if v is None else bool(v)


def set_autosync(on: bool, path: str = CONFIG) -> dict:
    d = load(path)
    d["autosync"] = bool(on)
    return save(d, path)


def last_sync(path: str = CONFIG) -> Optional[float]:
    """Epoch of the last successful GPS sync, or None if never synced."""
    v = load(path).get("last_sync")
    return float(v) if isinstance(v, (int, float)) else None


def _stamp_sync(epoch: float, path: str = CONFIG) -> dict:
    d = load(path)
    d["last_sync"] = float(epoch)
    return save(d, path)


# --- reading the current clock / timezone -----------------------------------

def current_timezone(run: Optional[ShellRunner] = None) -> str:
    """The system's configured timezone (e.g. ``Australia/Melbourne``), or ""."""
    run = run or _default_run
    code, out = run("timedatectl show -p Timezone --value")
    if code != 0:
        return ""
    return out.strip().splitlines()[-1].strip() if out.strip() else ""


def ntp_synchronized(run: Optional[ShellRunner] = None) -> bool:
    """Whether the OS reports the clock as NTP-synchronised (rarely true afield)."""
    run = run or _default_run
    code, out = run("timedatectl show -p NTPSynchronized --value")
    return code == 0 and out.strip().splitlines()[-1].strip() == "yes"


def now_string(now: Optional[datetime] = None) -> str:
    """Current local wall-clock, formatted for display / the manual fields."""
    return (now or datetime.now()).strftime(FMT)


# --- setting the clock / timezone -------------------------------------------

def _fmt_settime(value: Union[datetime, str]) -> str:
    if isinstance(value, datetime):
        return value.strftime(FMT)
    return str(value).strip()


def set_datetime(value: Union[datetime, str],
                 run: Optional[ShellRunner] = None) -> Tuple[bool, str]:
    """Set the system clock to *value* (a datetime or ``"YYYY-MM-DD HH:MM:SS"``
    string, interpreted as LOCAL wall-clock — this is the operator's manual entry).

    NTP auto-sync is turned off first, otherwise ``timedatectl`` refuses a manual
    set. Returns ``(ok, message)``."""
    run = run or _default_run
    stamp = _fmt_settime(value)
    run("sudo -n timedatectl set-ntp false")
    code, out = run(f'sudo -n timedatectl set-time "{stamp}"')
    if code == 0:
        return True, f"Clock set to {stamp}."
    return False, f"Could not set the clock: {out.strip()[-160:]}"


def set_timezone(tz: str, run: Optional[ShellRunner] = None) -> Tuple[bool, str]:
    """Set the system timezone (an IANA name, e.g. ``America/New_York``)."""
    run = run or _default_run
    tz = (tz or "").strip()
    if not tz:
        return False, "No timezone given."
    code, out = run(f'sudo -n timedatectl set-timezone "{tz}"')
    if code == 0:
        return True, f"Timezone set to {tz}."
    return False, f"Could not set the timezone: {out.strip()[-160:]}"


# --- GPS time ---------------------------------------------------------------

def _parse_iso_utc(s: str) -> Optional[datetime]:
    """Parse an ISO-8601 UTC timestamp (as gpsd emits, e.g.
    ``2026-07-23T12:34:56.000Z``) to an aware UTC datetime, or None."""
    s = (s or "").strip()
    if not s:
        return None
    s = s.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        # tolerate a trailing fractional second gpsd may omit / vary
        try:
            dt = datetime.strptime(s[:19], "%Y-%m-%dT%H:%M:%S").replace(
                tzinfo=timezone.utc)
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def parse_gps_time(text: str) -> Optional[datetime]:
    """Pull the first satellite UTC time out of ``gpspipe -w`` JSON.

    Reads the first ``TPV`` object that carries a ``time`` field (gpsd only fills
    ``time`` once it has a fix), returning an aware UTC datetime — or None when
    there's no fix / no time yet."""
    for line in (text or "").splitlines():
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if obj.get("class") == "TPV" and obj.get("time"):
            dt = _parse_iso_utc(obj["time"])
            if dt is not None:
                return dt
    return None


#: gpsd read: a few TPV frames so we catch one carrying a time.
GPS_TIME_CMD = "gpspipe -w -n 5 2>/dev/null"


def gps_time(run: Optional[ShellRunner] = None) -> Optional[datetime]:
    """The current GPS (satellite UTC) time as an aware UTC datetime, or None if
    there's no fix / no GPS."""
    run = run or _default_run
    try:
        code, out = run(GPS_TIME_CMD)
    except Exception:
        return None
    if code not in (0, None):
        # gpspipe returns non-zero on some timeouts even with usable output
        if not out:
            return None
    return parse_gps_time(out or "")


def sync_from_gps(run: Optional[ShellRunner] = None,
                  now: Callable[[], float] = None,
                  path: str = CONFIG) -> Tuple[bool, str]:
    """Set the system clock from GPS time (sudo). Graceful no-op with a clear
    message when there's no fix — NO clock command is issued in that case.

    Returns ``(ok, message)`` and stamps the last-sync time on success.
    GPS time is UTC; ``timedatectl set-time`` receives it as ``UTC`` below."""
    run = run or _default_run
    dt = gps_time(run)
    if dt is None:
        return False, ("No GPS fix — the clock was left unchanged. Take the medic "
                       "outside for clear sky, then try again (or set the time "
                       "manually).")
    stamp = dt.strftime(FMT)  # UTC wall-clock
    run("sudo -n timedatectl set-ntp false")
    code, out = run(f'sudo -n timedatectl set-time "{stamp} UTC"')
    if code == 0:
        import time as _time
        _stamp_sync((now or _time.time)(), path)
        return True, f"Clock synced from GPS: {stamp} UTC."
    return False, f"GPS fix found but the clock could not be set: {out.strip()[-160:]}"


# --- display helpers --------------------------------------------------------

def format_synced_ago(last_epoch: Optional[float], now: float) -> str:
    """A human "synced N ago" phrase for the last GPS sync (or 'never synced')."""
    if not last_epoch:
        return "never synced"
    secs = max(0, int(now - last_epoch))
    if secs < 45:
        return "synced just now"
    mins = secs // 60
    if mins < 60:
        return f"synced {mins} minute{'s' if mins != 1 else ''} ago"
    hrs = mins // 60
    if hrs < 24:
        return f"synced {hrs} hour{'s' if hrs != 1 else ''} ago"
    days = hrs // 24
    return f"synced {days} day{'s' if days != 1 else ''} ago"
