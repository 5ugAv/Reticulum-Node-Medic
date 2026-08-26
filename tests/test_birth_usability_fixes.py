"""Source-level pins for the safe birth-usability fixes (2026-08-26 walkthrough).
Kivy screens aren't instantiated in CI, so guard the wiring at source level."""

import pathlib

BG = pathlib.Path("ui/screens/birth_guide_screen.py").read_text()
BS = pathlib.Path("ui/screens/birth_screen.py").read_text()
WS = pathlib.Path("ui/widgets/wizard_step.py").read_text()


def test_silent_no_detect_gets_a_cable_nudge():
    assert "_start_detect_nudge" in BG
    assert "charge-only" in BG and "different cable" in BG
    # it's armed on the detect screen and cancelled on leaving / on detect
    assert "self._start_detect_nudge(step)" in BG
    assert "_stop_detect_nudge()" in BG
    # detection cancels it so a found board never shows the nudge
    assert "self._detected_something = True" in BG


def test_self_advancing_steps_show_a_live_heartbeat():
    # hide_next() marks a self-advancing step; it must start the heartbeat
    hn = WS.split("def hide_next", 1)[1].split("def show_next", 1)[0]
    assert "_start_heartbeat()" in hn
    assert "keeping watch" in WS
    # self-cancels when detached (no Clock leak after navigation)
    assert "self.parent is None" in WS


def test_a_bare_rnode_is_not_told_to_watch_vitals():
    # an RNode never beacons; the finish must say so instead of "watch VITALS"
    seg = BS.split('_last_type", "") == "rnode"', 1)[1].split("else:", 1)[0]
    assert "won't show up in VITALS" in seg
    assert "plug into a phone" in seg
