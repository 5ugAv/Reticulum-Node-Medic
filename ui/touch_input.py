"""ONE touch provider for the panel, chosen before Kivy opens a window.

The 2026-10-03 probes, with the operator's finger on the glass:

* SDL under XWayland got NOTHING — not finger events, not even button
  events — once SDL selected XI2 touch on its window. Every tap the medic
  ever saw was XWayland's pointer emulation arriving as a mouse. One finger
  only, by construction; the map's pinch could never work. (The Kivy wheel's
  SDL has no Wayland driver, so the native road is closed too.)
* Kivy's ``hidinput`` opened the Goodix and emitted nothing: the panel speaks
  MT protocol B (slots), and hidinput flushes a point only on the protocol-A
  ``SYN_MT_REPORT`` — which this stream never carries.
* Kivy's ``mtdev`` provider, over the system ``libmtdev``, delivered two
  simultaneous fingers at true 720x1280 positions within a second.

So: find the Goodix by its sysfs name (its /dev/input/eventN number is not
promised), prove libmtdev loads and the node is readable, and only then make
mtdev the provider AND drop the SDL ``mouse`` provider — the one that turned
XWayland's emulated pointer into touches. Keeping both is the 2026-07-31
"every tap twice" disease by a new road ([[doubled-touch-root-fix]]). If any
check fails, nothing changes and the mouse road stays: one-finger touch is
better than none.

Pure Python, injectable paths, tested on the Mac with a fake sysfs.
"""
from __future__ import annotations

import ctypes
import os
from typing import Optional

SYSFS_INPUT = "/sys/class/input"
PANEL_NAME_HINT = "goodix"
PROVIDER_KEY = "panel"


def find_panel(sysfs: str = SYSFS_INPUT, hint: str = PANEL_NAME_HINT) -> Optional[str]:
    """``/dev/input/eventN`` of the first device whose name contains *hint*."""
    try:
        entries = sorted(os.listdir(sysfs))
    except OSError:
        return None
    for e in entries:
        if not e.startswith("event"):
            continue
        try:
            with open(os.path.join(sysfs, e, "device", "name")) as f:
                name = f.read().strip()
        except OSError:
            continue
        if hint.lower() in name.lower():
            return "/dev/input/" + e
    return None


def libmtdev_loads(loader=ctypes.cdll.LoadLibrary) -> bool:
    try:
        loader("libmtdev.so.1")
        return True
    except OSError:
        return False


def choose(config, sysfs: str = SYSFS_INPUT, loader=ctypes.cdll.LoadLibrary,
           readable=lambda p: os.access(p, os.R_OK), log=None) -> str:
    """Set Kivy's [input] section. Returns a one-line verdict (also logged)."""
    say = log or (lambda m: None)
    dev = find_panel(sysfs)
    if dev is None:
        say("touch: no Goodix panel in sysfs — keeping the mouse provider")
        return "mouse (no panel found)"
    if not readable(dev):
        say("touch: %s not readable — keeping the mouse provider" % dev)
        return "mouse (%s not readable)" % dev
    if not libmtdev_loads(loader):
        say("touch: libmtdev.so.1 missing — keeping the mouse provider")
        return "mouse (no libmtdev)"
    config.set("input", PROVIDER_KEY, "mtdev,%s" % dev)
    if config.has_option("input", "mouse"):
        config.remove_option("input", "mouse")
    say("touch: mtdev on %s, mouse provider off (multitouch)" % dev)
    return "mtdev %s" % dev
