"""Updating the health reporter on an existing Pi node without a rebirth
(docs/HEALTH_REPLY_UNICAST.md, 2026-09-22)."""
import pytest

import monitor.node_time as nt
from workflows import build
from workflows import pi_reporter_push as prp

#: The push now also carries the time trust (2026-09-23); the medic's real
#: anchor needs RNS and the real identity file, so every test here stands
#: in a fixed one. tests/test_time_trust_build.py holds the trust to its
#: own read-backs; this file keeps holding the reporter push.
_ANCHOR = {"identity_hash": "ab" * 16, "reply_dest": "cd" * 16,
           "name": "nodemedic", "since": "2026-09-23"}


@pytest.fixture(autouse=True)
def _anchor(monkeypatch):
    monkeypatch.setattr(build, "medic_time_anchor", lambda: dict(_ANCHOR))


class FakeConn:
    """Returns transport.connection's REAL shape: (code, stdout, stderr)."""
    def __init__(self, marker_count="1", active="active", who="pi", push_ok=True):
        self.cmds, self.pushed = [], []
        self._marker, self._active, self._who, self._push_ok = marker_count, active, who, push_ok

    def run(self, cmd, timeout=30):
        self.cmds.append(cmd)
        if cmd == "id -un":
            return (0, self._who, "")
        if cmd == "echo $HOME":
            return (0, "/home/pi", "")
        if cmd.startswith("systemctl show rnm-health"):
            # the unit runs as the login user here (2026-09-23: the push
            # derives user/HOME from the unit, not from who logged in)
            unb = " PYTHONUNBUFFERED=1" if getattr(self, "_unbuffered", False) else ""
            return (0, "User=%s\nEnvironment=HOME=%s%s\n" % (
                self._who, "/root" if self._who == "root" else "/home/pi", unb), "")
        if "10-unbuffered.conf" in cmd and "tee" in cmd:
            self._unbuffered_written = True
            return (0, "", "")
        if cmd.startswith("cat ") and "10-unbuffered.conf" in cmd:
            from workflows.pi_reporter_push import UNBUFFERED_DROPIN
            return (0, UNBUFFERED_DROPIN if getattr(self, "_unbuffered_written", False) else "", "")
        if cmd.endswith("systemctl daemon-reload"):
            self._unbuffered = getattr(self, "_unbuffered_written", False)
            return (0, "", "")
        if "stat -c '%U:%G %a' /usr/local/sbin/nm-settime" in cmd:
            return (0, "root:root 755", "")
        if "stat -c '%U:%G %a' /etc/sudoers.d/nm-settime" in cmd:
            return (0, "root:root 440", "")
        if cmd.startswith("grep -c"):
            return (0, self._marker, "")
        if cmd.startswith("systemctl is-active"):
            return (0, self._active, "")
        # the time trust's three read-backs answer as written
        if "trusted_medic.json" in cmd and cmd.startswith("cat "):
            return (0, nt.trust_anchor_json(_ANCHOR), "")
        if cmd.startswith("cat /usr/local/sbin/nm-settime"):
            return (0, build.NM_SETTIME_SCRIPT, "")
        if "cat /etc/sudoers.d/nm-settime" in cmd:
            return (0, build.nm_settime_sudoers(self._who), "")
        return (0, "", "")

    def push_file(self, local, remote):
        self.pushed.append(remote)
        return self._push_ok


def test_push_copies_every_module_restarts_and_reads_back():
    c = FakeConn()
    ok, msg = prp.push_health_reporter(c)
    assert ok, msg
    names = {r.rsplit("/", 1)[1] for r in c.pushed}
    assert {"pi_health_reporter.py", "health_reply.py", "health_poll.py",
            "health_beacon.py"} <= names
    assert any(cmd == "sudo -n systemctl restart rnm-health" for cmd in c.cmds)
    assert "unicast" in msg


def test_root_needs_no_sudo():
    c = FakeConn(who="root")
    assert prp.push_health_reporter(c)[0]
    assert any(cmd == "systemctl restart rnm-health" for cmd in c.cmds)


def test_a_missing_marker_or_a_dead_service_is_a_failure_not_a_claim():
    c = FakeConn(marker_count="0")
    ok, msg = prp.push_health_reporter(c)
    assert not ok and "did not land" in msg
    assert not any("restart" in cmd for cmd in c.cmds), "no restart on a bad copy"
    c = FakeConn(active="failed")
    ok, msg = prp.push_health_reporter(c)
    assert not ok and "failed" in msg


def test_a_failed_copy_stops_early():
    c = FakeConn(push_ok=False)
    ok, msg = prp.push_health_reporter(c)
    assert not ok and "could not copy" in msg


def test_the_currency_check_fails_closed():
    """A node that will not answer is not evidence that it is up to date."""
    class Mute:
        def run(self, cmd):
            raise OSError("no route to host")
    assert prp.reporter_is_current(Mute()) is False


def test_the_currency_check_is_byte_identity_not_a_marker(tmp_path):
    """The marker version said "current" on 2026-09-29 for a node whose
    reporter lacked the whole neighbour report. Only the hash proves the file
    is the file: one stale byte in one module -> not current."""
    import hashlib
    mon = tmp_path / "monitor"; mon.mkdir()
    for name in prp._HEALTH_MODULES:
        (mon / name).write_bytes(b"# " + name.encode() + b"\n")
    good = {name: hashlib.md5((mon / name).read_bytes()).hexdigest()
            for name in prp._HEALTH_MODULES}

    class Node:
        def __init__(self, stale=None): self.stale = stale
        def run(self, cmd):
            if "systemctl show" in cmd: return (0, "User=pi\nEnvironment=HOME=/home/pi\n", "")
            if cmd.startswith("md5sum "):
                import re
                name = re.search(r"monitor/([\w.]+)", cmd).group(1)
                h = "deadbeef" if name == self.stale else good.get(name, "")
                return (0, f"{h}  x\n", "")
            return (0, "", "")
    assert prp.reporter_is_current(Node(), monitor_dir=str(mon)) is True
    assert prp.reporter_is_current(Node(stale="pi_health_reporter.py"), monitor_dir=str(mon)) is False

def test_the_fake_matches_the_real_connection_contract():
    """transport.connection.Result is (code, stdout, stderr); the step must
    index it, never attribute it — and the fake is held to the same shape."""
    r = FakeConn().run("echo $HOME")
    assert isinstance(r, tuple) and len(r) == 3
    src = open("workflows/pi_reporter_push.py").read()
    assert not any(".stdout" in l for l in src.splitlines() if "conn.run" in l)


def test_the_push_makes_an_older_reporter_unbuffered_and_proves_it():
    """Bench proof 2026-09-23: the node's journal showed sudo's nm-settime
    line and not the reporter's own NOTICE lines — Python block-buffers a
    pipe. An older unit gets a drop-in, reloaded and read back through the
    unit's environment; a unit that already carries it is left alone."""
    from workflows import pi_reporter_push as prp
    c = FakeConn()
    ok, msg = prp.push_health_reporter(c, log=lambda m: None)
    assert ok, msg
    assert any("10-unbuffered.conf" in cmd and "tee" in cmd for cmd in c.cmds)
    reload_i = next(i for i, cmd in enumerate(c.cmds) if cmd.endswith("systemctl daemon-reload"))
    restart_i = next(i for i, cmd in enumerate(c.cmds) if cmd.endswith("systemctl restart rnm-health"))
    assert reload_i < restart_i, "reload before the restart, or the restart runs the old unit"
    c2 = FakeConn(); c2._unbuffered = True
    ok, msg = prp.push_health_reporter(c2, log=lambda m: None)
    assert ok, msg
    assert not any("10-unbuffered.conf" in cmd for cmd in c2.cmds)


def test_a_drop_in_that_does_not_read_back_fails_the_push():
    from workflows import pi_reporter_push as prp
    c = FakeConn()
    real = c.run
    def run(cmd, timeout=30):
        if cmd.startswith("cat ") and "10-unbuffered.conf" in cmd:
            c.cmds.append(cmd); return (0, "garbage", "")
        return real(cmd, timeout)
    c.run = run
    ok, msg = prp.push_health_reporter(c, log=lambda m: None)
    assert not ok and "did not read back" in msg


def test_a_new_birth_writes_the_reporter_unit_unbuffered():
    from tests.test_build_workflow import build_conn, wf, _run_step
    from node_profile import NodeProfile, NodeRole
    conn = build_conn(rnode=True)
    conn.rules.insert(0, ("RNS.Destination.IN", 0, "aa" * 16, ""))
    p = NodeProfile(); p.role = NodeRole.PROPAGATION
    w = wf(conn, p); w.steps[0][1](w)
    r = _run_step(w, "install_health_reporter")
    assert r.success
    unit = [c for c in conn.history if "tee /etc/systemd/system/rnm-health.service" in c]
    assert unit and "PYTHONUNBUFFERED=1" in unit[-1]
