"""The UI's BUSY marker — the one signal a restart must respect.

2026-09-22: the operator was birthing a Pi Zero when the UI was stopped
from the shell to prune the registry. scripts/restart_ui.sh refuses during
a card write or a board flash (it looks for dd, esptool, nrfutil…), but a
Pi birth runs over SSH from INSIDE the UI process — nothing on the machine
showed it was busy, and the birth died with the process. A walk, a
self-check, an image download: same.

So the UI writes ``~/.reticulum-node-medic/ui_busy`` while it is busy and
touches it every HEARTBEAT_S; anything that would stop the UI (the restart
script, a shell prune, a deploy) must refuse while the marker is FRESH.
Freshness, not existence: a marker left by a crash must not block forever.
"""
from __future__ import annotations

import os
import time
from typing import Optional

MARKER_PATH = "~/.reticulum-node-medic/ui_busy"
HEARTBEAT_S = 30.0
#: A marker older than this is stale (the process that wrote it is gone).
FRESH_S = 90.0


def write(reason: str, path: str = MARKER_PATH, now=time.time) -> None:
    p = os.path.expanduser(path)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w") as fh:
        fh.write((reason or "busy").strip() + "\n")
    os.replace(tmp, p)
    os.utime(p, (now(), now()))


def clear(path: str = MARKER_PATH) -> None:
    try:
        os.remove(os.path.expanduser(path))
    except FileNotFoundError:
        pass


def busy_reason(path: str = MARKER_PATH, fresh_s: float = FRESH_S,
                now=time.time) -> Optional[str]:
    """The reason the UI is busy, or None when it is not (no marker, or a
    stale one — older than *fresh_s*, which a live UI would have touched)."""
    p = os.path.expanduser(path)
    try:
        age = now() - os.stat(p).st_mtime
        if age > fresh_s:
            return None
        with open(p) as fh:
            return (fh.read().strip() or "busy")
    except OSError:
        return None
