"""A running build must never be invisible.

Three call sites guarded against starting a second build by `return`-ing
silently. From the operator's side that is indistinguishable from a dead
button — so they tap again, which is exactly how the "phantom flash" reports
started (2026-07-30). The app already recorded which build was running and when
it started; nothing was asking it.
"""

SRC = open("ui/screens/birth_screen.py").read()


#: Methods whose busy-check is NOT an operator-facing refusal, so warning would
#: be wrong rather than merely noisy. Keep this list short and justified.
#:
#: enter_birth is a navigation hook: switch_mode calls it on EVERY entry to
#: BIRTH, including the operator deliberately going to WATCH a running build.
#: Popping "a build is already running" there would fire on the very navigation
#: that answers it — and would loop, because _warn_build_running's own dismiss
#: handler calls switch_mode("birth").
_SILENT_BY_DESIGN = {"enter_birth"}


def test_no_busy_guard_returns_silently():
    """Every place that REFUSES AN ACTION because a build is running must say so."""
    import ast
    tree = ast.parse(SRC)
    for fn in ast.walk(tree):
        if not isinstance(fn, ast.FunctionDef) or fn.name in _SILENT_BY_DESIGN:
            continue
        body = ast.get_source_segment(SRC, fn) or ""
        if "self._busy_with_a_build()" not in body:
            continue
        assert "_warn_build_running" in body, (
            f"{fn.name} refuses because a build is running but never tells the "
            f"operator — indistinguishable from a dead button (2026-07-30).")


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
