"""Serial splitter — one board, both jobs, LoRa never drops.

Jonesey (the medic's dedicated RNode, a Heltec Wireless Tracker) is BOTH the LoRa
radio and the GNSS receiver, but it has a single USB serial port and rnsd wants it
exclusively. This splitter owns the real port, presents a virtual PTY that rnsd
opens instead (so LoRa stays online 100%), and skims the ``CMD_GPS`` frames the
firmware injects into the KISS stream — decoding them to a small JSON state file
that ``monitor.geo`` reads. rnsd never sees the GPS frames; the GPS reader never
fights rnsd for the port.

The demux (:class:`KissGpsSplitter`) is pure and unit-tested; the PTY plumbing in
:func:`run` is the only hardware-facing part.
"""

from __future__ import annotations

import json
import os
import time
from typing import Optional

from monitor.rnode_gps import (
    FEND, FESC, TFEND, TFESC,
    CMD_GPS, GPS_CMD_LAT, GPS_CMD_LNG, GPS_CMD_STATE,
)

_MICRODEG = 1_000_000.0

# RNode stat frames (Framing.h). These are RECORDED as they pass through — but
# still forwarded byte-for-byte, because rnsd consumes them too.
CMD_STAT_RSSI = 0x23   # [rssi + 157]                    — per received packet
CMD_STAT_SNR = 0x24    # [snr * 4, signed]               — per received packet
CMD_STAT_CHTM = 0x25   # [ats:2 atl:2 cls:2 cll:2 crs nfl ntf] — periodic channel stats
RSSI_OFFSET = 157


def _unescape(b: bytes) -> bytes:
    out = bytearray()
    i = 0
    while i < len(b):
        if b[i] == FESC and i + 1 < len(b):
            out.append(FEND if b[i + 1] == TFEND else FESC if b[i + 1] == TFESC else b[i + 1])
            i += 2
        else:
            out.append(b[i])
            i += 1
    return bytes(out)


class KissGpsSplitter:
    """Demux an RNode KISS byte stream. ``feed(data)`` returns the bytes to forward
    to the radio host (rnsd) — every ``CMD_GPS`` frame is consumed and decoded into
    GPS state (``lat``/``lng``/``sats``/``fix``); everything else passes through
    byte-for-byte intact."""

    def __init__(self, now=time.time):
        self._buf = bytearray()
        self._in_frame = False
        self._now = now
        self.lat: Optional[float] = None
        self.lng: Optional[float] = None
        self.sats: int = 0
        self.fix: int = 0
        self.updated: Optional[float] = None
        # live signal state, recorded from stat frames passing through to rnsd
        self.last_rssi: Optional[int] = None       # dBm, per received packet
        self.last_snr: Optional[float] = None      # dB, per received packet
        self.packet_heard_at: Optional[float] = None
        self.noise_floor: Optional[int] = None     # dBm, periodic channel stats
        self.airtime: Optional[float] = None       # 0..1 short-term
        self.channel_load: Optional[float] = None  # 0..1 short-term
        self.interference: Optional[int] = None    # dBm, or None when clean

    def feed(self, data: bytes) -> bytes:
        out = bytearray()
        for byte in data:
            if byte == FEND:
                if self._in_frame and self._buf:
                    self._record_stats(self._buf)          # observe, never consume
                    if not self._consume_gps(self._buf):
                        out += bytes([FEND]) + self._buf + bytes([FEND])
                self._buf = bytearray()
                self._in_frame = True
            elif self._in_frame:
                self._buf.append(byte)
            else:
                out.append(byte)          # stray bytes before any frame — pass through
        return bytes(out)

    def _record_stats(self, frame: bytearray) -> None:
        """Record signal stats from frames that PASS THROUGH to rnsd."""
        cmd = frame[0]
        if cmd not in (CMD_STAT_RSSI, CMD_STAT_SNR, CMD_STAT_CHTM):
            return
        p = _unescape(bytes(frame[1:]))
        if cmd == CMD_STAT_RSSI and len(p) >= 1:
            self.last_rssi = p[0] - RSSI_OFFSET
            self.packet_heard_at = self._now()
        elif cmd == CMD_STAT_SNR and len(p) >= 1:
            self.last_snr = int.from_bytes(p[:1], "big", signed=True) * 0.25
            self.packet_heard_at = self._now()
        elif cmd == CMD_STAT_CHTM and len(p) >= 11:
            self.airtime = int.from_bytes(p[0:2], "big") / 10000.0
            self.channel_load = int.from_bytes(p[4:6], "big") / 10000.0
            self.noise_floor = p[9] - RSSI_OFFSET
            self.interference = (p[10] - RSSI_OFFSET) if p[10] != 0xFF else None
        self.updated = self._now()

    def _consume_gps(self, frame: bytearray) -> bool:
        """Return True if this frame is a CMD_GPS frame (consumed, not forwarded)."""
        if frame[0] != CMD_GPS:
            return False
        sub = frame[1] if len(frame) > 1 else -1
        payload = _unescape(bytes(frame[2:]))
        if sub == GPS_CMD_LAT and len(payload) >= 4:
            self.lat = int.from_bytes(payload[:4], "big", signed=True) / _MICRODEG
        elif sub == GPS_CMD_LNG and len(payload) >= 4:
            self.lng = int.from_bytes(payload[:4], "big", signed=True) / _MICRODEG
        elif sub == GPS_CMD_STATE and len(payload) >= 2:
            self.sats, self.fix = payload[0], payload[1]
        self.updated = self._now()
        return True                       # all CMD_GPS frames are kept from rnsd

    def state(self) -> dict:
        return {
            "lat": self.lat, "lng": self.lng,
            "sats": self.sats, "fix": self.fix,
            "has_fix": self.lat is not None and self.lng is not None,
            # live signal (for TRIAGE / VITALS): per-packet + periodic channel stats
            "last_rssi": self.last_rssi, "last_snr": self.last_snr,
            "packet_heard_at": self.packet_heard_at,
            "noise_floor": self.noise_floor, "airtime": self.airtime,
            "channel_load": self.channel_load, "interference": self.interference,
            "updated": self.updated,
        }


def _write_state(path: str, state: dict) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f)
    os.replace(tmp, path)                 # atomic for the reader


def run(real_port: str = "/dev/ttyACM0",
        symlink: str = "/dev/rnode-jonesey",
        state_file: str = None,
        baud: int = 115200) -> None:      # pragma: no cover - hardware I/O loop
    """Own *real_port*, expose a PTY at *symlink* for rnsd, and skim GPS to
    *state_file*. Runs forever; intended to be a systemd service ordered before rnsd."""
    import pty
    import select
    import serial

    if state_file is None:
        state_file = os.path.expanduser("~/gps_state.json")

    ser = serial.Serial(real_port, baud, timeout=0)
    master, slave = pty.openpty()
    os.set_blocking(master, False)
    try:
        os.remove(symlink)
    except OSError:
        pass
    os.symlink(os.ttyname(slave), symlink)
    try:
        os.chmod(os.ttyname(slave), 0o660)
    except OSError:
        pass

    split = KissGpsSplitter()
    last_written = 0.0
    while True:
        r, _, _ = select.select([ser.fileno(), master], [], [], 1.0)
        if ser.fileno() in r:
            data = ser.read(4096)
            if data:
                forward = split.feed(data)
                if forward:
                    try:
                        os.write(master, forward)          # -> rnsd
                    except OSError:
                        pass
                if split.updated and split.updated != last_written:
                    _write_state(state_file, split.state())
                    last_written = split.updated
        if master in r:
            try:
                out = os.read(master, 4096)                # rnsd -> device
                if out:
                    ser.write(out)
            except OSError:
                pass
