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

# i18n: wrapped — ui.i18n is pure (no Kivy), so this module stays display-free
# and unit-testable; every title/body is translated when decide() runs, never
# at import time.
from ui.i18n import tr

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
    #: FAILED after the post-restart check: the screen offers "Set it up again
    #: from the start" and "Not now" as well as Try again.
    after_check: bool = False


def succeeded(results) -> bool:
    """Whether a GpsTrackerSetup run won. Its ``run_all`` returns the LIST of
    StepResults and stops at the first failure, so success is a non-empty run
    whose every step passed — a non-empty list is NOT itself a win (that trap
    would celebrate a failed flash)."""
    results = list(results or [])
    return bool(results) and all(getattr(r, "success", False) for r in results)


def first_failure(results, default: str = "") -> str:
    """Message of the first failed step, for an honest FAILED screen. With no
    *default* the stock sentence is used — translated at call time, not at
    import time."""
    for r in results or []:
        if not getattr(r, "success", True):
            return getattr(r, "message", tr("A step did not complete."))
    return default or tr("The Heltec Wireless Tracker didn't finish coming up.")


def decide(gps_live: bool,
           tracker_candidates: int,
           running: bool = False,
           result: Optional[bool] = None,
           failure: str = "",
           checking: bool = False,
           owns_radio: bool = False,
           after_check: bool = False,
           no_image: bool = False) -> FirstbornView:
    """Pick the stage. Order matters: a run in progress and a finished result
    outrank the plug state, so the screen doesn't snap back to "plug it in"
    mid-flash if a USB re-enumeration briefly drops the candidate count (an
    ESP32-S3 does exactly that when it resets into the bootloader)."""
    if running and checking:
        return FirstbornView(
            BIRTHING, tr("Checking the Heltec Wireless Tracker…"),
            tr("Node Medic restarted to join its new radio. Now it checks the "
               "radio is on and the position finder is reporting — about a "
               "minute."),
            can_begin=False)
    if running:
        return FirstbornView(
            BIRTHING, tr("Setting up the Heltec Wireless Tracker…"),
            tr("Writing Node Medic's radio software onto the Heltec Wireless "
               "Tracker, switching the medic's radio on, then checking it hears "
               "both the radio and the satellites. About five minutes — leave it "
               "plugged in."),
            can_begin=False)
    if result is True:
        return FirstbornView(
            DONE, tr("The Heltec Wireless Tracker is set up"),
            tr("The Heltec Wireless Tracker is now this medic's radio and "
               "position finder, and the radio is on. As soon as it can see the "
               "sky, Node Medic knows where it is, and you can set the clock from "
               "it in Settings ▸ Date & time."),
            can_begin=False, celebrate=True)
    if result is False:
        text = failure or tr("The Heltec Wireless Tracker didn't finish coming up.")
        # the one-board sentence ONLY when the plug count is the problem — it
        # used to follow every failure, blaming the bench for software faults
        if "plugged in" in text.lower() and "unplug" not in text.lower():
            text += "\n\n" + tr("Unplug everything except the Heltec Wireless "
                                 "Tracker, then try again.")
        return FirstbornView(
            FAILED, tr("The Heltec Wireless Tracker needs another go"), text,
            can_begin=True, after_check=after_check)
    if no_image and not (gps_live or owns_radio):
        # a medic cloned from one that never had the Tracker's radio software
        # (~/overlay_test is an optional carried tree): say so, offer the way on
        return FirstbornView(
            NEED_TRACKER, tr("This medic cannot set up a radio yet"),
            tr("It did not receive the Heltec Wireless Tracker's radio software "
               "when it was made. On the medic that made it, open BUILD ▸ Clone "
               "this device ▸ Retry a clone, and it copies the missing part. "
               "Everything else works meanwhile."),
            can_begin=False)
    if gps_live or owns_radio:
        # the medic OWNS a radio (its roster names one, or its services are
        # bound to one) — a satellite fix is not required to know that
        return FirstbornView(
            ALREADY, tr("This medic already has its radio and position finder"),
            tr("Its Heltec Wireless Tracker is set up, so there's nothing to do "
               "here. A position needs a view of the sky."),
            can_begin=False)
    if tracker_candidates <= 0:
        return FirstbornView(
            NEED_TRACKER, tr("Plug in the Heltec Wireless Tracker"),
            tr("No Heltec Wireless Tracker is connected yet. Screw its aerial on "
               "first. Then plug it into any free USB socket on this Node Medic "
               "with a short USB-A to USB-C cable that carries data, not just "
               "power.\n\n"
               "This page moves on by itself when it sees the Heltec Wireless "
               "Tracker."),
            can_begin=False)
    if tracker_candidates > 1:
        return FirstbornView(
            NEED_TRACKER, tr("One board at a time"),
            tr("Two things are plugged in that could be the Heltec Wireless "
               "Tracker. Unplug everything except the Heltec Wireless Tracker."),
            can_begin=False)
    return FirstbornView(
        READY, tr("Ready to set up the Heltec Wireless Tracker"),
        tr("Something that looks like the Heltec Wireless Tracker is plugged in. "
           "Check the board says 'Wireless Tracker' on it, like the picture, then "
           "press Begin. Node Medic writes its radio software onto it and checks "
           "it works. About five minutes."),
        can_begin=True)
