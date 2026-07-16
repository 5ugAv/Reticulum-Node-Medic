"""Channel utilisation estimator — thresholds, bar, stat line, congestion hint."""

import pytest

from monitor.channel_util import (
    utilisation_label, utilisation_bar, stat_line,
    busiest_announcer, congestion_hint,
)
from monitor.registry import NodeRegistry
from monitor.history import HistoryPoint


@pytest.mark.parametrize("frac,label,sev", [
    (0.0, "Healthy", "ok"),
    (0.12, "Healthy", "ok"),
    (0.25, "Moderate", "ok"),
    (0.49, "Moderate", "ok"),
    (0.50, "Busy", "warn"),
    (0.75, "Congested - consider reducing announce frequency", "warn"),
    (0.90, "Critical - packet loss likely", "alert"),
    (1.0, "Critical - packet loss likely", "alert"),
])
def test_threshold_bands(frac, label, sev):
    assert utilisation_label(frac) == (label, sev)


def test_bar_is_ascii_and_proportional():
    assert utilisation_bar(0.0) == "[----------]"
    assert utilisation_bar(0.12) == "[#---------]"
    assert utilisation_bar(0.5) == "[#####-----]"
    assert utilisation_bar(1.0) == "[##########]"
    assert utilisation_bar(0.5).isascii()


def test_stat_line_matches_spec_shape():
    line = stat_line(0.12)
    assert line == "Channel utilisation: 12% [#---------] Healthy"
    assert "unknown" in stat_line(None)


def _registry_with_traffic(now):
    r = NodeRegistry()
    r.register("aaaa", name="QuietNode")
    r.register("bbbb", name="OutpostNorth")
    for i in range(3):
        r.history.append("aaaa", HistoryPoint(t=now - 100 * i))
    for i in range(12):
        r.history.append("bbbb", HistoryPoint(t=now - 200 * i))
    return r


def test_busiest_announcer_names_the_loudest_node():
    now = 100000.0
    assert busiest_announcer(_registry_with_traffic(now), now) == ("OutpostNorth", 12)


def test_busiest_announcer_ignores_old_traffic():
    now = 100000.0
    r = NodeRegistry()
    r.register("cccc", name="Ancient")
    r.history.append("cccc", HistoryPoint(t=now - 7200))     # 2h ago, outside window
    assert busiest_announcer(r, now) is None


def test_congestion_hint_only_when_busy():
    now = 100000.0
    r = _registry_with_traffic(now)
    assert congestion_hint(0.12, r, now) is None             # healthy: no finger-pointing
    hint = congestion_hint(0.62, r, now)
    assert hint is not None and "OutpostNorth" in hint and "announce" in hint


def test_congestion_hint_without_data_is_none():
    assert congestion_hint(None, NodeRegistry(), 0.0) is None
    assert congestion_hint(0.9, NodeRegistry(), 0.0) is None  # busy but nobody heard
