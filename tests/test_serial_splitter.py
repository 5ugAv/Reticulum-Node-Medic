"""The serial splitter's KISS demux: CMD_GPS frames are skimmed off and decoded,
everything else passes through to rnsd byte-for-byte. Pure — no hardware."""

import pytest

from monitor.serial_splitter import (
    KissGpsSplitter, CMD_STAT_RSSI, CMD_STAT_SNR, CMD_STAT_CHTM, RSSI_OFFSET,
)
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


# ---- signal-stat recording (frames still forwarded to rnsd) ----------------

def test_stat_rssi_and_snr_recorded_and_still_forwarded():
    s = KissGpsSplitter(now=lambda: 7.0)
    rssi_frame = _kiss(CMD_STAT_RSSI, bytes([-72 + RSSI_OFFSET]))
    snr_frame = _kiss(CMD_STAT_SNR, bytes([int(10.5 * 4)]))
    assert s.feed(rssi_frame) == rssi_frame        # forwarded byte-for-byte
    assert s.feed(snr_frame) == snr_frame
    st = s.state()
    assert st["last_rssi"] == -72
    assert st["last_snr"] == 10.5
    assert st["packet_heard_at"] == 7.0


def test_negative_snr_decodes_signed():
    s = KissGpsSplitter()
    s.feed(_kiss(CMD_STAT_SNR, (-9).to_bytes(1, "big", signed=True)))  # -2.25 dB
    assert s.state()["last_snr"] == -2.25


def test_channel_stats_frame_recorded_and_forwarded():
    # ats atl cls cll (u16 each) + crs nfl ntf
    payload = (int(0.253 * 10000)).to_bytes(2, "big")          # airtime 2.53%... (x100x100)
    payload += (0).to_bytes(2, "big")                           # atl
    payload += (int(0.31 * 10000)).to_bytes(2, "big")           # cls (channel load)
    payload += (0).to_bytes(2, "big")                           # cll
    payload += bytes([-96 + RSSI_OFFSET])                       # crs
    payload += bytes([-107 + RSSI_OFFSET])                      # nfl (noise floor)
    payload += bytes([0xFF])                                    # ntf: no interference
    frame = _kiss(CMD_STAT_CHTM, payload)
    s = KissGpsSplitter()
    assert s.feed(frame) == frame                               # forwarded
    st = s.state()
    assert st["noise_floor"] == -107
    assert st["airtime"] == pytest.approx(0.253, abs=1e-4)
    assert st["channel_load"] == pytest.approx(0.31, abs=1e-4)
    assert st["interference"] is None                           # 0xFF sentinel


def test_interference_value_decodes_when_present():
    payload = bytes(8) + bytes([-96 + RSSI_OFFSET, -107 + RSSI_OFFSET,
                                -74 + RSSI_OFFSET])
    s = KissGpsSplitter()
    s.feed(_kiss(CMD_STAT_CHTM, payload))
    assert s.state()["interference"] == -74


def test_sampleton_coordinate_survives_firmware_format_roundtrip():
    """A known Sampleton fix, encoded exactly as the firmware sends it (int32
    microdegrees, big-endian, KISS-escaped, negative latitude for the southern
    hemisphere), must decode back to the same coordinate through the splitter.
    This is the 'is the GPS maths right' guard the handover asked for — adapted
    to our CMD_GPS path (the firmware's on-device TinyGPS++ does the NMEA
    DDMM->decimal conversion, so it never reaches the Pi)."""
    LAT, LNG = -37.813600, 144.963100          # Sampleton CBD
    s = KissGpsSplitter()
    s.feed(_gps(GPS_CMD_LAT, LAT))
    s.feed(_gps(GPS_CMD_LNG, LNG))
    st = s.state()
    assert st["lat"] == pytest.approx(LAT, abs=1e-6)
    assert st["lng"] == pytest.approx(LNG, abs=1e-6)
    assert st["has_fix"] is True


# --- "no GPS at all" and "GPS with no lock" are different faults ------------
# 2026-08-07: Jonesey reported healthy radio telemetry (rssi -63, snr 10.75) and
# lat/lng null, sats 0, fix 0. Those zeros are the INITIAL values and only move
# when a GPS_CMD_STATE frame arrives — so the state read identically for
#   (a) firmware with no GPS support, and
#   (b) a working receiver that has not locked yet.
# One needs a reflash, the other needs a window. The medic could not tell them
# apart, so neither could the operator.

def test_a_radio_that_never_mentions_gps_is_distinguishable_from_one_with_no_lock():
    from monitor.serial_splitter import KissGpsSplitter
    st = KissGpsSplitter(now=lambda: 100.0)
    assert st.state()["gps_frames"] == 0, "silence must be visible as silence"
    assert st.state()["gps_seen_at"] is None


def test_a_gps_state_frame_is_counted_even_when_it_reports_no_lock():
    """The frame that says 'I have no fix' is still PROOF the GPS is alive and
    talking — it is the single most useful fact when a fix is missing."""
    from monitor.serial_splitter import (CMD_GPS, GPS_CMD_STATE, KissGpsSplitter)
    st = KissGpsSplitter(now=lambda: 100.0)
    st._consume_gps(bytearray([CMD_GPS, GPS_CMD_STATE, 0, 0]))   # 0 sats, no fix
    s = st.state()
    assert s["gps_frames"] == 1
    assert s["gps_seen_at"] == 100.0
    assert s["sats"] == 0 and s["has_fix"] is False


def test_every_gps_frame_counts_not_just_state_ones():
    from monitor.serial_splitter import (CMD_GPS, GPS_CMD_LAT, GPS_CMD_STATE,
                                         KissGpsSplitter)
    st = KissGpsSplitter(now=lambda: 1.0)
    st._consume_gps(bytearray([CMD_GPS, GPS_CMD_LAT, 0, 0, 0, 0]))
    st._consume_gps(bytearray([CMD_GPS, GPS_CMD_STATE, 7, 1]))
    assert st.state()["gps_frames"] == 2


def test_non_gps_frames_never_inflate_the_count():
    """Radio stat frames pour through constantly. If they counted, gps_frames
    would say the GPS is talking on a board that has none — which is exactly
    the confusion this exists to end."""
    from monitor.serial_splitter import CMD_GPS, KissGpsSplitter
    st = KissGpsSplitter(now=lambda: 1.0)
    assert st._consume_gps(bytearray([0x07, 0x01, 0x02])) is False
    assert st.state()["gps_frames"] == 0


# --- satellite UTC (GPS_CMD_UTC 0x03) for offline clock discipline ----------
# Pi 5 RTC is not battery-backed + no NTP afield, so the firmware pushes UTC.
# [year_hi, year_lo, month, day, hour, minute, second], year u16 big-endian.

def _utc_frame(year, month, day, hour, minute, second):
    from monitor.serial_splitter import CMD_GPS, GPS_CMD_UTC
    payload = bytes([year >> 8, year & 0xFF, month, day, hour, minute, second])
    return _kiss(CMD_GPS, bytes([GPS_CMD_UTC]) + payload)


def test_valid_utc_frame_sets_gps_utc_to_the_right_epoch():
    import calendar
    from monitor.serial_splitter import KissGpsSplitter
    s = KissGpsSplitter(now=lambda: 4242.0)
    out = s.feed(_utc_frame(2026, 8, 22, 1, 2, 3))    # 2026-08-22 01:02:03 UTC
    assert out == b""                                 # consumed, not forwarded
    st = s.state()
    assert st["gps_utc"] == calendar.timegm((2026, 8, 22, 1, 2, 3)) == 1787360523
    assert st["gps_utc_recv"] == 4242.0               # when we RECEIVED it


def test_out_of_range_month_is_rejected_no_garbage_clock():
    from monitor.serial_splitter import KissGpsSplitter
    s = KissGpsSplitter()
    s.feed(_utc_frame(2026, 13, 22, 1, 2, 3))          # month 13 is impossible
    assert s.state()["gps_utc"] is None
    assert s.state()["gps_utc_recv"] is None


def test_out_of_range_year_is_rejected():
    from monitor.serial_splitter import KissGpsSplitter
    s = KissGpsSplitter()
    s.feed(_utc_frame(1999, 8, 22, 1, 2, 3))           # before 2020 floor
    assert s.state()["gps_utc"] is None


def test_impossible_calendar_dates_are_rejected():
    """KISS has no CRC — a bit-flip in the day byte can make Feb 31 / Apr 31,
    which calendar.timegm would SILENTLY normalize to a wrong-but-plausible epoch.
    datetime() raises on them, so we reject and set nothing."""
    from monitor.serial_splitter import KissGpsSplitter
    for month, day in ((2, 31), (4, 31)):          # Feb 31, Apr 31
        s = KissGpsSplitter()
        s.feed(_utc_frame(2026, month, day, 1, 2, 3))
        assert s.state()["gps_utc"] is None, f"{month}/{day} must be rejected"


def test_leap_second_60_is_accepted():
    from monitor.serial_splitter import KissGpsSplitter
    s = KissGpsSplitter()
    s.feed(_utc_frame(2026, 12, 31, 23, 59, 60))       # leap second is valid
    assert s.state()["gps_utc"] is not None


def test_no_utc_frame_leaves_gps_utc_none_backward_compatible():
    """Old firmware never sends 0x03 — lat/lng/state still work, UTC stays None."""
    s = KissGpsSplitter()
    s.feed(_gps(GPS_CMD_LAT, -37.8))
    s.feed(_kiss(CMD_GPS, bytes([GPS_CMD_STATE, 9, 1])))
    st = s.state()
    assert st["gps_utc"] is None and st["gps_utc_recv"] is None
    assert st["sats"] == 9 and st["lat"] == pytest.approx(-37.8, abs=1e-6)


# --- fix quality: accuracy (HDOP 0x04) and altitude (0x05) ------------------
# The firmware already emits these; the medic used to ignore them, so accuracy
# and altitude read None on every certificate. Absence stays None (backward
# compatible); a glitched out-of-range value is rejected to None, never stamped.

def _acc_frame(hdop_x100: int) -> bytes:
    from monitor.serial_splitter import CMD_GPS, GPS_CMD_ACCURACY
    return _kiss(CMD_GPS, bytes([GPS_CMD_ACCURACY]) + hdop_x100.to_bytes(2, "big"))


def _alt_frame(metres: int) -> bytes:
    from monitor.serial_splitter import CMD_GPS, GPS_CMD_ALT
    return _kiss(CMD_GPS,
                 bytes([GPS_CMD_ALT]) + int(metres).to_bytes(2, "big", signed=True))


def test_accuracy_frame_sets_hdop():
    s = KissGpsSplitter()
    out = s.feed(_acc_frame(0x0078))          # 120 -> HDOP 1.20
    assert out == b""                         # consumed, not forwarded
    assert s.state()["hdop"] == pytest.approx(1.20, abs=1e-9)


def test_altitude_frame_sets_alt_m():
    s = KissGpsSplitter()
    out = s.feed(_alt_frame(325))
    assert out == b""
    assert s.state()["alt_m"] == 325


def test_negative_altitude_decodes_signed():
    """Below sea level (e.g. Dead Sea shore) must survive as a negative int."""
    s = KissGpsSplitter()
    s.feed(_alt_frame(-412))
    assert s.state()["alt_m"] == -412


def test_absurd_hdop_is_rejected_to_none():
    s = KissGpsSplitter()
    s.feed(_acc_frame(0xFFFF))                 # 655.35 -> nonsense
    assert s.state()["hdop"] is None


def test_absurd_altitude_is_rejected_to_none():
    s2 = KissGpsSplitter()
    s2.feed(_alt_frame(-32000))                # far below the Dead Sea -> reject
    assert s2.state()["alt_m"] is None
    s3 = KissGpsSplitter()
    s3.feed(_alt_frame(12001))                 # above the plausible cap -> reject
    assert s3.state()["alt_m"] is None


def test_no_accuracy_or_altitude_frames_leaves_both_none_backward_compatible():
    """Old firmware never sends 0x04/0x05 — position still works, quality None."""
    s = KissGpsSplitter()
    s.feed(_gps(GPS_CMD_LAT, -37.8))
    s.feed(_gps(GPS_CMD_LNG, 144.9))
    s.feed(_kiss(CMD_GPS, bytes([GPS_CMD_STATE, 9, 1])))
    st = s.state()
    assert st["hdop"] is None and st["alt_m"] is None
    assert st["has_fix"] is True and st["sats"] == 9
