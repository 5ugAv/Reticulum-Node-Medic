"""The box follows the text: the 2026-09-29 screen-walk clips stay fixed.

Seven screens captured over the control socket clipped their own sentences
(recovery key, trusted operators, notifications, Settings ▸ Home mode, radio
defaults, date & time, the MITOSIS parts list).  Two causes, both guarded here
at source level — no test can import a Kivy screen on the Mac:

* a Label pinned to ``h=NN`` for a paragraph that wraps;
* the frozen "grow" idiom: ``bind(size → text_size)`` plus
  ``bind(texture_size → height)`` on the SAME label, which freezes the height
  at the un-wrapped first render.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCREENS = ROOT / "ui" / "screens"

FROZEN_IDIOM = 'bind(texture_size=lambda w, v: setattr(w, "height", v[1]))'


def _src(name: str) -> str:
    return (SCREENS / f"{name}_screen.py").read_text()


def test_helper_re_holds_text_size_width_only():
    src = (ROOT / "ui" / "text_fit.py").read_text()
    assert "lbl.bind(text_size=_width_only)" in src
    assert 'setattr(i, "text_size", (w, None))' in src
    assert 'setattr(i, "height", ts[1]' in src


def test_mitosis_paragraphs_grow_through_the_helper():
    src = _src("mitosis")
    assert FROZEN_IDIOM not in src
    assert src.count("grow_to_text(") >= 8


def test_notifications_explainer_is_not_pinned():
    src = _src("notifications")
    assert FROZEN_IDIOM not in src
    assert "This is optional.\"), h=" not in src
    assert "grow_to_text(lbl)" in src


def test_walked_paragraphs_are_not_pinned():
    pinned = {
        "radio_defaults": ['color="warning_yellow", h='],
        "trusted_operators": ['h=2 * theme.line_dp', '"text_secondary", h=20)',
                              '"text_secondary", h=44)'],
        "settings": ['h=2 * theme.line_dp', 'color="text_secondary", h=46)',
                     'color="text_secondary", h=32)'],
        "datetime": ['color="text_secondary", h=24)', 'color="green", h=24)'],
        "recovery_key": ['"text_secondary", h=48)', '"text_secondary", h=24)',
                         '"16sp", h=110)'],
    }
    for screen, needles in pinned.items():
        src = _src(screen)
        assert "grow_to_text" in src, screen
        for n in needles:
            assert n not in src, (screen, n)


def test_left_aligned_buttons_centre_their_text_vertically():
    """Kivy's Label default is valign=bottom: a left-aligned Button whose
    text_size is bound to its size draws its label on the bottom edge (the
    region presets on radio defaults, the selectors on BIRTH)."""
    for screen in ("radio_defaults", "birth", "language", "settings"):
        src = _src(screen)
        for chunk in src.split("Button(")[1:]:
            head = chunk.split(")", 1)[0]
            if 'halign="left"' in head:
                assert 'valign="middle"' in head, (screen, head[:80])
