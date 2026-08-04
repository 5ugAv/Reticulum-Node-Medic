"""The connect-Pi step's artwork (operator's drawing, 2026-08-04).

The sprites are cut from one supplied image, and the layout depends on their
proportions — so a well-meaning re-crop can break the step without touching a
line of animation code. These guard the properties the geometry actually relies
on, with the reasons attached.
"""
import os

import pytest

from tests.srcutil import src

ANIM = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "assets", "ui", "anim")
WIDGET = "ui/widgets/birth_anims.py"


def _connect_pi():
    """Just ConnectPiAnim's source. The module has several _draw methods, so a
    bare func_source would happily return somebody else's."""
    whole = src(WIDGET)
    return whole[whole.index("class ConnectPiAnim"):]


def _open(name):
    Image = pytest.importorskip("PIL.Image", reason="Pillow not installed")
    path = os.path.join(ANIM, name)
    assert os.path.exists(path), f"{name} is missing"
    return Image.open(path)


def test_the_cut_sprites_ship():
    for name in ("medic_nocable_micro.png", "plug_micro_drawn.png",
                 "braid_micro_drawn.png"):
        im = _open(name)
        assert im.mode == "RGBA", f"{name} needs alpha to sit on the dark UI"
        assert im.width > 8 and im.height > 8


def test_the_plug_keeps_a_real_micro_usb_proportion():
    """A micro-USB moulding is roughly 2.4:1. The step sizes the plug by WIDTH,
    so a squatter crop would be drawn short and a taller one would swallow the
    whole gap between the boards, leaving no cable visible."""
    im = _open("plug_micro_drawn.png")
    ratio = im.height / im.width
    assert 1.9 <= ratio <= 3.0, f"plug aspect {ratio:.2f} will break the layout"


def test_the_braid_slice_is_a_tall_thin_strip():
    """It is tiled down the run, so it has to be a straight vertical slice."""
    im = _open("braid_micro_drawn.png")
    assert im.height > im.width * 3


def test_the_medic_sprite_has_no_cable_left_on_it():
    """THE uniform-cable invariant, checked on the pixels.

    The cable is now drawn entirely by tiling the braid at one width. If any of
    the DRAWING's own cable survives on the medic, it renders at the medic's
    scale — about a third of the tiled width — and the rope visibly steps where
    the two meet, which is the fault this sprite exists to remove.
    """
    im = _open("medic_nocable_micro.png").convert("RGBA")
    px = im.load()
    blue = 0
    for y in range(0, im.height, 2):
        for x in range(0, im.width, 2):
            r, g, b, a = px[x, y]
            if a > 60 and b > 120 and (b - r) > 45:
                blue += 1
    assert blue < 40, f"{blue} cable-blue pixels still on the medic sprite"


def test_the_step_prefers_the_drawn_art_but_still_falls_back():
    """A missing file must degrade to the older photo cut-outs, not blank the
    stage."""
    body = src(WIDGET)
    assert "MEDIC_NOCABLE_PNG" in body and "MEDIC_BODY_PNG" in body
    assert "PLUG_MICRO_DRAWN_PNG" in body and "PLUG_MICRO_PNG" in body
    assert "BRAID_MICRO_DRAWN_PNG" in body and "CABLE_BRAID_PNG" in body


def test_the_layers_stack_cable_medic_pi_plug():
    """Order is load-bearing and every step of it was learned from a render.

    The cable goes down FIRST so the case hides where it enters — painted over
    the medic it ran as a stripe across its screen. Then the Pi. The plug goes
    last: it is the thing in motion and must never be occluded by the board it
    is entering.
    """
    seg = _connect_pi()
    # "texture=braid" is a PREFIX of "texture=braid_h", so match the horizontal
    # leg by its full token or index() silently returns the wrong occurrence.
    names = ("texture=braid,", "texture=medic", "texture=pi,", "texture=plug")
    order = [seg.index(n) for n in names]
    assert order == sorted(order), (
        f"draw order wrong: {dict(zip(names, order))}")


def test_the_cable_sweeps_instead_of_running_straight():
    """THE reason the Pi can sit to the LEFT rather than on top.

    A plug enters a Pi Zero's socket vertically, so with one straight run the
    medic is forced below the socket — which on a wide, short stage reads as
    underneath. A curve lets the boards stand side by side, and it is modelled
    on the radio-board step's own sprite, which draws exactly this shape
    (operator, 2026-08-04). A right-angle elbow was the earlier, wrong answer.
    """
    seg = _connect_pi()
    assert "_bezier(" in seg, "no sweep — a straight run stacks the Pi again"
    assert "Rotate(angle=ang" in seg, (
        "each braid tile must follow the local tangent, or the rope kinks")


def test_the_sweep_ends_vertical_under_the_plug():
    """The final control point sits directly beneath the plug, which forces the
    last tangent vertical. Without that the cable would drag the plug in at an
    angle, and a micro-USB plug only goes in straight."""
    seg = _connect_pi()
    assert "p2 = (data_x," in seg and "p3 = (data_x," in seg, (
        "the last two control points must share the plug's x")


def test_the_sweep_stays_inside_the_widget():
    """Kivy does not clip a widget's canvas, so a control point below the floor
    paints over the body text beneath the stage."""
    seg = _connect_pi()
    assert "p1 = (p0[0], y + dp(" in seg and "p2 = (data_x, y + dp(" in seg


def test_the_plug_actually_enters_the_socket():
    """It must look INSERTED, not merely touching (operator, 2026-08-04). The
    metal tongue is the top of the moulding, so seating means sinking part of
    the plug's own height past the port line — a fixed dp(3) nudge just kissed
    the edge."""
    assert "plug_h * 0.28" in _connect_pi(), \
        "seating depth must scale with the plug"


def test_the_plug_starts_clear_of_the_board():
    """The step is 'plug it in', so it has to begin unplugged: a short nudge
    read as a plug already in the socket, twitching."""
    seg = _connect_pi()
    assert "travel = plug_h" in seg, "travel must be a real gap, not a nudge"
    assert "floor + travel + plug_h" in seg, (
        "the socket's height must be DERIVED from the travel — sized on its "
        "own, the plug sets off from inside its own cable")


def test_the_power_port_is_marked_forbidden_not_merely_different():
    """Two coloured rings say 'here are two ports'. A struck-through red one
    says 'not this one' — which is the whole point of the step."""
    seg = _connect_pi()
    assert "_NO = (" in seg, "needs its own forbidden colour"
    bar = seg.index("bar = dp(10)")
    assert bar > seg.index("circle=(pwr_x"), "the bar goes with the power ring"
