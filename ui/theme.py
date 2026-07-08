"""UI design system — palette, colour helpers and status thresholds.

Pure data / functions with no Kivy dependency so it can be unit-tested and
reused by non-graphical code (e.g. the monitor backend deciding a node's
status colour).
"""

from __future__ import annotations

from typing import Tuple

#: 1280x720 landscape dark theme palette.
COLORS = {
    "background": "#1a1a1a",
    "surface": "#242424",
    "sidebar": "#141414",
    "green": "#00c853",
    "amber": "#ff6d00",
    "red": "#d50000",
    "accent": "#4fc3f7",   # steel blue
    "text_primary": "#f0f0f0",
    "text_secondary": "#9e9e9e",
}

# Status thresholds (LiFePO4 deployment).
BATTERY_WARN_PCT = 20      # orange at or below
BATTERY_ALERT_PCT = 10     # red at or below
SIGNAL_WARN_DBM = -110     # orange at or below
SIGNAL_ALERT_DBM = -120    # red at or below
NOT_HEARD_ALERT_HOURS = 6  # red once not heard for this long


def hex_to_rgba(value: str, alpha: float = 1.0) -> Tuple[float, float, float, float]:
    """Convert ``#rrggbb`` to a Kivy ``(r, g, b, a)`` tuple of 0-1 floats."""
    value = value.lstrip("#")
    r = int(value[0:2], 16) / 255.0
    g = int(value[2:4], 16) / 255.0
    b = int(value[4:6], 16) / 255.0
    return (r, g, b, alpha)


def battery_status(pct: float) -> str:
    if pct <= BATTERY_ALERT_PCT:
        return "alert"
    if pct <= BATTERY_WARN_PCT:
        return "warn"
    return "ok"


def signal_status(dbm: float) -> str:
    if dbm <= SIGNAL_ALERT_DBM:
        return "alert"
    if dbm <= SIGNAL_WARN_DBM:
        return "warn"
    return "ok"


def last_seen_status(hours: float) -> str:
    return "alert" if hours > NOT_HEARD_ALERT_HOURS else "ok"


_STATUS_TO_COLOR = {
    "ok": "green",
    "warn": "amber",
    "alert": "red",
}


def status_color(status: str) -> str:
    """Map a status word to a palette hex string. Unknown -> grey."""
    return COLORS.get(_STATUS_TO_COLOR.get(status, "text_secondary"))


def status_rgba(status: str, alpha: float = 1.0) -> Tuple[float, float, float, float]:
    return hex_to_rgba(status_color(status), alpha)
