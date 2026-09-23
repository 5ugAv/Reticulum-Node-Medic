"""The UI's busy marker and the guard that respects it (2026-09-22: a Pi
birth over SSH was killed by a shell-side UI stop that had no way to know)."""
import os
import subprocess
import time

from monitor import busy_marker as bm


def test_marker_is_fresh_then_stale(tmp_path):
    p = str(tmp_path / "ui_busy")
    clock = [1000.0]
    bm.write("birthing ROOFRAK", path=p, now=lambda: clock[0])
    assert bm.busy_reason(path=p, now=lambda: clock[0]) == "birthing ROOFRAK"
    clock[0] = 1000.0 + 60
    assert bm.busy_reason(path=p, now=lambda: clock[0]) == "birthing ROOFRAK"
    clock[0] = 1000.0 + 91                     # a crashed UI stops touching it
    assert bm.busy_reason(path=p, now=lambda: clock[0]) is None
    bm.clear(path=p)
    assert not os.path.exists(p) and bm.busy_reason(path=p) is None
    bm.clear(path=p)                           # idempotent


def test_the_shell_guard_refuses_on_a_fresh_marker_and_not_on_a_stale_one(tmp_path):
    p = str(tmp_path / "ui_busy")
    env = dict(os.environ, UI_BUSY_MARKER=p)
    bm.write("a boundary walk", path=p)
    r = subprocess.run(["bash", "scripts/ui_busy_guard.sh"], env=env,
                       capture_output=True, text=True)
    assert r.returncode == 3 and "a boundary walk" in r.stderr
    old = time.time() - 600
    os.utime(p, (old, old))
    r = subprocess.run(["bash", "scripts/ui_busy_guard.sh"], env=env,
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    r = subprocess.run(["bash", "scripts/ui_busy_guard.sh"],
                       env=dict(env, FORCE="1"), capture_output=True, text=True)
    assert r.returncode == 0


def test_restart_script_uses_the_guard_and_offers_stop_only():
    s = open("scripts/restart_ui.sh").read()
    assert "ui_busy_guard.sh" in s and "STOP_ONLY" in s


def test_the_app_drives_the_marker_from_one_busy_predicate():
    src = open("ui/app.py").read()
    assert "def _busy_reason(self):" in src and "def _busy_heartbeat(self" in src
    i = src.index("def _show_screensaver(self):")
    assert "self._busy_reason()" in src[i:i + 800]
    assert "schedule_interval(self._busy_heartbeat, 30.0)" in src
    assert src.count("self._busy_heartbeat()") >= 3       # start, begin, end
    j = src.index("def on_stop(self):")
    assert "busy_marker.clear()" in src[j:j + 900]


def test_the_shell_scripts_parse():
    """A substring pin let a restart script with a stray `fi` ship and the
    medic's UI could not be restarted (2026-09-22). bash -n is the test."""
    for script in ("scripts/restart_ui.sh", "scripts/ui_busy_guard.sh"):
        r = subprocess.run(["bash", "-n", script], capture_output=True, text=True)
        assert r.returncode == 0, f"{script}: {r.stderr}"


def test_start_ui_logs_to_the_same_file_whoever_starts_it():
    """The desktop's autostart and restart_ui.sh must leave the UI's output in
    ONE file (~/ui.log). At boot it went to ~/.xsession-errors instead, and a
    doubled autostart ran two UIs for a whole boot with nothing in the file
    every diagnosis reads (2026-09-23)."""
    import os, subprocess
    from tests.srcutil import ROOT
    path = os.path.join(ROOT, "scripts/start_ui.sh")
    src = open(path, encoding="utf-8").read()
    execs = [l for l in src.splitlines() if l.strip().startswith("exec ")]
    assert len(execs) == 1 and 'main.py >> "$HOME/ui.log" 2>&1' in execs[0], execs
    assert subprocess.run(["bash", "-n", path]).returncode == 0
