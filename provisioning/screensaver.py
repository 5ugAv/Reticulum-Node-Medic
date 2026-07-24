"""Screen-saver settings — protect the always-on touchscreen from burn-in.

The home/VITALS screens hold static bright elements; after a spell of no touches a
moving screensaver takes over (dismissed by a tap). Selectable styles (first: a
50's hypnotic black/off-white swirl). Pure settings store + validation; the Kivy
overlay lives in ui.widgets.screensaver.
"""

from __future__ import annotations

import json
import os
from typing import Dict, List

CONFIG = os.path.expanduser("~/.reticulum-node-medic/screensaver.json")

#: Available styles (registry mirrored in ui.widgets.screensaver). Extend both.
STYLES: List[str] = ["swirl"]
STYLE_LABELS = {"swirl": "Hypnotic swirl (50's)"}

#: Idle delay choices, seconds.
IDLE_STEPS = [60, 120, 180, 300, 600, 1800]
MIN_IDLE_S, MAX_IDLE_S = 30, 3600

DEFAULTS: Dict = {"enabled": True, "style": "swirl", "idle_delay_s": 180}


def load(path: str = CONFIG) -> Dict:
    d = {}
    try:
        with open(path) as f:
            raw = json.load(f)
        if isinstance(raw, dict):
            d = raw
    except (OSError, ValueError):
        pass
    style = d.get("style")
    try:
        idle = int(d.get("idle_delay_s", DEFAULTS["idle_delay_s"]))
    except (TypeError, ValueError):
        idle = DEFAULTS["idle_delay_s"]
    return {
        "enabled": bool(d.get("enabled", DEFAULTS["enabled"])),
        "style": style if style in STYLES else DEFAULTS["style"],
        "idle_delay_s": max(MIN_IDLE_S, min(MAX_IDLE_S, idle)),
    }


def save(settings: Dict, path: str = CONFIG) -> Dict:
    s = load(path)
    for k in ("enabled", "style", "idle_delay_s"):
        if k in settings:
            s[k] = settings[k]
    s = load_from(s)                      # re-validate
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(s, f, indent=2)
    return s


def load_from(d: Dict) -> Dict:
    """Validate an in-memory dict the same way load() validates a file."""
    try:
        idle = int(d.get("idle_delay_s", DEFAULTS["idle_delay_s"]))
    except (TypeError, ValueError):
        idle = DEFAULTS["idle_delay_s"]
    return {
        "enabled": bool(d.get("enabled", DEFAULTS["enabled"])),
        "style": d.get("style") if d.get("style") in STYLES else DEFAULTS["style"],
        "idle_delay_s": max(MIN_IDLE_S, min(MAX_IDLE_S, idle)),
    }


def is_enabled(path: str = CONFIG) -> bool:
    return load(path)["enabled"]


def set_enabled(enabled: bool, path: str = CONFIG) -> Dict:
    return save({"enabled": bool(enabled)}, path)


def style(path: str = CONFIG) -> str:
    return load(path)["style"]


def set_style(s: str, path: str = CONFIG) -> Dict:
    return save({"style": s}, path)


def idle_delay_s(path: str = CONFIG) -> int:
    return load(path)["idle_delay_s"]


def set_idle_delay_s(n: int, path: str = CONFIG) -> Dict:
    return save({"idle_delay_s": int(n)}, path)


def step_idle(current: int, direction: int) -> int:
    steps = list(IDLE_STEPS)
    if current not in steps:
        steps = sorted(set(steps) | {max(MIN_IDLE_S, min(MAX_IDLE_S, current))})
    i = steps.index(max(MIN_IDLE_S, min(MAX_IDLE_S, current))) + direction
    return steps[max(0, min(len(steps) - 1, i))]


def format_delay(seconds: int) -> str:
    m = seconds / 60.0
    if seconds < 60:
        return f"{seconds} s"
    return f"{int(m)} min" if m == int(m) else f"{m:.1f} min"
