"""The Antenna test screen tells a first-time user what to do, once."""
import json
import os

STEP_ONE = ("Plug the node whose antenna you are testing into a spare USB "
            "port in the Node Medic.")
LONG_CABLE = "A long USB cable lets you hold the node clear of the medic."
UPRIGHT = "Keep it upright in the same spot for every antenna."
HALF_MINUTE = "Each reading takes about half a minute."
LANGS = ("es", "fr", "de", "ja", "ru", "pl", "id", "sv")


def _src():
    return open("ui/screens/antenna_test_screen.py").read()


def test_step_one_is_boxed_and_the_tips_are_one_action_bullets():
    """Operator, 2026-10-04: "this section reads just as a box of text" —
    step one in its own highlighted box, the tips as separate dot points,
    and no "only if that board is the medic's own radio" confusion."""
    s = _src()
    assert "class _StepBox" in s
    assert 'tr("Plug the node whose antenna you are testing "' in s
    assert 'self._step.show(self._stage == "detect")' in s
    assert 'tr("A long USB cable lets you hold the node clear "' in s
    assert 'tr("Keep it upright in the same spot for every "' in s
    assert 'tr("Each reading takes about half a minute.")' in s
    assert "antennas you are comparing" not in s
    assert "radio never counts" not in s


def test_no_board_is_not_repeated_under_the_instructions():
    s = _src()
    assert 'problem[0] != "no_board"' in s
    assert "tr(problem[1])" in s


def test_the_new_lines_are_translated_in_every_language():
    for code in LANGS:
        d = json.load(open(os.path.join("assets", "i18n", code + ".json"),
                           encoding="utf-8"))
        for key in (STEP_ONE, LONG_CABLE, UPRIGHT, HALF_MINUTE):
            assert d.get(key) and d[key] != key, (code, key)
        assert "Node Medic" in d[STEP_ONE], code      # the tool's name stays


# ---- the reading stage shows that something is happening -------------------

NEW_PROGRESS_KEYS = (
    "Hold the node steady and upright. Do not touch the antenna.",
    "Waiting for the first reading...",
    "Almost done...",
    "about {s} seconds left",
    "Sample {n} of {total}",
)


def test_the_failure_handler_keeps_its_own_copy_of_the_error():
    # `except ... as exc` deletes exc when the block ends; a lambda that runs
    # later on the UI thread then raises NameError and kills the app.
    import ast
    tree = ast.parse(_src())
    for h in ast.walk(tree):
        if isinstance(h, ast.ExceptHandler) and h.name:
            for stmt in h.body:
                for n in ast.walk(stmt):
                    if isinstance(n, ast.Lambda):
                        used = {m.id for m in ast.walk(n.body)
                                if isinstance(m, ast.Name)}
                        args = {a.arg for a in n.args.args + n.args.kwonlyargs}
                        assert h.name not in (used - args), (
                            f"lambda uses '{h.name}' after its except block")


def test_the_reading_stage_draws_a_bar_and_a_sample_count():
    s = _src()
    assert "class _Bar" in s and "self._bar.show(True)" in s
    assert "at.progress_fraction(" in s and "at.seconds_left(" in s
    assert 'tr("Sample {n} of {total}")' in s
    assert "def _stop_ticker" in s and s.count("self._stop_ticker()") >= 2


def test_the_new_progress_lines_are_translated_in_every_language():
    for code in LANGS:
        d = json.load(open(os.path.join("assets", "i18n", code + ".json"),
                           encoding="utf-8"))
        for key in NEW_PROGRESS_KEYS:
            assert d.get(key) and d[key] != key, (code, key)
            for ph in ("{s}", "{n}", "{total}"):
                assert (ph in key) == (ph in d[key]), (code, key, ph)


def test_bar_fraction_moves_between_samples_and_never_overshoots():
    from monitor.antenna_test import (EAR_GAP_S, EAR_SAMPLES, progress_fraction,
                                      seconds_left)
    assert progress_fraction(0, since_last_s=0.0) == 0.0
    mid = progress_fraction(3, since_last_s=1.5)
    assert progress_fraction(3, since_last_s=0.0) < mid < progress_fraction(4)
    # waiting longer than one gap does not run past the next sample
    assert progress_fraction(3, since_last_s=99) < progress_fraction(4)
    assert progress_fraction(EAR_SAMPLES) == 1.0
    assert progress_fraction(EAR_SAMPLES + 2) == 1.0
    assert seconds_left(EAR_SAMPLES) == 0
    assert seconds_left(0) == int(round(EAR_SAMPLES * EAR_GAP_S))
    assert seconds_left(EAR_SAMPLES - 1, since_last_s=99) >= 1


def test_poll_ear_defaults_come_from_the_shared_constants():
    import inspect
    from monitor.antenna_test import EAR_GAP_S, EAR_SAMPLES, poll_ear
    sig = inspect.signature(poll_ear)
    assert sig.parameters["samples"].default == EAR_SAMPLES
    assert sig.parameters["gap_s"].default == EAR_GAP_S


def test_poll_ear_does_not_wait_after_the_last_sample():
    import time
    from monitor.antenna_test import poll_ear
    from tests.test_antenna_test import FakeSerial, chtm_frame
    fake = FakeSerial([chtm_frame(-99), chtm_frame(-100), chtm_frame(-101)], 1)
    t0 = time.time()
    poll_ear("/dev/fake", samples=2, gap_s=1.0, reply_timeout_s=0.2,
             serial_factory=lambda p: fake)
    # 0.3 s settle + ONE gap between the two samples, not two
    assert time.time() - t0 < 1.9
