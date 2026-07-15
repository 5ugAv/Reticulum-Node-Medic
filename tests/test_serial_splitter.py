"""The serial splitter's KISS demux: CMD_GPS frames are skimmed off and decoded,
everything else passes through to rnsd byte-for-byte. Pure — no hardware."""

import pytest

from monitor.serial_splitter import KissGpsSplitter
from monitor.rnode_gps import (
    FEND, FESC, TFEND, TFESC,
    CMD_GPS, GPS_CMD_LAT, GPS_CMD_LNG, GPS_CMD_STATE,
)


def _kiss(first: int, rest: bytes) -> bytes:
    """A KISS frame: FEND + first(command) + escaped(rest) + FEND."""
    body = bytearray([first])
    for b in rest:
        if b == FEND:
            body += bytes([FESC, TFEND])
        elif b == FESC:
            body += bytes([FESC, TFESC])
        else:
            body.append(b)
    return bytes([FEND]) + bytes(body) + bytes([FEND])


def _gps(sub: int, deg: float) -> bytes:
    raw = int(round(deg * 1_000_000)).to_bytes(4, "big", signed=True)
    return _kiss(CMD_GPS, bytes([sub]) + raw)


# ---- passthrough ----------------------------------------------------------

def test_non_gps_frame_passes_through_unchanged():
    s = KissGpsSplitter()
    frame = _kiss(0x00, b"\x11\x22\x33")          # CMD_DATA-ish, not GPS
    assert s.feed(frame) == frame
    assert s.state()["has_fix"] is False


def test_gps_frames_are_consumed_not_forwarded():
    s = KissGpsSplitter()
    assert s.feed(_gps(GPS_CMD_LAT, -37.810123)) == b""
    assert s.feed(_gps(GPS_CMD_LNG, 144.962555)) == b""
    st = s.state()
    assert st["lat"] == pytest.approx(-37.810123, abs=1e-6)
    assert st["lng"] == pytest.approx(144.962555, abs=1e-6)
    assert st["has_fix"] is True


def test_state_heartbeat_frame_updates_sats_and_fix_and_is_consumed():
    s = KissGpsSplitter()
    out = s.feed(_kiss(CMD_GPS, bytes([GPS_CMD_STATE, 9, 1])))
    assert out == b""
    assert s.state()["sats"] == 9
    assert s.state()["fix"] == 1
    assert s.state()["has_fix"] is False          # sats/fix alone is not a position


def test_escaped_gps_payload_decodes_correctly():
    # a longitude whose microdegrees contain 0xC0 (FEND) must survive escaping
    deg = 0x0000C000 / 1_000_000
    s = KissGpsSplitter()
    s.feed(_gps(GPS_CMD_LNG, deg))
    assert s.lng == pytest.approx(deg, abs=1e-9)


def test_mixed_stream_forwards_radio_frames_and_skims_gps():
    s = KissGpsSplitter()
    a = _kiss(0x00, b"radioA")
    b = _kiss(0x07, b"radioB")
    stream = a + _gps(GPS_CMD_LAT, 1.5) + b + _gps(GPS_CMD_LNG, 2.5)
    forwarded = s.feed(stream)
    assert forwarded == a + b                      # only the radio frames reach rnsd
    assert s.state()["lat"] == pytest.approx(1.5, abs=1e-6)
    assert s.state()["lng"] == pytest.approx(2.5, abs=1e-6)


def test_frame_split_across_two_feeds():
    s = KissGpsSplitter()
    whole = _gps(GPS_CMD_LAT, 12.345678)
    assert s.feed(whole[:4]) == b""
    s.feed(whole[4:])
    assert s.lat == pytest.approx(12.345678, abs=1e-6)


def test_updated_timestamp_uses_injected_clock():
    s = KissGpsSplitter(now=lambda: 42.0)
    s.feed(_gps(GPS_CMD_LAT, 1.0))
    assert s.state()["updated"] == 42.0


def test_melbourne_coordinate_survives_firmware_format_roundtrip():
    """A known Melbourne fix, encoded exactly as the firmware sends it (int32
    microdegrees, big-endian, KISS-escaped, negative latitude for the southern
    hemisphere), must decode back to the same coordinate through the splitter.
    This is the 'is the GPS maths right' guard the handover asked for — adapted
    to our CMD_GPS path (the firmware's on-device TinyGPS++ does the NMEA
    DDMM->decimal conversion, so it never reaches the Pi)."""
    LAT, LNG = -37.813600, 144.963100          # Melbourne CBD
    s = KissGpsSplitter()
    s.feed(_gps(GPS_CMD_LAT, LAT))
    s.feed(_gps(GPS_CMD_LNG, LNG))
    st = s.state()
    assert st["lat"] == pytest.approx(LAT, abs=1e-6)
    assert st["lng"] == pytest.approx(LNG, abs=1e-6)
    assert st["has_fix"] is True
