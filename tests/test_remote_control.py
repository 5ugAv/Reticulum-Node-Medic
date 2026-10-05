"""The control socket's pure parts: what it accepts, and how it finds a node."""
from ui import remote as R


def test_commands_parse_exactly():
    assert R.parse_command("ping") == ("ping", None)
    assert R.parse_command("probe") == ("probe", None)
    assert R.parse_command("probe now") == ("", None)
    assert R.parse_command("  OPEN  vitals ") == ("open", "vitals")
    assert R.parse_command("node c627") == ("node", "c627")
    assert R.parse_command("list") == ("list", None)
    assert R.parse_command("home") == ("home", None)
    assert R.parse_command("current") == ("current", None)


def test_junk_and_half_commands_are_refused():
    for bad in ("", "   ", "flash", "open", "node", "ping now", "home please", "rm -rf /"):
        assert R.parse_command(bad) == ("", None), bad


def test_a_node_prefix_must_be_unambiguous_and_long_enough():
    hashes = ["a1b2c3d4e5f60718293a4b5c6d7e8f90", "a1b29999" + "0" * 24, "0f1e2d3c" + "0" * 24]
    assert R.resolve_node("0f1e", hashes) == (hashes[2], "")
    h, why = R.resolve_node("a1b2", hashes)
    assert h is None and "2 nodes" in why
    h, why = R.resolve_node("a1b", hashes)
    assert h is None and "4 hex" in why
    h, why = R.resolve_node("zzzz", hashes)
    assert h is None
    h, why = R.resolve_node("abcd", hashes)
    assert h is None and "no node" in why


def test_it_can_only_move_between_screens_or_press_probe():
    """The verb list IS the capability list. Nothing here flashes, wipes or
    deletes; adding such a verb is a security decision, not a convenience.
    "probe" (operator's call, 2026-10-04) presses PROBE's Run button — a
    read-only diagnostic of the board on USB — and nothing more."""
    # "wizard <n>" (2026-10-05) jumps the setup walkthrough to a step so its
    # layout can be looked at on the glass; it renders a step and switches
    # screens, nothing more — no secret lives on a step reached this way.
    assert set(R.VERBS) == {"ping", "list", "open", "node", "home", "current",
                            "map", "probe", "wizard"}
    src = open("ui/remote.py").read()
    body = src[src.index('if verb == "probe"'):src.index('return "err unreachable"')]
    assert "scr.start()" in body and "already running" in body
    for forbidden in ("fix_all", "_fix_one", "flash", "wipe", "delete"):
        assert forbidden not in body


def test_probe_verb_presses_run_once_and_refuses_while_busy():
    import types
    calls = []
    scr = types.SimpleNamespace(run_btn=types.SimpleNamespace(disabled=False),
                                _busy=False, header=types.SimpleNamespace(text="Checking: X"),
                                start=lambda: calls.append("start"))
    app = types.SimpleNamespace(sm=types.SimpleNamespace(screens=[], current="home"),
                                probe_screen=scr, switch_mode=lambda m: calls.append(m),
                                _reset_idle=lambda: None)
    sock = R.ControlServer.__new__(R.ControlServer)
    sock.app = app
    assert sock._apply("probe", None) == "ok probe Checking: X"
    assert calls == ["probe", "start"]
    scr.run_btn.disabled = True
    assert sock._apply("probe", None) == "err probe already running"
    assert calls == ["probe", "start"]


def test_the_socket_is_private_to_the_ui_user():
    src = open("ui/remote.py").read()
    assert "0o600" in src and "AF_UNIX" in src


def test_chat_tick_is_not_scheduled_as_a_lambda_that_returns_false():
    """Kivy cancels an interval whose callback returns False; tick() returns
    False when rate-limited. The propagation node was asked once and never again
    (2026-10-01)."""
    src = open("ui/app.py").read()
    assert "lambda dt: self._chat.tick()" not in src
    assert "Clock.schedule_interval(self._chat_tick, 60)" in src
    body = src[src.index("def _chat_tick"):src.index("def _refresh_chat_badge")]
    assert not any(l.strip().startswith("return") for l in body.splitlines())


def test_a_socket_navigation_wakes_the_screensaver_first():
    """Three walkthrough captures were the screensaver's rings: `open` changed
    the screen under the saver (2026-10-03)."""
    src = open("ui/remote.py").read()
    i = src.index("def _apply")
    body = src[i:src.index('if verb == "list"')]
    assert "_dismiss_screensaver()" in body and "_reset_idle()" in body
