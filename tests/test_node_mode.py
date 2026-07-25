"""Home / Backpack mode switching — no real config or daemon (EmulatedConnection)."""

from transport.connection import EmulatedConnection
from workflows.node_mode import (
    set_mode, current_mode, normalise, HOME, BACKPACK,
    RNS_CONFIG, LXMD_CONFIG, MODE_FILE,
)


def _conn():
    c = EmulatedConnection(default_code=0, default_stdout="ok")
    c.rule("systemctl restart", 0, "")
    return c


def test_home_enables_transport_and_propagation():
    c = _conn()
    res = set_mode("home", c)
    assert res.ok and res.mode == HOME
    joined = " ".join(c.history)
    assert f"enable_transport = Yes" in joined and RNS_CONFIG in joined
    assert f"enable_node = yes" in joined and LXMD_CONFIG in joined
    assert "systemctl restart rnsd" in joined
    assert "routing" in res.message.lower()


def test_backpack_disables_both():
    c = _conn()
    res = set_mode("backpack", c)
    assert res.ok and res.mode == BACKPACK
    joined = " ".join(c.history)
    assert "enable_transport = No" in joined
    assert "enable_node = no" in joined
    assert "safe to move" in res.message.lower()


def test_mode_marker_is_persisted():
    c = _conn()
    set_mode("backpack", c)
    assert any(f"> {MODE_FILE}" in cmd and "backpack" in cmd for cmd in c.history)


def test_transport_edit_failure_is_reported():
    c = EmulatedConnection(default_code=0, default_stdout="ok")
    c.rule("systemctl restart", 0, "")
    c.rules.insert(0, ("sed -i -E 's/^([[:space:]]*)enable_transport", 1, "", "err"))
    res = set_mode("home", c)
    assert res.ok is False
    assert "transport edit FAILED" in res.steps


def test_missing_lxmd_config_is_best_effort_not_fatal():
    c = EmulatedConnection(default_code=0, default_stdout="ok")
    c.rule("systemctl restart", 0, "")
    c.rules.insert(0, (LXMD_CONFIG, 2, "", "no such file"))   # lxmd edit fails
    res = set_mode("home", c)
    assert res.ok is True                                     # transport still ok
    assert any("propagation edit skipped" in s for s in res.steps)


def test_restart_can_be_skipped_for_dry_edits():
    c = _conn()
    set_mode("home", c, restart=False)
    assert not any("systemctl restart" in cmd for cmd in c.history)


def test_current_mode_from_marker():
    c = EmulatedConnection(default_code=0, default_stdout="ok")
    c.rule(f"cat {MODE_FILE}", 0, "backpack\n")
    assert current_mode(c) == BACKPACK


def test_current_mode_inferred_from_config_when_no_marker():
    c = EmulatedConnection(default_code=1, default_stdout="")     # no marker file
    c.rule(f"cat {MODE_FILE}", 1, "")
    c.rule("grep -iE '^[[:space:]]*enable_transport'", 0, "enable_transport = Yes")
    assert current_mode(c) == HOME


def test_normalise_defaults_unknown_to_backpack():
    assert normalise("HOME") == HOME
    assert normalise("weird") == BACKPACK
