"""The new medic's own screen during a clone (keeper, 2026-10-06): the home
page pixelated and sharpening behind a readable step list, drawn here and
written to its framebuffer. Proven on node-medic-2's panel first."""
import os

from PIL import Image

from workflows import clone_screen as cs
from tests.srcutil import func_source

STEPS = list(cs.STEP_WORDS)


def test_every_clone_step_has_plain_words():
    from workflows.clone import CloneWorkflow
    from transport.connection import EmulatedConnection
    names = ["find_new_medic"] + [n for n, _f in CloneWorkflow(EmulatedConnection(), None).steps]
    missing = [n for n in names if n not in cs.STEP_WORDS]
    assert not missing, missing
    for words in cs.STEP_WORDS.values():
        for jargon in ("toolchain", "wheel", "SSH", "lineage", "child", "boot chip", "deb"):
            assert jargon not in words, words


def test_the_picture_fills_the_panel_and_converts_to_its_framebuffer():
    img = cs.render_frame(STEPS, 5)
    assert img.size == (cs.WIDTH, cs.HEIGHT)
    raw = cs.framebuffer_bytes(img)
    assert len(raw) == 720 * 1280 * 4          # 32-bit XRGB, stride 2880 on the Pi 5
    # byte order B, G, R, X: a pure-red pixel reads 0,0,255
    red = cs.framebuffer_bytes(Image.new("RGB", (720, 1280), (255, 0, 0)))
    assert red[:3] == bytes([0, 0, 255])


def test_the_home_page_sharpens_as_the_steps_finish():
    sizes = [cs.pixel_size(i, len(STEPS)) for i in range(len(STEPS) + 1)]
    assert sizes[0] >= 40 and sizes[-1] == 1
    assert sizes == sorted(sizes, reverse=True)
    assert os.path.isfile(cs.BACKDROP)
    assert Image.open(cs.BACKDROP).size == (720, 1280)


def test_a_missing_backdrop_still_draws():
    img = cs.render_frame(STEPS, 0, backdrop="/nonexistent.png")
    assert img.size == (720, 1280)


def test_a_failed_step_is_drawn_and_marks_are_shapes_not_glyphs():
    assert cs.render_frame(STEPS, 9, failed=True).size == (720, 1280)
    src = open(cs.__file__).read()
    assert "_mark(d, kind," in src and "d.text((m + 14, y), mark" not in src


def test_the_clone_draws_only_on_the_known_panel_and_falls_back_to_text():
    body = func_source("workflows/clone.py", "_draw_on_new_medic")
    assert '["720,1280", "32"]' in body and "of=/dev/fb0" in body
    assert "cursor_blink" in body and "\\033[?25l" in body
    tell = func_source("workflows/clone.py", "_tell_new_medic")
    assert "/dev/tty1" in tell                    # the old text road stays as fallback
