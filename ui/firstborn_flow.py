"""The firstborn ceremony's decision logic — kivy-free, so it is unit-tested
with no display and no hardware.

THE FIRSTBORN is the moment a new medic births its own Heltec Wireless Tracker:
its number-one child, and the source of the position + time the Pi 5 cannot get
on its own (no GNSS, no battery-backed clock). The screen is a celebration, but
the *decisions* — is a Tracker even plugged in, does the medic already have GPS,
did the flash succeed — are ordinary state that belongs here where it can be
tested. The screen renders whatever stage this returns.

Stages, and why each exists:

* ``ALREADY``   — the medic can already see satellites (gpsd is streaming a
  fix). Re-flashing would be pointless and risks knocking out a working GPS, so
  the ceremony congratulates and steps aside rather than pushing a birth.
* ``NEED_TRACKER`` — nothing that could be a Tracker is on USB. Ask for it
  rather than flash a guess (the medic's own RNode is a look-alike ESP32-S3;
  flashing it would be the wrong-board disaster). This stage has no begin.
* ``READY``     — exactly one candidate Tracker is present and the medic has no
  GPS yet: offer to begin.
* ``BIRTHING``  — the flash/detect/gpsd ladder is running.
* ``DONE``      — a fix was seen; celebrate node number one.
* ``FAILED``    — the ladder stopped short; say where, offer to try again.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

ALREADY = "already"
NEED_TRACKER = "need_tracker"
READY = "ready"
BIRTHING = "birthing"
DONE = "done"
FAILED = "failed"


@dataclass(frozen=True)
class FirstbornView:
    """What the screen should show, derived from the world."""

    stage: str
    title: str
    body: str
    can_begin: bool
    #: True only on DONE — the screen fires confetti/anim on this.
    celebrate: bool = False


def decide(gps_live: bool,
           tracker_candidates: int,
           running: bool = False,
           result: Optional[bool] = None,
           failure: str = "") -> FirstbornView:
    """Pick the stage. Order matters: a run in progress and a finished result
    outrank the plug state, so the screen doesn't snap back to "plug it in"
    mid-flash if a USB re-enumeration briefly drops the candidate count (an
    ESP32-S3 does exactly that when it resets into the bootloader)."""
    if running:
        return FirstbornView(
            BIRTHING, "Welcoming the firstborn…",
            "Flashing the Tracker, then waiting for it to see the sky. This "
            "takes a few minutes — leave it plugged in.",
            can_begin=False)
    if result is True:
        return FirstbornView(
            DONE, "Node number one is alive 🎉",
            "The Tracker is flashed, adopted, and reporting real satellites. "
            "This medic can see where it stands and knows the time — every "
            "birth from here carries a true place and date.",
            can_begin=False, celebrate=True)
    if result is False:
        return FirstbornView(
            FAILED, "The firstborn needs another go",
            (failure or "The Tracker didn't finish coming up.") +
            "\n\nCheck it is the only board plugged in, then try again.",
            can_begin=True)
    if gps_live:
        return FirstbornView(
            ALREADY, "This medic already has its eyes",
            "A GPS source is already streaming a fix, so there's nothing to "
            "birth here. You can move on — or run this again from PROBE if you "
            "want to replace the Tracker.",
            can_begin=False)
    if tracker_candidates <= 0:
        return FirstbornView(
            NEED_TRACKER, "Plug in the Tracker",
            "No Tracker is connected yet. Plug the Heltec Wireless Tracker into "
            "Node Medic — and only the Tracker, so the medic doesn't mistake "
            "its own radio for it — then this begins.",
            can_begin=False)
    if tracker_candidates > 1:
        return FirstbornView(
            NEED_TRACKER, "One board at a time",
            "More than one board looks like it could be the Tracker. Unplug the "
            "others — especially the medic's own radio — and leave just the "
            "Tracker, so the firstborn is the board you mean.",
            can_begin=False)
    return FirstbornView(
        READY, "Ready to meet the firstborn",
        "A Tracker is connected. Press begin and the medic flashes it, waits "
        "for it to see satellites, and adopts it as node number one.",
        can_begin=True)
