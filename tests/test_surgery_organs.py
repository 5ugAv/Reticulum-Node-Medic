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


# --- the monitor has to STOP -----------------------------------------------
# Operator, watching a finished write on the live screen (2026-08-07):
#   "the heartbeat has been going all through the load — let's get rid of the
#    heartbeat to let the user know that this is actually finished."
# _done() called set_fraction(1.0) and nothing else, so Clock kept ticking and
# the trace scrolled for ever. A monitor that never stops says the operation
# never ended. CI has no Kivy, so like the tests above these assert the source
# contract rather than the draw path.

def _surgery_src():
    return open("ui/widgets/surgery_anim.py").read()


def test_finishing_stops_the_free_running_clock():
    """Whatever else finish() does, the ticking must end — that IS the fix."""
    src = _surgery_src()
    body = src[src.index("def finish(self)"):src.index("# -- drawing")]
    assert "self.stop()" in body, "finish() never stops the clock"


def test_the_monitor_never_flatlines():
    """A flat green line is the most legible image in medicine and it means the
    patient died — the exact opposite of what this screen is reporting. The
    trace must FADE while still beating, never be zeroed."""
    src = _surgery_src()
    mon = src[src.index("def _draw_monitor"):src.index("def _draw_discharged")]
    # the fade multiplies alpha; it must not touch the amplitude
    assert "(1.0 - gone)" in mon, "the trace is not faded out"
    # the trace itself moved to ui.surgery_layout (2026-09-22) so the
    # previewer draws the same one; the amplitude formula lives there now,
    # and tests/test_surgery_layout.py checks the beat is never flat
    trace = open("ui/surgery_layout.py").read()
    assert "amp = h * (0.16 + 0.26 * steady)" in trace, (
        "amplitude changed — check it is not being driven to zero (flatline)")


def test_the_tick_waits_for_the_trace_to_clear():
    """Found by rendering it offline before it ever reached the medic: the tick
    came up over the QRS spike and the two green shapes crossed. It is held
    back until the trace has mostly gone."""
    src = _surgery_src()
    d = src[src.index("def _draw_discharged"):]
    assert "p = (t - 0.45) / 0.55" in d, "the tick no longer waits"
    assert "if p <= 0.0:\n            return" in d


def test_only_a_SUCCESSFUL_write_is_discharged():
    """A failure gets stop() — no tick, no celebration. Discharging a patient
    who did not survive the operation is the worst thing this screen could
    say."""
    src = open("ui/screens/pi_imager_screen.py").read()
    done = src[src.index("def _done(self, ok, msg)"):]
    done = done[:done.index("lbl = getattr(self, \"_stage_lbl\"")]
    assert "surgery.finish()" in done
    ok_at, fail_at = done.index("surgery.finish()"), done.index("surgery.stop()")
    assert ok_at < fail_at, "finish() must be the ok branch, stop() the failure"
