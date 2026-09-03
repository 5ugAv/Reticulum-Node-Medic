"""What can this thing become? — the knowledge behind "Show me what you got".

Until now the medic asked "which of these 16 boards is yours?". That question
only works for someone who went out and bought one of the 16. Operator,
2026-09-03: the tool is for remote communities, and it has to work with "any
hardware people might have lying around — an old radio, a digital radio,
something with a chip inside", recycled and upcycled.

So the question turns around. The keeper shows the medic what they have, and the
medic says what it could BE. That is a different shape of knowledge: not a
catalogue of part numbers, but a set of roles a Reticulum network needs filled,
and honest rules about which piece of junk can fill which.

WHAT A RETICULUM NETWORK ACTUALLY NEEDS. Not all of it is a LoRa board, and
this is the part the old flow hid. A network needs radios to carry traffic
between places, something to keep messages for people who were not listening,
and things for people to read and write on. An old phone and a scrap router can
each do real work here. The LoRa board is one answer, not the answer.

WHAT THIS MODULE WILL NOT DO. It will not tell someone their hardware is fine
when it is not, and it will not skip past a licence. Transmitting data on radio
bands is regulated nearly everywhere, and the rules differ by country and by
band — so where a path needs a licence or a rule check, that is part of the
answer and not a footnote. A tool that got someone in a remote community into
trouble with their regulator would have failed them worse than one that could
not help at all.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Dict, List, Optional, Tuple

# --------------------------------------------------------------------------- #
# The roles a network needs filled.
# --------------------------------------------------------------------------- #

#: Keys are stable (they go in records); the words are what a person reads.
#: Deliberately NOT the tool's existing vocabulary — "transport node" and
#: "propagation node" are Reticulum's words, correct and meaningless to someone
#: meeting the network for the first time. The guide teaches the real terms; this
#: screen has to be understood before anyone has read the guide.
ROLES: Dict[str, Dict[str, str]] = {
    "radio_node": {
        "name": "A radio that carries messages",
        "plain": "It passes messages on to the next place. This is what makes "
                 "the network reach further than one village.",
    },
    "keeper": {
        "name": "A mailbox that holds messages",
        "plain": "It keeps messages for people who were switched off, and hands "
                 "them over when they come back.",
    },
    "reader": {
        "name": "Something to read and write on",
        "plain": "A screen and a keyboard for the person. It does not carry "
                 "anyone else's messages.",
    },
    "bridge": {
        "name": "A bridge between two networks",
        "plain": "It joins a radio network to another one — over a cable, or "
                 "over wifi, or over the internet if there is any.",
    },
    "parts": {
        "name": "Useful parts",
        "plain": "Not a node on its own, but there is something inside worth "
                 "keeping — an aerial, a battery, a case, a chip.",
    },
}


@dataclass(frozen=True)
class Path:
    """One route from a piece of hardware to a working role.

    ``needs`` is the honest bill: what the keeper must find or do that they do
    not already have in their hand. An empty ``needs`` means plug it in and go.
    """

    role: str
    title: str
    plain: str
    needs: Tuple[str, ...] = ()
    medic_can: Tuple[str, ...] = ()
    caution: str = ""
    difficulty: str = "easy"          # easy | some work | hard
    #: False when the medic cannot yet carry this out end to end. Shown as such
    #: rather than hidden, because "we know how, we cannot do it for you yet" is
    #: a useful answer and a silent omission is not.
    medic_ready: bool = True

    @property
    def ready_now(self) -> bool:
        return self.medic_ready and not self.needs


@dataclass
class Found:
    """What the keeper has, as far as anyone can tell.

    Every field is optional because this has to work for a thing nobody can
    identify. ``kind`` is the one thing a person can always answer, because it
    is a question about shape rather than about electronics.
    """

    kind: str = "unknown"       # see KINDS
    chip: str = ""              # esp32s3, nrf52840, ...
    has_lora: Optional[bool] = None
    has_wifi: Optional[bool] = None
    has_usb: Optional[bool] = None
    has_screen: Optional[bool] = None
    transmits: Optional[bool] = None    # is it a radio that can already talk?
    band: str = ""                      # what a radio is licensed/able to use
    runs_linux: Optional[bool] = None
    board_key: str = ""                 # set when it IS a catalogue board
    note: str = ""


#: The shapes a person can recognise without knowing any electronics. This list
#: is the actual question the screen asks, so it is written as things you can
#: hold, not as categories.
KINDS: List[Tuple[str, str]] = [
    ("dev_board", "A small circuit board with pins along the edges"),
    ("handheld_radio", "A handheld radio — the kind you talk into"),
    ("phone", "An old mobile phone or tablet"),
    ("computer", "An old laptop, desktop or single-board computer"),
    ("router", "A wifi router or set-top box"),
    ("unknown", "Something else with a chip in it"),
]


# --------------------------------------------------------------------------- #
# The rules. What each kind of found thing can become.
# --------------------------------------------------------------------------- #

#: Chips the RNode firmware runs on. Anything else with a LoRa radio still has a
#: route, it just needs a firmware port first — which is a real answer ("this
#: can work, somebody has to do a day's work") and not a refusal.
RNODE_CHIPS = ("esp32", "esp32s3", "esp32c3", "nrf52840")

#: The licence sentence. It appears on EVERY transmitting path, unchanged.
#:
#: Rules differ by country and by band and this tool cannot know the keeper's
#: jurisdiction, so it says what is universally true — that there ARE rules and
#: they must be checked — rather than guessing a country and being wrong. Two
#: specifics are worth naming because they surprise people who have already got
#: the hardware working: data on licence-free voice bands is often not allowed
#: at all, and Reticulum encrypts everything by default, which several countries
#: forbid on amateur bands.
LICENCE = ("Check what your country allows on this band before you transmit. "
           "Data is often not allowed on the licence-free voice channels, and "
           "some countries do not allow encrypted messages on amateur bands — "
           "Reticulum encrypts everything, always.")


def _dev_board_paths(f: Found) -> List[Path]:
    out: List[Path] = []
    chip = (f.chip or "").lower()
    known_chip = any(chip.startswith(c) for c in RNODE_CHIPS)

    if f.has_lora and f.board_key:
        out.append(Path(
            role="radio_node",
            title="This is a board the medic already knows",
            plain="Plug it in and the medic can turn it into a radio node for "
                  "you. Nothing else to find.",
            medic_can=("flash it", "set the radio settings", "give it a name"),
            difficulty="easy"))
        return out

    if f.has_lora and known_chip:
        out.append(Path(
            role="radio_node",
            title="This can be a radio node",
            plain="It has a LoRa radio and a chip the node software runs on. "
                  "The medic has not met this exact board before, so it has to "
                  "be told which pins the radio is wired to.",
            needs=("the board's pin diagram — printed on it, or on the seller's "
                   "page",),
            medic_can=("flash it once it knows the pins",),
            difficulty="some work"))
    elif f.has_lora:
        out.append(Path(
            role="radio_node",
            title="This could be a radio node, but not yet",
            plain="It has a LoRa radio, but its chip is one the node software "
                  "has never been built for. Somebody has to do that work once, "
                  "and then every board like it works.",
            needs=("someone to port the firmware to this chip",),
            difficulty="hard", medic_ready=False))
    elif known_chip:
        out.append(Path(
            role="radio_node",
            title="Add a radio and this becomes a node",
            plain="The chip is right — it just has no radio on it. A small LoRa "
                  "module and eight soldered wires turn this into a radio node.",
            needs=("a LoRa module (SX1276 or SX1262)",
                   "a soldering iron and eight short wires"),
            medic_can=("flash it once the radio is wired on",),
            caution="Get the module for YOUR part of the world — a 433 MHz "
                    "module cannot talk to an 868 or 915 MHz one.",
            difficulty="some work"))

    if f.has_wifi and not f.has_lora:
        out.append(Path(
            role="bridge",
            title="It can join two networks over wifi",
            plain="No radio for the long distance, but it can carry messages "
                  "between things that are already close together.",
            needs=("someone to write this part of the tool",),
            difficulty="some work", medic_ready=False))

    if not out:
        out.append(_parts_path("There is a chip, an aerial or a battery in here "
                               "worth keeping even if the board cannot be a node."))
    return out


def _handheld_radio_paths(f: Found) -> List[Path]:
    """The recycling path the operator asked for by name.

    A voice handheld has no LoRa in it and never will. What it DOES have is a
    transmitter, an aerial and a licence-shaped hole — and Reticulum speaks KISS,
    which is how packet radio has moved data over voice radios for forty years.
    The chain is radio -> audio lead -> a computer running a software TNC ->
    Reticulum. Every link is old, documented and cheap.

    It is slow. Saying so here is the point: someone who expects web pages will
    put it down in disgust, and someone who expects text messages between
    villages will be delighted. The tool should set that expectation before they
    spend a weekend on it.
    """
    return [
        Path(
            role="radio_node",
            title="This radio can carry messages",
            plain="Not by itself — it needs a lead to a computer, and a piece "
                  "of software that turns messages into sound and back. People "
                  "have been doing this for forty years. It is slow: think "
                  "short written messages, not pictures.",
            needs=("a cheap USB sound adaptor with a transmit wire (look for "
                   "the CM108 chip)",
                   "a lead from that adaptor to the radio's two sockets",
                   "any computer that can run the sound software — an old "
                   "laptop or another Raspberry Pi"),
            medic_can=("show you the wiring",
                       "give you the sound software it already carries"),
            caution=LICENCE,
            difficulty="some work", medic_ready=False),
        _parts_path("Even if you never use it this way, the aerial and the "
                    "battery are worth keeping."),
    ]


def _phone_paths(f: Found) -> List[Path]:
    return [
        Path(
            role="reader",
            title="This is something to read and write on",
            plain="An old phone with no sim card and no phone signal still "
                  "works as the screen and keyboard for the network. The medic "
                  "carries the app and can put it on for you, with no internet.",
            medic_can=("install the messaging app over a cable",),
            difficulty="easy"),
    ]


def _computer_paths(f: Found) -> List[Path]:
    return [
        Path(
            role="keeper",
            title="This can hold messages for people who were away",
            plain="Left switched on somewhere with power, it keeps messages "
                  "until the person they belong to comes back into range.",
            needs=("somewhere with steady power",),
            medic_can=("install the software from what it carries, with no "
                       "internet",),
            difficulty="some work", medic_ready=False),
        Path(
            role="bridge",
            title="It can be the computer a handheld radio talks to",
            plain="If you also have a handheld radio, this is the machine that "
                  "turns messages into sound for it.",
            caution=LICENCE,
            difficulty="some work", medic_ready=False),
        Path(
            role="reader",
            title="It is also something to read and write on",
            plain="A keyboard and a proper screen, which is easier than a "
                  "phone for anyone doing a lot of writing.",
            difficulty="easy", medic_ready=False),
    ]


def _router_paths(f: Found) -> List[Path]:
    return [
        Path(
            role="bridge",
            title="It may be able to join networks together",
            plain="Many routers can be given new software and made useful. It "
                  "depends entirely on the model, and getting it wrong leaves "
                  "you with a brick.",
            needs=("someone who has done this before, or a very careful read of "
                   "the model's own instructions",),
            difficulty="hard", medic_ready=False),
        _parts_path("The power supply and the aerials are worth keeping "
                    "whatever happens to the rest."),
    ]


def _parts_path(plain: str) -> Path:
    return Path(role="parts", title="Worth keeping for parts", plain=plain,
                difficulty="easy", medic_ready=False)


_BY_KIND = {
    "dev_board": _dev_board_paths,
    "handheld_radio": _handheld_radio_paths,
    "phone": _phone_paths,
    "computer": _computer_paths,
    "router": _router_paths,
}

#: Best first. A keeper with one afternoon should see the thing that works today
#: at the top, and "somebody has to port a firmware" at the bottom.
_ORDER = {"easy": 0, "some work": 1, "hard": 2}


def paths_for(found: Found) -> List[Path]:
    """Every route from *found* to a working role, best first.

    Never returns an empty list. "We do not know what this is" is a state the
    tool has to have an answer for, because it is the state most salvaged
    hardware arrives in — and the answer is a next step, not a shrug.
    """
    fn = _BY_KIND.get(found.kind)
    out = list(fn(found)) if fn else []
    if not out:
        out = [Path(
            role="parts",
            title="The medic cannot tell what this is yet",
            plain="If it has a USB socket, plug it into the medic and it will "
                  "read what it can off the chip. If it does not, the aerial, "
                  "the battery and the case are still worth keeping.",
            medic_can=("read the chip over USB, if it has a socket",),
            difficulty="easy")]
    # Parts is ALWAYS last. It is the consolation answer, and sorting it by
    # difficulty floated it above the real route — a keeper holding a handheld
    # radio was told to strip it for the aerial before being told it can carry
    # messages.
    out.sort(key=lambda p: (p.role == "parts", not p.ready_now,
                            _ORDER.get(p.difficulty, 9)))
    return out


def is_unidentified(found: Found) -> bool:
    """True when nothing is known yet — a different state from "known, and it
    cannot be a node", and it needs a different sentence."""
    return found.kind == "unknown" and not (found.chip or found.board_key)


def summary(found: Found) -> str:
    """One sentence for the top of the screen. Says what was decided, not what
    was looked at.

    "Good news" is reserved for something that works TODAY. An early version
    said it over the not-identified-yet answer, which is the tool congratulating
    someone on having told it nothing.
    """
    if is_unidentified(found):
        return ("Let's find out what this is. If it has a USB socket, plug it "
                "into the medic.")
    paths = paths_for(found)
    ready = [p for p in paths if p.ready_now and p.role != "parts"]
    if ready:
        return f"Good news — {ready[0].title.lower().rstrip('.')}."
    workable = [p for p in paths if p.role != "parts"]
    if workable:
        return ("This can be part of the network. You will need to find a "
                "thing or two first — here is what.")
    return "This cannot be a node on its own, but do not throw it away."


# --------------------------------------------------------------------------- #
# From what the medic can read, to what it still has to ask.
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class Question:
    """A yes/no question worth asking a person who knows no electronics.

    ``look_for`` is the whole trick. "Does it have a LoRa radio?" is unanswerable
    by the person this tool is for. "Is there a small metal box on it, with a
    little aerial socket next to it?" is answerable by anyone holding the board.
    """

    field: str
    text: str
    look_for: str = ""


def found_from_detection(result: Dict) -> Found:
    """Turn what ``ui.board_detect.detect_board`` read into a Found.

    The medic can read the chip, the flash size and the USB names off almost
    anything with a socket. It CANNOT read whether a LoRa radio is fitted —
    nothing on the USB bus says so. So that stays None, meaning "not known", and
    ``questions_for`` turns it into something a person can answer by looking.
    Guessing it instead would send someone soldering for an afternoon on the
    strength of a chip ID.
    """
    if not result or not result.get("found"):
        return Found(kind="unknown")
    board_key = result.get("board_key") or ""
    known = bool(board_key)
    return Found(
        kind="dev_board",
        chip=(result.get("chip") or ""),
        board_key=board_key,
        # A catalogue board's radio is a fact we hold; anything else is unknown.
        has_lora=True if known else None,
        has_usb=True,
        note=(result.get("reason") or ""),
    )


#: Asked only when the answer would change what the keeper is told. Ordered by
#: how much it changes.
_QUESTIONS: Tuple[Question, ...] = (
    Question("has_lora",
             "Is there a radio on the board?",
             "Look for a small metal box about the size of a fingernail, with "
             "a tiny screw socket or a short wire aerial next to it. If there "
             "is nowhere to attach an aerial, there is no radio."),
    Question("has_screen",
             "Does it have a little screen?",
             "A small glowing panel, usually white or blue text on black."),
    Question("has_wifi",
             "Does it say wifi or bluetooth anywhere on it?",
             "Printed on the board itself, or on the metal box."),
)


def questions_for(found: Found) -> List[Question]:
    """The questions still worth asking, best first.

    A question is worth asking ONLY when the two answers lead somewhere
    different. Asking anything else wastes the patience of someone who is
    already unsure they belong here — and this tool's whole job is to keep that
    person from putting it down.
    """
    out: List[Question] = []
    for q in _QUESTIONS:
        if getattr(found, q.field, None) is not None:
            continue                       # already known, do not ask again
        yes = paths_for(replace(found, **{q.field: True}))
        no = paths_for(replace(found, **{q.field: False}))
        if [p.title for p in yes] != [p.title for p in no]:
            out.append(q)
    return out
