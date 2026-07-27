"""Regression guard for audit C5 — no ``subprocess`` shell string execution.

Building a command STRING and handing it to a shell means any interpolated value
can be re-parsed by ``/bin/sh`` (injection). We banned it across the app: the
default runners now execute argv lists via ``safe_shell`` (or ``subprocess`` with
a list) instead. This test walks every non-test source file and fails if the
``shell=True`` pattern reappears, so a future change can't silently reintroduce
it.

If a site *genuinely* needs it (a fixed literal with NO interpolation, filtering
that's impractical in Python), add its repo-relative path to ``ALLOWLIST`` below
WITH a justification comment — a conscious, reviewed decision rather than drift.
"""

import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

#: Files permitted to use shell=True (repo-relative POSIX paths). Each entry MUST
#: be a fixed literal command with no external/interpolated input. Currently EMPTY
#: — every call site was converted to an argv list.
ALLOWLIST: set = set()

_SHELL_TRUE = re.compile(r"shell\s*=\s*True")
_SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", "node_modules", "assets", ".venv"}


def _python_sources():
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        # skip the test tree itself — tests legitimately reference the pattern
        if os.path.abspath(dirpath) == os.path.join(ROOT, "tests"):
            dirnames[:] = []
            continue
        for name in filenames:
            if name.endswith(".py"):
                yield os.path.join(dirpath, name)


def test_no_shell_true_in_non_test_sources():
    offenders = []
    for path in _python_sources():
        rel = os.path.relpath(path, ROOT).replace(os.sep, "/")
        if rel in ALLOWLIST:
            continue
        with open(path, encoding="utf-8") as f:
            if _SHELL_TRUE.search(f.read()):
                offenders.append(rel)
    assert not offenders, (
        "shell=True reintroduced (audit C5) in: " + ", ".join(sorted(offenders)) +
        " — convert to an argv list / safe_shell, or allowlist with justification.")
