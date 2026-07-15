"""geo.py reading the serial-splitter's skimmed GPS state — no hardware, no port;
just the JSON state file the splitter writes."""

import json
import time

from monitor.geo import (
    read_splitter_state, read_splitter_fix, splitter_gps_reader, read_gps, GpsFix,
)


def _write(tmp_path, **state) -> str:
    p = tmp_path / "gps_state.json"
    p.write_text(json.dumps(state))
    return str(p)


def test_reads_a_fresh_fix(tmp_path):
    p = _write(tmp_path, lat=-37.81, lng=144.96, sats=8, fix=1, has_fix=True, updated=1000.0)
    st = read_splitter_state(p, max_age_s=30, now=lambda: 1010.0)
    assert st is not None and st["lat"] == -37.81 and st["sats"] == 8


def test_stale_state_is_rejected(tmp_path):
    p = _write(tmp_path, lat=-37.81, lng=144.96, has_fix=True, updated=1000.0)
    assert read_splitter_state(p, max_age_s=30, now=lambda: 1100.0) is None   # 100s old


def test_missing_and_malformed_files_are_none(tmp_path):
    assert read_splitter_state(str(tmp_path / "nope.json")) is None
    bad = tmp_path / "bad.json"
    bad.write_text("{not valid json")
    assert read_splitter_state(str(bad)) is None


def test_read_splitter_fix_builds_full_gpsfix(tmp_path):
    p = _write(tmp_path, lat=-37.81, lng=144.96, sats=9, fix=1, has_fix=True, updated=1000.0)
    fix = read_splitter_fix(p, max_age_s=30, now=lambda: 1005.0)
    assert isinstance(fix, GpsFix)
    assert (fix.lat, fix.lon) == (-37.81, 144.96)
    assert fix.sats == 9 and fix.fix_quality == 1 and fix.source == "tracker_gps"
    assert "T" in fix.fix_time and fix.fix_time.endswith("+00:00")


def test_state_heartbeat_without_position_is_no_fix(tmp_path):
    # indoors: STATE frames give sats/fix but no lat/lng -> not a usable fix
    p = _write(tmp_path, lat=None, lng=None, sats=0, fix=0, has_fix=False, updated=1000.0)
    assert read_splitter_fix(p, max_age_s=30, now=lambda: 1000.0) is None


def test_reader_plugs_into_read_gps(tmp_path):
    p = _write(tmp_path, lat=1.5, lng=2.5, has_fix=True, updated=time.time())
    fix = read_gps(reader=splitter_gps_reader(p, max_age_s=1e9))
    assert fix is not None and (fix.lat, fix.lon) == (1.5, 2.5)


def test_reader_returns_none_when_stale(tmp_path):
    p = _write(tmp_path, lat=1.5, lng=2.5, has_fix=True, updated=0.0)   # ancient
    assert read_gps(reader=splitter_gps_reader(p, max_age_s=30)) is None
