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
from datetime import datetime, timezone
from typing import Callable, Optional, Tuple, Union

import safe_shell

CONFIG = os.path.expanduser("~/.reticulum-node-medic/datetime.json")

#: Format ``timedatectl set-time`` (and our display) uses.
FMT = "%Y-%m-%d %H:%M:%S"

ShellRunner = Callable[[str], Tuple[int, str]]


def _default_run(cmd: str) -> Tuple[int, str]:
    try:
        return safe_shell.run(cmd, timeout=15)      # no shell (audit C5)
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
    # Atomic (temp + fsync + os.replace) so a power cut mid-write can't truncate
    # datetime.json and silently wipe autosync / last_sync — the medic is a
    # solar/battery field device on an SD card where interrupted writes are THE
    # documented failure mode (see monitor.atomic_json).
    from monitor.atomic_json import write_json
    write_json(path, d, indent=2, sort_keys=True)
    return d


def _finite_number(v) -> Optional[float]:
    """*v* as a float only if it is a real finite number — NOT a JSON bool
    (isinstance(True, int) is True), NaN, or inf. Anything else -> None, so a
    corrupt/hostile config value can never reach the clock or the UI as a number."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    f = float(v)
    if f != f or f in (float("inf"), float("-inf")):
        return None
    return f


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
    """Epoch of the last successful GPS sync, or None if never synced (or if the
    stored value is not a real finite number)."""
    return _finite_number(load(path).get("last_sync"))


def _stamp_sync(epoch: float, path: str = CONFIG) -> dict:
    d = load(path)
    d["last_sync"] = float(epoch)
    return save(d, path)


def last_sync_source(path: str = CONFIG) -> Optional[str]:
    """How the clock was last synced (e.g. "GPS"), or None. Lets the datetime
    screen say "GPS-synced N ago" rather than a bare "synced N ago", so the
    operator can tell a GPS-held clock from a manually-set one."""
    v = load(path).get("last_sync_source")
    return v if isinstance(v, str) and v else None


def mark_synced(epoch: float, source: str, path: str = CONFIG) -> dict:
    """Stamp the shared last-sync surface the datetime screen reads, tagged with
    *source*. Called by the running medic's GPS clock discipline so the screen
    reflects "GPS-synced N ago" while GPS is actively holding the clock — the
    operator must be able to tell GPS-synced from never-synced.

    Deliberately does NOT persist any forward-only floor: that design caused a
    permanent lockout (a bad corroborated jump poisoned the floor forever) and is
    gone. GPS plausibility is now bounded by a FIXED window in monitor.gps_clock,
    which needs no persisted state."""
    d = load(path)
    d["last_sync"] = float(epoch)
    d["last_sync_source"] = str(source)
    d.pop("last_good_epoch", None)          # scrub any floor left by an old build
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


def ntp_enabled(run: Optional[ShellRunner] = None) -> bool:
    """Whether systemd-timesyncd is ENABLED (``set-ntp true`` state) — i.e. NTP is
    ALLOWED to run and steer the clock. This is the ``NTP`` property, NOT
    ``NTPSynchronized``: enabled says "timesyncd is active and will correct the
    clock once it reaches a server"; synchronised says "it already has". The GPS
    disciplinarian disables NTP (``set-ntp false``) when it takes the clock offline
    (see monitor.gps_clock.apply_clock); the online-detector reads THIS to know
    whether NTP still needs restoring. "Couldn't check" -> False, the safe default
    (a False here can only make the detector consider re-enabling, never disable)."""
    run = run or _default_run
    code, out = run("timedatectl show -p NTP --value")
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
                 run: Optional[ShellRunner] = None, translate=None) -> Tuple[bool, str]:
    """Set the system clock to *value* (a datetime or ``"YYYY-MM-DD HH:MM:SS"``
    string, interpreted as LOCAL wall-clock — this is the operator's manual entry).

    NTP auto-sync is turned off first, otherwise ``timedatectl`` refuses a manual
    set. Returns ``(ok, message)``."""
    run = run or _default_run
    t = translate or (lambda text: text)      # the screen passes tr (ledger #215)
    stamp = _fmt_settime(value)
    run("sudo -n timedatectl set-ntp false")
    code, out = run(f'sudo -n timedatectl set-time "{stamp}"')
    if code == 0:
        return True, t("Clock set to {stamp}.").format(stamp=stamp)
    return False, t("Could not set the clock: {why}").format(why=out.strip()[-160:])


def set_timezone(tz: str, run: Optional[ShellRunner] = None, translate=None) -> Tuple[bool, str]:
    """Set the system timezone (an IANA name, e.g. ``America/New_York``)."""
    run = run or _default_run
    t = translate or (lambda text: text)
    tz = (tz or "").strip()
    if not tz:
        return False, t("No timezone given.")
    code, out = run(f'sudo -n timedatectl set-timezone "{tz}"')
    if code == 0:
        return True, t("Timezone set to {tz}.").format(tz=tz)
    return False, t("Could not set the timezone: {why}").format(why=out.strip()[-160:])


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


def gps_time_or_reason(run: Optional[ShellRunner] = None, translate=None):
    """``(datetime, "")`` on a fix, else ``(None, reason)`` — and the reason
    tells the three failures apart. A missing gpspipe, a gpsd that never
    answered and a sky with no satellites all used to collapse into "take the
    medic outside" (readiness sweep, 2026-10-03)."""
    run = run or _default_run
    t = translate or (lambda text: text)
    try:
        code, out = run(GPS_TIME_CMD)
    except Exception as exc:                                       # noqa: BLE001
        return None, t("GPS could not be read: {why}").format(why=exc)
    low = (out or "").lower()
    if code == 127 or "not found" in low or "no such file" in low:
        return None, t("GPS tools aren't installed on this medic (gpspipe is "
                       "missing), so the clock can't be set from GPS.")
    if "timed out" in low or "timeout" in low:
        return None, t("GPS didn't answer within 15 seconds — is gpsd running and "
                       "the GPS plugged in? Try again, or set the time manually.")
    if code not in (0, None) and not out:
        return None, t("GPS gave no answer (exit code {code}) — is gpsd running? Set "
                       "the time manually if this persists.").format(code=code)
    dt = parse_gps_time(out or "")
    if dt is None:
        return None, t("No GPS fix — the clock was left unchanged. Take the medic "
                       "outside for clear sky, then try again (or set the time "
                       "manually).")
    return dt, ""


def gps_time(run: Optional[ShellRunner] = None) -> Optional[datetime]:
    """The current GPS (satellite UTC) time as an aware UTC datetime, or None if
    there's no fix / no GPS."""
    return gps_time_or_reason(run)[0]


def sync_from_gps(run: Optional[ShellRunner] = None,
                  now: Callable[[], float] = None,
                  path: str = CONFIG, translate=None) -> Tuple[bool, str]:
    """Set the system clock from GPS time (sudo). Graceful no-op with a clear
    message when there's no fix — NO clock command is issued in that case.

    Returns ``(ok, message)`` and stamps the last-sync time on success.
    GPS time is UTC; ``timedatectl set-time`` receives it as ``UTC`` below."""
    run = run or _default_run
    t = translate or (lambda text: text)
    dt, why = gps_time_or_reason(run, translate=translate)
    if dt is None:
        return False, why
    stamp = dt.strftime(FMT)  # UTC wall-clock
    run("sudo -n timedatectl set-ntp false")
    code, out = run(f'sudo -n timedatectl set-time "{stamp} UTC"')
    if code == 0:
        import time as _time
        _stamp_sync((now or _time.time)(), path)
        return True, t("Clock synced from GPS: {stamp} UTC.").format(stamp=stamp)
    return False, t("GPS fix found but the clock could not be set: {why}").format(
        why=out.strip()[-160:])


# --- display helpers --------------------------------------------------------

def format_synced_ago(last_epoch: Optional[float], now: float, translate=None,
                      source: str = "") -> str:
    """A human "synced N ago" phrase for the last GPS sync (or 'never synced').

    *source* "GPS" says "GPS-synced …" — the screen used to patch the word in
    with str.replace, which no translation survives (ledger #215)."""
    t = translate or (lambda text: text)
    if not last_epoch:
        return t("never synced")
    verb = t("GPS-synced") if source == "GPS" else t("synced")
    secs = max(0, int(now - last_epoch))
    if secs < 45:
        return t("{verb} just now").format(verb=verb)
    mins = secs // 60
    if mins < 60:
        return (t("{verb} 1 minute ago") if mins == 1
                else t("{verb} {n} minutes ago")).format(verb=verb, n=mins)
    hrs = mins // 60
    if hrs < 24:
        return (t("{verb} 1 hour ago") if hrs == 1
                else t("{verb} {n} hours ago")).format(verb=verb, n=hrs)
    days = hrs // 24
    return (t("{verb} 1 day ago") if days == 1
            else t("{verb} {n} days ago")).format(verb=verb, n=days)
