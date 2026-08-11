"""The nine-dot pad's geometry — the part that must work with cold hands."""

import pytest

from ui.pattern_geometry import (
    COLS, ROWS, dot_at, dot_centres, extend, hit_radius)


def test_dot_zero_is_top_left_not_bottom_left():
    """Kivy's y grows UPWARD. Reading row 0 as the bottom row mirrors every
    pattern vertically: the operator draws what they always drew, the vault
    refuses them, and nothing on screen explains why."""
    centres = dot_centres(0, 0, 300, 300)
    assert len(centres) == 9
    top_left, top_right = centres[0], centres[2]
    bottom_left = centres[6]
    assert top_left[1] > bottom_left[1]        # dot 0 is ABOVE dot 6
    assert top_right[0] > top_left[0]          # dot 2 is RIGHT of dot 0
    assert centres[4] == (150.0, 150.0)        # dot 4 is the middle


def test_dots_are_evenly_spaced_within_the_box():
    centres = dot_centres(10, 20, 300, 300)
    xs = sorted({round(c[0], 3) for c in centres})
    ys = sorted({round(c[1], 3) for c in centres})
    assert len(xs) == COLS and len(ys) == ROWS
    assert round(xs[1] - xs[0], 3) == round(xs[2] - xs[1], 3)
    assert round(ys[1] - ys[0], 3) == round(ys[2] - ys[1], 3)
    # and nothing escapes the box
    for cx, cy in centres:
        assert 10 <= cx <= 310 and 20 <= cy <= 320


def test_a_touch_on_a_dot_finds_it():
    for i, (cx, cy) in enumerate(dot_centres(0, 0, 300, 300)):
        assert dot_at(cx, cy, 0, 0, 300, 300) == i


def test_a_touch_between_dots_belongs_to_nobody():
    """A pad that grabs the nearest dot however far away would silently collect
    dots a diagonal passed near — and the operator only finds out when the
    vault will not open."""
    assert dot_at(100, 100, 0, 0, 300, 300) is None      # a cell corner
    assert dot_at(150, 100, 0, 0, 300, 300) is None      # between 4 and 7


def test_the_target_is_a_fingertip_and_never_touches_its_neighbour():
    r = hit_radius(300, 300)
    cell = 100.0
    assert 0 < r * 2 < cell, "targets must not overlap"
    assert r * 2 == pytest.approx(cell * 0.5)
    # just inside and just outside the target, on the same dot
    assert dot_at(150 + r * 0.9, 150, 0, 0, 300, 300) == 4
    assert dot_at(150 + r * 1.1, 150, 0, 0, 300, 300) is None


def test_a_dragged_finger_reports_each_dot_once():
    """One finger crossing a dot fires many touch events, and drifts back over
    dots it already used. Both mean the same thing: the path is unchanged."""
    path = []
    for dot in (0, 0, 0, None, 4, 4, None, 8, 4, 0):
        path = extend(path, dot)
    assert path == [0, 4, 8]


def test_extend_never_mutates_the_path_it_was_given():
    original = [0, 4]
    extended = extend(original, 8)
    assert original == [0, 4] and extended == [0, 4, 8]


def test_a_non_square_pad_still_makes_round_targets():
    """The lock screen is portrait; the pad may not get a square box. The hit
    radius has to come from the SMALLER cell or the targets overlap on one
    axis and a single touch could belong to two dots."""
    r = hit_radius(300, 600)
    assert r * 2 == pytest.approx(100.0 * 0.5)   # from the 300-wide cells
    assert dot_at(150, 300, 0, 0, 300, 600) == 4
