"""Home / Backpack mode switching — no real config or daemon (EmulatedConnection)."""

from transport.connection import EmulatedConnection
from workflows.node_mode import (
    set_mode, current_mode, normalise, save_home_profile, load_home_profile,
    HOME, BACKPACK, PROPAGATION, TRANSPORT,
    RNS_CONFIG, LXMD_CONFIG, MODE_FILE, HOME_PROFILE_FILE,
)


def _conn():
    c = EmulatedConnection(default_code=0, default_stdout="ok")
    c.rule("systemctl restart", 0, "")
    return c


def test_home_propagation_profile_enables_both():
    c = _conn()
    res = set_mode("home", c, home_profile=PROPAGATION)
    assert res.ok and res.mode == HOME
    joined = " ".join(c.history)
    assert "enable_transport = Yes" in joined and RNS_CONFIG in joined
    assert "enable_node = yes" in joined and LXMD_CONFIG in joined
    assert "systemctl restart rnsd" in joined
    assert "propagation node" in res.message.lower()


def test_home_transport_profile_is_routing_only():
    c = _conn()
    res = set_mode("home", c, home_profile=TRANSPORT)
    assert res.ok and res.mode == HOME
    joined = " ".join(c.history)
    assert "enable_transport = Yes" in joined       # still routes
    assert "enable_node = no" in joined             # but no store-and-forward
    assert "routing only" in res.message.lower()


def test_backpack_disables_both_regardless_of_profile():
    c = _conn()
    res = set_mode("backpack", c, home_profile=PROPAGATION)
    assert res.ok and res.mode == BACKPACK
    joined = " ".join(c.history)
    assert "enable_transport = No" in joined
    assert "enable_node = no" in joined
    assert "safe to move" in res.message.lower()


def test_home_profile_persists(tmp_path, monkeypatch):
    import workflows.node_mode as nm
    monkeypatch.setattr(nm, "HOME_PROFILE_FILE", str(tmp_path / "home_profile"))
    assert nm.load_home_profile() == PROPAGATION        # default
    assert nm.save_home_profile("transport") == TRANSPORT
    assert nm.load_home_profile() == TRANSPORT
    assert nm.save_home_profile("nonsense") == PROPAGATION  # unknown -> default


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


# -- what lxmd is DOING, not what the mode meant (2026-09-30) -----------------

def test_propagation_running_reads_the_p_flag_off_the_live_process():
    from workflows.node_mode import propagation_running
    c = EmulatedConnection(default_code=0, default_stdout="")
    c.rule("pgrep", 0, "35673 /usr/bin/python3 /home/nodemedic/.local/bin/lxmd -p -s\n")
    c.rule("grep -iE '^[[:space:]]*enable_node'", 0, "enable_node = no\n")
    assert propagation_running(c) is True          # -p wins over the config


def test_propagation_running_falls_back_to_the_config_without_p():
    from workflows.node_mode import propagation_running
    c = EmulatedConnection(default_code=0, default_stdout="")
    c.rule("pgrep", 0, "35673 /usr/bin/python3 /home/nodemedic/.local/bin/lxmd -s\n")
    c.rule("grep -iE '^[[:space:]]*enable_node'", 0, "enable_node = no\n")
    assert propagation_running(c) is False
    c2 = EmulatedConnection(default_code=0, default_stdout="")
    c2.rule("pgrep", 0, "")                         # no process visible
    c2.rule("grep -iE '^[[:space:]]*enable_node'", 0, "enable_node = yes\n")
    assert propagation_running(c2) is True
