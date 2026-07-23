"""Settings ▸ Date, time & timezone (item 8) — clock, timezone & GPS sync.

The field medic is usually offline with no NTP, so GPS is the clock's lifeline.
These tests pin the parts that must be exactly right, all with injected shell
runners — no real subprocess, no clock changes, no hardware:
  * a GPS time sync sets the system clock to the parsed satellite UTC time,
  * with no fix it falls back gracefully and issues NO clock command,
  * auto-sync on/off persists,
  * manual set-time / set-timezone produce the right command shape.
"""

import json

import pytest

from provisioning import tool_datetime as td


class RecordingRunner:
    """A shell runner that records every command and answers from rules.

    Each rule is ``(substring, returncode, output)``; the first rule whose
    substring is in the command wins. Unmatched commands return ``(0, "")``."""

    def __init__(self, rules=None):
        self.rules = list(rules or [])
        self.commands = []

    def __call__(self, cmd):
        self.commands.append(cmd)
        for sub, code, out in self.rules:
            if sub in cmd:
                return code, out
        return 0, ""

    def issued(self, sub):
        return [c for c in self.commands if sub in c]


# gpspipe -w output carrying a satellite UTC time (only present once fixed).
_GPS_WITH_TIME = (
    '{"class":"VERSION"}\n'
    '{"class":"TPV","mode":1}\n'                                    # no time yet
    '{"class":"TPV","mode":3,"time":"2026-07-23T12:34:56.000Z","lat":-37.8}\n'
)
_GPS_NO_FIX = '{"class":"TPV","mode":1}\n{"class":"SKY"}\n'


# ---- GPS time parsing ------------------------------------------------------

def test_parse_gps_time_reads_first_tpv_time_as_utc():
    dt = td.parse_gps_time(_GPS_WITH_TIME)
    assert dt is not None
    assert (dt.year, dt.month, dt.day) == (2026, 7, 23)
    assert (dt.hour, dt.minute, dt.second) == (12, 34, 56)
    assert dt.utcoffset().total_seconds() == 0        # aware UTC


def test_parse_gps_time_none_without_a_time():
    assert td.parse_gps_time(_GPS_NO_FIX) is None
    assert td.parse_gps_time("") is None


def test_gps_time_uses_gpspipe_and_parses(monkeypatch):
    run = RecordingRunner([("gpspipe", 0, _GPS_WITH_TIME)])
    dt = td.gps_time(run=run)
    assert dt is not None and dt.hour == 12 and dt.minute == 34
    assert run.issued("gpspipe")                       # went through gpsd


# ---- GPS sync sets the clock ----------------------------------------------

def test_sync_from_gps_sets_clock_to_parsed_gps_time(tmp_path):
    run = RecordingRunner([("gpspipe", 0, _GPS_WITH_TIME)])
    cfg = str(tmp_path / "datetime.json")
    ok, msg = td.sync_from_gps(run=run, now=lambda: 1000.0, path=cfg)

    assert ok is True
    setcmds = run.issued("set-time")
    assert len(setcmds) == 1
    # the set-time command must carry the exact parsed GPS UTC time
    assert "2026-07-23 12:34:56" in setcmds[0]
    assert setcmds[0].startswith("sudo -n")            # privileged
    # last-sync stamp persisted
    assert td.last_sync(cfg) == 1000.0


def test_sync_from_gps_graceful_noop_without_a_fix(tmp_path):
    run = RecordingRunner([("gpspipe", 0, _GPS_NO_FIX)])
    cfg = str(tmp_path / "datetime.json")
    ok, msg = td.sync_from_gps(run=run, now=lambda: 1000.0, path=cfg)

    assert ok is False
    assert "no gps fix" in msg.lower()
    assert run.issued("set-time") == []                # NO clock command issued
    assert td.last_sync(cfg) is None                   # nothing stamped


# ---- auto-sync persistence -------------------------------------------------

def test_autosync_defaults_on_and_persists(tmp_path):
    cfg = str(tmp_path / "datetime.json")
    assert td.is_autosync(cfg) is True                 # default ON

    td.set_autosync(False, path=cfg)
    assert td.is_autosync(cfg) is False
    assert json.load(open(cfg))["autosync"] is False   # actually on disk

    td.set_autosync(True, path=cfg)
    assert td.is_autosync(cfg) is True


# ---- manual set-time / set-timezone shape ----------------------------------

def test_set_datetime_command_shape():
    run = RecordingRunner()
    ok, msg = td.set_datetime("2026-01-02 03:04:05", run=run)
    assert ok is True
    setcmds = run.issued("set-time")
    assert setcmds == ['sudo -n timedatectl set-time "2026-01-02 03:04:05"']
    # NTP is disabled first so timedatectl accepts a manual set
    assert run.issued("set-ntp false")


def test_set_datetime_accepts_a_datetime():
    from datetime import datetime
    run = RecordingRunner()
    td.set_datetime(datetime(2026, 12, 31, 23, 59, 59), run=run)
    assert '"2026-12-31 23:59:59"' in run.issued("set-time")[0]


def test_set_timezone_command_shape():
    run = RecordingRunner()
    ok, msg = td.set_timezone("America/New_York", run=run)
    assert ok is True
    assert run.issued("set-timezone") == [
        'sudo -n timedatectl set-timezone "America/New_York"']


def test_set_timezone_rejects_empty():
    run = RecordingRunner()
    ok, msg = td.set_timezone("  ", run=run)
    assert ok is False
    assert run.commands == []                           # nothing issued


def test_set_datetime_reports_failure():
    run = RecordingRunner([("set-time", 1, "Failed to set time")])
    ok, msg = td.set_datetime("2026-01-02 03:04:05", run=run)
    assert ok is False
    assert "could not set" in msg.lower()


# ---- reading current tz ----------------------------------------------------

def test_current_timezone_reads_timedatectl():
    run = RecordingRunner([("Timezone", 0, "Australia/Melbourne\n")])
    assert td.current_timezone(run=run) == "Australia/Melbourne"


# ---- synced-ago formatter --------------------------------------------------

def test_format_synced_ago():
    assert td.format_synced_ago(None, 1000.0) == "never synced"
    assert td.format_synced_ago(1000.0, 1000.0) == "synced just now"
    assert td.format_synced_ago(1000.0, 1000.0 + 120) == "synced 2 minutes ago"
    assert td.format_synced_ago(1000.0, 1000.0 + 3600) == "synced 1 hour ago"
    assert td.format_synced_ago(1000.0, 1000.0 + 2 * 86400) == "synced 2 days ago"
