"""Push the health reporter to an EXISTING Pi node — the update path that
birth never needed (docs/HEALTH_REPLY_UNICAST.md, 2026-09-22).

Birth copies the reporter package once (workflows/build.py,
``_push_health_package``) and installs the rnm-health service. When the
reporter changes — the unicast health reply, 2026-09-21 — every Pi node
already in the field needs the new files and a service restart, without a
rebirth and without a cable: the same SSH road birth used, driven from the
medic (which holds the key) or from any machine that can log in.

Since 2026-09-23 it also carries time over the mesh: the trust file naming
this medic, the clock helper and its sudoers line — the same three things
birth's install_time_trust writes, through the same installer, read back
the same way (content, owner and mode). The user and HOME the trust is
written for are the ones the rnm-health UNIT runs as (`systemctl show`),
not whoever logged in to push — the two differ on a node built as one
account and administered from another (review, 2026-09-23); `id -un` /
`$HOME` are the fallback only when the unit is absent.

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
import shlex
from typing import Callable, List, Optional, Tuple

from workflows import build
from workflows.build import _HEALTH_MODULES, install_node_time_trust

SERVICE = "rnm-health"
#: The one line that proves the NEW reporter landed — the unicast handler.
MARKER = "def make_command_handler("
#: ...and the one that proves the time asker landed (2026-09-23).
TIME_MARKER = "def make_time_asker("


#: A drop-in beside the unit, for nodes born before the unit itself carried
#: PYTHONUNBUFFERED (2026-09-23). systemd merges it; the unit file is not
#: rewritten by this road, so the fix rides as a drop-in.
UNBUFFERED_DROPIN_PATH = f"/etc/systemd/system/{SERVICE}.service.d/10-unbuffered.conf"
UNBUFFERED_DROPIN = "[Service]\nEnvironment=PYTHONUNBUFFERED=1\n"


def ensure_unbuffered(conn, priv) -> Tuple[bool, str]:
    """Make the reporter's stdout unbuffered under systemd, and prove it.

    Block-buffered stdout meant the reporter's NOTICE lines reached the
    journal in 8 KB batches, a minute or more late — the bench proof of the
    time exchange (2026-09-23) showed sudo's record of nm-settime and not
    the reporter's own. Nodes born from now on carry the line in the unit;
    an older node gets a drop-in, daemon-reloaded and read back. Returns
    (ok, what was done).
    """
    env = conn.run(f"systemctl show {SERVICE} -p Environment")[1] or ""
    if "PYTHONUNBUFFERED=1" in env:
        return True, "reporter already unbuffered (unit environment)"
    d = os.path.dirname(UNBUFFERED_DROPIN_PATH)
    conn.run(priv(f"mkdir -p {d}"))
    code, out, err = conn.run(
        f"printf %s {shlex.quote(UNBUFFERED_DROPIN)} | {priv(f'tee {UNBUFFERED_DROPIN_PATH}')} >/dev/null")
    if code != 0:
        return False, f"could not write the unbuffered drop-in: {err or out}"
    got = conn.run(f"cat {UNBUFFERED_DROPIN_PATH}")[1] or ""
    if got != UNBUFFERED_DROPIN:
        return False, "the unbuffered drop-in did not read back as written"
    conn.run(priv("systemctl daemon-reload"))
    env = conn.run(f"systemctl show {SERVICE} -p Environment")[1] or ""
    if "PYTHONUNBUFFERED=1" not in env:
        return False, ("wrote the unbuffered drop-in and reloaded, but the unit's "
                       "environment does not show it")
    return True, "reporter made unbuffered (drop-in written, reloaded, read back)"


def _priv(conn, command: str) -> str:
    """sudo -n unless already root; -n fails fast instead of hanging."""
    try:
        who = (conn.run("id -un")[1] or "").strip()
    except Exception:                                                  # noqa: BLE001
        who = ""
    return command if who == "root" else f"sudo -n {command}"


def parse_unit_user_home(text: str) -> Tuple[Optional[str], Optional[str]]:
    """(User, HOME) from `systemctl show rnm-health -p User -p Environment`:
    ``User=pi`` and ``Environment=HOME=/home/pi FOO=bar``. None for either
    the unit does not state."""
    user = home = None
    for line in (text or "").splitlines():
        line = line.strip()
        if line.startswith("User="):
            user = line[len("User="):].strip() or None
        elif line.startswith("Environment="):
            for kv in line[len("Environment="):].split():
                if kv.startswith("HOME="):
                    home = kv[len("HOME="):].strip() or None
    return user, home


def reporter_user_home(conn) -> Tuple[str, str, str]:
    """(user, home, how): from the unit when it exists, else the login
    user's own — and *how* says which, so the message can."""
    try:
        code, out, _ = conn.run(f"systemctl show {SERVICE} -p User -p Environment")
    except Exception:                                                  # noqa: BLE001
        code, out = 1, ""
    user, home = parse_unit_user_home(out) if code == 0 else (None, None)
    if user:
        if not home:
            home = "/root" if user == "root" else f"/home/{user}"
        return user, home, "from the %s unit" % SERVICE
    login = (conn.run("id -un")[1] or "").strip() or "pi"
    home = (conn.run("echo $HOME")[1] or "").strip() or f"/home/{login}"
    return login, home, "from the login user (no %s unit found)" % SERVICE


def push_health_reporter(conn, monitor_dir: Optional[str] = None,
                         log: Optional[Callable[[str], None]] = None
                         ) -> Tuple[bool, str]:
    """Copy the reporter package, install the time trust, restart the
    service, and PROVE each: the marker lines are on the node, the three
    trust files read back (content, owner, mode), the service reports
    active. Returns (ok, message); nothing is claimed that was not read
    back."""
    _log = log or (lambda m: None)
    mon_dir = monitor_dir or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "monitor")
    user, home, how = reporter_user_home(conn)
    _log("reporter runs as %s with HOME %s (%s)" % (user, home, how))
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
    # Read back: the unicast handler and the time asker must be in the file
    # that landed.
    for marker, what in ((MARKER, "unicast handler"), (TIME_MARKER, "time asker")):
        got = conn.run(f"grep -c '{marker}' {pkg_dir}/pi_health_reporter.py")
        if (got[1] or "").strip() != "1":
            return False, f"the new reporter did not land ({what} missing on the node)"
    # The time trust — the medic's OWN anchor, loaded with RNS or not at all.
    # Through the module so a test can stand in for the real medic.
    try:
        anchor = build.medic_time_anchor()
    except Exception as e:                                             # noqa: BLE001
        return False, f"could not load this medic's time-trust anchor: {e}"
    priv = lambda c: _priv(conn, c)                                    # noqa: E731
    ok, msg = install_node_time_trust(conn, priv, user, home, anchor)
    if not ok:
        return False, msg
    _log("time trust installed and read back")
    ok, msg = ensure_unbuffered(conn, priv)
    if not ok:
        return False, msg
    _log(msg)
    conn.run(_priv(conn, f"systemctl restart {SERVICE}"))
    state = (conn.run(f"systemctl is-active {SERVICE}")[1] or "").strip()
    if state != "active":
        return False, f"{SERVICE} is '{state or 'unknown'}' after restart — check journalctl -u {SERVICE}"
    _log("%s restarted and active" % SERVICE)
    verified, problems, unchecked = build.time_trust_readback(conn, priv, user, home, anchor)
    if problems:
        return False, ("reporter updated and %s active, but the time trust did not read "
                       "back after the restart: %s" % (SERVICE, "; ".join(problems)))
    tail = (" Not checked: " + "; ".join(unchecked) + "." if unchecked else "")
    return True, ("reporter updated (%d files, unicast handler and time asker read back) — "
                  "%s active as %s; time trust read back (%s): the node takes the time "
                  "only from this medic.%s" % (len(pushed), SERVICE, user,
                                               "; ".join(verified), tail))


def reporter_is_current(conn, monitor_dir: Optional[str] = None) -> bool:
    """Does this node carry EXACTLY the reporter the medic would push?

    Byte identity, module by module: the node's copy of every file in
    _HEALTH_MODULES must hash the same as the medic's. The first version of
    this grepped for two marker lines and said "current" for any file that had
    them — which on 2026-09-29 answered "nothing to do" for a node whose
    reporter lacked the whole neighbour report, because the markers it looked
    for were from a week earlier. A marker proves one feature landed once;
    only the hash proves the file is the file.

    Answers FALSE when it cannot tell. A node that does not respond is not
    evidence that it is current; a wrong "no" costs an idempotent push, a
    wrong "yes" leaves a stale node stale for good.
    """
    import hashlib
    mon_dir = monitor_dir or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "monitor")
    try:
        _user, home, _how = reporter_user_home(conn)
        pkg_dir = f"{home}/.rnm-health/monitor"
        for name in _HEALTH_MODULES:
            with open(os.path.join(mon_dir, name), "rb") as fh:
                local = hashlib.md5(fh.read()).hexdigest()
            code, out, _err = conn.run(f"md5sum {pkg_dir}/{name} 2>/dev/null")
            remote = (out or "").split()[0] if code == 0 and out else ""
            if remote != local:
                return False
        return True
    except Exception:                                                  # noqa: BLE001
        return False
