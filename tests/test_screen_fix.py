"""Unit tests for provisioning.screen_fix — the no-reboot DSI panel re-init.

All subprocess work is injected, so these run tool-free in CI. The behaviours
guarded: the off->wait->on order, the ON retry ladder (a black screen is the
one unacceptable end state we can fight), and the honesty of every message —
success never claims the colours are fixed, failure always names the
power-pull fallback.
"""

import types

from provisioning import screen_fix


def _proc(rc=0, out="", err=""):
    return types.SimpleNamespace(returncode=rc, stdout=out, stderr=err)


class Runner:
    """Scripted subprocess.run stand-in; records every argv."""

    def __init__(self, results):
        self.results = list(results)
        self.calls = []

    def __call__(self, args, **kw):
        self.calls.append(list(args))
        r = self.results.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def test_success_runs_off_then_on_and_message_is_honest():
    r = Runner([_proc(), _proc()])
    ok, msg = screen_fix.reinit_panel(output="DSI-2", runner=r, sleep=lambda s: None)
    assert ok is True
    assert r.calls[0][-1] == "--off" and r.calls[1][-1] == "--on"
    # honesty: never "fixed" — the software cannot see the panel
    assert "fixed" not in msg.lower()
    assert "still wrong" in msg          # ...and the fallback is named


def test_off_failure_reports_and_never_tries_on():
    r = Runner([_proc(rc=1, err="no such output")])
    ok, msg = screen_fix.reinit_panel(output="DSI-2", runner=r, sleep=lambda s: None)
    assert ok is False
    assert "no such output" in msg
    assert len(r.calls) == 1             # never reached --on


def test_on_retries_until_it_comes_back():
    r = Runner([_proc(), _proc(rc=1, err="busy"), _proc()])
    ok, msg = screen_fix.reinit_panel(output="DSI-2", runner=r, sleep=lambda s: None)
    assert ok is True
    assert [c[-1] for c in r.calls] == ["--off", "--on", "--on"]


def test_black_screen_failure_names_the_power_pull():
    fails = [_proc()] + [_proc(rc=1, err="gone")] * screen_fix.ON_RETRIES
    r = Runner(fails)
    ok, msg = screen_fix.reinit_panel(output="DSI-2", runner=r, sleep=lambda s: None)
    assert ok is False
    assert "power" in msg.lower() and "10 seconds" in msg


def test_missing_tool_is_a_clear_message_not_a_raise():
    r = Runner([FileNotFoundError("wlr-randr")])
    ok, msg = screen_fix.reinit_panel(output="DSI-2", runner=r, sleep=lambda s: None)
    assert ok is False and "not installed" in msg


def test_detect_output_parses_first_unindented_line():
    out = 'DSI-2 "(null) (null) (DSI-2)"\n  Make: (null)\n  Enabled: yes\n'
    r = Runner([_proc(out=out)])
    assert screen_fix.detect_output(runner=r) == "DSI-2"


def test_detect_output_none_when_tool_absent():
    r = Runner([FileNotFoundError("wlr-randr")])
    assert screen_fix.detect_output(runner=r) is None


def test_reinit_falls_back_to_default_output_when_detect_fails():
    # detect (1 call, rc!=0) -> fall back to DEFAULT_OUTPUT for off/on
    r = Runner([_proc(rc=1), _proc(), _proc()])
    ok, _ = screen_fix.reinit_panel(runner=r, sleep=lambda s: None)
    assert ok is True
    assert r.calls[1][2] == screen_fix.DEFAULT_OUTPUT
