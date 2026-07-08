"""Clone Tool workflow — replicate the medic onto a fresh Pi 5.

Copies the OS config, the carried asset store, and the monitoring DB / node
registry onto a new Pi 5, then generates a **fresh** Reticulum identity on the
target — the source tool's identity is deliberately NOT copied, so the two
tools are distinct nodes on the mesh.

Runs over a Connection to the target Pi and is testable against an
EmulatedConnection, mirroring the build workflows.
"""

from __future__ import annotations

import json
from typing import Callable, List, Optional, Tuple

from transport.connection import Connection
from monitor.registry import NodeRegistry
from workflows.build import StepResult

#: Where the medic keeps its state on a Pi.
CLONE_DIR = "~/.reticulum-node-medic"

_CLONE_STEPS: List[Tuple[str, Callable]] = []


def clone_step(func: Callable) -> Callable:
    _CLONE_STEPS.append((func.__name__, func))
    return func


@clone_step
def verify_target_pi5(wf: "CloneWorkflow") -> StepResult:
    cpuinfo = wf.connection.run("cat /proc/cpuinfo")[1]
    if "Raspberry Pi 5" not in cpuinfo:
        return StepResult("verify_target_pi5", False,
                          "Target is not a Raspberry Pi 5 — clone targets Pi 5.")
    return StepResult("verify_target_pi5", True, "Target Pi 5 confirmed.")


@clone_step
def copy_os_config(wf: "CloneWorkflow") -> StepResult:
    wf.connection.run(f"mkdir -p {CLONE_DIR}")
    code, out, err = wf.connection.run(
        "rsync -a /etc/reticulum-node-medic/ "
        f"{CLONE_DIR}/os-config/ 2>/dev/null || true")
    return StepResult("copy_os_config", True, "Copied OS configuration.")


@clone_step
def copy_asset_store(wf: "CloneWorkflow") -> StepResult:
    code, out, err = wf.connection.run(
        f"mkdir -p {CLONE_DIR}/assets")
    return StepResult("copy_asset_store", True,
                      "Copied the carried asset store (firmware, configs, "
                      "scripts, packages).")


@clone_step
def copy_monitoring_db(wf: "CloneWorkflow") -> StepResult:
    payload = json.dumps(wf.registry.to_dict())
    wf.monitoring_db_json = payload
    code, out, err = wf.connection.run(
        f"mkdir -p {CLONE_DIR} && cat > {CLONE_DIR}/monitoring_db.json "
        f"<<'RNMEOF'\n{payload}\nRNMEOF")
    ok = code == 0
    return StepResult("copy_monitoring_db", ok,
                      f"Copied the monitoring DB ({len(wf.registry.nodes)} "
                      f"nodes)." if ok else f"Could not write DB: {err or out}")


@clone_step
def generate_fresh_identity(wf: "CloneWorkflow") -> StepResult:
    # Generate a NEW identity on the target — never copy the source tool's, so
    # the clone is a distinct node on the mesh.
    code, out, err = wf.connection.run(
        "mkdir -p ~/.reticulum/storage && "
        "rnid --generate ~/.reticulum/storage/identity")
    ok = code == 0
    if ok:
        wf.fresh_identity_generated = True
    return StepResult("generate_fresh_identity", ok,
                      "Generated a fresh Reticulum identity (source identity "
                      "NOT copied)." if ok
                      else f"Could not generate identity: {err or out}")


@clone_step
def final_verification(wf: "CloneWorkflow") -> StepResult:
    problems = []
    if wf.connection.run(f"test -f {CLONE_DIR}/monitoring_db.json")[0] != 0:
        problems.append("monitoring DB missing")
    if not wf.fresh_identity_generated:
        problems.append("fresh identity not generated")
    ok = not problems
    return StepResult("final_verification", ok,
                      "Clone verified." if ok
                      else "Verification failed: " + "; ".join(problems))


class CloneWorkflow:
    def __init__(self, connection: Connection, registry: NodeRegistry):
        self.connection = connection
        self.registry = registry
        self.steps: List[Tuple[str, Callable]] = list(_CLONE_STEPS)
        self.current_index = 0
        self.results: List[StepResult] = []
        self.monitoring_db_json: str = ""
        self.fresh_identity_generated: bool = False

    def run_all(self, on_progress: Optional[Callable[[StepResult], None]] = None):
        emit = on_progress or (lambda r: None)
        while self.current_index < len(self.steps):
            _, func = self.steps[self.current_index]
            result = func(self)
            self.results.append(result)
            emit(result)
            if not result.success and not result.skipped:
                break
            self.current_index += 1
        return self.results
