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
