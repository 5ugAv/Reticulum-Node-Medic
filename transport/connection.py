"""Transport layer for talking to nodes.

A ``Connection`` abstracts *how* a command is executed on a node — over SSH,
over a USB/direct serial link, or against an in-memory emulator used by the
test suite. Every concrete connection returns the same ``(exit_code, stdout,
stderr)`` tuple so diagnostics and workflows are transport-agnostic.
"""

from __future__ import annotations

import base64
import subprocess
import time
from abc import ABC, abstractmethod
from typing import Callable, List, Optional, Tuple

Result = Tuple[int, str, str]


class Connection(ABC):
    """Base class: run a command, get ``(code, stdout, stderr)``."""

    @abstractmethod
    def run(self, command: str, timeout: int = 30) -> Result:
        """Execute *command* and return ``(exit_code, stdout, stderr)``.

        Implementations must never raise for an ordinary command failure —
        a non-zero exit code is reported through the tuple, not an exception.
        """

    def run_checked(self, command: str, timeout: int = 30) -> str:
        """Run *command*, raising ``RuntimeError`` on a non-zero exit."""
        code, out, err = self.run(command, timeout)
        if code != 0:
            raise RuntimeError(
                f"Command failed (exit {code}): {command}\n{err or out}"
            )
        return out

    def push_file(self, local_path: str, remote_path: str) -> bool:
        """Copy a local file to the node. Returns ``True`` on success."""
        raise NotImplementedError

    def is_connected(self) -> bool:
        return True

    def close(self) -> None:  # pragma: no cover - trivial
        pass


# ---------------------------------------------------------------------------
# SSH
# ---------------------------------------------------------------------------


def _default_ssh_runner(argv: List[str], timeout: int) -> Result:
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return (proc.returncode, proc.stdout, proc.stderr)
    except subprocess.TimeoutExpired:
        return (255, "", "ssh: timed out")
    except FileNotFoundError:
        return (255, "", "ssh: command not found")


class SSHConnection(Connection):
    """Run commands on a node over SSH, retrying transient failures.

    ``ssh`` reports connection-level failures (host unreachable, refused,
    timed out) with exit code 255; those are treated as transient and
    retried. An ordinary non-zero exit from the *remote* command is a real
    result and is returned immediately.
    """

    TRANSIENT_EXIT = 255

    def __init__(
        self,
        host: str,
        user: str = "pi",
        port: int = 22,
        retry_count: int = 3,
        retry_delay: float = 5.0,
        runner: Optional[Callable[[List[str], int], Result]] = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.host = host
        self.user = user
        self.port = port
        self.retry_count = retry_count
        self.retry_delay = retry_delay
        self._runner = runner or _default_ssh_runner
        self._sleep = sleep

    def _argv(self, command: str) -> List[str]:
        return [
            "ssh",
            "-o", "BatchMode=yes",
            "-o", "ConnectTimeout=10",
            "-o", "StrictHostKeyChecking=accept-new",
            "-p", str(self.port),
            f"{self.user}@{self.host}",
            command,
        ]

    def run(self, command: str, timeout: int = 30) -> Result:
        argv = self._argv(command)
        last: Result = (self.TRANSIENT_EXIT, "", "not attempted")
        for attempt in range(1, self.retry_count + 1):
            code, out, err = self._runner(argv, timeout)
            last = (code, out, err)
            if code != self.TRANSIENT_EXIT:
                return last
            if attempt < self.retry_count:
                self._sleep(self.retry_delay)
        return last

    def push_file(self, local_path: str, remote_path: str) -> bool:
        argv = [
            "scp",
            "-P", str(self.port),
            "-o", "BatchMode=yes",
            "-o", "StrictHostKeyChecking=accept-new",
            local_path,
            f"{self.user}@{self.host}:{remote_path}",
        ]
        code, _, _ = self._runner(argv, 120)
        return code == 0


# ---------------------------------------------------------------------------
# Serial
# ---------------------------------------------------------------------------


class SerialConnection(Connection):
    """Run commands over a serial console using a sentinel marker.

    The remote shell has no clean framing, so each command is wrapped to echo
    a unique sentinel plus the exit code once it completes. Everything before
    the sentinel is stdout; the token after it is the exit code. If the
    sentinel never arrives (garbled line, wrong baud, dead console) we return
    ``(-1, raw, "Sentinel not found ...")`` rather than hanging or crashing.
    """

    CMD_DONE = "CMD_DONE_7f3a"

    def __init__(self, port: str, baud: int = 115200, transport=None):
        self.port = port
        self.baud = baud
        self._transport = transport

    def _wrap(self, command: str) -> str:
        return f"{command}; echo {self.CMD_DONE} $?\n"

    def run(self, command: str, timeout: int = 30) -> Result:
        if self._transport is None:
            return (-1, "", "No serial transport open")
        self._transport.write(self._wrap(command))
        raw = self._transport.read_all(timeout)

        # Use the LAST occurrence: a console that echoes the command replays the
        # wrapped command line (which contains the sentinel string), so the
        # real completion marker is always the final one.
        idx = raw.rfind(self.CMD_DONE)
        if idx == -1:
            return (-1, raw, "Sentinel not found in serial response")

        before = raw[:idx]
        after = raw[idx + len(self.CMD_DONE):].strip()
        exit_token = after.split()[0] if after.split() else ""
        try:
            code = int(exit_token)
        except ValueError:
            code = -1

        # Strip an echoed copy of the wrapped command if the console echoed it.
        stdout = before.replace(self._wrap(command), "").strip("\r\n")
        return (code, stdout, "")

    def push_file(self, local_path: str, remote_path: str) -> bool:
        """Push a local file to the node over the serial command channel.

        Rather than lrzsz (which needs a raw ZMODEM byte stream this
        sentinel-framed transport can't provide, and whose ``rz``/``sz`` roles
        are easy to get backwards), the file is base64-encoded and written
        through a heredoc — decoded on the node with ``base64 -d``. Correct for
        the small assets the tool carries; slow for large files.
        """
        try:
            with open(local_path, "rb") as fh:
                data = fh.read()
        except OSError:
            return False
        b64 = base64.b64encode(data).decode("ascii")
        code, _, _ = self.run(
            f"base64 -d > {remote_path} <<'RNMEOF'\n{b64}\nRNMEOF")
        return code == 0


# ---------------------------------------------------------------------------
# Emulator (test harness)
# ---------------------------------------------------------------------------


class EmulatedConnection(Connection):
    """In-memory connection driven by an ordered rule list.

    Rules are checked in insertion order and the first match wins. A rule
    whose pattern starts with ``^`` matches the *start* of the command;
    otherwise it matches anywhere. Register prefix rules before broader
    substring rules that could also match.
    """

    def __init__(
        self,
        default_code: int = 127,
        default_stdout: str = "",
        default_stderr: str = "no emulator rule matched",
    ):
        self.rules: List[Tuple[str, int, str, str]] = []
        self.default_code = default_code
        self.default_stdout = default_stdout
        self.default_stderr = default_stderr
        self.history: List[str] = []
        self.pushed: List[Tuple[str, str]] = []

    def rule(
        self,
        pattern: str,
        code: int = 0,
        stdout: str = "ok",
        stderr: str = "",
    ) -> "EmulatedConnection":
        self.rules.append((pattern, code, stdout, stderr))
        return self

    def _matches(self, pattern: str, command: str) -> bool:
        if pattern.startswith("^"):
            return command.startswith(pattern[1:])
        return pattern in command

    def run(self, command: str, timeout: int = 30) -> Result:
        self.history.append(command)
        for pattern, code, stdout, stderr in self.rules:
            if self._matches(pattern, command):
                return (code, stdout, stderr)
        return (self.default_code, self.default_stdout, self.default_stderr)

    def push_file(self, local_path: str, remote_path: str) -> bool:
        self.pushed.append((local_path, remote_path))
        return True


# ---------------------------------------------------------------------------
# Auto-detect
# ---------------------------------------------------------------------------


def auto_detect_connection(target: str, **kwargs) -> Connection:
    """Pick a connection type from *target*.

    Paths under ``/dev/`` (Linux ``/dev/ttyUSB0``, macOS
    ``/dev/cu.usbmodem*``) are serial; anything else is treated as an SSH
    host or IP address.
    """
    if target.startswith("/dev/"):
        serial_kwargs = {
            k: v for k, v in kwargs.items() if k in ("baud", "transport")
        }
        return SerialConnection(target, **serial_kwargs)
    # Filter to SSHConnection's accepted params so a stray serial-only kwarg
    # (e.g. baud=/transport=) doesn't raise TypeError.
    ssh_keys = ("user", "port", "retry_count", "retry_delay", "runner", "sleep")
    ssh_kwargs = {k: v for k, v in kwargs.items() if k in ssh_keys}
    return SSHConnection(target, **ssh_kwargs)
