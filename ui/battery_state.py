"""The battery gauge's PURE view-model — importable with no Kivy anywhere.

Split from ui/widgets/battery_icon.py the hard way: CI has no Kivy, and the
first cut put this function beside the widget, which took down collection on
both Python versions (2026-08-25). The house pattern is the one monitor/ups.py
uses — a pure, dependency-free core the tests import, and a thin UI shell that
imports the heavy toolkit.
"""

from __future__ import annotations

from typing import Optional

#: Fill colour bands (operator spec, 2026-08-25): red at/below 15%, yellow
#: at/below 20%, green above.
RED_FRACTION = 0.15
YELLOW_FRACTION = 0.20


def battery_view(reading) -> Optional[dict]:
    """What the icon should show for a UPS *reading*.

    ``None`` when there is nothing honest to draw (no UPS / no percentage).
    Otherwise ``{"fraction": 0..1, "charging": bool, "level": green|yellow|red}``.
    """
    if reading is None or not getattr(reading, "present", False):
        return None
    pct = getattr(reading, "percent", None)
    if pct is None:
        return None
    try:
        frac = max(0.0, min(1.0, float(pct) / 100.0))
    except (TypeError, ValueError):
        return None
    if frac <= RED_FRACTION:
        level = "red"
    elif frac <= YELLOW_FRACTION:
        level = "yellow"
    else:
        level = "green"
    return {"fraction": frac,
            "charging": bool(getattr(reading, "charging", False)),
            "level": level}
