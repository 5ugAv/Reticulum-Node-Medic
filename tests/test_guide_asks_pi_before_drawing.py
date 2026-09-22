"""Every picture must be the hardware in their hand (operator rule,
2026-08-06). 2026-09-22: the SD-handover step drew a generic green Pi
because the Zero had not been seen on USB in that run and the "Which
Raspberry Pi is this?" question comes later in the flow. Now the guide
asks BEFORE the first Pi drawing when nothing knows the model, returns
to that step with the answer, and never asks the same question twice."""
import re

SRC = "ui/screens/birth_guide_screen.py"


def _body(name):
    src = open(SRC).read()
    m = re.search(r"    def " + re.escape(name) + r"\(.*?(?=\n    def )", src, re.S)
    assert m, name
    return m.group(0)


def test_an_unknown_pi_is_asked_for_before_the_steps_begin():
    body = _body("_begin_steps")
    i = body.index('self._path == "pi" and not getattr(self, "_pi_key", "")')
    assert "_render_pick_pi()" in body[i:i + 1400]
    assert "_pi_asked_for_art = True" in body[i:i + 1400]
    assert "self._back_action = self._begin_steps" in body[i:i + 1400]
    # and never from inside a step: the matrix caught steps visited twice
    assert "_pi_asked_for_art" not in _body("_render_step")


def test_the_answer_continues_into_the_steps():
    body = _body("_pi_picked")
    assert "_pi_pick_returns_to_step" in body and "self._begin_steps()" in body


def test_the_question_is_never_asked_twice():
    body = _body("_render_pick_pi")
    assert body.index('getattr(self, "_pi_key", "")') < body.index("self._stop_current()")
    assert "_render_confirm_pair()" in body


def test_reset_forgets_the_asked_flag():
    body = _body("reset")
    assert "_pi_asked_for_art = False" in body and "_pi_pick_returns_to_step = False" in body
