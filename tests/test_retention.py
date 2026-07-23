"""Beacon-history retention setting + configurable pruning (Settings item 5)."""

from monitor import retention
from monitor.history import NodeHistory, HistoryPoint, RETENTION_S


def test_default_days(tmp_path):
    assert retention.load_days(path=str(tmp_path / "r.json")) == 90


def test_set_and_load_days(tmp_path):
    p = str(tmp_path / "r.json")
    assert retention.set_days(30, path=p) == 30
    assert retention.load_days(path=p) == 30
    assert retention.retention_seconds(path=p) == 30 * 86400


def test_days_clamped_to_range(tmp_path):
    p = str(tmp_path / "r.json")
    assert retention.set_days(1, path=p) == retention.MIN_DAYS
    assert retention.set_days(9999, path=p) == retention.MAX_DAYS


def test_step_walks_presets():
    assert retention.step(90, +1) == 180
    assert retention.step(90, -1) == 60
    assert retention.step(7, -1) == 7            # clamped at the low end
    assert retention.step(365, +1) == 365        # clamped at the high end


def test_estimate_scales_with_days_and_nodes():
    a = retention.estimate_bytes(90, 10)
    b = retention.estimate_bytes(180, 10)
    c = retention.estimate_bytes(90, 20)
    assert b == 2 * a and c == 2 * a
    assert retention.estimate_bytes(90, 0) == 0


def test_format_size():
    assert retention.format_size(512) == "512 B"
    assert retention.format_size(2 * 1024) == "2.0 KB"
    assert retention.format_size(5 * 1024 * 1024) == "5.0 MB"


# ---- configurable pruning on NodeHistory ---------------------------------

def test_history_defaults_to_90_day_retention():
    assert NodeHistory().retention_s == RETENTION_S


def test_append_prunes_to_configured_window():
    h = NodeHistory(retention_s=10 * 86400)          # 10 days
    h.append("aa", HistoryPoint(t=0))                # old
    h.append("aa", HistoryPoint(t=20 * 86400))       # 20 days later -> old dropped
    assert [p.t for p in h.series("aa")] == [20 * 86400]


def test_set_retention_reprunes_existing_series():
    h = NodeHistory(retention_s=90 * 86400)
    now = 100 * 86400
    for d in (10, 40, 95):                            # days ago from `now`
        h.append("aa", HistoryPoint(t=now - d * 86400))
    h.set_retention(30 * 86400, now)                  # tighten to 30 days
    kept = sorted(int((now - p.t) / 86400) for p in h.series("aa"))
    assert kept == [10]                               # only the 10-day-old point
