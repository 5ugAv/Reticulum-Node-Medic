"""BIRTH's back-swipe must not lock out every future build (audit 2026-08-03).

birth_screen sets ``_flash_view`` when a build takes over the screen, and it was
cleared in exactly two places: the failure popup's on_dismiss, and the
"Done — back to home" button under the certificate. The left-edge swipe is a
documented first-class exit and reached neither, so swiping home from the
certificate left the flag True forever. ``_busy_with_a_build`` then returned
True permanently, gating begin_guided / prefill_name / set_prefill_location —
and the next guided birth calls two of those, so the operator got two stacked
"a build is already running" popups that each bounced them onto the FINISHED
build's page, with no way out short of restarting the app.

Kivy cannot open a window in CI, so the screen can't be instantiated. Rather
than settle for substring-matching the source, these tests compile the ACTUAL
shipped function source and run it against a stub self and a stub kivy.app —
real behaviour, no display. sys.modules entries go through monkeypatch so they
are restored per-test and never leak into collection.
"""
import sys
import textwrap
import types

from tests.srcutil import func_source

SCREEN = "ui/screens/birth_screen.py"


def _load(name, extra_globals=None):
    """Compile one method out of the shipped file and return it as a function."""
    source = textwrap.dedent(func_source(SCREEN, name))
    ns = dict(extra_globals or {})
    exec(compile(source, SCREEN, "exec"), ns)
    return ns[name]


def _stub_kivy_app(monkeypatch, *, flashing):
    """Make ``from kivy.app import App`` inside the method yield a fake app."""
    app = types.SimpleNamespace(flash_in_progress=lambda: flashing)
    mod = types.ModuleType("kivy.app")
    mod.App = types.SimpleNamespace(get_running_app=lambda: app)
    pkg = types.ModuleType("kivy")
    pkg.app = mod
    monkeypatch.setitem(sys.modules, "kivy", pkg)
    monkeypatch.setitem(sys.modules, "kivy.app", mod)
    return app


# ---- handle_back ----------------------------------------------------------

def test_birth_screen_has_a_handle_back_at_all():
    """The whole bug was that BirthScreen had none, so the app's _with_back
    wrapper went straight home and the screen never got to tidy up."""
    assert func_source(SCREEN, "handle_back")


def test_back_swipe_from_a_finished_build_clears_the_flash_view(monkeypatch):
    _stub_kivy_app(monkeypatch, flashing=False)
    handle_back = _load("handle_back")
    exits = []
    screen = types.SimpleNamespace(
        _flash_view=True,
        _exit_flash_view=lambda *a, **k: exits.append(True))
    result = handle_back(screen)
    assert exits, "swiping off the certificate must drop the finished build's view"
    assert result is False, "BIRTH is one page — the swipe should still go home"


def test_back_swipe_during_a_live_build_keeps_the_progress_page(monkeypatch):
    """The operator may swipe home to watch the banner and come back. They
    should return to the progress they left, not to a reset chooser — and
    resetting under a live workflow corrupts the checklist (2026-08-01)."""
    _stub_kivy_app(monkeypatch, flashing=True)
    handle_back = _load("handle_back")
    exits = []
    screen = types.SimpleNamespace(
        _flash_view=True,
        _exit_flash_view=lambda *a, **k: exits.append(True))
    assert handle_back(screen) is False
    assert not exits, "a RUNNING build must keep its page"


# ---- _busy_with_a_build ---------------------------------------------------

def test_a_finished_build_is_not_busy(monkeypatch):
    """THE lockout. _flash_view is view state — which header is showing — not
    whether hardware is being written. It stays True across the whole
    certificate page, long after _finish() called _mark_activity(False)."""
    _stub_kivy_app(monkeypatch, flashing=False)
    busy = _load("_busy_with_a_build")
    assert busy(types.SimpleNamespace(_flash_view=True)) is False


def test_a_running_build_is_still_busy(monkeypatch):
    """The half that must NOT be lost: resetting under a live workflow corrupts
    the checklist and the outcome."""
    _stub_kivy_app(monkeypatch, flashing=True)
    busy = _load("_busy_with_a_build")
    assert busy(types.SimpleNamespace(_flash_view=False)) is True


def test_busy_check_does_not_consult_the_view_flag():
    """Guards the regression directly: if _flash_view creeps back into this
    predicate, the permanent lockout comes back with it.

    Checks the parsed CODE, not the text — the docstring names the flag on
    purpose, to explain why it is deliberately absent."""
    import ast
    tree = ast.parse(textwrap.dedent(func_source(SCREEN, "_busy_with_a_build")))
    read = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    read |= {n.value for n in ast.walk(tree)
             if isinstance(n, ast.Constant) and isinstance(n.value, str)}
    assert "_flash_view" not in read


# ---- the fresh lap --------------------------------------------------------

def test_begin_guided_clears_the_flash_view():
    """Now that the flag no longer gates anything, a new lap started from the
    certificate page must still drop it — begin_guided puts the chooser back,
    so the flag has to agree or it describes a header that isn't on screen."""
    assert "_flash_view = False" in func_source(SCREEN, "begin_guided")
