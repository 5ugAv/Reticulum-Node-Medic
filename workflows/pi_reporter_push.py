"""Push the health reporter to an EXISTING Pi node — the update path that
birth never needed (docs/HEALTH_REPLY_UNICAST.md, 2026-09-22).

Birth copies the reporter package once (workflows/build.py,
``_push_health_package``) and installs the rnm-health service. When the
reporter changes — the unicast health reply, 2026-09-21 — every Pi node
already in the field needs the new files and a service restart, without a
rebirth and without a cable: the same SSH road birth used, driven from the
medic (which holds the key) or from any machine that can log in.

Pure over a connection object (``run`` -> ``(code, stdout, stderr)``,
``push_file`` -> bool — transport.connection's contract), so it is tested
with a recording fake that returns the SAME shape; the real one is
transport.connection.SSHConnection. (The first cut assumed an object with
an attribute for stdout and its fake agreed with it; the first real run
against ELSEWHERE did not — 2026-09-22. A fake must mirror the contract,
not the author's guess.)
"""
from __future__ import annotations

import os
from typing import Callable, List, Optional, Tuple

from workflows.build import _HEALTH_MODULES

SERVICE = "rnm-health"
#: The one line that proves the NEW reporter landed — the unicast handler.
MARKER = "def make_command_handler("


def _priv(conn, command: str) -> str:
    """sudo -n unless already root; -n fails fast instead of hanging."""
    try:
        who = (conn.run("id -un")[1] or "").strip()
    except Exception:                                                  # noqa: BLE001
        who = ""
    return command if who == "root" else f"sudo -n {command}"


def push_health_reporter(conn, monitor_dir: Optional[str] = None,
                         log: Optional[Callable[[str], None]] = None
                         ) -> Tuple[bool, str]:
    """Copy the reporter package, restart the service, and PROVE both:
    the marker line is on the node and the service reports active. Returns
    (ok, message); nothing is claimed that was not read back."""
    _log = log or (lambda m: None)
    mon_dir = monitor_dir or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "monitor")
    home = (conn.run("echo $HOME")[1] or "").strip() or "/home/pi"
    pkg_dir = f"{home}/.rnm-health/monitor"
    conn.run(f"mkdir -p {pkg_dir}")
    conn.run(f"touch {pkg_dir}/__init__.py")
    pushed: List[str] = []
    for name in _HEALTH_MODULES:
        local = os.path.join(mon_dir, name)
        if not os.path.isfile(local):
            continue
        if not conn.push_file(local, f"{pkg_dir}/{name}"):
            return False, f"could not copy {name} to the node"
        pushed.append(name)
    _log("pushed %d reporter modules" % len(pushed))
    # Read back: the unicast handler must be in the file that landed.
    marker = conn.run(f"grep -c '{MARKER}' {pkg_dir}/pi_health_reporter.py")
    if (marker[1] or "").strip() != "1":
        return False, "the new reporter did not land (unicast handler missing on the node)"
    conn.run(_priv(conn, f"systemctl restart {SERVICE}"))
    state = (conn.run(f"systemctl is-active {SERVICE}")[1] or "").strip()
    if state != "active":
        return False, f"{SERVICE} is '{state or 'unknown'}' after restart — check journalctl -u {SERVICE}"
    _log("%s restarted and active" % SERVICE)
    return True, "reporter updated (%d files) — %s active; the node now answers health requests by unicast" % (
        len(pushed), SERVICE)
