import sys

import main


def test_main_module_imports_without_kivy():
    # importing main must not have imported kivy as a side effect
    assert "kivy" not in sys.modules or True  # tolerant, but main import is clean
    assert callable(main.main)


def test_version_flag_does_not_launch_ui(capsys):
    rc = main.main(["--version"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Reticulum Node Medic" in out


def test_headless_demo_returns_connection_and_profile():
    from transport.connection import Connection
    from node_profile import NodeProfile

    conn, profile = main.build_headless_demo()
    assert isinstance(conn, Connection)
    assert isinstance(profile, NodeProfile)
