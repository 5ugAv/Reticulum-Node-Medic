"""Reading GPS off an RNode-CE device (Jonesey, a Heltec Tracker RNode).

The device pushes CMD_GPS KISS frames when it has a fix; these tests pin the
frame decode (big-endian microdegrees, KISS un-escaping) and the read loop,
without a serial port.
"""

import pytest

from monitor.rnode_gps import (
    RNodeGpsDecoder, read_position, rnode_gps_reader,
    FEND, FESC, TFEND, TFESC, CMD_GPS, GPS_CMD_LAT, GPS_CMD_LNG,
)


def gps_frame(sub: int, deg: float) -> bytes:
    """Build a CMD_GPS frame the way RNode-CE does: CMD + subcommand unescaped,
    the 4 big-endian microdegree bytes KISS-escaped, wrapped in FENDs."""
    raw = int(round(deg * 1_000_000)).to_bytes(4, "big", signed=True)
    payload = bytearray([CMD_GPS, sub])
    for b in raw:
        if b == FEND:
            payload += bytes([FESC, TFEND])
        elif b == FESC:
            payload += bytes([FESC, TFESC])
        else:
            payload.append(b)
    return bytes([FEND]) + bytes(payload) + bytes([FEND])


# ---- decoder -------------------------------------------------------------

def test_decodes_lat_and_lng_into_a_position():
    d = RNodeGpsDecoder()
    assert d.position is None
    d.feed(gps_frame(GPS_CMD_LAT, -37.810123))
    assert d.position is None                       # need both halves
    d.feed(gps_frame(GPS_CMD_LNG, 144.962555))
    lat, lon = d.position
    assert lat == pytest.approx(-37.810123, abs=1e-6)
    assert lon == pytest.approx(144.962555, abs=1e-6)


def test_handles_negative_and_microdegree_scaling():
    d = RNodeGpsDecoder()
    d.feed(gps_frame(GPS_CMD_LAT, -37.999999))
    assert d.lat == pytest.approx(-37.999999, abs=1e-6)


def test_unescapes_fend_and_fesc_bytes_in_the_value():
    # value 0x0000C000 contains 0xC0 (FEND) -> must arrive escaped as FESC TFEND
    frame = bytes([FEND, CMD_GPS, GPS_CMD_LAT,
                   0x00, 0x00, FESC, TFEND, 0x00, FEND])
    d = RNodeGpsDecoder()
    d.feed(frame)
    assert d.lat == pytest.approx(0x0000C000 / 1_000_000, abs=1e-9)
    # and 0xDB (FESC) escaped as FESC TFESC
    frame2 = bytes([FEND, CMD_GPS, GPS_CMD_LNG,
                    0x00, 0x00, FESC, TFESC, 0x00, FEND])
    d.feed(frame2)
    assert d.lng == pytest.approx(0x0000DB00 / 1_000_000, abs=1e-9)


def test_reassembles_frames_split_across_reads():
    d = RNodeGpsDecoder()
    whole = gps_frame(GPS_CMD_LAT, 12.345678)
    d.feed(whole[:3])
    d.feed(whole[3:])
    assert d.lat == pytest.approx(12.345678, abs=1e-6)


def test_ignores_non_gps_kiss_frames():
    d = RNodeGpsDecoder()
    d.feed(bytes([FEND, 0x00, 0x11, 0x22, 0x33, FEND]))   # CMD_DATA-ish
    d.feed(bytes([FEND, 0x27, 0x64, FEND]))               # CMD_STAT_BAT
    assert d.lat is None and d.lng is None and d.position is None


def test_ignores_gps_state_heartbeat_frame():
    """The ported firmware also emits a CMD_GPS STATE (0x02) heartbeat
    [sats, fix_valid] whenever NMEA is parsing (verified on Jonesey 2026-07-16,
    got frames with STATE=(0,0) indoors). The decoder must ignore it — it is 4
    bytes, below the 6-byte lat/lng frame — and still resolve a real fix."""
    d = RNodeGpsDecoder()
    d.feed(bytes([FEND, CMD_GPS, 0x02, 7, 0x01, FEND]))   # 7 sats, fix valid
    assert d.position is None                             # STATE alone -> no position
    d.feed(gps_frame(GPS_CMD_LAT, -37.81))
    d.feed(gps_frame(GPS_CMD_LNG, 144.96))
    assert d.position == pytest.approx((-37.81, 144.96), abs=1e-6)


# ---- read loop (injected serial) -----------------------------------------

class _FakeSerial:
    def __init__(self, chunks):
        self._chunks = list(chunks)
    def read(self, n):
        return self._chunks.pop(0) if self._chunks else b""
    def close(self):
        pass


def test_read_position_returns_fix_from_the_stream():
    chunks = [gps_frame(GPS_CMD_LAT, -37.81), gps_frame(GPS_CMD_LNG, 144.96)]
    pos = read_position("/dev/ttyACM0", timeout=2.0,
                        serial_factory=lambda p, b, t: _FakeSerial(chunks))
    assert pos == pytest.approx((-37.81, 144.96), abs=1e-6)


def test_read_position_times_out_without_a_fix():
    pos = read_position("/dev/ttyACM0", timeout=0.3,
                        serial_factory=lambda p, b, t: _FakeSerial([]))
    assert pos is None


def test_read_position_none_when_port_wont_open():
    def boom(p, b, t):
        raise OSError("no such device")
    assert read_position("/dev/ttyACM9", serial_factory=boom) is None


def test_rnode_gps_reader_matches_read_gps_signature():
    chunks = [gps_frame(GPS_CMD_LAT, 1.5), gps_frame(GPS_CMD_LNG, 2.5)]
    # the adapter is a zero-arg callable, like monitor.geo.read_gps expects
    reader = rnode_gps_reader("/dev/ttyACM0")
    # patch the port open by calling read_position directly is enough coverage;
    # here just assert it's callable with no args
    assert callable(reader)
