"""LocalConnection — the medic running commands on its OWN USB / filesystem.

The keystone that lets Node Medic flash a board attached to itself (previously
the UI only had an EmulatedConnection, so on-medic flashing was faked).
"""

from transport.connection import LocalConnection


def test_run_wraps_with_local_bin_path_and_bash():
    seen = {}

    def runner(argv, timeout, stdin=None):
        seen["argv"] = argv
        return (0, "out", "")

    c = LocalConnection(runner=runner)
    code, out, err = c.run("rnodeconf --info")
    assert code == 0 and out == "out"
    # runs via bash -c with ~/.local/bin prepended so rnodeconf resolves
    assert seen["argv"][0] == "bash" and seen["argv"][1] == "-c"
    assert "$HOME/.local/bin" in seen["argv"][2]
    assert "rnodeconf --info" in seen["argv"][2]


def test_login_env_false_runs_command_verbatim():
    seen = {}

    def runner(argv, timeout, stdin=None):
        seen["argv"] = argv
        return (0, "", "")

    LocalConnection(runner=runner, login_env=False).run("echo hi")
    assert seen["argv"][2] == "echo hi"          # no PATH export wrapper


def test_run_checked_raises_on_failure():
    c = LocalConnection(runner=lambda a, t, stdin=None: (3, "", "boom"))
    try:
        c.run_checked("rnodeconf --info")
        assert False, "should have raised"
    except RuntimeError as e:
        assert "exit 3" in str(e) and "boom" in str(e)


def test_run_never_raises_on_ordinary_failure():
    # a non-zero exit is a tuple, not an exception (transport contract)
    c = LocalConnection()
    code, out, err = c.run("false")
    assert code != 0


def test_real_subprocess_roundtrip():
    c = LocalConnection()
    code, out, err = c.run("echo hello-medic")
    assert code == 0 and out.strip() == "hello-medic"


def test_push_file_is_a_local_copy(tmp_path):
    src = tmp_path / "firmware.bin"
    src.write_bytes(b"RGBFW")
    dst = tmp_path / "staged" / "rnm_rgb_firmware.bin"
    ok = LocalConnection().push_file(str(src), str(dst))
    assert ok and dst.read_bytes() == b"RGBFW"     # dirs created, bytes copied


def test_push_file_reports_failure_for_missing_source(tmp_path):
    ok = LocalConnection().push_file(str(tmp_path / "nope.bin"),
                                     str(tmp_path / "out.bin"))
    assert ok is False
