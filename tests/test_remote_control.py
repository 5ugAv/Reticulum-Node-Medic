"""The control socket's pure parts: what it accepts, and how it finds a node."""
from ui import remote as R


def test_commands_parse_exactly():
    assert R.parse_command("ping") == ("ping", None)
    assert R.parse_command("  OPEN  vitals ") == ("open", "vitals")
    assert R.parse_command("node c627") == ("node", "c627")
    assert R.parse_command("list") == ("list", None)
    assert R.parse_command("home") == ("home", None)
    assert R.parse_command("current") == ("current", None)


def test_junk_and_half_commands_are_refused():
    for bad in ("", "   ", "flash", "open", "node", "ping now", "home please", "rm -rf /"):
        assert R.parse_command(bad) == ("", None), bad


def test_a_node_prefix_must_be_unambiguous_and_long_enough():
    hashes = ["5a11001100000000000000000000000b", "5a119999" + "0" * 24, "5a120012" + "0" * 24]
    assert R.resolve_node("f7b0", hashes) == (hashes[2], "")
    h, why = R.resolve_node("c627", hashes)
    assert h is None and "2 nodes" in why
    h, why = R.resolve_node("c62", hashes)
    assert h is None and "4 hex" in why
    h, why = R.resolve_node("zzzz", hashes)
    assert h is None
    h, why = R.resolve_node("abcd", hashes)
    assert h is None and "no node" in why


def test_it_can_only_move_between_screens():
    """The verb list IS the capability list. Nothing here flashes, wipes or
    deletes; adding such a verb is a security decision, not a convenience."""
    assert set(R.VERBS) == {"ping", "list", "open", "node", "home", "current"}


def test_the_socket_is_private_to_the_ui_user():
    src = open("ui/remote.py").read()
    assert "0o600" in src and "AF_UNIX" in src


def test_chat_tick_is_not_scheduled_as_a_lambda_that_returns_false():
    """Kivy cancels an interval whose callback returns False; tick() returns
    False when rate-limited. The post office was asked once and never again
    (2026-10-01)."""
    src = open("ui/app.py").read()
    assert "lambda dt: self._chat.tick()" not in src
    assert "Clock.schedule_interval(self._chat_tick, 60)" in src
    body = src[src.index("def _chat_tick"):src.index("def _refresh_chat_badge")]
    assert not any(l.strip().startswith("return") for l in body.splitlines())
