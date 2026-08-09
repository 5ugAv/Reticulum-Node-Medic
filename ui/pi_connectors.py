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
    "pi_zero_2w": Connectors(
        key="pi_zero_2w", can_cable=True,
        data_port=tr("the INNER micro-USB, the one nearer the mini-HDMI"),
        power_port=tr("the OUTER micro-USB, marked PWR IN"),
        caveat=tr("The two sockets look identical. The outer one cannot carry "
                  "data at all — it will power the Pi perfectly and never "
                  "appear here.")),
    "pi_3a_plus": Connectors(
        key="pi_3a_plus", can_cable=True,
        data_port=tr("the full-size USB-A socket"),
        power_port=tr("the micro-USB socket, which is power only"),
        caveat=tr("This one needs an A-to-A cable, and that cable carries 5V at "
                  "BOTH ends — so do not power the Pi separately unless the "
                  "cable has its power wire removed, or the two supplies will "
                  "fight each other.")),
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
        data_port=tr("the USB-C socket — the same one you would power it from"),
        caveat=tr("The USB-C port carries both power and data, so one cable to "
                  "Node Medic does everything.")),
    "pi_5": Connectors(
        key="pi_5", can_cable=True,
        data_port=tr("the USB-C socket — the same one you would power it from"),
        caveat=tr("The USB-C port carries both power and data, so one cable to "
                  "Node Medic does everything.")),
}

_ALIASES = {"pi_5_full": "pi_5"}


def get(pi_key: str) -> Optional[Connectors]:
    """Connector facts for *pi_key*, or None when the board is unknown."""
    k = (pi_key or "").strip()
    return PI_CONNECTORS.get(_ALIASES.get(k, k))


#: What to say when we do NOT know which board it is. Deliberately generic and
#: deliberately short: naming a specific socket for a board we have not
#: identified is the exact bug this module exists to prevent.
UNKNOWN_HINT = tr("Use the Pi's DATA port and a cable that carries DATA — a "
                  "charge-only lead will power the Pi perfectly and never show "
                  "up here.")


def connect_hint(pi_key: str) -> str:
    """The connector guidance for this board, or the generic line if unknown."""
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
    bits.append(tr("The cable must carry DATA — a charge-only lead will power "
                   "the Pi perfectly and never show up here."))
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
                     "PWR IN. It is a separate socket from the data one, so "
                     "the two never fight — and Node Medic is left carrying "
                     "data alone, which is the safest way through a long "
                     "install."),
    # One USB-A, one micro-USB, and an ordinary A-to-A carries 5V at BOTH ends.
    # A second supply back-feeds into the medic. Only safe with the cable's
    # power wire lifted.
    "pi_3a_plus": tr("The Pi is drawing its power from Node Medic through this "
                     "cable. Do NOT plug a supply into its micro-USB as well "
                     "unless your A-to-A cable has its 5V wire removed — an "
                     "ordinary one carries 5V at both ends and the two "
                     "supplies will fight. With a power-less cable, do give it "
                     "its own supply: this is the longest, hungriest part of "
                     "the build."),
    # Same socket for both; nothing to add.
    "pi_4b": tr("The Pi is powered through the same USB-C carrying the data, "
                "so there is no second socket to add a supply to. Keep the run "
                "short and the cable good."),
    "pi_5": tr("The Pi is powered through the same USB-C carrying the data, so "
               "there is no second socket to add a supply to. Keep the run "
               "short and the cable good."),
}

#: Said when the board is unknown: true of every Pi, and claims no socket.
UNKNOWN_POWER_HINT = tr(
    "Installing takes several minutes and draws more current than anything "
    "before it. If this Pi has a power socket separate from its data one, give "
    "it its own supply.")


def power_hint(pi_key: str) -> str:
    """What to do about POWER for the long provisioning run, per board."""
    return _POWER_HINTS.get((pi_key or "").strip(), UNKNOWN_POWER_HINT)


def can_cable(pi_key: str) -> bool:
    """False only when we KNOW the board cannot do it. An unknown board is not
    declared impossible — fail open, and let the operator try."""
    c = get(pi_key)
    return True if c is None else c.can_cable
