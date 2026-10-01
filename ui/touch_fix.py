"""Two fingers reach the app: feed SDL's finger events to Kivy on Linux.

Kivy 2.3.1's SDL window (kivy/core/window/window_sdl2.py, ``_mainloop``)
hands SDL finger events to the touch provider ONLY on iOS and Android —
``if platform in ('ios', 'android'): queue it`` then ``pass``. On the medic
every touch therefore arrives as the one mouse pointer SDL synthesises from
the FIRST finger, and the map's pinch-to-zoom — written and tuned twice —
never saw a second finger (operator, 2026-10-02: "I don't think the screen
is registering two touches"). The panel itself is a proper multitouch
controller (Goodix, MT slots + tracking ids in the kernel).

This wraps the window's event poll: a finger event goes onto the SDL2
provider's queue exactly as Kivy does on Android, and is swallowed from the
main loop. Pair it with ``SDL_TOUCH_MOUSE_EVENTS=0`` in the launcher
(scripts/start_ui.sh) so a finger is not ALSO a mouse — that doubling was
the 2026-07-31 "every touch twice" bug by another road.

Why a wrapper and not a patched Kivy: the medic's Kivy is the wheel the
clone carries ([[offline-clone-wheelhouse]]); a patch inside site-packages
would not survive a clone. This lives in the tool.
"""
from __future__ import annotations

FINGER_EVENTS = ("fingerdown", "fingerup", "fingermotion")

_installed = False
_seen = 0


def install(window, log=None) -> bool:
    """Wrap *window*._win.poll once. Returns True when installed."""
    global _installed
    if _installed:
        return True
    try:
        from kivy.core.window.window_sdl2 import SDL2MotionEventProvider
    except Exception:                                                  # noqa: BLE001
        return False
    win = getattr(window, "_win", None)
    if win is None or not hasattr(win, "poll"):
        return False
    orig = win.poll

    def poll():
        global _seen
        ev = orig()
        if ev and ev[0] in FINGER_EVENTS:
            SDL2MotionEventProvider.q.appendleft(ev)
            _seen += 1
            if _seen == 1 and log is not None:
                log("touch: SDL finger events are reaching Kivy (multitouch on)")
            return None                        # the main loop skips None
        return ev

    win.poll = poll
    _installed = True
    return True

