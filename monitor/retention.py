"""Beacon-history retention — how long stored node history is kept.

Every beacon/poll the registry ingests appends a compact point per node
(monitor.history.NodeHistory); the window is pruned to the retention setting.
Default 90 days, adjustable. This module persists the setting and estimates the
storage impact so Settings can show it.

Pure filesystem + arithmetic, unit-tested.
"""

from __future__ import annotations

import json
import os
from typing import Optional

CONFIG = os.path.expanduser("~/.reticulum-node-medic/retention.json")

DEFAULT_DAYS = 90
MIN_DAYS = 7
MAX_DAYS = 365
DAY_S = 86400

#: Rough size of one stored history point (a HistoryPoint as JSON + container
#: overhead). Used only for the storage-impact ESTIMATE shown in Settings.
BYTES_PER_POINT = 90
#: Assumed points appended per node per day (a beacon/poll roughly every 10 min).
POINTS_PER_DAY = 144

#: Selectable retention steps (days) the +/- stepper walks through.
STEPS = (7, 14, 30, 60, 90, 180, 365)


def _clamp(days: int) -> int:
    return max(MIN_DAYS, min(MAX_DAYS, int(days)))


def load_days(path: str = CONFIG) -> int:
    try:
        with open(path) as f:
            d = json.load(f)
        return _clamp(d["days"])
    except (OSError, ValueError, KeyError, TypeError):
        return DEFAULT_DAYS


def set_days(days: int, path: str = CONFIG) -> int:
    days = _clamp(days)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump({"days": days}, f, indent=2)
    return days


def retention_seconds(path: str = CONFIG) -> int:
    return load_days(path) * DAY_S


def step(days: int, direction: int) -> int:
    """Next/previous retention step from *days* (direction +1 / -1)."""
    steps = list(STEPS)
    if days not in steps:
        steps = sorted(set(steps) | {_clamp(days)})
    i = steps.index(_clamp(days)) + direction
    return steps[max(0, min(len(steps) - 1, i))]


def estimate_bytes(days: int, node_count: int,
                   points_per_day: int = POINTS_PER_DAY) -> int:
    """Estimated stored-history size for *node_count* nodes at *days* retention."""
    return max(0, int(days) * max(0, int(node_count)) * points_per_day * BYTES_PER_POINT)


def format_size(n: int) -> str:
    n = float(max(0, n))
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024.0
    return f"{n:.1f} GB"
