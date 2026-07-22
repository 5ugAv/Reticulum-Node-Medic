"""Screen backlight brightness for the medic's touchscreen.

Reads/writes the Linux backlight sysfs (``/sys/class/backlight/<dev>/``). The
device name varies by panel, so we glob for it rather than hard-code it. The
brightness file is root-owned, so writes go through ``sudo -n`` (the medic has
passwordless sudo). A floor keeps the operator from blacking the screen out
entirely. Injectable ``run`` + paths make it unit-testable off-hardware; on a
box with no backlight (a dev Mac, an HDMI panel with no sysfs) every call fails
gracefully so the setting just shows as unavailable.
"""

from __future__ import annotations

import glob
import os
import subprocess
from typing import Callable, Optional, Tuple

Runner = Callable[[list], Tuple[int, str]]

BACKLIGHT_GLOB = "/sys/class/backlight/*"
MIN_PCT = 8                                    # never let it go fully dark
CONFIG = os.path.expanduser("~/.reticulum-node-medic/brightness")


def _default_run(argv: list) -> Tuple[int, str]:
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=10)
        return p.returncode, (p.stdout + p.stderr)
    except Exception as e:
        return 1, str(e)


def backlight_device(glob_pattern: str = BACKLIGHT_GLOB) -> Optional[str]:
    """The first backlight device directory, or None if the display exposes none."""
    devs = sorted(glob.glob(glob_pattern))
    return devs[0] if devs else None


def _read_int(path: str) -> Optional[int]:
    try:
        with open(path) as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return None


def has_control(glob_pattern: str = BACKLIGHT_GLOB) -> bool:
    return backlight_device(glob_pattern) is not None


def get_brightness(device: Optional[str] = None,
                   glob_pattern: str = BACKLIGHT_GLOB) -> Optional[int]:
    """Current brightness as a percent (0-100), or None if there's no backlight."""
    dev = device or backlight_device(glob_pattern)
    if not dev:
        return None
    cur = _read_int(os.path.join(dev, "brightness"))
    mx = _read_int(os.path.join(dev, "max_brightness"))
    if cur is None or not mx:
        return None
    return max(0, min(100, round(cur / mx * 100)))


def pct_to_raw(pct: int, max_raw: int) -> int:
    """Map a 0-100 percent (floored at MIN_PCT) to a 1..max_raw backlight value."""
    pct = max(MIN_PCT, min(100, int(pct)))
    return max(1, round(pct / 100.0 * max_raw))


def set_brightness(pct: int, device: Optional[str] = None,
                   glob_pattern: str = BACKLIGHT_GLOB,
                   run: Optional[Runner] = None) -> Tuple[bool, str]:
    """Set brightness to *pct* (0-100, floored at MIN_PCT). Returns (ok, message)."""
    dev = device or backlight_device(glob_pattern)
    if not dev:
        return False, "No screen brightness control on this display."
    mx = _read_int(os.path.join(dev, "max_brightness"))
    if not mx:
        return False, "Couldn't read the screen's brightness range."
    pct = max(MIN_PCT, min(100, int(pct)))
    raw = pct_to_raw(pct, mx)
    run = run or _default_run
    target = os.path.join(dev, "brightness")
    code, out = run(["sudo", "-n", "sh", "-c", f"echo {raw} > {target}"])
    if code == 0:
        save_pct(pct)
        return True, f"Brightness {pct}%"
    return False, (out.strip().splitlines() or ["Couldn't set brightness."])[-1]


def save_pct(pct: int, path: str = CONFIG) -> None:
    """Remember the chosen level so it can be restored after a reboot (the backlight
    resets to default on boot). Best-effort — never raises."""
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(str(int(pct)))
    except OSError:
        pass


def load_pct(path: str = CONFIG) -> Optional[int]:
    try:
        with open(path) as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return None


def restore(run: Optional[Runner] = None) -> None:
    """Re-apply the saved brightness at startup (backlight resets on reboot).
    Best-effort and silent when there's nothing saved or no backlight."""
    pct = load_pct()
    if pct is not None:
        set_brightness(pct, run=run)
