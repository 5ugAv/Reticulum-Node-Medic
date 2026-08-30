"""BIRTH asks what you are building ONCE.

Operator, 2026-08-30, birthing a Heltec Wireless Tracker as an RNode: they
tapped "A radio for phone or computer (RNode)", read the antenna warning,
connected the board, watched the medic read it — and were then dropped back on
"What are you building?" with the whole lap to walk again. Reliably, every
time: choose, antenna, connect, chooser; choose, antenna, connect, and only
then the name step.

Four ways the guide could take a screen away from the operator were found by
driving the real screen against a stand-in Kivy. Each is pinned here:

  1. the chooser cleared its widgets without stopping the previous screen's
     watchers, so the detect step's board poll went on ticking underneath it
     and — with a board plugged in, which is exactly the state that screen
     leaves behind — replaced the question with "Reading the board…";
  2. "Choose manually" is built in the same corner as the "Antenna on →" that
     opened the detect screen, so a press bleeding through the render threw the
     chosen build away;
  3. the board read is a thread the better part of a minute long and answered
     to nobody, so a read from an abandoned walkthrough rendered over whatever
     screen the operator had reached since;
  4. a Pi sighting could rewrite a chosen build whose key was neither "host"
     nor "radio".

Source-level pins: the screen is Kivy, which does not import in CI.
"""

import textwrap

from tests.srcutil import func_source

SCREEN = "ui/screens/birth_guide_screen.py"


# --- 1. the chooser stands still ------------------------------------------

def test_the_chooser_stops_the_previous_screens_watchers():
    """Every other render here sweeps first. The chooser did not, so a poll
    from the screen before it kept driving the flow from underneath."""
    intro = textwrap.dedent(func_source(SCREEN, "_render_intro"))
    assert "self._stop_current()" in intro
    assert intro.index("self._stop_current()") < intro.index("self.clear_widgets()"), \
        "sweep BEFORE clearing — a poll that ticks mid-render still owns the flow"


# --- 2. the escape hatch cannot be pressed by accident ---------------------

def test_choose_manually_goes_through_the_tap_guard():
    detect = textwrap.dedent(func_source(SCREEN, "_render_detect"))
    assert "on_next=self._choose_manually" in detect, \
        "the chooser escape must not be wired straight to _render_intro"
    assert "_detect_shown_at" in detect, "the guard needs to know when this screen appeared"


def test_the_tap_guard_ignores_a_press_that_arrives_with_the_screen():
    guard = textwrap.dedent(func_source(SCREEN, "_choose_manually"))
    assert "_detect_shown_at" in guard
    assert "return" in guard.split("_detect_shown_at", 1)[1], \
        "a press inside the window must be dropped, not merely noted"
    assert "_render_intro" in guard, "a real press still reaches the chooser"


# --- 3. no read from an abandoned walkthrough may draw --------------------

def test_the_board_read_records_which_walkthrough_it_belongs_to():
    reading = textwrap.dedent(func_source(SCREEN, "_render_reading"))
    assert "self._read_gen = getattr(self, \"_nav_token\", 0)" in reading, \
        "the read must carry the navigation generation it was started in"
    assert "self._read_path = self._path" in reading, \
        "the read must carry the build that was chosen when it started"
    # taken AFTER the sweep, or the render invalidates its own read
    assert reading.index("self._stop_current()") < reading.index("self._read_gen"), \
        "capture the generation after _stop_current, not before"
    assert "gen, chose = self._read_gen, self._read_path" in reading, \
        "the worker thread must close over them, not re-read them when it finishes"
    assert "self._route(c, gen, chose)" in reading


def test_a_stale_read_is_dropped_instead_of_rendering():
    route = textwrap.dedent(func_source(SCREEN, "_route"))
    assert "def _route(self, c, gen=None, chose=None)" in route
    guard = route.index("gen != getattr(self, \"_nav_token\", 0)")
    assert "return" in route[guard:guard + 200], \
        "a read whose walkthrough has been left must draw nothing at all"
    # and the drop comes FIRST — before any branch that renders
    assert guard < route.index("_render_already_kin"), \
        "check for staleness before deciding what to show"


def test_stop_current_still_bumps_the_generation():
    """The whole guard rests on this: every navigation, reset() included,
    moves _nav_token on."""
    stop = textwrap.dedent(func_source(SCREEN, "_stop_current"))
    assert "self._nav_token = getattr(self, \"_nav_token\", 0) + 1" in stop
    reset = textwrap.dedent(func_source(SCREEN, "reset"))
    assert "self._stop_current()" in reset


# --- the chosen build survives the read ----------------------------------

def test_the_read_never_re_asks_a_build_the_operator_already_chose():
    route = textwrap.dedent(func_source(SCREEN, "_route"))
    assert "path = self._path or chose" in route, \
        "a reset mid-read must not turn a chosen build into an unanswered one"
    assert 'path in ("host", "radio", "pi")' in route
    chooser = route.index("self._render_intro()")
    chosen = route.index('path in ("host", "radio", "pi")')
    assert chosen < chooser, \
        "the chooser is the LAST resort — only when no build was ever chosen"


def test_the_chosen_build_is_written_back_before_the_name_step():
    route = textwrap.dedent(func_source(SCREEN, "_route"))
    branch = route[route.index('path in ("host", "radio", "pi")'):]
    assert "self._path = path" in branch
    assert branch.index("self._path = path") < branch.index("_render_name"), \
        "restore the path BEFORE the name step reads it (it sizes the step count)"


# --- 4. a Pi sighting never overrules a chosen build ----------------------

def test_a_pi_sighting_cannot_hijack_any_chosen_non_pi_build():
    pd = textwrap.dedent(func_source(SCREEN, "_on_pi_detected"))
    assert 'self._path and self._path != "pi"' in pd, \
        "guard by MEANING (any chosen non-Pi build), not by listing two keys"
    assert 'self._path in ("host", "radio")' not in pd


# --- the trail the next report will be read from -------------------------

def test_choosing_a_build_and_resetting_both_leave_a_line_in_the_log():
    """The night this was reported there was nothing in ui.log covering the
    chooser or the detect landing — the walkthrough only traced itself from
    _next onwards, so the account was all there was to go on."""
    assert "_trace" in textwrap.dedent(func_source(SCREEN, "_choose"))
    assert "_trace" in textwrap.dedent(func_source(SCREEN, "reset"))
    assert "_trace" in textwrap.dedent(func_source(SCREEN, "_route"))
