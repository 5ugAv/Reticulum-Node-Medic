"""A breadcrumb trail of 'still under construction' features people actually hit.

When the operator taps a path that isn't built yet, we append a line here. The
developer (who SSHes into the medic between sessions) reads this to see what real
users reached for — a cheap, honest way to catch bugs and prioritise what to build
next, straight from the field instead of guessing.

Offline-first: it's a local append-only JSONL file, no network. Best-effort — a
logging failure must never break the UI.
"""

from __future__ import annotations

import json
import os
import time

LOG_PATH = os.path.expanduser("~/.reticulum-node-medic/construction.log")


def log_hit(title: str, detail: str = "", note: str = "") -> None:
    """Record that an under-construction feature was reached. *note* is optional
    free text from the operator. Never raises."""
    try:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        entry = {"t": round(time.time()), "title": title,
                 "detail": detail[:300], "note": note[:500]}
        with open(LOG_PATH, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        pass


def read_hits(path: str = LOG_PATH) -> list:
    """All recorded hits (newest last); [] if none. For the developer / a future
    'what have people asked for' view."""
    out = []
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line:
                    out.append(json.loads(line))
    except Exception:
        pass
    return out
