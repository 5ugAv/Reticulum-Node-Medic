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
           checking: bool = False) -> FirstbornView:
    """Pick the stage. Order matters: a run in progress and a finished result
    outrank the plug state, so the screen doesn't snap back to "plug it in"
    mid-flash if a USB re-enumeration briefly drops the candidate count (an
    ESP32-S3 does exactly that when it resets into the bootloader)."""
    if running and checking:
        return FirstbornView(
            BIRTHING, tr("Checking the Heltec Wireless Tracker…"),
            tr("Node Medic restarted to join its new radio. Now it checks the "
               "radio is up on the mesh and the GPS is reporting — about a "
               "minute."),
            can_begin=False)
    if running:
        return FirstbornView(
            BIRTHING, tr("Setting up the Heltec Wireless Tracker…"),
            tr("Flashing the Heltec Wireless Tracker as this medic's radio and "
               "GPS, starting the mesh services around it, then checking it hears "
               "both. This takes a few minutes — leave it plugged in."),
            can_begin=False)
    if result is True:
        return FirstbornView(
            DONE, tr("The Heltec Wireless Tracker is set up"),
            tr("The Heltec Wireless Tracker is this medic's LoRa radio and GPS, "
               "and its mesh services are running. As soon as it sees the sky, "
               "Node Medic knows where it stands, and you can set the clock from "
               "it in Settings ▸ Date & time."),
            can_begin=False, celebrate=True)
    if result is False:
        return FirstbornView(
            FAILED, tr("The Heltec Wireless Tracker needs another go"),
            (failure or tr("The Heltec Wireless Tracker didn't finish coming up.")) + "\n\n" +
            tr("Check it is the only board plugged in, then try again."),
            can_begin=True)
    if gps_live:
        return FirstbornView(
            ALREADY, tr("This medic already has its radio and GPS"),
            tr("Its Heltec Wireless Tracker is already reporting, so there's "
               "nothing to set up here. You can move on — or run this again from "
               "Settings if you want to replace it."),
            can_begin=False)
    if tracker_candidates <= 0:
        return FirstbornView(
            NEED_TRACKER, tr("Plug in the Heltec Wireless Tracker"),
            tr("No Heltec Wireless Tracker is connected yet. Plug it into "
               "a free USB socket on this Node Medic with the USB-A to USB-C "
               "cable, its aerial already attached.\n\n"
               "This page moves on by itself when it sees the Heltec Wireless "
               "Tracker."),
            can_begin=False)
    if tracker_candidates > 1:
        return FirstbornView(
            NEED_TRACKER, tr("One board at a time"),
            tr("More than one board looks like it could be the Heltec Wireless "
               "Tracker. Unplug the others — especially the medic's own radio — "
               "and leave just the Heltec Wireless Tracker, so Node Medic sets "
               "up the board you mean."),
            can_begin=False)
    return FirstbornView(
        READY, tr("Ready to set up the Heltec Wireless Tracker"),
        tr("A board that could be the Heltec Wireless Tracker is plugged in. If it is the Heltec "
           "Wireless Tracker, press Begin: the medic flashes it as its own radio "
           "and GPS, starts its mesh services, and checks it hears both."),
        can_begin=True)
