"""Self Diagnose runtime — live gather + repairs (injected shell)."""

from monitor import self_diagnose_runtime as rt
from monitor.self_diagnose import SEV_OK, SEV_CRIT, SEV_WARN, ONBOARD_SERIAL


def fake_run(responses):
    """A run() that returns a canned string based on a substring of the command."""
    def run(cmd):
        for needle, out in responses.items():
            if needle in cmd:
                return out
        return ""
    return run


def test_gather_all_healthy():
    now = 1_704_070_000.0                            # a real 2024+ time (clock check)
    run = fake_run({
        "serial/by-id": f"usb-Espressif_..._{ONBOARD_SERIAL}-if00",
        "is-active rnode-splitter": "active",
        "MainPID": "1676",
        "cputimes": "5 1560",                       # 5s CPU in 1560s = healthy
        "journalctl": "Started rnode-splitter",
        "gps_state.json": f'{{"updated": {now - 5}}}',
        "df -P": "Cap\n/dev/root 100 40 50 40% /",  # 40% used
        "is-active rnsd": "active",
        "measure_temp": "temp=48.3'C",
        "get_throttled": "throttled=0x0",
        "dev wifi": "*:70:TestNet",
        "timedatectl": "NTPSynchronized=yes",
        "rnstatus": "Shared Instance[37428]\n  Status  : Up",
        "node_mode": "home",
        "is-active lxmd": "active",
        # the NetworkManager drop-in that keeps the cable-birth link alive.
        # Its CONTENT is what is checked — a `test -f` through safe_shell (no
        # shell, so "&&" becomes an argument) silently reported it missing.
        "99-nodemedic-usb0.conf": "[keyfile]\nunmanaged-devices=interface-name:usb0",
    })
    findings = rt.gather(run=run, now_fn=lambda: now)
    assert all(f.severity == SEV_OK for f in findings), \
        [f"{f.check}: {f.detail}" for f in findings if f.severity != SEV_OK]
    assert len(findings) == 12                       # 3 radio/gps + 9 system health


def test_gather_catches_the_jonesey_incident():
    run = fake_run({
        "serial/by-id": "usb-Espressif_..._A1:B2:C3:D4:E5:F6-if00",  # still on USB
        "is-active rnode-splitter": "active",
        "MainPID": "1676",
        "cputimes": "1320 1560",                    # spinning hot (~85%)
        "journalctl": "serial.serialutil.SerialException: readiness to read",
        "gps_state.json": '{"updated": 100}',       # very stale
    })
    findings = rt.gather(run=run, now_fn=lambda: 100 + 40000)
    sev = {f.check: f.severity for f in findings}
    assert sev["splitter"] == SEV_WARN and sev["gps"] == SEV_WARN
    assert any(f.fix == "restart_splitter" for f in findings)


def test_gather_usb_dropped():
    run = fake_run({"serial/by-id": "usb-somethingelse-if00",
                    "is-active rnode-splitter": "inactive",
                    "gps_state.json": ""})
    findings = rt.gather(run=run, now_fn=lambda: 0)
    assert findings[0].severity == SEV_CRIT and findings[0].fix == "usb_recover"


def test_run_repair_restart_splitter_success():
    ok, msg = rt.run_repair("restart_splitter", run=lambda c: "")
    assert ok is True


def test_run_repair_restart_splitter_needs_auth():
    ok, msg = rt.run_repair(
        "restart_splitter",
        run=lambda c: "Failed to restart: Interactive authentication required")
    assert ok is False


def test_repair_kind_and_guidance():
    assert rt.repair_kind("restart_splitter") == "auto"
    assert rt.repair_kind("reflash_provision") == "guided"
    assert rt.repair_kind("nope") == "unknown"
    ok, msg = rt.run_repair("reflash_provision", run=lambda c: "SHOULD NOT RUN")
    assert ok is False and "provision" in msg.lower()      # guidance, not executed


def test_the_drop_in_is_read_not_shell_tested():
    """safe_shell runs without a shell, so `test -f X && echo yes` passes "&&"
    through as an argument and always comes back empty — the check reported the
    file missing on a medic that had just installed it. Reading it also proves
    the CONTENT, so a truncated or hand-edited file is caught."""
    from tests.srcutil import func_source
    src = func_source("monitor/self_diagnose_runtime.py", "gather")
    # comments stripped: the explanation names the broken form it replaced
    code = "\n".join(l for l in src.splitlines() if not l.strip().startswith("#"))
    assert "test -f" not in code, "no shell operators reach safe_shell"
    assert 'unmanaged-devices" in run' in code


def test_rns_tools_are_called_by_absolute_path():
    """pip --user puts every RNS console script in ~/.local/bin, and safe_shell
    runs without a shell — a non-login subprocess gets
    PATH=/usr/local/bin:/usr/bin:/bin:/usr/games. Calling "rnstatus" by name
    could never have worked on this medic."""
    from tests.srcutil import func_source
    src = func_source("monitor/self_diagnose_runtime.py", "gather")
    assert '_tool(\'rnstatus\')' in src or '_tool("rnstatus")' in src
    helper = func_source("monitor/self_diagnose_runtime.py", "_tool")
    assert ".local/bin" in helper or "_USER_BIN" in helper
    assert "return name" in helper, "unresolved falls through honestly, not empty"
