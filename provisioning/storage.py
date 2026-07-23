"""Storage usage — SD fullness + a breakdown of what's using space.

Read-only accounting for Settings ▸ Storage usage: how full the SD card is, what
the big consumers are (map tiles, beacon history, firmware assets, registry,
logs), and how much is free. Pure filesystem primitives, unit-testable; the
screen assembles the labelled breakdown from the real paths.
"""

from __future__ import annotations

import os
import shutil
from typing import Dict


def path_size(path: str) -> int:
    """Total bytes at *path* — a file's size, a directory's recursive size, or 0
    if it doesn't exist. Unreadable entries are skipped, never raised."""
    path = os.path.expanduser(path)
    if os.path.isfile(path):
        try:
            return os.path.getsize(path)
        except OSError:
            return 0
    if not os.path.isdir(path):
        return 0
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                continue
    return total


def paths_size(paths) -> int:
    """Combined size of several paths."""
    return sum(path_size(p) for p in paths)


def disk_usage(path: str = "/") -> Dict[str, int]:
    """{'total','used','free','percent'} for the filesystem holding *path*."""
    try:
        t, u, f = shutil.disk_usage(os.path.expanduser(path))
    except OSError:
        return {"total": 0, "used": 0, "free": 0, "percent": 0}
    return {"total": t, "used": u, "free": f,
            "percent": round(u / t * 100) if t else 0}


def format_size(n: int) -> str:
    n = float(max(0, n))
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024.0
    return f"{n:.1f} TB"
