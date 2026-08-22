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

import calendar
import json
import os
import time
from datetime import datetime
from typing import Optional

from monitor.rnode_gps import (
    FEND, FESC, TFEND, TFESC,
    CMD_GPS, GPS_CMD_LAT, GPS_CMD_LNG, GPS_CMD_STATE,
)

_MICRODEG = 1_000_000.0

# Satellite UTC time, pushed by the firmware alongside lat/lng/state. The Pi 5's
# RTC is NOT battery-backed and a field medic has no internet for NTP, so the
# GNSS receiver is the only trustworthy clock offline — this sub-frame carries it.
# Emitted ONLY when the receiver has a valid+fresh satellite UTC; its ABSENCE is
# an honest "no trustworthy time" (old firmware never sends it -> stays None).
# Payload: [year_hi, year_lo, month, day, hour, minute, second], year big-endian.
GPS_CMD_UTC = 0x03

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
        # HAS THE FIRMWARE EVER SPOKEN ABOUT GPS? sats/fix both start at 0 and
        # only move when a GPS_CMD_STATE frame arrives, so "sats: 0, fix: 0" was
        # ambiguous in the worst way — it reads identically for
        #   (a) this firmware has no GPS support at all, and
        #   (b) the GPS is fitted and working but has not locked yet.
        # Those need opposite responses: reflash versus go outside and wait.
        # The medic could not tell them apart, so neither could the operator
        # (2026-08-07: Jonesey reporting healthy radio telemetry and zero GPS).
        self.gps_frames: int = 0
        self.gps_seen_at: Optional[float] = None
        # Satellite UTC (epoch seconds) and the local time we RECEIVED it. Both
        # None until a GPS_CMD_UTC frame with in-range fields arrives — the honest
        # unknown that lets the clock-discipline logic refuse to guess. gps_utc_recv
        # anchors latency compensation (the fix was true at receipt, not now).
        self.gps_utc: Optional[int] = None
        self.gps_utc_recv: Optional[float] = None
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
        elif sub == GPS_CMD_UTC and len(payload) >= 7:
            # [year_hi, year_lo, month, day, hour, minute, second], year u16 BE.
            year = int.from_bytes(payload[0:2], "big")
            month, day, hour, minute, second = payload[2:7]
            # HONEST REJECT: KISS has no CRC, so a single bit-flip in the day byte
            # can turn a real date into a plausible-but-wrong one (Feb 31 -> Mar 3
            # if we let calendar.timegm silently normalize it). We set NOTHING
            # unless the fields form a REAL calendar instant. datetime() raises on
            # impossible dates AND enforces month/day/hour/minute ranges for us; we
            # still bound the year (it accepts 1..9999) and the leap second (it
            # rejects 60, so clamp only for this validity probe — the real second
            # is kept below, and timegm normalizes 23:59:60 to the right instant).
            if 2020 <= year <= 2100 and 0 <= second <= 60:
                try:
                    datetime(year, month, day, hour, minute, min(second, 59))
                except ValueError:
                    pass                      # impossible date -> reject, set nothing
                else:
                    # timegm() treats the tuple as UTC (unlike mktime, which applies
                    # the local timezone) — the wire is UTC, so no local-tz bug.
                    self.gps_utc = calendar.timegm(
                        (year, month, day, hour, minute, second))
                    self.gps_utc_recv = self._now()
        self.gps_frames += 1
        self.gps_seen_at = self._now()
        self.updated = self._now()
        return True                       # all CMD_GPS frames are kept from rnsd

    def state(self) -> dict:
        return {
            "lat": self.lat, "lng": self.lng,
            "sats": self.sats, "fix": self.fix,
            "has_fix": self.lat is not None and self.lng is not None,
            # Evidence, not inference: how many CMD_GPS frames this radio has
            # sent, and when the last one arrived. gps_frames == 0 means the
            # firmware is not reporting GPS AT ALL — a different fault from a
            # receiver that is reporting and has not locked.
            "gps_frames": self.gps_frames, "gps_seen_at": self.gps_seen_at,
            # Satellite UTC for clock discipline (offline authority). Both None
            # until a valid GPS_CMD_UTC frame arrives -> old firmware that never
            # sends 0x03 is fully backward compatible (feature is inert).
            "gps_utc": self.gps_utc, "gps_utc_recv": self.gps_utc_recv,
            # live signal (for TRIAGE / VITALS): per-packet + periodic channel stats
            "last_rssi": self.last_rssi, "last_snr": self.last_snr,
            "packet_heard_at": self.packet_heard_at,
            "noise_floor": self.noise_floor, "airtime": self.airtime,
            "channel_load": self.channel_load, "interference": self.interference,
            "updated": self.updated,
        }


def _write_state(path: str, state: dict) -> None:
    """Atomically publish the state for readers.

    Mode 0600 on purpose. The default home now is /dev/shm (tmpfs, so the
    position never reaches the SD card and dies with the power) — but /dev/shm
    is world-READABLE by default, and this file carries where the medic is right
    now. It is written for one reader on one machine; nothing else has business
    with it. Costs nothing, and matters most on a tool built around nodes being
    untraceable to a person or a place.
    """
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
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
        from monitor.geo import SPLITTER_STATE
        state_file = SPLITTER_STATE

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
