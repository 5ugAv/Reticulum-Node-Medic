"""Runtime for Self Diagnose — gather live medic state, run the checks, do repairs.

The pure checks live in monitor.self_diagnose; this layer runs the actual shell on
the medic (the app runs ON the medic, so these are local reads) and maps repair
keys to actions. Everything routes through an injected ``run`` so it's unit-tested
with no hardware. The default gather is SAFE/non-disruptive (reads files + systemd
state) — it never resets the board or steals the port from the splitter. The
deeper firmware probe (which does reset the board) is a separate, explicit action.
"""

from __future__ import annotations

import subprocess
import time
from typing import Callable, List, Tuple

from monitor import self_diagnose as sd

Runner = Callable[[str], str]


def _default_run(cmd: str) -> str:
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=15)
        return (r.stdout or "") + (r.stderr or "")
    except Exception as e:
        return str(e)


def _splitter_cpu_uptime(run: Runner) -> Tuple[float, float]:
    """(cpu_seconds, uptime_seconds) for the splitter process, or (0, 0)."""
    pid = run("systemctl show -p MainPID --value rnode-splitter 2>/dev/null").strip()
    if not pid or pid == "0":
        return 0.0, 0.0
    parts = run(f"ps -o cputimes=,etimes= -p {pid} 2>/dev/null").split()
    try:
        return float(parts[0]), float(parts[1])
    except (IndexError, ValueError):
        return 0.0, 0.0


def gather(run: Runner = _default_run, now_fn=time.time) -> List[sd.Finding]:
    """Run the SAFE checks against the medic's own onboard radio/GPS board."""
    findings = [sd.check_usb_present(run("ls /dev/serial/by-id/ 2>/dev/null"))]
    active = run("systemctl is-active rnode-splitter 2>/dev/null").strip() == "active"
    cpu, up = _splitter_cpu_uptime(run)
    log = run("journalctl -u rnode-splitter -n 12 --no-pager 2>/dev/null")
    findings.append(sd.check_splitter(active, cpu, up, log))
    findings.append(sd.check_gps_fresh(run("cat $HOME/gps_state.json 2>/dev/null"),
                                       now_fn()))
    return findings


#: Auto-runnable repairs (safe, one command). Others are GUIDANCE (need hardware /
#: the operator at the bench) — reflash+provision is the big one, still being built.
_AUTO_REPAIRS = {
    "restart_splitter": {
        "label": "Restart the radio splitter",
        "cmd": "sudo -n systemctl restart rnode-splitter 2>&1",
        "ok": lambda out: not any(w in out.lower()
                                  for w in ("fail", "error", "not loaded", "authentication")),
    },
}

_GUIDANCE = {
    "usb_recover": ("Onboard radio dropped off USB. Re-seat it (or power-cycle the "
                    "whole medic). If it stays gone, try a different USB port and a "
                    "known-good data cable."),
    "reflash_provision": ("The onboard firmware is corrupt/unprovisioned. Recovery is "
                          "reflash the Tracker firmware then provision it "
                          "(autoinstall → homebrew → --firmware-hash). This runs at the "
                          "bench — auto-recovery is coming."),
}


def repair_kind(key: str) -> str:
    if key in _AUTO_REPAIRS:
        return "auto"
    if key in _GUIDANCE:
        return "guided"
    return "unknown"


def guidance(key: str) -> str:
    return _GUIDANCE.get(key, "")


def run_repair(key: str, run: Runner = _default_run) -> Tuple[bool, str]:
    """Execute an auto repair. Returns (ok, message). Guided/unknown keys return
    False with their guidance text (the UI shows it rather than running anything)."""
    r = _AUTO_REPAIRS.get(key)
    if r is None:
        return False, guidance(key) or f"No automatic repair for '{key}'."
    out = run(r["cmd"])
    return r["ok"](out), (out.strip() or "Done.")
