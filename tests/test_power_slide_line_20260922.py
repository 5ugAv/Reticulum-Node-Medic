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


def test_the_hint_never_escapes_the_capsule():
    """OFF is the place the knob travels TO, so it must sit inside the thing
    the knob travels along. Checked across the sizes the front page and a
    future wider panel would give it."""
    for w, h in ((234.0, 84.0), (156.0, 52.0), (400.0, 96.0)):
        tx, _ty, tw, _th, _r = L.track_rect(14, 0, w, h, "neon")
        hx, _hy, hw, _hh = L.hint_rect(14, 0, w, h, h, "neon")
        assert hx >= tx + h, "OFF overlaps the resting knob"
        assert hx + hw <= tx + tw, (
            f"OFF runs {hx + hw - tx - tw:.0f}px past the end of the capsule "
            f"at {w:.0f}x{h:.0f}")


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
