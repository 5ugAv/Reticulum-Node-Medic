"""Runtime for Self Diagnose — gather live medic state, run the checks, do repairs.

The pure checks live in monitor.self_diagnose; this layer runs the actual shell on
the medic (the app runs ON the medic, so these are local reads) and maps repair
keys to actions. Everything routes through an injected ``run`` so it's unit-tested
with no hardware. The default gather is SAFE/non-disruptive (reads files + systemd
state) — it never resets the board or steals the port from the splitter. The
deeper firmware probe (which does reset the board) is a separate, explicit action.
"""

from __future__ import annotations

import time
from typing import Callable, List, Tuple

import safe_shell
from monitor import self_diagnose as sd

Runner = Callable[[str], str]


def _default_run(cmd: str) -> str:
    # Execute the fixed medic-local command without a shell (audit C5); returns
    # combined stdout+stderr, exactly as the previous capture did.
    try:
        _code, out = safe_shell.run(cmd, timeout=15)
        return out
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
    findings.append(sd.check_gps_fresh(run("cat /dev/shm/nodemedic-gps.json 2>/dev/null || cat $HOME/gps_state.json 2>/dev/null"),
                                       now_fn()))
    # medic system health (safe reads — no board reset, no port steal)
    findings.append(sd.check_disk_space(run("df -P / 2>/dev/null")))
    findings.append(sd.check_service(
        "rnsd", run("systemctl is-active rnsd 2>/dev/null").strip() == "active"))
    findings.append(sd.check_cpu_temp(run("vcgencmd measure_temp 2>/dev/null")))
    findings.append(sd.check_throttled(run("vcgencmd get_throttled 2>/dev/null")))
    findings.append(sd.check_wifi(
        run("nmcli -t -f IN-USE,SIGNAL,SSID dev wifi 2>/dev/null")))
    findings.append(sd.check_clock_sync(run("timedatectl show 2>/dev/null"), now_fn()))
    findings.append(sd.check_rns_responding(run("rnstatus 2>/dev/null")))
    # lxmd is mode-aware: only expected when this medic is a HOME propagation node
    mode = run("cat ~/.reticulum-node-medic/node_mode 2>/dev/null").strip().lower()
    profile = run("cat ~/.reticulum-node-medic/home_profile 2>/dev/null").strip().lower()
    wants_prop = mode == "home" and profile != "transport"     # default = propagation
    lxmd_active = run("systemctl is-active lxmd 2>/dev/null").strip() == "active"
    findings.append(sd.check_lxmd(lxmd_active, wants_prop))
    # THE OTHER END OF THE CABLE. The medic checks its own rail, its own USB and
    # its own services, and never asked whether its own NetworkManager was
    # eating the cable-birth link — which it was, killing builds for an evening
    # while the blame went to a power supply, a new cable, two Pis and two USB
    # ports (2026-08-11). The node side had been immune for months.
    # READ THE FILE, do not test for it. safe_shell runs without a shell, so
    # `test -f X && echo yes` passes "&&" through as an argument and always
    # comes back empty — the check reported the drop-in missing on a medic that
    # had it installed, ten minutes after it was written. Reading it also
    # verifies the CONTENT, so a truncated or hand-edited file is caught too.
    findings.append(sd.check_cable_link_unmanaged(
        "unmanaged-devices" in run(f"cat {sd.NM_USB0_CONF} 2>/dev/null"),
        run("journalctl -u NetworkManager -n 200 --no-pager 2>/dev/null")))
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
    "restart_rnsd": {
        "label": "Restart the Reticulum service (rnsd)",
        "cmd": "sudo -n systemctl restart rnsd 2>&1",
        "ok": lambda out: not any(w in out.lower()
                                  for w in ("fail", "error", "not loaded", "authentication")),
    },
    "restart_lxmd": {
        "label": "Restart the message store-and-forward (lxmd)",
        "cmd": "sudo -n systemctl restart lxmd 2>&1",
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
    "free_space": ("Storage is filling up. Safe things to clear: old journal logs "
                   "(journalctl --vacuum-size=50M), cached firmware/images you've "
                   "already flashed, and birth certificates you've exported."),
    "sync_clock": ("The clock is wrong (likely no RTC battery + a power loss). Set it "
                   "in Settings > Date & time — sync from GPS (needs a fix) or from "
                   "NTP when the medic is online."),
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
