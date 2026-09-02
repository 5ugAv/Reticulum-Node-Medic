"""MITOSIS — replicate the medic onto a fresh Pi 5 (mode 6, formerly Clone Tool).

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

import glob
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

#: Everything a medic needs to make ANOTHER medic that does NOT already live
#: inside the tool tree. Without these the clone inherits the code, the Python
#: stack, the maps and the roster - but cannot build firmware, image a card, or
#: birth its own first radio. That is a copy of a medic, not a medic.
#:
#: Found the hard way: the first clone reached its firstborn step and stopped
#: with "the medic's Tracker fork build is missing" and "arduino-cli not
#: installed", because TRACKER_BUILD_DIR points at ~/overlay_test - outside
#: TOOL_ROOT - and nothing installed a toolchain.
#:
#: (path, why it travels, required)
CARRIED_TREES = (
    ("~/.arduino15", "the ESP32 and nRF52 toolchains arduino-cli installs", True),
    ("~/.local/bin", "arduino-cli, esptool, rnodeconf, adafruit-nrfutil, pio", True),
    ("~/Arduino", "Arduino libraries the firmware builds include", True),
    ("~/pi_os_lite.img.xz", "the Pi OS image, so the clone can image the NEXT card", True),
    ("~/overlay_test", "the Tracker firmware fork - its firstborn's radio", True),
    ("~/RNode_Firmware", "the RNode firmware fork", False),
    ("~/MeshPocket", "the MeshPocket RNode port", False),
    ("~/RTNode-2400", "the RTNode-2400 firmware", False),
    ("~/rnm-assets", "RTNode-2400 build assets", False),
)

#: Scratch that must NOT travel: working images, one-off build dirs, backups of
#: a particular board, and anything a debugging session left behind. Carrying
#: these would add gigabytes and pass on this medic's mess as if it were the
#: tool.
CARRY_SKIP = ("imgwork", "techo-test", "tracker_build", "supreme_build",
              "upstream_pr", "pr115_alt", "dev_pristine", "pr126_dev",
              "upstream_baseline")


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
                          "Couldn't copy the tool across to the new medic. "
                          "Check the cable between the two medics is firmly in "
                          "both, then press Retry. (rsync of the tool tree "
                          "failed.)")
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
def carry_the_toolchain(wf: "CloneWorkflow") -> StepResult:
    """Copy the toolchains, firmware trees and OS image the clone needs to be a
    medic in its own right rather than a read-only copy of one.

    This is the step that makes replication actually transitive: after it, the
    new medic can build firmware, flash a board, image a card and clone again -
    with no internet, and without this medic.

    Several gigabytes, so it is the slowest step by a wide margin. Missing
    OPTIONAL trees are skipped and named rather than failing: a medic that never
    had an RTNode tree should still be able to make a medic.
    """
    import os
    sent, skipped, failed = [], [], []
    for path, why, required in CARRIED_TREES:
        local = os.path.expanduser(path)
        if not os.path.exists(local):
            (failed if required else skipped).append(f"{path} ({why})")
            continue
        remote = path
        if os.path.isdir(local):
            wf.connection.run(f"mkdir -p {remote}")
            ok = wf.connection.push_tree(local, remote, exclude=CARRY_SKIP)
        else:
            ok = wf.connection.push_file(local, remote) if hasattr(
                wf.connection, "push_file") else wf.connection.push_tree(
                    os.path.dirname(local), os.path.dirname(remote) or "~")
        (sent if ok else failed).append(path)
    if failed:
        return StepResult(
            "carry_the_toolchain", False,
            "The new medic did not get everything it needs to build firmware: "
            + ", ".join(failed) + ". Without these it can run, but it cannot "
            "birth its own radio or make another medic.")
    msg = f"Carried {len(sent)} toolchain/firmware trees."
    if skipped:
        msg += f" Not on this medic, so not carried: {', '.join(skipped)}."
    return StepResult("carry_the_toolchain", True, msg)


@clone_step
def install_dependencies(wf: "CloneWorkflow") -> StepResult:
    # THE LITE IMAGE SHIPS NO pip3 — the lesson the node births paid for on
    # HOPE (2026-08-01) and the clone relearned on HAWKEYE (2026-08-25, live:
    # "pip3: command not found" behind a clipped red row). _ensure_pip
    # bootstraps pip OFFLINE from the wheel Debian already put on the image.
    from workflows.build import _ensure_pip
    pip_ok, pip_note = _ensure_pip(wf)
    if not pip_ok:
        return StepResult("install_dependencies", False, pip_note)
    pip = wf.pip_cmd
    # Install the pinned stack (assets/requirements.txt). Prefer the carried
    # wheelhouse (offline field clone); fall back to online pip if it's absent.
    have_wheels = wf.connection.run(f"ls {REMOTE_WHEELS}/*.whl")[0] == 0
    if have_wheels:
        cmd = (f"{pip} install --no-index --find-links {REMOTE_WHEELS} "
               f"--break-system-packages --user -r {REMOTE_REQUIREMENTS}")
        source = f"carried wheelhouse (offline){pip_note}"
    elif wf.connection.run("curl -fsI -m 5 https://pypi.org")[0] == 0:
        cmd = (f"{pip} install --break-system-packages --user "
               f"-r {REMOTE_REQUIREMENTS}")
        source = f"online pip{pip_note}"
    else:
        return StepResult(
            "install_dependencies", False,
            "The new medic needs its software, but this medic has no carried "
            "copy and no internet to fetch it. Connect THIS medic to WiFi "
            "(Settings \u25b8 WiFi) and press Retry — once it has been online "
            "even once, it carries its own copy and later clones work offline. "
            "(No carried wheelhouse and no internet.)")
    code, out, err = wf.connection.run(cmd, timeout=1200)
    ok = code == 0
    # No apt step needed: the Kivy wheel vendors its own SDL2/SDL2_image/mixer/
    # ttf + libpng (auditwheel Kivy.libs/), verified by ldd showing zero
    # unresolved libs. The only OS requirement is EGL/GLES, which is part of base
    # Raspberry Pi OS — so the carried wheelhouse alone gives a runnable GUI
    # offline, with nothing extra to carry.
    return StepResult("install_dependencies", ok,
                      f"Installed the tool's Python stack from {source}." if ok
                      else f"Dependency install failed ({source}): {(err or out)[-200:]}")


@clone_step
def copy_monitoring_db(wf: "CloneWorkflow") -> StepResult:
    """The registry, to the filename the app actually LOADS (registry.json
    — the old monitoring_db.json was a green-ticked no-op the app never
    read), moved by scp rather than a shell heredoc (a fleet-scale registry
    overflows the kernel's single-argv ceiling and killed the whole thread
    — both found by adversarial review, 2026-08-25)."""
    import tempfile
    payload = json.dumps(wf.registry.to_dict())
    wf.monitoring_db_json = payload
    wf.connection.run(f"mkdir -p {CLONE_DIR}")
    with tempfile.NamedTemporaryFile("w", suffix=".json",
                                     delete=False) as fh:
        fh.write(payload)
        tmp = fh.name
    try:
        ok = wf.connection.push_file(tmp, f"{CLONE_DIR}/registry.json")
    finally:
        os.unlink(tmp)
    return StepResult("copy_monitoring_db", ok,
                      f"Copied the monitoring records ({len(wf.registry.nodes)} "
                      f"nodes)." if ok else
                      "Could not copy the registry to the clone (scp).")


@clone_step
def copy_offline_maps(wf: "CloneWorkflow") -> StepResult:
    """The carried basemaps, from the DURABLE maps home (2026-08-27: tiles
    moved out of the repo tree to ~/.reticulum-node-medic/maps so deploys
    can't eat them — which silently removed them from the tool-tree rsync
    above; a clone made after that carried ZERO map tiles, found preparing
    the 2026-08-30 walkthrough). A map the medic spent hours downloading is
    exactly the kind of thing a child should inherit rather than re-fetch —
    offline is the whole point. No maps yet is a recorded absence, not a
    failure: the clone downloads its own when its keeper asks."""
    src = os.path.expanduser(os.path.join(CLONE_DIR, "maps"))
    if not glob.glob(os.path.join(src, "*.mbtiles")):
        return StepResult("copy_offline_maps", True,
                          "No offline maps on this medic to hand down — the "
                          "clone can download its own from Settings.",
                          skipped=True)
    wf.connection.run(f"mkdir -p {CLONE_DIR}/maps")
    ok = True
    copied = []
    for path in sorted(glob.glob(os.path.join(src, "*.mbtiles"))):
        name = os.path.basename(path)
        if not wf.connection.push_file(path, f"{CLONE_DIR}/maps/{name}"):
            ok = False
            break
        copied.append(name)
    mb = sum(os.path.getsize(os.path.join(src, n)) for n in copied) / 1e6
    return StepResult("copy_offline_maps", ok,
                      f"Handed down the offline maps ({len(copied)} file(s), "
                      f"{mb:.0f} MB)." if ok else
                      "Could not copy the offline maps across.")


@clone_step
def copy_kin_roster(wf: "CloneWorkflow") -> StepResult:
    """Carry the medic's fleet roster (monitor.kin_roster: each node's name, type,
    DEPLOYED LOCATION and interface links) to the clone, so the clone's VITALS +
    SCAN map show the same kin from first boot. The map TILES themselves ride the
    tool tree (assets/maps); this is the who-and-where that populates them."""
    import tempfile
    from monitor.kin_roster import load_roster
    roster = load_roster()
    payload = json.dumps(roster, indent=2, sort_keys=True)
    wf.connection.run(f"mkdir -p {CLONE_DIR}")
    with tempfile.NamedTemporaryFile("w", suffix=".json",
                                     delete=False) as fh:
        fh.write(payload)
        tmp = fh.name
    try:
        ok = wf.connection.push_file(tmp, f"{CLONE_DIR}/kin.json")
    finally:
        os.unlink(tmp)
    return StepResult("copy_kin_roster", ok,
                      f"Carried the fleet roster ({len(roster)} node(s) with their "
                      f"locations) to the clone." if ok
                      else f"Could not write kin roster: {err or out}")


@clone_step
def generate_fresh_identity(wf: "CloneWorkflow") -> StepResult:
    # A NEW identity on the target — never the source's — so the clone is a
    # distinct node on the mesh. rnid prints "New identity <hash> written to …".
    # A RETRY must not deadlock: rnid refuses to overwrite an existing
    # identity (exit nonzero), so a ladder failing AFTER this step could
    # never be re-run (adversarial review 2026-08-25). An identity already
    # on the clone was made by THIS flow — keep it.
    if wf.connection.run("test -f ~/.reticulum/storage/identity")[0] == 0:
        wf.fresh_identity_generated = True
        return StepResult("generate_fresh_identity", True,
                          "The clone already has its own identity from an "
                          "earlier run — kept (never regenerated).")
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
def stamp_lineage(wf: "CloneWorkflow") -> StepResult:
    """Record on the CLONE which parent unit it descended from — its own
    tool-identity store gets a ``parent`` = THIS (source) medic's identity + name.
    Trust is per-unit and never transitive (Settings ▸ Trusted operators), so a
    clone knows its parent but inherits no trust automatically. The clone stamps
    its OWN born date on first boot (from its fresh identity)."""
    from provisioning import tool_identity as ti
    src_hash = ti.identity_hash()                    # this source medic (local)
    src_name = ti.tool_name()
    payload = json.dumps(
        {"parent": {"hash": src_hash, "name": src_name,
                    "via": "cloned from this unit"}}, indent=2, sort_keys=True)
    code, out, err = wf.connection.run(
        "mkdir -p ~/.reticulum-node-medic && "
        f"cat > ~/.reticulum-node-medic/tool_identity.json <<'RNMEOF'\n{payload}\nRNMEOF")
    ok = code == 0
    return StepResult("stamp_lineage", ok,
                      f"Stamped lineage on the clone: parent {src_name}"
                      f"{' (' + src_hash + ')' if src_hash else ''}." if ok
                      else f"Could not stamp lineage: {(err or out)[-160:]}")


@clone_step
def record_child_trust(wf: "CloneWorkflow") -> StepResult:
    """Record the new clone in THIS (source) medic's trust store as a trusted
    DIRECT child unit — you made it, so you trust it. Its own future clones are
    NOT covered (trust is non-transitive; grandchildren need manual approval)."""
    child = getattr(wf, "fresh_identity_hash", None)
    if not child:
        return StepResult("record_child_trust", True,
                          "No child identity hash captured — skipped.")
    try:
        from monitor import trust
        from provisioning import tool_identity as ti
        self_hash = ti.identity_hash()
        if self_hash:
            trust.set_self(self_hash, ti.tool_name())
        trust.record_child_clone(child, f"Clone {child[:8]}", parent_hash=self_hash or "")
    except Exception as e:
        return StepResult("record_child_trust", False, f"Could not record trust: {e}")
    return StepResult("record_child_trust", True,
                      f"Recorded clone {child[:8]} as a trusted child unit.")


@clone_step
def configure_autostart(wf: "CloneWorkflow") -> StepResult:
    """Boot-into-the-tool, the way that PROVED OUT on HAWKEYE (2026-08-25):
    a `cage` Wayland kiosk owning tty1 as a real logind seat (PAMName +
    TTYPath — that grant is what lets it open the display), running the UI
    with the GL-through-SDL backend (desktop libGL does not exist on Lite)
    and the theme's designed density. Plus the touch-retry unit: the DSI
    panel's Goodix chip isn't awake when the driver first probes (~3s,
    I2C -121); a warm rebind moments later binds instantly."""
    user = wf.connection.run("id -un")[1].strip() or "pi"
    home = f"/home/{user}" if user != "root" else "/root"
    unit = (
        "[Unit]\n"
        "Description=Reticulum Node Medic (tool)\n"
        "Conflicts=getty@tty1.service\n"
        "After=getty@tty1.service systemd-user-sessions.service\n\n"
        "[Service]\n"
        "Type=simple\n"
        f"User={user}\n"
        "PAMName=login\n"
        "TTYPath=/dev/tty1\n"
        "StandardInput=tty\n"
        "StandardOutput=journal\n"
        "StandardError=journal\n"
        f"Environment=HOME={home}\n"
        "Environment=KIVY_METRICS_DENSITY=1.5\n"
        "Environment=KIVY_GL_BACKEND=sdl2\n"
        "Environment=XCURSOR_THEME=nodemedic-empty\n"
        f"WorkingDirectory={home}/reticulum-tool\n"
        # cage does NOT forward its child's stdout to the journal — the first
        # crash-loop on HAWKEYE was invisible until the child got its own log.
        f"ExecStart=/usr/bin/cage -s -- /bin/sh -c 'exec /usr/bin/python3 "
        f"{home}/reticulum-tool/main.py >> {home}/ui.log 2>&1'\n"
        "Restart=on-failure\n"
        "RestartSec=5\n\n"
        "[Install]\n"
        "WantedBy=multi-user.target\n"
    )
    rebind = (
        "[Unit]\n"
        "Description=Retry the DSI touch controller after the panel wakes\n"
        "After=multi-user.target\n\n"
        "[Service]\n"
        "Type=oneshot\n"
        "ExecStart=/bin/sh -c 'for i in $(seq 1 15); do "
        "grep -q Goodix /proc/bus/input/devices && exit 0; "
        "echo 11-005d > /sys/bus/i2c/drivers/Goodix-TS/bind 2>/dev/null; "
        "sleep 2; done; exit 0'\n\n"
        "[Install]\n"
        "WantedBy=multi-user.target\n"
    )
    # The DSI touchscreen advertises a legacy mouse beside its touch — the
    # compositor then parks a pointer arrow at every tap (the lost tourist,
    # HAWKEYE 2026-08-25). Declare it touch-only at the udev layer.
    touch_rule = (
        'ATTRS{name}=="Goodix Capacitive TouchScreen", '
        'ENV{ID_INPUT_MOUSE}="", ENV{ID_INPUT_POINTINGSTICK}="", '
        'ENV{ID_INPUT_TOUCHSCREEN}="1"\n'
    )
    priv = "" if user == "root" else "sudo -n "
    # The invisible cursor theme (68-byte generated Xcursor): cage 0.2 draws
    # its pointer arrow regardless of devices — parked wherever the last tap
    # landed. Transparent beats fighting it (HAWKEYE, 2026-08-25).
    theme_dir = os.path.join(TOOL_ROOT, "assets", "ui", "empty-cursor")
    if os.path.isdir(theme_dir):
        wf.connection.run("mkdir -p /tmp/nm-cursor")
        wf.connection.push_file(os.path.join(theme_dir, "left_ptr"),
                                "/tmp/nm-cursor/left_ptr")
        wf.connection.push_file(os.path.join(theme_dir, "index.theme"),
                                "/tmp/nm-cursor/index.theme")
        wf.connection.run(
            priv + "mkdir -p /usr/share/icons/nodemedic-empty/cursors && "
            + priv + "cp /tmp/nm-cursor/left_ptr "
            "/usr/share/icons/nodemedic-empty/cursors/left_ptr && "
            + priv + "ln -sf left_ptr "
            "/usr/share/icons/nodemedic-empty/cursors/default && "
            + priv + "cp /tmp/nm-cursor/index.theme "
            "/usr/share/icons/nodemedic-empty/index.theme")
    # /dev/i2c-1 for the UPS gauge needs the i2c-dev MODULE as well as the
    # dtparam — the half raspi-config does that the card bake missed.
    wf.connection.run(priv + "modprobe i2c-dev || true")
    wf.connection.run("grep -q '^i2c-dev' /etc/modules || "
                      "echo i2c-dev | " + priv + "tee -a /etc/modules")
    for path, content in (
            ("/etc/systemd/system/reticulum-node-medic.service", unit),
            ("/etc/systemd/system/goodix-rebind.service", rebind),
            ("/etc/udev/rules.d/71-nodemedic-touch-only.rules", touch_rule)):
        heredoc = (f"{priv}tee {path} >/dev/null <<'RNMUNIT'\n"
                   f"{content}\nRNMUNIT")
        if wf.connection.run(heredoc)[0] != 0:
            return StepResult("configure_autostart", False,
                              f"Could not write {path}.")
    wf.connection.run(f"{priv}systemctl daemon-reload")
    code = wf.connection.run(
        f"{priv}systemctl enable reticulum-node-medic.service "
        "goodix-rebind.service")[0]
    return StepResult("configure_autostart", code == 0,
                      "Kiosk autostart + touch retry enabled — the clone "
                      "boots into the tool on its own screen." if code == 0
                      else "Could not enable the autostart units.")


@clone_step
def final_verification(wf: "CloneWorkflow") -> StepResult:
    problems = []
    if wf.connection.run(f"test -f {REMOTE_TOOL_DIR}/main.py")[0] != 0:
        problems.append("tool code missing")
    if wf.connection.run(f"test -f {CLONE_DIR}/registry.json")[0] != 0:
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


@clone_step
def restart_into_tool(wf: "CloneWorkflow") -> StepResult:
    """Reboot the new medic so it actually COMES UP in the tool.

    Without this the clone only ``enable``d the autostart service (starts on the
    NEXT boot) — so after "Clone finished" the new medic sits at a blank console
    and a solo keeper thinks it failed (three-persona walkthrough 2026-08-26).
    A reboot also applies the pending EEPROM boot-order bake. The reboot is
    issued a few seconds in the FUTURE and backgrounded so this ssh call returns
    cleanly before the link drops; we can't verify past our own disconnect, so a
    dropped connection here is SUCCESS, not failure."""
    # sleep-then-reboot, detached, so ssh returns 0 before the box goes down.
    wf.connection.run(
        wf.priv("sh -c 'nohup sh -c \"sleep 3; reboot\" "
                ">/dev/null 2>&1 &'"), timeout=15)
    return StepResult(
        "restart_into_tool", True,
        "Restarting the new medic — in about a minute its own screen opens the "
        "tool's setup. (Its screen may show start-up text until then; that's "
        "normal.)")


def carry_the_time(wf: "CloneWorkflow") -> StepResult:
    """Set the clone's clock from THIS medic's own disciplined clock.

    The clone has no RTC battery, no GPS board yet, and maybe no internet —
    it boots into a bogus date and would stamp garbage on everything this
    ladder writes (the lineage stamp, the roster, its first certificates).
    Medic time is the best time on the bench: GPS-disciplined when outdoors,
    NTP-synced when online. NTP is switched on for the clone too, so it
    self-corrects the moment it ever sees internet. Honest about both
    directions: reports the drift it corrected, and a refusal (no passwordless
    sudo on the target) is a named failure, not a silent skip."""
    import time as _time

    code, out, _err = wf.connection.run("date -u +%s")
    try:
        clone_epoch = float((out or "").strip())
    except ValueError:
        clone_epoch = None
    now = _time.time()
    drift = (now - clone_epoch) if clone_epoch is not None else None

    code, out, err = wf.connection.run(f"sudo -n date -u -s @{int(now)}")
    if code != 0:
        return StepResult("carry_the_time", False,
                          "The new medic wouldn't let this medic set its clock. "
                          "This usually means its card wasn't imaged by THIS "
                          "medic (step 1) — re-image the card and try again. "
                          f"(sudo refused: {(err or out)[-80:]})")
    wf.connection.run("sudo -n timedatectl set-ntp true")

    # local source honesty: say what the carried time was disciplined by
    src = "this medic's clock"
    try:
        import subprocess
        r = subprocess.run(["timedatectl", "show", "-p", "NTPSynchronized"],
                           capture_output=True, text=True, timeout=5)
        if "yes" in (r.stdout or ""):
            src = "this medic's NTP-synced clock"
    except Exception:                                      # noqa: BLE001
        pass
    moved = (f" (corrected {abs(drift):.0f}s of drift)"
             if drift is not None and abs(drift) > 2 else "")
    return StepResult("carry_the_time", True,
                      f"Carried the time from {src}{moved}; NTP enabled on "
                      "the clone for whenever it sees internet.")


# Splice: the clock rides immediately after the target is proven to be a Pi 5 —
# every later step writes timestamps and deserves a sane clock under them.
_CLONE_STEPS.insert(
    [n for n, _f in _CLONE_STEPS].index("verify_target_pi5") + 1,
    ("carry_the_time", carry_the_time))


def carry_touch_cure(wf: "CloneWorkflow") -> StepResult:
    """The medic's ~/.kivy/config.ini rides to the clone — it carries the
    every-tap-twice CURE (probesysfs disabled 2026-07-31 after every touch
    landed doubled; the SDL2 window already delivers touch). A clone left on
    Kivy defaults would regenerate probesysfs and relive that bug on its
    first screen. Same Kivy version both sides, so the file carries as-is."""
    src = os.path.expanduser("~/.kivy/config.ini")
    if not os.path.isfile(src):
        return StepResult("carry_touch_cure", True,
                          "No Kivy config on this medic to carry — the clone "
                          "starts on defaults.", skipped=True)
    wf.connection.run("mkdir -p ~/.kivy")
    ok = wf.connection.push_file(src, "~/.kivy/config.ini")
    # A kiosk has fingers, not a mouse — hide the pointer (it sat in the
    # middle of HAWKEYE's home screen like a lost tourist, 2026-08-25).
    wf.connection.run(
        "sed -i 's/^show_cursor = 1/show_cursor = 0/' ~/.kivy/config.ini")
    return StepResult("carry_touch_cure", ok,
                      "Carried the touch settings (the doubled-tap cure "
                      "rides along)." if ok else
                      "Could not copy the Kivy config to the clone.")


_CLONE_STEPS.insert(
    [n for n, _f in _CLONE_STEPS].index("install_dependencies") + 1,
    ("carry_touch_cure", carry_touch_cure))


def install_display_stack(wf: "CloneWorkflow") -> StepResult:
    """The Lite image cannot open a window: the Kivy wheel's SDL has no
    kmsdrm driver, and desktop libGL doesn't exist. Proven cure (HAWKEYE,
    2026-08-25): the `cage` Wayland kiosk + wlroots stack, ferried as .debs
    over the clone link because the field has no apt. The debs are CARRIED
    in assets/debs (like the wheelhouse); a clone that already has cage
    skips clean."""
    if wf.connection.run("command -v cage")[0] == 0:
        return StepResult("install_display_stack", True,
                          "Display stack already present.", skipped=True)
    debs_local = os.path.join(TOOL_ROOT, "assets", "debs")
    have = (os.path.isdir(debs_local)
            and any(f.endswith(".deb") for f in os.listdir(debs_local)))
    if not have:
        return StepResult(
            "install_display_stack", False,
            "No carried display debs (assets/debs is empty) and the clone "
            "has no cage. On an online medic: run the deb cache refresh, "
            "then retry.")
    wf.connection.run("mkdir -p /tmp/nm-debs")
    ok = wf.connection.push_tree(debs_local, "/tmp/nm-debs")
    if not ok:
        return StepResult("install_display_stack", False,
                          "Could not copy the display debs to the clone.")
    code, out, err = wf.connection.run(
        wf.priv("apt-get install -y --no-install-recommends /tmp/nm-debs/*.deb"),
        timeout=600)
    if code != 0:
        return StepResult("install_display_stack", False,
                          f"Deb install failed: {(err or out)[-160:]}")
    return StepResult("install_display_stack", True,
                      "Installed the display stack (cage kiosk) from "
                      "carried debs — no internet needed.")


_CLONE_STEPS.insert(
    [n for n, _f in _CLONE_STEPS].index("carry_touch_cure") + 1,
    ("install_display_stack", install_display_stack))


#: BOOT_ORDER we bake into every child. Hex nibbles are read RIGHT-TO-LEFT, so
#: 0xf321 means: SD(1) first, then NETWORK(2), then RPIBOOT(3), then RESTART(f)
#: the sequence (loop forever rather than give up).
#:
#: The NETWORK(2) rung is the one that matters, and it is the lesson HAWKEYE
#: taught the hard way (2026-08-25): a medic SEALED IN ITS CASE with a dead SD
#: boot partition has NO button, NO card slot, NO pins reachable — its ONLY
#: remaining door is the ethernet cable. NETWORK boot (mode 2, plain TFTP) lets
#: a sibling medic serve it a rescue OS over that cable, headless, and repair it
#: in place. HAWKEYE was mis-baked to 0x71 (SD then HTTP-boot, mode 7), which is
#: HTTPS-to-Raspberry-Pi + signature-checked and CANNOT be served locally — so a
#: sealed HAWKEYE had no remote door at all. RPIBOOT(3) stays as the
#: button-accessible fallback for a Pi whose case is still open.
#:
#: SECURITY TRADEOFF (accept-or-revert decision, flagged 2026-08-25): NETWORK
#: boot (mode 2) is plain TFTP with NO signature check. It only ever triggers
#: when SD boot has already FAILED, and then the Pi boots whatever a DHCP/TFTP
#: responder offers. In normal use the medic boots from SD and never reaches
#: this rung; the exposure is the narrow case of a dead SD boot partition AND
#: the medic plugged into a hostile/shared LAN (a field medic's real network is
#: its own LoRa mesh + direct cables, not a shared ethernet). The safe
#: alternative that KEEPS everything except remote recoverability is "0xf31"
#: (SD -> RPIBOOT -> loop, no network door). Kept as 0xf321 because sealed-case
#: recoverability is the whole point of the HAWKEYE lesson; revert this one
#: constant to "0xf31" if the operator prefers signature-only boot.
RECOVERY_BOOT_ORDER = "0xf321"


def bake_recovery_bootorder(wf: "CloneWorkflow") -> StepResult:
    """Bake remote-recoverability into the child's boot chip
    (BOOT_ORDER=:data:`RECOVERY_BOOT_ORDER`): try the SD card, then offer
    NETWORK boot so a sibling medic can rescue it over nothing but an ethernet
    cable — even sealed in its case — then RPIBOOT for the case-open path, then
    loop. Factory EEPROMs (0xf461) lack the network rung, which is why a virgin
    Pi's FIRST birth still needs one button ritual; after this bake, that
    machine is recoverable for the rest of its life with no hands on the board.

    A bonus hardening, not a load-bearing rung: on any failure the clone is
    still a complete medic (SD boot is unaffected), so this reports an honest
    skip instead of failing the whole clone."""
    code, out, _e = wf.connection.run(
        wf.priv("rpi-eeprom-config") + " 2>/dev/null")
    if code != 0 or "BOOT_ORDER" not in (out or ""):
        return StepResult("bake_recovery_bootorder", True,
                          "Recovery boot-order not baked (could not read the "
                          "boot chip) — SD boot still works.",
                          skipped=True)
    current = ""
    for line in out.splitlines():
        if line.strip().startswith("BOOT_ORDER="):
            current = line.strip().split("=", 1)[1]
    if current == RECOVERY_BOOT_ORDER:
        return StepResult("bake_recovery_bootorder", True,
                          "Recovery boot-order already baked.", skipped=True)
    script = (
        "set -e; rpi-eeprom-config > /tmp/nm-eeprom.conf; "
        "if grep -q '^BOOT_ORDER=' /tmp/nm-eeprom.conf; then "
        f"sed -i 's/^BOOT_ORDER=.*/BOOT_ORDER={RECOVERY_BOOT_ORDER}/' "
        "/tmp/nm-eeprom.conf; "
        f"else echo 'BOOT_ORDER={RECOVERY_BOOT_ORDER}' >> /tmp/nm-eeprom.conf; "
        "fi; rpi-eeprom-config --apply /tmp/nm-eeprom.conf")
    code, out2, err = wf.connection.run(
        wf.priv(f"sh -c \"{script}\""), timeout=120)
    if code != 0:
        return StepResult("bake_recovery_bootorder", True,
                          "Recovery boot-order not baked "
                          f"({(err or out2)[-100:].strip()}) — SD boot still "
                          "works.", skipped=True)
    return StepResult("bake_recovery_bootorder", True,
                      "Boot chip baked: if this medic's card ever dies, a "
                      "sibling medic can rescue it over the ethernet cable "
                      "alone — no case-opening, no buttons. (Takes effect "
                      "after the reboot.)")


_CLONE_STEPS.insert(
    [n for n, _f in _CLONE_STEPS].index("configure_autostart") + 1,
    ("bake_recovery_bootorder", bake_recovery_bootorder))


class CloneWorkflow:
    def __init__(self, connection: Connection, registry: NodeRegistry):
        self.connection = connection
        self.registry = registry
        self.steps: List[Tuple[str, Callable]] = list(_CLONE_STEPS)
        self.current_index = 0
        self.results: List[StepResult] = []
        self.monitoring_db_json: str = ""
        self.pip_cmd: str = "pip3"
        self.fresh_identity_generated: bool = False
        self.fresh_identity_hash: Optional[str] = None

    def priv(self, cmd: str) -> str:
        """Privileged form of *cmd* on the clone — the imaged 'pi' account
        carries NOPASSWD sudo (written by the card bake)."""
        return f"sudo -n {cmd}"

    def run_all(self, on_progress: Optional[Callable[[StepResult], None]] = None):
        emit = on_progress or (lambda r: None)
        while self.current_index < len(self.steps):
            name, func = self.steps[self.current_index]
            try:
                result = func(self)
            except Exception as e:                     # noqa: BLE001
                # A crashing step used to kill the worker thread silently —
                # no red row, the button stuck on "Cloning..." forever
                # (adversarial review 2026-08-25). A crash is a FAILURE
                # with a name, like any other.
                result = StepResult(name, False,
                                    f"Step crashed: {e!r}")
            self.results.append(result)
            emit(result)
            if not result.success and not result.skipped:
                break
            self.current_index += 1
        return self.results

# --------------------------------------------------------------------------- #
# The REAL entry: discover the fresh medic on the cable/LAN, then clone.
# --------------------------------------------------------------------------- #

class _NotYetConnected:
    """Placeholder connection until find_new_medic swaps in the real one. Any
    accidental use before discovery is a loud bug, not a quiet hang."""

    def run(self, *_a, **_k):
        raise RuntimeError("clone step ran before find_new_medic connected")

    def push_tree(self, *_a, **_k):
        raise RuntimeError("clone step ran before find_new_medic connected")


def make_discovering_workflow(registry: NodeRegistry, hostname: str = "",
                              username: str = "pi") -> "CloneWorkflow":
    """A CloneWorkflow whose FIRST step finds the new medic and connects.

    Discovery order is provisioning.direct_link's: <hostname>.local (mDNS —
    also answers over the household WiFi), then the baked static /29
    (10.55.0.1) over the patch cable. The address that answers gets its host
    key freshly PINNED — a rebirthed/reimaged machine at a reused address is
    the stale-key trap that has burned every flow before this one, so the old
    pin is dropped first and the new machine's key trusted on first contact.
    """
    wf = CloneWorkflow(_NotYetConnected(), registry)

    def find_new_medic(wf: "CloneWorkflow") -> StepResult:
        from provisioning.direct_link import discover_peer
        from provisioning import host_keys
        from transport.connection import SSHConnection
        import subprocess

        target = discover_peer(hostname=hostname, timeout=300)
        if not target:
            name = hostname or "the new medic"
            return StepResult(
                "find_new_medic", False,
                f"Could not find {name} for 5 minutes — tried its name "
                f"({hostname or 'set name'}.local), the stock raspberrypi.local, "
                "every address on the cable, and any board that answered on the "
                "wire (a first boot needs most of that time). Is it powered, and "
                "cabled to this medic or on this WiFi?")
        # Fresh machine, possibly at a reused address: drop any stale pin,
        # then pin THIS machine's key (first-contact trust).
        try:
            for store in (host_keys.PINNED_KNOWN_HOSTS,
                          os.path.expanduser("~/.ssh/known_hosts")):
                subprocess.run(["ssh-keygen", "-R", target, "-f", store],
                               capture_output=True, timeout=10)
            host_keys.pin_host(target)
        except Exception:                                  # noqa: BLE001
            pass                     # transport falls back to accept-new
        conn = SSHConnection(target, user=username)
        code, out, _err = conn.run("echo medic-here && id -un", timeout=20)
        if code != 0 or "medic-here" not in (out or ""):
            return StepResult(
                "find_new_medic", False,
                f"{target} answered on the network but SSH login as "
                f"'{username}' failed — was the card imaged with this medic's "
                "key (the Clone step, step 1)?")
        wf.connection = conn
        wf.target_address = target
        return StepResult("find_new_medic", True,
                          f"Found the new medic at {target} and logged in.")

    wf.steps.insert(0, ("find_new_medic", find_new_medic))
    return wf

