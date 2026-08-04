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


def test_the_braid_is_drawn_before_the_medic():
    """Painted after, it runs as a stripe straight down the medic's screen —
    caught in an offline render, which is the only place it is visible."""
    body = src(WIDGET)
    start = body.index("class ConnectPiAnim")
    seg = body[start:]
    braid_at = seg.index("texture=braid")
    medic_at = seg.index("texture=medic")
    assert braid_at < medic_at, "the braid must be painted behind the medic"
