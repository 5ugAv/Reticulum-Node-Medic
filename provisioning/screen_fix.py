"""Re-initialise the DSI touchscreen panel without a reboot.

The medic's panel can scramble its own controller state — the screen shifts
sideways or draws the wrong colours (red rendered blue/purple) — typically
nudged by a USB device plugging in. PROVEN 2026-08-25 on the live medic:

- the framebuffer stays pixel-perfect (grim capture), so software is innocent;
- ``vcgencmd get_throttled`` stayed 0x0 across the event, so undervolt is not
  the cause;
- the kernel log shows no drm/vc4/dsi errors — the DSI link never dropped;
  the fault lives INSIDE the panel;
- and forcing a modeset (``wlr-randr --output DSI-2 --off`` … ``--on``) makes
  the driver rerun the panel's init sequence, which cleared it live — the
  same cure a cold power cycle delivers, without pulling power.

HONESTY: software cannot see the panel's true state (the framebuffer always
looks correct), so this module NEVER claims the screen is fixed — only that
the re-init ran. The operator's eyes are the verdict.

The one dangerous corner: if ``--off`` succeeds and ``--on`` fails, the
operator is looking at a black screen. The ON step therefore retries before
giving up, and the failure message tells them the power-pull fallback.
"""

from __future__ import annotations

import os
import subprocess
import time
from typing import Callable, Optional, Tuple

#: The medic's touchscreen connector as labwc/wlroots names it (card1-DSI-2).
DEFAULT_OUTPUT = "DSI-2"

#: How long the panel stays off before re-enable — long enough for the panel
#: controller to fully power down and forget its scrambled state.
OFF_SECONDS = 2.0

#: Attempts to switch the output back ON before declaring the black-screen
#: failure. Each retry waits a second first.
ON_RETRIES = 3


def _env() -> dict:
    """The Wayland env the compositor session uses — filled in when absent so
    the fix also works from an SSH shell, not only from inside the UI."""
    env = dict(os.environ)
    env.setdefault("WAYLAND_DISPLAY", "wayland-0")
    env.setdefault("XDG_RUNTIME_DIR", "/run/user/1000")
    return env


def detect_output(runner: Callable = subprocess.run) -> Optional[str]:
    """The first output name ``wlr-randr`` reports, or None (no tool / no
    compositor / no outputs). Output lines start at column 0; detail lines are
    indented."""
    try:
        proc = runner(["wlr-randr"], capture_output=True, text=True,
                      timeout=10, env=_env())
    except Exception:
        return None
    if proc.returncode != 0:
        return None
    for line in (proc.stdout or "").splitlines():
        if line and not line[0].isspace():
            return line.split()[0]
    return None


def reinit_panel(output: Optional[str] = None,
                 runner: Callable = subprocess.run,
                 sleep: Callable[[float], None] = time.sleep) -> Tuple[bool, str]:
    """One panel re-init: off -> wait -> on (with retries). Returns
    ``(ran_ok, operator_message)``. The message never claims the colours are
    fixed — it cannot know."""
    name = output or detect_output(runner) or DEFAULT_OUTPUT

    def _run(args):
        return runner(["wlr-randr", "--output", name] + args,
                      capture_output=True, text=True, timeout=15, env=_env())

    try:
        off = _run(["--off"])
    except FileNotFoundError:
        return False, "wlr-randr is not installed — cannot re-initialise the panel."
    except Exception as e:                                    # noqa: BLE001
        return False, f"Panel re-init failed before switching off: {e}"
    if off.returncode != 0:
        detail = (off.stderr or "").strip() or "unknown error"
        return False, f"Could not switch {name} off: {detail}"

    sleep(OFF_SECONDS)

    last_detail = "unknown error"
    for attempt in range(1, ON_RETRIES + 1):
        try:
            on = _run(["--on"])
        except Exception as e:                                # noqa: BLE001
            last_detail = str(e)
            on = None
        if on is not None and on.returncode == 0:
            return True, (f"Panel {name} re-initialised. If the colours are "
                          "still wrong, power the medic off, wait 10 seconds, "
                          "and power back on.")
        if on is not None:
            last_detail = (on.stderr or "").strip() or "unknown error"
        if attempt < ON_RETRIES:
            sleep(1.0)

    return False, (f"{name} switched off but did not come back on "
                   f"({last_detail}). Pull the medic's power, wait 10 "
                   "seconds, and power back on.")
