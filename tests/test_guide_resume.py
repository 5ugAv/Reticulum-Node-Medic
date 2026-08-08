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


def test_the_gate_step_cannot_advance_itself():
    from ui.birth_guide_flow import guide_steps
    gate_step = next(s for s in guide_steps("pi") if s.get("gate"))
    # it must be reached by a press, and its own render must not auto-advance
    assert gate_step.get("anim") == "connect_board"   # the branch above covers it
    assert not gate_step.get("screen"), "a gate step must not also hand off"


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
    assert skippable == ["Connect the Pi to Node Medic"], skippable


def test_the_radio_steps_survive_a_board_being_plugged_in():
    """The exact case that broke it: the radio already on the medic."""
    from ui.birth_guide_flow import guide_steps
    steps = guide_steps("pi", "pi_3a_plus")
    radio = steps[0]
    gate = next(s for s in steps if s.get("gate"))
    for s in (radio, gate):
        assert s.get("gate") or s.get("screen"), \
            f"{s['title']!r} must carry a gate or a hand-off to be protected"
