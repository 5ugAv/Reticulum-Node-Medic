"""Node activity profile — when-is-this-node-up patterns from heard timestamps."""

from monitor.history import (
    HistoryPoint, activity_profile, describe_activity, _fmt_hour,
)

DAY = 86400
BASE = 1000 * DAY            # midnight UTC of an arbitrary day (multiple of 86400)
NOW = BASE + 10 * DAY


def _evening():
    # heard at 18:00-21:00 UTC on three different days
    return [HistoryPoint(t=BASE + d * DAY + h * 3600)
            for d in (5, 6, 7) for h in (18, 19, 20, 21)]


def test_profile_evening_pattern():
    prof = activity_profile(_evening(), now=NOW, tz_offset_hours=0)
    assert prof["total"] == 12
    assert prof["days"] == 3
    assert prof["active_window"] == (18, 21)
    assert prof["busiest_hour"] in (18, 19, 20, 21)
    assert sum(prof["by_hour"][18:22]) == 12          # all activity in the evening
    assert sum(prof["by_hour"][:18]) == 0


def test_describe_evening_reads_as_a_window():
    d = describe_activity(activity_profile(_evening(), now=NOW))
    assert "12 times over 3 days" in d
    assert "Usually active 6pm-10pm" in d              # window 18..21 -> 6pm..(22)10pm


def test_tz_offset_shifts_local_hour():
    # same UTC evening, +10h local -> lands in the small hours next local day
    prof = activity_profile(_evening(), now=NOW, tz_offset_hours=10)
    assert prof["by_hour"][18] == 0                    # no longer 6pm local
    assert prof["total"] == 12                         # same events, shifted bucket


def test_empty_history_is_no_pattern():
    prof = activity_profile([], now=NOW)
    assert prof["total"] == 0
    assert prof["busiest_hour"] is None
    assert prof["active_window"] is None
    assert describe_activity(prof).startswith("Not enough data")


def test_single_day_does_not_claim_a_rhythm():
    pts = [HistoryPoint(t=BASE + 3 * DAY + h * 3600) for h in (19, 20, 21)]
    d = describe_activity(activity_profile(pts, now=NOW))
    assert "Heard 3 times over 1 day" in d
    assert "Usually active" not in d                   # one day isn't a pattern


def test_around_the_clock():
    pts = [HistoryPoint(t=BASE + d * DAY + h * 3600)
           for d in (1, 2, 3) for h in range(24)]
    assert "around the clock" in describe_activity(activity_profile(pts, now=NOW))


def test_window_ignores_outside_retention():
    old = [HistoryPoint(t=BASE - 60 * DAY + 3 * 3600)]     # 60 days before BASE
    recent = _evening()
    prof = activity_profile(old + recent, now=NOW, window_s=30 * DAY)
    assert prof["total"] == 12                          # the old point is dropped


def test_fmt_hour_edges():
    assert _fmt_hour(0) == "midnight"
    assert _fmt_hour(12) == "noon"
    assert _fmt_hour(6) == "6am"
    assert _fmt_hour(18) == "6pm"
    assert _fmt_hour(23) == "11pm"
