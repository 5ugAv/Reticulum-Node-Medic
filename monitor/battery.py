"""Battery runtime estimator — backlog feature 7 (engine).

Extrapolates days-remaining from the node's battery history (the same points
the VITALS History stores). Honest by design: it returns nothing at all for
nodes with no battery data (mains-powered Pis, RTNode-2400s — no node type
reports battery yet, so today this is dormant plumbing awaiting that field),
declares "solar maintaining charge" when the trend isn't falling, and always
carries the uncertainty note.
"""

from __future__ import annotations

from typing import List, Optional

from monitor.history import HistoryPoint, _slope_per_day

RED_THRESHOLD_PCT = 10.0      # "days remaining" counts down to this
MIN_POINTS = 5
MIN_SPAN_S = 3 * 86400        # need a few days of trend before estimating
UNCERTAINTY = ("Estimate only - based on observed trends. Actual runtime "
               "depends on weather and traffic.")


def estimate_runtime(points: List[HistoryPoint], now: float) -> Optional[dict]:
    """``None`` when there's no battery story to tell; otherwise
    ``{"kind": "indefinite"|"days", "days": float|None, "text", "note"}``."""
    batt = [(p.t, p.battery_pct) for p in points if p.battery_pct is not None]
    if len(batt) < MIN_POINTS or (batt[-1][0] - batt[0][0]) < MIN_SPAN_S:
        return None
    current = batt[-1][1]
    slope = _slope_per_day(batt)          # % per day; negative = draining

    if slope >= -0.1:                     # flat or charging: solar is keeping up
        return {"kind": "indefinite", "days": None,
                "text": "Solar maintaining charge - runtime indefinite",
                "note": UNCERTAINTY}

    days = max(0.0, (current - RED_THRESHOLD_PCT) / -slope)
    return {"kind": "days", "days": round(days, 1),
            "text": (f"Battery runtime estimate: ~{days:.0f} days remaining "
                     f"(now {current:.0f}%, losing about {abs(slope):.1f}% per day)"),
            "note": UNCERTAINTY}
