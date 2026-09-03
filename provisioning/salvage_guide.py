"""How to actually do it — the recycling how-tos, in plain words.

``salvage.py`` decides WHAT a piece of hardware could become. This is the part
that tells the keeper HOW, step by step, for the routes worth walking. Operator,
2026-09-03: "maybe we can start building a section on recycling old materials,
just a how-to, as well as the software flash that we needed inside that".

Content only, no Kivy, same as ``network_guide`` — so it is unit-testable, can
be rendered as a screen or an inline help popup, and can be read aloud later if
the medic ever gets a speaker.

HOW THESE ARE WRITTEN. Short sentences. Ordinary words. Nothing assumed except
that the reader can look at the thing in their hand. Where a step needs a tool
or a part, it is named at the TOP in "what you need", never sprung halfway
through — the worst moment to discover you need a soldering iron is with the
case already open.

WHERE THE MEDIC CANNOT YET HELP, THE GUIDE SAYS SO. None of these are wired to
a button yet. A how-to that quietly implies the tool will do it for you is the
same lie as a screen promising a vault that was never built.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


@dataclass(frozen=True)
class Step:
    text: str
    detail: str = ""
    #: Something that will go wrong for a lot of people at this step. Put where
    #: it happens, not collected at the end where nobody reads it.
    watch_out: str = ""


@dataclass(frozen=True)
class Guide:
    key: str
    title: str
    opening: str
    needs: Tuple[str, ...]
    steps: Tuple[Step, ...]
    expect: str = ""
    caution: str = ""
    #: True once the medic can carry this out itself. All False today, and the
    #: screens must show that rather than imply a button exists.
    medic_does_it: bool = False


# --------------------------------------------------------------------------- #
# The handheld radio. The one the operator asked for by name.
# --------------------------------------------------------------------------- #

HANDHELD = Guide(
    key="handheld_radio",
    title="Turn a handheld radio into part of the network",
    opening=(
        "A talking radio has no computer chip for messages in it. What it does "
        "have is a transmitter and an aerial, and that is the expensive part. "
        "You make up the rest: the radio sends sound, so we turn messages into "
        "sound at one end and back into messages at the other. People have "
        "been doing this since the 1980s."),
    needs=(
        "The radio, and a way to charge it.",
        "A lead from the radio's headphone and microphone sockets to a "
        "computer. You can buy one made for radios, or make one.",
        "A cheap USB sound adaptor, if the computer has no microphone socket.",
        "Any computer that can stay switched on — an old laptop, or another "
        "small board like the one inside this medic.",
    ),
    steps=(
        Step("Find out what your radio's sockets are.",
             "Most handhelds use two small round plugs of different sizes. "
             "Search for your radio's name and the word 'programming cable' — "
             "the picture will show you.",
             watch_out="The two plugs are usually different sizes on purpose. "
                       "Forcing the wrong one in can break the socket."),
        Step("Connect the radio's earphone socket to the computer's microphone "
             "socket, and the computer's earphone socket to the radio's "
             "microphone socket.",
             "Out of one goes into the other, both ways. That is all the lead "
             "does.",
             watch_out="Turn the radio's volume down to about a quarter first. "
                       "Full volume will overload the computer and nothing will "
                       "work, and it will look like the lead is wrong."),
        Step("Set the radio to transmit when it hears sound.",
             "Most handhelds call this VOX. It saves you wiring a separate "
             "wire to press the transmit button.",
             watch_out="VOX takes a moment to start transmitting, so the "
                       "beginning of every message gets cut off. The software "
                       "in the next step has a setting to send a longer run-up. "
                       "If messages are getting through only sometimes, make "
                       "that run-up longer."),
        Step("Install the sound software on the computer.",
             "It is called Dire Wolf, and it is free. It listens on the "
             "microphone socket and writes out messages, and the other way "
             "round."),
        Step("Point Reticulum at it.",
             "Dire Wolf offers what is called a KISS connection. Reticulum has "
             "been able to talk to those for years — it is one line in its "
             "settings file."),
        Step("Test it with a second radio before you rely on it.",
             "Two radios on a table, a metre apart, both turned right down. If "
             "a message gets across the table, the hard part is done."),
    ),
    expect=(
        "It is slow — think short written messages, like a note passed between "
        "villages, not pictures or web pages. It will carry a conversation. It "
        "will not carry a photograph in any reasonable time."),
    caution=(
        "Check what your country allows on this band before you transmit. Data "
        "is often not allowed on the licence-free voice channels, and some "
        "countries do not allow encrypted messages on amateur bands — "
        "Reticulum encrypts everything, always. Ask a local radio club; this "
        "is exactly what they know."),
)


# --------------------------------------------------------------------------- #
# A bare chip with no radio.
# --------------------------------------------------------------------------- #

ADD_A_RADIO = Guide(
    key="add_a_radio",
    title="Add a radio to a board that has none",
    opening=(
        "Plenty of salvaged boards have the right chip and no radio — out of "
        "an old sensor, a toy, a broken gadget. A radio module costs very "
        "little and connects with eight wires."),
    needs=(
        "The board, with a working USB socket.",
        "A LoRa radio module — the numbers to look for are SX1276 or SX1262.",
        "A soldering iron, thin solder, and eight short wires.",
        "An aerial that matches the module's frequency.",
    ),
    steps=(
        Step("Buy the module for YOUR part of the world.",
             "The frequency is printed on it. A 433 MHz module cannot talk to "
             "an 868 or a 915 one, no matter what software you put on it.",
             watch_out="This is the mistake that wastes the most money. Check "
                       "what the nodes near you already use, and match it."),
        Step("Find the six named connections on the module.",
             "They are usually printed on the board itself: MOSI, MISO, SCK, "
             "NSS, RESET and DIO0."),
        Step("Add power and ground — that is the other two wires.",
             "The module runs on 3.3 volts.",
             watch_out="Never 5 volts. It will destroy the module the first "
                       "time you switch it on."),
        Step("Write down which pin on your board each wire went to.",
             "The medic has to be told this once. After that it can flash this "
             "board and any other like it."),
        Step("Plug it into the medic and let it flash the node software.",
             "From here it is an ordinary board."),
    ),
    expect=(
        "Once it works it is a full radio node, the same as a bought one. The "
        "soldering is the only hard part, and it is eight joints."),
)


# --------------------------------------------------------------------------- #
# An old phone.
# --------------------------------------------------------------------------- #

OLD_PHONE = Guide(
    key="old_phone",
    title="Use an old phone to read and write messages",
    opening=(
        "A phone with no sim card, no credit and no signal is still a good "
        "screen and keyboard. It does not need a phone company at all — it "
        "talks to a radio node over bluetooth or a cable."),
    needs=(
        "An Android phone or tablet that still charges.",
        "Nothing else. No sim card, no credit, no internet.",
    ),
    steps=(
        Step("On the medic, open CHAT and choose 'Send to my phone'.",
             "The medic already has the app. It puts a square pattern on the "
             "screen for the phone to look at."),
        Step("Join the phone to the medic's wifi.",
             "The medic makes its own wifi. The phone joins that — this is not "
             "the internet, it is just the two of them talking."),
        Step("Point the phone's camera at the square pattern.",
             "The phone will offer to download the app. Say yes."),
        Step("Let the phone install it.",
             "It will warn you that the app did not come from its usual shop. "
             "That is expected — it came from the medic in front of you.",
             watch_out="The permission is usually called 'install unknown "
                       "apps'. If you cannot find it, search the phone's "
                       "settings for the word 'unknown'."),
        Step("Open the app and let it find your radio node.",
             "Over bluetooth, or over a cable if the node is plugged in."),
    ),
    expect=(
        "This phone does not carry anyone else's messages — it is just your "
        "way of reading and writing your own. To help the network reach "
        "further, you need a radio node as well."),
    # The ONLY one of these the medic can already do end to end: CHAT ▸ Send to
    # my phone serves the carried APK over its own wifi behind a QR
    # (workflows/phone_serve.py, workflows/phone_apps.py). Verified in the code
    # 2026-09-03 — it is a wifi handout, NOT a cable install, and an earlier
    # draft of this guide had the keeper hunting for a cable that is not needed.
    medic_does_it=True,
)


GUIDES: Tuple[Guide, ...] = (HANDHELD, ADD_A_RADIO, OLD_PHONE)

#: Which how-to answers which route out of ``salvage.paths_for``. Keyed on the
#: path title so the two files cannot drift apart silently — a test holds them
#: in step.
GUIDE_FOR_PATH: Dict[str, str] = {
    "This radio can carry messages": "handheld_radio",
    "Add a radio and this becomes a node": "add_a_radio",
    "This is something to read and write on": "old_phone",
}


def guide(key: str) -> Optional[Guide]:
    for g in GUIDES:
        if g.key == key:
            return g
    return None


def guide_for_path(path_title: str) -> Optional[Guide]:
    """The how-to for a route, or None when there is not one yet."""
    key = GUIDE_FOR_PATH.get(path_title)
    return guide(key) if key else None
