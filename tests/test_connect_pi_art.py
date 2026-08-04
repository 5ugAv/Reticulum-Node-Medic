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


def test_the_three_cut_sprites_ship():
    for name in ("medic_case_micro.png", "plug_micro_drawn.png",
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


def test_the_medic_case_carries_no_antenna_and_no_cable():
    """The full drawing is portrait; height-capping it on this wide, short stage
    shrank it to a sliver. The case crop is much closer to square, and the cable
    is cut away because the braid is tiled separately so it can be any length."""
    im = _open("medic_case_micro.png")
    ratio = im.width / im.height
    assert 0.6 <= ratio <= 1.1, f"case aspect {ratio:.2f} — antenna left in?"


def test_the_step_prefers_the_drawn_art_but_still_falls_back():
    """A missing file must degrade to the older photo cut-outs, not blank the
    stage."""
    body = src(WIDGET)
    assert "MEDIC_CASE_MICRO_PNG" in body and "MEDIC_BODY_PNG" in body
    assert "PLUG_MICRO_DRAWN_PNG" in body and "PLUG_MICRO_PNG" in body
    assert "BRAID_MICRO_DRAWN_PNG" in body and "CABLE_BRAID_PNG" in body


def test_the_layers_stack_medic_cable_pi_plug():
    """Order is load-bearing, and every step of it was learned the hard way.

    The medic is the backdrop. The cable goes over it, because with the Pi to
    the LEFT the braid runs beside the case, not across its screen. The Pi goes
    over the cable. The plug goes last — it is the thing in motion and must
    never be occluded by the board it is entering.

    Putting the medic last is the tempting mistake: it covers the socket, which
    is the one thing this step exists to point at.
    """
    seg = _connect_pi()
    order = [seg.index(f"texture={n}") for n in ("medic", "braid", "pi", "plug")]
    assert order == sorted(order), (
        "draw order must be medic -> braid -> pi -> plug, got "
        f"{dict(zip(('medic', 'braid', 'pi', 'plug'), order))}")


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
    assert "travel = plug_h" in _connect_pi(), \
        "travel must be a real gap, not a nudge"


def test_the_power_port_is_marked_forbidden_not_merely_different():
    """Two coloured rings say 'here are two ports'. A struck-through red one
    says 'not this one' — which is the whole point of the step."""
    seg = _connect_pi()
    assert "_NO = (" in seg, "needs its own forbidden colour"
    bar = seg.index("bar = dp(10)")
    assert bar > seg.index("circle=(pwr_x"), "the bar goes with the power ring"
