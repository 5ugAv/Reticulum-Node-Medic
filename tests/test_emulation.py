import pytest

from transport.connection import Connection, EmulatedConnection


def test_emulated_is_a_connection():
    assert isinstance(EmulatedConnection(), Connection)


def test_rule_is_chainable():
    conn = EmulatedConnection()
    ret = conn.rule("foo").rule("bar")
    assert ret is conn


def test_substring_rule_matches_anywhere():
    conn = EmulatedConnection().rule("ttyUSB0", code=0, stdout="present")
    code, out, err = conn.run("test -c /dev/ttyUSB0")
    assert code == 0
    assert out == "present"


def test_first_match_wins_in_insertion_order():
    conn = (
        EmulatedConnection()
        .rule("^test -c /dev/ttyUSB0", code=0, stdout="prefix-win")
        .rule("ttyUSB0", code=1, stdout="substr-win")
    )
    code, out, err = conn.run("test -c /dev/ttyUSB0")
    assert out == "prefix-win"
    assert code == 0


def test_prefix_rule_only_matches_start():
    conn = EmulatedConnection().rule("^systemctl is-active", code=0, stdout="active")
    # command that contains but does not start with the pattern
    code, out, err = conn.run("echo systemctl is-active rnsd")
    assert out != "active"


def test_prefix_rule_matches_when_at_start():
    conn = EmulatedConnection().rule("^systemctl is-active", code=0, stdout="active")
    code, out, err = conn.run("systemctl is-active rnsd")
    assert out == "active"
    assert code == 0


def test_unmatched_command_default_is_failure():
    conn = EmulatedConnection()
    code, out, err = conn.run("anything at all")
    assert code != 0


def test_custom_default_for_unmatched():
    conn = EmulatedConnection(default_code=0, default_stdout="fallback")
    code, out, err = conn.run("whatever")
    assert code == 0
    assert out == "fallback"


def test_stderr_is_returned():
    conn = EmulatedConnection().rule("boom", code=2, stdout="", stderr="exploded")
    code, out, err = conn.run("boom now")
    assert code == 2
    assert err == "exploded"


def test_run_checked_raises_on_nonzero():
    conn = EmulatedConnection().rule("badcmd", code=3, stderr="nope")
    with pytest.raises(RuntimeError):
        conn.run_checked("badcmd")


def test_run_checked_returns_stdout_on_success():
    conn = EmulatedConnection().rule("goodcmd", code=0, stdout="yay")
    assert conn.run_checked("goodcmd") == "yay"
