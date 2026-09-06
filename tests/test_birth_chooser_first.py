"""BIRTH now LANDS on the node-type chooser, not the antenna animation
(operator, 2026-08-26). Radio builds get the antenna warning AFTER the choice;
Clone needs none. Source-level pins — the screen is kivy."""

import pathlib

SRC = pathlib.Path("ui/screens/birth_guide_screen.py").read_text()


def test_reset_lands_on_the_chooser_not_the_antenna_landing():
    body = SRC.split("def reset(self):", 1)[1].split("@staticmethod", 1)[0]
    assert "_render_intro()" in body
    # it must NOT open straight on the antenna/detect landing any more
    assert "_render_antenna()" not in body
    assert "_pi_present_without_radio" not in body


def test_choosing_a_radio_build_shows_the_antenna_warning_next():
    """"pi" no longer jumps straight to the antenna landing (2026-09-06): it
    asks whether a radio needs flashing here at all, since a Pi build can now
    use one that already works. "Flash a radio now" on that question leads to
    exactly the same antenna-first sequence host/radio always used — nothing
    about THAT sequence changed, only what decides whether "pi" enters it."""
    ch = SRC.split("def _choose(self, path):", 1)[1].split("def ", 1)[0]
    assert 'path in ("host", "radio")' in ch
    assert "_render_antenna()" in ch          # radio builds -> antenna first
    assert "_render_name()" in ch             # non-radio -> straight on
    assert "_render_pi_radio_choice()" in ch  # pi -> ask, don't assume


def test_the_pi_radio_choice_can_still_reach_the_antenna_landing():
    """Whichever way "pi" is chosen, "flash one now" is still the untouched,
    bench-tested antenna-first sequence — this screen only decides whether
    "pi" ENTERS it, never changes what happens once it does."""
    rc = SRC.split("def _render_pi_radio_choice(self):", 1)[1].split(
        "\n    def ", 1)[0]
    assert "self._render_antenna()" in rc
    assert "self._render_name()" in rc
    assert "self._pi_flash_radio = True" in rc
    assert "self._pi_flash_radio = False" in rc


def test_detect_does_not_re_ask_the_type_when_already_chosen():
    # _route also carries the read's generation + the build chosen when it
    # started, so a read from an abandoned lap draws nothing and a reset
    # mid-read cannot turn a chosen build back into a question
    # (tests/test_birth_asks_once.py, 2026-08-30).
    rt = SRC.split("def _route(self, c, gen=None, chose=None):", 1)[1].split("\n    def ", 1)[0]
    assert 'path in ("host", "radio", "pi")' in rt
    assert "_render_name()" in rt             # pre-chosen -> name, not chooser


def test_pi_autodetect_does_not_hijack_a_chosen_non_pi_build():
    pd = SRC.split("def _on_pi_detected(self):", 1)[1].split("\n    def ", 1)[0]
    # by MEANING now, not by listing the two radio keys: any chosen build that
    # is not a Pi wins over the watcher (2026-08-30).
    assert 'self._path and self._path != "pi"' in pd


def test_antenna_still_precedes_powering_the_board():
    # the antenna step's Next still leads to detect (which powers the board);
    # antenna is AFTER the choice but STILL before the board is energised.
    ant = SRC.split("def _render_antenna(self):", 1)[1].split("def ", 1)[0]
    assert "on_next=self._render_detect" in ant
    assert "self._render_intro" in ant        # Back returns to the chooser
