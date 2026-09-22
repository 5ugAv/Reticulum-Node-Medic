"""The front page's OFF control rides a thin black line, not a pill.

Operator, 2026-09-22, on the glass: "the off button will stay the same, but
the little sliding empty box behind it that says off will just make a little
black line ... the off button will stay where it is and it will just slide on
a thin black line." The imager's "slide to wipe" keeps the pill — a thin line
cannot carry that sentence.
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


def test_the_front_page_uses_the_line_and_the_imager_keeps_the_pill():
    home = open(os.path.join(ROOT, "ui/screens/home_screen.py"), encoding="utf-8").read()
    i = home.index("SlideToPowerOff(")
    assert 'track="line"' in home[i:i + 400]
    imager = open(os.path.join(ROOT, "ui/screens/pi_imager_screen.py"), encoding="utf-8").read()
    j = imager.index("SlideToPowerOff(")
    assert 'track="line"' not in imager[j:j + 400], "a sentence needs the pill"


def test_the_widget_draws_from_the_shared_geometry_in_black():
    src = open(os.path.join(ROOT, "ui/widgets/slide_to_power.py"), encoding="utf-8").read()
    assert "track_rect(" in src and "hint_rect(" in src and "hint_font_px(" in src
    assert "TRACK_FRAC" not in src, "no second copy of the geometry"
    assert 'COLORS["black"' in src, "the line is black, from the theme"
    theme = open(os.path.join(ROOT, "ui/theme.py"), encoding="utf-8").read()
    assert '"black": "#000000"' in theme
