"""Run fixed, medic-local command lines WITHOUT invoking a shell (audit C5).

Several Settings / diagnostics modules expose an injectable runner whose public
API — and whose unit tests — pass a command *string* (e.g.
``'sudo -n timedatectl set-time "2026-01-02 03:04:05"'``). Historically the
default runner executed those with ``subprocess.run(cmd, shell=<enabled>)``,
which means any interpolated value could be re-parsed by ``/bin/sh``. This helper
keeps the string-based API but executes the command by tokenising it with
``shlex`` and calling subprocess with an ARGV LIST — so no interpolated value is
ever shell-parsed (no command substitution, globbing, ``;`` chaining, or pipes).

It understands only the handful of trailing stderr redirections those fixed
commands actually use — ``2>/dev/null`` (discard stderr) and ``2>&1`` (merge
stderr into stdout) — and expands ``~`` and ``$VARS`` the way the shell would for
these commands. Pipes and other redirections are UNSUPPORTED by design (raise):
do that filtering in Python instead.

Pure and side-effect free apart from running the requested program; unit-testable
without hardware.
"""

from __future__ import annotations

import os
import shlex
import subprocess
from typing import Tuple

def run(cmd: str, timeout: int = 15) -> Tuple[int, str]:
    """Execute *cmd* without a shell.

    Returns ``(returncode, combined_output)`` where combined_output is stdout
    plus stderr, mirroring the old ``capture_output=True`` + concatenation. A
    trailing ``2>/dev/null`` discards stderr; a trailing ``2>&1`` merges it into
    stdout. Raises ``ValueError`` if the command uses any other shell feature.
    """
    tokens = shlex.split(cmd)
    stderr_dest = subprocess.PIPE
    if tokens and tokens[-1] == "2>/dev/null":
        tokens, stderr_dest = tokens[:-1], subprocess.DEVNULL
    elif tokens and tokens[-1] == "2>&1":
        tokens, stderr_dest = tokens[:-1], subprocess.STDOUT

    # shlex.split keeps ';', '(', ')' etc. as literal characters inside a token
    # (it only ever splits on unquoted whitespace), so those are safe to pass on
    # as argv. The features it would leave as standalone/attached operators — and
    # that we deliberately do NOT emulate — are pipes and further redirections.
    _redirs = {"|", "<", ">", ">>", "&", "2>", "1>", "&>", "2>/dev/null", "2>&1"}
    for tok in tokens:
        if "|" in tok or tok in _redirs:
            raise ValueError(
                f"safe_shell.run does not support pipes/redirections (do it in Python): {cmd!r}")

    argv = [os.path.expanduser(os.path.expandvars(tok)) for tok in tokens]
    if not argv:
        return 0, ""
    p = subprocess.run(argv, stdout=subprocess.PIPE, stderr=stderr_dest,
                       text=True, timeout=timeout)
    return p.returncode, (p.stdout or "") + (p.stderr or "")
