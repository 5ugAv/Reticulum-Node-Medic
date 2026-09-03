"""Source-level guards for Settings ▸ Encrypt my records.

Kivy is not importable here, so the screen cannot be instantiated. That is
exactly how the setup wizard shipped a TypeError on its FIRST screen with a
green suite (2026-08-31): the logic was tested, the drawing was not, and nothing
in between noticed. These check the things that would break on open.
"""
import ast

import pytest

from tests.srcutil import func_source, src

SCREEN = "ui/screens/encryption_screen.py"
SETTINGS = "ui/screens/settings_screen.py"
APP = "ui/app.py"


def _calls(source, name):
    """Every call to *name* in *source*, as ast.Call nodes."""
    tree = ast.parse(source)
    return [n for n in ast.walk(tree)
            if isinstance(n, ast.Call)
            and getattr(n.func, "id", getattr(n.func, "attr", None)) == name]


# --------------------------------------------------------------------------- #
# The screen draws
# --------------------------------------------------------------------------- #

def test_the_screen_parses():
    ast.parse(src(SCREEN))


def test_every_line_call_passes_only_arguments_line_accepts():
    """THE wizard bug, exactly: _label() was handed size_hint_y and height,
    which it does not take, and the screen died on open with a green suite."""
    source = src(SCREEN)
    sig = ast.parse(func_source(SCREEN, "_line")).body[0].args
    allowed = {a.arg for a in sig.args} | {a.arg for a in sig.kwonlyargs}
    for call in _calls(source, "_line"):
        for kw in call.keywords:
            assert kw.arg in allowed, (
                f"_line() got {kw.arg!r}, which it does not accept — "
                f"this screen would raise TypeError on open")
        assert len(call.args) <= len(sig.args), "too many positional args to _line"


def test_every_button_call_matches_its_helper():
    source = src(SCREEN)
    sig = ast.parse(func_source(SCREEN, "_button")).body[0].args
    allowed = {a.arg for a in sig.args}
    for call in _calls(source, "_button"):
        for kw in call.keywords:
            assert kw.arg in allowed, f"_button() got {kw.arg!r}"
        assert 1 <= len(call.args) <= len(sig.args)


def test_every_colour_named_exists_in_the_theme():
    """A missing key is a KeyError inside a draw, which reads as a crash rather
    than a wrong colour."""
    from ui import theme
    source = src(SCREEN)
    for call in _calls(source, "_line") + _calls(source, "_button"):
        for kw in call.keywords:
            if kw.arg == "color" and isinstance(kw.value, ast.Constant):
                assert kw.value.value in theme.COLORS, (
                    f"{kw.value.value!r} is not a theme colour")


def test_no_label_uses_a_fixed_height():
    """Fixed heights have clipped the top off this project's screens twice.
    Every label here sizes to its texture."""
    body = func_source(SCREEN, "_line")
    assert "texture_size" in body and "size_hint_y=None" in body


# --------------------------------------------------------------------------- #
# It is reachable, and it reports the truth
# --------------------------------------------------------------------------- #

def test_the_screen_is_registered_under_the_name_the_row_opens():
    """A row whose target names no Screen is a button that does nothing."""
    app = src(APP)
    assert 'Screen(name="encryption")' in app
    assert "EncryptionScreen()" in app
    assert '"encryption"' in src(SETTINGS)


def test_both_screens_refresh_when_opened():
    """They REPORT the encryption state and the operator changes it on one and
    reads it on the other. Built once at startup, they would show boot state."""
    app = src(APP)
    assert "scr.show_overview()" in app
    assert "refresh_encryption_row()" in app


def test_the_settings_row_states_the_state_in_its_own_line():
    """_entry does not render its subtitle argument and never has, so a row
    that put its status there would say nothing at all."""
    body = func_source(SETTINGS, "_encryption_entry", cls="SettingsScreen")
    assert "_line(" in body
    assert "Not encrypted" in body and "Encrypted" in body


def test_a_failed_check_reads_as_not_encrypted_never_as_blank():
    """"Could not check" is not "yes", and it is not silence either."""
    body = func_source(SETTINGS, "_encryption_entry", cls="SettingsScreen")
    assert "except Exception" in body and "Could not check" in body


# --------------------------------------------------------------------------- #
# The dangerous parts
# --------------------------------------------------------------------------- #

def test_the_work_runs_off_the_ui_thread():
    """Several scrypt derivations at ~1.6s each. On the main thread the screen
    freezes and reads as a crash — and this project has a standing rule that a
    busy medic must never look dead."""
    body = func_source(SCREEN, "_work", cls="EncryptionScreen")
    assert "threading.Thread" in body and "daemon=True" in body
    assert "Clock.schedule_once" in body, "results must return to the UI thread"


def test_the_screen_cannot_start_two_jobs_at_once():
    body = func_source(SCREEN, "_work", cls="EncryptionScreen")
    assert "self._busy" in body


def test_the_pattern_must_be_drawn_twice():
    """This pattern BECOMES the key. A slip while setting it produces a vault
    whose key is a gesture nobody ever made deliberately."""
    body = func_source(SCREEN, "_pattern_drawn", cls="EncryptionScreen")
    assert "confirm_pattern" in body


def test_the_passphrase_must_be_typed_twice():
    body = func_source(SCREEN, "_ask_passphrase", cls="EncryptionScreen")
    assert "confirm_problem" in body and "passphrase_problem" in body


def test_the_recovery_key_must_be_copied_back():
    """It is the only door the operator cannot choose, so the medic has to be
    sure it left the screen and onto paper."""
    body = func_source(SCREEN, "_ask_recovery_back", cls="EncryptionScreen")
    assert "recovery_matches" in body


def test_the_recovery_key_screen_names_the_medic():
    """An owner with several medics photographs these keys to keep them, and a
    photo of a bare key does not say which unit it opens (operator, 2026-09-01)."""
    body = func_source(SCREEN, "_show_recovery_key", cls="EncryptionScreen")
    assert "tool_name" in body


def test_a_failure_says_nothing_was_left_half_done():
    body = func_source(SCREEN, "_finished", cls="EncryptionScreen")
    assert "half-done" in body or "as they" in body
