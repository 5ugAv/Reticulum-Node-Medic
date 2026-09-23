"""The chooser's ? is in the sentence that names it, and the cards scroll.

Operator photo, 2026-09-23: "Not sure which is which? Tap the ? above." —
and no ? above it. The bottom bar took 34 dp, the fixed stack overflowed,
and the title row carrying the ? was pushed off the top of the glass while
the sentence still promised it. A promise about a control must be drawn
next to that control, and a list of cards must scroll rather than clip.
"""
from tests.srcutil import func_source

GUIDE = "ui/screens/birth_guide_screen.py"


def _intro():
    body = func_source(GUIDE, "_render_intro", cls="BirthGuideScreen")
    return "\n".join(l for l in body.splitlines() if not l.strip().startswith("#"))


def test_the_question_mark_follows_its_own_sentence():
    body = _intro()
    assert 'tr("Not sure which is which? Tap the ?")' in body
    assert "above" not in body.split('tr("Not sure which is which?')[1][:120]
    hint = body[body.index("hint = BoxLayout"):body.index("wrap.add_widget(hint)")]
    assert "HelpButton()" in hint, "the ? sits in the hint row, after the words"
    head = body[body.index("head = BoxLayout"):body.index("wrap.add_widget(head)")]
    assert "HelpButton" not in head, "no second ? in the title row"


def test_the_cards_scroll_so_no_height_can_clip_them():
    body = _intro()
    assert "ScrollView(" in body
    for card in ("self._path_button(", "self._mitosis_button()", "self._over_air_button()"):
        assert f"col.add_widget({card}" in body, card
    assert "wrap.add_widget(cards)" in body
