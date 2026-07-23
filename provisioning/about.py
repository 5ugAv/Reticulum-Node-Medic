"""About this software (Settings item 9, read-only).

The medic's own provenance, gathered honestly and cheaply:

  * **Software version** — the git short hash (and branch) this unit is running,
    so a field report can be pinned to an exact build.
  * **Test-suite status** — an HONEST, non-blocking indicator. We do NOT run the
    suite live on the medic (too slow) and we do NOT fabricate a "passing" badge.
    We only report how many tests this checkout *collects* ("N tests"), falling
    back to "run in CI" with the repo link when even collection isn't possible.
  * **Uptime** — how long this unit has been powered, parsed from /proc/uptime.
  * **Licence + repo** — MIT, and the origin remote as a browsable link.

Runner + paths are injectable so it's unit-testable off-hardware (macOS has no
/proc/uptime; a failed git call must degrade gracefully, never raise).
"""

from __future__ import annotations

import re
import subprocess
from typing import Callable, Dict, Optional, Tuple

LICENSE = "MIT"
UPTIME_PATH = "/proc/uptime"

ShellRunner = Callable[[str], Tuple[int, str]]

_HASH_CMD = "git rev-parse --short HEAD"
_BRANCH_CMD = "git rev-parse --abbrev-ref HEAD"
_REMOTE_CMD = "git remote get-url origin"
#: Collect (not run) the suite; the last line carries the "N tests collected" tally.
_COLLECT_CMD = "python3 -m pytest --collect-only -q 2>/dev/null | tail -1"


def _default_run(cmd: str) -> Tuple[int, str]:
    try:
        p = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30)
        return p.returncode, (p.stdout + p.stderr)
    except Exception as e:
        return 1, str(e)


def _one_line(run: ShellRunner, cmd: str) -> str:
    """Run *cmd*, return its last non-empty stdout line, or "" on any failure."""
    code, out = run(cmd)
    if code != 0:
        return ""
    lines = [ln.strip() for ln in (out or "").splitlines() if ln.strip()]
    return lines[-1] if lines else ""


def git_hash(run: Optional[ShellRunner] = None) -> str:
    """Short commit hash of the running build, or "" if git is unavailable."""
    return _one_line(run or _default_run, _HASH_CMD)


def git_branch(run: Optional[ShellRunner] = None) -> str:
    """Current branch name, or "" if unavailable / detached."""
    b = _one_line(run or _default_run, _BRANCH_CMD)
    return "" if b == "HEAD" else b        # detached checkout -> no branch name


def software_version(run: Optional[ShellRunner] = None) -> str:
    """A human string like ``"a1b2c3d (main)"``, ``"a1b2c3d"`` (detached), or
    ``"unknown"`` when git can't be reached."""
    run = run or _default_run
    h = git_hash(run)
    if not h:
        return "unknown"
    b = git_branch(run)
    return f"{h} ({b})" if b else h


def repo_link(run: Optional[ShellRunner] = None) -> str:
    """The origin remote as a browsable https URL (ssh remotes are normalised;
    a trailing ``.git`` is dropped). "" when there's no origin."""
    url = _one_line(run or _default_run, _REMOTE_CMD)
    if not url:
        return ""
    # git@github.com:owner/repo(.git) -> https://github.com/owner/repo
    m = re.match(r"git@([^:]+):(.+)", url)
    if m:
        url = f"https://{m.group(1)}/{m.group(2)}"
    if url.endswith(".git"):
        url = url[:-4]
    return url


def parse_test_count(output: str) -> Optional[int]:
    """Pull the collected-test tally out of ``pytest --collect-only -q`` output.

    Matches lines like ``"142 tests collected in 0.34s"`` / ``"1 test collected"``.
    Returns the integer, or None when the line doesn't report a collection (an
    error, "no tests ran", empty output — anything we can't trust)."""
    if not output:
        return None
    last = [ln.strip() for ln in output.splitlines() if ln.strip()]
    if not last:
        return None
    m = re.search(r"(\d+)\s+tests?\s+collected", last[-1])
    return int(m.group(1)) if m else None


def test_status(run: Optional[ShellRunner] = None) -> str:
    """Honest, non-blocking test indicator: ``"N tests"`` from a collect-only
    pass, else ``"run in CI"`` (never a fabricated "passing")."""
    _code, out = (run or _default_run)(_COLLECT_CMD)
    n = parse_test_count(out)
    return f"{n} tests" if n else "run in CI"


def uptime_seconds(path: str = UPTIME_PATH) -> Optional[float]:
    """Seconds since boot from /proc/uptime (first float), or None when the file
    is absent/unreadable (e.g. off-Pi on macOS)."""
    try:
        with open(path) as f:
            first = f.read().split()[0]
        return float(first)
    except (OSError, ValueError, IndexError):
        return None


def format_uptime(seconds: Optional[float]) -> str:
    """"Up 3 days, 4 h" / "Up 4 h 12 min" / "Up 7 min" / "unknown"."""
    if seconds is None or seconds < 0:
        return "unknown"
    total_min = int(seconds // 60)
    days, rem_min = divmod(total_min, 1440)
    hours, minutes = divmod(rem_min, 60)
    if days:
        return f"Up {days} day{'s' if days != 1 else ''}, {hours} h"
    if hours:
        return f"Up {hours} h {minutes} min"
    return f"Up {minutes} min"


def summary(run: Optional[ShellRunner] = None,
            uptime_path: str = UPTIME_PATH) -> Dict:
    """Everything the About screen shows, in one call."""
    run = run or _default_run
    return {
        "version": software_version(run),
        "test_status": test_status(run),
        "uptime": format_uptime(uptime_seconds(uptime_path)),
        "license": LICENSE,
        "repo": repo_link(run),
    }
