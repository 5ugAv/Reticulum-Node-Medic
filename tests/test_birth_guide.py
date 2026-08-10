"""Guided-birth step ordering — pure data, no Kivy (CI has no Kivy installed)."""

from ui.birth_guide_flow import guide_steps, BIRTH_PATHS, _STEPS, ANTENNA_STEP


def test_three_intro_paths_in_order():
    # ordered by rising complexity: RNode -> RTNode-2400 -> Pi + radio
    assert [p[0] for p in BIRTH_PATHS] == ["host", "radio", "pi"]
    for _key, title, subtitle in BIRTH_PATHS:
        assert title and subtitle


def test_step_counts_per_path():
    # The old final 'Let's set it up' filler page (a narration of what the
    # button was about to do) was removed — operator decision 2026-07-31; the
    # previous page now carries the Start-setup handoff.
    assert len(guide_steps("radio")) == 2      # connect -> what-happens-next/setup
    assert len(guide_steps("host")) == 1       # connect page carries Start setup
    # The Pi path was rebuilt 2026-08-09 to the operator's order: the radio is
    # FINISHED first and must pass before anything else starts, then it comes
    # off the medic, and it goes onto the Pi at the very end.
    assert [s["title"] for s in guide_steps("pi")] == [
        "Connect the radio board to Node Medic",   # -> BIRTH, flashes + verifies
        "The radio has to work first",             # GATE: radio_ready
        "Take the radio out of Node Medic",
        "Put the SD card into Node Medic",         # -> pi_imager
        "Move the card to the Raspberry Pi",
        "Connect the Pi to Node Medic",
        "Bring the node to life",                  # GATE: node_online -> BIRTH
        "Unplug the Pi, put the radio on it, give it power",
    ]


def test_the_radio_is_finished_before_anything_else_begins():
    """The operator's order, 2026-08-09: "recognise radio board — flash as
    rnode (finish this first) — then image sd card".

    The radio is the cheapest thing to test and the likeliest to be broken. On
    2026-08-08 a boot-looping Heltec V4 was walked straight past, two cards were
    written, and an evening went on diagnosing a Pi while the dead radio
    re-enumerated 97 times on the same bus.
    """
    steps = guide_steps("pi")
    titles = [s["title"] for s in steps]
    radio = titles.index("Connect the radio board to Node Medic")
    gate = next(i for i, s in enumerate(steps) if s.get("gate") == "radio_ready")
    card = titles.index("Put the SD card into Node Medic")
    assert radio < gate < card, "the gate must stand between the radio and the card"
    assert steps[radio].get("screen") == "birth", \
        "the radio step must actually hand off to the flash, not just say 'plug it in'"


def test_the_operator_is_told_to_take_the_radio_back_off():
    """Reported 2026-08-09: reached the Pi steps with the radio still plugged
    into the medic and was never told to remove it. Not tidiness — it draws
    current the Pi is about to want, and a board left on the bus keeps
    re-enumerating in the window the medic is watching for the Pi."""
    steps = guide_steps("pi")
    titles = [s["title"] for s in steps]
    out = titles.index("Take the radio out of Node Medic")
    assert out < titles.index("Put the SD card into Node Medic")
    assert out < titles.index("Connect the Pi to Node Medic")


def test_the_walkthrough_ends_by_joining_the_two_halves():
    """#40 has said since 2026-08-01: "the radio has never been exercised FROM
    the Pi". Every birth ended with two working halves and nothing joining
    them, because a hand-off could not return so nothing could come after one."""
    steps = guide_steps("pi")
    titles = [s["title"] for s in steps]
    assert any("put the radio on it" in t for t in titles)


def test_joining_the_halves_is_not_the_end_of_it():
    """Two working halves and a cable are not a node: the Pi is still running
    stock Raspberry Pi OS. The mesh software is installed OVER that cable, and
    the walkthrough is not finished until it has been."""
    steps = guide_steps("pi")
    life = next(s for s in steps if s.get("gate") == "node_online")
    assert life["title"] == "Bring the node to life"
    assert life.get("screen") == "birth", "the existing build flow does the work"
    assert life.get("job") == "pi", \
        "sent to BIRTH to provision the node, NOT to flash a radio"


def test_the_radio_goes_on_the_pi_only_after_the_cable_is_finished_with():
    """A Pi 3 A+ has ONE USB-A socket, and a Zero 2 W one data micro-USB. The
    cable to Node Medic and the radio want the SAME hole. Asking for the radio
    before provisioning made the flow impossible to complete on either board
    (operator, holding the hardware, 2026-08-09) — and the next screen then
    waited for the Pi over the cable it had just had them pull."""
    steps = guide_steps("pi")
    titles = [s["title"] for s in steps]
    life = titles.index("Bring the node to life")
    radio = next(i for i, t in enumerate(titles) if "put the radio on it" in t)
    assert life < radio, "provision over the cable BEFORE the socket is taken"
    assert radio == len(steps) - 1, "the radio going on is the last act"
    assert "Unplug the Pi" in titles[radio], "say the socket is being freed"
    assert "power" in steps[radio]["body"].lower(), \
        "off the medic means it needs its own supply — say so here"


def test_each_birth_handoff_declares_its_own_job():
    """The Pi path hands off to the BIRTH screen twice for opposite reasons.
    Hard-coded to one of them, the final step scoped the screen to a radio
    flash and then hunted for a radio that was, by then, on the Pi."""
    jobs = [s.get("job") for s in guide_steps("pi") if s.get("screen") == "birth"]
    assert jobs == ["host", "pi"]


def test_the_pi_walkthrough_does_not_end_on_a_form():
    """Its last button used to call _on_complete, which handed straight back to
    the BIRTH form scoped to build a Pi + RNode — asking for the thing that had
    just been built, and failing, because the radio was on the Pi by then."""
    from tests.srcutil import func_source
    src = func_source("ui/screens/birth_guide_screen.py", "_finish")
    body = src.split('"""')[2]          # past the docstring, which names both
    assert 'path == "pi"' in body and "_render_done" in body
    assert body.index('path == "pi"') < body.index("_on_complete"), \
        "the Pi path must return before the intro-path hand-off"


def test_antenna_is_the_landing_not_a_guided_step():
    # the antenna step is the first BIRTH screen (a landing), NOT inside guide_steps
    for path in ("radio", "pi", "host"):
        assert all(s.get("anim") != "connect_antenna" for s in guide_steps(path))
    # and it carries the damage warning + both connector types
    assert ANTENNA_STEP.get("warning")
    assert ANTENNA_STEP["anim"] == "connect_antenna"
    assert "U.FL" in ANTENNA_STEP["body"] and "SMA" in ANTENNA_STEP["body"]


def test_unknown_path_is_empty():
    assert guide_steps("nonsense") == []


def test_every_step_has_title_and_body():
    for steps in _STEPS.values():
        for s in steps:
            assert s["title"].strip()
            assert s["body"].strip()


def test_pi_path_does_the_RADIO_before_the_card():
    """Radio first, then the Pi's card (operator, 2026-08-02).

    Nothing forces the Pi first — the radio is flashed BY the medic, never
    through the Pi. Doing it first means the medic knows exactly which radio
    this is BEFORE it spends four minutes writing the Pi's card, so a pairing
    that cannot work (a Pi Zero cannot feed a Heltec V4) is caught while it
    still costs nothing to change.
    """
    titles = [s["title"] for s in guide_steps("pi")]
    i_radio = [i for i, t in enumerate(titles) if "radio board" in t.lower()][0]
    i_card = [i for i, t in enumerate(titles) if "SD card" in t][0]
    assert i_radio < i_card, titles


# --- the CABLE birth (proven 2026-08-01, HOPE) -----------------------------
# These steps described a different flow until then: card into a USB reader,
# radio onto the Pi, network address typed in. Every one of those was wrong.

def test_the_card_goes_into_the_MEDICS_READER_not_into_the_pi():
    """REVERSED 2026-08-06 by operator decision. The old rule was "the Pi is its
    own card reader" (rpiboot), which read as kindness but was board-dependent
    in a way the operator could not see: a Pi 3A+ has its OTG ID hardwired to
    0V, so it can NEVER present itself as a USB device, and the step failed
    silently and identically to a bad cable. One uniform route instead."""
    card = [s for s in guide_steps("pi") if "SD card" in s["title"]][0]
    assert "into Node Medic" in card["title"]
    assert "card reader on Node Medic" in card["body"]
    assert card.get("screen") == "pi_imager", "writing happens from this step"


def test_the_radio_goes_on_the_MEDIC_not_on_the_pi():
    """This is what removes the powered-hub problem: a Pi Zero cannot reliably
    feed a Heltec V3, but the medic can, and it does the flashing."""
    radio = [s for s in guide_steps("pi") if "radio" in s["title"].lower()][0]
    assert "Node Medic" in radio["title"]
    # A hub counts as Node Medic's side — the medic identifies boards by USB
    # serial, not by which port they hang off (verified: nothing in the tree
    # keys on bus/port). What must never happen is the radio going on the Pi.
    assert "not into the pi" in radio["body"].lower()


def test_no_replug_is_needed_because_the_card_is_written_first():
    """The old flow imaged the card INSIDE the Pi, so the Pi had to be power
    cycled to boot what had just been written — and the medic cannot do that
    itself (uhubctl on the Pi 5 root hub does not cut VBUS; measured). Writing
    the card before it goes near the Pi removes the step, and with it the
    commonest place for the flow to silently stall."""
    titles = [s["title"] for s in guide_steps("pi")]
    assert not any("Restart" in t for t in titles), titles
    idx = {t: i for i, t in enumerate(titles)}
    i_write = [i for i, st in enumerate(guide_steps("pi"))
               if st.get("screen") == "pi_imager"][0]
    assert i_write < idx["Move the card to the Raspberry Pi"] < idx["Connect the Pi to Node Medic"]


def test_the_data_port_trap_is_called_out_where_it_happens():
    """A Zero has two identical micro-USB sockets and only one carries data.
    This cost an hour on the bench, to the person who designed the flow."""
    connect = [s for s in guide_steps("pi") if "Connect the Pi" in s["title"]][0]
    hint = connect["hint"]
    assert "DATA port" in hint and "PWR IN" in hint
    assert "mini-HDMI" in hint, "must say WHICH socket, not just 'the data one'"
    assert "coiled" in hint, "a thin/coiled cable drops the link - measured"


def test_no_step_asks_for_a_network_address_or_wifi():
    """The medic reaches the Pi over the USB cable. Asking a field operator for
    an IP address was the original complaint that started this whole path."""
    joined = " ".join(s["title"] + " " + s["body"] + " " + s.get("hint", "")
                      for s in guide_steps("pi")).lower()
    for phrase in ("ip address", "hostname of the pi", "wi-fi password",
                   "wifi password", "join your wi-fi"):
        assert phrase not in joined, f"still asks for {phrase!r}"


#: Animations the wizard advances itself on, by watching USB. A step using one
#: needs no button and deliberately has none (operator, 2026-08-02).
_SELF_ADVANCING = {"connect_pi", "connect_board"}


def test_last_step_hands_off_to_setup():
    """Every path must END somewhere — by a labelled button, or by detection.

    The Pi path now finishes on "Restart the Pi", which the medic detects and
    carries forward on its own, so it correctly has no button.
    """
    for path in ("radio", "pi", "host"):
        last = guide_steps(path)[-1]
        assert (last.get("next", "").strip()
                or last.get("anim") in _SELF_ADVANCING), path


def test_guide_steps_returns_a_copy():
    a = guide_steps("radio")
    a.append({"title": "x", "body": "y"})
    assert len(guide_steps("radio")) == 2          # internal list untouched


def test_the_pi_step_uses_the_pi_animation_not_the_radio_one():
    """Walkthrough 2026-08-02: the step said 'Connect the Pi' while showing a
    radio board sliding into the medic."""
    connect = [s for s in guide_steps("pi") if "Connect the Pi" in s["title"]][0]
    assert connect["anim"] == "connect_pi"


def test_the_pi_connect_step_is_not_detected_by_serial_port_polling():
    """The bug that stalled the first walkthrough: a Pi in boot-ROM mode is not
    a serial device at all, so local_board_ports() can never see one and the
    step sat there telling the operator to connect an already-connected Pi."""
    src = open("ui/screens/birth_guide_screen.py").read()
    assert "_start_pi_poll" in src
    assert "pi_usbboot" in src
    # the Pi branch must be chosen BEFORE the generic serial-board branch,
    # since ConnectPiAnim subclasses ConnectBoardAnim
    i_pi = src.index("isinstance(anim, ConnectPiAnim)")
    i_board = src.index("isinstance(anim, ConnectBoardAnim)")
    assert i_pi < i_board, "the Pi branch is unreachable behind its own base class"


def test_no_pi_step_uses_the_radio_board_animation_except_the_radio_step():
    for s in guide_steps("pi"):
        if "radio" in s["title"].lower():
            continue
        assert s.get("anim") != "connect_board", f"{s['title']} shows a radio board"


def test_detect_finds_a_pi_so_choose_manually_is_not_required():
    """'Choose manually' is the ADVANCED escape. Needing it to build a Pi — the
    commonest node — made the primary path the fallback."""
    src = open("ui/screens/birth_guide_screen.py").read()
    assert "_start_detect_pi_poll" in src and "_on_pi_detected" in src


def test_the_pi_poll_is_stopped_when_the_step_changes():
    """Left running it fires _on_pi_detected forever and drags the operator back
    to the name screen from wherever they got to."""
    from tests.srcutil import func_source
    body = func_source("ui/screens/birth_guide_screen.py", "_stop_current")
    assert "_stop_detect_pi_poll" in body
    assert "_stop_board_poll" in body
    # and it must cancel pending renders, or a scheduled "Reading the board…"
    # lands on top of wherever the operator navigated to
    assert "_nav_token" in body


def test_a_pi_build_does_not_open_with_an_antenna_instruction():
    """A Pi has no antenna and no radio attached yet. Opening with 'attach the
    antenna' is an instruction about a board the operator isn't holding."""
    src = open("ui/screens/birth_guide_screen.py").read()
    assert "_pi_present_without_radio" in src


def test_back_and_forward_share_one_notion_of_redundant():
    """Back must never land on a step the forward path would skip.

    While the radio stayed plugged in, Back decremented onto the 'connect the
    radio' step, which the renderer then skipped forward again — so the button
    did nothing at all (operator, 2026-08-02). Both directions now consult
    _step_is_redundant, and Back walks past anything it reports.
    """
    import pathlib
    src = (pathlib.Path(__file__).resolve().parent.parent
           / "ui" / "screens" / "birth_guide_screen.py").read_text()
    assert "def _step_is_redundant" in src
    back = src[src.index("    def _back(self):"):]
    back = back[:back.index("\n    def ", 10)]
    assert "_step_is_redundant" in back, "Back does not skip redundant steps"
    assert "while" in back, "Back must walk past a RUN of redundant steps"
    fwd = src[src.index("    def _render_step(self):"):]
    fwd = fwd[:fwd.index("\n    def ", 10)]
    assert "_step_is_redundant" in fwd, "forward skip stopped sharing the rule"


# --- the 2026-08-03 audit findings, pinned --------------------------------

def test_the_pairing_gate_is_keyed_on_the_path_not_on_the_step_index():
    """It must fire even though step 0 auto-skips.

    The gate first lived in _next() keyed on `self._i == 0`. But _render_step
    advances _i past redundant steps BEFORE rendering, and step 0 of the Pi
    path is "connect the radio" — redundant precisely because the radio IS
    plugged in by then. So _i was always 1 when _next() looked, the gate never
    fired once, and the operator reached the four-minute card write with no
    power-compatibility check at all.
    """
    from tests.srcutil import func_source
    render = func_source("ui/screens/birth_guide_screen.py", "_render_step")
    assert "_pair_checked" in render, "gate is not in _render_step"
    assert 'self._path == "pi"' in render, "gate must key on the path"
    nxt = func_source("ui/screens/birth_guide_screen.py", "_next")
    assert "_render_pick_board" not in nxt, \
        "gate is back in _next(), where the skip logic outruns it"


def test_the_board_detection_result_is_kept_for_the_picker():
    """_board_candidates reads self._detected; nothing assigned it, so the
    picker listed the whole ~15-board catalogue under copy saying it had been
    narrowed."""
    from tests.srcutil import src
    text = src("ui/screens/birth_guide_screen.py")
    assert "self._detected = det" in text, \
        "detection result discarded — the picker cannot narrow"


def test_every_cross_walkthrough_flag_is_cleared_on_reset():
    """Flags that outlive one walkthrough break the NEXT one.

    _reading_pending stuck True killed the Pi auto-detect for the rest of the
    session; _pi_art_key stuck gave the second Pi the first Pi's portrait.
    """
    from tests.srcutil import func_source
    reset = func_source("ui/screens/birth_guide_screen.py", "reset")
    for flag in ("_pair_checked", "_board_key", "_pi_key",
                 "_reading_pending", "_pi_art_key"):
        assert flag in reset, f"{flag} survives reset() into the next build"


def test_back_from_the_board_pick_reaches_the_name_step():
    """It pointed at _render_step_zero, which sets _i=0 and re-renders — and
    the redundant radio step then skips straight to step 1. Back moved the
    operator FORWARDS, into a closed two-screen loop."""
    from tests.srcutil import func_source
    pick = func_source("ui/screens/birth_guide_screen.py", "_render_pick_board")
    assert "= self._render_step_zero" not in pick, "Back still loops forward"
    assert "self._back_action = self._render_name" in pick


# --- ONE birth route: the medic's own card reader --------------------------
# Operator decision, 2026-08-06, after a Pi 3A+ birth stalled on a step that
# board physically cannot perform: "make all births the uniform process...
# using the SD card reader in the medic instead of through the pi".

def test_the_pi_route_writes_the_card_in_the_medics_reader():
    """Pi-as-card-reader (rpiboot) is OFF the birth path. It cannot work on a
    Pi 3A+ at all — that board's OTG ID is hardwired to 0V, so it can never
    present itself as a USB device — and it failed silently and identically to
    a bad cable, which is the worst failure mode for a field tool."""
    steps = guide_steps("pi")
    titles = " | ".join(s["title"] for s in steps)
    bodies = " ".join(s.get("body", "") for s in steps)

    assert "card reader on Node Medic" in bodies, "card must go in the MEDIC's reader"
    assert "Put the SD card into Node Medic" in titles
    # and the old route's promise must be gone
    assert "the Pi will hand its card to Node Medic" not in bodies
    assert "You don't need a card reader" not in bodies


def test_the_card_is_written_before_it_reaches_the_pi():
    """Order matters: write, THEN move it. The old flow put the card in the Pi
    first and imaged through it."""
    steps = guide_steps("pi")
    idx = {s["title"]: i for i, s in enumerate(steps)}
    write = idx["Put the SD card into Node Medic"]
    move = idx["Move the card to the Raspberry Pi"]
    connect = idx["Connect the Pi to Node Medic"]
    assert write < move < connect, "must write, then move, then connect"
    # the imager is opened from the WRITE step, not from a Pi-connected step
    assert steps[write].get("screen") == "pi_imager"


def test_there_is_no_replug_step_any_more():
    """With the card written before it goes in, the Pi boots from it first
    time. The old 'Restart the Pi' step existed only because the card was
    imaged while inside the Pi."""
    titles = [s["title"] for s in guide_steps("pi")]
    assert "Restart the Pi" not in titles


def test_the_cable_hint_names_the_data_trap_and_both_boards():
    """Three separate faults in one bench session (2026-08-06) were cables, and
    every one first presented as a software bug. A charge-only lead powers a Pi
    perfectly and never enumerates — the operator has no way to tell by eye."""
    hints = " ".join(s.get("hint", "") for s in guide_steps("pi"))
    assert "DATA" in hints and "charge-only" in hints
    assert "Pi Zero" in hints and "mini-HDMI" in hints      # inner vs PWR IN
    assert "3A+" in hints and "USB-A" in hints              # its micro-USB is power only


def test_the_card_step_watches_for_the_card_and_greets_it():
    """Operator, looking straight at the step, 2026-08-07: "should I be getting
    the green circles when I plug an sd card in reader into medic here?" The
    answer was no — only the imager screen was watching, so a step that says
    "Put the SD card into Node Medic" sat there saying nothing. Same complaint
    as #71: the medic visibly not knowing what is plugged into it.
    """
    src = open("ui/screens/birth_guide_screen.py").read()
    assert "InsertSdAnim)" in src and "_start_card_poll" in src
    seen = src[src.index("def _on_card_seen"):src.index("def _pi_key_for_art")]
    assert "mark_card_found" in seen, "must fire the same burst a board gets"
    assert "_card_greeted" in seen, "poll repeats; greet once"


def test_the_card_poll_dies_with_the_step():
    """A poll outliving its screen is a real bug in this file's history — the
    Pi poll used to yank the operator back to the name screen from wherever
    they had got to."""
    src = open("ui/screens/birth_guide_screen.py").read()
    i = src.index("def _stop_current")
    stop = src[i:i + 1800]          # the body, wherever it sits in the file
    assert "_stop_card_poll" in stop


def test_seeing_a_card_does_not_write():
    """Seeing a card may ADVANCE, but must never WRITE.

    This guard used to forbid advancing too, on the reasoning that "this step is
    followed by a DESTRUCTIVE write, so the deliberate press stays". The
    operator changed the design live on the bench (2026-08-08): once the medic
    can see a card there is nothing left for them to decide on that step, and a
    green "Write the card →" button sitting under a finished animation reads as
    the tool waiting on them.

    The safety property is UNCHANGED, and that is why the change is allowed: the
    step advances to the imager screen, which asks. That screen is explicitly
    "auto-DETECT, not auto-WRITE", and its write is bound to a popup button
    (``on_release ... self._write(v)``). The deliberate press still exists — it
    now lives on the screen that actually names what is about to be destroyed,
    which is the better place for it.

    So this guards what still matters: no write, flash or confirm may be
    triggered by merely seeing a card."""
    src = open("ui/screens/birth_guide_screen.py").read()
    block = src[src.index("elif isinstance(anim, InsertSdAnim):"):]
    block = block[:block.index("\n\n")]
    # The green "Write the card →" button is GONE — asked for twice, 2026-08-08
    # and again 2026-08-09. It was kept the first time as an escape hatch for a
    # card the medic fails to see; that was wrong, because a button whose only
    # purpose is a failure mode still reads on every successful run as "the tool
    # is waiting for you", under a finished animation. Back covers the failure.
    assert "hide_next" in block, "the green Write-the-card button must not return"
    seen = src[src.index("def _on_card_seen"):src.index("def _pi_key_for_art")]
    for destructive in ("flash(", "_confirm(", "_write("):
        assert destructive not in seen, f"{destructive!r} fires on merely seeing a card"


def test_the_card_auto_advance_cannot_outrun_the_operator():
    """The advance is delayed past the ripple, so a manual tap can land first.
    It must lose that race rather than skip a step nobody saw — hence the
    _advance_token check, the same token _next bumps."""
    src = open("ui/screens/birth_guide_screen.py").read()
    fn = src[src.index("def _advance_after_card"):]
    fn = fn[:fn.index("\n    def ", 1)]
    assert "_advance_token" in fn, "a manual tap during the ripple must win"
    assert "insert_sd" in fn, "must confirm it is still on the card step"


# --- no screen may be a trap -----------------------------------------------
# Reported twice, on the same screen, a day apart:
#   "this screen will not allow me to go back" (2026-08-06)
#   "this screen is still a trap, user cant go back from here" (2026-08-07)
# Both times it was the "Which Raspberry Pi is this?" chooser: six options and
# no exit. handle_back() and _back_action existed the whole time — but only a
# LEFT-EDGE SWIPE reached them, and nothing on screen said so. An invisible
# affordance is no affordance; recovering it needed a UI restart over SSH,
# which a field operator does not have.

def test_every_screen_with_somewhere_to_go_back_to_SHOWS_it():
    """If a screen sets _back_action it must also render a visible control.
    Guided steps get theirs from WizardStep(on_back=...); the rest use
    _back_row()."""
    import re
    src = open("ui/screens/birth_guide_screen.py").read()
    bad = []
    for name, body in re.findall(r"def (_render_\w+)\(self[^)]*\):(.*?)(?=\n    def |\Z)",
                                 src, re.S):
        sets_back = re.search(r"_back_action\s*=\s*self\._(render|back)", body)
        # "_back_row(" not "_back_row()": a screen may rename its exit (the
        # hardware confirmation calls it "Not right — change") and it still has
        # to be THE back control, not a second one.
        shows = "_back_row(" in body or "on_back=" in body
        if sets_back and not shows:
            bad.append(name)
    assert not bad, f"back target but no visible way to use it: {bad}"


def test_the_back_control_never_lies_about_being_there():
    """A terminal screen has nowhere to go. _back_row returns None then, so the
    caller adds nothing rather than a button that does nothing."""
    src = open("ui/screens/birth_guide_screen.py").read()
    row = src[src.index("def _back_row"):src.index("def handle_back")]
    assert "return None" in row
    assert 'callable(getattr(self, "_back_action"' in row


# --- a hint must not be drawn through the body ------------------------------
#
# Photo, 2026-08-09, step 7 of 8: the body paragraph and the yellow connector
# hint rendered ON TOP OF EACH OTHER, unreadable. The hint's height was pinned
# at dp(40) — two lines — while the per-model connector guidance runs to six
# ("On a Pi 3A+ it's the full-size USB-A socket — its micro-USB is power
# only..."). The overflow drew straight through the paragraph above it, on the
# one step whose entire job is telling you which socket to use.

def test_the_hint_grows_with_its_text():
    # Read the file, not func_source: there are two __init__s here (_Dots has
    # one) and it returns the first.
    src = open("ui/widgets/wizard_step.py").read()
    hint = src[src.index("if hint:"):src.index("if warning:")]
    assert "texture_size" in hint, \
        "a fixed-height hint overflows into the body text above it"
    body = src[src.index("body_lbl = Label"):src.index("if hint:")]
    assert "texture_size" in body, "the body already does this — keep it"


# --- power, on the step that spends it -------------------------------------
#
# Operator, 2026-08-09, looking at the final step: "this screen also needs to
# tell the user to attach the pi to a power source."
#
# Right on one board, WRONG on another and impossible on a third — so it is a
# per-board line, exactly like the connector hint. On a 3A+ over an ordinary
# A-to-A a second supply back-feeds into Node Medic, which the step-6 hint has
# warned about all along; saying "plug in power" there would contradict it.

def test_the_provisioning_step_says_where_power_comes_from():
    from ui.birth_guide_flow import guide_steps
    life = next(s for s in guide_steps("pi", "pi_3a_plus")
                if s.get("gate") == "node_online")
    assert "power" in life["hint"].lower()


def test_the_power_line_differs_by_board():
    from ui.birth_guide_flow import guide_steps
    hints = {k: next(s for s in guide_steps("pi", k)
                     if s.get("gate") == "node_online")["hint"]
             for k in ("pi_zero_2w", "pi_3a_plus", "pi_4b")}
    assert len(set(hints.values())) == 3, "one sentence for all boards is the bug"


def test_it_never_contradicts_the_connector_warning():
    """The 3A+ hint at step 6 says a second supply will fight the medic's over
    an ordinary A-to-A. The power line must not then tell them to add one."""
    from ui.birth_guide_flow import guide_steps
    power = next(s for s in guide_steps("pi", "pi_3a_plus")
                 if s.get("gate") == "node_online")["hint"]
    assert "do NOT plug a supply" in power or "Do NOT plug a supply" in power
    assert "5V wire removed" in power, "name the one case where it IS safe"


def test_the_zero_is_told_to_use_its_own_supply():
    """PWR IN is a separate socket, so there is nothing to fight — and it takes
    the long install off Node Medic's rail, which has browned out before."""
    from ui.birth_guide_flow import guide_steps
    power = next(s for s in guide_steps("pi", "pi_zero_2w")
                 if s.get("gate") == "node_online")["hint"]
    assert "PWR IN" in power and "own power" in power


def test_an_unknown_board_claims_no_socket():
    from ui.pi_connectors import power_hint
    h = power_hint("something_new")
    assert "if this pi has" in h.lower(), "no socket may be named for a board we don't know"


def test_the_end_of_a_walkthrough_is_not_headed_like_the_start():
    """Operator, 2026-08-09: "i pressed wake it up and got taken to this name
    screen, the node has already been named." Nothing was being re-asked — the
    radio, the board, the Pi and the address were all carried across — but the
    screen was headed "Birth a new node" over "Name this node", which reads as
    starting over."""
    from tests.srcutil import func_source
    src = func_source("ui/screens/birth_screen.py", "_build_chooser")
    assert "_declared_pi_address" in src
    assert "Bring {nm} to life" in src or 'f"Bring {nm} to life"' in src
    assert '"Its name" if' in src, "the name field must stop asking for a name"


def test_the_provisioning_step_draws_a_cable_not_radio_waves():
    """Operator, reading it off the screen 2026-08-09: "the animation depicts a
    radio board talking via radio signals to the node medic; in fact it's a
    Raspberry Pi talking to the medic over cable." Same class as the wrong Pi
    picture — the words were right and the picture taught something else."""
    from ui.birth_guide_flow import guide_steps
    life = next(s for s in guide_steps("pi") if s.get("gate") == "node_online")
    assert life["anim"] == "provision_cable"
    assert life["anim"] != "provision", "that one broadcasts"


def test_the_cable_animation_gets_the_operators_own_pi():
    """Standing rule: every picture is the hardware in their hand."""
    src = open("ui/screens/birth_guide_screen.py").read()
    assert "ProvisionOverCableAnim" in src
    pi_anims = src[src.index("_PI_ANIMS = ("):]
    pi_anims = pi_anims[:pi_anims.index(")")]
    assert "ProvisionOverCableAnim" in pi_anims, \
        "it must be in _PI_ANIMS or it never receives pi_key"


def test_the_boluses_travel_and_swell():
    """"the cable's like a python swallowing a tennis ball — there'll be balls
    going down the tube travelling towards the Node Medic from the Pi"."""
    from tests.srcutil import func_source
    src = func_source("ui/widgets/birth_anims.py", "_draw",
                      cls="ProvisionOverCableAnim")
    assert "BOLUSES" in src and "swell" in src
    assert "self.phase" in src, "they have to move"


def test_a_failed_pi_build_gets_pi_advice_not_board_advice():
    """Operator, live 2026-08-09, on a Pi + RNode build that died at its first
    step: the popup said "Board won't flash? Hold PRG, press RST once… try a
    known-good USB data cable". A Pi has no PRG button and the cable was not
    the problem — it had wedged on a browning-out supply. Advice for the wrong
    failure spends the one thing the operator has least of."""
    from tests.srcutil import func_source
    src = func_source("ui/screens/birth_screen.py", "_popup_outcome")
    assert '_last_type' in src and 'pi_rnode' in src
    pi_branch = src[src.index('== "pi_rnode"'):src.index("else:")]
    assert "power" in pi_branch.lower()
    assert "PRG" not in pi_branch and "cable" not in pi_branch.lower() or \
        "brown out" in pi_branch.lower()
    assert "reader" in pi_branch, "point at the card check, which now exists"


def test_a_proved_address_is_not_re_litigated_by_a_weaker_search():
    """Operator, 2026-08-10, photo: "this page still says cant find the pi
    while the button says ok start. ok start works, so im guessing the
    statement that the pi cant be found is false?" — it was false. The guided
    step before this one does not open until the node ANSWERS, so the medic had
    already spoken to the Pi at 10.55.0.1. The screen then ran the general
    search again, that slower probe came back empty, and its amber "Couldn't
    find the Pi yet" got the last word directly above a Start button that
    worked. A false alarm beside a working button teaches an operator to
    distrust the warnings that matter."""
    from tests.srcutil import func_source
    src = func_source("ui/screens/birth_screen.py", "_build_chooser")
    proved = src.split('proved = getattr(self, "_declared_pi_address"')[1]
    head = proved[:900]
    assert "return" in head, "a proved address must end the question"
    assert "_pi_addr_in.text = proved" in head
    # and it must settle BEFORE the search that can contradict it
    assert src.index("proved = getattr") < src.index("self._find_pi()")


def test_no_screen_points_at_a_button_that_was_deleted():
    """The Find button went on 2026-08-02 — the medic searches by itself. The
    copy telling the operator to tap it outlived it by a week."""
    from tests.srcutil import func_source
    src = func_source("ui/screens/birth_screen.py", "_build_chooser")
    body = "\n".join(l for l in src.splitlines() if not l.strip().startswith("#"))
    assert "Tap Find" not in body


def test_a_failed_build_does_not_advance_to_the_node_is_built():
    """Operator, 2026-08-10: "after I press got it on this warning it takes me
    to the screen that tells me to unplug pi and connect it to radio and power
    it up. the button says that's the node built... this is confusing after a
    warning saying something didn't work."

    The hand-off remembered a return point and returned there whatever
    happened. A walkthrough that celebrates a failed build sends a dead node
    into the field."""
    from tests.srcutil import func_source
    hand = func_source("ui/screens/birth_screen.py", "_hand_back_to_guide")
    assert "build_failed" in hand and "_had_failure" in hand, \
        "the outcome must cross back with the hand-off"
    res = func_source("ui/screens/birth_guide_screen.py", "resume")
    assert '"build_failed"' in res and "at - 1" in res, \
        "a failed build stays on the step that did the work"


def test_a_failed_build_stops_the_step_driving_itself():
    """That step advances itself the moment the Pi answers — which it still
    does after a failure, so left alone the medic would relaunch the identical
    build by itself, forever."""
    from tests.srcutil import func_source
    src = func_source("ui/screens/birth_guide_screen.py", "_render_step")
    assert "failed = bool(getattr(self, \"_build_failed\", False))" in src
    assert "if ok and not failed:" in src, "no self-driving after a failure"
    assert 'if not ok and s["gate"] == "node_online" and not failed:' in src, \
        "and the way out must not be hidden behind the patience timer"
    nxt = func_source("ui/screens/birth_guide_screen.py", "_next")
    assert "self._build_failed = False" in nxt, "retrying clears the failure"
