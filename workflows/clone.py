"""Clone Tool — replicate the medic onto a fresh Pi 5 (spec mode #5).

A working medic is the tool code + its carried asset store + the offline RNode
firmware cache + its Python environment + a place on the mesh. Cloning images all
of that onto a fresh Pi 5 over SSH, then gives the new unit a **fresh** Reticulum
identity — the source identity is deliberately never copied, so the two medics
are distinct nodes. Ends by installing an autostart service so the clone boots
straight into the tool.

Runs over a Connection to the target Pi and is testable against an
EmulatedConnection, mirroring the build workflows. Large payloads (tool tree,
61 MB firmware cache) move by ``push_tree`` (rsync); everything else is a command.
"""

from __future__ import annotations

import json
import os
import re
from typing import Callable, List, Optional, Tuple

from transport.connection import Connection
from monitor.registry import NodeRegistry
from workflows.build import StepResult

#: The medic's own tool root, on the source medic (…/reticulum-tool).
TOOL_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
#: Where the tool lands on the clone.
REMOTE_TOOL_DIR = "~/reticulum-tool"
#: The offline RNode firmware cache (outside the repo) — needed to flash offline.
FIRMWARE_CACHE_LOCAL = os.path.expanduser("~/.config/rnodeconf/update")
FIRMWARE_CACHE_REMOTE = "~/.config/rnodeconf/update"
#: Excluded from the tool-tree copy — history, caches, scratch.
TOOL_EXCLUDES = (".git", "__pycache__", "*.pyc", ".pytest_cache", "*.egg-info")
#: Where the clone keeps its copied monitoring DB.
CLONE_DIR = "~/.reticulum-node-medic"
#: The tool's Python stack is pinned in this manifest; wheels for it live in the
#: wheelhouse (populated by workflows.wheelhouse) and travel with the tool tree.
REMOTE_REQUIREMENTS = f"{REMOTE_TOOL_DIR}/assets/requirements.txt"
REMOTE_WHEELS = f"{REMOTE_TOOL_DIR}/assets/packages"

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
def transfer_tool(wf: "CloneWorkflow") -> StepResult:
    # rsync the whole tool tree (code + carried assets: configs, scripts,
    # sketches, packages, maps), minus history/caches.
    wf.connection.run(f"mkdir -p {REMOTE_TOOL_DIR}")
    ok = wf.connection.push_tree(TOOL_ROOT, REMOTE_TOOL_DIR, exclude=TOOL_EXCLUDES)
    if not ok:
        return StepResult("transfer_tool", False,
                          "Could not copy the tool tree to the target (rsync).")
    present = wf.connection.run(f"test -f {REMOTE_TOOL_DIR}/main.py")[0] == 0
    return StepResult("transfer_tool", present,
                      "Copied the tool code + asset store." if present
                      else "Tool tree copied but main.py is missing.")


@clone_step
def transfer_firmware_cache(wf: "CloneWorkflow") -> StepResult:
    # The offline RNode firmware cache lets the clone flash boards with no
    # internet. It lives outside the repo; skip cleanly if this medic has none.
    if not os.path.isdir(FIRMWARE_CACHE_LOCAL):
        return StepResult("transfer_firmware_cache", True,
                          "No local firmware cache to copy (clone can sync it "
                          "online later).", skipped=True)
    wf.connection.run(f"mkdir -p {FIRMWARE_CACHE_REMOTE}")
    ok = wf.connection.push_tree(FIRMWARE_CACHE_LOCAL, FIRMWARE_CACHE_REMOTE)
    return StepResult("transfer_firmware_cache", ok,
                      "Copied the offline RNode firmware cache." if ok
                      else "Could not copy the firmware cache.")


@clone_step
def install_dependencies(wf: "CloneWorkflow") -> StepResult:
    # Install the pinned stack (assets/requirements.txt). Prefer the carried
    # wheelhouse (offline field clone); fall back to online pip if it's absent.
    have_wheels = wf.connection.run(f"ls {REMOTE_WHEELS}/*.whl")[0] == 0
    if have_wheels:
        cmd = (f"pip3 install --no-index --find-links {REMOTE_WHEELS} "
               f"--break-system-packages --user -r {REMOTE_REQUIREMENTS}")
        source = "carried wheelhouse (offline)"
    elif wf.connection.run("curl -fsI -m 5 https://pypi.org")[0] == 0:
        cmd = (f"pip3 install --break-system-packages --user "
               f"-r {REMOTE_REQUIREMENTS}")
        source = "online pip"
    else:
        return StepResult(
            "install_dependencies", False,
            "No carried wheelhouse and no internet — run wheelhouse.cache_wheels "
            "on the medic (online) so clones install offline, or connect WiFi.")
    code, out, err = wf.connection.run(cmd, timeout=1200)
    ok = code == 0
    # Kivy's Python wheel is here, but its runtime needs SDL2 system libs (apt).
    # On Raspberry Pi OS Desktop they're present; a fully offline Lite clone also
    # needs those debs carried. Best-effort, non-fatal.
    if ok:
        wf.connection.run(
            "command -v apt-get >/dev/null && sudo -n apt-get install -y "
            "libsdl2-2.0-0 libsdl2-image-2.0-0 libsdl2-mixer-2.0-0 "
            "libsdl2-ttf-2.0-0 libmtdev1 >/dev/null 2>&1 || true")
    return StepResult("install_dependencies", ok,
                      f"Installed the tool's Python stack from {source}." if ok
                      else f"Dependency install failed ({source}): {(err or out)[-200:]}")


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
    # A NEW identity on the target — never the source's — so the clone is a
    # distinct node on the mesh. rnid prints "New identity <hash> written to …".
    code, out, err = wf.connection.run(
        "mkdir -p ~/.reticulum/storage && "
        "rnid --generate ~/.reticulum/storage/identity")
    if code != 0:
        return StepResult("generate_fresh_identity", False,
                          f"Could not generate identity: {(err or out)[-160:]}")
    m = re.search(r"New identity <([0-9a-f]+)>", out)
    wf.fresh_identity_hash = m.group(1) if m else None
    wf.fresh_identity_generated = True
    tail = f" ({wf.fresh_identity_hash})" if wf.fresh_identity_hash else ""
    return StepResult("generate_fresh_identity", True,
                      f"Generated a fresh Reticulum identity{tail} — source "
                      f"identity NOT copied.")


@clone_step
def configure_autostart(wf: "CloneWorkflow") -> StepResult:
    # A systemd service so the clone boots into the tool. Runs main.py as the
    # login user with its ~/.local/bin on PATH (pip --user console scripts).
    user = wf.connection.run("id -un")[1].strip() or "pi"
    home = f"/home/{user}" if user != "root" else "/root"
    priv = "" if user == "root" else "sudo -n "
    unit = (
        "[Unit]\n"
        "Description=Reticulum Node Medic (tool)\n"
        "After=graphical.target network-online.target\n\n"
        "[Service]\n"
        "Type=simple\n"
        f"User={user}\n"
        f"Environment=HOME={home}\n"
        f"WorkingDirectory={home}/reticulum-tool\n"
        f"ExecStart=/usr/bin/python3 {home}/reticulum-tool/main.py\n"
        "Restart=on-failure\n"
        "RestartSec=5\n\n"
        "[Install]\n"
        "WantedBy=graphical.target\n"
    )
    heredoc = (f"{priv}tee /etc/systemd/system/reticulum-node-medic.service "
               f">/dev/null <<'RNMUNIT'\n{unit}\nRNMUNIT")
    if wf.connection.run(heredoc)[0] != 0:
        return StepResult("configure_autostart", False,
                          "Could not write the autostart service unit.")
    wf.connection.run(f"{priv}systemctl daemon-reload")
    code = wf.connection.run(
        f"{priv}systemctl enable reticulum-node-medic.service")[0]
    return StepResult("configure_autostart", code == 0,
                      "Autostart enabled — the clone boots into the tool." if code == 0
                      else "Could not enable the autostart service.")


@clone_step
def final_verification(wf: "CloneWorkflow") -> StepResult:
    problems = []
    if wf.connection.run(f"test -f {REMOTE_TOOL_DIR}/main.py")[0] != 0:
        problems.append("tool code missing")
    if wf.connection.run(f"test -f {CLONE_DIR}/monitoring_db.json")[0] != 0:
        problems.append("monitoring DB missing")
    if not wf.fresh_identity_generated:
        problems.append("fresh identity not generated")
    if wf.connection.run("python3 -c 'import RNS'")[0] != 0:
        problems.append("RNS not importable")
    if wf.connection.run(
            "systemctl is-enabled reticulum-node-medic.service")[0] != 0:
        problems.append("autostart not enabled")
    ok = not problems
    return StepResult("final_verification", ok,
                      "Clone verified — a fresh medic is ready." if ok
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
        self.fresh_identity_hash: Optional[str] = None

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
