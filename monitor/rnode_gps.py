"""Read GPS position from an RNode-CE device (e.g. a Heltec Tracker).

The Pi has no GNSS; an RNode built on a GPS board (RNode_Firmware_CE with
HAS_GPS) PUSHES its fix over the KISS serial link whenever the fix is valid:

    FEND CMD_GPS GPS_CMD_LAT <int32 big-endian> FEND    # lat * 1e6 (microdeg)
    FEND CMD_GPS GPS_CMD_LNG <int32 big-endian> FEND    # lng * 1e6

(payload bytes KISS-escaped). Mainline RNS's RNodeInterface does NOT handle
CMD_GPS (0xA0), so a device used as rnsd's radio would drop these — hence the GPS
board is read here as a DEDICATED device on its own serial port, and the position
feeds ``monitor.geo.read_gps`` (map centring, node placement, coverage tracks).

The frame decoder is pure and unit-tested; the serial read is injected.
"""

from __future__ import annotations

from typing import Callable, Optional, Tuple

# KISS control bytes
FEND = 0xC0
FESC = 0xDB
TFEND = 0xDC
TFESC = 0xDD
# RNode-CE GPS command bytes (Framing.h)
CMD_GPS = 0xA0
GPS_CMD_LAT = 0x00
GPS_CMD_LNG = 0x01
GPS_CMD_STATE = 0x02   # [sats, fix_valid] heartbeat, emitted whenever NMEA is parsing
#: RNode-CE only emits a CMD_GPS frame when it has a valid fix, so a plausible
#: coordinate is a sanity floor, not a real constraint.
_MICRODEG = 1_000_000.0


class RNodeGpsDecoder:
    """Incrementally decode CMD_GPS KISS frames from a byte stream. Feed bytes as
    they arrive; ``latest`` holds the most recent lat/lng seen, and ``position``
    is ``(lat, lon)`` once both have arrived."""

    def __init__(self):
        self._buf = bytearray()
        self._in_frame = False
        self._esc = False
        self.lat: Optional[float] = None
        self.lng: Optional[float] = None

    def feed(self, data: bytes) -> bool:
        """Consume bytes; return True if a lat or lng was updated."""
        updated = False
        for b in data:
            if b == FEND:
                if self._in_frame and self._buf and self._decode(self._buf):
                    updated = True
                self._buf = bytearray()
                self._in_frame = True
                self._esc = False
            elif self._in_frame:
                if self._esc:
                    self._buf.append(FEND if b == TFEND
                                     else FESC if b == TFESC else b)
                    self._esc = False
                elif b == FESC:
                    self._esc = True
                else:
                    self._buf.append(b)
        return updated

    def _decode(self, frame: bytearray) -> bool:
        # frame = [CMD_GPS, subcommand, b0, b1, b2, b3]
        if len(frame) < 6 or frame[0] != CMD_GPS:
            return False
        val = int.from_bytes(bytes(frame[2:6]), "big", signed=True) / _MICRODEG
        if frame[1] == GPS_CMD_LAT:
            self.lat = val
            return True
        if frame[1] == GPS_CMD_LNG:
            self.lng = val
            return True
        return False

    @property
    def position(self) -> Optional[Tuple[float, float]]:
        if self.lat is not None and self.lng is not None:
            return (self.lat, self.lng)
        return None


def read_position(port: str, timeout: float = 20.0, baud: int = 115200,
                  serial_factory: Optional[Callable] = None
                  ) -> Optional[Tuple[float, float]]:
    """Open *port*, read the RNode KISS stream until a full lat+lng fix arrives
    or *timeout* elapses. Returns ``(lat, lon)`` or ``None``. ``serial_factory``
    (``port, baud, read_timeout -> serial-like``) is injected for tests; the
    default uses pyserial."""
    import time
    if serial_factory is None:
        import serial

        def serial_factory(p, b, t):   # noqa: E306 - local default
            return serial.Serial(p, b, timeout=t)

    dec = RNodeGpsDecoder()
    try:
        ser = serial_factory(port, baud, 0.5)
    except Exception:
        return None
    try:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            chunk = ser.read(512)
            if chunk:
                dec.feed(chunk)
                if dec.position is not None:
                    return dec.position
    finally:
        try:
            ser.close()
        except Exception:
            pass
    return None


def rnode_gps_reader(port: str, timeout: float = 20.0) -> Callable[[], Optional[Tuple[float, float]]]:
    """A reader matching ``monitor.geo.read_gps``'s signature — ``() ->
    (lat, lon) | None`` — sourced from an RNode GPS device on *port*. Drop it in
    wherever a gps_reader is accepted (Map download, RTNode build, birth cert)."""
    return lambda: read_position(port, timeout=timeout)
