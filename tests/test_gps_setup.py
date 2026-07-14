"""Heltec Tracker GPS setup — NMEA detection, gpsd wiring, build/flash flow.

The Pi has no GPS; a Tracker flashed with the passthrough is its source. These
tests pin the parts that must be exactly right: telling the NMEA-streaming GPS
port apart from the RNode port, and pointing gpsd at it — all without hardware.
"""

import pytest

from transport.connection import EmulatedConnection
from workflows.gps_setup import (
    is_nmea, parse_gpspipe_fix, gpsd_defaults, detect_gps_port,
    compile_command, upload_command, GpsTrackerSetup, FQBN,
)


def _nmea(body: str) -> str:
    cs = 0
    for ch in body:
        cs ^= ord(ch)
    return f"${body}*{cs:02X}"


# ---- NMEA validation (GPS vs RNode) --------------------------------------

def test_is_nmea_accepts_well_formed_sentences():
    assert is_nmea("$GPGGA,123519,4807.038,N,01131.000,E,1,08,0.9,545.4,M")
    assert is_nmea(_nmea("GNRMC,123519,A,4807.038,N,01131.000,E,022.4,084.4"))
    assert is_nmea(_nmea("GPGSV,3,1,11,03,03,111,00"))


def test_is_nmea_rejects_bad_checksum_and_non_nmea():
    good = _nmea("GPGGA,123519,4807.038,N")
    bad = good[:-2] + ("00" if good[-2:] != "00" else "01")
    assert is_nmea(bad) is False              # checksum mismatch
    assert is_nmea("KISS\xc0\x00garbage") is False
    assert is_nmea("Reticulum RNode boot") is False
    assert is_nmea("$GP,short") is False       # not talker+3-type


# ---- gpsd plumbing -------------------------------------------------------

def test_gpsd_defaults_pins_the_device_and_polls():
    cfg = gpsd_defaults("/dev/ttyACM1")
    assert 'DEVICES="/dev/ttyACM1"' in cfg
    assert '-n' in cfg                         # poll without a client
    assert 'USBAUTO="false"' in cfg            # we set the device explicitly


def test_parse_gpspipe_fix_extracts_first_tpv():
    text = ('{"class":"VERSION"}\n'
            '{"class":"TPV","mode":1}\n'                     # no fix yet
            '{"class":"TPV","lat":-37.8102,"lon":144.9629}\n')
    assert parse_gpspipe_fix(text) == (-37.8102, 144.9629)


def test_parse_gpspipe_fix_none_without_a_fix():
    assert parse_gpspipe_fix('{"class":"TPV","mode":1}\n{"class":"SKY"}') is None


# ---- port detection ------------------------------------------------------

def _stream(*sentences):
    return "\n".join(sentences)


def gps_conn(gps_port="/dev/ttyACM1", rnode_port="/dev/ttyACM0"):
    c = EmulatedConnection(default_code=0, default_stdout="ok")
    c.rule("ls /dev/ttyACM", 0, f"{rnode_port} {gps_port}")
    # the RNode port emits KISS-ish noise, never NMEA
    c.rule(f"cat {rnode_port}", 0, "\xc0\x00\xff boot RNode ready\xc0")
    # the GPS port streams NMEA
    c.rule(f"cat {gps_port}", 0, _stream(
        _nmea("GPGGA,123519,4807.038,N,01131.000,E,1,08"),
        _nmea("GPRMC,123519,A,4807.038,N,01131.000,E,022.4"),
        _nmea("GPGSV,3,1,11,03,03,111,00")))
    return c


def test_detect_gps_port_finds_the_nmea_stream():
    conn = gps_conn()
    assert detect_gps_port(conn) == "/dev/ttyACM1"


def test_detect_gps_port_excludes_the_rnode_port():
    # even if the RNode port were probed first, it isn't NMEA; excluding it also
    # avoids disturbing a port the tool knows is the radio
    conn = gps_conn()
    assert detect_gps_port(conn, exclude=("/dev/ttyACM0",)) == "/dev/ttyACM1"


def test_detect_gps_port_none_when_no_nmea():
    conn = EmulatedConnection(default_code=0, default_stdout="")
    conn.rule("ls /dev/ttyACM", 0, "/dev/ttyACM0")
    conn.rule("cat /dev/ttyACM0", 0, "not gps data at all")
    assert detect_gps_port(conn) is None


# ---- command builders ----------------------------------------------------

def test_compile_and_upload_commands_use_the_s3_fqbn():
    assert FQBN in compile_command() and "arduino-cli compile" in compile_command()
    up = upload_command("/dev/ttyACM0")
    assert "arduino-cli upload" in up and "-p /dev/ttyACM0" in up and FQBN in up


# ---- workflow ------------------------------------------------------------

def full_conn():
    c = gps_conn()
    c.rules.insert(0, ("id -u", 0, "1000", ""))            # non-root -> sudo
    c.rules.insert(0, ("command -v arduino-cli", 0, "/usr/bin/arduino-cli", ""))
    c.rules.insert(0, ("command -v gpsd", 0, "/usr/sbin/gpsd", ""))
    c.rules.insert(0, ("gpspipe", 0,
                       '{"class":"TPV","lat":-37.81,"lon":144.96}', ""))
    return c


def test_flash_tracker_sequence():
    conn = full_conn()
    conn.rules.insert(0, ("ls /dev/ttyACM", 0, "/dev/ttyACM0", ""))  # single board
    wf = GpsTrackerSetup(conn)
    results = wf.flash_tracker()
    assert [r.name for r in results] == [
        "ensure_toolchain", "build_firmware", "ensure_single_board", "flash"]
    assert all(r.success for r in results)
    assert any("arduino-cli upload" in c for c in conn.history)


def test_flash_refuses_multiple_boards():
    conn = full_conn()                                      # two ttyACM ports
    wf = GpsTrackerSetup(conn)
    results = wf.flash_tracker()
    assert results[-1].name == "ensure_single_board"
    assert results[-1].success is False


def test_setup_gpsd_detects_configures_and_verifies_fix():
    conn = full_conn()
    wf = GpsTrackerSetup(conn, rnode_port="/dev/ttyACM0")
    results = wf.setup_gpsd()
    assert [r.name for r in results] == [
        "detect_gps", "configure_gpsd", "verify_fix"]
    assert all(r.success for r in results)
    assert wf.gps_port == "/dev/ttyACM1"
    assert wf.fix == (-37.81, 144.96)
    # gpsd was pinned to the GPS port and (re)started
    assert any("tee /etc/default/gpsd" in c for c in conn.history)
    assert any("systemctl restart gpsd" in c for c in conn.history)


def test_setup_gpsd_ok_when_streaming_but_no_fix_yet():
    conn = full_conn()
    conn.rules.insert(0, ("gpspipe", 0, '{"class":"TPV","mode":1}', ""))
    wf = GpsTrackerSetup(conn, rnode_port="/dev/ttyACM0")
    results = wf.setup_gpsd()
    verify = results[-1]
    assert verify.name == "verify_fix" and verify.success is True
    assert wf.fix is None and "waiting for a fix" in verify.message
