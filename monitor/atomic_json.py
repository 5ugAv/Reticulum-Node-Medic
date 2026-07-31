"""Crash-safe JSON writes for the medic's records.

The medic is a battery/solar-powered field device on an SD card, and power can
vanish mid-write — the documented failure mode for these cards is not media
death but a truncated file left by an interrupted write. A plain
``open(path, "w")`` truncates FIRST, so a power cut between truncate and flush
leaves an EMPTY roster / certificate / config: silent data loss discovered
much later.

``write_json`` writes to a temp file in the same directory, flushes it to the
platform (fsync), then atomically renames it into place — so a reader either
sees the old file or the new one, never a half-written one. It also fsyncs the
DIRECTORY, without which the rename itself can be lost on a power cut.

Found by the 2026-08-01 bug hunt (kin roster, birth certificates, radio
defaults and the onboard roster were all writing non-atomically).
"""

from __future__ import annotations

import json
import os
import tempfile
from typing import Any


def write_json(path: str, data: Any, **dump_kwargs) -> bool:
    """Atomically write *data* as JSON to *path*. Returns True on success.
    Never raises — callers keep their own honest-fail reporting."""
    tmp = None
    try:
        path = os.path.expanduser(path)
        parent = os.path.dirname(path) or "."
        os.makedirs(parent, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=parent, suffix=".tmp")
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, **dump_kwargs)
            f.flush()
            os.fsync(f.fileno())          # the bytes reach the card...
        os.replace(tmp, path)             # ...then swap in atomically
        tmp = None
        try:                              # ...and persist the rename itself
            dfd = os.open(parent, os.O_DIRECTORY)
            try:
                os.fsync(dfd)
            finally:
                os.close(dfd)
        except (OSError, AttributeError):
            pass                          # not all platforms allow this
        return True
    except Exception:                     # noqa: BLE001
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass
        return False
