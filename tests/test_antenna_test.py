"""TRIAGE Antenna Test logic — every rule here was learned the hard way on
the 2026-08-27 bench (docs/ANTENNA_BENCH_2026-08-27.md): the discarded first
sample, the ~1-in-3 reply rate, the sponge gap, the two-sample minimum."""

import time

from monitor.antenna_test import (AntennaSession, EarReading, SPONGE_GAP_DB,
                                  find_test_board, poll_ear)
from monitor.serial_splitter import CMD_STAT_CHTM, FEND, RSSI_OFFSET


# --------------------------------------------------------------------- fakes

def chtm_frame(floor_dbm: int, ntf_dbm=None, ats=0, cls=0) -> bytes:
    p = bytearray(11)
    p[0:2] = int(ats * 10000).to_bytes(2, "big")
    p[4:6] = int(cls * 10000).to_bytes(2, "big")
    p[8] = (floor_dbm + RSSI_OFFSET) & 0xFF
    p[9] = (floor_dbm + RSSI_OFFSET) & 0xFF
    p[10] = 0xFF if ntf_dbm is None else (ntf_dbm + RSSI_OFFSET) & 0xFF
    return bytes([FEND, CMD_STAT_CHTM]) + bytes(p) + bytes([FEND])


class FakeSerial:
    """Answers every Nth poll with the next queued frame — the TNC-mode
    1-in-3 reply behaviour, scriptable."""

    def __init__(self, frames, answer_every=1):
        self.frames = list(frames)
        self.answer_every = answer_every
        self.polls = 0
        self._out = b""

    def write(self, data):
        self.polls += 1
        if self.frames and self.polls % self.answer_every == 0:
            self._out += self.frames.pop(0)

    def read(self, n):
        if self._out:
            b, self._out = self._out[:n], self._out[n:]
            return b
        return b""

    def reset_input_buffer(self):
        self._out = b""

    def close(self):
        pass


def run_poll(frames, samples=3, answer_every=1):
    fake = FakeSerial(frames, answer_every)
    t0 = time.time()
    reading = poll_ear("/dev/fake", samples=samples, gap_s=0.0,
                       reply_timeout_s=0.2, serial_factory=lambda p: fake)
    assert time.time() - t0 < 5, "poll must not hang"
    return reading, fake


# ------------------------------------------------------------------ polling

def test_first_reply_is_discarded_as_the_stale_artifact():
    # bench: the first CHTM after connect carries stale 6.44% fields
    reading, _ = run_poll([chtm_frame(-99),        # the artifact — discarded
                           chtm_frame(-104), chtm_frame(-104),
                           chtm_frame(-105)], samples=3)
    assert reading.floors == [-104, -104, -105]
    assert -99 not in reading.floors


def test_unanswered_polls_are_retried_not_recorded():
    reading, fake = run_poll([chtm_frame(-99)] + [chtm_frame(-106)] * 3,
                             samples=3, answer_every=3)
    assert reading.floors == [-106, -106, -106]
    assert fake.polls > reading.replies            # retries really happened


def test_a_dead_board_stops_honestly_instead_of_hanging():
    reading, _ = run_poll([], samples=3)
    assert reading.floors == []
    assert reading.floor is None


def test_floor_decodes_via_rssi_offset_and_needs_two_samples():
    reading, _ = run_poll([chtm_frame(-99), chtm_frame(-112)], samples=1)
    assert reading.floors == [-112]
    assert reading.floor is None                   # one sample = coin flip
    reading, _ = run_poll([chtm_frame(-99), chtm_frame(-112),
                           chtm_frame(-110)], samples=2)
    assert reading.floor == -111


def test_unbelievable_floors_are_rejected():
    reading, _ = run_poll([chtm_frame(-99), chtm_frame(-20),   # not a receiver
                           chtm_frame(-104), chtm_frame(-104)], samples=2)
    assert -20 not in reading.floors


def test_strongest_interference_burst_is_kept():
    reading, _ = run_poll([chtm_frame(-99), chtm_frame(-104, ntf_dbm=-80),
                           chtm_frame(-104, ntf_dbm=-44),
                           chtm_frame(-104)], samples=3)
    assert reading.interference_dbm == -44


# --------------------------------------------------------------- the target

def test_target_needs_exactly_one_work_board():
    work = {"/dev/serial/by-id/usb-T114": True,
            "/dev/serial/by-id/usb-Jonesey": False}
    port, problem = find_test_board(is_work_board=lambda p: work[p],
                                    listing=list(work))
    assert port == "/dev/serial/by-id/usb-T114" and problem is None

    port, problem = find_test_board(is_work_board=lambda p: False,
                                    listing=list(work))
    assert port is None and problem[0] == "no_board"

    port, problem = find_test_board(is_work_board=lambda p: True,
                                    listing=list(work))
    assert port is None and problem[0] == "too_many"


def test_the_medics_own_radio_is_never_offered():
    # even though this flow only reads, the roster guard applies
    port, problem = find_test_board(
        is_work_board=lambda p: "Jonesey" not in p,
        listing=["/dev/serial/by-id/usb-Jonesey"])
    assert port is None and problem[0] == "no_board"


# -------------------------------------------------------------- the session

def reading(*floors, ntf=None):
    r = EarReading()
    r.floors = list(floors)
    r.interference_dbm = ntf
    return r


def test_ranking_is_by_ear_best_floor_first():
    s = AntennaSession()
    s.add("stubby", reading(-112, -112))
    s.add("8cm", reading(-103, -103))
    s.add("40cm", reading(-104, -104))
    ranked = s.rank()
    assert [r.label for r in ranked] == ["8cm", "40cm", "stubby"]
    assert ranked[0].verdict == "best"
    assert ranked[1].verdict == "good"


def test_the_sponge_gap_flags_a_suspect():
    # the -118-vs--103 bench signature: matched sponge / wrong band / broken
    s = AntennaSession()
    s.add("8cm", reading(-103, -103))
    s.add("17cm sponge", reading(-118, -118))
    ranked = s.rank()
    assert ranked[-1].verdict == "suspect"
    assert ranked[-1].floor - ranked[0].floor <= -SPONGE_GAP_DB


def test_mid_gap_is_weak_not_suspect():
    s = AntennaSession()
    s.add("8cm", reading(-103, -103))
    s.add("twin", reading(-107, -107))            # 4 dB down: weak, not suspect
    assert s.rank()[-1].verdict == "weak"


def test_an_empty_reading_is_refused_not_faked():
    s = AntennaSession()
    assert s.add("ghost", EarReading()) is None
    assert s.count == 0
