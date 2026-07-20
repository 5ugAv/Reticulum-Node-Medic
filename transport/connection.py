"""Transport layer for talking to nodes.

A ``Connection`` abstracts *how* a command is executed on a node — over SSH,
over a USB/direct serial link, or against an in-memory emulator used by the
test suite. Every concrete connection returns the same ``(exit_code, stdout,
stderr)`` tuple so diagnostics and workflows are transport-agnostic.
"""

from __future__ import annotations

import base64
import shlex
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

    def push_tree(self, local_dir: str, remote_dir: str,
                  exclude: "tuple[str, ...]" = ()) -> bool:
        """Copy a whole local directory tree to the node (contents of
        *local_dir* into *remote_dir*). Returns ``True`` on success. Used by the
        MITOSIS (clone) to move the tool + asset store onto a fresh Pi."""
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

    #: Prepended to the PATH of every remote command. `pip install --user rns`
    #: puts the RNS console scripts (rnsd, rnstatus, rnodeconf, rnpath, …) in
    #: ~/.local/bin, which a non-interactive ssh shell does NOT include
    #: (verified: PATH=/usr/local/bin:/usr/bin:/bin:/usr/games). Without this,
    #: every bare RNS command resolves to "command not found".
    REMOTE_PATH = "$HOME/.local/bin:/usr/local/bin:$PATH"

    def __init__(
        self,
        host: str,
        user: str = "pi",
        port: int = 22,
        retry_count: int = 3,
        retry_delay: float = 5.0,
        runner: Optional[Callable[[List[str], int], Result]] = None,
        sleep: Callable[[float], None] = time.sleep,
        login_env: bool = True,
    ):
        self.host = host
        self.user = user
        self.port = port
        self.retry_count = retry_count
        self.retry_delay = retry_delay
        self._runner = runner or _default_ssh_runner
        self._sleep = sleep
        self.login_env = login_env

    def _wrap(self, command: str) -> str:
        """Run *command* under a shell that has ~/.local/bin on PATH.

        We deliberately export PATH ourselves rather than use a login shell
        (``bash -lc``): that keeps behaviour deterministic and avoids stdout
        noise from profile scripts polluting command output. ``shlex.quote``
        makes the whole payload a single safe token, so heredocs and embedded
        quotes in *command* pass through untouched.
        """
        if not self.login_env:
            return command
        payload = f'export PATH="{self.REMOTE_PATH}"; {command}'
        return f"bash -c {shlex.quote(payload)}"

    def _argv(self, command: str) -> List[str]:
        return [
            "ssh",
            "-o", "BatchMode=yes",
            "-o", "ConnectTimeout=10",
            "-o", "StrictHostKeyChecking=accept-new",
            "-p", str(self.port),
            f"{self.user}@{self.host}",
            self._wrap(command),
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

    def push_tree(self, local_dir: str, remote_dir: str,
                  exclude: "tuple[str, ...]" = ()) -> bool:
        """rsync the tree over SSH (incremental, compressed). A trailing slash on
        the source copies its CONTENTS into *remote_dir*."""
        ssh_e = (f"ssh -p {self.port} -o BatchMode=yes "
                 f"-o StrictHostKeyChecking=accept-new")
        argv = ["rsync", "-az", "-e", ssh_e]
        for pat in exclude:
            argv += ["--exclude", pat]
        argv += [local_dir.rstrip("/") + "/",
                 f"{self.user}@{self.host}:{remote_dir}"]
        code, _, _ = self._runner(argv, 900)
        return code == 0


# ---------------------------------------------------------------------------
# Local (the medic itself)
# ---------------------------------------------------------------------------


def _default_local_runner(argv: List[str], timeout: int,
                          stdin: Optional[str] = None) -> Result:
    try:
        proc = subprocess.run(argv, capture_output=True, text=True,
                              timeout=timeout, input=stdin)
        return (proc.returncode, proc.stdout, proc.stderr)
    except subprocess.TimeoutExpired:
        return (255, "", "command timed out")
    except FileNotFoundError:
        return (255, "", f"{argv[0]}: command not found")


def _pexpect_interactive(command: str, interactions, timeout: int) -> Result:
    """Drive an interactive shell *command* through a PTY, answering prompts.

    *interactions* is a list of ``(regex, response)``; when a prompt matching a
    regex appears, its response is sent (with a newline). Returns ``(code, out,
    err)`` where out is the full transcript. Used for ``rnodeconf --autoinstall``,
    which reads confirm/continue prompts from the terminal (a piped run hangs)."""
    try:
        import pexpect
    except Exception as exc:                       # pragma: no cover
        return (255, "", f"pexpect unavailable: {exc}")
    patterns = [p for p, _ in interactions]
    child = pexpect.spawn("/bin/bash", ["-lc", command], encoding="utf-8",
                          timeout=timeout, dimensions=(40, 120))
    pats = patterns + [pexpect.EOF, pexpect.TIMEOUT]
    transcript: List[str] = []
    # A prompt appears at most a handful of times; cap iterations so a genuinely
    # stuck prompt ends in TIMEOUT rather than an unbounded loop.
    for _ in range(len(interactions) + 40):
        try:
            i = child.expect(pats)
        except Exception as exc:                   # pragma: no cover
            transcript.append(f"\n[expect error: {exc}]")
            break
        transcript.append(child.before or "")
        if i < len(interactions):
            transcript.append(child.after or "")
            child.sendline(interactions[i][1])
        elif pats[i] is pexpect.EOF:
            break
        else:                                      # TIMEOUT
            transcript.append("\n[timeout waiting for next prompt]")
            break
    try:
        child.expect(pexpect.EOF, timeout=30)
        transcript.append(child.before or "")
    except Exception:
        pass
    try:
        child.close()
    except Exception:
        pass
    code = child.exitstatus if child.exitstatus is not None else 255
    return (code, "".join(transcript), "")


class LocalConnection(Connection):
    """Run commands on THIS machine — the medic flashing / diagnosing a board on
    its OWN USB (no SSH, no emulator). Same ``(code, stdout, stderr)`` contract as
    every other Connection, and the same ``~/.local/bin`` PATH prepend as
    SSHConnection so bare RNS console scripts (``rnodeconf``, ``rnsd``,
    ``rnstatus``) resolve in a non-login shell. ``push_file`` / ``push_tree`` are
    plain local copies, so workflows that "carry" a firmware .bin to a target work
    unchanged when the target IS the medic.
    """

    #: identical to SSHConnection.REMOTE_PATH — pip --user console scripts live in
    #: ~/.local/bin, absent from a non-login shell's PATH.
    LOCAL_PATH = "$HOME/.local/bin:/usr/local/bin:$PATH"

    def _wrap(self, command: str) -> str:
        if not self.login_env:
            return command
        return f'export PATH="{self.LOCAL_PATH}"; {command}'

    def __init__(self, runner: Optional[Callable] = None, login_env: bool = True,
                 interactive_runner: Optional[Callable] = None):
        self._runner = runner or _default_local_runner
        self.login_env = login_env
        # Injectable so tests can drive run_interactive without a PTY / hardware.
        self._interactive_runner = interactive_runner

    def run(self, command: str, timeout: int = 30) -> Result:
        return self._runner(["bash", "-c", self._wrap(command)], timeout)

    def run_interactive(self, command: str, interactions, timeout: int = 400) -> Result:
        """Run *command* in a real PTY, answering prompts as they appear.

        Needed for ``rnodeconf --autoinstall``: its "Hit enter to continue" and
        "Is the above correct? [y/N]" prompts read a keypress from the TERMINAL,
        not stdin, so the old ``printf answers | rnodeconf`` pipe hangs forever
        (and wedges the USB port). Driving it through a PTY lets those prompts be
        answered. *interactions* is a list of ``(regex, response)`` pairs applied
        as each prompt's text appears. Returns the usual ``(code, out, err)`` with
        the full transcript as stdout."""
        if self._interactive_runner is not None:
            return self._interactive_runner(command, interactions, timeout)
        return _pexpect_interactive(self._wrap(command), interactions, timeout)

    def push_file(self, local_path: str, remote_path: str) -> bool:
        import os
        import shutil
        try:
            dst = os.path.expanduser(remote_path)
            os.makedirs(os.path.dirname(dst) or ".", exist_ok=True)
            shutil.copy(os.path.expanduser(local_path), dst)
            return True
        except (OSError, shutil.Error):
            return False

    def push_tree(self, local_dir: str, remote_dir: str,
                  exclude: "tuple[str, ...]" = ()) -> bool:
        import os
        import shutil
        try:
            src = os.path.expanduser(local_dir)
            dst = os.path.expanduser(remote_dir)
            shutil.copytree(
                src, dst, dirs_exist_ok=True,
                ignore=shutil.ignore_patterns(*exclude) if exclude else None)
            return True
        except (OSError, shutil.Error):
            return False


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
        self.pushed_trees: List[Tuple[str, str]] = []

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

    def push_tree(self, local_dir: str, remote_dir: str,
                  exclude: "tuple[str, ...]" = ()) -> bool:
        self.pushed_trees.append((local_dir, remote_dir))
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
    ssh_keys = ("user", "port", "retry_count", "retry_delay", "runner", "sleep",
                "login_env")
    ssh_kwargs = {k: v for k, v in kwargs.items() if k in ssh_keys}
    return SSHConnection(target, **ssh_kwargs)
