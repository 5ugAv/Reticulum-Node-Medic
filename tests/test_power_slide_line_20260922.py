"""The three track styles of the slide-to-power control, and who uses which.

Operator, 2026-09-22, on the glass: "the off button will stay the same, but
the little sliding empty box behind it that says off will just make a little
black line ... the off button will stay where it is and it will just slide on
a thin black line." The imager's "slide to wipe" keeps the pill — a thin line
cannot carry that sentence.

SUPERSEDED FOR THE FRONT PAGE on 2026-09-29. The operator supplied artwork for
a green power switch and asked for the red button to go: "here's a power switch
that's more suitable to the new style... remove the old red button and replace
it with this sliding icon." The front page now uses "neon" — a lit capsule the
green knob rides INSIDE. The line style is kept, tested and working; it is
simply not what the front page asks for any more. The pill and the red knob are
untouched, because the imager's slide WIPES A CARD and red is the warning.
"""
import os
import re

from tests.srcutil import ROOT
from ui import power_slide_layout as L

W, H = 156.0, 52.0                         # the front page: knob*3 by knob


def test_the_line_is_thin_and_centred_on_the_knob():
    tx, ty, tw, th, r = L.track_rect(10, 20, W, H, "line")
    assert th <= H * 0.1, "a line, not a bar"
    assert th >= L.LINE_MIN_PX
    assert abs((ty + th / 2) - (20 + H / 2)) < 1e-6, "centred on the knob"
    assert (tx, tw) == (10, W), "spans the whole control, so the knob rides it end to end"
    assert r == th / 2


def test_the_pill_is_unchanged_for_the_wipe_slider():
    tx, ty, tw, th, r = L.track_rect(0, 0, W, H, "pill")
    assert th == H * 0.64 and r == th / 2


def test_an_unknown_style_is_refused():
    import pytest
    with pytest.raises(ValueError):
        L.track_rect(0, 0, W, H, "oval")


def test_the_word_on_the_line_has_a_face_a_line_cannot_give_it():
    assert L.hint_font_px(H, "line") > L.track_rect(0, 0, W, H, "line")[3]
    _x, hy, _w, hh = L.hint_rect(0, 0, W, H, H, "line")
    assert hh > L.track_rect(0, 0, W, H, "line")[3]
    assert abs((hy + hh / 2) - H / 2) < 1e-6, "the word sits ON the line"
    assert _x > 0, "clear of the resting knob"


def test_the_front_page_uses_the_neon_capsule_and_the_imager_keeps_the_pill():
    home = open(os.path.join(ROOT, "ui/screens/home_screen.py"), encoding="utf-8").read()
    i = home.index("SlideToPowerOff(")
    assert 'track="neon"' in home[i:i + 400]
    imager = open(os.path.join(ROOT, "ui/screens/pi_imager_screen.py"), encoding="utf-8").read()
    j = imager.index("SlideToPowerOff(")
    assert 'track=' not in imager[j:j + 400], "a sentence needs the pill (the default)"


def test_the_neon_capsule_holds_the_knob_rather_than_sitting_under_it():
    """The line and pill are thinner than the knob so it rides proud of them.
    The supplied artwork is the other shape: a capsule the knob sits INSIDE,
    so it has to be nearly as tall as the knob or the picture is wrong."""
    _tx, ty, tw, th, r = L.track_rect(0, 0, W, H, "neon")
    assert th > H * 0.8, "the knob would stand proud of it"
    assert th < H, "and it must not be taller than the knob"
    assert (_tx, tw) == (0, W) and r == th / 2


def test_the_word_off_sits_at_the_end_the_knob_must_reach():
    """On the capsule OFF is the destination, not a caption beside the knob."""
    hx, _hy, hw, _hh = L.hint_rect(0, 0, W, H, H, "neon")
    assert hx >= H, "OFF overlaps the resting knob"
    assert hx + hw <= W, "OFF runs off the end of the capsule"


def test_the_destructive_slide_keeps_the_red_knob():
    """Green is the front page's language; the imager's slide WIPES A CARD.
    A green button that destroys a card is the wrong picture."""
    src = open(os.path.join(ROOT, "ui/widgets/slide_to_power.py"), encoding="utf-8").read()
    assert "POWER_RED" in src and "power.png" in src
    assert "power_knob.png" in src
    assert 'track == "neon"' in src, "the knob colour must follow the style"


def test_the_widget_draws_from_the_shared_geometry_in_black():
    src = open(os.path.join(ROOT, "ui/widgets/slide_to_power.py"), encoding="utf-8").read()
    assert "track_rect(" in src and "hint_rect(" in src and "hint_font_px(" in src
    assert "TRACK_FRAC" not in src, "no second copy of the geometry"
    assert 'COLORS["black"' in src, "the line is black, from the theme"
    theme = open(os.path.join(ROOT, "ui/theme.py"), encoding="utf-8").read()
    assert '"black": "#000000"' in theme


def test_the_hint_label_is_not_stretched_by_the_layout():
    """The bug behind the escaping OFF (operator photo, 2026-09-29).

    A Kivy Label defaults to size_hint (1, 1), so FloatLayout's own pass
    overwrote the box _layout had computed — the label kept its left edge and
    was stretched to the full control width, moving the centred text right by
    half the difference. Geometry alone cannot fix that; the label has to opt
    out of the layout.
    """
    src = open(os.path.join(ROOT, "ui/widgets/slide_to_power.py"), encoding="utf-8").read()
    i = src.index("self.hint = Label(")
    assert "size_hint=(None, None)" in src[i:i + 400], (
        "the hint label is size-hinted again — its computed box will be "
        "overwritten and the word will drift out of the track")


def test_the_caller_gets_the_size_it_asked_for():
    """The knob is the full height of the control, so the height decides how
    much track is left to slide along. Kivy applies kwargs in order and the
    widget's own ``setdefault("height", ...)`` landed AFTER the caller's
    ``size``, so the front page asked for 156x52 and got 156x84: the knob grew
    to 84, ate more than half its own track, and left the hint a 51px box that
    wrapped OFF onto two lines.
    """
    src = open(os.path.join(ROOT, "ui/widgets/slide_to_power.py"), encoding="utf-8").read()
    i = src.index("def __init__(self")
    head = src[i:i + 2000]
    assert 'setdefault("height"' not in head, (
        "the height default is back as a setdefault — it will override a "
        "caller's explicit size again")
    assert '"size" not in kwargs' in head


# --- the capsule is a PICTURE now (operator artwork, 2026-09-29) -------------
# ON and OFF are painted on it, so the neon style draws no capsule and no hint.
# The first attempt drew both: OFF escaped the end of the drawn capsule, then
# wrapped onto two lines when the box was tightened. The word was always going
# to fight art it was sitting on.

def test_the_knob_rides_the_painted_channel_and_stops_before_off():
    """Every number here was measured off the artwork, so this is a claim about
    the PNG: the knob must start over ON and finish where the inner channel
    does, short of the painted OFF."""
    w = 200.0
    h = w / L.NEON_ART_ASPECT
    px, _py, pw, _ph = L.neon_pill_rect(0, 0, w, h)
    for progress, name in ((0.0, "at rest"), (1.0, "at full travel")):
        kx, _ky, ks = L.neon_knob_rect(0, 0, w, h, progress)
        assert kx >= px, f"the knob is off the left of the capsule {name}"
        right_frac = (kx + ks - px) / pw
        assert right_frac <= 0.81, (
            f"the knob overlaps the painted OFF {name} (reaches {right_frac:.3f})")
    rest = L.neon_knob_rect(0, 0, w, h, 0.0)[0]
    end = L.neon_knob_rect(0, 0, w, h, 1.0)[0]
    assert end > rest, "there is no travel"


def test_the_knob_fits_inside_the_capsule_not_over_its_rim():
    w = 200.0
    h = w / L.NEON_ART_ASPECT
    _px, py, _pw, ph = L.neon_pill_rect(0, 0, w, h)
    _kx, ky, ks = L.neon_knob_rect(0, 0, w, h, 0.0)
    assert ks < ph, "the knob is taller than the capsule it rides in"
    assert ky >= py - 0.01 and ky + ks <= py + ph + 0.01


def test_progress_round_trips_through_the_knob_position():
    """The drag reads progress back out of where the knob ended up, so the two
    have to be exact inverses or a full slide would not fire the shutdown."""
    w = 240.0
    h = w / L.NEON_ART_ASPECT
    for want in (0.0, 0.25, 0.5, 0.92, 1.0):
        kx, _ky, ks = L.neon_knob_rect(0, 0, w, h, want)
        got = L.neon_progress(0, 0, w, h, kx + ks / 2.0)
        assert abs(got - want) < 1e-9, f"{want} came back as {got}"


def test_the_neon_style_draws_no_capsule_and_no_word():
    src = open(os.path.join(ROOT, "ui/widgets/slide_to_power.py"), encoding="utf-8").read()
    assert "power_track.png" in src, "the painted capsule is not loaded"
    assert 'text="" if track == "neon"' in src, (
        "the neon style is writing a hint over art that already says OFF")


def test_the_front_page_box_matches_the_artworks_aspect():
    """A picture in a box of the wrong shape either stretches or floats in a
    letterbox, and then the knob stops lining up with the channel."""
    home = open(os.path.join(ROOT, "ui/screens/home_screen.py"), encoding="utf-8").read()
    i = home.index("SlideToPowerOff(")
    assert "NEON_ART_ASPECT" in home[i - 600:i + 500]
