"""Offline wheelhouse — caching the tool's Python deps for a field clone.

The medic downloads wheels for its own (== the clone target's) platform, then a
--no-index install must resolve the whole stack. These tests pin the commands and
the success/failure reporting without touching the network.
"""

import pytest

from transport.connection import EmulatedConnection
from workflows.wheelhouse import (
    cache_wheels, download_command, verify_command, wheel_count,
    REQUIREMENTS, WHEELHOUSE,
)


def test_download_command_pulls_the_manifest_into_the_wheelhouse():
    cmd = download_command()
    assert cmd == f"pip3 download -r {REQUIREMENTS} -d {WHEELHOUSE}"


def test_verify_command_does_a_no_index_install_in_a_throwaway_venv():
    cmd = verify_command()
    assert "python3 -m venv" in cmd
    assert "--no-index" in cmd and f"--find-links {WHEELHOUSE}" in cmd
    assert f"-r {REQUIREMENTS}" in cmd


def test_wheel_count_parses_ls_wc():
    c = EmulatedConnection(default_code=0, default_stdout="ok")
    c.rule("wc -l", 0, "17")
    assert wheel_count(c) == 17


def _conn(wheels="17", download=0, verify=0):
    c = EmulatedConnection(default_code=0, default_stdout="ok")
    c.rule("pip3 download", download, "Saved ...")
    c.rule("wc -l", 0, wheels)
    c.rule("python3 -m venv", verify, "Successfully installed" if verify == 0
           else "ERROR")
    return c


def test_cache_wheels_downloads_then_verifies_offline_install():
    c = _conn()
    ok, msg = cache_wheels(c)
    assert ok
    assert "17 wheels" in msg and "offline install" in msg
    assert any("pip3 download" in h for h in c.history)
    assert any("--no-index" in h for h in c.history)   # the verify step ran


def test_cache_wheels_can_skip_verification():
    c = _conn()
    ok, msg = cache_wheels(c, verify=False)
    assert ok and "17 wheels" in msg
    assert not any("--no-index" in h for h in c.history)


def test_cache_wheels_fails_when_download_fails():
    c = _conn(download=1)
    ok, msg = cache_wheels(c)
    assert ok is False and "download failed" in msg


def test_cache_wheels_fails_when_no_wheels_land():
    c = _conn(wheels="0")
    ok, msg = cache_wheels(c)
    assert ok is False and "no wheels" in msg


def test_cache_wheels_fails_when_offline_install_check_fails():
    c = _conn(verify=1)
    ok, msg = cache_wheels(c)
    assert ok is False and "offline install check failed" in msg
