"""Node health history — series storage, pruning, pattern flags, persistence."""

from monitor.history import (
    NodeHistory, HistoryPoint, analyse,
    RETENTION_S, GRAPH_WINDOW_S,
)
from monitor.registry import NodeRegistry
from monitor.health_beacon import encode, decode

DAY = 86400.0
H = "ad272c6b32b6dbbe62a7d6c8c7cbbf42"


def _pt(t, rssi=None, uptime=None, batt=None):
    return HistoryPoint(t=t, rssi=rssi, uptime_s=uptime, battery_pct=batt)


# ---- storage ----------------------------------------------------------------

def test_appends_and_reads_series():
    h = NodeHistory()
    h.append(H, _pt(100, rssi=-80))
    h.append(H, _pt(200, rssi=-82))
    assert [p.rssi for p in h.series(H)] == [-80, -82]
    assert h.series("unknown") == []


def test_prunes_beyond_ninety_days():
    h = NodeHistory()
    h.append(H, _pt(0.0, rssi=-80))
    h.append(H, _pt(RETENTION_S + DAY, rssi=-81))     # 91 days later
    assert [p.rssi for p in h.series(H)] == [-81]


def test_graph_series_is_last_thirty_days():
    h = NodeHistory()
    now = 100 * DAY
    h.append(H, _pt(now - 40 * DAY, rssi=-70))
    h.append(H, _pt(now - 10 * DAY, rssi=-75))
    assert [p.rssi for p in h.graph_series(H, now)] == [-75]


def test_roundtrips_through_dict():
    h = NodeHistory()
    h.append(H, _pt(50, rssi=-88, uptime=3600))
    again = NodeHistory.from_dict(h.to_dict())
    p = again.series(H)[0]
    assert (p.t, p.rssi, p.uptime_s) == (50, -88, 3600)


# ---- registry integration ---------------------------------------------------

def _beacon(uptime=100, rssi=-62):
    return decode(encode(uptime_s=uptime, heap_kb=140, wifi_rssi_dbm=rssi,
                         reset_reason=0, wifi_up=True, lora_up=True,
                         tcp_backbone_up=True, local_tcp_server_up=True,
                         wdt_armed=True, psram=True, fault=False,
                         board_id=0x3F, fw=(0, 6, 2)))


def test_registry_ingest_appends_history_and_persists():
    r = NodeRegistry()
    r.ingest(H, _beacon(uptime=100, rssi=-62), now=1000.0)
    r.ingest(H, _beacon(uptime=200, rssi=-64), now=2000.0)
    assert [p.rssi for p in r.history.series(H)] == [-62, -64]
    again = NodeRegistry.from_dict(r.to_dict())
    assert [p.uptime_s for p in again.history.series(H)] == [100, 200]


# ---- pattern flags ------------------------------------------------------------

def _keys(flags):
    return {f["key"] for f in flags}


def test_healthy_history_has_no_flags():
    now = 30 * DAY
    # steady signal, uptime growing with time (no reboots)
    pts = [_pt(now - i * DAY, rssi=-80, uptime=int((15 - i) * DAY))
           for i in range(14, 0, -1)]
    assert analyse(pts, now) == []


def test_signal_degrading_flags():
    now = 30 * DAY
    pts = [_pt(now - (14 - i) * DAY, rssi=-75 - i) for i in range(14)]  # -75 -> -88
    flags = analyse(pts, now)
    assert "signal_degrading" in _keys(flags)
    assert any("antenna" in f["text"] for f in flags)


def test_sudden_drop_flags_with_a_date():
    now = 30 * DAY
    pts = ([_pt(now - (20 - i) * DAY, rssi=-75) for i in range(10)] +
           [_pt(now - (10 - i) * DAY, rssi=-95) for i in range(10)])   # step down 20 dB
    flags = analyse(pts, now)
    assert "sudden_rssi_drop" in _keys(flags)


def test_frequent_restarts_flags():
    now = 10 * DAY
    # uptime keeps falling back to small values = reboots (4 in the last week)
    seq = [(6.0, 90000), (5.5, 100), (5.0, 40000), (4.5, 200),
           (4.0, 30000), (3.5, 300), (3.0, 20000), (2.5, 100)]
    pts = [_pt(now - d * DAY, uptime=u) for d, u in seq]
    flags = analyse(pts, now)
    assert "frequent_restarts" in _keys(flags)
    assert any("power" in f["text"] for f in flags)


def test_battery_declining_flags_when_data_exists():
    now = 30 * DAY
    pts = [_pt(now - (10 - i) * DAY, batt=90 - i * 3) for i in range(10)]  # -3%/day
    flags = analyse(pts, now)
    assert "battery_declining" in _keys(flags)


def test_no_battery_flag_without_battery_data():
    now = 30 * DAY
    pts = [_pt(now - i * DAY, rssi=-80) for i in range(10)]
    assert "battery_declining" not in _keys(analyse(pts, now))
