"""TRIAGE ▸ Antenna Test — the bench process of 2026-08-27, made repeatable.

A keeper with a handful of look-alike antennas has no way to know which one
to trust: the six-antenna bench campaign (docs/ANTENNA_BENCH_2026-08-27.md)
proved that looks and labels mean NOTHING — a perfectly-matched whip was 15 dB
deaf (a "sponge": absorbed power measures identically to radiated power at the
feed), two identical-looking twins measured wildly differently, and folding a
folder cost 4 dB that no impedance meter could see.

What the medic CAN measure, with no extra instruments, is each antenna's EAR:
the noise floor a connected work board samples through it. A better antenna
hears more of everything — so the HIGHER (less negative) floor wins. That is
deliberately counter-intuitive ("more noise = better"?!), which is exactly why
this flow exists: the numbers need the interpretation carried with them.

The poll itself is the RNode/RTNode-2400 ``CMD_STAT_CHTM`` (0x25) KISS stat
frame, the same one Jonesey's splitter records (monitor/serial_splitter.py) —
one reply carries airtime, channel load, current RSSI, noise floor and the
interference flag. Read-only: nothing here writes, flashes or resets a board.

Bench-proven method rules baked in:
  * the FIRST reply after connecting carries stale 6.44%-artifact fields —
    discarded, never shown;
  * a board in TNC mode answers roughly 1 poll in 3 — every sample retries;
  * an entry >= ``SPONGE_GAP_DB`` quieter than the best ear is flagged as a
    suspect (wrong band, lossy sponge, or broken) — the -118 dBm signature;
  * the target must be a WORK board: ``ui.onboard_roster`` keeps the medic's
    own radios (Jonesey, the onboard S3) out of reach, mirroring the flash
    guard even though this flow only ever reads.
"""

from __future__ import annotations

import glob
import statistics
import time
from dataclasses import dataclass, field
from typing import Callable, List, Optional

from monitor.serial_splitter import (CMD_STAT_CHTM, FEND, RSSI_OFFSET,
                                     _unescape)

#: An ear this many dB below the best ear is a SUSPECT antenna (wrong band,
#: lossy sponge, or broken) — the bench's -118-vs--103 signature, with margin.
SPONGE_GAP_DB = 6

#: A fresh floor must sit inside physical reality for a LoRa receiver.
FLOOR_MIN_DBM, FLOOR_MAX_DBM = -140, -40


# --------------------------------------------------------------------------
# target selection — a work board on USB, never the medic's own radio

def find_test_board(is_work_board: Optional[Callable[[str], bool]] = None,
                    listing: Optional[List[str]] = None) -> tuple:
    """Pick the ONE work board to read through, or explain why we can't.

    Returns ``(port, problem)`` — exactly one of the two is None. Mirrors the
    flash-t114 guard shape: refuse on none, refuse on many (the reading must
    belong to the antenna the keeper is holding, not a lottery), and never
    offer one of the medic's own radios even for a read.
    """
    if is_work_board is None:
        from ui.onboard_roster import is_flashable_work_board
        is_work_board = is_flashable_work_board
    links = sorted(glob.glob("/dev/serial/by-id/*")) if listing is None \
        else list(listing)
    candidates = [p for p in links if is_work_board(p)]
    if not candidates:
        return None, ("no_board", "No work board found on USB. Plug the node "
                      "whose antenna you are testing into a spare port — the "
                      "medic's own radio does not count.")
    if len(candidates) > 1:
        return None, ("too_many", "More than one work board is plugged in. "
                      "Leave just the one wearing the antenna under test, so "
                      "the reading belongs to it.")
    return candidates[0], None


# --------------------------------------------------------------------------
# the poll

@dataclass
class EarReading:
    """One antenna's ear, as sampled: floors in dBm, newest last."""

    floors: List[int] = field(default_factory=list)
    polls_sent: int = 0
    replies: int = 0
    interference_dbm: Optional[int] = None   # strongest burst seen, if any

    @property
    def floor(self) -> Optional[int]:
        """The reading that represents this antenna: the MEDIAN floor.
        None until at least two believable samples exist — one sample is a
        coin-flip, not a measurement."""
        if len(self.floors) < 2:
            return None
        return int(statistics.median(self.floors))

    @property
    def spread(self) -> int:
        return (max(self.floors) - min(self.floors)) if self.floors else 0


def _read_frame(ser, want: int, deadline: float) -> Optional[bytes]:
    """Collect one KISS frame whose command byte is *want*; None on timeout.
    Frames for other commands (the board chats stats on its own) are skipped,
    not errors."""
    buf, in_frame = bytearray(), False
    while time.time() < deadline:
        b = ser.read(1)
        if not b:
            continue
        byte = b[0]
        if byte == FEND:
            if in_frame and buf and buf[0] == want:
                return _unescape(bytes(buf[1:]))
            buf, in_frame = bytearray(), True
            continue
        if in_frame:
            buf.append(byte)
    return None


#: One reading = this many kept samples, this far apart. The screen's progress
#: bar is drawn from the same two numbers, so it can never disagree with poll_ear.
EAR_SAMPLES = 8
EAR_GAP_S = 3.0


def progress_fraction(done: int, total: int = EAR_SAMPLES,
                      since_last_s: float = 0.0, gap_s: float = EAR_GAP_S) -> float:
    """How full the bar is: samples kept, plus a creep toward the next one so it
    keeps moving between samples (never past 90% of the next step - it only
    jumps when a sample really lands)."""
    if total <= 0 or done >= total:
        return 1.0
    creep = min(max(since_last_s, 0.0) / gap_s, 0.9) if gap_s > 0 else 0.0
    return min((max(done, 0) + creep) / total, 1.0)


def seconds_left(done: int, total: int = EAR_SAMPLES,
                 since_last_s: float = 0.0, gap_s: float = EAR_GAP_S) -> int:
    """A rounded 'about N seconds' for the screen; never below 1 until done."""
    if done >= total:
        return 0
    left = (total - max(done, 0)) * gap_s - min(max(since_last_s, 0.0), gap_s * 0.9)
    return max(1, int(round(left)))


def poll_ear(port: str, samples: int = EAR_SAMPLES, gap_s: float = EAR_GAP_S,
             reply_timeout_s: float = 3.0, serial_factory=None,
             progress: Optional[Callable[[int, Optional[int]], None]] = None
             ) -> EarReading:
    """Sample the board's noise floor *samples* times over its USB serial.

    Opens at 115200 (the ordinary console rate — NEVER 1200, which is the
    nRF52 DFU touch). The first believable reply is DISCARDED (stale-field
    artifact, bench-proven); each kept sample retries until the per-sample
    deadline. *progress(i, floor)* fires per kept sample for a live UI.
    """
    if serial_factory is None:
        import serial as pyserial

        def serial_factory(p):
            return pyserial.Serial(p, 115200, timeout=2)

    reading = EarReading()
    ser = serial_factory(port)
    try:
        time.sleep(0.3)
        ser.reset_input_buffer()
        kept = 0
        first_discarded = False
        while kept < samples:
            ser.write(bytes([FEND, CMD_STAT_CHTM, FEND]))
            reading.polls_sent += 1
            p = _read_frame(ser, CMD_STAT_CHTM, time.time() + reply_timeout_s)
            if p is None or len(p) < 11:
                if reading.polls_sent > samples * 6:
                    break        # the board is not answering — stop honestly
                continue
            reading.replies += 1
            floor = p[9] - RSSI_OFFSET
            ntf = None if p[10] == 0xFF else p[10] - RSSI_OFFSET
            if not first_discarded:
                first_discarded = True     # the stale-artifact sample
                continue
            if not (FLOOR_MIN_DBM <= floor <= FLOOR_MAX_DBM):
                continue                   # not a believable receiver reading
            reading.floors.append(floor)
            if ntf is not None:
                if (reading.interference_dbm is None
                        or ntf > reading.interference_dbm):
                    reading.interference_dbm = ntf
            kept += 1
            if progress is not None:
                progress(kept, floor)
            if kept < samples:       # nothing left to wait for after the last one
                time.sleep(gap_s)
    finally:
        try:
            ser.close()
        except Exception:
            pass
    return reading


# --------------------------------------------------------------------------
# the session — several antennas, one honest ranking

@dataclass
class AntennaResult:
    label: str
    floor: int                       # median dBm
    spread: int
    interference_dbm: Optional[int]
    #: set by rank(): "best" | "good" | "suspect"
    verdict: str = ""


class AntennaSession:
    """Collects one EarReading per antenna and ranks them.

    Ranking is by ear: the HIGHEST median floor hears best. Anything
    ``SPONGE_GAP_DB`` or more below the best ear is a SUSPECT — the bench
    signature of a wrong-band antenna, a lossy "sponge" (which can measure a
    PERFECT VSWR while radiating nothing), or a broken element. Within
    ``GOOD_WITHIN_DB`` of the best is simply "good" — at that spacing the
    difference is real but small.
    """

    GOOD_WITHIN_DB = 3

    def __init__(self):
        self.results: List[AntennaResult] = []

    def add(self, label: str, reading: EarReading) -> Optional[AntennaResult]:
        """Record *reading* under *label*; None (nothing recorded) when the
        reading never produced a believable floor — the caller shows the
        honest failure instead of a fabricated rank."""
        if reading.floor is None:
            return None
        r = AntennaResult(label=label.strip() or "unnamed",
                          floor=reading.floor, spread=reading.spread,
                          interference_dbm=reading.interference_dbm)
        self.results.append(r)
        return r

    def rank(self) -> List[AntennaResult]:
        """Best ear first, verdicts stamped."""
        ranked = sorted(self.results, key=lambda r: r.floor, reverse=True)
        if not ranked:
            return ranked
        best = ranked[0].floor
        for r in ranked:
            gap = best - r.floor
            if gap >= SPONGE_GAP_DB:
                r.verdict = "suspect"
            elif gap == 0:
                r.verdict = "best"
            elif gap <= self.GOOD_WITHIN_DB:
                r.verdict = "good"
            else:
                r.verdict = "weak"
        return ranked

    @property
    def count(self) -> int:
        return len(self.results)
