"""Birth and the reporter push carry the time trust (docs/HEALTH_REPLY_UNICAST.md,
"Time over the mesh", 2026-09-23): the node's trust file naming THIS medic's
health-reply identity, the root helper that is the only way the reporter
may move the clock, and the sudoers line that lets it — each written, then
READ BACK; the sudoers line held to visudo; the step failing rather than
guessing when the medic cannot compute its own anchor."""
import base64
import json
import os
import re
import shutil
import stat
import subprocess

import pytest

import monitor.node_time as nt
from node_profile import NodeProfile, NodeRole
from tests.test_build_workflow import build_conn, wf, _run_step
from workflows import build
from workflows import pi_reporter_push as prp

ANCHOR = {"identity_hash": "ab" * 16, "reply_dest": "cd" * 16,
          "name": "nodemedic", "since": "2026-09-23"}
SUDOERS = "pi ALL=(root) NOPASSWD: /usr/local/sbin/nm-settime\n"


def _decoded(cmd):
    m = re.search(r"echo '?([A-Za-z0-9+/=]+)'? \| base64 -d", cmd)
    assert m, f"no base64 payload in: {cmd}"
    return base64.b64decode(m.group(1)).decode()


def _prop_conn(read_back=True, visudo_rc=0):
    conn = build_conn(rnode=True)
    conn.rules.insert(0, ("id -un", 0, "pi", ""))
    conn.rules.insert(0, ("echo $HOME", 0, "/home/pi", ""))
    conn.rules.insert(0, ("RNS.Destination.IN", 0, "11" * 16, ""))
    conn.rules.insert(0, ("visudo -c -f", visudo_rc, "" if visudo_rc == 0 else "syntax error",
                          ""))
    if read_back:
        conn.rules.insert(0, ("cat /home/pi/.rnm-health/trusted_medic.json", 0,
                              nt.trust_anchor_json(ANCHOR), ""))
        conn.rules.insert(0, ("cat /usr/local/sbin/nm-settime", 0, build.NM_SETTIME_SCRIPT, ""))
        conn.rules.insert(0, ("cat /etc/sudoers.d/nm-settime", 0, SUDOERS, ""))
    return conn


@pytest.fixture
def anchor(monkeypatch):
    monkeypatch.setattr(build, "medic_time_anchor", lambda: dict(ANCHOR))


def _w(conn):
    return wf(conn, NodeProfile(role=NodeRole.PROPAGATION, has_solar_controller=True))


# -- the step ------------------------------------------------------------------

def test_the_step_follows_the_reporter_and_is_a_planned_row():
    names = [n for n, _ in build._BUILD_STEPS]
    assert names.index("install_time_trust") == names.index("install_health_reporter") + 1
    from tests.test_build_workflow import EXPECTED_STEPS
    assert "install_time_trust" in EXPECTED_STEPS
    # the birth screen's per-step weight and busy label (a Kivy module: read
    # as source, the way the rest of the suite does)
    import ast
    from tests.srcutil import src
    tree = ast.parse(src("ui/screens/birth_screen.py"))
    keyed = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Dict) \
                and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            keyed[node.targets[0].id] = {k.value for k in node.value.keys
                                         if isinstance(k, ast.Constant)}
    assert "install_time_trust" in keyed["_STEP_SECONDS"]
    assert "install_time_trust" in keyed["_PHASE_LABELS"]


def test_skipped_for_a_node_that_runs_no_reporter(anchor):
    r = _run_step(wf(build_conn(rnode=True)), "install_time_trust")
    assert r.success and r.skipped


def test_the_healthy_path_writes_three_files_checks_sudoers_and_reads_all_back(anchor):
    w = _w(_prop_conn())
    r = _run_step(w, "install_time_trust")
    assert r.success and not r.skipped, r.message
    hist = w.connection.history
    trust = [c for c in hist if "trusted_medic.json" in c and "base64 -d" in c]
    assert trust and json.loads(_decoded(trust[0])) == ANCHOR
    assert "sudo" not in trust[0].split("|")[-1], "the trust file is the reporter user's own"
    helper = [c for c in hist if "/usr/local/sbin/nm-settime" in c and "base64 -d" in c]
    assert helper and _decoded(helper[0]) == build.NM_SETTIME_SCRIPT
    assert "sudo -n tee" in helper[0]
    sud = [c for c in hist if "/etc/sudoers.d/nm-settime" in c and "base64 -d" in c]
    assert sud and _decoded(sud[0]) == SUDOERS
    assert any("chmod 0755 /usr/local/sbin/nm-settime" in c for c in hist)
    assert any("chown root:root /usr/local/sbin/nm-settime" in c for c in hist)
    assert any("chmod 0440 /etc/sudoers.d/nm-settime" in c for c in hist)
    assert any("chown root:root /etc/sudoers.d/nm-settime" in c for c in hist)
    assert any(c.startswith("sudo -n visudo -c -f /etc/sudoers.d/nm-settime") for c in hist)
    # read back, all three
    assert any(c.startswith("cat /home/pi/.rnm-health/trusted_medic.json") for c in hist)
    assert any(c.startswith("cat /usr/local/sbin/nm-settime") for c in hist)
    assert any("cat /etc/sudoers.d/nm-settime" in c for c in hist)
    assert "read back" in r.message and ANCHOR["reply_dest"][:8] in r.message
    # the sudoers file is written to a staging path first and moved in only
    # after visudo accepts it: a rejected file must never sit in sudoers.d
    assert hist.index(next(c for c in hist if "visudo -c -f" in c)) < \
        hist.index(next(c for c in hist if "mv " in c and "/etc/sudoers.d/nm-settime" in c))


def test_visudo_rejecting_the_line_fails_the_step_and_removes_the_file(anchor):
    w = _w(_prop_conn(visudo_rc=1))
    r = _run_step(w, "install_time_trust")
    assert not r.success and "visudo" in r.message
    hist = w.connection.history
    assert not any("mv " in c and "/etc/sudoers.d/nm-settime" in c for c in hist)
    assert any("rm -f" in c and "nm-settime" in c for c in hist)


def test_a_read_back_that_disagrees_is_a_failure_not_a_claim(anchor):
    w = _w(_prop_conn(read_back=False))          # every cat answers "ok"
    r = _run_step(w, "install_time_trust")
    assert not r.success and "read back" in r.message


def test_no_anchor_on_the_medic_fails_and_writes_nothing(monkeypatch):
    def boom():
        raise RuntimeError("RNS is not importable on this medic")
    monkeypatch.setattr(build, "medic_time_anchor", boom)
    w = _w(_prop_conn())
    r = _run_step(w, "install_time_trust")
    assert not r.success and "RNS" in r.message
    assert not any("trusted_medic.json" in c for c in w.connection.history)


def test_the_anchor_is_computed_from_the_medics_own_reply_identity(tmp_path, monkeypatch):
    RNS = pytest.importorskip("RNS")
    import monitor.health_reply as hr
    p = str(tmp_path / "health_reply_identity")
    monkeypatch.setattr(build, "_REPLY_IDENTITY_PATH", p)
    a = build.medic_time_anchor(name="medic-x")
    ident = hr.load_or_create_identity(RNS, p)
    assert a["identity_hash"] == ident.hash.hex()
    assert a["reply_dest"] == RNS.Destination.hash(ident, hr.REPLY_APP, *hr.REPLY_ASPECTS).hex()
    assert a["name"] == "medic-x" and re.match(r"^\d{4}-\d{2}-\d{2}$", a["since"])
    assert build.medic_time_anchor(name="medic-x")["identity_hash"] == a["identity_hash"]


def test_the_anchor_never_guesses_without_rns(monkeypatch):
    import builtins
    real = builtins.__import__

    def no_rns(name, *a, **kw):
        if name == "RNS":
            raise ImportError("no RNS here")
        return real(name, *a, **kw)
    monkeypatch.setattr(builtins, "__import__", no_rns)
    with pytest.raises(RuntimeError):
        build.medic_time_anchor(name="x")


def test_node_time_rides_with_the_reporter_package():
    assert "node_time.py" in build._HEALTH_MODULES


# -- the helper script ----------------------------------------------------------

def test_the_helper_is_bash_clean_and_does_only_one_thing(tmp_path):
    bash = shutil.which("bash")
    if not bash:
        pytest.skip("no bash on this box")
    script = tmp_path / "nm-settime"
    script.write_text(build.NM_SETTIME_SCRIPT)
    assert subprocess.run([bash, "-n", str(script)], capture_output=True).returncode == 0
    assert build.NM_SETTIME_SCRIPT.startswith("#!/bin/bash\n")
    assert "date -s" in build.NM_SETTIME_SCRIPT and "date -u" in build.NM_SETTIME_SCRIPT
    assert build.NM_SETTIME_SCRIPT.count("date ") == 2, "date -s then date -u: nothing else"
    assert str(nt.EPOCH_MIN) in build.NM_SETTIME_SCRIPT
    assert str(nt.EPOCH_MAX) in build.NM_SETTIME_SCRIPT


def test_the_helper_validates_its_argument_before_touching_date(tmp_path):
    """Run the real script with a fake `date` on PATH: a bad argument must
    exit non-zero without calling date; a good one calls date -s @epoch."""
    bash = shutil.which("bash")
    if not bash:
        pytest.skip("no bash on this box")
    fake = tmp_path / "bin"
    fake.mkdir()
    log = tmp_path / "date.log"
    (fake / "date").write_text("#!/bin/sh\necho \"$@\" >> %s\necho FAKE-DATE-OUT\n" % log)
    os.chmod(fake / "date", 0o755)
    script = tmp_path / "nm-settime"
    script.write_text(build.NM_SETTIME_SCRIPT)
    os.chmod(script, 0o755)
    env = dict(os.environ, PATH=f"{fake}:{os.environ.get('PATH', '')}")

    def run(*args):
        return subprocess.run([bash, str(script), *args], capture_output=True,
                              text=True, env=env)
    for bad in ((), ("abc",), ("123",), ("1767225599",), (str(nt.EPOCH_MAX),),
                ("17672256001",), ("1767225600; id",), ("1767225600", "extra")):
        r = run(*bad)
        assert r.returncode != 0, f"accepted {bad!r}"
    assert not log.exists(), "date was called on a rejected argument"
    r = run("1790000000")
    assert r.returncode == 0 and "FAKE-DATE-OUT" in r.stdout
    assert log.read_text().splitlines() == ["-s @1790000000", "-u"]


def test_the_sudoers_line_passes_visudo_here_too(tmp_path):
    visudo = shutil.which("visudo")
    if not visudo:
        pytest.skip("no visudo on this box")
    f = tmp_path / "nm-settime"
    f.write_text(build.nm_settime_sudoers("pi"))
    assert subprocess.run([visudo, "-c", "-f", str(f)], capture_output=True).returncode == 0
    assert build.nm_settime_sudoers("pi") == SUDOERS
    with pytest.raises(ValueError):
        build.nm_settime_sudoers("pi ALL")            # a user name, nothing else


# -- the push road for nodes already in the field ---------------------------------

class FakeConn:
    """transport.connection's real shape: (code, stdout, stderr)."""
    def __init__(self, who="pi", visudo_rc=0, read_back=True, trust_ok=True):
        self.cmds, self.pushed = [], []
        self._who, self._visudo_rc, self._read_back, self._trust_ok = who, visudo_rc, read_back, trust_ok

    def run(self, cmd, timeout=30):
        self.cmds.append(cmd)
        if cmd == "id -un":
            return (0, self._who, "")
        if cmd == "echo $HOME":
            return (0, "/home/pi", "")
        if cmd.startswith("grep -c"):
            return (0, "1", "")
        if cmd.startswith("systemctl is-active"):
            return (0, "active", "")
        if "visudo -c -f" in cmd:
            return (self._visudo_rc, "", "syntax error" if self._visudo_rc else "")
        if self._read_back:
            if cmd.startswith("cat /home/pi/.rnm-health/trusted_medic.json"):
                return (0, nt.trust_anchor_json(ANCHOR) if self._trust_ok else "{}", "")
            if cmd.startswith("cat /usr/local/sbin/nm-settime"):
                return (0, build.NM_SETTIME_SCRIPT, "")
            if "cat /etc/sudoers.d/nm-settime" in cmd:
                return (0, SUDOERS, "")
        return (0, "", "")

    def push_file(self, local, remote):
        self.pushed.append(remote)
        return True


def test_push_ships_the_time_module_and_the_trust_and_reads_back(anchor):
    c = FakeConn()
    ok, msg = prp.push_health_reporter(c)
    assert ok, msg
    assert any(r.endswith("/node_time.py") for r in c.pushed)
    assert any("trusted_medic.json" in cmd and "base64 -d" in cmd for cmd in c.cmds)
    assert any("visudo -c -f" in cmd for cmd in c.cmds)
    assert any(cmd.startswith("cat /home/pi/.rnm-health/trusted_medic.json") for cmd in c.cmds)
    assert any(cmd == "sudo -n systemctl restart rnm-health" for cmd in c.cmds)
    assert "time" in msg


def test_push_fails_before_restart_when_the_trust_does_not_land(anchor):
    c = FakeConn(trust_ok=False)
    ok, msg = prp.push_health_reporter(c)
    assert not ok and "read back" in msg
    assert not any("restart" in cmd for cmd in c.cmds)
    c = FakeConn(visudo_rc=1)
    ok, msg = prp.push_health_reporter(c)
    assert not ok and "visudo" in msg


def test_push_fails_without_an_anchor(monkeypatch):
    def boom():
        raise RuntimeError("RNS is not importable on this medic")
    monkeypatch.setattr(build, "medic_time_anchor", boom)
    c = FakeConn()
    ok, msg = prp.push_health_reporter(c)
    assert not ok and "RNS" in msg
    assert not any("restart" in cmd for cmd in c.cmds)
