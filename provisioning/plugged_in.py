"""What is physically plugged into Node Medic right now — and what to say about it.

Operator, at the bench 2026-08-06: *"medic is still on this screen after we
unplugged the pi and put the sd card in the reader. we need node medic to be
more responsive to knowing what is plugged in and removed"*.

The imaging flow had ended on "Unplugged — plug it back in…". The operator then
pulled the card OUT of the Pi and put it into the medic's OWN card reader — a
completely different and entirely unambiguous hardware state — and the screen
carried on asking for the Pi.

WHY THIS IS WORTH A MODULE. The medic is a diagnostic tool. When it is visibly
wrong about what is connected to it, the operator stops believing the rest of
what it says, and that is expensive: the whole point of PROBE and VITALS is that
their answers are trusted. It also stranded the flow — the only way out of that
screen was a button reading "Stop waiting", which is worded as giving up rather
than as the medic noticing something.

WHAT THE OLD CODE DID. ``_boot_tick`` knew three states: gadget (success), card
reader, absent. Everything that was not a gadget collapsed into "plug it back
in". So four genuinely different situations — each with a different cause and a
different fix — produced one message:

  * nothing on USB at all            -> cable, port, or power
  * the Pi in boot-ROM               -> it could NOT boot the card we wrote
  * the Pi as mass storage           -> it is acting as a card reader, not booting
  * our card sitting in OUR reader   -> the operator has moved on; catch up

The last one is the one that stung, because the medic had all the information
and said the wrong thing anyway.

DESIGN. Pure functions over injected readings: no Kivy, no subprocess, no
clock. The caller does the I/O (lsusb, card_status, disk serial) and passes the
results in; this decides what is true and what to say. That is the same shape as
``pi_imager.card_status`` and ``next_steps_after_imaging`` — copy and data live
with the logic that produces them, and the screen stays thin.

Deliberately NOT here: any action. This module reports and proposes; it never
mounts, writes, or reboots anything. Naming a branch is not taking it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Sequence

from provisioning import pi_usbboot

# --- what we found ---------------------------------------------------------

WAITING = "waiting"                  # too early to say anything
PI_ALIVE = "pi_alive"                # the gadget is up: success
PI_WONT_BOOT = "pi_wont_boot"        # boot-ROM: it did not boot the card
PI_AS_READER = "pi_as_reader"        # mass storage: presenting its card
OUR_CARD_BACK = "our_card_back"      # the card WE wrote is in OUR reader
A_CARD_BACK = "a_card_back"          # a card is in our reader, not ours
SEVERAL_CARDS = "several_cards"      # more than one — never guess
NO_USB_AT_ALL = "no_usb_at_all"      # nothing appeared: cable/port/power
NO_GADGET = "no_gadget"              # something appeared, but no network link


#: How long before silence becomes a diagnosis. Below this the honest answer is
#: "still waiting" — a Pi Zero 2 W takes a while to boot a freshly written card,
#: and calling a fault early trains the operator to ignore the screen.
PATIENCE_S = 45.0


@dataclass
class Situation:
    """What is true, what to say, and what the operator can actually do."""
    state: str
    headline: str = ""
    detail: str = ""
    #: Semantic action keys, most useful first. The screen maps them to buttons;
    #: the words live here so they can be tested without a display.
    actions: List[Dict[str, str]] = field(default_factory=list)

    @property
    def is_settled(self) -> bool:
        """True when there is nothing further to wait for — stop polling.

        "Settled" means the medic has reached a conclusion it cannot talk
        itself out of by looking again. It does NOT mean "the situation will
        not change" — the operator is standing there with the hardware in their
        hands, and most of these states name something they are about to fix.

        OUR_CARD_BACK is the one that got this wrong (live, 2026-08-09). "The
        card I just wrote is in my reader" was called settled, so the poll
        stopped — at the exact moment the screen was telling the operator to
        take that card out and put it in the Pi. They did, the Pi booted and
        came up on the cable, and the medic never looked again. The only way
        forward was a button, on a screen whose whole design is "the medic can
        see this, so it should not have to ask".

        A state the operator is being ASKED to change is the last thing that
        should stop the watching.
        """
        return self.state in (PI_ALIVE, PI_WONT_BOOT, SEVERAL_CARDS)

    @property
    def is_good(self) -> bool:
        return self.state == PI_ALIVE


def _act(key: str, label: str) -> Dict[str, str]:
    return {"key": key, "label": label}


def read(lsusb_output: str = "",
         card: Dict = None,
         waited_s: float = 0.0,
         baseline_ids: Sequence[str] = (),
         our_card_serial: str = "",
         card_serial: str = "",
         node_name: str = "") -> Situation:
    """Decide what is plugged in, from readings the caller has already taken.

    *lsusb_output*   — raw ``lsusb``.
    *card*           — ``pi_imager.card_status()`` result, or None.
    *waited_s*       — seconds since we started waiting for the Pi.
    *baseline_ids*   — USB ids present WHEN THE WAIT BEGAN. The difference
                       between "nothing ever appeared" and "something appeared
                       but it is not offering a link" is the difference between
                       a cable fault and a config fault, and it cannot be told
                       from a single snapshot. This is what makes that possible.
    *our_card_serial*— serial of the card we just wrote, if known.
    *card_serial*    — serial of the card in the reader now, if readable.
    *node_name*      — what we named it, purely for the copy.

    Order matters: most specific and most actionable first. A Pi sitting in
    boot-ROM is a REAL DIAGNOSIS ("that card did not boot") and must not be
    buried under a generic "still waiting".
    """
    card = card or {}
    who = node_name or "the Pi"

    pi = pi_usbboot.classify(lsusb_output or "")

    # 1. Success.
    if pi.state == pi_usbboot.GADGET:
        return Situation(PI_ALIVE, f"{who} is up and talking over the cable.")

    # 2. Boot-ROM. The Pi is powered and enumerating, but it fell back to its
    #    ROM loader — which means it did not boot what we wrote. Saying "plug it
    #    back in" here is actively misleading: it IS plugged in.
    if pi.state == pi_usbboot.BOOTROM:
        return Situation(
            PI_WONT_BOOT,
            f"{who} is plugged in, but it didn't boot the card.",
            "It came up in its built-in recovery mode instead, which means the "
            "card wasn't readable to it. Usually the card, not the Pi.",
            [_act("rewrite_card", "Write the card again"),
             _act("back_to_birth", "Go back")])

    # 3. Mass storage: it is offering its card, not running from it.
    if pi.state == pi_usbboot.CARD_READER:
        return Situation(
            PI_AS_READER,
            f"{who} is showing its card instead of booting from it.",
            "It's acting as a card reader. Unplug it, wait a few seconds, then "
            "plug it back in so it starts up normally.",
            [_act("back_to_birth", "Go back")])

    # 4. THE ONE THAT STUNG. A card is in the medic's own reader — the operator
    #    has already moved on to a different step, and the medic should catch up
    #    rather than keep asking for something that is no longer in their hand.
    state = card.get("state")
    if state == "several":
        return Situation(
            SEVERAL_CARDS,
            "There's more than one card in the reader.",
            card.get("detail", ""),
            [_act("back_to_birth", "Go back")])
    if state == "one":
        known = bool(our_card_serial) and card_serial == our_card_serial
        if known:
            return Situation(
                OUR_CARD_BACK,
                "That's the card I just wrote — it's in my reader.",
                f"To bring {who} up, it needs to go into the Pi's own slot, and "
                "the Pi plugged in with a DATA cable.",
                [_act("back_to_birth", "Go back"),
                 _act("rewrite_card", "Write this card again")])
        # A card, but we cannot prove it is ours. Say exactly that — claiming
        # recognition we do not have is how a diagnostic tool loses its
        # authority (see the honesty gate).
        return Situation(
            A_CARD_BACK,
            f"There's a card in my reader ({card.get('label', '')}).".replace(" ()", ""),
            "I can't tell whether it's the one I just wrote. If it is, it goes "
            "into the Pi, not in me.",
            [_act("back_to_birth", "Go back"),
             _act("rewrite_card", "Write this card")])

    # 5. Nothing conclusive yet. Below the patience threshold, say so plainly.
    if waited_s < PATIENCE_S:
        return Situation(WAITING, f"Waiting for {who}… ({int(waited_s)}s)")

    # 6. Split the two failures the old copy blurred together. This is the whole
    #    reason baseline_ids is threaded through.
    now_ids = set(_ids(lsusb_output))
    appeared = now_ids - set(baseline_ids)
    if not appeared:
        return Situation(
            NO_USB_AT_ALL,
            f"Nothing has appeared on USB since you plugged {who} in.",
            "No new device at all — so this is the cable, the port or the "
            "power, not the card. A charge-only lead powers a Pi perfectly and "
            "never shows up. On a Pi Zero use the INNER micro-USB; on a 3A+ the "
            "full-size USB-A socket.",
            [_act("back_to_birth", "Go back and check the cable")])
    return Situation(
        NO_GADGET,
        f"{who} is there, but it isn't offering a network link yet.",
        "Something new did appear on USB, so the cable and power are fine — "
        "it's the card's setup that hasn't come up. Give it a moment; if it "
        "stays like this the card needs writing again.",
        [_act("rewrite_card", "Write the card again"),
         _act("back_to_birth", "Go back")])


def _ids(lsusb_output: str) -> List[str]:
    """Every ``vvvv:pppp`` in an lsusb dump, lowercased."""
    return [m.lower() for m in pi_usbboot._ID_RE.findall(lsusb_output or "")]
