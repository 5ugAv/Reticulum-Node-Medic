"""MITOSIS — replicate the medic onto a fresh Pi 5 (mode 6, formerly Clone Tool).

A working medic is the tool code + its carried asset store + the offline RNode
firmware cache + its Python environment + a place on the mesh. Cloning images all
of that onto a fresh Pi 5 over SSH, then gives the new unit a **fresh** Reticulum
identity — the source identity is deliberately never copied, so the two medics
are distinct nodes. Installs an autostart service so the clone boots straight
into the tool, and ENDS by locking it the way its parent is locked (scoped
sudo, key-only SSH, the SSH firewall — provisioning/security/), removing this
medic's own key last when the clone goes to a new community.

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
TOOL_EXCLUDES = (".git", "__pycache__", "*.pyc", ".pytest_cache", "*.egg-info",
                 # a developer's scratch is not the tool (readiness ledger #127)
                 ".claude", ".local-backups", "docs/previews", "*.log", "typescript",
                 ".kivy", ".DS_Store", ".venv", "venv", "htmlcov", ".coverage",
                 "build", "dist")
#: Where the clone keeps its copied monitoring DB.
CLONE_DIR = "~/.reticulum-node-medic"
#: The tool's Python stack is pinned in this manifest; wheels for it live in the
#: wheelhouse (populated by workflows.wheelhouse) and travel with the tool tree.
REMOTE_REQUIREMENTS = f"{REMOTE_TOOL_DIR}/assets/requirements.txt"
REMOTE_WHEELS = f"{REMOTE_TOOL_DIR}/assets/packages"

#: A clone takes this medic's WHOLE home folder, minus what is named below.
#:
#: The default is inverted on purpose (keeper, 2026-10-09: "We shouldn't give
#: the medic an option to miss anything"). The old rule was a hand-kept list of
#: what to carry, so everything new started out missing: the EoRa-S3 tree and
#: the boundary walks were both left behind because nobody had listed them.
#: Now a new folder, toolchain or firmware tree travels by itself; only what is
#: named here, with its reason, stays behind.
#:
#: What never travels falls into a few fixed kinds, so this list does not grow
#: with the tool: this medic's own identity and keys, what a step of its own
#: carries, personal and desktop files, and clutter. Work files belong in
#: ~/scratch and things kept for this medic alone in ~/this-medic; neither
#: ever travels, so a clone of a clone is no bigger than its parent.
HOME_NEVER = {
    # identity, keys and trust: one per unit, never copied
    ".ssh": "this medic's own keys; the clone makes its own",
    ".gnupg": "this medic's own signing keys",
    ".gitconfig": "the keeper's own git identity",
    ".git-credentials": "saved logins",
    ".netrc": "saved logins",
    ".reticulum": "this medic's Reticulum identity; the clone makes a fresh one",
    ".lxmd": "this medic's own messaging node and its store",
    ".nodemedic-vault": "this medic's encrypted records",
    ".nodemedic-vault.img": "this medic's encrypted records",
    ".rnm-health": "a node's own health state, never a medic's",
    "gps_state.json": "this medic's own last position fix",
    ".sudo-scope-confirmed": "this medic's own lock-down record",
    ".nodemedic-sudo-confirmed": "this medic's own lock-down record",
    ".nodemedic-ssh-confirmed": "this medic's own lock-down record",
    ".sudo_as_admin_successful": "a marker of this machine's admin use",
    # carried by a step of their own
    "reticulum-tool": "the tool itself: carried by transfer_tool",
    ".reticulum-node-medic": "the records: carried by their own rule (RECORDS_NEVER)",
    ".config": "this machine's program settings; the RNode firmware cache "
               "inside it is carried by transfer_firmware_cache",
    # personal and desktop
    "Desktop": "personal files", "Documents": "personal files",
    "Downloads": "personal files", "Music": "personal files",
    "Pictures": "personal files", "Public": "personal files",
    "Templates": "personal files", "Videos": "personal files",
    ".bash_history": "this machine's command history",
    ".python_history": "this machine's command history",
    ".lesshst": "this machine's command history",
    ".viminfo": "this machine's command history",
    ".wget-hsts": "this machine's download history",
    ".Xauthority": "a desktop session key", ".ICEauthority": "a desktop session key",
    ".xsession-errors": "a desktop log", ".xsession-errors.old": "a desktop log",
    ".dbus": "desktop session state", ".pki": "a browser's certificates",
    ".mozilla": "a browser profile",
    # work files and clutter
    "scratch": "work files: disposable by design",
    "this-medic": "kept for this medic alone",
    "reticulum-tool-check": "the deploy sandbox, a scratch copy of the tool",
    ".cache": "caches, rebuilt on demand",
    ".venv": "test environments", ".venvs": "test environments",
    ".venv-test": "test environments",
}

#: Development leftovers named before Node Medic 1's home was tidied into
#: ~/scratch, so a parent that was never tidied cannot pass them on.
CARRY_SKIP = ("imgwork", "techo-test", "tracker_build", "supreme_build",
              "upstream_pr", "pr115_alt", "dev_pristine", "pr126_dev",
              "upstream_baseline")
for _name in CARRY_SKIP:
    HOME_NEVER.setdefault(_name, "development leftovers")

#: Clutter that never travels from inside anything that does, at any depth:
#: caches, compiled Python, logs and backups (rsync --exclude patterns).
CLUTTER = ("__pycache__", "*.pyc", "*.log", "*.out", "*.pid", "*.bak", "*.bak-*",
           "*.bak.*", "*.old", ".cache")
#: Clutter at one known place inside a folder that travels, anchored to that
#: folder's top so a same-named folder deeper down is untouched.
CLUTTER_IN = {
    # download archives arduino-cli keeps after unpacking them: 1 GB on Node
    # Medic 1, carried by every clone until 2026-10-09
    ".arduino15": ("/staging",),
    ".local": ("/share", "/state"),       # desktop data and histories
    ".kivy": ("/logs",),
}
#: Loose files in the home folder that a clone for a NEW community still gets.
#: Anything else lying loose there may be the keeper's own and stays in the
#: fleet; folders travel either way.
TOOL_FILES = ("pi_os_lite.img.xz",)
#: Without the OS image a medic cannot make the next card.
REQUIRED = ("pi_os_lite.img.xz",)


def _home_entry_skips(name: str) -> tuple:
    """The rsync excludes for one folder of the home that travels. Folders
    that are not hidden (the firmware trees, the Arduino libraries, anything
    added later) go without their git history: unpublished commits keep the
    address they were made under, and two on Node Medic 1 carried a personal
    e-mail. No firmware build reads it. Hidden toolchain folders keep theirs:
    a platform installed from source may need it."""
    skips = CLUTTER + CLUTTER_IN.get(name, ())
    return skips if name.startswith(".") else skips + (".git",)


def _is_clutter_file(name: str) -> bool:
    import fnmatch
    return any(fnmatch.fnmatch(name, pat) for pat in CLUTTER if "*" in pat)


_CLONE_STEPS: List[Tuple[str, Callable]] = []


def clone_step(func: Callable) -> Callable:
    _CLONE_STEPS.append((func.__name__, func))
    return func


@clone_step
def verify_target_pi5(wf: "CloneWorkflow") -> StepResult:
    cpuinfo = wf.connection.run("cat /proc/cpuinfo")[1]
    if "Raspberry Pi 5" not in cpuinfo:
        return StepResult("verify_target_pi5", False,
                          "The machine on the cable is not a Raspberry Pi 5. Node Medic only makes medics on a Raspberry Pi 5.")
    return StepResult("verify_target_pi5", True, "Target Pi 5 confirmed.")


def _freeze_parent() -> str:
    """Freeze this medic's packages for the clone; a sentence for the step."""
    try:
        from workflows.parent_freeze import freeze
        from workflows.wheelhouse import REQUIREMENTS, WHEELHOUSE
        f_ok, f_msg = freeze(WHEELHOUSE, REQUIREMENTS)
        print("[clone] parent freeze:", f_ok, f_msg, flush=True)
        return (" Its software goes across exactly as this medic runs it."
                if f_ok else " (This medic's own package versions could not all "
                "be carried; the clone gets the standard set.)")
    except Exception as exc:                                     # noqa: BLE001
        print("[clone] parent freeze failed:", exc, flush=True)
        return ""


def _stamp_release(wf: "CloneWorkflow") -> None:
    """A clone carries no git history (TOOL_EXCLUDES drops .git), so its
    About screen read "unknown". Hand it this medic's version as a stamp that
    provisioning/about.py reads when git has no answer (parity sweep,
    2026-10-08). A parent that is itself a clone passes its own stamp on."""
    from provisioning import about
    stamp = {"hash": about.git_hash(), "branch": about.git_branch(),
             "remote": about.repo_link()}
    if not stamp["hash"]:
        return
    payload = json.dumps(stamp, sort_keys=True)
    wf.connection.run(f"cat > {REMOTE_TOOL_DIR}/{about.RELEASE_STAMP_NAME} "
                      f"<<'RNMEOF'\n{payload}\nRNMEOF")


@clone_step
def transfer_tool(wf: "CloneWorkflow") -> StepResult:
    # rsync the whole tool tree (code + carried assets: configs, scripts,
    # sketches, packages, maps), minus history/caches.
    # FIRST, freeze this medic's Python packages exactly as it runs them, so
    # the wheelhouse that travels holds the parent's versions (patches
    # included) and the clone installs those — not the latest downloads.
    # Node Medic 2 got a stock rns that did not know the Tracker (2026-10-06).
    frozen = _freeze_parent()
    wf.connection.run(f"mkdir -p {REMOTE_TOOL_DIR}")
    ok = wf.connection.push_tree(TOOL_ROOT, REMOTE_TOOL_DIR, exclude=TOOL_EXCLUDES)
    if not ok:
        return StepResult("transfer_tool", False,
                          "Couldn't copy Node Medic across. Check the network "
                          "cable at both ends, or try another cable, then press "
                          "Retry.")
    present = wf.connection.run(f"test -f {REMOTE_TOOL_DIR}/main.py")[0] == 0
    if present:
        _stamp_release(wf)
    return StepResult("transfer_tool", present,
                      f"Copied the tool code + asset store.{frozen}" if present
                      else "Copying Node Medic did not finish. Press Retry — it starts again from this step.")


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
    """Copy this medic's home folder to the new medic, whole: the toolchains,
    the firmware trees, the OS image and anything added since, minus
    HOME_NEVER and clutter. Nothing has to be listed to travel.

    This is the step that makes replication transitive: after it, the new
    medic can build firmware, flash a board, image a card and clone again,
    with no internet and without this medic. Several gigabytes, so it is the
    slowest step by a wide margin.
    """
    import os
    home = os.path.expanduser("~")
    fresh = bool(getattr(wf, "fresh_fleet", False))
    missing = [n for n in REQUIRED if not os.path.exists(os.path.join(home, n))]
    if missing:
        return StepResult(
            "carry_the_toolchain", False,
            "This medic has no Pi OS image (" + ", ".join(missing) + "), so the "
            "new medic could not make the next card. Put the image back on this "
            "medic, then press Retry.")
    sent, failed = [], []
    for name in sorted(os.listdir(home)):
        if name in HOME_NEVER:
            continue
        local = os.path.join(home, name)
        if os.path.islink(local):
            continue
        remote = f"~/{name}"
        if os.path.isdir(local):
            wf.connection.run(f"mkdir -p {remote}")
            ok = wf.connection.push_tree(local, remote, exclude=_home_entry_skips(name))
        elif os.path.isfile(local):
            if _is_clutter_file(name) or (fresh and name not in TOOL_FILES):
                continue
            ok = wf.connection.push_file(local, remote) if hasattr(
                wf.connection, "push_file") else wf.connection.push_tree(home, "~")
        else:
            continue
        (sent if ok else failed).append(name)
    if failed:
        return StepResult(
            "carry_the_toolchain", False,
            "The new medic did not get everything from this medic: "
            + ", ".join(failed) + ". Check the network cable at both ends, or "
            "try another cable, then press Retry.")
    return StepResult(
        "carry_the_toolchain", True,
        f"Carried this medic's home folder ({len(sent)} items: toolchains, "
        "firmware, the OS image and everything added since), minus its own "
        "identity, personal files and clutter.")


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
    # THE PARENT'S OWN VERSIONS, when it froze them (workflows.parent_freeze):
    # a clone must run what its parent runs — rns 1.3.8 from the downloads
    # could not name the Tracker that the parent's patched 1.3.7 could
    # (Node Medic 2, 2026-10-06)
    from workflows.parent_freeze import PARENT_REQUIREMENTS
    parent_pins = f"{REMOTE_WHEELS}/{PARENT_REQUIREMENTS}"
    pins = parent_pins if wf.connection.run(f"test -s {parent_pins}")[0] == 0 else REMOTE_REQUIREMENTS
    if have_wheels:
        cmd = (f"{pip} install --no-index --find-links {REMOTE_WHEELS} "
               f"--break-system-packages --user -r {pins}")
        source = (f"carried wheelhouse (offline, the parent's own versions){pip_note}"
                  if pins == parent_pins else f"carried wheelhouse (offline){pip_note}")
    elif wf.connection.run("curl -fsI -m 5 https://pypi.org")[0] == 0:
        cmd = (f"{pip} install --break-system-packages --user "
               f"-r {REMOTE_REQUIREMENTS}")
        source = f"online pip{pip_note}"
    else:
        return StepResult(
            "install_dependencies", False,
            "The new medic needs its software, but this medic has no carried "
            "copy and no internet to fetch it. Put THIS medic online once "
            "(Settings \u25b8 Field readiness \u25b8 Prepare for the field), "
            "then start the clone again — after that, clones work offline.")
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


def _carried_debs():
    """``(baseline files, packages added since imaging, their files, packages
    still not covered)`` from THIS medic's store and dpkg log. One seam, so
    the tests can hand the step a store of their own."""
    from workflows.wheelhouse import (APT_PACKAGES, DEB_CACHE, added_packages,
                                      debs_for, ensure_added_plan)
    picked = debs_for(APT_PACKAGES, DEB_CACHE) or []
    added = added_packages()
    extra, short = ensure_added_plan(added, DEB_CACHE)
    return picked, added, extra, short


@clone_step
def install_carried_packages(wf: "CloneWorkflow") -> StepResult:
    """Install, with no internet, every package this medic carries: the
    baseline (Dire Wolf, ALSA, gpsd, uhubctl, the programs the code runs) and
    everything this medic installed after it was imaged.

    Nothing here is optional any more (keeper, 2026-10-09: "We shouldn't give
    the medic an option to miss anything"). This step used to call a missing
    package optional and report success, which is how a clone could say
    "every step verified" while missing tools. Now it installs everything or
    stops and names what is missing, with what to do.
    """
    from workflows.wheelhouse import APT_PACKAGES, offline_install_command
    name = "install_carried_packages"
    picked, added, extra, short = _carried_debs()
    if not picked:
        return StepResult(name, False,
                          "This medic's package store is empty, so the new medic "
                          "would miss packages it needs. Connect this medic to the "
                          "internet once and run Settings > Field readiness, then "
                          "press Retry.")
    if short:
        return StepResult(name, False,
                          "This medic has packages it cannot hand on yet: "
                          + ", ".join(short[:8]) + ("..." if len(short) > 8 else "")
                          + ". Connect this medic to the internet once, then press "
                          "Retry; it fetches them and carries on.")
    cache = "/tmp/nm-radio-debs"
    files = sorted(set(picked) | set(extra))
    wf.connection.run(f"rm -rf {cache} && mkdir -p {cache}")
    for f in files:
        wf.connection.push_file(f, f"{cache}/")
    wf.connection.run(wf.priv(offline_install_command(cache)), timeout=900)
    # Ask dpkg, not PATH: gpsd lives in /usr/sbin, which a normal user's PATH
    # does not include — the Wi-Fi-off proof clone called a perfect offline
    # install a failure that way (2026-10-06).
    def _installed(pkg):
        code, out, _e = wf.connection.run(
            f"dpkg-query -W -f='${{Status}}' {pkg} 2>/dev/null")
        return code == 0 and "install ok installed" in (out or "")
    missing = [p for p in list(APT_PACKAGES) + list(added) if not _installed(p)]
    if missing:
        return StepResult(name, False,
                          "These packages did not install on the new medic: "
                          + ", ".join(missing[:8]) + ("..." if len(missing) > 8 else "")
                          + ". Press Retry.")
    return StepResult(name, True,
                      f"Installed every package this medic carries ({len(files)} "
                      "files, offline), including "
                      + (f"{len(added)} it added since it was imaged."
                         if added else "Dire Wolf for radio work."))




#: Settings that travel to EVERY clone: the band the fleet is on (a clone
#: on the standard 915.125 MHz would be deaf to an 868 MHz fleet and births
#: nodes into a different mesh) and the keeper's language.
SETTINGS_ALWAYS = ("radio_defaults.json", "language",
                   # what this medic installed after imaging: tool state, so it
                   # reaches a new community's clone too (see wheelhouse)
                   "packages_added.txt")
#: The records folder follows the home folder's rule. A clone that stays in
#: this medic's fleet takes ALL of it, except RECORDS_NEVER (this medic's own
#: identity, trust, setup state and messages) and what a step of its own
#: carries (RECORDS_OWN_STEP). A new kind of record therefore travels with the
#: fleet without anyone listing it: the boundary walks did not, because the
#: old list never named them (keeper, 2026-10-09). A clone for a NEW community
#: takes only SETTINGS_ALWAYS: no fleet record reaches another community.

#: Records the clone carries through a step of their own.
RECORDS_OWN_STEP = {"registry.json": "copy_monitoring_db",
                    "kin.json": "copy_kin_roster",
                    "maps": "copy_offline_maps"}

#: Records that never leave this medic, and why. tests/test_clone_parity.py
#: fails when the code writes a record this file does not classify, so a new
#: kind of record is never left behind by accident again.
RECORDS_NEVER = {
    # spelled in halves: the chip-MAC guard test refuses the whole name
    # anywhere in the clone's code, so it can never slip into a carry list
    "board_" + "memory.json": "the board picker's memory holds boards' chip MACs",
    "board_" + "traits.json": "the board picker's memory holds boards' chip MACs",
    "trust.json": "trust is per unit and never transitive",
    "trust_hmac_key": "trust is per unit and never transitive",
    "location_salt": "this medic's own secret for fuzzing positions",
    "tool_identity.json": "this medic's identity; the clone is stamped with its lineage instead",
    "health_ping_identity": "this medic's own Reticulum identity",
    "health_reply_identity": "this medic's own Reticulum identity",
    "pi_health_identity": "this medic's own Reticulum identity",
    "lxmf_identity": "this medic's own messaging identity",
    "lxmf": "this medic's own messaging store",
    "chat": "the keeper's personal messages",
    "onboard.json": "this medic's own setup state",
    "first_use.json": "this medic's own setup state",
    "vault_policy.json": "this medic's own choice to encrypt its records",
    "node_mode": "this medic's own role on the mesh",
    "last_imaged_pi.json": "this medic's own imaging history",
    "health_ping_state.json": "this medic's own pings, rebuilt by pinging",
    "time_ledger.json": "the time pushes this medic itself sent",
    "apps_last_sync": "this medic's own download stamp",
    "construction.log": "this medic's own log",
    "ui_busy": "a marker the running app keeps fresh",
}


def _carry_settings(wf: "CloneWorkflow") -> str:
    """Copy this medic's records to the clone (see the rule above); a short
    note for the step, "" when there was nothing to copy."""
    local_dir = os.path.expanduser(CLONE_DIR)
    if not os.path.isdir(local_dir):
        return ""
    wf.connection.run(f"mkdir -p {CLONE_DIR}")
    if getattr(wf, "fresh_fleet", False):
        carried = 0
        for name in SETTINGS_ALWAYS:
            src = os.path.join(local_dir, name)
            if os.path.isfile(src):
                try:
                    carried += 1 if wf.connection.push_file(src, f"{CLONE_DIR}/{name}") else 0
                except Exception as exc:                         # noqa: BLE001
                    print("[clone] setting not carried:", name, exc, flush=True)
        return f" Carried {carried} settings." if carried else ""
    skips = tuple("/" + n for n in list(RECORDS_NEVER) + list(RECORDS_OWN_STEP)) + CLUTTER
    try:
        ok = wf.connection.push_tree(local_dir, CLONE_DIR, exclude=skips)
    except Exception as exc:                                     # noqa: BLE001
        print("[clone] records not carried:", exc, flush=True)
        ok = False
    return (" Carried this medic's records about the fleet." if ok else
            " The fleet's records did not copy; press Retry.")


@clone_step
def copy_monitoring_db(wf: "CloneWorkflow") -> StepResult:
    """The registry, to the filename the app actually LOADS (registry.json
    — the old monitoring_db.json was a green-ticked no-op the app never
    read), moved by scp rather than a shell heredoc (a fleet-scale registry
    overflows the kernel's single-argv ceiling and killed the whole thread
    — both found by adversarial review, 2026-08-25)."""
    settings_note = _carry_settings(wf)
    if getattr(wf, "fresh_fleet", False):
        # an EMPTY registry, not none: final_verification and the app both
        # look for the file (a fresh clone failed verification forever)
        wf.connection.run(f"mkdir -p {CLONE_DIR} && "
                          f"[ -f {CLONE_DIR}/registry.json ] || "
                          f"echo '{{}}' > {CLONE_DIR}/registry.json")
        return StepResult("copy_monitoring_db", True,
                          f"Fresh fleet — the new medic starts with no nodes.{settings_note}",
                          skipped=True)
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
                      f"nodes).{settings_note}" if ok else
                      "Copying the records of your radios did not finish. Press Retry.")


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
    if ok and copied:
        # the files must not say where they came from: metadata names and
        # centres go to the coarse grid; a clone for someone else also loses
        # the node-detail zooms (the patches around THIS keeper's nodes)
        drop = bool(getattr(wf, "fresh_fleet", False))
        wf.connection.run(
            f"cd {REMOTE_TOOL_DIR} && python3 -c \"import glob,os\nfrom ui.map_download import sanitise_carried_maps\n"
            f"[sanitise_carried_maps(p, drop_detail={drop}) for p in glob.glob(os.path.expanduser('{CLONE_DIR}/maps/*.mbtiles'))]\"",
            timeout=900)
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
    if getattr(wf, "fresh_fleet", False):
        return StepResult("copy_kin_roster", True,
                          "Fresh fleet — the keeper chose to start the new medic "
                          "without the fleet roster.", skipped=True)
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
                      # NOT "{err or out}": neither name exists in this
                      # function, so the failure branch raised NameError
                      # instead of reporting the failure (found by the
                      # undefined-name guard, 2026-09-09). push_file returns a
                      # bool and nothing else, so the honest message says only
                      # what is known.
                      else "Could not write the kin roster to the clone.")


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
        # read the kept identity back, so lineage and trust still record it
        code, out, _e = wf.connection.run(
            "python3 -c \"import RNS,sys; i=RNS.Identity.from_file(sys.argv[1]); "
            "print(i.hash.hex() if i else '')\" "
            f"{CLONE_DIR}/identity 2>/dev/null", timeout=60)
        kept = (out or "").strip().split()[-1] if (out or "").strip() else ""
        if code == 0 and len(kept) == 32:
            wf.fresh_identity_hash = kept
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
    # The other direction, at the board level: boards THIS medic has named
    # carry its signature; the clone validates them only if it trusts this
    # medic's public signing key. Write the file rnodeconf --trust-key would.
    trusted = ""
    try:
        from workflows.signing_key import ensure_signing_key, public_key_file
        from transport.connection import LocalConnection
        ensure_signing_key(LocalConnection())        # a parent that never named a board
        pk = public_key_file()
        if pk:
            name, data = pk
            local = f"/tmp/nm-trust-{name}"
            with open(local, "wb") as fh:
                fh.write(data)
            if wf.connection.push_file(local, f"/tmp/{name}"):
                wf.connection.run("mkdir -p ~/.config/rnodeconf/trusted_keys && "
                                  f"mv /tmp/{name} ~/.config/rnodeconf/trusted_keys/{name}")
                trusted = " It trusts the boards this medic has named."
        if not getattr(wf, "fresh_fleet", False):
            # and the keys THIS medic trusts (its own parent's), so the fleet
            # it inherits verifies on the grandchild too
            tk = os.path.expanduser("~/.config/rnodeconf/trusted_keys")
            if os.path.isdir(tk) and os.listdir(tk):
                wf.connection.push_tree(tk, "~/.config/rnodeconf/trusted_keys")
            try:
                os.remove(local)
            except OSError:
                pass
    except Exception as exc:                                     # noqa: BLE001
        print("[clone] trusted key not handed over:", exc, flush=True)
    child = getattr(wf, "fresh_identity_hash", None)
    if not child:
        return StepResult("record_child_trust", True,
                          f"No child identity hash captured — skipped.{trusted}", skipped=True)
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
                      f"Recorded clone {child[:8]} as a trusted child unit.{trusted}")


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
    # the cable-birth cure travels too: without this drop-in NetworkManager
    # runs DHCP on usb0 and wedges the clone's first cable birth of a Pi node
    # (the parent has it by hand; Self Diagnose could only say "ask whoever
    # set up this medic")
    try:
        usb0 = os.path.join(TOOL_ROOT, "scripts", "nodemedic-usb0-unmanaged.conf")
        if os.path.isfile(usb0) and wf.connection.push_file(usb0, "/tmp/nm-usb0.conf"):
            wf.connection.run("sudo -n install -m 644 /tmp/nm-usb0.conf "
                              "/etc/NetworkManager/conf.d/99-nodemedic-usb0.conf")
    except Exception as exc:                                     # noqa: BLE001
        print("[clone] usb0 drop-in not carried:", exc, flush=True)
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
        # THE SAME START AS THE PARENT: scripts/start_ui.sh carries the touch
        # switches (no synthesised mouse → no pointer arrow), ~/.local/bin for
        # the flashing tools, and the log (first real clone, 2026-10-06).
        f"ExecStart=/usr/bin/cage -s -- /bin/bash "
        f"{home}/reticulum-tool/scripts/start_ui.sh\n"
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
        # CAGE IGNORES XCURSOR_THEME: it loads the theme named "default", so
        # the arrow stayed on the first real clone (2026-10-06; noted on
        # HAWKEYE in August). The kiosk user's own "default" theme is empty.
        wf.connection.run(
            "mkdir -p ~/.icons/default/cursors && "
            "cp /tmp/nm-cursor/left_ptr ~/.icons/default/cursors/left_ptr && "
            "for n in default arrow top_left_arrow pointer; do "
            "ln -sf left_ptr ~/.icons/default/cursors/$n; done && "
            "printf '[Icon Theme]\\nName=default\\n' > ~/.icons/default/index.theme")
    # /dev/i2c-1 for the UPS gauge needs the i2c-dev MODULE as well as the
    # dtparam — the half raspi-config does that the card bake missed.
    wf.connection.run(priv + "modprobe i2c-dev || true")
    wf.connection.run("grep -q '^i2c-dev' /etc/modules || "
                      "echo i2c-dev | " + priv + "tee -a /etc/modules")
    # the world map keeps filling in whenever the new medic is online, as it
    # does on Node Medic 1 (parity, 2026-10-09)
    from workflows.medic_setup import render_world_map_unit
    with open(os.path.join(TOOL_ROOT, "scripts", "world-map-fill.service"),
              encoding="utf-8") as fh:
        world_map = render_world_map_unit(fh.read(), user, home)
    for path, content in (
            ("/etc/systemd/system/reticulum-node-medic.service", unit),
            ("/etc/systemd/system/goodix-rebind.service", rebind),
            ("/etc/systemd/system/world-map-fill.service", world_map),
            ("/etc/udev/rules.d/71-nodemedic-touch-only.rules", touch_rule)):
        heredoc = (f"{priv}tee {path} >/dev/null <<'RNMUNIT'\n"
                   f"{content}\nRNMUNIT")
        if wf.connection.run(heredoc)[0] != 0:
            return StepResult("configure_autostart", False,
                              f"Could not write {path}.")
    wf.connection.run(f"{priv}systemctl daemon-reload")
    code = wf.connection.run(
        f"{priv}systemctl enable reticulum-node-medic.service "
        "goodix-rebind.service world-map-fill.service")[0]
    return StepResult("configure_autostart", code == 0,
                      "Kiosk autostart + touch retry enabled — the clone "
                      "boots into the tool on its own screen." if code == 0
                      else "Starting Node Medic at power-on did not finish. Press Retry.")


@clone_step
def install_card_helper(wf: "CloneWorkflow") -> StepResult:
    """The root card-writing helper. Without it the new medic cannot image
    a card — so cannot clone itself, cannot birth a Pi node. This medic had
    it only because the operator installed it by hand; a clone never did
    (readiness sweep, 2026-10-03). The imaged user carries NOPASSWD sudo
    (card bake), so an install is one privileged copy, read back."""
    from provisioning.pi_imager import PREPARE_CARD, PREPARE_CARD_SOURCE
    if not os.path.isfile(PREPARE_CARD_SOURCE):
        return StepResult("install_card_helper", False,
                          "The tool's own prepare_card.py is missing — the card "
                          "helper could not be installed on the clone.")
    if not wf.connection.push_file(PREPARE_CARD_SOURCE, "/tmp/nm-prepare-card"):
        return StepResult("install_card_helper", False,
                          "Could not copy the card helper to the clone.")
    wf.connection.run(wf.priv(f"install -D -m 755 -o root -g root "
                              f"/tmp/nm-prepare-card {PREPARE_CARD}"))
    if wf.connection.run(f"test -x {PREPARE_CARD}")[0] != 0:
        return StepResult("install_card_helper", False,
                          f"The card helper did not land at {PREPARE_CARD}.")
    return StepResult("install_card_helper", True,
                      "Card-writing helper installed and read back.")


@clone_step
def install_radio_helper(wf: "CloneWorkflow") -> StepResult:
    """The root helper the new medic's OWN radio set-up needs. That set-up
    (workflows/medic_radio.py, its first job on its own screen) runs AFTER
    the last steps here have scoped its sudo, and the units it writes can
    only be written by fixed text in a root-owned program — a unit the app
    composed could carry any User= and ExecStart= (2026-10-08). Installed
    now, while the new medic still has its card's full sudo; read back."""
    from workflows.medic_radio import RADIO_HELPER, RADIO_HELPER_SOURCE
    if not os.path.isfile(RADIO_HELPER_SOURCE):
        return StepResult("install_radio_helper", False,
                          "The tool's own radio_units.py is missing — the new "
                          "medic could not be given its radio set-up helper.")
    if not wf.connection.push_file(RADIO_HELPER_SOURCE, "/tmp/nm-radio-units"):
        return StepResult("install_radio_helper", False,
                          "Could not copy the radio set-up helper to the new "
                          "medic. Check the cable, then press Retry.")
    wf.connection.run(wf.priv(f"install -D -m 755 -o root -g root "
                              f"/tmp/nm-radio-units {RADIO_HELPER}"))
    if wf.connection.run(f"test -x {RADIO_HELPER}")[0] != 0:
        return StepResult("install_radio_helper", False,
                          "The radio set-up helper did not land on the new "
                          "medic. Press Retry.")
    return StepResult("install_radio_helper", True,
                      "Radio set-up helper installed and read back.")


@clone_step
def ensure_ssh_keypair(wf: "CloneWorkflow") -> StepResult:
    """Pi births reach the new node over SSH with the medic's own key. A
    fresh clone has none, so its first Pi birth would fail at the first
    hop (readiness sweep, 2026-10-03). Made once, kept."""
    if wf.connection.run("test -f ~/.ssh/id_ed25519")[0] == 0:
        return StepResult("ensure_ssh_keypair", True,
                          "SSH key already present.", skipped=True)
    wf.connection.run("mkdir -p ~/.ssh")
    wf.connection.run("chmod 700 ~/.ssh")
    wf.connection.run("ssh-keygen -q -t ed25519 -N '' -f ~/.ssh/id_ed25519 -C nodemedic")
    if wf.connection.run("test -f ~/.ssh/id_ed25519.pub")[0] != 0:
        return StepResult("ensure_ssh_keypair", False,
                          "Giving it its own door key did not finish. Press Retry.")
    return StepResult("ensure_ssh_keypair", True, "SSH key created for the clone.")


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
    # What a medic needs to clone ITSELF and to birth a Pi (ledger #116):
    # the two steps above land these; the verification now says if not.
    from provisioning.pi_imager import PREPARE_CARD
    if wf.connection.run(f"test -x {PREPARE_CARD}")[0] != 0:
        problems.append("card-writing helper missing")
    # ...and to set up its own radio once its sudo is scoped (2026-10-08)
    from workflows.medic_radio import RADIO_HELPER
    if wf.connection.run(f"test -x {RADIO_HELPER}")[0] != 0:
        problems.append("radio set-up helper missing")
    if wf.connection.run("test -f ~/.ssh/id_ed25519.pub")[0] != 0:
        problems.append("SSH keypair missing")
    # THE APP MUST BE ABLE TO OPEN ITS WINDOW. "Every step verified" sat over a
    # clone that crash-looped on a missing libGL (first real clone, 2026-10-06).
    from workflows.wheelhouse import DISPLAY_PACKAGES
    if wf.connection.run("dpkg -s " + " ".join(DISPLAY_PACKAGES)
                         + " >/dev/null 2>&1")[0] != 0:
        problems.append("screen packages incomplete")
    # full path: ldconfig lives in /sbin, not on a normal user's PATH
    if wf.connection.run("/sbin/ldconfig -p | grep -q 'libGL.so.1 '")[0] != 0:
        problems.append("graphics library (libGL) missing")
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
    _c, boot, _e = wf.connection.run("cat /proc/sys/kernel/random/boot_id")
    wf.boot_id_before = (boot or "").strip()
    wf.connection.run(
        wf.priv("sh -c 'nohup sh -c \"sleep 3; reboot\" "
                ">/dev/null 2>&1 &'"), timeout=15)
    return StepResult(
        "restart_into_tool", True,
        "Restarting the new medic — in about a minute its own screen opens the "
        "tool's setup. (Its screen may show start-up text until then; that's "
        "normal.)")


@clone_step
def confirm_tool_running(wf: "CloneWorkflow") -> StepResult:
    """After the restart, the new medic's app is running and STAYS running.

    The first real clone said "every step verified" while its app crash-looped
    every five seconds (2026-10-06). This waits for the new medic to come back
    (up to ~3 minutes), then watches the service for 20 seconds: active, and
    not restarting."""
    import time as _time
    _sleep = getattr(wf, "sleep", _time.sleep)          # tests pass a no-op
    deadline = _time.time() + 180
    before = getattr(wf, "boot_id_before", "")
    _sleep(10)                                   # the reboot is 3 s away
    while _time.time() < deadline:
        code, boot, _e = wf.connection.run("cat /proc/sys/kernel/random/boot_id",
                                           timeout=10)
        # back, AND a different boot: answering before the reboot is not "back"
        if code == 0 and (not before or (boot or "").strip() != before):
            break
        _sleep(6)
    else:
        return StepResult("confirm_tool_running", False,
                          "The new medic did not come back after its restart. Check "
                          "its power and screen; the copy itself is complete.")
    _sleep(25)
    first = wf.connection.run("systemctl show -p NRestarts --value reticulum-node-medic")
    _sleep(20)
    code, state, _e = wf.connection.run("systemctl is-active reticulum-node-medic")
    again = wf.connection.run("systemctl show -p NRestarts --value reticulum-node-medic")
    a, b = (first[1] or "").strip(), (again[1] or "").strip()
    restarting = bool(a and b and a != b)        # empty = not up yet, not a count
    if (state or "").strip() == "active" and not restarting:
        return StepResult("confirm_tool_running", True,
                          "The new medic is running Node Medic on its own screen.")
    _c, tail, _e = wf.connection.run(
        "grep -aE 'CRITICAL|Error|rror:' ~/ui.log | tail -2")
    return StepResult("confirm_tool_running", False,
                      "Node Medic is not staying open on the new medic. Look at "
                      "its screen, then press Retry. ("
                      + " ".join((tail or "").split())[-160:] + ")")


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
                      "Setting up the touchscreen did not finish. Press Retry.")


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
    # THE ONE DEB CACHE (workflows.wheelhouse.DEB_CACHE, refreshed by
    # scripts/refresh_deb_cache.py on an online medic), with the old
    # gitignored assets/debs accepted if a medic still has it filled.
    from workflows.wheelhouse import DEB_CACHE
    debs_local = os.path.expanduser(DEB_CACHE)
    legacy = os.path.join(TOOL_ROOT, "assets", "debs")
    def _has_debs(d):
        return os.path.isdir(d) and any(f.endswith(".deb") for f in os.listdir(d))
    if not _has_debs(debs_local) and _has_debs(legacy):
        debs_local = legacy
    if not _has_debs(debs_local):
        return StepResult(
            "install_display_stack", False,
            "No carried display debs and the clone has no cage. On an "
            "online medic run: python3 scripts/refresh_deb_cache.py — then "
            "retry.")
    from workflows.wheelhouse import DISPLAY_PACKAGES, debs_for, offline_install_command
    picked = debs_for(DISPLAY_PACKAGES, debs_local)
    if not picked:
        return StepResult("install_display_stack", False,
                          "The carried packages do not include the screen set. On "
                          "an online medic run: python3 scripts/refresh_deb_cache.py "
                          "--closure — then retry.")
    wf.connection.run("rm -rf /tmp/nm-debs && mkdir -p /tmp/nm-debs")
    ok = all(wf.connection.push_file(f, "/tmp/nm-debs/") for f in picked)
    if not ok:
        return StepResult("install_display_stack", False,
                          "Could not copy the display packages to the new medic.")
    code, out, err = wf.connection.run(
        wf.priv(offline_install_command("/tmp/nm-debs")), timeout=900)
    if code != 0:
        # the LAST lines of apt's own words, so the ladder says what is missing
        return StepResult("install_display_stack", False,
                          "The screen packages would not install without the "
                          "internet: " + " ".join((err or out or "").strip().splitlines()[-3:])[-220:])
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


# --------------------------------------------------------------------------- #
# THE LAST TWO STEPS: lock the new medic the way its parent is locked.
#
# The keeper's policy (2026-10-08): EVERY clone ends with its parent's
# hardening — scoped sudo for its own app user, key-only SSH, SSH only from
# private networks (provisioning/security/). A clone for the keeper's OWN fleet
# keeps this medic's key, so the keeper can look after it remotely. A clone for
# a NEW community has this medic's key removed as the very last thing this
# medic does to it, leaving no key at all: nobody can log in to it from another
# machine. Its own screen is unaffected.
#
# They come after confirm_tool_running on purpose: every step before them needs
# the new medic's full sudo (the card bake's NOPASSWD:ALL), and the app is
# proven to stay open before anything is locked.
# --------------------------------------------------------------------------- #

#: The new medic's own copy of the hardening kit. Root-owned, because the root
#: process that later confirms or rolls back (security/apply_all.sh) must never
#: run a file the app account could have edited once its sudo is scoped.
HARDENING_DIR = "/usr/local/lib/nodemedic/security"
#: (path in this tool, path under HARDENING_DIR, mode)
HARDENING_KIT = (
    ("provisioning/security/apply_all.sh", "apply_all.sh", "755"),
    ("provisioning/security/apply_sshd.sh", "apply_sshd.sh", "755"),
    ("provisioning/security/rollback_sshd.sh", "rollback_sshd.sh", "755"),
    ("provisioning/security/apply_firewall.sh", "apply_firewall.sh", "755"),
    ("provisioning/security/rollback_firewall.sh", "rollback_firewall.sh", "755"),
    ("provisioning/security/apply_sudoers.sh", "apply_sudoers.sh", "755"),
    ("provisioning/security/rollback_sudoers.sh", "rollback_sudoers.sh", "755"),
    ("provisioning/security/render_sudoers.sh", "render_sudoers.sh", "755"),
    ("provisioning/security/sshd_config.d/01-nodemedic-hardening.conf",
     "sshd_config.d/01-nodemedic-hardening.conf", "644"),
    ("provisioning/security/nftables/nodemedic-ssh.nft",
     "nftables/nodemedic-ssh.nft", "644"),
    ("provisioning/sudoers.d/nodemedic", "sudoers.nodemedic", "644"),
)
#: One line from apply_all.sh: running | applied | confirmed | rolled-back: why
HARDEN_STATUS = "/run/nodemedic-harden/status"
#: How long apply_all.sh waits for this medic's verdict before it rolls every
#: change back by itself.
HARDEN_WINDOW_S = 600
#: The latest the new medic undoes an unconfirmed change on its own, even if
#: apply_all.sh itself died: its scripts' own self-reverts are armed for
#: WINDOW/60 + 10 minutes (apply_all.sh, REVERT_MIN).
HARDEN_UNDO_WITHIN_MIN = HARDEN_WINDOW_S // 60 + 10
#: The verdict files apply_all.sh watches. Plain files in the app user's home:
#: once its sudo is scoped, that is all this medic can still write there.
HARDEN_CONFIRM = "~/.nodemedic-harden-confirm"
HARDEN_ROLLBACK = "~/.nodemedic-harden-rollback"
#: The keeper's two sudo checks, run on the new medic AFTER it is scoped
#: (tests/test_privileged_commands.py proves each against the policy):
#: a whitelisted, read-only command that must still RUN (NM_DIAG)...
HARDEN_ALLOWED_PROBE = "sudo -n /usr/bin/ss -tlnp"
#: ...and a command no rule grants, which must be REFUSED.
HARDEN_REFUSED_PROBE = "sudo -n /usr/bin/true"


def _harden_status(wf: "CloneWorkflow") -> str:
    code, out, _e = wf.connection.run(f"cat {HARDEN_STATUS} 2>/dev/null", timeout=15)
    return (out or "").strip() if code == 0 else ""


def _harden_wait(wf: "CloneWorkflow", wanted, seconds: int, sleep) -> str:
    """Poll apply_all.sh's status until it reaches one of *wanted* or rolls
    back; "" if it never does. Bounded twice: by turns, so a test's no-op
    sleep cannot spin for real minutes, and by the monotonic clock, because
    against a new medic that has stopped answering one poll is an ssh that
    retries its connection timeout three times (~40 s each)."""
    import time as _time
    deadline = _time.monotonic() + seconds
    for _ in range(max(1, seconds // 3)):
        state = _harden_status(wf)
        if state.startswith("rolled-back") or state in wanted:
            return state
        if _time.monotonic() >= deadline:
            break
        sleep(3)
    return ""


def _lock_problems(wf: "CloneWorkflow") -> List[str]:
    """What is NOT right about the new medic's locks, checked from here.

    Every run() over the real connection is a NEW login — SSHConnection starts
    a fresh `ssh -o BatchMode=yes` for each command, with no shared master — so
    a pass means this medic's key is accepted AFTER the change, not that an
    old session survived it. [] when all is well."""
    if wf.connection.run("true", timeout=20)[0] != 0:
        return ["this medic could not log in to it again"]
    problems = []
    if wf.connection.run(
            "test -f /etc/ssh/sshd_config.d/01-nodemedic-hardening.conf")[0] != 0:
        problems.append("its key-only login setting is not in place")
    if wf.connection.run(HARDEN_ALLOWED_PROBE, timeout=20)[0] != 0:
        problems.append("it refused one of its own allowed admin jobs")
    code = wf.connection.run(HARDEN_REFUSED_PROBE, timeout=20)[0]
    if code == 0:
        problems.append("its app can still do anything as administrator")
    elif code in (124, 255):
        problems.append("it could not be asked whether its admin rights are limited")
    return problems


def _install_hardening_kit(wf: "CloneWorkflow") -> bool:
    stage = "/tmp/nm-security"
    wf.connection.run(f"rm -rf {stage} && mkdir -p {stage}/sshd_config.d {stage}/nftables")
    for rel, dest, _mode in HARDENING_KIT:
        if not wf.connection.push_file(os.path.join(TOOL_ROOT, rel), f"{stage}/{dest}"):
            return False
    if wf.connection.run(wf.priv(
            f"install -d -m 755 -o root -g root {HARDENING_DIR} "
            f"{HARDENING_DIR}/sshd_config.d {HARDENING_DIR}/nftables"))[0] != 0:
        return False
    for _rel, dest, mode in HARDENING_KIT:
        if wf.connection.run(wf.priv(
                f"install -m {mode} -o root -g root {stage}/{dest} "
                f"{HARDENING_DIR}/{dest}"))[0] != 0:
            return False
    return wf.connection.run(f"test -x {HARDENING_DIR}/apply_all.sh")[0] == 0


def harden_new_medic(wf: "CloneWorkflow") -> StepResult:
    """Lock the new medic the way this one is locked, without ever shutting
    this medic (or anyone) out.

    The anti-lockout, in order:
      1. refuse unless the new medic holds a key for this medic — every command
         here IS a key login, so this medic's key is proven by being here;
      2. install the kit root-owned, then start security/apply_all.sh as a
         root process of its own (systemd-run): it applies key-only SSH, the
         SSH firewall and the scoped sudo, each with its own self-revert
         armed, and then WAITS for a verdict;
      3. check from here, every check a NEW login: this medic gets back in,
         a whitelisted `sudo -n` command runs, a non-whitelisted one is
         refused, key-only login is in place;
      4. only then write the confirm verdict (a plain file the scoped account
         can still make); on any problem, the rollback verdict instead, and
         apply_all.sh runs the three rollback scripts;
      5. if this medic loses sight of it, nothing is confirmed: apply_all.sh
         rolls back at its deadline, and failing that each script's own
         self-revert fires (at the latest HARDEN_UNDO_WITHIN_MIN minutes,
         and at every restart until then)."""
    import shlex
    import time as _time
    name = "harden_new_medic"
    sleep = getattr(wf, "sleep", _time.sleep)
    later = (f"Left unconfirmed, the new medic undoes the change by itself "
             f"within {HARDEN_UNDO_WITHIN_MIN} minutes. Wait that long, then "
             f"press Retry.")

    # A Retry after a run that DID finish (this medic lost sight of it, or the
    # new medic restarted and forgot the status): it is locked already, and
    # its sudo would refuse a second install anyway.
    if not _lock_problems(wf) and \
            wf.connection.run("test -f /etc/nftables.d/nodemedic-ssh.nft")[0] == 0:
        return StepResult(name, True,
                          "The new medic was already locked like this one — "
                          "checked again from here.")

    if wf.connection.run("test -s ~/.ssh/authorized_keys")[0] != 0:
        return StepResult(name, False,
                          "The new medic holds no key for this medic, so locking "
                          "it could shut everyone out. Nothing was changed. "
                          "Write its card again with this medic, then clone again.")
    user = (wf.connection.run("id -un")[1] or "").strip() or "pi"

    # An earlier attempt's supervisor may still be waiting: one at a time.
    if _harden_status(wf) in ("running", "applied"):
        wf.connection.run(f"touch {HARDEN_ROLLBACK}")
        _harden_wait(wf, (), 120, sleep)

    if not _install_hardening_kit(wf):
        return StepResult(name, False,
                          "Could not copy the locks to the new medic. Nothing "
                          "was changed. Check the cable, then press Retry.")

    wf.connection.run(f"rm -f {HARDEN_CONFIRM} {HARDEN_ROLLBACK}")
    unit = f"nodemedic-harden-{int(_time.time())}"
    code, out, err = wf.connection.run(wf.priv(
        f"systemd-run --unit={unit} --collect --quiet /bin/bash "
        f"{HARDENING_DIR}/apply_all.sh --user {shlex.quote(user)} "
        f"--window {HARDEN_WINDOW_S}"), timeout=30)
    if code != 0:
        return StepResult(name, False,
                          "The new medic would not start locking itself. Nothing "
                          "was changed. Press Retry. ("
                          + " ".join((err or out or "").split())[-120:] + ")")

    state = _harden_wait(wf, ("applied", "confirmed"), 180, sleep)
    if state.startswith("rolled-back"):
        why = state.split(":", 1)[1].strip() if ":" in state else ""
        return StepResult(name, False,
                          "The new medic could not be locked"
                          + (f" ({why})" if why else "")
                          + ", so it undid every change and is as it was. "
                          "Press Retry.")
    if not state:
        wf.connection.run(f"touch {HARDEN_ROLLBACK}")
        return StepResult(name, False,
                          "The new medic did not say it had locked itself. "
                          "This medic told it to undo the change. " + later)

    problems = _lock_problems(wf)
    verdict = HARDEN_ROLLBACK if problems else HARDEN_CONFIRM
    told = wf.connection.run(f"touch {verdict}")[0] == 0
    state = _harden_wait(wf, ("confirmed",), 120, sleep)
    if problems:
        what = "The locks did not check out from here (" + "; ".join(problems) + ")"
        if state.startswith("rolled-back"):
            return StepResult(name, False,
                              what + ", so the new medic undid every change and "
                              "is as it was. Press Retry.")
        return StepResult(name, False,
                          what + ". This medic told it to undo them. " + later)
    if state != "confirmed":
        return StepResult(name, False,
                          "The locks checked out, but the new medic did not make "
                          "them permanent"
                          + ("" if told else " (this medic could not reach it)")
                          + ". " + later)

    # Once more, now that it is permanent and the revert timers are gone.
    problems = _lock_problems(wf)
    if wf.connection.run("test -f /etc/nftables.d/nodemedic-ssh.nft")[0] != 0:
        problems.append("its SSH firewall would not survive a restart")
    if problems:
        return StepResult(name, False,
                          "The new medic says it is locked, but the last check "
                          "from here found: " + "; ".join(problems)
                          + ". Look at it before you hand it over.")
    return StepResult(name, True,
                      "Locked like this medic: its app can do only its own "
                      "admin jobs, and it takes remote logins only with a key, "
                      "only from nearby networks. Checked from here with a "
                      "fresh login.")


def remove_parent_key(wf: "CloneWorkflow") -> StepResult:
    """THE LAST THING THIS MEDIC DOES to a medic going to a new community: take
    its own key away. The new medic is then left with no authorized key at all
    — with key-only SSH from harden_new_medic, nobody can log in to it from
    another machine. Its own screen works as before; its keeper can still add a
    key from there. A clone for this keeper's own fleet keeps the key."""
    name = "remove_parent_key"
    if not getattr(wf, "fresh_fleet", False):
        return StepResult(name, True,
                          "Same keeper: this medic keeps its key to the new one, "
                          "so you can look after it from here.", skipped=True)
    code, out, _e = wf.connection.run(
        "rm -f ~/.ssh/authorized_keys ~/.ssh/authorized_keys2 && "
        "test ! -e ~/.ssh/authorized_keys && test ! -e ~/.ssh/authorized_keys2 "
        "&& echo nm-no-keys-left", timeout=20)
    if code != 0 or "nm-no-keys-left" not in (out or ""):
        return StepResult(name, False,
                          "Could not take this medic's key off the new medic. "
                          "Press Retry. (If this medic can no longer reach it, "
                          "the key is already gone.)")
    # The door must now be shut to this medic too: a fresh login is refused.
    if wf.connection.run("true", timeout=20)[0] == 0:
        return StepResult(name, False,
                          "This medic's key file is gone from the new medic, yet "
                          "this medic can still log in to it. Look at it before "
                          "you hand it over.")
    return StepResult(name, True,
                      "This medic's key is gone from the new medic. Nobody can "
                      "log in to it from another machine now; its own screen "
                      "works as normal.")


# The very end of the list, in this order (tests pin it): nothing may run after
# the parent's key is gone — this medic can no longer reach the new one.
_CLONE_STEPS.append(("harden_new_medic", harden_new_medic))
_CLONE_STEPS.append(("remove_parent_key", remove_parent_key))


#: Steps that draw nothing on the new medic's screen. By then its own app has
#: owned that screen since restart_into_tool, and once harden_new_medic has
#: scoped its sudo, the framebuffer and VT writes below are refused (and would
#: only fill its log with refusals). This medic's screen still shows them.
_QUIET_ON_NEW_MEDIC = ("harden_new_medic", "remove_parent_key")


class CloneWorkflow:
    def __init__(self, connection: Connection, registry: NodeRegistry,
                 fresh_fleet: bool = False):
        self.connection = connection
        self.registry = registry
        #: The keeper's answer to "bring your fleet along?" (Clone screen,
        #: 2026-10-06). True = the new medic starts with an empty VITALS and
        #: roster — for a medic going to another keeper; the maps and the
        #: lineage still travel.
        self.fresh_fleet = bool(fresh_fleet)
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

    #: What the NEW medic's own screen says while it is being filled. Its
    #: console showed a bare login prompt for the whole copy, which a new
    #: keeper reads as "nothing is happening" (first real clone, 2026-10-06).
    # the SAME words the picture uses (workflows.clone_screen) — the console
    # fallback and the clone page must never disagree
    @property
    def STEP_WORDS(self):
        from workflows.clone_screen import STEP_WORDS as _W
        return _W


    def _tell_new_medic(self, step_name: str, failed: bool = False) -> None:
        """Show the new medic what is happening, on ITS OWN screen. Best-effort
        and quiet: a screen message must never be what fails a clone.

        A full-screen picture when its console framebuffer is the medic's
        720x1280 32-bit panel (workflows.clone_screen: the home page
        sharpening behind a readable step list — keeper, 2026-10-06), else the
        old line of console text."""
        if isinstance(self.connection, _NotYetConnected) or step_name == "find_new_medic":
            return
        if step_name in _QUIET_ON_NEW_MEDIC:
            return
        names = [n for n, _f in self.steps]
        try:
            idx = names.index(step_name)
        except ValueError:
            return
        try:
            if self._draw_on_new_medic(names, idx, failed):
                return
        except Exception:                                          # noqa: BLE001
            pass
        words = self.STEP_WORDS.get(step_name, "Receiving Node Medic")
        import shlex
        line = (f"\n  NODE MEDIC - being cloned. Leave both medics plugged in.\n"
                f"  Step {idx + 1} of {len(names)}: {words}...\n")
        try:
            # shlex.quote: an apostrophe ("medic's") ended the old quoting
            self.connection.run(self.priv(
                "sh -c " + shlex.quote("printf '%s' " + shlex.quote(line) + " > /dev/tty1")),
                timeout=10)
        except Exception:                                          # noqa: BLE001
            pass

    def _draw_on_new_medic(self, names, idx: int, failed: bool) -> bool:
        if getattr(self, "_fb_ok", None) is None:
            code, out, _e = self.connection.run(
                "cat /sys/class/graphics/fb0/virtual_size "
                "/sys/class/graphics/fb0/bits_per_pixel", timeout=10)
            self._fb_ok = code == 0 and (out or "").split() == ["720,1280", "32"]
            if self._fb_ok:
                # a quiet console of its own: no login prompt or cursor drawn
                # over the picture
                # (setterm needs a TERM the sudo shell lacks; the raw escape
                # and fbcon's own switch are what actually hid the flashing
                # cursor on node-medic-2, 2026-10-06)
                self.connection.run(self.priv(
                    "sh -c 'chvt 8; printf \"\\033[?25l\" > /dev/tty8; "
                    "echo 0 > /sys/class/graphics/fbcon/cursor_blink'"),
                    timeout=10)
        if not self._fb_ok:
            return False
        import tempfile
        from workflows.clone_screen import render_frame, framebuffer_bytes
        import time as _t
        t0 = getattr(self, "_clone_t0", None) or _t.monotonic()
        self._clone_t0 = t0
        raw = framebuffer_bytes(render_frame(names, idx, failed=failed,
                                             elapsed_s=int(_t.monotonic() - t0)))
        with tempfile.NamedTemporaryFile(prefix="nm-frame-", suffix=".raw",
                                         delete=False) as fh:
            fh.write(raw)
            local = fh.name
        try:
            if not self.connection.push_file(local, "/tmp/nm-frame.raw"):
                return False
        finally:
            os.unlink(local)
        code = self.connection.run(self.priv(
            "dd if=/tmp/nm-frame.raw of=/dev/fb0 bs=1M status=none"), timeout=20)[0]
        if failed:
            # a failed clone left on tty8 hid the console login: hand it back
            self.connection.run(self.priv("chvt 1"), timeout=10)
        return code == 0

    def run_all(self, on_progress: Optional[Callable[[StepResult], None]] = None):
        emit = on_progress or (lambda r: None)
        while self.current_index < len(self.steps):
            name, func = self.steps[self.current_index]
            self._tell_new_medic(name)
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
                self._tell_new_medic(name, failed=True)   # red on its own screen too
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


def make_discovering_workflow(registry: NodeRegistry, hostname: str = "", fresh_fleet: bool = False,
                              username: str = "pi") -> "CloneWorkflow":
    """A CloneWorkflow whose FIRST step finds the new medic and connects.

    Discovery order is provisioning.direct_link's: <hostname>.local (mDNS —
    also answers over the household WiFi), then the baked static /29
    (10.55.0.1) over the patch cable. The address that answers gets its host
    key freshly PINNED — a rebirthed/reimaged machine at a reused address is
    the stale-key trap that has burned every flow before this one, so the old
    pin is dropped first and the new machine's key trusted on first contact.
    """
    wf = CloneWorkflow(_NotYetConnected(), registry, fresh_fleet=fresh_fleet)

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

