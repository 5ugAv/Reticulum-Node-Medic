"""The 'there is more below' rule.

Imports ui.scroll_rule, NOT ui.widgets.scroll_hint: the widget pulls in
kivy.animation/kivy.properties, which the suite's process-global Kivy stubs do
not cover, and a stubbed parent package with an unstubbed child breaks the
import machinery for every test collected afterwards.

Operator, 2026-08-02: a green continue button below the fold reads as an ABSENT
button, not a hidden one — so the screen looks broken. The visibility rule is
pure and tested here; the drawing needs a window and isn't.
"""

import pytest

from ui.scroll_rule import more_below   # pure: no Kivy import


def test_no_hint_when_everything_already_fits():
    assert more_below(scroll_y=1.0, viewport_height=400, content_height=300) is False
    assert more_below(scroll_y=0.0, viewport_height=400, content_height=400) is False


def test_hint_when_content_overflows_and_we_are_at_the_top():
    """The exact case that lost the operator: a tall form, start button below."""
    assert more_below(scroll_y=1.0, viewport_height=400, content_height=900) is True


def test_no_hint_once_scrolled_to_the_bottom():
    assert more_below(scroll_y=0.0, viewport_height=400, content_height=900) is False


def test_hint_persists_part_way_down():
    assert more_below(scroll_y=0.5, viewport_height=400, content_height=900) is True


def test_a_few_stray_pixels_do_not_trigger_it():
    """Rounding leaves a pixel or two of overflow on almost every layout; a
    chevron that never goes away teaches the operator to ignore it."""
    assert more_below(scroll_y=1.0, viewport_height=400, content_height=404) is False


@pytest.mark.parametrize("scroll_y", [1.0, 0.75, 0.25])
def test_a_very_tall_page_hints_at_every_position_except_the_end(scroll_y):
    assert more_below(scroll_y, viewport_height=300, content_height=3000) is True


def test_zero_height_viewport_does_not_explode():
    assert more_below(1.0, 0, 0) is False
