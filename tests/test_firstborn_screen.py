"""Source-level pins for the firstborn screen (kivy screens aren't instantiated
in CI). The logic is tested in test_firstborn_flow; here we guard that the
screen actually USES that logic and does REAL work, never a fake."""

import ast
import pathlib

SRC = pathlib.Path("ui/screens/firstborn_screen.py").read_text()
APP = pathlib.Path("ui/app.py").read_text()


def test_screen_drives_the_tested_decision_logic():
    assert "firstborn_flow" in SRC and "ff.decide(" in SRC


def test_screen_runs_the_real_tracker_workflow_not_a_demo():
    # no-fake-demos: on a medic it flashes a real Tracker or fails honestly —
    # never an emulated connection or a demo factory stand-in.
    assert "GpsTrackerSetup" in SRC
    assert "LocalConnection" in SRC
    assert "EmulatedConnection" not in SRC
    assert "demo_factory" not in SRC


def test_screen_parses_and_defines_the_class():
    tree = ast.parse(SRC)
    classes = {n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)}
    assert "FirstbornScreen" in classes


def test_app_registers_the_firstborn_screen_dormant():
    # the tour step opens "firstborn"; the screen must exist and be dormant
    # until shown (its poll must not run hidden, like MITOSIS).
    assert 'Screen(name="firstborn")' in APP
    assert "FirstbornScreen(" in APP
    assert "_fb.begin_screen()" in APP and "_fb.sleep()" in APP


# -- review fixes (2026-08-25 adversarial UX pass) ---------------------------

def test_screen_shows_the_tracker_board_image_not_just_the_name():
    # show-don't-tell: the physical-action stages carry the board picture
    assert "heltec_wireless_tracker" in SRC
    assert "_tracker_image" in SRC


def test_celebration_has_a_nameplate_and_honest_proof():
    # "firstborn" / "node #1" retired from the glass (the keeper, 2026-10-06)
    assert "this medic\'s GPS" in SRC and "node #1" not in SRC
    # proof is the REAL fix read back, never fabricated coordinates
    assert "_fix_proof" in SRC and "read_splitter_fix" in SRC


def test_non_terminal_stages_offer_an_on_screen_skip():
    assert "Skip for now" in SRC


def test_a_birth_in_flight_is_not_double_started_on_re_entry():
    assert "if not self._running:" in SRC


def test_probe_offers_an_honest_route_to_birth_the_tracker():
    PROBE = pathlib.Path("ui/screens/probe_screen.py").read_text()
    assert "on_birth_tracker" in PROBE
    assert "on_birth_tracker=lambda: self.switch_mode(\"firstborn\")" in APP


def test_tour_button_labels_are_not_a_trap():
    # the green "next" must NOT claim to open the ceremony (that button skips);
    # the muted opens button carries the "meet the firstborn" label instead.
    FLOW = pathlib.Path("ui/setup_flow.py").read_text()
    assert '"opens_label": "Set up the Tracker' in FLOW
    assert '"next": "Skip for now' in FLOW


def test_firstborn_returns_home_not_into_a_setup_restart():
    # on_home must NOT be switch_mode("setup") — that re-runs the wizard reset()
    # and dumps a keeper who just finished the firstborn back at the security
    # welcome screen (loop bug, 2026-08-27). Home is where tour opens land.
    assert 'FirstbornScreen(on_home=lambda: self.switch_mode("home"))' in APP
    assert 'FirstbornScreen(on_home=lambda: self.switch_mode("setup"))' not in APP
