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
    run = fake_run({
        "serial/by-id": f"usb-Espressif_..._{ONBOARD_SERIAL}-if00",
        "is-active rnode-splitter": "active",
        "MainPID": "1676",
        "cputimes": "5 1560",                       # 5s CPU in 1560s = healthy
        "journalctl": "Started rnode-splitter",
        "gps_state.json": '{"updated": 1000}',
    })
    findings = rt.gather(run=run, now_fn=lambda: 1030)
    assert [f.severity for f in findings] == [SEV_OK, SEV_OK, SEV_OK]


def test_gather_catches_the_jonesey_incident():
    run = fake_run({
        "serial/by-id": "usb-Espressif_..._3C:0F:02:EB:2E:18-if00",  # still on USB
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
