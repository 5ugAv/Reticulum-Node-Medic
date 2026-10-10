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


def first_failure(results, default: str = "",
                  board_name: str = "Heltec Wireless Tracker") -> str:
    """Message of the first failed step, for an honest FAILED screen. With no
    *default* the stock sentence is used — translated at call time, not at
    import time."""
    for r in results or []:
        if not getattr(r, "success", True):
            return getattr(r, "message", tr("A step did not complete."))
    return default or tr("The {board} didn't finish coming up.").format(board=board_name)


# -- which board: any GPS-capable board the medic can take as its own ---------

#: The board a fresh page assumes until the plug or the keeper says otherwise.
DEFAULT_BOARD = "heltec_wireless_tracker"


def board_choices():
    """``[(key, display name)]`` of every board a medic can take as its own
    radio and position finder, the Tracker first (keeper, 2026-10-10: "give
    the user an option at that page ... the Heltec wireless tracker or the
    T114 or whatever options we're going to give them")."""
    from workflows.medic_radio import OWN_RADIO_BOARDS
    from workflows.rnode_boards import get_board
    out = []
    for key in OWN_RADIO_BOARDS:
        b = get_board(key)
        out.append((key, b.display_name if b else key))
    return out


def board_name(key: str) -> str:
    for k, name in board_choices():
        if k == key:
            return name
    return key


def board_from_usb(vendor: str, product: str):
    """The own-radio board the plugged-in thing most likely is, from its USB
    identity alone (no port is opened), or None when nothing says. The keeper
    confirms against the picture either way."""
    v, pr = (vendor or "").lower(), (product or "").lower()
    if "n5262" in pr or "t114" in pr:
        return "heltec_t114_gps"
    if "espressif" in v or "jtag" in pr:
        return "heltec_wireless_tracker"
    return None


def decide(gps_live: bool,
           tracker_candidates: int,
           running: bool = False,
           result: Optional[bool] = None,
           failure: str = "",
           checking: bool = False,
           owns_radio: bool = False,
           after_check: bool = False,
           no_image: bool = False,
           board_name: str = "Heltec Wireless Tracker") -> FirstbornView:
    """Pick the stage. Order matters: a run in progress and a finished result
    outrank the plug state, so the screen doesn't snap back to "plug it in"
    mid-flash if a USB re-enumeration briefly drops the candidate count (an
    ESP32-S3 does exactly that when it resets into the bootloader).
    *board_name* is the board the keeper chose (or the plug suggested); every
    sentence names it."""
    b = board_name
    if running and checking:
        return FirstbornView(
            BIRTHING, tr("Checking the {board}…").format(board=b),
            tr("Node Medic restarted to join its new radio. Now it checks the "
               "radio is on and the position finder is reporting — about a "
               "minute."),
            can_begin=False)
    if running:
        return FirstbornView(
            BIRTHING, tr("Setting up the {board}…").format(board=b),
            tr("Writing Node Medic's radio software onto the {board}, "
               "switching the medic's radio on, then checking it hears "
               "both the radio and the satellites. About five minutes — leave it "
               "plugged in.").format(board=b),
            can_begin=False)
    if result is True:
        return FirstbornView(
            DONE, tr("The {board} is set up").format(board=b),
            tr("The {board} is now this medic's radio and "
               "position finder, and the radio is on. As soon as it can see the "
               "sky, Node Medic knows where it is, and you can set the clock from "
               "it in Settings ▸ Date & time.").format(board=b),
            can_begin=False, celebrate=True)
    if result is False:
        text = failure or tr("The {board} didn't finish coming up.").format(board=b)
        # the one-board sentence ONLY when the plug count is the problem — it
        # used to follow every failure, blaming the bench for software faults
        if "plugged in" in text.lower() and "unplug" not in text.lower():
            text += "\n\n" + tr("Unplug everything except the {board}, "
                                 "then try again.").format(board=b)
        return FirstbornView(
            FAILED, tr("The {board} needs another go").format(board=b), text,
            can_begin=True, after_check=after_check)
    if no_image and not (gps_live or owns_radio):
        # a medic cloned from one that never had this board's radio software
        # (a carried firmware tree): say so, offer the way on
        return FirstbornView(
            NEED_TRACKER, tr("This medic cannot set up a radio yet"),
            tr("It did not receive the {board}'s radio software "
               "when it was made. On the medic that made it, open BUILD ▸ Clone "
               "this device ▸ Retry a clone, and it copies the missing part. "
               "Everything else works meanwhile.").format(board=b),
            can_begin=False)
    if gps_live or owns_radio:
        # the medic OWNS a radio (its roster names one, or its services are
        # bound to one) — a satellite fix is not required to know that
        return FirstbornView(
            ALREADY, tr("This medic already has its radio and position finder"),
            tr("Its own radio board is set up, so there's nothing to do "
               "here. A position needs a view of the sky."),
            can_begin=False)
    if tracker_candidates <= 0:
        return FirstbornView(
            NEED_TRACKER, tr("Plug in the {board}").format(board=b),
            tr("No {board} is connected yet. Screw its aerial on "
               "first. Then plug it into any free USB socket on this Node Medic "
               "with a short USB cable that carries data, not just "
               "power.\n\n"
               "This page moves on by itself when it sees the board. Using a "
               "different board? Tap its picture.").format(board=b),
            can_begin=False)
    if tracker_candidates > 1:
        return FirstbornView(
            NEED_TRACKER, tr("One board at a time"),
            tr("Two things are plugged in that could be the {board}. "
               "Unplug everything except the {board}.").format(board=b),
            can_begin=False)
    return FirstbornView(
        READY, tr("Ready to set up the {board}").format(board=b),
        tr("Something that could be the {board} is plugged in. "
           "Check it matches the picture — if it is a different board, tap "
           "that board's picture — then press Begin. Node Medic writes its "
           "radio software onto it and checks it works. About five "
           "minutes.").format(board=b),
        can_begin=True)
