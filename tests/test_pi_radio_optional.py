"""A Pi build no longer has to include flashing a radio (2026-09-06).

Operator, across three messages: "we need to have maybe just RNode, RTNode and
Pi... the birthing the pi process will walk through... with the option to add
an RNode to that" and then, with photos of the old forced sequence: "give the
user an option to either attach a pre-existing RNode or to go back... and
birth an RNode and then connect it to the finished Raspberry Pi."

The pre-existing "pi" sequence (host-detect radio -> flash -> take it out -> SD
card -> Pi to life -> plug radio onto Pi) is bench-tested, hard-won design
(2026-08-02: catch a dead radio before the four-minute card write). It is not
wrong, it is just the ONLY path offered — an operator with an already-working
radio was walked through flashing a second one they did not need.

guide_steps() keeps that original sequence, byte for byte, as its default.
flash_radio=False is the new second path.
"""
from ui.birth_guide_flow import guide_steps

_ORIGINAL_TITLES = [
    "Connect the radio to Node Medic", "The radio has to work first",
    "Take the radio out of Node Medic", "Put the SD card into Node Medic",
    "Remove the SD card from Node Medic and insert it into the Raspberry Pi",
    "Card in the Pi? Now give it power", "Bring the node to life", "",
]


def test_the_default_pi_sequence_is_byte_for_byte_unchanged():
    """The bench-tested order (2026-08-02) must survive untouched — this
    feature ADDS a second path, it does not touch the first."""
    assert [s["title"] for s in guide_steps("pi")] == _ORIGINAL_TITLES


def test_flash_radio_false_drops_exactly_the_three_medic_flashing_steps():
    titles = [s["title"] for s in guide_steps("pi", flash_radio=False)]
    assert titles == [
        "Put the SD card into Node Medic",
        "Remove the SD card from Node Medic and insert it into the Raspberry Pi",
        "Card in the Pi? Now give it power",
        "Bring the node to life", "",
    ]


def test_only_the_pi_path_is_affected_by_the_flag():
    """host and radio never had a radio-flashing choice to make — the flag
    must not change anything about them."""
    for path in ("host", "radio"):
        assert guide_steps(path, flash_radio=True) == guide_steps(
            path, flash_radio=False)


def test_the_final_handoff_stops_naming_a_radio_that_was_never_flashed_here():
    """The last step's own words used to say "the radio you flashed at the
    start" — true only when the medic did the flashing. An operator plugging in
    their own pre-existing radio would read an instruction about something
    that never happened."""
    default_body = [s["body"] for s in guide_steps("pi")
                    if s.get("anim") == "radio_to_pi"][0]
    skip_body = [s["body"] for s in guide_steps("pi", flash_radio=False)
                if s.get("anim") == "radio_to_pi"][0]
    assert "you flashed at the start" in default_body
    assert "you flashed at the start" not in skip_body
    assert "plug in your radio" in skip_body


def test_the_antenna_warning_travels_with_the_flashing_steps_not_the_flag():
    """The warning belongs to POWERING a board with no antenna, which only
    happens when a radio is flashed here. Dropping the flashing steps must
    drop their warning with them, not leave an orphaned caution with nothing
    above it to explain what it is warning about."""
    skip = guide_steps("pi", flash_radio=False)
    from ui.birth_guide_flow import NO_ANTENNA_WARNING
    assert not any(s.get("warning") == NO_ANTENNA_WARNING for s in skip)


def test_no_step_is_duplicated_across_the_two_sequences():
    """The bug the operator's photos showed: "Connect the radio to Node
    Medic" and "Connect your node" (the antenna-landing's own detect screen)
    describe the same physical act in near-identical words. Skipping the
    flashing steps must not leave a second, differently-worded copy of any of
    them still in the list."""
    from ui.birth_guide_flow import _PI_FLASH_STEP_TITLES
    skip_titles = {s["title"] for s in guide_steps("pi", flash_radio=False)}
    assert not (skip_titles & _PI_FLASH_STEP_TITLES)


def test_the_pi_key_hint_still_applies_on_the_shorter_sequence():
    """The board-specific connector wording (pi_connectors) must not have been
    wired only to steps that no longer exist in this path."""
    steps = guide_steps("pi", pi_key="pi_zero_2w", flash_radio=False)
    connect = [s for s in steps if s.get("anim") == "connect_pi"]
    assert connect and connect[0]["hint"]


# --------------------------------------------------------------------------- #
# The board-pick gate, found live 2026-09-06
#
# Reported by the operator with a photo: chose "A Raspberry Pi propagation
# node" -> "I already have a working radio" -> named the node -> answered the
# map question -> landed on "Which radio board is this?" anyway. That screen
# identifies an EXACT radio model so the power-compat check can warn about a
# Pi that cannot feed it — a question that only means anything when the medic
# is about to power that radio itself. A radio never plugged into the medic at
# all has nothing here to identify.
#
# The gate lives in _render_step, which is real Kivy screen code (Kivy is not
# importable in CI), so this is a source-level pin: srcutil.func_source over
# the shipped method, same technique test_encryption_screen.py uses.
# --------------------------------------------------------------------------- #

from tests.srcutil import func_source

SCREEN = "ui/screens/birth_guide_screen.py"


def test_the_board_pick_gate_checks_the_flash_radio_flag():
    body = func_source(SCREEN, "_render_step", cls="BirthGuideScreen")
    gate = body.split("_render_pick_board()", 1)[0].splitlines()[-6:]
    gate_src = "\n".join(gate)
    assert '_pi_flash_radio' in gate_src, (
        "the board-pick gate fires for every \"pi\" build regardless of "
        "whether a radio is being flashed here at all")


def test_the_gate_still_fires_on_the_default_flash_here_path():
    """The fix must narrow the gate, not remove it — the bench-tested
    power-compat check (2026-08-03) still has to run when the medic really is
    about to flash a radio."""
    body = func_source(SCREEN, "_render_step", cls="BirthGuideScreen")
    assert 'self._path == "pi"' in body
    assert "_render_pick_board()" in body


# --------------------------------------------------------------------------- #
# The final hand-off's own "Board (radio)" field, found live 2026-09-06
#
# The operator's Pi already carried a working RAK4631, on its own power,
# never plugged into the medic — and the "Bring the node to life" hand-off
# still blocked on "Tap to choose a board", because self._sel_board is only
# ever set by identifying a radio ON THE MEDIC'S USB, which never happens in
# this path. begin_guided() now carries the SAME flash_radio flag the guide
# itself uses, all the way into birth_screen.py.
# --------------------------------------------------------------------------- #

BIRTH_SCREEN = "ui/screens/birth_screen.py"


def test_begin_guided_accepts_and_stores_flash_radio():
    sig = func_source(BIRTH_SCREEN, "begin_guided", cls="BirthScreen")
    assert "flash_radio=True" in sig.split("\n")[0] or "flash_radio" in sig
    assert "self._flash_radio = bool(flash_radio)" in sig


def test_a_fresh_lap_resets_flash_radio_to_true():
    """A stale False from an earlier skip-flash build must never leak into a
    build reached some other way."""
    body = func_source(BIRTH_SCREEN, "_fresh_lap", cls="BirthScreen")
    assert "self._flash_radio = True" in body


def test_the_board_field_does_not_block_when_the_radio_is_never_flashed_here():
    body = func_source(BIRTH_SCREEN, "_build_chooser", cls="BirthScreen")
    assert "_flash_radio" in body
    assert "Not flashed here" in body


def test_build_action_lets_a_moot_board_through():
    """The actual gate that decides whether the build can start at all."""
    body = func_source(BIRTH_SCREEN, "_build_action", cls="BirthScreen")
    assert "radio_known_or_moot" in body
    assert "_flash_radio" in body


def test_the_handoff_forwards_the_guides_own_flag():
    body = func_source(SCREEN, "_hand_over_name", cls="BirthGuideScreen")
    assert "flash_radio=getattr(self, \"_pi_flash_radio\", True)" in body


# --------------------------------------------------------------------------- #
# "Unplug BOTH boards" when neither was ever on the medic (live, 2026-09-06)
#
# Operator: Pi Zero already had a working RAK4631 attached, on its own power,
# reached only over Wi-Fi (never cabled). Both the build-completion popup
# (birth_screen.py) and the guide's own final step (birth_guide_screen.py,
# "Step 7 of 7") told them to unplug boards that were never plugged in. "if
# the node medic registers that the pi is not plugged into the node medic
# then this message does not need to appear — you can just say finished."
# --------------------------------------------------------------------------- #

def test_the_final_guide_step_is_skippable_when_nothing_is_on_the_medic():
    body = func_source(SCREEN, "_step_is_redundant", cls="BirthGuideScreen")
    assert '"radio_to_pi"' in body
    assert "_pi_flash_radio" in body
    assert '"10.55.0."' in body


def test_the_final_step_is_never_skipped_when_the_radio_was_flashed_here():
    """The default (flash-here) path always has real work in this step —
    the check must short-circuit to "not redundant" for it."""
    body = func_source(SCREEN, "_step_is_redundant", cls="BirthGuideScreen")
    section = body.split('"radio_to_pi"', 1)[1]
    assert "getattr(self, \"_pi_flash_radio\", True)" in section
    assert "return False" in section.split("\n\n")[0]


def test_the_completion_popup_checks_whether_anything_is_on_the_medic():
    body = func_source(BIRTH_SCREEN, "_popup_outcome", cls="BirthScreen")
    assert "on_medic" in body
    assert "_flash_radio" in body
    assert '"10.55.0."' in body


def test_the_completion_popup_has_a_plain_finished_message():
    body = func_source(BIRTH_SCREEN, "_popup_outcome", cls="BirthScreen")
    assert '"Finished"' in body
    assert "nothing here needs moving" in body


# --- a waypoint never says "finished", in the body OR the title -------------
# Operator, mid-build 2026-09-07, looking at the new blue card three steps
# into a ten-step Pi build: "at the very top it shouldn't say build finished,
# it should say radio flashed — anything that says finished here gives the
# illusion that we're at the end of the build process." The colour was right
# and the WORD was still claiming an ending.

def _waypoint_card():
    """The literal card text of the more_to_come branch — comments stripped,
    because a test that reads the rationale instead of the copy proves
    nothing about what reaches the panel."""
    from tests.srcutil import func_source
    src = func_source("ui/screens/birth_screen.py", "_popup_outcome",
                      cls="BirthScreen")
    branch = src[src.index("if more_to_come:"):]
    branch = branch[:branch.index("elif")]
    return "\n".join(l for l in branch.splitlines()
                     if not l.strip().startswith("#"))


def test_the_waypoint_card_is_titled_for_the_step_not_the_build():
    head = _waypoint_card()
    assert '"Radio flashed"' in head, "the waypoint names what just happened"
    assert "finished" not in head.lower(), (
        "no form of 'finished' may appear on a card with steps still to come")


def test_the_waypoint_card_does_not_call_this_radio_a_phone_accessory():
    """"This is a radio to plug into a phone or computer" is true of a radio
    built on its own and FALSE of this one — it is going onto a Raspberry Pi,
    which the operator was told two screens earlier. It stays on the
    standalone card, where it is true."""
    head = _waypoint_card()
    assert "phone or computer" not in head
    assert "VITALS" not in head, "a mid-build waypoint sends nobody to VITALS"


def test_the_standalone_rnode_card_keeps_its_finished_wording():
    """The fix must not strip the ending from a build that IS the ending."""
    from tests.srcutil import func_source
    src = func_source("ui/screens/birth_screen.py", "_popup_outcome",
                      cls="BirthScreen")
    tail = src[src.index('elif getattr(self, "_last_type", "") == "rnode":'):]
    assert "phone or computer" in tail, "a standalone RNode still says what it is for"
    assert '"Build finished"' in tail
