import pytest

from transport.connection import (
    Connection,
    SSHConnection,
    SerialConnection,
    auto_detect_connection,
)


# ---- auto_detect_connection ---------------------------------------------


def test_auto_detect_dev_path_is_serial():
    conn = auto_detect_connection("/dev/ttyUSB0")
    assert isinstance(conn, SerialConnection)


def test_auto_detect_macos_dev_path_is_serial():
    conn = auto_detect_connection("/dev/cu.usbmodem1234")
    assert isinstance(conn, SerialConnection)


def test_auto_detect_hostname_is_ssh():
    conn = auto_detect_connection("node1.local")
    assert isinstance(conn, SSHConnection)


def test_auto_detect_ip_is_ssh():
    conn = auto_detect_connection("192.168.1.50")
    assert isinstance(conn, SSHConnection)


def test_auto_detect_ssh_ignores_serial_only_kwargs():
    # a stray baud/transport must not blow up the SSH branch
    conn = auto_detect_connection("node.local", baud=115200, transport=object())
    assert isinstance(conn, SSHConnection)


def test_auto_detect_ssh_still_honours_ssh_kwargs():
    conn = auto_detect_connection("node.local", user="root", port=2222)
    assert conn.user == "root"
    assert conn.port == 2222


# ---- SSHConnection retry behaviour --------------------------------------


def test_ssh_defaults():
    conn = SSHConnection("host")
    assert conn.retry_count == 3
    assert conn.retry_delay == 5.0
    assert conn.user == "pi"


def test_ssh_retries_transient_failure_then_succeeds():
    calls = []

    def runner(argv, timeout):
        calls.append(argv)
        # ssh exit 255 == transient connection failure
        if len(calls) < 3:
            return (255, "", "connection refused")
        return (0, "hello", "")

    slept = []
    conn = SSHConnection(
        "host", runner=runner, sleep=lambda s: slept.append(s), retry_count=3
    )
    code, out, err = conn.run("echo hello")
    assert code == 0
    assert out == "hello"
    assert len(calls) == 3
    # slept between the two failed attempts
    assert slept == [5.0, 5.0]


def test_ssh_gives_up_after_retry_count():
    calls = []

    def runner(argv, timeout):
        calls.append(argv)
        return (255, "", "connection refused")

    conn = SSHConnection(
        "host", runner=runner, sleep=lambda s: None, retry_count=3
    )
    code, out, err = conn.run("echo hello")
    assert code == 255
    assert len(calls) == 3


def test_ssh_does_not_retry_normal_nonzero_exit():
    calls = []

    def runner(argv, timeout):
        calls.append(argv)
        return (1, "", "file not found")

    conn = SSHConnection("host", runner=runner, sleep=lambda s: None)
    code, out, err = conn.run("cat /missing")
    assert code == 1
    # a normal command failure is NOT transient — no retry
    assert len(calls) == 1


# ---- SerialConnection sentinel parsing ----------------------------------


class FakeSerialTransport:
    """Canned request/response transport for tests."""

    def __init__(self, response):
        self.response = response
        self.written = []

    def write(self, text):
        self.written.append(text)

    def read_all(self, timeout):
        return self.response


def test_serial_parses_sentinel_and_exit_code():
    resp = "line one\nline two\nCMD_DONE_7f3a 0\n"
    conn = SerialConnection("/dev/ttyUSB0", transport=FakeSerialTransport(resp))
    code, out, err = conn.run("do something")
    assert code == 0
    assert "line one" in out
    assert "line two" in out
    assert "CMD_DONE_7f3a" not in out


def test_serial_parses_nonzero_exit_code():
    resp = "oops\nCMD_DONE_7f3a 7\n"
    conn = SerialConnection("/dev/ttyUSB0", transport=FakeSerialTransport(resp))
    code, out, err = conn.run("do something")
    assert code == 7


def test_serial_missing_sentinel_does_not_crash():
    resp = "garbage output with no marker"
    conn = SerialConnection("/dev/ttyUSB0", transport=FakeSerialTransport(resp))
    code, out, err = conn.run("do something")
    assert code == -1
    assert out == resp
    assert "Sentinel not found" in err


def test_serial_push_file_missing_local_returns_false():
    conn = SerialConnection("/dev/ttyUSB0",
                            transport=FakeSerialTransport("CMD_DONE_7f3a 0\n"))
    assert conn.push_file("/nonexistent/path/xyz", "/tmp/remote") is False


def test_serial_push_file_base64_encodes_content(tmp_path):
    import base64 as _b64
    p = tmp_path / "asset.bin"
    p.write_bytes(b"hello node \x00\x01")

    class CapTransport:
        def __init__(self):
            self.written = []

        def write(self, text):
            self.written.append(text)

        def read_all(self, timeout):
            return "CMD_DONE_7f3a 0\n"

    cap = CapTransport()
    conn = SerialConnection("/dev/ttyUSB0", transport=cap)
    ok = conn.push_file(str(p), "/tmp/remote")
    assert ok is True
    written = "".join(cap.written)
    assert _b64.b64encode(b"hello node \x00\x01").decode() in written
    assert "base64 -d > /tmp/remote" in written


def test_serial_is_a_connection():
    conn = SerialConnection("/dev/ttyUSB0", transport=FakeSerialTransport(""))
    assert isinstance(conn, Connection)


class EchoSerialTransport:
    """Realistic console: echoes the wrapped command, then output, then marker.

    The echoed command line itself contains the sentinel string
    (``echo CMD_DONE_7f3a $?``), so a naive first-match parse would mis-read it.
    """

    def __init__(self):
        self._last = ""

    def write(self, text):
        self._last = text

    def read_all(self, timeout):
        return f"{self._last}file1 file2\nCMD_DONE_7f3a 0\n"


def test_serial_ignores_sentinel_in_echoed_command():
    conn = SerialConnection("/dev/ttyUSB0", transport=EchoSerialTransport())
    code, out, err = conn.run("ls")
    assert code == 0                      # not -1 from parsing "$?"
    assert "file1 file2" in out
    assert "CMD_DONE_7f3a" not in out
    assert "echo" not in out              # echoed command stripped


def test_serial_nonzero_exit_with_echo():
    class T:
        def write(self, t): self._c = t
        def read_all(self, to): return f"{self._c}oops\nCMD_DONE_7f3a 5\n"
    code, out, err = SerialConnection("/dev/ttyUSB0", transport=T()).run("badcmd")
    assert code == 5
    assert "oops" in out
