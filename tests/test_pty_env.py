"""The autoinstall PTY must be a usable terminal, whatever launched the app.

Live 2026-08-05::

    [birth] flash: FAIL — Flash failed: autoinstall did not complete:
    TERM environment variable not set.

The same RAK4631 had flashed successfully 40 minutes earlier. Nothing about the
board or the firmware changed — the UI had been restarted, and that restart came
from a non-interactive ssh session with no TERM in its environment.
``pexpect.spawn`` inherits the parent environment, so the PTY had no TERM, and
rnodeconf's autoinstall refused to run in a terminal it could not identify.

That made flashing depend on how the app happened to be started: launched from a
login session it worked, relaunched by restart_ui.sh it did not. A flash must
not be luckier on some days than others.
"""
import transport.connection as C


class _FakeChild:
    def __init__(self):
        self.exitstatus = 0
        self.before = ""

    def expect(self, *a, **k):
        raise _Done()

    def sendline(self, *a, **k):
        pass

    def close(self, *a, **k):
        pass

    def isalive(self):
        return False


class _Done(Exception):
    pass


def _spawn_env(monkeypatch, parent_env):
    """Run _pexpect_interactive with a fake pexpect and report the env it used."""
    seen = {}

    class FakePexpect:
        EOF = type("EOF", (), {})
        TIMEOUT = type("TIMEOUT", (), {})

        @staticmethod
        def spawn(*args, **kwargs):
            seen.update(kwargs.get("env") or {})
            seen["__had_env__"] = "env" in kwargs
            return _FakeChild()

    import os
    import sys
    # _pexpect_interactive does `import os as _os` at call time, so patching the
    # real os module's environ is what it will see.
    monkeypatch.setitem(sys.modules, "pexpect", FakePexpect)
    monkeypatch.setattr(os, "environ", parent_env)
    try:
        C._pexpect_interactive("true", [("x", "y")], 5)
    except Exception:
        pass
    return seen


def test_TERM_is_set_even_when_the_app_inherited_none(monkeypatch):
    """THE bug: restart_ui.sh over a non-interactive ssh leaves no TERM."""
    env = _spawn_env(monkeypatch, {"PATH": "/usr/bin"})
    assert env.get("__had_env__"), "spawn must be given an explicit env"
    assert env.get("TERM"), "no TERM — autoinstall will refuse to run"


def test_TERM_is_a_capable_terminal_not_dumb(monkeypatch):
    """An inherited TERM=dumb has no terminfo capabilities, so passing it
    through would fail the same way while looking correct."""
    env = _spawn_env(monkeypatch, {"PATH": "/usr/bin", "TERM": "dumb"})
    assert env.get("TERM") != "dumb"


def test_the_rest_of_the_environment_still_reaches_the_child(monkeypatch):
    """PATH matters most: ~/.local/bin carries rnodeconf and adafruit-nrfutil,
    and a child that cannot find them fails in a much more confusing way."""
    env = _spawn_env(monkeypatch, {"PATH": "/home/x/.local/bin:/usr/bin",
                                   "HOME": "/home/x"})
    assert env.get("PATH") == "/home/x/.local/bin:/usr/bin"
    assert env.get("HOME") == "/home/x"
