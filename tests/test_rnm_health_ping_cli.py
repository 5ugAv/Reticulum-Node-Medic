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
