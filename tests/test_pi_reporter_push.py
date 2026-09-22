"""Updating the health reporter on an existing Pi node without a rebirth
(docs/HEALTH_REPLY_UNICAST.md, 2026-09-22)."""
from workflows import pi_reporter_push as prp


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
        if cmd.startswith("grep -c"):
            return (0, self._marker, "")
        if cmd.startswith("systemctl is-active"):
            return (0, self._active, "")
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


def test_the_action_is_reachable_from_a_pi_nodes_page():
    src = open("ui/screens/node_detail_screen.py").read()
    assert "Update health reporter" in src and 'node_type == "pi"' in src
    assert "_on_push_reporter(" in src
    app = open("ui/app.py").read()
    assert "on_push_reporter=self._push_reporter" in app
    i = app.index("def _push_reporter(self, record, report):")
    body = app[i:i + 2000]
    assert "push_health_reporter(conn" in body and "SSHConnection(host" in body


def test_the_fake_matches_the_real_connection_contract():
    """transport.connection.Result is (code, stdout, stderr); the step must
    index it, never attribute it — and the fake is held to the same shape."""
    r = FakeConn().run("echo $HOME")
    assert isinstance(r, tuple) and len(r) == 3
    src = open("workflows/pi_reporter_push.py").read()
    assert not any(".stdout" in l for l in src.splitlines() if "conn.run" in l)
