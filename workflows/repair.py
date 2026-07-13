"""Repair (Diagnose) workflow.

Chains the six diagnostic modules in a fixed order, running every check and
collecting the issues into a ``RepairSession``. Progress is reported through
``ProgressEvent`` callbacks so a UI can expand each category into its
individual checks live. The same code powers all three self-healing tiers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, List, Optional

from node_profile import NodeProfile
from transport.connection import Connection
from workflows.build import detect_rnode_port
from diagnostics.base import DiagnosticCheck, Fix, Issue
from diagnostics.power_hardware import PowerHardwareCheck
from diagnostics.reticulum_software import ReticulumSoftwareCheck
from diagnostics.radio_firmware import RadioFirmwareCheck
from diagnostics.system_health import SystemHealthCheck
from diagnostics.network_mesh import NetworkMeshCheck
from diagnostics.client_connectivity import ClientConnectivityCheck

#: Diagnostic modules in the order the operator sees them.
MODULE_ORDER = [
    PowerHardwareCheck,
    ReticulumSoftwareCheck,
    RadioFirmwareCheck,
    SystemHealthCheck,
    NetworkMeshCheck,
    ClientConnectivityCheck,
]


@dataclass
class CategoryResult:
    category: str
    issues: List[Issue] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return len(self.issues) == 0


@dataclass
class RepairSession:
    categories: List[CategoryResult] = field(default_factory=list)

    @property
    def all_issues(self) -> List[Issue]:
        issues = [i for c in self.categories for i in c.issues]
        return sorted(issues, key=lambda i: i.severity_rank)

    @property
    def auto_fixable_issues(self) -> List[Issue]:
        return [i for i in self.all_issues if i.auto_fixable]


@dataclass
class ProgressEvent:
    type: str  # category_start | check_start | check_done | category_done | run_complete
    category: str = ""
    check_name: str = ""
    issue: Optional[Issue] = None
    category_result: Optional[CategoryResult] = None
    session: Optional[RepairSession] = None


ProgressCallback = Callable[[ProgressEvent], None]


class _InstrumentedModule:
    """Wraps a module and monkey-patches ``_check`` so every check fires
    ``check_start`` / ``check_done`` progress events as it runs."""

    def __init__(self, module: DiagnosticCheck, emit: ProgressCallback):
        self.module = module
        self.emit = emit

    def run(self) -> List[Issue]:
        original = self.module._check
        category = self.module.category_name

        def wrapped(check_name, condition, *args, **kwargs):
            self.emit(ProgressEvent(
                "check_start", category=category, check_name=check_name))
            issue = original(check_name, condition, *args, **kwargs)
            self.emit(ProgressEvent(
                "check_done", category=category, check_name=check_name,
                issue=issue))
            return issue

        self.module._check = wrapped
        try:
            return self.module.run()
        finally:
            self.module._check = original


class RepairWorkflow:
    def __init__(self, connection: Connection, profile: NodeProfile):
        self.connection = connection
        self.profile = profile
        self.modules: List[DiagnosticCheck] = [
            cls(connection, profile) for cls in MODULE_ORDER
        ]
        self.session: Optional[RepairSession] = None

    def run(self, on_progress: Optional[ProgressCallback] = None) -> RepairSession:
        emit = on_progress or (lambda e: None)
        # Detect the real RNode serial port up front so EVERY module checks the
        # same actual port. The profile default is often wrong (ttyUSB0 vs a real
        # Heltec V4 on ttyACM0), which false-flagged serial_port_exists and made
        # the radio checks probe the wrong device. Modules share this profile.
        detected = detect_rnode_port(self.connection)
        if detected:
            self.profile.radio.serial_port = detected
        session = RepairSession()

        for module in self.modules:
            emit(ProgressEvent("category_start", category=module.category_name))
            issues = _InstrumentedModule(module, emit).run()
            result = CategoryResult(category=module.category_name, issues=issues)
            session.categories.append(result)
            emit(ProgressEvent(
                "category_done", category=module.category_name,
                category_result=result))

        self.session = session
        emit(ProgressEvent("run_complete", session=session))
        return session

    def _module_for(self, issue: Issue) -> Optional[DiagnosticCheck]:
        for module in self.modules:
            if module.category_name == issue.category:
                return module
        return None

    def fix_one(self, issue: Issue) -> Fix:
        module = self._module_for(issue)
        if module is None:
            return Fix(issue=issue, success=False,
                       message="No module owns this issue.")
        fix = module.fix(issue)
        if fix.success:
            self.profile.fixes_applied.append(issue.check_name)
        return fix

    def fix_all(self, on_progress: Optional[ProgressCallback] = None) -> List[Fix]:
        emit = on_progress or (lambda e: None)
        fixes: List[Fix] = []
        issues = (self.session.auto_fixable_issues
                  if self.session else [])
        for issue in issues:
            emit(ProgressEvent(
                "check_start", category=issue.category,
                check_name=issue.check_name, issue=issue))
            fix = self.fix_one(issue)
            fixes.append(fix)
            emit(ProgressEvent(
                "check_done", category=issue.category,
                check_name=issue.check_name, issue=issue))
        return fixes
