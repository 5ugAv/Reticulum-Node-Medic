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
    # NOT "through its pigtail cable". This screen is shown BEFORE detection, so
    # the medic does not know the board — and the catalogue is not all one kind:
    # the Heltec/RAK boards take a U.FL pigtail, while the T-Beam family carries
    # its SMA socket on the PCB and has no pigtail to look for. Stating one as
    # fact is the same bug ui.pi_connectors exists to prevent: wrong text reads
    # as authoritative, and an operator hunting a gold plug that isn't there
    # blames the tool.
    "body": tr("Fit a 915 MHz antenna (or one rated for your band) before Node "
               "Medic powers the board. On a pigtail BOTH ends count: press the "
               "gold U.FL / IPEX plug down until it clicks, and screw the "
               "antenna onto the SMA end."),
    "hint": tr("The U.FL / IPEX plug is fragile — connect it once and leave it."),
    "warning": tr("Never power a radio board with no antenna — transmitting "
                  "without one can permanently damage its radio."),
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
        # "Nothing is running yet — this page just explains what's coming" is
        # gone: narrating its own existence is exactly the filler the operator
        # struck out on 2026-07-31 ("Let's set it up", "Let's flash it").
        #
        # AND IT NO LONGER PROMISES "no manual web portal needed". That promise
        # holds only when the medic is itself on Wi-Fi: wifi_onboarding reads
        # the medic's own SSID/PSK to hand to the node, and with no SSID (or an
        # unreadable secret) the step FAILS and sends the operator to the
        # board's portal by hand (workflows/rtnode_build.py:291-305). In
        # Backpack mode, or anywhere in the field, that is the normal case — so
        # the precondition is stated instead of the outcome promised.
        {"title": tr("What happens next"),
         "body": tr("After flashing, Node Medic joins the node's own setup "
                    "Wi-Fi, sets its name and radio settings, and puts it on "
                    "the same Wi-Fi as Node Medic."),
         "hint": tr("Node Medic has to be on Wi-Fi for this. It briefly leaves "
                    "yours to talk to the node, then rejoins."),
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
        # RADIO FIRST. Reordered on the bench (operator, 2026-08-02): "we can,
        # because we're setting up the RNode through the Node Medic — we're not
        # plugging it into the Raspberry Pi anymore."
        #
        # Nothing forces the Pi first: the radio is flashed BY the medic, which
        # is also what removes the powered-hub problem during the build (a Pi
        # Zero can't reliably feed a Heltec V3 — 900 mA peak against a 500 mA
        # budget — but the medic has 1600 mA). Doing it first means the medic
        # knows exactly WHICH radio this is before it writes the Pi's card, so a
        # pairing that can't work is caught before the four-minute write instead
        # of after it. It also puts the antenna warning where the operator is
        # actually holding the radio.
        # "…and remembers which radio it is so the Pi finds it later" IS GONE,
        # because it was never true. Written on 2026-08-01 (fc1af07) for a flow
        # that no longer exists, it describes a mechanism this tree has never
        # had. What the medic actually remembers is a chip MAC -> board MODEL,
        # kept on the medic, so IT stops asking which board this is (see
        # _remember_board). Nothing about this radio's identity is written
        # onto the card or handed to the Pi: the Pi finds its radio by scanning
        # its OWN /dev/serial/by-id and taking the first RNode-looking device
        # (workflows/build.py detect_rnode_port). Plug a second radio into that
        # Pi and nothing the medic "remembered" would choose between them.
        {"title": tr("Connect the radio board to Node Medic"),
         "body": tr("Plug the radio into Node Medic — NOT into the Pi. Node "
                    "Medic flashes it here, where there is power to spare."),
         # NO HINT HERE. The antenna caution used to appear twice on this one
         # screen — as the amber warning box below, and again as a yellow hint
         # in almost the same words. Two identical alarms side by side teach the
         # operator to skim both, which is the opposite of what a warning is
         # for. One warning, once, in the heavier style.
         # WORD FOR WORD the antenna landing's warning. It was a near-copy —
         # same danger, two wordings — which is one string too many to keep
         # true in eight languages, and a warning the operator has to re-read
         # to check it is the same warning.
         "warning": ANTENNA_STEP["warning"],
         "anim": "connect_board",
         # THE GATE. Finish the radio before anything else starts.
         #
         # This step used to say "plug it in" and let the operator walk straight
         # past. On 2026-08-08 they did: the Heltec V4 on the bench was
         # boot-looping every 2.4 seconds — its bootloader written with
         # --flash_size keep — and the flow happily went on to write TWO SD cards
         # and spend an evening diagnosing a Pi, while the dead radio sat on the
         # bus re-enumerating 97 times.
         #
         # The radio is the cheapest thing to test and the most likely to be
         # broken, so it goes first AND it has to pass. A four-minute card write
         # for a radio that cannot work is wasted; worse, its failure arrives
         # later, attached to the wrong suspect.
         #
         # THE RADIO IS FINISHED HERE. This step hands off to the BIRTH screen,
         # which flashes and verifies it, and hands back with the VERIFY verdict
         # (birth_screen._hand_back_to_guide). Only then does the walkthrough
         # continue — the gate below reads that verdict.
         # WHICH JOB the BIRTH screen is being sent to do. The Pi path hands
         # off to that screen twice, for completely different reasons — flash a
         # radio here, provision the finished node at the end — and a hand-off
         # that could not say which one always scoped to the first.
         "next": tr("Flash this radio  →"), "screen": "birth", "job": "host"},
        # THE GATE. Nothing past this point starts until the radio has passed.
        #
        # Its own step, so the refusal has somewhere to be SEEN. Attaching the
        # gate to the step above would re-render the "plug the radio in"
        # instructions on every refusal, burying the reason in text the operator
        # has already read.
        {"title": tr("The radio has to work first"),
         "body": tr("Node Medic flashes the radio and waits for it to report "
                    "back. Nothing else starts until it does."),
         "hint": tr("If it failed, try another USB port or a fresh cable before "
                    "trying another board."),
         # NO ANIMATION. This step is a VERDICT, not an action — there is nothing
         # for the operator to do with their hands. It used to loop
         # connect_board, a picture of a board descending onto Node Medic, while
         # the board in question was already plugged in and being judged. Same
         # fault as the one fixed for "take the radio out": the picture showed a
         # different act from the words, and a picture reads as authoritative.
         "anim": None,
         "gate": "radio_ready"},
        # AND THEN TAKE IT OFF. Asked for explicitly on 2026-08-09: the operator
        # reached the Pi steps with the radio still on the medic and was never
        # told to remove it.
        #
        # It is not tidiness. The radio's job is done, it draws current the Pi is
        # about to want, and a board left plugged in keeps re-enumerating on the
        # same bus the medic is trying to watch for the Pi — on 2026-08-08 a
        # boot-looping V4 added 97 USB events to exactly that window. Its next
        # appearance should be on the PI, at the end.
        {"title": tr("Take the radio out of Node Medic"),
         "body": tr("The radio is done. Unplug it and set it aside — it goes "
                    "on the Pi at the very end. Left here it draws power the "
                    "Pi is about to need."),
         "anim": "disconnect_board"},
        # ONE ROUTE FOR EVERY BOARD — the medic's own card reader (operator
        # decision, 2026-08-06). This replaced "card into the Pi, Pi becomes its
        # own card reader" (rpiboot), which was clever and board-dependent in a
        # way the operator could not see: a Pi 3A+ can NEVER do it, because its
        # OTG ID signal is hardwired to 0V (permanent host mode), so it cannot
        # present itself as a USB device at all. Verified live — powered, LED
        # on, never appeared on the medic's USB. And it was circular: the only
        # override lives on a card that has not been written yet.
        #
        # The old route also failed SILENTLY and identically to a bad cable,
        # which is the worst possible failure for a field tool. One uniform
        # route is teachable, demonstrable, and behaves the same everywhere.
        {"title": tr("Put the SD card into Node Medic"),
         "body": tr("Slide the blank microSD card into the card reader on Node "
                    "Medic. The whole system is written and checked here, "
                    "before you carry the card away."),
         "anim": "insert_sd",
         "next": tr("Write the card  \u2192"), "screen": "pi_imager"},
        {"title": tr("Move the card to the Raspberry Pi"),
         "body": tr("The card is ready. Take it out of the reader and slide it "
                    "into the Pi's own card slot."),
         # BOARD-AWARE, and it earns the change. The card leaves the medic's
         # reader, crosses, and enters the slot WHERE THAT MODEL'S SLOT ACTUALLY
         # IS — measured per board, not assumed from one of them.
         #
         # It also turns over in mid-air on the four boards whose slot is on the
         # UNDERSIDE (3A+/3B+/4B/5), because those take the card contacts-up.
         # Showing a label-up card sliding into a top-view Pi 4 would be
         # depicting the operator doing it wrong — and this step is the one most
         # likely to be got wrong. A Zero 2 W never flips, so the difference
         # between the two families is visible in the animation itself.
         "anim": "sd_handover"},
        # THE CARD FIRST, IN THE TITLE. The step before this one moves the card
        # into the Pi, and the picture here shows it seated — but on a 3 A+ the
        # slot is on the UNDERSIDE, so the drawing cannot show it at all. A
        # forgotten card presents as a Pi that powers up and never appears,
        # which is the same symptom as a charge-only cable and a dead gadget:
        # three suspects, one of them free to rule out (operator, 2026-08-10,
        # asked for it in the title).
        {"title": tr("Check the SD card is in the Pi, then connect it to "
                     "Node Medic"),
         # AND SAY HOW LONG IT TAKES. The step advances itself, so there is
         # nothing to press — which from the operator's side is indistinguishable
         # from a screen that has hung (asked for outright, 2026-08-10: "user
         # should also be told at this stage that medic will take up to 60
         # seconds to recognise attached pi, please wait"). A Pi answers in
         # 30-45 s from power; a first boot expands the card and takes longer.
         # "no Wi-Fi and no network setup anywhere in this build" IS GONE. It
         # was false, and false on the screen straight AFTER the one that asks
         # for Wi-Fi: the imager pre-fills the medic's own SSID and PSK from
         # nmcli and bakes a NetworkManager connection onto the card
         # (ui/screens/pi_imager_screen.py:497-504, provisioning/pi_imager.py
         # card_cfg["wifi_ssid"]). The cable is not even the preferred road \u2014
         # when both answer, addresses_for() sorts 10.55.0.* LAST, because the
         # network one survives the operator walking away. SkyFinger was
         # provisioned over Wi-Fi on 2026-08-11 and the operator caught the
         # closing screen claiming otherwise; the same claim was still standing
         # here, two screens earlier.
         "body": tr("Plug the Pi into Node Medic with a USB cable and let it "
                    "boot from the card you just wrote.\n\nNode Medic can take "
                    "up to a minute to see it, and longer on a card's first "
                    "boot. There is nothing to press \u2014 this moves on by "
                    "itself the moment the Pi answers."),
         # EARNED THE HARD WAY, 2026-08-06: three separate faults in one bench
         # session were cables, and every one first looked like a software bug.
         # A charge-only lead powers the Pi perfectly and never appears.
         "hint": tr("Use the Pi's DATA port, and a cable that carries DATA \u2014 a "
                    "charge-only lead will power the Pi perfectly and never "
                    "show up here. On a Pi Zero it's the inner micro-USB, "
                    "nearer the mini-HDMI; the outer one is PWR IN and cannot "
                    "carry data. On a Pi 3A+ it's the full-size USB-A socket "
                    "\u2014 its micro-USB is power only. Use a short, thick cable "
                    "\u2014 a thin or coiled one drops the link."),
         "anim": "connect_pi"},
        # THE HAND-OFF, AND THE ONLY STEP THAT MAKES THIS A NODE.
        #
        # It has never existed. Task #40 has carried the same line since
        # 2026-08-01: "the physical hand-off (radio onto the Pi) has not been
        # done yet, so the radio has never been exercised FROM the Pi." Every
        # birth in this project has ended with two working halves on a bench and
        # nothing joining them \u2014 because a hand-off could not return, so there
        # was nowhere to put a step that came after one.
        #
        # The radio was flashed on the MEDIC, where there is power to spare. It
        # comes back out (three steps ago) and lands here, on the Pi, which is
        # where it lives for the rest of its life.
        # ORDER FIXED 2026-08-09, mid-walkthrough, by the operator holding the
        # hardware: "we have just been instructed to use the usb plug on pi to
        # attach radio so pi is not currently connected to medic."
        #
        # A Pi 3 A+ has ONE USB-A socket. The step before this one puts it into
        # Node Medic; putting the radio on the Pi needs that same socket. So the
        # walkthrough asked for the cable to be pulled and then, on the next
        # screen, waited for the Pi over that cable. A Zero 2 W has the same
        # problem with its single data micro-USB. The flow could not be
        # completed on either board, however carefully it was followed.
        #
        # Provisioning happens over the cable, so it goes FIRST. The radio goes
        # on at the very END — which is also when the Pi leaves Node Medic and
        # takes its own power, so the power question answers itself instead of
        # colliding with a back-feeding A-to-A.
        # THE STEP THAT ACTUALLY MAKES IT A NODE.
        #
        # Until this existed the walkthrough's last button — "That's the node
        # built" — called _finish(), which handed straight back to the BIRTH
        # form scoped to "Pi + RNode" and started board detection. So the final
        # act of the walkthrough was to ask for the thing it had just finished;
        # and because the radio is on the PI by then, that detection reported
        # "No work board on the medic's USB". The flow ended by demanding its
        # own output and then failing (audit, 2026-08-09).
        #
        # Two working halves joined by a cable are not yet a node. The Pi is
        # running stock Raspberry Pi OS: no Reticulum, no LXMF, no health
        # reporter, no certificate. All of that is installed OVER THE CABLE, by
        # the same BuildWorkflow every other birth uses — which is why this is a
        # hand-off and not a new mechanism.
        #
        # The gate is what makes it safe: provisioning cannot start until the
        # medic has actually reached the node at the other end of the cable.
        # NOT "over the cable", flatly. The medic PROBES every road the node
        # answers on and takes the first that opens SSH, and when both are up
        # the Wi-Fi address WINS — addresses_for() sorts 10.55.0.* last on
        # purpose (provisioning/pi_discover.py; tests/test_birth_guide.py::
        # test_the_cable_address_is_tried_last_among_equals). "Over the cable"
        # is the same sentence the operator caught on the closing screen on
        # 2026-08-11 — "the text should never mislead the user" — and it was
        # only fixed there. Also drops "the two halves are joined", which is
        # wrong on its own terms: the radio came off the medic three steps ago
        # and does not go onto the Pi until the step AFTER this one.
        {"title": tr("Bring the node to life"),
         "body": tr("The Pi is still just a Raspberry Pi. Node Medic reaches "
                    "it — over the cable, or by name on your Wi-Fi — installs "
                    "the mesh software and issues its birth certificate."),
         "hint": tr("Nothing to unplug. Leave the Pi on Node Medic until this "
                    "finishes."),
         "gate": "node_online",
         "screen": "birth",
         "job": "pi",
         # ONLY EVER A RETRY. The operator already consented on the previous
         # screen — "That's the node built →" — and this one says exactly what
         # it is about to do, so making them press again is asking twice
         # (operator, 2026-08-09: "wake it up is unnecessary, we just clicked a
         # button to get here that could lead straight to wake up. if no node is
         # detected then a button can appear saying try again"). It advances
         # itself the moment the node answers; the button exists for the case
         # where it does not.
         "next": tr("Try again  →"),
         # A PI ON A CABLE, not a radio board broadcasting. "provision"
         # draws radio waves, which is exactly what this step is not
         # (operator, reading it off the screen, 2026-08-09).
         "anim": "provision_cable"},
        {"title": tr("Unplug the Pi, put the radio on it, give it power"),
         "body": tr("The software is on. Unplug the Pi from Node Medic \u2014 the "
                    "radio needs that socket \u2014 plug in the radio you flashed "
                    "at the start, and give the Pi its own power. From now on "
                    "it talks to the mesh through this radio, on its own."),
         "hint": tr("A DATA cable to the radio, and keep it short \u2014 the Pi "
                    "powers the radio now, so its supply carries both."),
         # A BUTTON, unlike the connect-to-medic steps. Those hide Next because
         # the medic senses the board itself. It cannot sense this one: the radio
         # is on the PI now, on the Pi's USB, not the medic's. Hiding Next here
         # would leave the last step of the walkthrough with no way to finish.
         "next": tr("That's the node built  →"),
         "anim": "radio_to_pi"},
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


def guide_steps(path, pi_key=""):
    """The ordered step dicts for a birth *path* (pure — unit-testable). Unknown
    paths return an empty list. Returns a copy so callers can't mutate the source.

    *pi_key* makes the connector wording follow the board the operator actually
    picked. Without it every Pi got one hint written around a Pi Zero, so a 3A+
    operator was told to find "the inner micro-USB, nearer the mini-HDMI" on a
    board that has no data micro-USB at all (operator, 2026-08-06). One string
    for every board is the same bug as one photo for every board — and worse in
    one way, because wrong text reads as authoritative while a wrong photo just
    looks wrong.

    Empty pi_key keeps the generic line. Naming a specific socket on a board we
    have not identified is precisely the failure being fixed.
    """
    from ui.pi_connectors import (connect_hint, power_hint,
                                  standalone_power_hint)
    steps = [dict(s) for s in _STEPS.get(path, [])]
    if pi_key:
        for s in steps:
            if s.get("anim") == "connect_pi":
                s["hint"] = connect_hint(pi_key)
            # POWER, on the step that spends it. Provisioning is the longest and
            # hungriest thing a node does on the cable, and the medic has
            # already recorded undervoltage on this bench. Where a board CAN
            # take its own supply it should — but on a 3 A+ over an ordinary
            # A-to-A a second supply back-feeds into the medic, so this is a
            # per-board line and never one sentence for all of them (operator
            # asked for it, 2026-08-09; the answer differs by board).
            # Both of the steps where power actually matters: the one that
            # hangs the radio off the Pi (its load appears here) and the one
            # that spends minutes installing. Operator asked for it on both
            # (2026-08-09) — and on a 3 A+ the honest answer at both is the
            # same warning about a second supply, not an instruction to add one.
            if s.get("gate") == "node_online":
                s["hint"] = power_hint(pi_key) + " " + s.get("hint", "")
            # THE LAST STEP ASKS THE OPPOSITE QUESTION. Both steps are about
            # power, so both got the same sentence — but by this one the Pi has
            # been unplugged from Node Medic, and power_hint's 3 A+ line ("do
            # NOT plug a supply into its micro-USB") then contradicts the
            # instruction directly above it. The back-feed it guards against
            # cannot happen with the medic's cable gone.
            elif s.get("anim") == "radio_to_pi":
                s["hint"] = standalone_power_hint(pi_key) + " " + s.get("hint", "")
    return steps


def paths_for_chip(chip, rtnode_capable):
    """Which build paths to offer for a board whose chip reads *chip*.

    Returns ``(paths, why_missing)``. Pure so the rule is testable without a
    display. *rtnode_capable* is ``firmware_options``-style: given the chip, does
    it list rtnode2400?

    Fail-open by design: an unreadable chip offers everything. A wrong exclusion
    here blocks a build the operator legitimately wants, which is worse than one
    extra choice.
    """
    if not chip:
        return list(BIRTH_PATHS), ""
    if rtnode_capable(chip):
        return list(BIRTH_PATHS), ""
    return [p for p in BIRTH_PATHS if p[0] != "radio"], "rtnode-needs-s3"
