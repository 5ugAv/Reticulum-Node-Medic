"""Scoped node sudo (C2 fix) — the arg-constrained policy + the anti-lockout
apply sequence, driven by an EmulatedConnection (no live node)."""

import base64

from provisioning.node_sudoers import (
    scoped_sudoers_text, apply_scoped_node_sudo, SCOPED_PATH, BLANKET_PATH)
from transport.connection import EmulatedConnection


USER = "everywhere"


# -- the policy text ---------------------------------------------------------

def test_policy_covers_every_privileged_node_command():
    t = scoped_sudoers_text(USER)
    assert "/usr/bin/systemctl" in t                      # rnsd/lxmd/gpsd control
    assert "apt-get install -y gpsd gpsd-clients" in t    # gps setup
    assert "apt-get install -y lrzsz" in t                # serial transfer tool
    assert "/usr/bin/tee /etc/default/gpsd" in t          # the one config write
    assert "ss -tlnp" in t and "dmesg" in t               # diagnostics reads


def test_policy_is_actually_scoped_not_blanket():
    t = scoped_sudoers_text(USER)
    # inspect only the directive lines, not the explanatory comments
    directives = "\n".join(l for l in t.splitlines() if not l.lstrip().startswith("#"))
    assert "NOPASSWD:ALL" not in directives.replace(" ", "")   # no blanket grant
    assert "ALL=(ALL) NOPASSWD:ALL" not in directives
    # no arbitrary-shell escape hatches
    for danger in ("/bin/bash", "/bin/sh", "bash -c", "ALL) NOPASSWD: ALL\n"):
        assert danger not in t
    assert f"{USER} ALL=(ALL) NOPASSWD: NM_" in t         # grants only the aliases


def test_apt_is_pinned_to_exact_packages_not_wildcard():
    # `apt-get install -y *` would let a node install a local malicious .deb as
    # root — the packages must be pinned.
    t = scoped_sudoers_text(USER)
    assert "apt-get install -y *" not in t


# -- the apply sequence ------------------------------------------------------

def _all_ok():
    # every command succeeds; is-active may be non-zero in reality but --version
    # (the liveness probe) must pass
    return EmulatedConnection(default_code=0, default_stdout="ok")


def test_happy_path_installs_scoped_and_drops_blanket():
    c = _all_ok()
    res = apply_scoped_node_sudo(c, USER)
    assert res.ok and res.scoped and res.blanket_removed
    joined = " || ".join(c.history)
    # staged -> validated -> installed -> combined-validated -> blanket removed
    assert "base64 -d | sudo -n tee" in joined
    assert f"visudo -cf" in joined
    assert f"install -m 440 -o root -g root" in joined and SCOPED_PATH in joined
    assert f"rm -f {BLANKET_PATH}" in joined


def test_staged_file_decodes_to_the_policy():
    c = _all_ok()
    apply_scoped_node_sudo(c, USER)
    stage = c.history[0]
    b64 = stage.split("echo ", 1)[1].split(" |", 1)[0]
    assert base64.b64decode(b64).decode() == scoped_sudoers_text(USER)


def test_antilockout_bad_policy_keeps_blanket():
    # visudo rejects the staged scoped file -> we must NOT remove the blanket
    c = EmulatedConnection(default_code=0, default_stdout="ok")
    c.rule("visudo -cf", code=1, stderr="parse error")
    res = apply_scoped_node_sudo(c, USER)
    assert not res.ok and not res.scoped
    joined = " || ".join(c.history)
    assert f"rm -f {BLANKET_PATH}" not in joined           # blanket untouched
    assert f"install -m 440" not in joined                 # never installed


def test_combined_invalid_reverts_scoped_and_keeps_blanket():
    # staged file validates, but the COMBINED /etc/sudoers is broken after install
    c = EmulatedConnection(default_code=0, default_stdout="ok")
    c.rule("visudo -cf", code=0)          # staged file is fine...
    c.rule("visudo -c", code=1)           # ...but the whole policy is not
    res = apply_scoped_node_sudo(c, USER)
    assert not res.ok
    joined = " || ".join(c.history)
    assert f"rm -f {SCOPED_PATH}" in joined                # pulled our file back out
    assert f"rm -f {BLANKET_PATH}" not in joined           # blanket kept


def test_stage_failure_touches_nothing():
    c = EmulatedConnection(default_code=0, default_stdout="ok")
    c.rule("base64 -d | sudo -n tee", code=1)
    res = apply_scoped_node_sudo(c, USER)
    assert not res.ok and not res.scoped
    joined = " || ".join(c.history)
    assert "visudo" not in joined and BLANKET_PATH not in joined
