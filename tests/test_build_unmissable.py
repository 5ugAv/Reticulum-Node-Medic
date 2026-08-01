"""A running build must never be invisible.

Three call sites guarded against starting a second build by `return`-ing
silently. From the operator's side that is indistinguishable from a dead
button — so they tap again, which is exactly how the "phantom flash" reports
started (2026-07-30). The app already recorded which build was running and when
it started; nothing was asking it.
"""

SRC = open("ui/screens/birth_screen.py").read()


def test_no_busy_guard_returns_silently():
    """Every place that refuses because a build is running must say so."""
    import re
    # find each `if self._busy_with_a_build():` and check the following lines
    for m in re.finditer(r"if self\._busy_with_a_build\(\):\n((?:.*\n){1,3})", SRC):
        block = m.group(1)
        assert "_warn_build_running" in block, (
            "a busy guard returns without telling the operator:\n" + block)


def test_the_warning_names_the_running_build_and_its_age():
    block = SRC[SRC.index("def _warn_build_running"):SRC.index("def _scroll_to_progress")]
    assert "activity_info" in block, "must name WHICH build, not just 'a build'"
    assert "minute" in block and "still going" in block


def test_the_warning_explains_the_actual_risk():
    """'Please wait' is not a reason. Interrupting a flash can leave a board
    half-written, which is the thing the operator would want to know."""
    block = SRC[SRC.index("def _warn_build_running"):SRC.index("def _scroll_to_progress")]
    assert "half-written" in block


def test_dismissing_takes_the_operator_to_the_progress():
    block = SRC[SRC.index("def _warn_build_running"):SRC.index("def _scroll_to_progress")]
    assert "switch_mode" in block and "_scroll_to_progress" in block


def test_the_warning_can_never_break_a_running_build():
    """It runs while a flash is in flight; a crash here would be far worse than
    a missing popup."""
    block = SRC[SRC.index("def _warn_build_running"):SRC.index("def _scroll_to_progress")]
    assert "except Exception" in block
