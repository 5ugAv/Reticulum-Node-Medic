"""The shell health ping is a real program: it imports, parses, and refuses
a bad destination before touching RNS."""
import runpy
import sys


def test_cli_refuses_a_malformed_destination(capsys):
    sys.argv = ["rnm_health_ping.py", "nothex"]
    try:
        runpy.run_path("scripts/rnm_health_ping.py", run_name="__main__")
    except SystemExit as e:
        assert e.code == 2
    assert "32 hex" in capsys.readouterr().err


def test_the_tool_has_one_persistent_identity_that_the_medic_treats_as_its_own():
    """Four throwaway identities became four ghost neighbours on VITALS
    (2026-09-22). One file, on the own-identity list."""
    src = open("scripts/rnm_health_ping.py").read()
    assert "load_or_create_identity(RNS, PING_TOOL_IDENTITY_PATH)" in src
    assert "RNS.Identity()" not in src
    from provisioning import tool_identity
    assert any(p.endswith("health_ping_identity") for p in tool_identity._OWN_IDENTITY_FILES)


def test_uptime_falling_since_the_last_ping_means_a_reboot():
    """No battery sensor exists on a Pi node (2026-09-24): the one honest
    signal a repeated ping can give about power is uptime. If it drops
    between two pings the node restarted — lost power and came back — even
    though it answered both times and 'looks fine'."""
    from scripts.rnm_health_ping import rebooted_since
    assert rebooted_since(prev_uptime_s=50000, now_uptime_s=120) is True
    assert rebooted_since(prev_uptime_s=50000, now_uptime_s=50600) is False
    assert rebooted_since(prev_uptime_s=None, now_uptime_s=120) is False
    # a few seconds of clock/measurement jitter is not a reboot
    assert rebooted_since(prev_uptime_s=1000, now_uptime_s=997) is False


def test_the_reply_beacon_is_decoded_for_uptime_not_just_counted_in_bytes():
    src = open("scripts/rnm_health_ping.py").read()
    assert "health_beacon.decode(" in src or "from monitor.health_beacon import decode" in src
    assert "uptime" in src.lower()


def test_last_uptime_persists_across_pings_and_tolerates_a_missing_or_bad_file(tmp_path):
    from scripts.rnm_health_ping import load_last_uptime, save_last_uptime
    path = str(tmp_path / "state.json")
    assert load_last_uptime("aa" * 16, path=path) is None    # never pinged
    save_last_uptime("aa" * 16, 500, path=path)
    assert load_last_uptime("aa" * 16, path=path) == 500
    save_last_uptime("bb" * 16, 10, path=path)                # a second node, same file
    assert load_last_uptime("aa" * 16, path=path) == 500
    assert load_last_uptime("bb" * 16, path=path) == 10
    open(path, "w").write("not json")
    assert load_last_uptime("aa" * 16, path=path) is None      # corrupt file: no raise


def test_note_uptime_is_wired_into_the_unicast_answer():
    src = open("scripts/rnm_health_ping.py").read()
    assert "_note_uptime(args.dest, beacon)" in src
    body = src[src.index("def _note_uptime"):src.index("def main(")]
    assert "rebooted_since(prev, uptime_s)" in body
    assert "save_last_uptime(node_dest_hex, uptime_s)" in body
