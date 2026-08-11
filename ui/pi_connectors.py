"""Which socket on THIS Raspberry Pi carries data — per board, never generic.

Operator's standing rule (2026-08-06): *"All images that the user sees on node
medic should correspond to the hardware they've got in their hand."* The same
must be true of the WORDS. What triggered it: with a Pi 3A+ selected, the guide
showed a yellow callout explaining that *"a Pi Zero has two identical micro-USB
sockets; the one nearer the mini-HDMI is USB (data)"*. A 3A+ has no data
micro-USB at all — its data path is the full-size USB-A. An operator following
that literally cannot succeed, and will reasonably blame the tool.

One string covering every board is the same bug as one photo covering every
board, and it is worse in one way: a wrong photo looks wrong, while wrong text
reads as authoritative.

THE FACT THAT MAKES THIS MORE THAN COPY. Not every Pi CAN do the cable link, and
the difference is invisible from the outside:

  * Raspberry Pi's own OTG white paper states that A, B, 2B, 3B and 3B+ do not
    support OTG, and that a board with a HUB between the USB controller and the
    ports cannot do gadget mode even when forced into peripheral mode.
  * A 3B+ has exactly that — the LAN7515 Ethernet/USB hub sits between the SoC
    and every port. So a 3B+ can never present a USB gadget, whatever we write
    to its card.
  * A 3A+ has no such hub (which is why it has one USB-A and no Ethernet), so
    its OTG reaches the USB-A socket. It works, but only with
    ``dtoverlay=dwc2,dr_mode=peripheral`` — a USB-A socket has no ID pin, so the
    default ``otg`` resolves to host every time (proven live 2026-08-07).

Saying so BEFORE the four-minute card write is the whole point. The operator hit
exactly this with a 3A+: a birth that stalled on a step the hardware could not
perform, with nothing on screen admitting it was impossible.

Pure data + copy, no Kivy — so the guide's wording is testable without a screen.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional

from ui.i18n import tr


@dataclass(frozen=True)
class Connectors:
    """How to plug THIS board into Node Medic."""
    key: str
    #: Can it present a USB gadget at all? False = the cable route is impossible,
    #: not merely fiddly, and the guide must say so instead of asking.
    can_cable: bool
    #: Which socket carries DATA, in the operator's words.
    data_port: str = ""
    #: Which socket takes power, when it is a different one.
    power_port: str = ""
    #: The extra thing this board needs, if any.
    caveat: str = ""
    #: Why the cable route is impossible. Only meaningful when can_cable is False.
    why_not: str = ""


PI_CONNECTORS: Dict[str, Connectors] = {
    # CAVEATS ARE FOR THE TRAP, NOT THE EXPLANATION. Each one used to restate
    # what the data_port / power_port pair had already said, and the 3A+ one
    # additionally carried the two-supplies warning — which belongs on the step
    # where a second supply is actually in question (see _POWER_HINTS), not on
    # the step where the operator is choosing a socket.
    "pi_zero_2w": Connectors(
        key="pi_zero_2w", can_cable=True,
        data_port=tr("the INNER micro-USB, the one nearer the mini-HDMI"),
        power_port=tr("the OUTER micro-USB, marked PWR IN"),
        caveat=tr("The two look identical; the outer one cannot carry data at "
                  "all.")),
    "pi_3a_plus": Connectors(
        key="pi_3a_plus", can_cable=True,
        data_port=tr("the full-size USB-A socket"),
        power_port=tr("the micro-USB, which is power only"),
        # THE HAZARD STAYS HERE, shortened but not moved. A second supply is a
        # natural thing to reach for the moment a Pi is plugged in, so the
        # warning has to be on the step where the plugging happens — the
        # provisioning step repeats it later because that is where the long
        # current draw is, not because this one can drop it.
        caveat=tr("It needs an A-to-A cable, which carries 5V at BOTH ends — "
                  "so don't add a separate supply.")),
    # The board that cannot, however willing the operator is.
    "pi_3b_plus": Connectors(
        key="pi_3b_plus", can_cable=False,
        why_not=tr("A Pi 3B+ cannot be connected this way. Its USB sockets sit "
                   "behind a built-in network/USB hub chip, so the Pi can only "
                   "ever act as a host — it can never appear to Node Medic as a "
                   "device. This is the board's wiring, not a setting. Set this "
                   "one up over Wi-Fi instead.")),
    "pi_4b": Connectors(
        key="pi_4b", can_cable=True,
        data_port=tr("the USB-C socket"),
        caveat=tr("It carries power and data, so one cable does everything.")),
    "pi_5": Connectors(
        key="pi_5", can_cable=True,
        data_port=tr("the USB-C socket"),
        caveat=tr("It carries power and data, so one cable does everything.")),
}

_ALIASES = {"pi_5_full": "pi_5"}


def get(pi_key: str) -> Optional[Connectors]:
    """Connector facts for *pi_key*, or None when the board is unknown."""
    k = (pi_key or "").strip()
    return PI_CONNECTORS.get(_ALIASES.get(k, k))


#: What to say when we do NOT know which board it is. Deliberately generic and
#: deliberately short: naming a specific socket for a board we have not
#: identified is the exact bug this module exists to prevent.
UNKNOWN_HINT = tr("Use the Pi's DATA port and a DATA cable — a charge-only "
                  "lead powers the Pi and never shows up.")


def connect_hint(pi_key: str) -> str:
    """The connector guidance for this board, or the generic line if unknown.

    SOCKET FIRST, in one sentence, because that is what the operator is
    looking for while holding a plug. The DATA-cable line is last and is the
    same sentence on every board — it is a rule, not a fact about this Pi.
    """
    c = get(pi_key)
    if c is None:
        return UNKNOWN_HINT
    if not c.can_cable:
        return c.why_not
    bits = [tr("Plug into {port}.").format(port=c.data_port)]
    if c.power_port:
        bits.append(tr("Power goes into {port}.").format(port=c.power_port))
    if c.caveat:
        bits.append(c.caveat)
    bits.append(tr("Use a DATA cable — a charge-only lead powers the Pi and "
                   "never shows up."))
    return " ".join(bits)


#: Where a node's power comes from during the provisioning run, per board. The
#: operator asked for the final step to "tell the user to attach the pi to a
#: power source" (2026-08-09), which is right on one of these boards, wrong on
#: another and impossible on a third — so it is a per-board line, exactly like
#: connect_hint above.
#:
#: WHY IT MATTERS RATHER THAN BEING TIDINESS. Provisioning is the longest, most
#: current-hungry thing a node does on the cable: a full apt/pip install with
#: the radio attached. The medic reads `throttled=0x50000` — undervoltage has
#: occurred — on a 3 A supply, and a browning-out rail has already taken out a
#: whole xhci controller on this bench once. Where a node CAN take its own
#: supply, it should.
_POWER_HINTS = {
    # Separate PWR IN socket, electrically apart from the data port: its own
    # supply is safe AND wanted, and the medic then carries data only.
    "pi_zero_2w": tr("Give the Pi its own power on the OUTER micro-USB marked "
                     "PWR IN. A separate socket, so nothing fights, and Node "
                     "Medic is left carrying data alone."),
    # One USB-A, one micro-USB, and an ordinary A-to-A carries 5V at BOTH ends.
    # A second supply back-feeds into the medic. Only safe with the cable's
    # power wire lifted.
    # Why the two supplies fight is stated at the CONNECT step, where the
    # A-to-A cable is first named. Here it only has to be enforced.
    "pi_3a_plus": tr("Power comes from Node Medic through this cable. Do NOT "
                     "plug a supply into the micro-USB as well, unless your "
                     "A-to-A has its 5V wire removed."),
    # Same socket for both; nothing to add.
    "pi_4b": tr("Power and data share the one USB-C, so there is no second "
                "socket to add a supply to. Keep the cable short and good."),
    "pi_5": tr("Power and data share the one USB-C, so there is no second "
               "socket to add a supply to. Keep the cable short and good."),
}

#: Said when the board is unknown: true of every Pi, and claims no socket.
UNKNOWN_POWER_HINT = tr(
    "Installing takes minutes and draws more current than anything before it. "
    "If this Pi has a power socket separate from its data one, use it.")


def power_hint(pi_key: str) -> str:
    """What to do about POWER for the long provisioning run, per board."""
    return _POWER_HINTS.get((pi_key or "").strip(), UNKNOWN_POWER_HINT)


def can_cable(pi_key: str) -> bool:
    """False only when we KNOW the board cannot do it. An unknown board is not
    declared impossible — fail open, and let the operator try."""
    c = get(pi_key)
    return True if c is None else c.can_cable


#: The wiring AFTER the Pi comes off Node Medic — the last step of the birth.
#:
#: A different question from power_hint, and the two had been answered with one
#: string. The 3 A+ line reads "The Pi is drawing its power from Node Medic
#: through this cable. Do NOT plug a supply into its micro-USB" — correct while
#: it IS on the cable, and a flat contradiction on the step whose whole
#: instruction is "unplug the Pi from Node Medic and give it its own power
#: supply" (seen in a render, 2026-08-10). The back-feed it warns about cannot
#: happen once the medic's cable is gone; the operator is left choosing between
#: two sentences on the same screen.
_STANDALONE_HINTS = {
    "pi_zero_2w": tr("Power on the OUTER micro-USB (PWR IN). The radio goes on "
                     "the inner one — it needs a micro-USB OTG adapter."),
    "pi_3a_plus": tr("Power on the micro-USB. The radio goes in the full-size "
                     "USB-A, the socket Node Medic was using. The earlier "
                     "two-supplies warning is done with — the medic's cable is "
                     "off."),
    "pi_4b": tr("Power on the USB-C. The radio goes in any USB-A socket."),
    "pi_5": tr("Power on the USB-C. The radio goes in any USB-A socket."),
}

#: Claims no socket, because we do not know which board it is.
UNKNOWN_STANDALONE_HINT = tr(
    "Give the Pi its own power supply and put the radio on a data port. It "
    "runs on its own from here.")


def standalone_power_hint(pi_key: str) -> str:
    """Power and radio wiring once the node is off Node Medic and on its own."""
    return _STANDALONE_HINTS.get((pi_key or "").strip(),
                                 UNKNOWN_STANDALONE_HINT)
