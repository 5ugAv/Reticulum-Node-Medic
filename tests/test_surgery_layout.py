"""The operating theatre's staging: who is bigger than whom, where the arm
goes, and that none of it leaves the box it was given.

Written 2026-09-22 after the operator photographed the card-writing screen:
"just the node medic with some pink coloured lines that are supposed to
represent arms holding the SD card, throwing the icons back and forwards ...
the arms are very rudimentary." These pin the redesign's contract — a real
pick-and-place by one instrument arm, the surgeon larger than the patient, the
organ HELD from tray to seat — against the pure module both the widget and
scripts/preview_surgery.py draw from. Kivy is not importable here, so the
widget itself is checked by source inspection, as its neighbours are.
"""

import math
import os

import pytest

from tests.srcutil import src
from ui import surgery_layout as sl
from ui.organ_art import CARD_WINDOW, ORGAN_SEATS


def _arm_points(pose, lay):
    """Every point the arm occupies — a TEST helper (it lived in the module
    and the dead-code ratchet rightly asked who in production called it)."""
    return [lay.shoulder, pose.elbow, pose.sleeve, pose.sleeve2, pose.wrist,
            pose.tip, pose.tine_a, pose.tine_b]

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WIDGET = "ui/widgets/surgery_anim.py"

#: The card sprite is 1328x1003, the medic body 752x804.
CARD_ASPECT = 1328 / 1003.0
MEDIC_ASPECT = 752 / 804.0

#: The 5" panel's stage in portrait (the imager column is the panel's width
#: less its gutters, and the widget is given dp(230)); a narrow box; a short
#: one; a big one. Fractions must hold in all of them.
PANEL = (0.0, 0.0, 700.0, 230.0)
STAGES = (PANEL, (0.0, 0.0, 360.0, 230.0),
          (0.0, 0.0, 700.0, 140.0), (10.0, 20.0, 1200.0, 320.0))


def _inside(rect, stage, slack=0.5):
    x, y, w, h = rect
    sx, sy, sw, sh = stage
    return (x >= sx - slack and y >= sy - slack
            and x + w <= sx + sw + slack and y + h <= sy + sh + slack)


def _point_inside(p, stage, slack=0.5):
    sx, sy, sw, sh = stage
    return sx - slack <= p[0] <= sx + sw + slack and sy - slack <= p[1] <= sy + sh + slack


# --------------------------------------------------------------------------- #
# layout
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("stage", STAGES)
def test_the_surgeon_is_bigger_than_the_patient(stage):
    """The relationship the photo had backwards: a microSD card drawn 1.6x
    wider than the hand-held device writing it. The medic is the one doing
    the work and the hardware in the operator's hand; it is the biggest thing
    on the stage."""
    lay = sl.layout(stage, CARD_ASPECT, MEDIC_ASPECT)
    assert lay.medic[3] > lay.card[3] * 1.25, "the medic must stand taller"
    assert lay.card[2] <= lay.medic[2] * sl.CARD_OF_MEDIC + 0.5, \
        "the card has grown wider than the surgeon again"


@pytest.mark.parametrize("stage", STAGES)
def test_nothing_is_drawn_outside_the_box(stage):
    """Kivy does not clip a widget's canvas: anything past the edge is painted
    over the caption below or the callout above."""
    lay = sl.layout(stage, CARD_ASPECT, MEDIC_ASPECT)
    for name, rect in sl.rects(lay):
        assert _inside(rect, stage), f"{name} leaves the stage: {rect}"
    for p in (lay.shoulder, lay.pick, lay.park, lay.lead_pad):
        assert _point_inside(p, stage), f"{p} is off the stage"
    for p in sl.lead_points(lay):
        assert _point_inside(p, stage), f"the lead leaves the stage at {p}"


@pytest.mark.parametrize("stage", STAGES)
def test_the_scene_sits_under_the_monitor_and_the_card_on_the_table(stage):
    lay = sl.layout(stage, CARD_ASPECT, MEDIC_ASPECT)
    mon_bottom = lay.monitor[1]
    for name, rect in sl.rects(lay):
        if name != "monitor":
            assert rect[1] + rect[3] <= mon_bottom + 0.5, f"{name} crosses the monitor"
    surface = lay.table[1] + lay.table[3]
    assert lay.card[1] == pytest.approx(surface), "the patient rests ON the table"
    assert lay.tray[1] == pytest.approx(surface), "so does the tray"
    # the pedestal holds the slab up, the medic stands on the same floor
    assert lay.pedestal[1] == pytest.approx(lay.medic[1])
    assert lay.pedestal[1] + lay.pedestal[3] == pytest.approx(lay.table[1])


@pytest.mark.parametrize("stage", STAGES)
def test_the_tray_is_between_the_patient_and_the_surgeon(stage):
    """Card, tray, medic — left to right, none overlapping, all on the table
    (the tray is where the next organ waits; it must not sit on the card)."""
    lay = sl.layout(stage, CARD_ASPECT, MEDIC_ASPECT)
    cx, _, cw, _ = lay.card
    trx, _, trw, _ = lay.tray
    tx, _, tw, _ = lay.table
    assert cx + cw <= trx + 0.5
    assert trx + trw <= lay.medic[0] + 0.5
    assert tx <= cx and trx + trw <= tx + tw + 0.5


def test_the_window_is_the_card_window_from_the_art_data():
    lay = sl.layout(PANEL, CARD_ASPECT, MEDIC_ASPECT)
    cx, cy, cw, ch = lay.card
    x0, y0, x1, y1 = CARD_WINDOW
    assert lay.window[0] == pytest.approx(cx + cw * x0)
    assert lay.window[2] == pytest.approx(cw * (x1 - x0))
    assert lay.window[1] + lay.window[3] == pytest.approx(cy + ch * (1.0 - y0))


def test_the_window_clears_the_cards_notch():
    """The microSD sprite's bottom-right corner is cut away below y = 0.877;
    a window that ran into it painted a dark panel over thin air."""
    assert CARD_WINDOW[3] <= 0.87


def test_five_organs_fit_the_window_in_a_row():
    """Drawn at their diameter, the seats ~0.19 of the window apart: five must
    fit with air between them, or they overlap and spill (offline render,
    2026-08-04)."""
    lay = sl.layout(PANEL, CARD_ASPECT, MEDIC_ASPECT)
    d = sl.organ_diam(lay)
    xs = sorted(sl.seat_point(lay.window, k)[0] for k in ORGAN_SEATS)
    for a, b in zip(xs, xs[1:]):
        assert b - a > d, "organs overlap"
    for k in ORGAN_SEATS:
        sx, sy = sl.seat_point(lay.window, k)
        assert lay.window[0] <= sx - d / 2 and sx + d / 2 <= lay.window[0] + lay.window[2]
        assert lay.window[1] <= sy - d / 2 and sy + d / 2 <= lay.window[1] + lay.window[3]


def test_the_organs_are_still_readable_on_the_panel():
    """The whole point of the scene. On the 5" panel an organ must not shrink
    below what it was before the surgeon grew (about 22 px there)."""
    lay = sl.layout(PANEL, CARD_ASPECT, MEDIC_ASPECT)
    assert sl.organ_diam(lay) >= 21.0


def test_the_lead_runs_from_the_monitor_to_the_patient():
    lay = sl.layout(PANEL, CARD_ASPECT, MEDIC_ASPECT)
    pts = sl.lead_points(lay)
    assert pts[0][1] == pytest.approx(lay.monitor[1]), "starts at the band"
    assert pts[-1] == pytest.approx(lay.lead_pad)
    cx, cy, cw, ch = lay.card
    assert cx <= lay.lead_pad[0] <= cx + cw and cy <= lay.lead_pad[1] <= cy + ch


# --------------------------------------------------------------------------- #
# the arm
# --------------------------------------------------------------------------- #

def _lay():
    return sl.layout(PANEL, CARD_ASPECT, MEDIC_ASPECT)


def test_the_arm_never_comes_apart():
    """Upper arm a fixed length from the shoulder; forearm from elbow to
    wrist; forceps from wrist to tip — collinear, so the telescoping forearm
    is one straight instrument."""
    lay = _lay()
    for key in ORGAN_SEATS:
        seat = sl.seat_point(lay.window, key)
        for i in range(101):
            pose = sl.arm_pose(i / 100.0, lay, seat)
            assert math.dist(lay.shoulder, pose.elbow) == pytest.approx(lay.upper, abs=0.01)
            assert math.dist(pose.wrist, pose.tip) == pytest.approx(lay.forceps, abs=0.01)
            assert math.dist(pose.elbow, pose.wrist) + lay.forceps == \
                pytest.approx(math.dist(pose.elbow, pose.tip), abs=0.01)


def test_the_arm_is_an_arm_not_a_rod_on_the_panel():
    """An upper arm has to be long enough to read as a limb, and the forearm
    must always be at least as long as the forceps it ends in."""
    lay = _lay()
    assert lay.upper >= lay.forceps * 2.5
    shortest = min(math.dist(sl.arm_pose(i / 100.0, lay, seat).elbow,
                             sl.arm_pose(i / 100.0, lay, seat).wrist)
                   for seat in [sl.seat_point(lay.window, k) for k in ORGAN_SEATS] + [None]
                   for i in range(101))
    assert shortest >= lay.forceps


def test_the_elbow_is_up_not_dragging_on_the_table():
    """Of the two mirror elbows the higher is taken — a surgeon's elbow is up.
    So the elbow is never below where a straight arm would put it."""
    lay = _lay()
    for key in ORGAN_SEATS:
        seat = sl.seat_point(lay.window, key)
        for u in (0.0, 0.3, 0.6, 0.9):
            pose = sl.arm_pose(u, lay, seat)
            d = math.dist(lay.shoulder, pose.tip)
            straight_y = lay.shoulder[1] + (pose.tip[1] - lay.shoulder[1]) / d * lay.upper
            assert pose.elbow[1] >= straight_y - 0.01


def test_the_elbow_stays_under_the_monitor():
    """The bound that made the two-link arm impossible here: on a 3:1 stage a
    folded arm throws its elbow through the monitor. The upper arm's length is
    capped so it cannot, even pointing straight up."""
    for stage in STAGES:
        lay = sl.layout(stage, CARD_ASPECT, MEDIC_ASPECT)
        assert lay.shoulder[1] + lay.upper <= lay.ceiling + 0.01
        assert lay.ceiling < lay.monitor[1]


@pytest.mark.parametrize("key", sorted(ORGAN_SEATS))
def test_one_cycle_is_pick_carry_fit_release_withdraw(key):
    """The motion the operator will see over 2.4 s, pinned point by point."""
    lay = _lay()
    seat = sl.seat_point(lay.window, key)
    # 0: over the tray, forceps open, nothing in them yet
    p0 = sl.arm_pose(0.0, lay, seat)
    assert p0.tip == pytest.approx(lay.pick)
    assert not p0.carrying and p0.open == pytest.approx(1.0)
    # gripped before it leaves
    p1 = sl.arm_pose(sl.GRIP_AT + 0.01, lay, seat)
    assert p1.carrying and p1.tip == pytest.approx(lay.pick)
    # mid-carry: HELD, lifted above the straight line between tray and seat
    p2 = sl.arm_pose((sl.PICK_END + sl.CARRY_END) / 2.0, lay, seat)
    assert p2.carrying and p2.fit == 0.0
    assert p2.tip[1] > max(lay.pick[1], seat[1]), "it is lifted over, not slid"
    # arrival: on the seat, still held, being fitted
    p3 = sl.arm_pose(sl.CARRY_END + 0.02, lay, seat)
    assert p3.carrying and math.dist(p3.tip, seat) < lay.organ_r * 0.3
    assert 0.0 < p3.fit < 1.0
    # released: organ fitted, forceps opening, arm still at the seat
    p4 = sl.arm_pose(sl.RELEASE_AT + 0.04, lay, seat)
    assert not p4.carrying and p4.fit == pytest.approx(1.0)
    assert p4.open > 0.0 and math.dist(p4.tip, seat) < lay.organ_r * 0.3
    # withdrawing: empty, open, on the way back
    p5 = sl.arm_pose((sl.PLACE_END + 1.0) / 2.0, lay, seat)
    assert not p5.carrying and p5.open == pytest.approx(1.0)
    assert lay.pick[0] < p5.tip[0] < seat[0] or seat[0] < p5.tip[0] < lay.pick[0]
    # the loop closes where it opened
    p6 = sl.arm_pose(0.999, lay, seat)
    assert math.dist(p6.tip, lay.pick) < lay.organ_r


def test_the_next_organ_waits_on_the_tray_and_is_not_conjured_in_the_forceps():
    """The first render showed the forceps closing on nothing — the organ only
    existed once held. It is on the tray while the forceps close on it, gone
    while carried and fitted, and back on the tray as the arm returns."""
    assert sl.tray_organ_alpha(0.0) == 1.0
    assert sl.tray_organ_alpha(sl.GRIP_AT - 0.001) == 1.0
    for u in (sl.GRIP_AT, 0.3, 0.6, 0.8):
        assert sl.tray_organ_alpha(u) == 0.0, u
    assert 0.0 < sl.tray_organ_alpha((sl.TRAY_FADE_FROM + 1.0) / 2.0) < 1.0
    assert sl.tray_organ_alpha(0.999) > 0.95


def test_the_forceps_close_on_the_organ_and_open_to_let_go():
    lay = _lay()
    seat = sl.seat_point(lay.window, "kernel")
    held = sl.arm_pose(0.3, lay, seat)
    free = sl.arm_pose(0.9, lay, seat)
    assert math.dist(held.tine_a, held.tine_b) == pytest.approx(lay.organ_r * 2.0, abs=0.01)
    assert math.dist(free.tine_a, free.tine_b) > math.dist(held.tine_a, held.tine_b)


def test_the_finished_pose_is_raised_forceps_open():
    """Nothing left to implant: the arm parks up and out of the way — the
    operator's own 'tweezers raised high' pose (drpi/POSES.md, pose_02) —
    and stays there. No thumbs-up blob."""
    lay = _lay()
    for u in (0.0, 0.5, 0.99):
        pose = sl.arm_pose(u, lay, None)
        assert pose.tip == pytest.approx(lay.park)
        assert not pose.carrying and pose.open == pytest.approx(1.0)
    assert lay.park[1] > lay.shoulder[1], "raised, not lowered"
    assert lay.park[1] < lay.monitor[1], "and not into the monitor"


@pytest.mark.parametrize("stage", STAGES)
def test_the_arm_never_leaves_the_stage(stage):
    lay = sl.layout(stage, CARD_ASPECT, MEDIC_ASPECT)
    seats = [sl.seat_point(lay.window, k) for k in ORGAN_SEATS] + [None]
    for seat in seats:
        for i in range(60):
            pose = sl.arm_pose(i / 60.0, lay, seat)
            for p in _arm_points(pose, lay):
                assert _point_inside(p, stage, slack=1.0), \
                    f"arm at {p} is off the stage {stage} (seat {seat}, {i / 60.0:.2f})"
            assert pose.tip[1] + lay.organ_r * 1.2 <= lay.monitor[1] + 0.5, \
                "the carried organ crosses into the monitor"


# --------------------------------------------------------------------------- #
# the monitor
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("steady", (0.0, 0.3, 0.6, 0.9, 1.0))
def test_the_monitor_never_flatlines(steady):
    """A flat green line is the most legible image in medicine and it means
    the patient died. Whatever the write's progress, the trace has a beat."""
    rect = (0.0, 0.0, 700.0, 60.0)
    ys = [p[1] for p in sl.monitor_trace(rect, steady, 0.37)]
    assert max(ys) - min(ys) > 60.0 * 0.12
    if steady >= 0.9:
        assert max(ys) - min(ys) > 60.0 * 0.5, "strong and clean by the end"


def test_the_heart_rate_survived_the_slower_loop():
    """The free phase was slowed from 0.6/s to 0.42/s so the arm has 2.4 s
    for a placement; the trace scroll was raised to match, so the beat on the
    monitor is the same ~72 bpm it always was."""
    assert sl.PHASE_PER_S * sl.TRACE_SCROLL == pytest.approx(0.6 * 2.0, rel=0.01)


# --------------------------------------------------------------------------- #
# the widget (source inspection — Kivy is not importable here)
# --------------------------------------------------------------------------- #

def test_the_widget_draws_from_the_shared_layout():
    body = src(WIDGET)
    assert "surgery_layout" in body
    for name in ("sl.layout(", "sl.arm_pose(", "sl.monitor_trace(",
                 "sl.lead_points(", "sl.seat_point(", "sl.organ_diam(",
                 "sl.tray_organ_alpha("):
        assert name in body, f"the widget does not call {name}"


def test_the_pink_string_arms_are_gone():
    body = src(WIDGET)
    assert "0.98, 0.82, 0.64" not in body, "the skin-tone arm colour is back"
    assert "0.98, 0.80, 0.62" not in body, "the thumbs-up blob is back"
    assert "self.width * 0.72, self.y + self.height * 0.52" not in body, \
        "the organ is being thrown from mid-air again"
    assert "_draw_incoming" not in body and "_draw_surgeon" not in body


def test_the_arm_is_drawn_over_the_medic_and_the_patient():
    """Draw order IS the picture: the arm comes OUT of the case and reaches
    OVER the card, so it is painted after both."""
    body = src(WIDGET)
    scene = body[body.index("def _draw_scene"):body.index("def _draw_organ")]
    assert scene.index("Rectangle(texture=medic") < scene.index("self._draw_arm(")
    assert scene.index("Rectangle(texture=card") < scene.index("self._draw_arm(")
    assert scene.index("self._draw_arm(") < scene.index("self._draw_monitor(")


def test_the_widget_has_no_fixed_heights():
    """It is given a box and fills it (2026-08-12: fixed heights clip)."""
    body = src(WIDGET)
    assert "height=dp(" not in body and "size_hint_y=None" not in body


def test_the_pi_is_still_accepted_and_still_not_drawn():
    """pi_key stays in the signature so the imager screen's call site holds;
    the Pi is in the operator's other hand and must not appear here."""
    body = src(WIDGET)
    assert 'def __init__(self, pi_key: str = "", **kwargs)' in body
    for forbidden in ("PI_ZERO", "board_images", "pi_sd_geometry", "_draw_pi_board"):
        assert forbidden not in body, f"a Pi is being drawn: {forbidden}"


def test_the_tick_uses_the_shared_rate():
    body = src(WIDGET)
    assert "PHASE_PER_S" in body[body.index("def _tick"):body.index("def set_fraction")]


def test_the_previewer_draws_from_the_same_module():
    """If the previewer and the widget ever disagree the preview stops being
    evidence — both take their numbers from ui.surgery_layout."""
    body = src("scripts/preview_surgery.py")
    for name in ("sl.layout(", "sl.arm_pose(", "sl.monitor_trace(",
                 "sl.lead_points(", "sl.seat_point(", "sl.organ_diam(",
                 "sl.tray_organ_alpha("):
        assert name in body, f"the previewer does not call {name}"
