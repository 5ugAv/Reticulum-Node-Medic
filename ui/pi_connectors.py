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
    #: A complete replacement hint, when the composed form runs too long for
    #: the step it lands on (the 3 A+ connect step carries a warning box too,
    #: and the panel-height budget is real — test_birth_guide holds it).
    one_liner: str = ""
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
        # No power_port sentence: on this board the medic's cable IS the
        # power, and naming the micro-USB here invited exactly the two-supply
        # mistake the warning box below the step now forbids (2026-08-14).
        power_port="",
        # The dual-supply HAZARD moved to connect_warning() — a proper
        # warning box on the step — because folded into this sentence it
        # "read like plug both in at the same time" (operator, 2026-08-14).
        caveat=tr("Use an A-to-A DATA cable — a charge-only lead won't "
                  "be seen."),
        # BOTH routes name their socket (operator, 2026-08-25, holding a
        # supply over the wrong plug): the cable route uses the USB-A, the
        # own-supply route puts power into the micro-USB. Before this, every
        # socket this screen named was the USB-A — so "power the Pi" read as
        # "power into the USB-A".
        one_liner=tr("Cable: A-to-A DATA lead into the USB-A, not "
                     "charge-only. Own power: micro-USB.")),
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


#: THE TRAP, per board — the thing the step's instruction cannot say for
#: itself. Since 2026-09-07 the connect step's BODY names this board's sockets
#: (see power_roads), so a hint that also named them said everything twice and
#: spent panel height doing it. What is left here is what the instruction
#: leaves out: on a Zero, that the two sockets are indistinguishable by eye;
#: everywhere else, that a charge-only cable fails INVISIBLY.
#:
#: Said as a SYMPTOM, not a specification. "Use a DATA cable" is unactionable
#: — charge-only cables look identical, so there is no test the operator can
#: perform. "If nothing happens, try a different cable" is something they can
#: actually do at the moment they are stuck, which is the moment it is read.
#: Three bench faults in one session on 2026-08-06 were cables, and every one
#: first looked like a software bug.
_CONNECT_TRAPS = {
    # BOTH traps on this board, because it has room for both and it is the
    # likeliest first Pi anyone builds: the sockets cannot be told apart by
    # eye, AND a charge-only lead in the right socket still fails invisibly.
    "pi_zero_2w": tr("The two micro-USB sockets look identical. The outer one "
                     "cannot carry data at all, and a charge-only cable in "
                     "the inner one fails the same silent way."),
    # ONE LINE on this board, and it is a height decision, not a style one:
    # the 3 A+ is the only Pi carrying a warning box on this step, and title +
    # body + a two-line hint + that box leaves the animation 26 dp — under the
    # 40 dp floor the guard enforces. The dual-supply hazard earns the box;
    # the cable rule fits in one line.
    "pi_3a_plus": tr("A-to-A DATA cable only — a charge-only lead never "
                     "shows up."),
    "pi_4b": tr("A charge-only cable powers the Pi, but Node Medic never sees "
                "it. If nothing happens, try a different cable."),
    "pi_5": tr("A charge-only cable powers the Pi, but Node Medic never sees "
               "it. If nothing happens, try a different cable."),
}


def connect_hint(pi_key: str) -> str:
    """This board's TRAP, or the generic cable rule when the board is unknown.

    Until 2026-09-07 this named the board's sockets, because the step's body
    was one generic string for every Pi and the sockets had nowhere else to
    live. power_roads() now carries them in the body, where the instruction
    is — so this line stopped being the socket guidance and became the
    warning that goes with it. A board we cannot identify still gets a line
    that names NO socket, which is the rule this whole module exists for.
    """
    c = get(pi_key)
    if c is None:
        return UNKNOWN_HINT
    if not c.can_cable:
        return c.why_not
    return _CONNECT_TRAPS.get((pi_key or "").strip(), UNKNOWN_HINT)


def connect_warning(pi_key: str) -> str:
    """The one DANGEROUS thing about cabling this board, for the step's
    warning box — or "" when the board has none (a warning box with nothing
    dangerous in it teaches people to skip warning boxes).

    Only the 3 A+ carries one: its A-to-A cable feeds 5 V from the medic, so
    adding a wall supply puts two sources against each other (back-feed).
    The Zero 2 W's identical-sockets trap is a mix-up, not a hazard, and the
    USB-C boards carry power and data in the one plug.
    """
    if pi_key == "pi_3a_plus":
        # NO EXCEPTION INSIDE THE BOX (operator approved, 2026-09-07). This
        # ended "(safe only with the power wire removed)" from 2026-08-14.
        # An exception in a hazard box turns an absolute into something
        # negotiable, and "the power wire" names nothing the operator can see
        # — the only reading that makes it actionable is cutting the 5V
        # conductor inside their cable, which is bench surgery, not a step.
        # A tired reader lands on "so there IS a safe way to have both".
        #
        # It also reads as a rule about COMBINING rather than a ban on the
        # micro-USB, which is what stops it fighting the own-supply road that
        # puts a power lead in that same socket.
        return tr("Never both at once — the medic's cable and a micro-USB "
                  "supply are two 5V sources fighting. It can damage the Pi "
                  "or Node Medic.")
    return ""


#: NOTE (2026-09-07): power_hint()/_POWER_HINTS/UNKNOWN_POWER_HINT lived here
#: and were DELETED. They answered "what about power during the long
#: provisioning run", per board — a real question, and the only place the fact
#: "on a Zero, cable AND supply is right, a socket each" was written down.
#: They had had no production caller since 2026-08-14 (imported by
#: birth_guide_flow and never used), so that fact reached no screen while the
#: connect step was telling Zero operators to "choose ONE way".
#:
#: power_roads() below now carries it, in the BODY of the step where the
#: operator is actually holding the plug. Deleted rather than left dangling:
#: a tested-but-never-run function is how can_cable() hid for months.

def can_cable(pi_key: str) -> bool:
    """False only when we KNOW the board cannot do it. An unknown board is not
    declared impossible — fail open, and let the operator try."""
    c = get(pi_key)
    return True if c is None else c.can_cable


#: HOW THIS BOARD GETS ITS POWER, as the two lines of the connect step's BODY.
#:
#: The body used to be one string for every board: "A — Node Medic's cable
#: powers the Pi. / B — its own supply; the card knows this Wi-Fi." Three
#: things were wrong with that, all found 2026-09-07 and all board-shaped:
#:
#: 1. "Choose ONE way" is FALSE on a Zero 2 W. That board has a separate PWR
#:    IN socket, and _STANDALONE_HINTS and _POWER_HINTS both say the right
#:    setup is cable AND supply — a socket each, nothing fighting. The screen
#:    forbade the configuration the tool itself recommends.
#: 2. "A —" / "B —" read as buttons. There is no button on this step; it
#:    advances itself. Operators hunted the screen for controls that do not
#:    exist. The roads are now an instruction and a fallback introduced by a
#:    question the operator answers by looking at their hands ("No cable?").
#: 3. Neither line told anyone to DO anything. Both explained why a road
#:    works, on the one screen whose whole job is getting a plug into a hole.
#:
#: Sockets are named by SIZE as well as by name ("big USB-A", "small
#: micro-USB") because that is checkable by someone who does not know
#: connector names — which is who this tool is for.
_POWER_ROADS = {
    # ONE CABLE ON THIS STEP. The board does have a separate PWR IN socket,
    # and naming both here read as an instruction to plug both in — "it sounds
    # like the user's being instructed to plug the Pi into its own power as
    # well as into Node Medic, which is misleading; we don't want two lots of
    # power going in" (operator, with this screen in hand, 2026-09-09).
    #
    # They are right about this step. The medic's cable in the inner socket
    # carries data AND power; that is the whole cable-birth path. The outer
    # PWR IN belongs to the LAST step, where the Pi leaves the medic and takes
    # its own supply — and that step already says so in its own bullets.
    "pi_zero_2w": tr("One cable from Node Medic into the INNER micro-USB, "
                     "nearer the mini-HDMI.\n"
                     "It carries power and data — nothing goes in PWR IN "
                     "yet."),
    # One USB-A and one micro-USB, and an ordinary A-to-A carries 5V at both
    # ends — so here the two roads really are exclusive, and the warning box
    # under this body says so.
    "pi_3a_plus": tr("Node Medic's DATA cable into the big USB-A socket.\n"
                     "No cable? Its own supply in the small micro-USB "
                     "instead."),
    # One socket carries both; there is no second road to offer.
    "pi_4b": tr("One USB-C cable from Node Medic carries power and data.\n"
                "Nothing else to plug in."),
    "pi_5": tr("One USB-C cable from Node Medic carries power and data.\n"
               "Nothing else to plug in."),
    # Cannot do the cable at all (_check_pairing stops the build before this
    # step) — but if it is ever reached, only one road is true.
    "pi_3b_plus": tr("This Pi needs its own power supply.\n"
                     "Node Medic finds it over Wi-Fi, using the card's "
                     "details."),
}

#: Claims NO socket, because we do not know which board this is — the whole
#: reason this module exists. Still names both roads and the cable trap.
UNKNOWN_POWER_ROADS = tr(
    "Node Medic's DATA cable into the Pi's data port.\n"
    "No cable? Its own supply instead, if the card has your Wi-Fi.")


def power_roads(pi_key: str) -> str:
    """The connect step's BODY for this board: how to give the Pi power.

    Two lines, an instruction and its fallback. Never "choose one" — on some
    boards both at once is correct, and on others there is only one road.
    """
    return _POWER_ROADS.get((pi_key or "").strip(), UNKNOWN_POWER_ROADS)


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
    # Socket facts ONLY (operator, 2026-08-14): the bullets above this hint
    # already say unplug / radio on / own power — repeating the story here
    # was the 'instructions run twice' complaint. What survives is the one
    # thing the bullets cannot know: WHICH socket is which on this board.
    "pi_3a_plus": tr("Power into the micro-USB; radio into the USB-A with a "
                     "short DATA cable."),
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
