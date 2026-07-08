"""Base classes for diagnostic checks.

A ``DiagnosticCheck`` is a category of related checks (e.g. "Reticulum
software"). Its ``run()`` executes every check in the category and returns an
``Issue`` for each one that fails — checks never short-circuit, so a single
failure does not hide later problems. Fixes are dispatched by check name
through ``_fix_handlers()``.

The exact same check code runs in all three self-healing tiers: on the node
itself (Tier 1, via a systemd timer), remotely from the tool (Tier 2), or
over a physical serial link (Tier 3). Only the ``Connection`` differs.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

from node_profile import NodeProfile
from transport.connection import Connection

Result = Tuple[int, str, str]

SEVERITY_ORDER = {"critical": 0, "warning": 1, "info": 2}


@dataclass
class Issue:
    """A single failed check, described in plain English."""

    check_name: str
    category: str
    description: str
    severity: str = "warning"  # "critical" | "warning" | "info"
    raw_detail: str = ""
    auto_fixable: bool = False
    fix_description: str = ""

    @property
    def severity_rank(self) -> int:
        return SEVERITY_ORDER.get(self.severity, 1)


@dataclass
class Fix:
    """Outcome of attempting to repair an ``Issue``."""

    issue: Issue
    success: bool
    message: str = ""
    raw_output: str = ""


class DiagnosticCheck(ABC):
    category_name: str = "Unnamed"

    def __init__(self, connection: Connection, profile: NodeProfile):
        self.connection = connection
        self.profile = profile
        self._root = None  # cached _is_root() result

    # -- required ----------------------------------------------------------

    @abstractmethod
    def run(self) -> List[Issue]:
        """Run every check in this category, returning one ``Issue`` per
        failure. Must not stop early — all checks always execute."""

    # -- fixes -------------------------------------------------------------

    def _fix_handlers(self) -> Dict[str, Callable[[Issue], Fix]]:
        """Map ``check_name`` -> handler. Override to register fixes."""
        return {}

    def fix(self, issue: Issue) -> Fix:
        handler = self._fix_handlers().get(issue.check_name)
        if handler is None:
            return Fix(
                issue=issue,
                success=False,
                message=f"No automatic fix available for '{issue.check_name}'.",
            )
        try:
            return handler(issue)
        except Exception as exc:  # never let a fix crash the run
            return Fix(
                issue=issue,
                success=False,
                message=f"Fix failed: {exc}",
            )

    # -- check construction ------------------------------------------------

    def _check(
        self,
        check_name: str,
        condition: bool,
        plain_description: str,
        severity: str = "warning",
        raw_detail: str = "",
        auto_fixable: bool = False,
        fix_description: str = "",
    ) -> Optional[Issue]:
        """Return an ``Issue`` when *condition* is False, else ``None``.

        A ``True`` condition means the check passed — nothing to report.
        """
        if condition:
            return None
        return Issue(
            check_name=check_name,
            category=self.category_name,
            description=plain_description,
            severity=severity,
            raw_detail=raw_detail,
            auto_fixable=auto_fixable,
            fix_description=fix_description,
        )

    # -- command helpers ---------------------------------------------------

    def _run_cmd(self, command: str, timeout: int = 30) -> Result:
        return self.connection.run(command, timeout)

    def _cmd_output(self, command: str, timeout: int = 30) -> str:
        code, out, _ = self._run_cmd(command, timeout)
        return out if code == 0 else ""

    def _service_is_active(self, service_name: str) -> bool:
        code, out, _ = self._run_cmd(f"systemctl is-active {service_name}")
        return code == 0 and out.strip() == "active"

    def _service_is_enabled(self, service_name: str) -> bool:
        code, out, _ = self._run_cmd(f"systemctl is-enabled {service_name}")
        return code == 0 and "enabled" in out

    def _file_exists(self, path: str) -> bool:
        code, _, _ = self._run_cmd(f"test -f {path}")
        return code == 0

    def _dir_exists(self, path: str) -> bool:
        code, _, _ = self._run_cmd(f"test -d {path}")
        return code == 0

    # -- privilege ---------------------------------------------------------

    def _is_root(self) -> bool:
        """True if the session runs as root (cached)."""
        if self._root is None:
            code, out, _ = self._run_cmd("id -u")
            self._root = (code == 0 and out.strip() == "0")
        return self._root

    def _priv(self, command: str) -> str:
        """Prefix ``sudo -n`` when not root, so privileged reads work over
        SSH-as-non-root. ``-n`` fails fast instead of prompting for a password.
        """
        return command if self._is_root() else f"sudo -n {command}"
