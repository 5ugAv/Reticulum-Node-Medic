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
