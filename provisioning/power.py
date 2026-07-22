"""Clean power-off for the medic.

A graceful shutdown so the SD card isn't corrupted by yanking power — the exact
failure mode risk we hit on 2026-07-22. The medic runs with passwordless sudo, so
``systemctl poweroff`` goes through ``sudo -n``. Injectable ``run`` for tests.
"""

from __future__ import annotations

import subprocess
from typing import Callable, Tuple

Runner = Callable[[list], Tuple[int, str]]


def _default_run(argv: list) -> Tuple[int, str]:
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=15)
        return p.returncode, (p.stdout + p.stderr)
    except Exception as e:
        return 1, str(e)


def power_off(run: Runner = _default_run) -> Tuple[bool, str]:
    """Shut the medic down cleanly. Returns (ok, message). On success the Pi is on
    its way down, so 'ok' just means the command was accepted."""
    code, out = run(["sudo", "-n", "systemctl", "poweroff"])
    if code == 0:
        return True, "Shutting down — wait for the screen to go dark, then it's safe to unplug."
    return False, (out.strip().splitlines() or ["Couldn't power off."])[-1]
