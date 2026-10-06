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
    assert "this medic's radio and GPS" in SRC and "node #1" not in SRC
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


def test_the_tracker_step_sets_up_first_and_skipping_is_the_quiet_road():
    """Keeper, 2026-10-06: set up its own radio and GPS FIRST. The green button
    does the set-up; "Skip for now" is the muted one; the step names the board
    in full and promises nothing about a radio the medic does not have yet."""
    FLOW = pathlib.Path("ui/setup_flow.py").read_text()
    assert '"setup_first": True' in FLOW
    assert '"opens_label": "Set up its radio and GPS' in FLOW
    assert '"next": "Skip for now' in FLOW
    assert "recognises its own radio and" not in FLOW
    WIZ = pathlib.Path("ui/screens/setup_wizard_screen.py").read_text()
    assert "on_next=lambda *_: self._set_up_then_return(step)" in WIZ
    assert "sf.save_resume(nxt)" in WIZ and "sf.take_resume()" in WIZ


def test_after_the_set_up_the_walkthrough_carries_on_where_it_was():
    """The 2026-08-27 loop (the wizard reset dumped the keeper back at its first
    screen) cannot return: reset() resumes at the step after the set-up, and the
    app goes back to the walkthrough ONLY when the walkthrough sent it."""
    assert "FirstbornScreen(on_home=self._after_medic_setup)" in APP
    body = APP[APP.index("def _after_medic_setup"):APP.index("def _no_cert_popup")]
    assert "_sf.peek_resume()" in body and '"setup" if _sf.peek_resume() else "home"' in body
    # the restart rnsd needs happens INSIDE the set-up (hand-over), and the app
    # opens on the Tracker page afterwards to check the radio and GPS
    assert 'return "firstborn"' in APP and "check_pending()" in APP
    FB = pathlib.Path("ui/screens/firstborn_screen.py").read_text()
    assert "self._begin(check_only=True)" in FB
    assert "check_only = check_only or _mr.check_pending()" in FB   # Try again = re-check
