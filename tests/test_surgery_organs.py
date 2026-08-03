"""The operating-theatre organ art.

Written because a careless edit deleted _ORGAN_SEATS and left _CARD_WINDOW
undefined, and the whole suite still passed — nothing exercises the Kivy draw
path, so a NameError sat there waiting for the operator to open the screen
(2026-08-04). These assert the DATA the drawing depends on.
"""

import os

from ui.organ_art import (CARD_WINDOW as _CARD_WINDOW, ORGAN_DIR as _ORGAN_ART,
                          ORGANS as _ORGANS, ORGAN_SEATS as _ORGAN_SEATS,
                          SPARE_ORGANS as _SPARE_ORGANS)


def test_every_organ_has_a_seat_and_a_sprite_on_disk():
    assert set(_ORGANS) == set(_ORGAN_SEATS), "an organ has nowhere to sit"
    for key, (fname, colour, label) in _ORGANS.items():
        path = os.path.normpath(os.path.join(_ORGAN_ART, fname))
        assert os.path.exists(path), f"{key}: {fname} missing from disk"
        assert len(colour) == 4, f"{key}: fallback colour must be RGBA"


def test_the_organs_match_the_write_stages():
    """The art narrates flash(); if the stages change, the art must follow or
    the animation describes an operation that is not happening."""
    from provisioning.pi_imager import IMAGING_STAGES
    stage_keys = {s["organ"] for s in IMAGING_STAGES}
    assert stage_keys == set(_ORGANS), (
        f"stages {stage_keys} vs art {set(_ORGANS)}")


def test_the_seats_are_inside_the_card_window():
    x0, y0, x1, y1 = _CARD_WINDOW
    assert 0.0 <= x0 < x1 <= 1.0 and 0.0 <= y0 < y1 <= 1.0
    for key, (fx, fy) in _ORGAN_SEATS.items():
        assert 0.0 <= fx <= 1.0 and 0.0 <= fy <= 1.0, f"{key} sits outside"


def test_the_seats_run_in_stage_order_left_to_right():
    """They land one at a time; a row that jumps about reads as scattered."""
    from provisioning.pi_imager import IMAGING_STAGES
    xs = [_ORGAN_SEATS[s["organ"]][0] for s in IMAGING_STAGES]
    assert xs == sorted(xs), f"seats not in stage order: {xs}"


def test_the_spare_sprites_are_kept_not_referenced():
    """message/wifi were drawn by the operator but have no stage yet."""
    for fname in _SPARE_ORGANS:
        assert os.path.exists(os.path.normpath(os.path.join(_ORGAN_ART, fname)))
        assert fname not in [a[0] for a in _ORGANS.values()]
