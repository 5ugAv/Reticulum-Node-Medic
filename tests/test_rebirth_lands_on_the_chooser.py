"""After a wipe, the operator picks the node type again — visibly.

Operator, 2026-08-06, pointing at the "Birth a new node" form that followed the
wipe: "this screen is the step I want to replace with the node choice screen ...
reuse the screen that gives the user visible options of what they want to birth
'rnode, rtnode, pi+rnode'".

A rebirth is exactly when the node's type may change — the board is blank again
and could become anything it is capable of. Handing straight off to the BIRTH
form arrived with a type already chosen and only a small "change" link, so the
rebirth read as "the same thing again" and the operator had to notice a link to
escape it.

The guide already owns the screen that asks properly: _render_intro, the card
chooser. Going there instead also re-reads the board on the way in
(_paths_for_connected_board), which is right after a wipe — the options are
computed from what the board is NOW, not from what it was when the guide was
first entered.

Compiles the shipped function and runs it against a stub self, because importing
the screen pulls in Kivy and breaks collection (tests/srcutil idiom).
"""
import textwrap
import types

from tests.srcutil import func_source

SCREEN = "ui/screens/birth_guide_screen.py"


def _wipe_done_source():
    """The wipe handler and its nested done(), as shipped."""
    return textwrap.dedent(func_source(SCREEN, "_do_rebirth"))


def test_the_wipe_no_longer_hands_off_to_the_birth_form():
    """_on_complete is the hand-off that skipped the question."""
    src = _wipe_done_source()
    ok_branch = src[src.index("if ok:"):]
    hand_off = ok_branch.find("_on_complete")
    chooser = ok_branch.find("_render_intro")
    assert chooser != -1, "the wipe must land on the card chooser"
    assert hand_off == -1 or chooser < hand_off, (
        "the success path still hands off to the BIRTH form instead of asking")


def test_the_old_name_is_NOT_carried_to_the_name_step():
    """REVERSED 2026-08-07, by operator decision — and the reasoning it replaces
    is kept because it was not silly.

    The old assertion argued: "reusing the name is legitimate and common — a
    repair keeps its identity. Losing it here would force the operator to retype
    it." True as far as it goes, but it optimised for typing and ignored what
    the reused name DOES. The certificate id derives from the node name, so
    saving under the old name overwrites the previous certificate — born date,
    stamped location and notes — with no warning at all.

    Then the operator met it: "It says wiping rak3 ... but I'm not prompted to
    change the name from rak3." Nothing marked the field as a decision, so the
    destructive answer was the one that happened by default.

    The default in the box now has to be safe when nobody reads it. It offers
    the next free name; the old one is shown alongside as history, so nothing
    is lost and retyping is still not required."""
    src = _wipe_done_source()
    flat = src.replace(" ", "")
    assert "_node_name=old_name" not in flat, "the old name is back as the default"
    assert "rebirth_default_name(old_name" in flat
    assert "_rebirth_of=old_name" in flat, "the old name must survive as history"


def test_the_pi_pair_check_is_re_armed():
    """_pair_checked gates the Pi path's power/compatibility check. Left True
    from the previous lap, a rebirth into Pi + RNode would skip it and walk
    into the card write with no compatibility check at all."""
    src = _wipe_done_source()
    ok_branch = src[src.index("if ok:"):]
    assert "_pair_checked" in ok_branch, "the pair check was not re-armed"
    assert "False" in ok_branch[ok_branch.index("_pair_checked"):
                                ok_branch.index("_pair_checked") + 40]


def test_the_step_index_is_reset():
    """_i survives on the screen object; a stale index would drop the operator
    part-way into the previous walkthrough."""
    ok_branch = _wipe_done_source()
    ok_branch = ok_branch[ok_branch.index("if ok:"):]
    assert "_i = 0" in ok_branch


def test_the_failure_path_is_untouched():
    """A failed wipe must still say so and go back to detect — not silently
    present a chooser for a board that was never wiped."""
    src = _wipe_done_source()
    assert "Couldn't wipe the board" in src
    assert "_render_detect()" in src


# -- the rebirth chooser is narrower than the normal one ----------------------

def test_the_rebirth_asks_the_builds_only_question():
    """Operator: "this screen, except without the mitosis or over air options".
    After a wipe the question is only "what shall THIS board become?" — cloning
    the medic and adopting a node over LoRa are neither, and a mis-tap there
    abandons a board mid-rebirth."""
    src = _wipe_done_source()
    ok_branch = src[src.index("if ok:"):]
    assert "_render_intro(builds_only=True)" in ok_branch


def test_the_intro_can_hide_mitosis_and_over_the_air():
    intro = textwrap.dedent(func_source(SCREEN, "_render_intro"))
    assert "builds_only" in intro.split("\n")[0], "no builds_only parameter"
    guard = intro.index("if not builds_only:")
    assert intro.index("_mitosis_button") > guard, "mitosis is not gated"
    assert intro.index("_over_air_button") > guard, "over-the-air is not gated"


def test_the_normal_chooser_still_offers_everything():
    """builds_only defaults False — the ordinary BIRTH entry is unchanged."""
    intro = textwrap.dedent(func_source(SCREEN, "_render_intro"))
    assert "builds_only=False" in intro.split("\n")[0]


def test_impossible_builds_are_still_dropped_by_the_board_read():
    """The operator's other condition: "if the board is not suitable for rtnode
    that should not be shown either". That filtering is _paths_for_connected_board,
    which the chooser calls — builds_only must not bypass it."""
    intro = textwrap.dedent(func_source(SCREEN, "_render_intro"))
    assert "_paths_for_connected_board()" in intro
    assert intro.index("_paths_for_connected_board()") < intro.index(
        "if not builds_only:"), "the board read must still happen"
