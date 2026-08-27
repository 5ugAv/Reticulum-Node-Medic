"""Source-level pins for the antenna-test screen (kivy screens aren't
instantiated in CI). The decisions are tested in test_antenna_test; here we
guard that the screen USES that logic, stays honest, and is wired dormant."""

import ast
import pathlib

SRC = pathlib.Path("ui/screens/antenna_test_screen.py").read_text()
APP = pathlib.Path("ui/app.py").read_text()
TRIAGE = pathlib.Path("ui/screens/triage_screen.py").read_text()


def test_screen_parses_and_defines_the_class():
    tree = ast.parse(SRC)
    classes = {n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)}
    assert "AntennaTestScreen" in classes


def test_screen_drives_the_tested_logic_not_its_own():
    assert "monitor import antenna_test" in SRC or \
        "from monitor import antenna_test" in SRC
    assert "AntennaSession" in SRC and "poll_ear" in SRC \
        and "find_test_board" in SRC


def test_the_unplug_between_antennas_is_enforced():
    # two readings must never silently share one antenna: the port has to
    # VANISH and RETURN before the next reading arms.
    assert "_board_gone" in SRC
    assert '"swap"' in SRC


def test_the_higher_is_better_rule_is_always_on_screen():
    # (source-level: the sentence is line-wrapped, so match its head)
    assert "a good antenna hears MORE" in SRC


def test_the_sponge_lesson_is_told_at_the_verdict():
    assert "impedance meter" in SRC and "absorbs power" in SRC
    assert "folding antenna folded" in SRC


def test_swap_instructions_forbid_live_swapping():
    assert "never swap live" in SRC


def test_app_registers_the_screen_dormant():
    assert 'Screen(name="antenna_test")' in APP
    assert "AntennaTestScreen(" in APP
    assert "_ant.begin_screen()" in APP and "_ant.sleep()" in APP


def test_triage_offers_the_antenna_test():
    assert "on_antenna_test" in TRIAGE
    assert 'switch_mode("antenna_test")' in APP
