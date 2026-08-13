"""The map-sharing answer is ONE switch, and it rests on hidden.

Asked for by the operator, 2026-08-11: "the keep it hidden or show it roughly
options on birth process should be a toggle switch instead of two separate
buttons. That way it's clear for the user to know where it sits. left for...
keep it hidden, right for show it roughly."

Two buttons never showed WHERE the answer sat — they showed two things you
could press, which is a different question from what this node is going to do.
A switch has a position, and a position can be read across a bench.

What these tests are actually guarding is the direction of the failure. A
switch has one end that publishes a position it can never take back, so:

  * left is hidden and left is where it rests, in both places it appears;
  * a touch on the left half can never turn sharing on, whatever it was on;
  * an unreadable stored value puts it on the left, not on the right;
  * the sentence under it is the model's own words about what a stranger gets,
    and it follows the switch rather than being typed twice beside it.

Kivy cannot open a window on a dev Mac (and CI has no Kivy at all), so nothing
here renders. The widget's *decisions* are reachable without a window —
classmethods and stub-self calls — and the wiring in the two screens is read
out of the shipped source, which is this suite's established way of testing a
screen it cannot instantiate (tests/srcutil.py).
"""

import textwrap
import types

import pytest

from monitor import location_share as ls
from tests.srcutil import func_source, src

# declared synthetic coordinates (see tests/test_privacy_tree.py)
LAT, LON = -37.512345, 145.523456

GUIDE = "ui/screens/birth_guide_screen.py"
PANEL = "ui/widgets/map_sharing.py"


# --- the sentence under the switch ------------------------------------------

def test_hidden_says_nothing_leaves_the_medic():
    line = ls.consequence_line(ls.HIDDEN, "NODE", LAT, LON, "NODE")
    assert "Hidden" in line
    assert "Node Medic" in line
    # and no coordinate of any kind, real or fuzzed
    assert "37.5" not in line and "145.5" not in line


def test_shared_names_everything_in_the_packet_not_just_the_position():
    """Same contract as stranger_view, because it IS stranger_view: the line is
    composed from the model rather than written again beside the switch."""
    line = ls.consequence_line(ls.APPROX, "NODE", LAT, LON, "NODE").lower()
    assert "name" in line
    assert "radio settings" in line
    assert "transport identity" in line
    assert "800" in line


def test_the_line_changes_when_the_switch_moves():
    a = ls.consequence_line(ls.HIDDEN, "NODE", LAT, LON, "NODE")
    b = ls.consequence_line(ls.APPROX, "NODE", LAT, LON, "NODE")
    assert a != b


def test_the_point_it_quotes_is_the_fuzzed_one_never_the_real_one():
    """The line is shown next to a switch somebody is about to flip. If it
    printed the true coordinates the screen would be teaching the operator the
    thing the whole feature exists to withhold."""
    line = ls.consequence_line(ls.APPROX, "NODE", LAT, LON, "NODE")
    assert "-37.512345" not in line and "145.523456" not in line
    flat, flon, _r = ls.public_pin(LAT, LON, "NODE")
    from monitor.geo import format_coord
    assert format_coord(flat) in line and format_coord(flon) in line


def test_no_coordinates_means_no_point_is_quoted():
    line = ls.consequence_line(ls.APPROX, "NODE", None, None, "NODE")
    assert "point announced" not in line.lower()


# --- the switch itself -------------------------------------------------------

def _toggle_module():
    """The widget module. Importing it is safe without a display — only
    building a widget needs a Window — but CI has no Kivy at all."""
    return pytest.importorskip("ui.widgets.share_toggle",
                               reason="Kivy not installed")


def test_left_is_hidden_and_right_is_shown():
    st = _toggle_module().ShareToggle
    assert st.STATES == (ls.HIDDEN, ls.APPROX)


def test_the_switch_rests_on_hidden_when_nobody_has_answered():
    st = _toggle_module().ShareToggle
    assert st.normalise(None) == ls.HIDDEN
    assert st.STATES[0] == ls.HIDDEN


def test_an_unreadable_setting_puts_it_on_the_left():
    """Same rule as the model: a value this tool cannot read is not consent."""
    st = _toggle_module().ShareToggle
    for value in ("", "yes", "true", "SHARE", None, 0):
        assert st.normalise(value) == ls.HIDDEN


def test_a_touch_picks_the_side_it_landed_on():
    mod = _toggle_module()
    assert mod.side_at(0.0) == ls.HIDDEN
    assert mod.side_at(0.49) == ls.HIDDEN
    assert mod.side_at(0.5) == ls.APPROX
    assert mod.side_at(1.0) == ls.APPROX


def test_a_tap_on_the_hidden_half_can_never_turn_sharing_on():
    """The reason this switch picks a SIDE instead of flipping: a flip means a
    mis-tap anywhere on the control publishes a position. Here the left half is
    always the safe half, whatever the switch was showing."""
    st = _toggle_module().ShareToggle
    stub = types.SimpleNamespace(x=0.0, width=200.0, state=ls.APPROX)
    touch = types.SimpleNamespace(x=20.0, y=5.0)
    assert st.target_state(stub, touch) == ls.HIDDEN


def test_the_front_page_toggle_still_falls_to_backpack():
    """The generalisation must not have changed what the OTHER switch means. An
    unreadable mode there is backpack — a mobile leaf disturbs no mesh, which is
    that widget's safe end, and it is the opposite end from this one's."""
    mt = pytest.importorskip("ui.widgets.mode_toggle", reason="Kivy not installed")
    assert mt.ModeToggle.normalise("") == mt.BACKPACK
    assert mt.ModeToggle.normalise(mt.HOME) == mt.HOME


def test_an_idempotent_tap_asks_for_nothing():
    """Tapping the side it already sits on must not re-fire the callback: on the
    node-detail panel that callback writes the registry and clears the
    "written to the node" stamp, so a second tap on the same side would report
    an applied node as unapplied."""
    mod = _toggle_module()
    target = _compile_method("ui/widgets/mode_toggle.py", "touch_target",
                             cls="TwoStateToggle")
    stub = types.SimpleNamespace(
        _busy=False, _tap_slop=14.0, state=ls.HIDDEN,
        collide_point=lambda *a: True,
        target_state=lambda touch: mod.side_at(0.1))
    touch = types.SimpleNamespace(x=10.0, y=10.0, ox=10.0, oy=10.0, pos=(10.0, 10.0))
    assert target(stub, touch) is None


def test_a_drag_across_the_switch_is_not_a_tap():
    """The panel it sits on scrolls. A finger that travelled is a scroll, not an
    answer about publishing a position."""
    target = _compile_method("ui/widgets/mode_toggle.py", "touch_target",
                             cls="TwoStateToggle")
    stub = types.SimpleNamespace(
        _busy=False, _tap_slop=14.0, state=ls.HIDDEN,
        collide_point=lambda *a: True,
        target_state=lambda touch: ls.APPROX)
    touch = types.SimpleNamespace(x=90.0, y=10.0, ox=10.0, oy=10.0, pos=(90.0, 10.0))
    assert target(stub, touch) is None


def _compile_method(path, name, cls=""):
    """One shipped method, compiled on its own so it can be run against a stub
    self — the widget cannot be instantiated without a Window."""
    ns = {"types": types}
    exec(compile(textwrap.dedent(func_source(path, name, cls=cls)), path, "exec"), ns)
    return ns[name]


# --- the birth step ----------------------------------------------------------

def _guide_method(name, extra=None):
    ns = dict(extra or {})
    source = textwrap.dedent(func_source(GUIDE, name))
    exec(compile(source, GUIDE, "exec"), ns)
    return ns[name]


def _share_screen_stub(policy=ls.HIDDEN, name="NODE"):
    """A stand-in for the screen, carrying the widgets the two methods touch."""
    screen = types.SimpleNamespace(
        _share_pending=policy, _share_location=ls.HIDDEN, _share_asked=False,
        _node_name=name, _i=99, _render_step=lambda: None,
        _share_consequence=types.SimpleNamespace(text=""),
        _share_commit=types.SimpleNamespace(text="", background_color=None,
                                            color=None))
    show = _guide_method("_share_show", _guide_globals())
    screen._share_show = lambda: show(screen)
    return screen


def _guide_globals():
    from ui import theme
    from ui.birth_guide_flow import LOCATION_SHARE_STEP
    return {"theme": theme, "LOCATION_SHARE_STEP": LOCATION_SHARE_STEP}


def test_moving_the_switch_rewrites_the_consequence_underneath_it():
    pytest.importorskip("ui.theme", reason="Kivy not installed")
    moved = _guide_method("_share_moved", _guide_globals())
    screen = _share_screen_stub()
    moved(screen, ls.APPROX)
    assert screen._share_consequence.text == ls.consequence_line(ls.APPROX, "NODE")
    moved(screen, ls.HIDDEN)
    assert screen._share_consequence.text == ls.consequence_line(ls.HIDDEN, "NODE")


def test_the_sentence_underneath_follows_the_switch():
    """Since the 2026-08-12 redesign there is no commit button — the operator
    found a button that named an end and also advanced 'confusing UI'. The
    switch's position is the answer, the model's sentence under it says what
    that position does, and the ordinary Next commits. The sentence must
    follow the switch as it moves."""
    pytest.importorskip("ui.theme", reason="Kivy not installed")
    moved = _guide_method("_share_moved", _guide_globals())
    screen = _share_screen_stub()
    moved(screen, ls.APPROX)
    assert screen._share_pending == ls.APPROX
    shown_when_on = screen._share_consequence.text
    moved(screen, ls.HIDDEN)
    assert screen._share_pending == ls.HIDDEN
    assert screen._share_consequence.text != shown_when_on


def test_moving_the_switch_decides_nothing_on_its_own():
    """The switch sets a position; the button below it is what commits. So a
    walkthrough left on this screen, or backed out of, carries the answer it
    arrived with — never the one a finger brushed past."""
    pytest.importorskip("ui.theme", reason="Kivy not installed")
    moved = _guide_method("_share_moved", _guide_globals())
    screen = _share_screen_stub()
    moved(screen, ls.APPROX)
    assert screen._share_location == ls.HIDDEN
    assert screen._share_asked is False


def test_the_button_commits_what_the_switch_shows():
    chosen = _guide_method("_share_chosen")
    screen = _share_screen_stub(policy=ls.APPROX)
    chosen(screen, screen._share_pending)
    assert screen._share_location == ls.APPROX
    assert screen._share_asked is True


def test_a_fresh_walkthrough_starts_the_switch_on_hidden():
    """reset() is what a new birth runs. The screen's own answer starts hidden,
    and the switch is seeded from it — so the control cannot be found resting on
    "share" because of the node built before this one."""
    reset = func_source(GUIDE, "reset")
    assert "self._share_location = location_share.HIDDEN" in reset
    render = func_source(GUIDE, "_render_location_share")
    assert "self._share_pending = self._share_location" in render
    assert "policy=self._share_pending" in render


def test_next_commits_exactly_what_the_switches_show():
    """DECISION REVERSED by the operator, 2026-08-12, with the first version
    on the glass: "pressing 'keep it hidden' takes you to the next build
    step, this is a confusing UI. replace it with a toggle... below that a
    toggle that is Bluetooth". The screen now has the ordinary green Next,
    and the protections moved into the switches themselves: they select the
    half you touch (never flip), they rest on their quiet ends, and Next
    commits only the positions actually showing."""
    render = func_source(GUIDE, "_render_location_share")
    assert "hide_next" not in render
    assert "on_next=self._prelude_next" in render
    nxt = func_source(GUIDE, "_prelude_next")
    assert "_share_pending" in nxt
    chosen = func_source(GUIDE, "_share_chosen")
    assert "_bt_pending" in chosen and "_bluetooth_on" in chosen


# --- both places, one control ------------------------------------------------

def test_both_screens_use_the_same_switch():
    """The birth step and the node's own page must mean the same thing by the
    same control, or an operator learns it twice."""
    for path in (GUIDE, PANEL):
        text = src(path)
        assert "ShareToggle" in text, f"{path} does not use the shared switch"


def test_neither_screen_still_offers_two_buttons():
    guide = func_source(GUIDE, "_render_location_share")
    assert "def choice(" not in guide
    panel = func_source(PANEL, "__init__", cls="MapSharingPopup")
    assert "Keep it hidden" not in panel and "Show it, roughly" not in panel


def test_the_panel_reads_the_switch_back_off_the_record():
    """The node-detail panel writes through the registry, which normalises. The
    switch is re-seeded from what came back, so it shows the stored answer and
    not the one that was asked for (say-only-what-you-checked)."""
    refresh = func_source(PANEL, "_refresh", cls="MapSharingPopup")
    assert "set_state(rec.share_location)" in refresh


def test_the_panel_sentence_comes_from_the_model_too():
    refresh = func_source(PANEL, "_refresh", cls="MapSharingPopup")
    assert "consequence_line" in refresh


# --- the on/off slot switch (Bluetooth at birth) ----------------------------

def test_onoff_rests_on_off_whatever_was_stored():
    """An unreadable stored value must land on the end that emits and drains
    nothing — the same direction-of-failure rule as the share switch."""
    from ui.widgets import share_toggle as st_mod
    onoff = st_mod.OnOffToggle
    assert onoff.STATES[0] == "off"
    for junk in (None, "", "maybe", 3, False):
        assert onoff.normalise(junk) == "off", junk
    assert onoff.normalise(True) == "on"        # a stored boolean is honoured
    assert onoff.normalise("on") == "on"


def test_a_tap_on_the_off_half_can_never_turn_bluetooth_on():
    from ui.widgets import share_toggle as st_mod
    stub = types.SimpleNamespace(x=0.0, width=200.0, state="on")
    touch = types.SimpleNamespace(x=20.0, y=5.0)
    assert st_mod.OnOffToggle.target_state(stub, touch) == "off"
    touch_right = types.SimpleNamespace(x=180.0, y=5.0)
    assert st_mod.OnOffToggle.target_state(stub, touch_right) == "on"


def test_the_two_slot_switches_share_one_drawing():
    """Two switches drawn twice is two spines: the slot geometry, colours and
    caption contrast must come from SlotToggle alone."""
    text = src("ui/widgets/share_toggle.py")
    import re
    assert len(re.findall(r"def _redraw", text)) == 1


def test_the_prelude_knob_moves_when_the_answer_does():
    """On the glass the sliders looked DEAD (operator, rebirth of EVERYWHERE,
    2026-08-14): the tap fired, the pending answer and the sentence flipped —
    but nothing ever told the KNOB, so it sat on the resting end while the
    sentence said Shared. The prelude handlers must re-seat their switches;
    the node-detail panel keeps its deliberate stored-answer re-seat."""
    moved = _guide_method("_share_moved", _guide_globals())
    screen = _share_screen_stub()
    calls = []
    screen._share_toggle = types.SimpleNamespace(
        set_state=lambda s: calls.append(s))
    moved(screen, ls.APPROX)
    assert calls == [ls.APPROX], "the knob was never told"
    from tests.srcutil import func_source
    bt = func_source(GUIDE, "_bt_moved")
    assert "set_state" in bt, "the Bluetooth knob was never told either"
