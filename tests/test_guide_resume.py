"""A hand-off must be able to come BACK.

Until this existed, a guided birth ended the moment it handed off to a full
screen: control went to the imager (or the BIRTH screen) and the walkthrough's
remaining steps — move the card into the Pi, connect the Pi, connect the radio,
prove it — were simply never reached.

Three visible symptoms, all one cause:
  * the imager has to print "Next: bring this Pi to life, 1. take the card
    out..." as its own plain text, because those are guide steps it can no
    longer reach;
  * the operator watched that screen "just sit there" (2026-08-08). It was not
    waiting for anything. It was the end of the road;
  * the final "put the radio on the Pi and prove it" step has never existed,
    and neither has a mid-flow radio flash — neither can be a hand-off if a
    hand-off cannot return. No birth in this project has ever finished.

Checked as pure logic against a stand-in screen: Kivy is not importable here.
"""

import pytest

from tests.srcutil import func_source

SCREEN = "ui/screens/birth_guide_screen.py"
IMAGER = "ui/screens/pi_imager_screen.py"
APP = "ui/app.py"


class _Guide:
    """The real resume logic, on a featherweight stand-in."""

    def __init__(self, steps=4, i=1):
        self._n = steps
        self._i = i
        self._path = "pi_rnode"
        self._resume_at = None
        self.finished = False
        self.rendered = 0

    # the bits the real methods lean on
    def _pi_key_for_text(self):
        return ""

    def _render_step(self):
        self.rendered += 1

    def _finish(self):
        self.finished = True


def _bind(guide, steps):
    """Run the real methods against the stand-in, with guide_steps stubbed."""
    import ui.screens.birth_guide_screen as bgs
    real = bgs.guide_steps
    bgs.guide_steps = lambda *a, **k: [{}] * steps
    try:
        yield bgs
    finally:
        bgs.guide_steps = real


@pytest.fixture
def bgs(monkeypatch):
    pytest.importorskip("ui.screens.birth_guide_screen",
                        reason="screen module not importable in this run")
    import ui.screens.birth_guide_screen as mod
    return mod


# --- the mechanism --------------------------------------------------------

def test_a_handoff_records_where_to_come_back_to():
    src = func_source(SCREEN, "_next")
    assert "_resume_at = self._i + 1" in src, \
        "handing off must record the step to resume at"
    # against the CALL, not the `if self._on_navigate` guard above it
    assert src.index("_resume_at") < src.index('_on_navigate(cur["screen"])'), \
        "record BEFORE navigating — the screen may hand back immediately"


def test_resume_returns_false_when_nothing_is_pending():
    """A screen that finishes after the operator has walked away must not drag
    them back into a walkthrough they left."""
    src = func_source(SCREEN, "resume")
    assert "if at is None" in src and "return False" in src


def test_resume_clears_the_return_point_before_moving():
    """Or a second call — a double-tap, a late poll — would replay the step."""
    src = func_source(SCREEN, "resume")
    assert src.index("_resume_at = None") < src.index("_render_step"), \
        "clear the return point before rendering, not after"


def test_resume_finishes_when_the_handoff_was_the_last_step():
    src = func_source(SCREEN, "resume")
    assert "_finish()" in src, "resuming past the last step must complete"


def test_a_new_walkthrough_forgets_the_old_return_point():
    """A stale one would send a late-finishing screen into a walkthrough that
    has since restarted, landing the operator at a step for a different node."""
    src = func_source(SCREEN, "reset")
    assert "_resume_at = None" in src
    assert "_radio_verified = False" in src, "and the gate's evidence with it"


def test_resume_records_what_the_screen_achieved():
    """The result is what a later gate consults — e.g. radio_verified."""
    src = func_source(SCREEN, "resume")
    assert "setattr" in src and "result" in src


# --- the offer is only made when it is real -------------------------------

def test_the_app_refuses_to_resume_when_nothing_is_waiting():
    src = func_source(APP, "resume_guided_birth")
    assert "has_pending_resume" in src
    assert "return False" in src


def test_the_app_switches_to_the_guide_before_resuming():
    src = func_source(APP, "resume_guided_birth")
    assert src.index("switch_mode") < src.index("g.resume"), \
        "render the guide first, or resume draws into a screen nobody sees"


# --- the imager stops being a terminus ------------------------------------

def test_the_imager_hands_back_before_falling_through_to_birth():
    src = func_source(IMAGER, "_back_to_birth")
    assert "resume_guided_birth" in src
    assert src.index("resume_guided_birth") < src.index("switch_mode"), \
        "try the walkthrough first; BIRTH is the fallback"


def test_a_broken_resume_cannot_strand_the_operator():
    """If the hand-back throws, the operator must still land somewhere. Being
    stuck on a finished screen is the failure this whole change removes."""
    src = func_source(IMAGER, "_back_to_birth")
    guard = src[:src.index("switch_mode")]
    assert "except Exception" in guard
    assert "switch_mode" in src, "there must still be an unconditional exit"


# --- the tool must not tell you off for obeying it ------------------------
#
# Reported live 2026-08-09, with a photo: the screen said "Take the radio out of
# Node Medic" (step 4 of 8) and five seconds later a popup said "Board
# disconnected! The board that was plugged in has vanished from USB. Check the
# cable and plug it back in before continuing."
#
# The tool contradicting its own instruction is worse than saying nothing: it
# teaches the operator that the warnings are noise, and the next one might be
# real.

def test_the_disconnect_watcher_can_be_told_an_absence_is_intended():
    src = func_source(APP, "expect_board_absence")
    assert "_board_absence_expected" in src


def test_the_watcher_actually_checks_that_flag():
    src = open(APP).read()
    i = src.index("Board disconnected!")
    watcher = src[max(0, i - 3000):i]
    assert "_board_absence_expected" in watcher, \
        "the warning must be suppressed when a step asked for the board to go"
    assert watcher.index("_board_absence_expected") > watcher.index("flash_in_progress"), \
        "checked alongside the other legitimate-absence escapes"


def test_the_unplug_step_declares_it():
    src = func_source(SCREEN, "_render_step")
    assert "_expect_board_absence(True)" in src
    assert "DisconnectBoardAnim" in src


def test_a_new_walkthrough_gets_the_warning_back():
    """Otherwise one guided birth would silence a real fault for the rest of
    the session."""
    src = func_source(SCREEN, "reset")
    assert "_expect_board_absence(False)" in src


def test_declaring_it_can_never_raise_inside_a_step_render():
    src = func_source(SCREEN, "_expect_board_absence")
    assert "except Exception" in src


# --- detection is feedback, not consent -----------------------------------
#
# Operator, 2026-08-09: "the birth process asked me to remove the radio awfully
# fast. I've got a feeling it hasn't been flashed in that time." They were
# right — the log showed no flash at all, and 511 USB attach/detach events in
# 25 minutes from a boot-looping V4.
#
# Steps 1 and 2 both used the connect_board animation, whose branch hides Next
# and advances the moment a board is SEEN. So step 1 never showed its "Flash
# this radio" button and moved on from mere presence — and a board that
# boot-loops is present. Step 2, the gate, auto-advanced past itself for the
# same reason. Presence is not a flash, and it is certainly not a pass.

def test_a_step_that_hands_off_or_gates_requires_a_press():
    src = func_source(SCREEN, "_render_step")
    branch = src[src.index("isinstance(anim, ConnectBoardAnim)"):]
    branch = branch[:branch.index("elif isinstance(anim, InsertSdAnim)")]
    assert 's.get("screen") or s.get("gate")' in branch, \
        "a hand-off or a gate must suppress the auto-advance"
    # the plain 'plug it in' step keeps its old behaviour
    assert "hide_next()" in branch
    assert "else:" in branch


def test_the_ripple_still_fires_on_those_steps():
    """Detection remains useful feedback — "I can see your board" — it just
    stops deciding anything."""
    src = func_source(SCREEN, "_render_step")
    branch = src[src.index("isinstance(anim, ConnectBoardAnim)"):]
    branch = branch[:branch.index("elif isinstance(anim, InsertSdAnim)")]
    assert "mark_connected" in branch and "on_present=" in branch


def test_the_radio_step_still_carries_its_button():
    """Which is what the operator presses to actually start the flash."""
    from ui.birth_guide_flow import guide_steps
    first = guide_steps("pi")[0]
    assert first.get("screen") == "birth"
    assert first.get("next", "").strip(), "no button = nothing starts the flash"


def test_the_radio_gate_cannot_advance_itself():
    from ui.birth_guide_flow import guide_steps
    gate_step = next(s for s in guide_steps("pi")
                     if s.get("gate") == "radio_ready")
    # No animation, deliberately: this step is a VERDICT, not an action. It used
    # to loop connect_board — a board descending onto Node Medic — while the
    # board in question was already plugged in and being judged.
    assert gate_step.get("anim") is None
    assert not gate_step.get("screen"), \
        "the radio verdict has nowhere to hand off TO; the flash already ran"


def test_a_gate_is_checked_before_the_step_hands_off():
    """The last step is BOTH a gate and a hand-off — provisioning must not start
    against a Pi that is not answering. That is only safe because _next tests
    the gate before it looks at `screen`."""
    src = func_source(SCREEN, "_next")
    body = src.split('"""')[2] if '"""' in src else src
    assert body.index('gate = cur.get("gate")') < body.index('cur.get("screen")')


# --- the silent skip loop ate the gate ------------------------------------
#
# THE ROOT CAUSE of 2026-08-09, and the second time this trap has been walked
# into. _render_step advances _i BEFORE rendering:
#
#     while self._i < len(steps) and self._step_is_redundant(steps[self._i]):
#         self._i += 1
#
# so anything checked in _next() is simply bypassed. The comment in
# _render_step already records the first instance (2026-08-03, the
# power-compatibility check, fixed by re-keying it on the path).
#
# The radio gate was then added to _next() and eaten the same way: both new
# steps carry the connect_board animation, the V4 was plugged in, so BOTH were
# judged "already done" and skipped in silence — the flash hand-off and the gate
# guarding it. The operator reached "Take the radio out" having flashed nothing,
# and the trace was empty because _next never ran.
#
# "The board is plugged in" answers "have you plugged the board in?". It does
# not answer "has it been flashed?" or "did it pass?".

def test_a_gated_step_can_never_be_skipped_as_redundant():
    src = func_source(SCREEN, "_step_is_redundant")
    assert 'step.get("gate")' in src
    guard = src[:src.index('anim = step.get("anim")')]
    assert "return False" in guard, "the guard must sit BEFORE the anim checks"


def test_a_handoff_step_can_never_be_skipped_either():
    """Skipping one silently discards the real work it exists to start."""
    src = func_source(SCREEN, "_step_is_redundant")
    assert 'step.get("screen")' in src


def test_on_the_pi_path_only_the_plug_in_the_pi_step_is_skippable():
    """A live check against the real flow, with a board notionally present."""
    from ui.birth_guide_flow import guide_steps
    skippable = [
        s["title"] for s in guide_steps("pi", "pi_3a_plus")
        if s.get("anim") in ("connect_board", "connect_pi")
        and not (s.get("gate") or s.get("screen"))
    ]
    assert skippable == ["Card in the Pi? Choose ONE way to power it"], skippable


def test_the_radio_steps_survive_a_board_being_plugged_in():
    """The exact case that broke it: the radio already on the medic."""
    from ui.birth_guide_flow import guide_steps
    steps = guide_steps("pi", "pi_3a_plus")
    radio = steps[0]
    gate = next(s for s in steps if s.get("gate"))
    for s in (radio, gate):
        assert s.get("gate") or s.get("screen"), \
            f"{s['title']!r} must carry a gate or a hand-off to be protected"


# --- the gate must have something in front of it --------------------------
#
# Live, 2026-08-09: the gate fired correctly and said "This radio hasn't been
# flashed and verified yet. Finish it here first" — while the step that could
# finish it had been skipped. A refusal the operator cannot act on is a wall.

def test_resuming_after_the_pair_check_starts_at_the_top():
    """It used to hardcode index 1, skipping step 0 on the reasoning that the
    radio was already connected. Step 0 is now where the radio is FLASHED."""
    src = func_source(SCREEN, "_resume_steps")
    assert "self._i = 0" in src
    assert "self._i = 1" not in src


def test_the_step_before_the_gate_can_actually_satisfy_it():
    """Whatever precedes the gate must hand off to real work, or the gate's
    instruction ("finish it here first") points at nothing."""
    from ui.birth_guide_flow import guide_steps
    steps = guide_steps("pi")
    gate_at = next(i for i, s in enumerate(steps) if s.get("gate"))
    assert gate_at > 0, "a gate cannot be the first step"
    assert steps[gate_at - 1].get("screen"), \
        "the step before a gate must hand off to the work that satisfies it"


def test_the_connect_ripple_finishes_like_the_card_one():
    """Same maths, same bug, reported the same way: 'this animation stopped'."""
    import re
    body = open("ui/widgets/birth_anims.py").read()
    seg = body[body.index("def mark_connected"):][:2600]
    target = float(re.search(r"Animation\(burst=([\d.]+)", seg).group(1))
    rip = body[body.index("def _ripples"):][:1400]
    count = int(re.search(r"for i in range\((\d+)\)", rip).group(1))
    stagger = float(re.search(r"i \* ([\d.]+)", rip).group(1))
    for i in range(count):
        alpha = (1.0 - min(1.0, target - i * stagger)) * 0.9
        assert alpha == 0, f"ring {i} left at alpha {alpha:.3f} when the burst ends"


# --- a hand-off must prepare the destination ------------------------------
#
# Live, 2026-08-09: "Flash this radio →" landed the operator on the full,
# unscoped BIRTH form with an EMPTY name field. The walkthrough already knows
# the name and the job; making them retype it is asking twice, and an unscoped
# form does not say what it is for.

# These were three substring checks over _hand_over_name's source, and all
# three PASSED while the screen the operator actually landed on was unscoped
# (live, 2026-08-09, second lap): scoping then naming was the documented order,
# but prefill_name calls _fresh_lap() too, and _fresh_lap clears ``_firmware``.
# The source said "begin_guided before prefill_name" and the behaviour was
# "begin_guided undone by prefill_name". So run the shipped code instead.

BIRTH = "ui/screens/birth_screen.py"


def _real_birth_screen():
    """A stand-in BIRTH screen running the SHIPPED begin_guided / prefill_name /
    _fresh_lap, with only the Kivy-shaped parts stubbed. Kivy cannot open a
    window here, but these three methods are pure attribute logic."""
    import textwrap
    import types

    scr = types.SimpleNamespace(
        _firmware="stale", _forced_firmware=None, _sel_board="stale",
        _sel_pi=None, _detected="stale", _rtnode_target=None,
        _flash_view=False, _lap_prepared=False, _from_imaging="stale",
        _prefill_location=("x", "y", "z"),
        _name_in=types.SimpleNamespace(text=""),
        chooser_builds=0, detects=0)
    scr._busy_with_a_build = lambda: False
    scr._warn_build_running = lambda: None
    scr._build_chooser = lambda *a, **k: setattr(
        scr, "chooser_builds", scr.chooser_builds + 1)
    scr._detect_board = lambda: setattr(scr, "detects", scr.detects + 1)
    for name in ("_fresh_lap", "begin_guided", "prefill_name"):
        ns = {}
        exec(compile(textwrap.dedent(func_source(BIRTH, name)), BIRTH, "exec"), ns)
        setattr(scr, name, types.MethodType(ns[name], scr))
    return scr


def test_begin_guided_scopes_and_names_in_one_call():
    scr = _real_birth_screen()
    scr.begin_guided("host", name="Rooftop-East")
    assert scr._firmware == "rnode", "the radio step's job is flash-as-an-RNode"
    assert scr._name_in.text == "Rooftop-East", "the guide already asked the name"
    assert scr.detects == 1, "the board is already plugged in per the guide"


def test_naming_never_undoes_the_scoping():
    """THE 2026-08-09 bug, as behaviour: whatever the hand-off does to carry the
    name across, the screen must still be scoped when it settles."""
    scr = _real_birth_screen()
    scr.begin_guided("host", name="Rooftop-East")
    assert (scr._firmware, scr._name_in.text) == ("rnode", "Rooftop-East")


def test_the_handoff_hands_over_both_at_once():
    """Run the shipped _hand_over_name against a real-behaviour BIRTH screen."""
    import sys
    import textwrap
    import types

    scr = _real_birth_screen()
    app = types.SimpleNamespace(birth_screen=scr)
    mod = types.ModuleType("kivy.app")
    mod.App = types.SimpleNamespace(get_running_app=lambda: app)
    pkg = sys.modules.get("kivy") or types.ModuleType("kivy")
    saved = {k: sys.modules.get(k) for k in ("kivy", "kivy.app")}
    sys.modules["kivy"], sys.modules["kivy.app"] = pkg, mod
    try:
        ns = {}
        exec(compile(textwrap.dedent(func_source(SCREEN, "_hand_over_name")),
                     SCREEN, "exec"), ns)
        guide = types.SimpleNamespace(_node_name="Rooftop-East")
        types.MethodType(ns["_hand_over_name"], guide)("birth")
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v
    assert scr._firmware == "rnode", "landed on the unscoped chooser again"
    assert scr._name_in.text == "Rooftop-East", "made to retype the name"


def test_it_uses_the_method_each_screen_actually_has():
    """BIRTH takes prefill_name; the imager takes prefill_hostname. Calling the
    wrong one silently does nothing, which is how the empty field happened."""
    src = func_source(SCREEN, "_hand_over_name")
    assert "prefill_name" in src and "prefill_hostname" in src


# --- one board choice, one confirmation ------------------------------------
#
# Operator, live, 2026-08-09, landing on the BIRTH screen after picking the
# radio AND confirming it against its photo in the guide: "we have already
# selected the radio board. is this screen necessary?" — then the rule:
# "board choice once, and one confirmation."
#
# It matters beyond tidiness. The screen it landed on was a grid of
# near-identical ESP32-S3 boards, and picking the wrong one there flashes the
# wrong image. A question whose answer is already known trains people to tap
# past it, and that is the tap that bricks a board.

def _declared(mismatch_boards=None):
    """A BIRTH screen handed a board the operator already confirmed, with
    detection's opinion applied."""
    import types

    scr = _real_birth_screen()
    scr._boards = [types.SimpleNamespace(key="heltec32_v4",
                                         display_name="Heltec LoRa32 v4"),
                   types.SimpleNamespace(key="t3s3", display_name="LilyGO T3S3")]
    scr.begin_guided("host", name="Rooftop", board_key="heltec32_v4")
    if mismatch_boards is not None:
        _run(scr, "_detected_done", {"found": True, "platform": "ESP32",
                                     "boards": mismatch_boards})
    return scr


def _run(scr, name, *args):
    import textwrap
    import types

    ns = {}
    exec(compile(textwrap.dedent(func_source(BIRTH, name)), BIRTH, "exec"), ns)
    return types.MethodType(ns[name], scr)(*args)


def test_a_confirmed_board_is_not_asked_for_again():
    scr = _declared()
    assert scr._sel_board is not None and scr._sel_board.key == "heltec32_v4"


def test_detection_agreeing_leaves_the_choice_alone():
    import types
    agree = [types.SimpleNamespace(key="heltec32_v4"),
             types.SimpleNamespace(key="t3s3")]
    scr = _declared(mismatch_boards=agree)
    assert scr._sel_board.key == "heltec32_v4"
    assert not scr._declared_mismatch


def test_detection_disagreeing_stops_and_says_so():
    """Not 'ask again' — REPORT. The board in the medic is not the board they
    believe they are holding, and that has to be said out loud."""
    import types
    scr = _declared(mismatch_boards=[types.SimpleNamespace(key="tbeam")])
    assert scr._sel_board is None, "a real conflict must not be auto-resolved"
    assert "Heltec LoRa32 v4" in scr._declared_mismatch
    assert "ESP32" in scr._declared_mismatch


def test_changing_the_board_here_takes_over_the_choice():
    import types
    scr = _declared(mismatch_boards=[types.SimpleNamespace(key="tbeam")])
    _run(scr, "_pick_board", types.SimpleNamespace(key="t3s3",
                                                   display_name="LilyGO T3S3"))
    assert scr._declared_board_key is None and scr._declared_mismatch == ""


def test_the_guide_hands_the_confirmed_board_over():
    src = func_source(SCREEN, "_hand_over_name")
    assert "board_key" in src, "the guide knows the board; it must say so"


# --- doing the thing IS the interaction ------------------------------------
#
# Operator, mid-walkthrough 2026-08-09: "when the Node Medic senses the radio's
# been removed, the user doesn't have to press next — it automatically goes to
# the next step."
#
# The connect steps already advance when a board APPEARS, because plugging it
# in is the action. Unplugging is the action here, for the same reason.

def test_taking_the_radio_out_advances_by_itself():
    src = func_source(SCREEN, "_render_step")
    assert "_start_absence_poll" in src
    branch = src[src.index("DisconnectBoardAnim, RadioToPiAnim"):]
    assert "isinstance(anim, DisconnectBoardAnim)" in branch, \
        "only the step the medic can SEE — a board landing on the Pi it cannot"


def test_the_absence_poll_only_fires_on_a_confirmed_absence():
    """'Can't tell' must never read as 'gone'. An exception in the port scan
    advancing the flow would skip the step on a transient error."""
    src = func_source(SCREEN, "_start_absence_poll")
    assert "gone = False" in src.split("except Exception")[1][:120]


def test_the_unplug_step_has_no_button_to_press():
    """Operator, 2026-08-10: "that doesn't need to be there because when the
    radio is disconnected the Node Medic automatically detects that and moves to
    the next screen." A button that only ever repeats what the medic already saw
    is an invitation to press it.

    It kept its Next until now on the grounds that a poll which never fires must
    not strand anyone — which really did happen on step 7 of 8 the same day, a
    Pi's USB id missing from a table. So the button is hidden and scheduled
    back after the patience wait: nothing to press while this works, a way out
    if it does not."""
    src = func_source(SCREEN, "_render_step")
    branch = src[src.index("DisconnectBoardAnim, RadioToPiAnim"):]
    branch = branch[:branch.index("elif isinstance(anim, ConnectBoardAnim)")]
    assert "hide_next" in branch
    assert "show_next" in branch and "WAIT_PATIENCE_S" in branch, \
        "hidden is not the same as gone — the dead end is the worse failure"
    # and ONLY that step: the radio going onto the Pi is not something the medic
    # can ever see, so its button is the only road forward.
    disc = branch[branch.index("isinstance(anim, DisconnectBoardAnim)"):]
    assert "hide_next" in disc


def test_a_manual_tap_still_wins_the_race():
    src = func_source(SCREEN, "_on_board_absent")
    assert "_advance_token" in src, "a tap during the beat must not be overtaken"
    assert "mark_removed" in src, "let the picture finish before moving on"


def test_a_gate_that_has_passed_does_not_stop_you():
    """Operator, 2026-08-09: "this step also does not need user to press the
    button." The radio verdict is a fact the medic is already holding — the
    flash and the verify happened two screens ago. It still STOPS on a failure;
    that is what the gate is for."""
    src = func_source(SCREEN, "_render_step")
    assert 'elif s.get("gate"):' in src
    branch = src[src.index('elif s.get("gate"):'):]
    # "and not failed" since 2026-08-10: a gate that passes still advances by
    # itself, EXCEPT straight after a failed build, where advancing would relaunch
    # the identical build unasked. "and not back_arrival" since 2026-08-14:
    # EXCEPT against someone walking backward, too — re-arming the advance on a
    # BACK arrival bounced the operator off this very gate eleven times (see
    # test_guide_exit_and_back_20260814).
    assert "_gate_state" in branch
    assert "if ok and not failed and not back_arrival:" in branch
    assert "_advance_token" in branch, "a manual tap must still win the race"


def test_the_provisioning_button_only_ever_means_try_again():
    """Consent was given one screen earlier — "That's the node built" — and this
    screen says plainly what it is about to do. Asking again is asking twice
    (operator, 2026-08-09: "wake it up is unnecessary")."""
    from ui.birth_guide_flow import guide_steps
    life = next(s for s in guide_steps("pi") if s.get("gate") == "node_online")
    assert life["next"].startswith("Try again")


def test_the_wait_says_how_long_a_pi_takes():
    """"if no node is detected then a button can appear saying try again, with
    a text box stating the wake up time of a pi"."""
    src = func_source(SCREEN, "_node_gate")
    assert "30" in src and "two minutes" in src


def test_a_reasonable_wait_offers_no_button_to_press():
    """Operator, 2026-08-09: "it looked like I didn't have to press the try
    again button, if that's the case the button should be replaced with a text
    box that says please wait." An escape hatch offered too early invites a
    press that skips past hardware which was merely slow — a Pi expanding its
    card on first boot looks exactly like a Pi that will never come up."""
    src = func_source(SCREEN, "_render_step")
    branch = src[src.index('elif s.get("gate"):'):]
    assert "hide_next()" in branch
    assert "show_next()" in branch, "and it must come back if the wait is overdue"
    assert "WAIT_PATIENCE_S" in branch


def test_the_wait_says_please_wait_and_that_nothing_needs_pressing():
    src = func_source(SCREEN, "_node_gate")
    assert "Please wait" in src
    assert "nothing" in src and "press" in src


def test_the_reason_is_on_screen_before_any_press():
    """It used to be set only by a BLOCKED press, so a step that carries itself
    forward showed nothing at all while it worked."""
    src = func_source(SCREEN, "_render_step")
    branch = src[src.index('elif s.get("gate"):'):]
    assert "_gate_warning = why" in branch


def test_the_gate_tries_wifi_when_the_cable_is_dead():
    """Operator, 2026-08-09, with a Pi that had gone deaf on the cable: "did you
    think about the wifi connection attempt if the cable connection fails?"

    No, and the imager already knew better — its boot poll watches BOTH, because
    "a card imaged WITH WiFi comes back on the network, not on USB" (bench,
    2026-08-02). Provisioning does not care which road it takes."""
    src = func_source(SCREEN, "_start_node_poll")
    assert "discover_peer" in src, "the cable stays the first road"
    assert "pi_discover" in src and "resolve" in src, "and Wi-Fi the second"
    assert src.index("discover_peer") < src.index("resolve"), \
        "cable first — it is the one the walkthrough set up"
    assert "hostnameify" in src, "the node is found by the name we gave it"


def test_the_refusal_names_both_roads():
    """Saying only 'not over the cable' sends the operator to check a cable when
    the Pi may be missing from the network entirely."""
    src = func_source(SCREEN, "_node_gate")
    assert "not over the cable" in src and "Wi-Fi" in src


def test_the_connect_pi_step_has_no_button_at_all():
    """This test used to demand a patience-timer Next as the way out for a
    self-powered Pi that only ever comes back on Wi-Fi — a road the step's
    old USB-only watcher could not see. The proof watcher walks both roads
    now (cable, then Wi-Fi, demanding the birth token on either), so the
    case that button served is gone, and the operator ordered it off
    (2026-08-14: "remove that next and let the Node Medic move on by
    itself"). Back remains as the escape for a dead card."""
    src = func_source(SCREEN, "_render_step")
    branch = src[src.index("isinstance(anim, ConnectPiAnim)"):]
    branch = branch[:branch.index("elif")]
    assert "hide_next()" in branch
    assert "show_next()" not in branch


# --- THE STANDING RULE: if the medic drives, there is nothing to press -----

def test_no_self_advancing_step_offers_a_button_to_press():
    """Operator, 2026-08-10: "anywhere where the Node Medic moves the screen on
    by itself, if the user is asked to move that page on by themselves, we need
    to remove that green button and just let the Node Medic do the work."

    Stated as a rule because it had to be asked for four separate times, once
    per step — the card into the reader, the radio out, the card into the Pi,
    the Pi onto the cable. A Next sitting under a finished animation reads as
    "the tool is waiting for you" on every successful run, which is exactly how
    these steps came to look stalled."""
    src = func_source(SCREEN, "_render_step")
    # every poll that ADVANCES the flow by itself (as opposed to _start_board_poll's
    # feedback-only form, which passes on_present and decides nothing)
    drivers = ("_start_absence_poll", "_start_card_poll", "_start_card_gone_poll",
               "_start_pi_poll")
    for call in drivers:
        assert call in src, f"{call} is not wired into the step renderer"
        # the branch it lives in: back to the nearest elif/if, forward to the next
        at = src.index(call)
        head = max(src.rfind("\n        elif ", 0, at), src.rfind("\n        if ", 0, at))
        nxt = src.find("\n        elif ", at)
        branch = src[head:nxt if nxt > 0 else len(src)]
        assert "hide_next" in branch, \
            f"{call} drives the flow but its step still offers a button"
        if call in ("_start_card_poll", "_start_pi_poll"):
            # TWO DELIBERATE EXCEPTIONS. "Put the SD card into Node Medic"
            # has no patience button because the step after it WRITES the
            # card: walking past a card the medic cannot see leads straight
            # to a destructive write against an unknown device. And the
            # connect-Pi step lost its patience button on 2026-08-14: its
            # proof watcher walks both roads (cable and Wi-Fi), so the
            # Wi-Fi-only Pi the button once rescued advances the step by
            # itself now. Back is the escape on both.
            continue
        assert "show_next" in branch and "WAIT_PATIENCE_S" in branch, \
            f"{call} must still offer a way out if the sensing never fires"


def test_a_gate_that_has_passed_offers_nothing_to_press_either():
    """Same rule, the other mechanism: a gate that passed advances itself after
    a beat, so its button is an invitation to race the tool. It stays only while
    the gate is BLOCKED, where it means "try again"."""
    src = func_source(SCREEN, "_render_step")
    # the full condition carries the 2026-08-14 direction guard as well
    branch = src[src.index("if ok and not failed and not back_arrival:"):]
    assert "hide_next" in branch[:900]
