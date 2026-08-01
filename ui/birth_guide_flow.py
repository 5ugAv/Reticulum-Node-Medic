"""Pure guided-birth flow data — NO Kivy, so the ordering/copy is unit-testable
(and CI, which has no Kivy, can import it).

The step LISTS live here: the intro paths, and the ordered steps per path. Each
step names its animation by a string KEY (resolved to a widget in
``ui.screens.birth_guide_screen``) so this stays free of any UI imports.
"""

from __future__ import annotations

from ui.i18n import tr  # i18n: wrapped — guided-birth titles/bodies/hints/warnings

#: What the operator can build — the intro chooser, ordered by rising complexity.
#: key -> (title, subtitle).
BIRTH_PATHS = [
    ("host", tr("A radio for phone or computer (RNode)"),
     tr("Just flash a radio (RNode) to plug into a phone or computer you've already set up.")),
    # NOT "standalone radio" — operators wanting a plain RNode read that as
    # "just the radio" and walked into an RTNode build (2026-07-31).
    ("radio", tr("A mesh transport node (RTNode-2400)"),
     tr("Runs the mesh by itself — no phone, computer or Pi attached. Reports "
        "its health back and is remotely repairable.")),
    ("pi", tr("A Raspberry Pi + radio"),
     tr("A propagation node - reports its health back and is remotely repairable, and "
        "can hold messages for users who aren't online. It can also mesh LoRa radio to "
        "Wi-Fi, Bluetooth and the internet. The best node to future-proof the network.")),
]

#: The antenna-first step. It is NOT part of the guided lists — it's the very first
#: BIRTH screen (``birth_guide_screen._render_antenna``), shown BEFORE the detect
#: landing powers the board over USB, since that's when a missing antenna can fry
#: the radio. Every birth path reaches detect, so one landing covers them all.
ANTENNA_STEP = {
    "title": tr("Attach the antenna to the radio board"),
    "body": tr("Connect the antenna to the radio board through its pigtail cable — you "
               "need BOTH ends attached. Push the tiny gold U.FL / IPEX plug straight "
               "down onto the matching socket on the radio board until it clicks, AND "
               "make sure a 915 MHz antenna (or one rated for your band) is screwed onto "
               "the SMA connector at the other end of the cable."),
    "hint": tr("The tiny U.FL / IPEX plug is fragile — connect it once and leave it. "
               "Repeated unplugging weakens the connector and it can snap off."),
    "warning": tr("Never power a radio board with no antenna attached — transmitting "
                  "without one can permanently damage its radio (the power amplifier)."),
    "anim": "connect_antenna",
}

#: Ordered guided steps per path. Each step: title, body, optional ``anim`` key
#: ("connect_board" | "insert_sd" | None) and optional ``hint`` / ``warning`` /
#: ``next`` label. The last step's Next hands off to the real BIRTH flow. The
#: antenna step is NOT here — it's the landing screen ahead of all of these
#: (see ``ANTENNA_STEP`` + ``birth_guide_screen._render_antenna``).
_STEPS = {
    "radio": [
        {"title": tr("Connect your radio board"),
         "body": tr("Plug the radio board into Node Medic with a USB cable. Node Medic "
                    "powers it and will detect it automatically."),
         "hint": tr("Use a DATA USB cable — a charge-only cable won't be seen."),
         "anim": "connect_board"},
        # (The old third page — 'Let's set it up', a narration of what the
        # button was about to do — was removed as unnecessary; operator
        # decision 2026-07-31. This page now carries the Start-setup handoff.)
        {"title": tr("What happens next"),
         "body": tr("Nothing is running yet — this page just explains what's coming. "
                    "After flashing, Node Medic joins your node's own setup Wi-Fi, sets "
                    "its name and radio settings, and puts it on your network — no manual "
                    "web portal needed."),
         "hint": tr("The medic briefly leaves your Wi-Fi to talk to the node, then rejoins."),
         "anim": "provision",             # ANIMATION PLACEHOLDER — refine with the designer
         "next": tr("Start setup  →")},
    ],
    # THE CABLE BIRTH, proven end to end on 2026-08-01 (HOPE: Pi Zero 2 W +
    # Heltec V3, no WiFi and no powered hub anywhere in it). These steps used to
    # describe a different flow entirely — card into a USB reader, radio onto
    # the Pi, then a network address typed in — and every one of those is now
    # wrong. What actually happens:
    #
    #   the card goes in the PI, and the Pi becomes its own card reader;
    #   the RADIO goes on the MEDIC, which has the power to flash it;
    #   the medic reaches the Pi over the USB cable, so there is no address;
    #   they are only joined at the very end.
    #
    # The operator's single unavoidable job is the replug after imaging: the
    # medic cannot power-cycle a Pi (uhubctl on the Pi 5 root hub does not cut
    # VBUS — measured, the device stays powered), so it must be asked for.
    "pi": [
        {"title": tr("Put the SD card into the Raspberry Pi"),
         "body": tr("Slide the blank microSD card into the Pi's own card slot. "
                    "You don't need a card reader — the Pi will hand its card "
                    "to Node Medic by itself."),
         "anim": "insert_sd_pi"},
        {"title": tr("Connect the Pi to Node Medic"),
         "body": tr("Plug the Pi into Node Medic with a USB cable. Node Medic "
                    "powers it and opens its card automatically — no Wi-Fi and "
                    "no network setup are needed anywhere in this build."),
         "hint": tr("Use the Pi's DATA port. A Pi Zero has two identical "
                    "micro-USB sockets: the one nearer the mini-HDMI is USB "
                    "(data), the outer one is PWR IN and cannot carry data. "
                    "Use a short, thick cable — a thin or coiled one drops the "
                    "link."),
         "anim": "connect_pi",
         "next": tr("Open the card  →"), "screen": "pi_imager"},
        {"title": tr("Restart the Pi"),
         "body": tr("The card now has an operating system on it. Unplug the Pi "
                    "from Node Medic and plug it straight back in, so it starts "
                    "up from the new system."),
         "hint": tr("Node Medic can't switch the Pi off and on for you — this "
                    "is the one step it needs your hands for. First start-up "
                    "takes a minute or two."),
         "anim": "connect_pi"},
        # The radio goes on the MEDIC, not the Pi. This is the step that removes
        # the powered-hub problem entirely: a Pi Zero can't reliably feed a
        # Heltec V3 (900 mA peak against a 500 mA budget), but the medic has
        # 1600 mA and does the flashing itself.
        {"title": tr("Connect the radio board to Node Medic"),
         "body": tr("Plug the radio into Node Medic — or into the same USB hub, "
                    "either works — but NOT into the Pi. Node Medic powers and "
                    "flashes it here, where there's plenty of power, and "
                    "remembers which radio it is so the Pi finds it later."),
         "hint": tr("Attach the antenna first if you haven't — never power a "
                    "radio board without one."),
         "anim": "connect_board",
         "next": tr("Start setup  →")},
    ],
    "host": [
        # (The old second page — "Let's flash it", narrating its own button —
        # was removed like its radio/pi siblings; operator decision 2026-07-31.)
        {"title": tr("Connect the radio board"),
         "body": tr("Plug the radio board into Node Medic with a USB cable so it can be "
                    "flashed as an RNode."),
         "hint": tr("Use a DATA USB cable — a charge-only cable won't be seen."),
         "anim": "connect_board",
         "next": tr("Start setup  →")},
    ],
}


def guide_steps(path):
    """The ordered step dicts for a birth *path* (pure — unit-testable). Unknown
    paths return an empty list. Returns a copy so callers can't mutate the source."""
    return [dict(s) for s in _STEPS.get(path, [])]
