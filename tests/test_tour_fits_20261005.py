"""Readiness ledger #146 — the setup tour's words scroll under a fixed picture
shelf, so no step can overflow the 480 dp panel or squeeze its poster card away.
The guided birth keeps its fixed body; its own height tests guard that."""
import math

from tests.srcutil import ROOT, func_source, src
from tests.test_birth_guide import _PAD, _TOP_H, _NAV_H, _SPACING, _block_h
from ui import theme
import ui.setup_flow as sf

#: What a scrolling step must still show of its body without a scroll: three
#: lines of 19 sp at 1.25 leading is ~90 dp; 80 leaves a little for rounding.
BODY_FLOOR_DP = 80
PICTURE_DP = 120   # WizardStep.PICTURE_STAGE_DP, kept in step by the test below


def _plain_steps():
    custom = {sf.RECOVERY_KEY, sf.RECOVERY_KEY_BACK, sf.PASSPHRASE_STEP, sf.LEVEL,
              sf.PATTERN_STEP, sf.KEYFILE_STEP, sf.SECURITY_SUMMARY}
    for s in list(sf._TOUR_INTRO) + list(sf._SECURITY_STEPS) + list(sf._TOUR_STEPS):
        if s["key"] not in custom:
            yield s


def fixed_height(step):
    """dp the NON-scrolling parts of a plain step stack up to."""
    h = 2 * _PAD + _TOP_H + _NAV_H
    parts = 2                                   # top block, nav
    h += _block_h(step["title"], "27sp", bold=True)
    parts += 1
    if step.get("poster_card") or step.get("board_image"):
        h += PICTURE_DP
        parts += 1
    parts += 1                                  # the body scroll itself
    if step.get("hint"):
        h += _block_h(step["hint"], "14sp")
        parts += 1
    if step.get("warning"):
        h += _block_h(step["warning"], "15sp", bold=True, extra=20.0)
        parts += 1
    return h + _SPACING * (parts - 1)


def test_every_plain_setup_step_shows_three_lines_of_body_before_scrolling():
    short = [(s["key"], theme.PANEL_H_DP - fixed_height(s)) for s in _plain_steps()
             if theme.PANEL_H_DP - fixed_height(s) < BODY_FLOOR_DP]
    assert not short, ("body viewport under the floor: "
                       + ", ".join(f"{k} leaves {h:.0f} dp" for k, h in short))


def test_plain_steps_scroll_their_body_under_a_fixed_picture_shelf():
    ws = src("ui/widgets/wizard_step.py")
    assert f"PICTURE_STAGE_DP = {PICTURE_DP}" in ws
    assert "scroll_body=False, stage_height=None, extra_nav=None" in ws
    assert "sv = ScrollView(do_scroll_x=False, bar_width=dp(4), size_hint_y=1)" in ws
    assert "nav.add_widget(extra_nav)" in ws
    plain = func_source("ui/screens/setup_wizard_screen.py", "_render_plain",
                        cls="SetupWizardScreen")
    assert "scroll_body=True" in plain and "extra_nav=extra" in plain
    assert "stage_h += WizardStep.PICTURE_STAGE_DP" in plain
    see = func_source("ui/screens/setup_wizard_screen.py", "_see_it_button",
                      cls="SetupWizardScreen")
    assert "size_hint_x=0.75" in see and "height=dp(48)" not in see


def test_the_birth_walkthrough_keeps_its_fixed_body():
    """The default must not change under the guided birth: its steps are held
    under the panel by their own height tests, with a flexible stage."""
    ws = src("ui/widgets/wizard_step.py")
    assert "        else:\n            self.add_widget(self.stage)" in ws
    assert "        else:\n            self.add_widget(body_lbl)" in ws
    guide = src("ui/screens/birth_guide_screen.py")
    assert "scroll_body=" not in guide


def test_the_control_socket_can_jump_to_a_wizard_step():
    from ui.remote import parse_command
    assert parse_command("wizard 3") == ("wizard", "3")
    assert parse_command("wizard") == ("", None)
    assert "scr._render()" in src("ui/remote.py")


def test_the_open_it_now_caption_wraps_inside_its_button():
    """Deploy 79506336 on the glass: "Open MAPS to download an area" and
    "Meet the firstborn →" spilled over Back and Next — Kivy centres an
    unwrapped caption on the button and lets it run past both edges."""
    import pathlib
    src = pathlib.Path("ui/screens/setup_wizard_screen.py").read_text()
    body = src[src.index("def _see_it_button"):src.index("def _leave_for")]
    assert 'setattr(i, "text_size", (w - dp(14), None))' in body
    assert 'halign="center"' in body
