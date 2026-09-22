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
