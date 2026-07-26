"""Scoped sudo for a provisioned NODE — the fix for the birth-time ``NOPASSWD:ALL``.

``provisioning/link.py`` bootstraps a fresh node with ``<user> ALL=(ALL)
NOPASSWD:ALL`` because provisioning genuinely needs broad root (install packages,
write configs, enable services). But leaving that blanket grant in place forever is
the risk the security audit flagged as **C2**: any process that gets a toehold on
the node (a compromised rnsd/lxmd, a stray service) then has unconditional root via
the medic's key.

This module tightens it. Once a node is set up, the medic only ever runs a FIXED
set of commands on it over SSH — service control, a couple of package installs, one
config write, and read-only diagnostics. So we grant exactly those (arg-constrained
``Cmnd_Alias`` entries) and drop the blanket. Same shape as the medic's own scoped
sudoers (``provisioning/sudoers.d/nodemedic``).

The command list is derived from every privileged medic→node call in ``workflows/``
+ ``diagnostics/`` (systemctl, apt-get for the exact packages, tee to gpsd's config,
ss/dmesg). Like the medic's, it is HAND-MAINTAINED: a NEW privileged node command
must be added here or it will silently fail once a node is scoped. Re-grep before
relying on it, and validate on a real birth (see ``apply_scoped_node_sudo``'s
anti-lockout: it never drops the blanket unless the scoped file passes ``visudo``).

Access is via an injected ``Connection`` (SSH to the node, or EmulatedConnection in
tests), so the command sequence is unit-testable without a live node. NOT yet wired
into the live birth flow — dormant until sandbox- and real-node-validated.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from typing import List

#: Where the scoped policy lands, replacing the blanket file link.py writes.
SCOPED_PATH = "/etc/sudoers.d/010-nodemedic"
BLANKET_PATH = "/etc/sudoers.d/010-nodemedic-nopasswd"
_TMP_PATH = "/tmp/.nodemedic-sudoers.new"


def scoped_sudoers_text(user: str) -> str:
    """The arg-constrained sudoers policy for *user* (the node's login). systemctl
    is allowed broadly (bounded to the binary — it can't write unit files because
    ``tee`` is pinned to one path), apt-get only for the exact packages we install,
    tee only to gpsd's config, and the diagnostic readers."""
    return "\n".join([
        "# Managed by Node Medic — scoped access (replaces NOPASSWD:ALL). C2 fix.",
        "# Only the fixed set of commands the medic runs on a provisioned node.",
        "Cmnd_Alias NM_SYSTEMCTL = /usr/bin/systemctl, /bin/systemctl",
        "Cmnd_Alias NM_APT = /usr/bin/apt-get update, "
        "/usr/bin/apt-get install -y gpsd gpsd-clients, "
        "/usr/bin/apt-get install -y lrzsz",
        "Cmnd_Alias NM_GPSD = /usr/bin/tee /etc/default/gpsd",
        "Cmnd_Alias NM_DIAG = /usr/bin/ss -tlnp, /usr/bin/ss -tlnp *, "
        "/bin/dmesg *, /usr/bin/dmesg *",
        "Cmnd_Alias NM_REBOOT = /sbin/reboot, /usr/sbin/reboot",
        f"{user} ALL=(ALL) NOPASSWD: NM_SYSTEMCTL, NM_APT, NM_GPSD, "
        "NM_DIAG, NM_REBOOT",
        "",
    ])


@dataclass
class ScopeResult:
    ok: bool
    scoped: bool = False          # scoped file installed + validated
    blanket_removed: bool = False
    steps: List[str] = field(default_factory=list)
    message: str = ""


def apply_scoped_node_sudo(connection, user: str) -> ScopeResult:
    """Replace a node's blanket ``NOPASSWD:ALL`` with the scoped policy, over an
    injected *connection* (which still has broad sudo at call time — this is the
    LAST provisioning step). Anti-lockout: the blanket is dropped ONLY after the
    scoped file is written AND ``visudo -cf`` validates it, so a bad policy can
    never strand the node without working sudo."""
    text = scoped_sudoers_text(user)
    b64 = base64.b64encode(text.encode()).decode()
    res = ScopeResult(ok=True)

    # 1) stage the scoped file to a temp path (base64 avoids all shell quoting)
    rc, _, _ = connection.run(
        f"echo {b64} | base64 -d | sudo -n tee {_TMP_PATH} >/dev/null")
    if rc != 0:
        return ScopeResult(ok=False, steps=["stage FAILED"],
                           message="Could not stage the scoped sudoers file.")
    res.steps.append("scoped file staged")

    # 2) validate BEFORE touching the live policy — a syntax error must not land
    rc, _, _ = connection.run(f"sudo -n visudo -cf {_TMP_PATH}")
    if rc != 0:
        connection.run(f"rm -f {_TMP_PATH}")
        return ScopeResult(ok=False, steps=res.steps + ["visudo REJECTED scoped"],
                           message="Scoped sudoers failed visudo — blanket kept, "
                                   "node untouched.")
    res.steps.append("visudo validated scoped")

    # 3) install the validated file (0440), then re-validate the WHOLE /etc/sudoers
    rc, _, _ = connection.run(
        f"sudo -n install -m 440 -o root -g root {_TMP_PATH} {SCOPED_PATH}")
    if rc != 0:
        connection.run(f"rm -f {_TMP_PATH}")
        return ScopeResult(ok=False, steps=res.steps + ["install FAILED"],
                           message="Could not install the scoped file — blanket kept.")
    res.scoped = True
    res.steps.append("scoped installed")

    rc, _, _ = connection.run("sudo -n visudo -c")
    if rc != 0:
        # the combined policy is broken — pull our scoped file back out, keep blanket
        connection.run(f"sudo -n rm -f {SCOPED_PATH}")
        connection.run(f"rm -f {_TMP_PATH}")
        return ScopeResult(ok=False, scoped=False,
                           steps=res.steps + ["combined visudo FAILED — reverted"],
                           message="Combined sudoers invalid — scoped removed, "
                                   "blanket kept.")
    res.steps.append("combined policy valid")

    # 4) NOW it's safe to drop the blanket grant
    rc, _, _ = connection.run(f"sudo -n rm -f {BLANKET_PATH}")
    res.blanket_removed = rc == 0
    res.steps.append("blanket removed" if res.blanket_removed
                     else "blanket removal FAILED (scoped is in force anyway)")
    connection.run(f"rm -f {_TMP_PATH}")

    # 5) prove sudo still works under the scoped policy
    rc, _, _ = connection.run("sudo -n systemctl is-active rnsd")
    # is-active returns non-zero if the service is inactive, but a scoped-out sudo
    # returns a sudo error — distinguish by asking for a definitely-allowed no-op.
    rc_true, _, _ = connection.run("sudo -n systemctl --version")
    if rc_true != 0:
        res.ok = False
        res.message = ("Scoped installed but a known-allowed sudo command failed — "
                       "check the Cmnd_Alias paths against this node's binaries.")
        return res

    res.ok = res.scoped
    res.message = ("Node sudo scoped — blanket NOPASSWD:ALL replaced with the medic's "
                   "fixed command set." if res.blanket_removed else
                   "Scoped policy installed (blanket file could not be removed — "
                   "remove /etc/sudoers.d/010-nodemedic-nopasswd manually).")
    return res
