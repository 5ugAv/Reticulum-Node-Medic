"""Node health history — the VITALS "History" backend (backlog feature 1).

Every beacon / HTTP poll the registry ingests appends one compact point per
node: timestamp, signal, uptime (and battery when a node type reports it —
none do yet, but the schema is ready). The series feeds the 30-day history
graph, and ``analyse()`` turns it into plain-English pattern flags:

* battery declining over multiple weeks
* signal gradually worsening ("check antenna")
* frequent restarts ("check power supply")
* a sudden signal drop on a specific date ("possible antenna/obstruction change")

Pure data + maths, injected clocks, no hardware. History is pruned to 90 days
and travels inside the monitoring DB that MITOSIS copies.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

RETENTION_S = 90 * 86400          # keep 90 days of points per node
GRAPH_WINDOW_S = 30 * 86400       # the graph shows the last 30 days

# analysis thresholds
BATTERY_DECLINE_PCT_PER_DAY = 1.0   # sustained fall faster than this -> flag
BATTERY_MIN_SPAN_S = 7 * 86400
SIGNAL_DEGRADE_DB = 6               # first-third vs last-third drop -> flag
SIGNAL_MIN_SPAN_S = 7 * 86400
RESTARTS_WINDOW_S = 7 * 86400
RESTARTS_FLAG_COUNT = 3
SUDDEN_DROP_DB = 10                 # adjacent-sample fall that STAYS low -> flag


@dataclass
class HistoryPoint:
    t: float                        # epoch seconds
    rssi: Optional[int] = None      # dBm (beacon wifi_rssi / mesh rssi)
    uptime_s: Optional[int] = None  # node-reported uptime (restart detection)
    battery_pct: Optional[float] = None   # no node type reports this yet

    def to_dict(self) -> dict:
        return {"t": self.t, "rssi": self.rssi, "uptime_s": self.uptime_s,
                "battery_pct": self.battery_pct}


class NodeHistory:
    """Per-node time series, pruned to ``retention_s`` (default 90 days; the
    runtime overrides it from the Settings ▸ Beacon history retention value)."""

    def __init__(self, retention_s: int = RETENTION_S):
        self._series: Dict[str, List[HistoryPoint]] = {}
        self.retention_s = retention_s

    def append(self, dst_hash: str, point: HistoryPoint) -> None:
        pts = self._series.setdefault(dst_hash, [])
        pts.append(point)
        cutoff = point.t - self.retention_s
        if pts and pts[0].t < cutoff:
            self._series[dst_hash] = [p for p in pts if p.t >= cutoff]

    def set_retention(self, retention_s: int, now: float) -> None:
        """Change the retention window and re-prune every series to it now."""
        self.retention_s = retention_s
        cutoff = now - retention_s
        for h in list(self._series):
            self._series[h] = [p for p in self._series[h] if p.t >= cutoff]

    def series(self, dst_hash: str, since: Optional[float] = None
               ) -> List[HistoryPoint]:
        pts = self._series.get(dst_hash, [])
        if since is None:
            return list(pts)
        return [p for p in pts if p.t >= since]

    def graph_series(self, dst_hash: str, now: float) -> List[HistoryPoint]:
        return self.series(dst_hash, since=now - GRAPH_WINDOW_S)

    # -- persistence (rides in the monitoring DB) ---------------------------

    def to_dict(self) -> dict:
        return {h: [p.to_dict() for p in pts] for h, pts in self._series.items()}

    @classmethod
    def from_dict(cls, data: dict, retention_s: int = RETENTION_S) -> "NodeHistory":
        h = cls(retention_s=retention_s)
        for dst, pts in (data or {}).items():
            h._series[dst] = [HistoryPoint(**p) for p in pts]
        return h


# ---- pattern analysis -------------------------------------------------------

def _slope_per_day(points: List[tuple]) -> float:
    """Least-squares slope of (t, value) points, in value-units per day."""
    n = len(points)
    xs = [t / 86400.0 for t, _v in points]
    ys = [v for _t, v in points]
    mx, my = sum(xs) / n, sum(ys) / n
    den = sum((x - mx) ** 2 for x in xs)
    if den == 0:
        return 0.0
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den


def analyse(points: List[HistoryPoint], now: float) -> List[dict]:
    """Plain-English pattern flags for a node's history. Each flag is
    ``{"key", "severity", "text"}``; an empty list means nothing suspicious."""
    flags: List[dict] = []

    # battery trending down despite time passing (only when battery data exists)
    batt = [(p.t, p.battery_pct) for p in points if p.battery_pct is not None]
    if len(batt) >= 5 and (batt[-1][0] - batt[0][0]) >= BATTERY_MIN_SPAN_S:
        slope = _slope_per_day(batt)
        if slope <= -BATTERY_DECLINE_PCT_PER_DAY:
            flags.append({
                "key": "battery_declining", "severity": "warn",
                "text": "Battery declining - losing about "
                        f"{abs(slope):.1f}% per day over the last "
                        f"{(batt[-1][0] - batt[0][0]) / 86400:.0f} days."})

    sig = [(p.t, p.rssi) for p in points if p.rssi is not None]
    if len(sig) >= 6 and (sig[-1][0] - sig[0][0]) >= SIGNAL_MIN_SPAN_S:
        third = max(1, len(sig) // 3)
        early = sum(v for _t, v in sig[:third]) / third
        late = sum(v for _t, v in sig[-third:]) / third
        if early - late >= SIGNAL_DEGRADE_DB:
            flags.append({
                "key": "signal_degrading", "severity": "warn",
                "text": "Signal gradually worsening - down about "
                        f"{early - late:.0f} dB. Check the antenna and its "
                        "connector."})

    # sudden drop: one step down of >= SUDDEN_DROP_DB that stays low
    for i in range(1, len(sig) - 1):
        if sig[i - 1][1] - sig[i][1] >= SUDDEN_DROP_DB:
            after = [v for _t, v in sig[i:i + 3]]
            if all(sig[i - 1][1] - v >= SUDDEN_DROP_DB * 0.8 for v in after):
                import datetime
                day = datetime.datetime.fromtimestamp(
                    sig[i][0], datetime.timezone.utc).strftime("%d %b %Y")
                flags.append({
                    "key": "sudden_rssi_drop", "severity": "warn",
                    "text": f"Signal dropped sharply around {day} and stayed "
                            "low - possibly an antenna knock or a new "
                            "obstruction."})
                break

    # frequent restarts: node-reported uptime going BACKWARDS = a reboot
    ups = [(p.t, p.uptime_s) for p in points
           if p.uptime_s is not None and p.t >= now - RESTARTS_WINDOW_S]
    restarts = sum(1 for a, b in zip(ups, ups[1:]) if b[1] < a[1])
    if restarts >= RESTARTS_FLAG_COUNT:
        flags.append({
            "key": "frequent_restarts", "severity": "warn",
            "text": f"Restarted {restarts} times in the last week - check the "
                    "power supply and connections."})

    return flags
