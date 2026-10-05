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


# --- 5. the escape hatch cannot fire once a read is already under way -----
#
# Operator, 2026-09-07, birthing a Pi + a fresh RNode: chose "pi", chose
# "Flash a radio now", attached the antenna, plugged the board in, watched it
# celebrate as "connected" — and was dropped back on "What are you building?"
# with no explanation. ui.log's last line was "chose build 'pi'" with NOTHING
# after it — because _choose_manually calls _render_intro() straight through,
# with no _trace call of its own on the success path.
#
# _on_detect marks the board connected and then WAITS 1.6s before replacing
# the screen with "Reading the board…" — and "Choose manually" sits on that
# same screen, live, for the whole of that wait. A tap to acknowledge
# "Connected!" (the natural reflex after a success animation) lands in that
# exact corner and throws the chosen build away. The existing 0.5s
# bleed-through guard does not cover this: by the time a board is plugged in
# and read, the detect screen has been up far longer than 0.5s.

def test_choose_manually_is_inert_once_a_board_read_is_pending():
    guard = textwrap.dedent(func_source(SCREEN, "_choose_manually"))
    assert "_reading_pending" in guard, \
        "the guard must know a board has already been seen and is being read"
    assert "return" in guard.split("_reading_pending", 1)[1].split("_detect_shown_at")[0], \
        "a tap that arrives once a read is pending must be dropped, not merely noted"
    # and checked FIRST — a pending read outranks the 0.5s bleed-through window
    assert guard.index("_reading_pending") < guard.index("_detect_shown_at"), \
        "check the pending-read guard before the arrival-time guard"


def test_on_detect_sets_the_flag_the_escape_hatch_now_checks():
    on_detect = textwrap.dedent(func_source(SCREEN, "_on_detect"))
    assert "self._reading_pending = True" in on_detect, \
        "the flag _choose_manually now trusts must actually be set the moment a board appears"


def test_a_stray_choose_manually_tap_now_leaves_a_trace_too():
    """The night this was reported, ui.log had 'chose build pi' and then
    nothing — the operator's account was all there was to go on. The success
    path through this escape hatch must trace too, not just the guarded ones."""
    guard = textwrap.dedent(func_source(SCREEN, "_choose_manually"))
    assert guard.count("_trace") >= 2, \
        "both guarded returns must trace — a silent one is how this bug hid"


# --- the trail the next report will be read from -------------------------

def test_choosing_a_build_and_resetting_both_leave_a_line_in_the_log():
    """The night this was reported there was nothing in ui.log covering the
    chooser or the detect landing — the walkthrough only traced itself from
    _next onwards, so the account was all there was to go on."""
    assert "_trace" in textwrap.dedent(func_source(SCREEN, "_choose"))
    assert "_trace" in textwrap.dedent(func_source(SCREEN, "reset"))
    assert "_trace" in textwrap.dedent(func_source(SCREEN, "_route"))


# --- 6. a fresh lap does not inherit the last one's rebirth ----------------
# Operator, walking a fresh Heltec V4 through its FIRST birth of the lap,
# 2026-09-07: the name step announced "This board was Brick. It's blank now."
# about a board that had never been wiped and had nothing to do with that
# name. _rebirth_of is set when a board IS wiped, and was never cleared
# anywhere — so after one rebirth in a session, every later birth claimed to
# be a rebirth of that same old name. A screen stating hardware history it
# cannot know: the same class of fault as the false-sentences audit.

def test_a_fresh_walkthrough_clears_the_rebirth_name():
    reset = textwrap.dedent(func_source(SCREEN, "reset"))
    assert 'self._rebirth_of = ""' in reset, (
        "a fresh lap must not inherit the previous lap's wiped-board name")


def test_the_name_step_only_claims_a_rebirth_when_there_was_one():
    name = textwrap.dedent(func_source(SCREEN, "_render_name"))
    assert 'was = getattr(self, "_rebirth_of", "")' in name
    assert "if was:" in name, "the history sentence is conditional, not default"


# --- 5. the REBIRTH road obeys the same rule ------------------------------
# Operator, with a photo, 2026-09-08, birthing an Ebyte EoRa-S3 that was
# already a provisioned RNode: "this is the second time I've been asked in
# this process ... we shouldn't need this page here after the board's been
# read." The board being already provisioned sent the lap down the rebirth
# road, and that road landed on the chooser unconditionally.
#
# It was not a careless line. It was written 2026-08-06, when a rebirth was
# entered directly and the node's type genuinely was an open question, and it
# was written BECAUSE an operator asked for the visible options. On 2026-08-26
# the chooser became the first birth screen, and that quietly made this a
# second asking of a question already answered one screen earlier.

def test_rebirth_does_not_re_ask_a_build_chosen_this_lap():
    reb = textwrap.dedent(func_source(SCREEN, "_do_rebirth"))
    assert 'self._path in ("host", "radio", "pi")' in reb, \
        "an answer given on the chooser THIS lap is current — carry it"
    chosen = reb.index('self._path in ("host", "radio", "pi")')
    chooser = reb.index("_render_intro(builds_only=True)")
    assert chosen < chooser, \
        "the chooser is the fallback, not the default, on the rebirth road too"


def test_rebirth_still_asks_when_nothing_was_chosen():
    """A rebirth reached WITHOUT the chooser is a real question - keep asking.

    The 2026-08-06 request stands for that case; only the redundant re-ask
    goes away.
    """
    reb = textwrap.dedent(func_source(SCREEN, "_do_rebirth"))
    assert "_render_intro(builds_only=True)" in reb, \
        "do not delete the chooser - it is still right when nothing is chosen"


def test_both_rebirth_outcomes_leave_a_line_in_the_log():
    """This was diagnosed from ui.log showing a single 'chose build' line and
    nothing after it. Whichever way the rebirth goes, say so."""
    reb = textwrap.dedent(func_source(SCREEN, "_do_rebirth"))
    branch = reb[reb.index('self._path in ("host", "radio", "pi")'):]
    assert branch.count("self._trace(") >= 2, \
        "trace BOTH roads or the next report is unanswerable again"


# --- 6. the existing-node screen mirrors the RNode one --------------------
# Operator, with photos, 2026-09-09, on an EoRa-S3 already running as an
# RTNode: "the buttons on this screen should mirror the buttons on the screen
# when we're wiping an RNode that's already an RNode ... it should be red and
# it should say wipe", and then, having pressed it: "instead of saying keep
# this one it's taking me back [to the] selection page ask me what I want to
# birth and the first step was I've already chosen ... an RTNode".
#
# Two faults in one button. It looked harmless — plain text beside the green
# action, nothing marking it destructive — and it did not re-birth anything:
# it called _render_intro(), throwing the walkthrough back to the chooser.

def test_the_existing_node_screen_actually_rebirths():
    """It must run the real rebirth, not navigate to the chooser."""
    src = textwrap.dedent(func_source(SCREEN, "_render_adopt"))
    assert "_confirm_rebirth(c)" in src, \
        "the rebirth button must run the rebirth, not re-ask the question"
    assert "self._render_intro()" not in src, \
        "sending the operator back to 'What are you building?' is the bug"


def test_it_is_marked_destructive_like_the_rnode_screen():
    """Same wording and same warning colour as the already-an-RNode screen."""
    src = textwrap.dedent(func_source(SCREEN, "_render_adopt"))
    assert 'tr("Rebuild — wipe this node & build it fresh")' in src, \
        "reuse the RNode screen's exact words (also: already translated)"
    assert 'COLORS["amber"]' in src, "a destructive action must not read as neutral"


def test_the_two_screens_use_the_same_rebirth_control():
    """Pin the mirroring itself: if one screen's control changes, this fails."""
    adopt = textwrap.dedent(func_source(SCREEN, "_render_adopt"))
    for token in ('tr("Rebuild — wipe this node & build it fresh")',
                  'COLORS["amber"]', "_confirm_rebirth(c)"):
        assert token in adopt, token
