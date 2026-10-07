"""Set up a Node Medic from GitHub alone: the clone's ladder, with the internet
standing in for the parent.

The keeper, 2026-10-08: "Anything cloned needs to be able to do everything that
the original did ... And anybody downloading it from GitHub needs to have all
that functionality as well."

A clone becomes a medic because its parent hands it everything over a cable
(``workflows/clone.py``). A Pi set up from this repository has no parent, so
this module does the same job on the Pi itself, from the internet:

* **one architecture** — the end state is the clone's: Raspberry Pi OS Lite,
  the ``cage`` kiosk unit, the same packages, the same caches. Where the clone
  configures the new medic with a step that does not depend on the parent
  (the kiosk, the boot chip, the card helper, the SSH key, the identity, the
  final check), this runs THAT SAME FUNCTION against this machine through a
  LocalConnection. Where the clone copies something from the parent, a step
  here fetches the same thing from where the parent got it.
* **one manifest** — every version and source is in
  ``assets/medic_manifest.json``: Node Medic 1's own pins, read on that medic.
  Nothing here invents a version.
* **resumable** — every step first checks whether its work is already on disk;
  finished steps are skipped, so after any failure the same command carries
  on. ``--check`` runs only those checks and changes nothing.
* **honest** — a step that cannot run (no internet, no passwordless sudo, too
  little disk, the wrong board) says so in a sentence and the others go on;
  only the three checks that make the whole run meaningless (not a Pi 5, not
  64-bit Lite trixie, Node Medic not at ~/reticulum-tool) stop it.

``CLONE_ROUTE`` accounts for every step of the clone, so a step added to the
clone cannot be missing from a medic built from GitHub without a test failing
(tests/test_medic_setup.py).

The medic's own radio (the Heltec Wireless Tracker, the splitter, rnsd, lxmd
and its Reticulum config) is NOT set up here, exactly as it is not set up by a
clone: the first page of the walkthrough's tour does it on the glass, with the
board plugged in (``workflows/medic_radio.py``). What that page needs — the
Tracker image and a Reticulum that can name the Tracker — is built here.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import shlex
import sys
import time
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple

from transport.connection import Connection
from workflows import clone
from workflows.build import StepResult

#: The tool's own checkout (…/reticulum-tool).
TOOL_ROOT = clone.TOOL_ROOT
MANIFEST_PATH = os.path.join(TOOL_ROOT, "assets", "medic_manifest.json")
#: Downloads in progress and the files the setup writes for itself.
CACHE_DIR = "~/.cache/nodemedic-setup"
#: Every command and the tail of its output, for whoever reads a failure later.
LOG_PATH = "~/medic_setup.log"
#: Free space kept beyond a step's own need, so no step fills the card.
RESERVE_MB = 1024
#: The kiosk unit configure_autostart installs (and medic_radio restarts).
KIOSK_UNIT = "reticulum-node-medic.service"
BOOT_CONFIG = "/boot/firmware/config.txt"
KIVY_CONFIG = "~/.kivy/config.ini"
WORLD_MAP_UNIT = "/etc/systemd/system/world-map-fill.service"
WORLD_MAP_FILE = "~/.reticulum-node-medic/maps/offline.mbtiles"


def load_manifest(path: str = MANIFEST_PATH) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


# --------------------------------------------------------------------------- #
# Pure helpers — no machine needed, each pinned by a test.
# --------------------------------------------------------------------------- #

def kivy_touch_cure(text: str) -> str:
    """The two changes the clone's code names for the ``~/.kivy/config.ini`` it
    carries (``carry_touch_cure``: the doubled-tap cure, then ``show_cursor =
    0``), made to Kivy's own default file. Whatever else Node Medic 1's own
    file holds was not captured.

    Kivy writes ``%(name)s = probesysfs`` under ``[input]`` on Linux, and
    probesysfs opens the Goodix panel through mtdev as soon as libmtdev is
    installed — which cage brings. main.py adds its own mtdev provider for the
    panel (ui/touch_input.py), so the default file means TWO providers on one
    panel and every tap twice: the 2026-07-31 disease. The line is commented
    out, as it is on Node Medic 1. ``show_cursor`` goes to 0: a kiosk has
    fingers, not a mouse."""
    out = []
    section = ""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            section = stripped[1:-1].strip().lower()
        elif (section == "input" and not stripped.startswith(("#", ";"))
              and re.search(r"=\s*probesysfs\b", stripped)):
            line = "#" + line
        elif section == "graphics" and re.match(r"show_cursor\s*=", stripped):
            line = "show_cursor = 0"
        out.append(line)
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def index_with_standins(target: dict, donor: dict, standins: Dict[str, str],
                        host: str = "aarch64-linux-gnu") -> Tuple[dict, List[str]]:
    """A copy of board-manager index *target* in which every tool named in
    *standins* (``packager:tool@version`` -> ``packager:tool@version``) gains
    a *host* download borrowed from *donor*, when it has none of its own.

    Why: RAKwireless's index lists no aarch64 build of nrfjprog 9.4.0 (read
    2026-10-08), and arduino-cli then refuses to install rakwireless:nrf52 on a
    Pi at all. Adafruit's and Heltec's indexes serve nrfjprog-10.15.0-arm for
    that very tool on aarch64, so the copy borrows that entry. Node Medic 1's
    RAK core went in by hand ("the native-ARM64 toolchain trick",
    workflows/rtnode_build.py) and those steps were never written down; this is
    a written-down road to the same installed core. nrfjprog itself is a
    J-Link flasher that no Node Medic build runs. Returns ``(index, changes)``."""
    def tool(index, spec):
        pk, rest = spec.split(":", 1)
        name, ver = rest.split("@", 1)
        for p in index.get("packages", []):
            if p.get("name") != pk:
                continue
            for t in p.get("tools", []):
                if t.get("name") == name and t.get("version") == ver:
                    return t
        return None

    out = json.loads(json.dumps(target))
    changes = []
    for want, have in standins.items():
        t, d = tool(out, want), tool(donor, have)
        if t is None or d is None:
            continue
        systems = t.setdefault("systems", [])
        if any(s.get("host") == host for s in systems):
            continue
        borrowed = next((s for s in d.get("systems", []) if s.get("host") == host), None)
        if borrowed:
            systems.append(dict(borrowed))
            changes.append(f"{want} borrows {have}'s {host} download")
    return out, changes


def parse_core_list(text: str) -> Dict[str, str]:
    """``{core id: installed version}`` from ``arduino-cli core list --json``
    (1.x: ``{"platforms": [...]}``; older: a bare list)."""
    try:
        data = json.loads(text or "")
    except ValueError:
        return {}
    items = data.get("platforms", []) if isinstance(data, dict) else data
    out = {}
    for it in items or []:
        if not isinstance(it, dict):
            continue
        ver = it.get("installed_version") or it.get("installed") or ""
        if it.get("id") and ver:
            out[it["id"]] = ver
    return out


def parse_lib_list(text: str) -> Dict[str, str]:
    """``{library name: version}`` from ``arduino-cli lib list --json``."""
    try:
        data = json.loads(text or "")
    except ValueError:
        return {}
    items = data.get("installed_libraries", []) if isinstance(data, dict) else data
    out = {}
    for it in items or []:
        lib = it.get("library") if isinstance(it, dict) else None
        if isinstance(lib, dict) and lib.get("name"):
            out[lib["name"]] = lib.get("version", "")
    return out


def render_world_map_unit(text: str, user: str, home: str) -> str:
    """scripts/world-map-fill.service, written for THIS user and home: the repo
    copy names Node Medic 1's account."""
    text = re.sub(r"(?m)^User=.*$", f"User={user}", text)
    return text.replace("/home/nodemedic", home)


def medic_requirements(manifest: dict, requirements_txt: str) -> str:
    """The pip pins a medic set up from GitHub installs: Node Medic 1's own
    user-site packages, exactly, plus the lines of assets/requirements.txt for
    the packages Node Medic 1 takes from its OS — which is what a clone of
    Node Medic 1 installs (workflows.parent_freeze.requirements_text). rns is
    1.3.7 here, not requirements.txt's 1.3.8: only 1.3.7 carries the medic's
    patch, without which a medic cannot name its own Tracker."""
    from workflows.parent_freeze import manifest_lines, requirements_text
    pins = manifest["python"]["packages"]
    return requirements_text({n: (v, True) for n, v in pins.items()},
                             manifest_lines(requirements_txt))


def custom_board_builds(board_keys) -> List[dict]:
    """How each board BUILD flashes from a pre-built image gets that image:
    the tree it compiles in, the command (the board's own
    ``RNodeBoard.compile_command``, which compiles the sketch in the working
    directory) and the files BUILD reads afterwards."""
    from workflows import rnode_boards, rnode_flash
    out = []
    for key in board_keys:
        b = rnode_boards.get_board(key)
        build_dir = rnode_flash.fork_build_dir_for(b)
        if b.flash_method == "serial_dfu":
            artefacts = [f"{b.build_dir}/{b.dfu_package}"]
        else:
            artefacts = [rnode_flash.fork_image_for(b, sfx)
                         for sfx in ("bin", "bootloader.bin", "partitions.bin")]
        out.append({"key": key, "name": b.display_name,
                    "tree": build_dir.split("/build/", 1)[0],
                    "command": b.compile_command(), "artefacts": artefacts})
    return out


def rtnode_builds(manifest: dict) -> List[dict]:
    """The RTNode-2400-NM compiles the setup runs once, so both toolchains are
    proven and cached before a board is on the bench: the PlatformIO proof env
    (ESP32-S3 family) and every nRF52 Makefile target BUILD uses."""
    from workflows import rtnode_build as rb
    env = manifest["prebuilt"]["rtnode_proof_env"]
    # the linked image, whatever it is called: the tree's extra_script.py
    # renames it rtnode_<custom_variant> (a firmware.bin check called a
    # finished build a failure — found compiling it, 2026-10-08)
    out = [{"name": f"PlatformIO {env}", "dir": rb.RTNODE_PROJECT_DIR,
            "command": f"pio run -e {env}",
            "artefact": f"{rb.RTNODE_PROJECT_DIR}/.pio/build/{env}/*.elf"}]
    for t in rb.RTNODE_TARGETS.values():
        if t.mechanism != "nrf_dfu":
            continue
        out.append({"name": f"{t.display} (make {t.build_env})", "dir": rb.TECHO_PROJECT_DIR,
                    "command": f"make {t.build_env}",
                    # the Makefile's --output-dir: build/<target minus "firmware-">
                    "artefact": f"{rb.TECHO_PROJECT_DIR}/build/"
                                f"{t.build_env[len('firmware-'):]}/RNode_Firmware.ino.zip"})
    return out


def pio_envs() -> List[str]:
    """Every PlatformIO env BUILD compiles (the ESP32-S3 RTNode-2400 targets)."""
    from workflows import rtnode_build as rb
    return [t.build_env for t in rb.RTNODE_TARGETS.values() if t.mechanism == "pio"]


#: The clone's messages talk to a keeper watching one medic copy itself onto
#: another. Run here, on the medic itself, the same failure reads like this.
_LOCAL_WORDS = (
    ("The machine on the cable is not a Raspberry Pi 5.", "This machine is not a Raspberry Pi 5."),
    ("Node Medic only makes medics on a Raspberry Pi 5.", "Node Medic runs on a Raspberry Pi 5 only."),
    ("installed on the clone", "installed on this medic"),
    ("to the clone", "onto this medic"),
    ("for the clone", "for this medic"),
    ("on the clone", "on this medic"),
    ("The new medic", "This medic"), ("the new medic", "this medic"),
    ("the clone", "this medic"), ("The clone", "This medic"),
    ("Press Retry", "Run the setup again"),
)


def local_words(message: str) -> str:
    for old, new in _LOCAL_WORDS:
        message = message.replace(old, new)
    return message


# --------------------------------------------------------------------------- #
# The run.
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class SetupStep:
    name: str
    #: The one plain-English line the keeper reads.
    title: str
    #: Reads only: ``(done, detail)``. Never changes anything (``--check``).
    check: Callable[["MedicSetup"], Tuple[bool, str]]
    #: Does the work; None for a step that only checks.
    act: Optional[Callable] = None
    #: The clone's own step function, when this step IS that step run here.
    clone_fn: Optional[Callable] = None
    internet: bool = False
    root: bool = False
    needs_mb: int = 0
    #: Its failure makes the rest meaningless: stop the run.
    fatal: bool = False
    #: Keeps working after the setup ends (the world map): a started job is ok.
    background: bool = False
    #: Only true after the restart: in a setup run, not done yet is not a failure.
    after_restart: bool = False
    #: Not shown by --check.
    run_only: bool = False
    #: Its act's own result is the outcome; there is nothing to re-check.
    trust_act: bool = False


@dataclass
class Outcome:
    step: SetupStep
    status: str          # ok | already | skipped | running | failed | missing | refused
    detail: str = ""


#: How each status reads at the end of a step's line.
_WORDS = {"ok": "ok", "already": "ok (already done)", "skipped": "skipped",
          "running": "running in the background", "failed": "FAILED",
          "missing": "MISSING", "refused": "REFUSED"}


class MedicSetup:
    """One run of the setup (or of ``--check``) on this machine."""

    def __init__(self, connection: Connection, manifest: Optional[dict] = None,
                 home: Optional[str] = None, tool_root: str = TOOL_ROOT,
                 check_only: bool = False, reboot: bool = False,
                 stream=None, sleep: Callable[[float], None] = time.sleep,
                 local: bool = False):
        from monitor.registry import NodeRegistry
        self.conn = connection
        self.manifest = manifest if manifest is not None else load_manifest()
        self.home = home or os.path.expanduser("~")
        self.tool_root = tool_root
        self.check_only = check_only
        self.reboot = reboot
        self.stream = stream if stream is not None else sys.stdout
        self.sleep = sleep
        #: True only on the real machine: then this process also sees the
        #: Python packages it installs (Field readiness freezes them).
        self.local = local
        #: The clone's own workflow object, so its step functions run here
        #: unchanged. fresh_fleet: there is no parent fleet to bring.
        self.wf = clone.CloneWorkflow(connection, NodeRegistry(), fresh_fleet=True)
        self.wf.sleep = sleep
        self._user = None
        self._online = None
        self._sudo = None
        self._apt_updated = False
        self.steps: List[SetupStep] = list(SETUP_STEPS)
        self.results: List[Outcome] = []

    # -- small services the steps share --------------------------------------

    def path(self, p: str) -> str:
        """*p* with a leading ``~`` made THIS medic's home."""
        return self.home + p[1:] if p.startswith("~") else p

    def out_of(self, command: str, timeout: int = 60) -> str:
        code, out, _err = self.conn.run(command, timeout=timeout)
        return (out or "").strip() if code == 0 else ""

    def priv(self, command: str) -> str:
        return self.wf.priv(command)

    @property
    def user(self) -> str:
        if self._user is None:
            self._user = self.out_of("id -un") or os.environ.get("USER", "")
        return self._user

    def online(self) -> bool:
        if self._online is None:
            from workflows.updater import has_connectivity
            self._online = has_connectivity(self.conn)
        return self._online

    def can_sudo(self) -> bool:
        if self._sudo is None:
            self._sudo = self.conn.run("sudo -n true", timeout=20)[0] == 0
        return self._sudo

    def free_mb(self) -> int:
        out = self.out_of(f"df -Pk {shlex.quote(self.home)} | awk 'NR==2 {{print $4}}'")
        return int(out) // 1024 if out.isdigit() else -1

    # -- the loop --------------------------------------------------------------

    def run_all(self) -> int:
        """Every step in order, one line each. Returns the exit code: 0 all
        done, 1 something still missing or failed, 2 refused."""
        shown = [s for s in self.steps if not (self.check_only and s.run_only)]
        total = len(shown)
        for i, step in enumerate(shown, 1):
            self._write(f"[{i:>2}/{total}] {step.title} ... ")
            outcome = self._do(step)
            self.results.append(outcome)
            words = _WORDS[outcome.status]
            detail = " ".join((outcome.detail or "").split())
            self._write(words + (f": {detail}" if detail and outcome.status != "ok"
                                 else f" ({detail})" if detail and outcome.status == "ok"
                                 and len(detail) < 90 else "") + "\n")
            if outcome.status == "refused":
                self._write("\nStopped: this machine cannot be set up as a Node Medic "
                            "until the line above is put right.\n")
                return 2
        return self._summary()

    def _summary(self) -> int:
        bad = [o for o in self.results if o.status in ("failed", "missing")]
        log = self.path(LOG_PATH)
        if self.check_only:
            self._write(("\nEverything the manifest lists is on this medic.\n" if not bad else
                         f"\n{len(bad)} item(s) still missing. Run the setup without "
                         "--check to fetch and build them.\n"))
            return 1 if bad else 0
        if bad:
            self._write(f"\n{len(bad)} step(s) need attention (above). Put each right "
                        "and run the same command again: finished steps are skipped. "
                        f"Every command and its output is in {log}.\n")
            return 1
        status = {o.step.name: o.status for o in self.results}
        lines = ["", "Node Medic is set up."]
        if status.get("tool_running") in ("ok", "already"):
            lines.append("- It is running on its screen.")
        elif status.get("restart") == "ok":
            lines.append("- It is restarting now; its screen then opens the setup "
                         "walkthrough.")
        else:
            lines.append("- Restart to start it: sudo reboot. Its screen then opens the "
                         "setup walkthrough.")
        lines.append("- A medic with no radio yet sets it up on the first page of the "
                     "walkthrough's tour (or later: Settings > This medic's radio and "
                     "position finder): screw the aerial onto the Heltec Wireless "
                     "Tracker, plug it in, and press Set up its radio. That writes the "
                     "radio software, the rnsd and lxmd services and the Reticulum "
                     "config.")
        if status.get("world_map") == "running":
            lines.append("- The world map keeps downloading in the background until it "
                         "is complete; python3 scripts/setup_medic.py --check says how far "
                         "it has got.")
        self._write("\n".join(lines) + "\n")
        return 0

    def _write(self, text: str) -> None:
        try:
            self.stream.write(text)
        except UnicodeEncodeError:
            self.stream.write(text.encode("ascii", "replace").decode("ascii"))
        try:
            self.stream.flush()
        except Exception:                                      # noqa: BLE001
            pass

    def _check(self, step: SetupStep) -> Tuple[bool, str]:
        try:
            return step.check(self)
        except Exception as exc:                               # noqa: BLE001
            return False, f"could not check ({type(exc).__name__}: {exc})"

    def _do(self, step: SetupStep) -> Outcome:
        done, detail = self._check(step)
        if self.check_only:
            return Outcome(step, "ok" if done else "missing", detail)
        if done:
            return Outcome(step, "ok" if step.act is None else "already", detail)
        if step.after_restart:
            return Outcome(step, "skipped", detail)
        if step.act is None:
            return Outcome(step, "refused" if step.fatal else "failed", detail)
        why = self._cannot_start(step)
        if why:
            return Outcome(step, "failed", why)
        try:
            if step.clone_fn is not None and step.act is step.clone_fn:
                result = step.act(self.wf)
            else:
                result = step.act(self)
        except Exception as exc:                               # noqa: BLE001
            result = StepResult(step.name, False,
                                f"stopped with an error ({type(exc).__name__}: {exc})")
        message = local_words(result.message) if step.clone_fn else result.message
        if not result.success:
            return Outcome(step, "refused" if step.fatal else "failed", message)
        if step.trust_act:
            return Outcome(step, "skipped" if result.skipped else "ok", message)
        done, after = self._check(step)
        if done:
            return Outcome(step, "ok", message)
        if step.background:
            return Outcome(step, "running", message)
        if result.skipped:
            return Outcome(step, "skipped", message)
        return Outcome(step, "failed", f"it finished, but the check still says: {after}")

    def _cannot_start(self, step: SetupStep) -> str:
        """Why *step* cannot begin here and now, or ""."""
        if step.needs_mb:
            free = self.free_mb()
            if 0 <= free < step.needs_mb + RESERVE_MB:
                return (f"needs about {(step.needs_mb + RESERVE_MB) / 1024:.1f} GB free "
                        f"in {self.home}; {free / 1024:.1f} GB is left. Free some space "
                        "(or use a bigger card) and run the setup again.")
        if step.internet and not self.online():
            return ("needs the internet, and this Pi cannot reach github.com. Connect "
                    "it (Wi-Fi or ethernet) and run the setup again.")
        if step.root and not self.can_sudo():
            return ("needs sudo without a password, which this user does not have "
                    "(`sudo -n true` was refused). Raspberry Pi OS gives it to the "
                    "first user it creates; run the setup as that user.")
        return ""


# --------------------------------------------------------------------------- #
# The steps. Each check reads only; each act does the work and returns a
# StepResult whose message says what happened or why not.
# --------------------------------------------------------------------------- #

def _q(s: str) -> str:
    return shlex.quote(s)


def _tail(*texts, n: int = 220) -> str:
    text = " ".join(" ".join((t or "").split()) for t in texts if t)
    return text[-n:]


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _remote_sha256(s: MedicSetup, path: str) -> str:
    out = s.out_of(f"sha256sum {_q(path)}", timeout=600)
    return out.split()[0] if out else ""


def _download(s: MedicSetup, url: str, dest: str, sha256: str,
              timeout: int = 3600) -> Tuple[bool, str]:
    """Fetch *url* to *dest*, resuming an interrupted download, and keep it
    only if its SHA-256 is the pinned one."""
    if _remote_sha256(s, dest) == sha256:
        return True, "already downloaded and checked"
    part = dest + ".part"
    if _remote_sha256(s, part) != sha256:
        code, out, err = s.conn.run(
            f"mkdir -p {_q(os.path.dirname(dest))} && curl -fL --retry 3 --retry-delay 5 "
            f"-C - -o {_q(part)} {_q(url)}", timeout=timeout)
        if code != 0:
            return False, f"the download did not finish ({_tail(err, out)})"
    got = _remote_sha256(s, part)
    if got != sha256:
        s.conn.run(f"rm -f {_q(part)}")
        return False, (f"the download's checksum is not the pinned one (got {got[:12] or 'nothing'}, "
                       f"expected {sha256[:12]}); it was deleted")
    s.conn.run(f"mv -f {_q(part)} {_q(dest)}")
    return True, "downloaded and checked"


# -- the three checks that stop the run -------------------------------------

def _check_pi5(s: MedicSetup) -> Tuple[bool, str]:
    r = clone.verify_target_pi5(s.wf)
    return r.success, local_words(r.message)


def _os_codename(os_release: str) -> str:
    m = re.search(r'(?m)^VERSION_CODENAME="?([A-Za-z0-9]+)"?', os_release or "")
    return m.group(1) if m else ""


def _check_system(s: MedicSetup) -> Tuple[bool, str]:
    want = s.manifest["system"]
    problems = []
    arch = s.out_of("uname -m")
    if arch != want["arch"]:
        problems.append(f"the system is {arch or 'unknown'}, not {want['arch']} — write "
                        f"{want['image']} to the card")
    codename = _os_codename(s.out_of("cat /etc/os-release"))
    if codename != want["os_codename"]:
        problems.append(f"the system is Raspberry Pi OS '{codename or 'unknown'}', not "
                        f"'{want['os_codename']}' — write {want['image']} to the card")
    py = s.out_of("python3 -c 'import sys; print(\"%d.%d\" % sys.version_info[:2])'")
    if py != want["python"]:
        problems.append(f"Python is {py or 'missing'}, not {want['python']}")
    if s.out_of("id -u") == "0":
        problems.append("it is running as root — run it as the medic's own user, "
                        "without sudo")
    home = s.out_of("echo $HOME")
    if s.user and home and home != f"/home/{s.user}":
        problems.append(f"the home folder is {home}, not /home/{s.user} (the kiosk unit "
                        "is written for /home/<user>)")
    if "install ok installed" in s.out_of(
            "dpkg-query -W -f='${Status}' lightdm 2>/dev/null"):
        problems.append("this card runs the desktop (lightdm), which would fight the "
                        f"cage kiosk for the screen — write {want['image']} instead")
    if problems:
        return False, "; ".join(problems) + "."
    return True, f"{codename}, {arch}, Python {py}, user {s.user}"


def _check_checkout(s: MedicSetup) -> Tuple[bool, str]:
    want = os.path.realpath(s.path(s.manifest["system"]["tool_dir"]))
    have = os.path.realpath(s.tool_root)
    if have != want:
        return False, (f"Node Medic is at {have}, but it must be at {want}: the kiosk, "
                       f"the field caches and the services all read that path. Move it "
                       f"(mv {have} {want}) and run the setup from there.")
    # .git is a FILE in a worktree or submodule checkout, a folder otherwise
    git = os.path.exists(os.path.join(s.tool_root, ".git"))
    return True, ("a git checkout" if git else
                  "no git history here, so About cannot name the version")


# -- clock and packages -------------------------------------------------------

def _clock_state(s: MedicSetup) -> Dict[str, str]:
    out = s.out_of("timedatectl show -p NTP -p NTPSynchronized")
    return dict(line.split("=", 1) for line in out.splitlines() if "=" in line)


def _check_clock(s: MedicSetup) -> Tuple[bool, str]:
    st = _clock_state(s)
    if st.get("NTPSynchronized") == "yes":
        return True, "synchronised"
    return False, ("not synchronised" + ("" if st.get("NTP") == "yes"
                                         else "; network time is off"))


def _act_clock(s: MedicSetup) -> StepResult:
    # A Pi 5 has no clock battery: until NTP answers, the date is whatever it
    # was at the last shutdown, and every TLS download below fails on it.
    # Offline (a field medic, whose GPS keeps time and switches NTP off) there
    # is nothing to download, so the clock is left exactly as it is.
    if not s.online():
        return StepResult("use_network_time", True, "no internet, so the clock was left "
                          "as it is", skipped=True)
    if not s.can_sudo():
        return StepResult("use_network_time", True, "network time cannot be switched on "
                          "without sudo; the clock was left as it is", skipped=True)
    s.conn.run(s.priv("timedatectl set-ntp true"), timeout=30)
    for _ in range(15):
        if _clock_state(s).get("NTPSynchronized") == "yes":
            return StepResult("use_network_time", True, "network time on, clock synchronised")
        s.sleep(2)
    return StepResult("use_network_time", False,
                      "network time is on but the clock has not synchronised in 30 "
                      "seconds; check the internet connection and run the setup again")


def _missing_debs(s: MedicSetup, packages) -> List[str]:
    out = s.out_of("dpkg-query -W -f='${Package} ${Status}\\n' "
                   + " ".join(packages) + " 2>/dev/null")
    have = {ln.split()[0] for ln in out.splitlines()
            if ln.strip().endswith("install ok installed")}
    return [p for p in packages if p not in have]


def _apt_install(s: MedicSetup, name: str, packages) -> StepResult:
    missing = _missing_debs(s, packages)
    if not missing:
        return StepResult(name, True, "already installed")
    if not s._apt_updated:
        code, out, err = s.conn.run(
            s.priv("apt-get -o DPkg::Lock::Timeout=300 update"), timeout=900)
        if code != 0:
            return StepResult(name, False,
                              f"apt could not refresh its package lists ({_tail(err, out)})")
        s._apt_updated = True
    code, out, err = s.conn.run(
        s.priv("env DEBIAN_FRONTEND=noninteractive apt-get -o DPkg::Lock::Timeout=300 "
               "install -y --no-install-recommends " + " ".join(missing)), timeout=2400)
    if code != 0:
        return StepResult(name, False, f"apt could not install {', '.join(missing)} "
                                       f"({_tail(err, out)})")
    return StepResult(name, True, f"installed {', '.join(missing)}")


def _check_screen_packages(s: MedicSetup) -> Tuple[bool, str]:
    from workflows.wheelhouse import DISPLAY_PACKAGES
    missing = _missing_debs(s, DISPLAY_PACKAGES)
    return not missing, ("installed" if not missing else "missing " + ", ".join(missing))


def _act_screen_packages(s: MedicSetup) -> StepResult:
    # THE CLONE'S SCREEN SET (wheelhouse.DISPLAY_PACKAGES): the cage kiosk and
    # what the app draws through. A clone installs it from carried .debs;
    # here apt fetches the very same packages.
    from workflows.wheelhouse import DISPLAY_PACKAGES
    return _apt_install(s, "install_screen_packages", DISPLAY_PACKAGES)


def _check_system_packages(s: MedicSetup) -> Tuple[bool, str]:
    from workflows.wheelhouse import APT_PACKAGES
    missing = _missing_debs(s, APT_PACKAGES)
    return not missing, ("installed" if not missing else "missing " + ", ".join(missing))


def _act_system_packages(s: MedicSetup) -> StepResult:
    from workflows.wheelhouse import APT_PACKAGES
    return _apt_install(s, "install_system_packages", APT_PACKAGES)


# -- Python ---------------------------------------------------------------------

def _installed_versions(s: MedicSetup, names) -> Dict[str, Optional[str]]:
    script = ("import importlib.metadata as m, json, sys\n"
              "out = {}\n"
              "for n in sys.argv[1:]:\n"
              "    try:\n"
              "        out[n] = m.version(n)\n"
              "    except m.PackageNotFoundError:\n"
              "        out[n] = None\n"
              "print(json.dumps(out))\n")
    out = s.out_of("python3 -c " + _q(script) + " " + " ".join(_q(n) for n in names))
    try:
        data = json.loads(out.splitlines()[-1]) if out else {}
    except ValueError:
        data = {}
    return data if isinstance(data, dict) else {}


def _check_python(s: MedicSetup) -> Tuple[bool, str]:
    pins = s.manifest["python"]["packages"]
    have = _installed_versions(s, list(pins))
    wrong = [f"{n} {have.get(n) or 'missing'} (wants {v})" for n, v in pins.items()
             if have.get(n) != v]
    if wrong:
        return False, f"{len(wrong)} of {len(pins)} not at Node Medic 1's version: " + \
            ", ".join(wrong[:6]) + (" ..." if len(wrong) > 6 else "")
    return True, f"{len(pins)} packages at Node Medic 1's versions"


def _refresh_user_site() -> None:
    """Let THIS process see packages pip just put in a user site that did not
    exist when it started: Field readiness freezes them for clones."""
    import importlib
    import site
    us = site.getusersitepackages()
    if os.path.isdir(us) and us not in sys.path:
        site.addsitedir(us)
    importlib.invalidate_caches()


def _act_python(s: MedicSetup) -> StepResult:
    # THE LITE IMAGE SHIPS NO pip3 until the system packages step: the clone's
    # own bootstrap (offline from Debian's pip wheel, else apt) finds one.
    from workflows.build import _ensure_pip
    pip_ok, pip_note = _ensure_pip(s.wf)
    if not pip_ok:
        return StepResult("install_python_stack", False, pip_note)
    req = os.path.join(s.path(CACHE_DIR), "requirements-medic.txt")
    os.makedirs(os.path.dirname(req), exist_ok=True)
    with open(req, "w", encoding="utf-8") as fh:
        fh.write(medic_requirements(
            s.manifest, os.path.join(s.tool_root, "assets", "requirements.txt")))
    code, out, err = s.conn.run(
        f"{s.wf.pip_cmd} install --user --break-system-packages --no-warn-script-location "
        f"-r {_q(req)}", timeout=3000)
    if code != 0:
        return StepResult("install_python_stack", False,
                          f"pip could not install the pinned packages ({_tail(err, out)})")
    if s.local:
        _refresh_user_site()
    return StepResult("install_python_stack", True,
                      f"installed from {req}{pip_note}")


def _rns_site(s: MedicSetup) -> str:
    """The site-packages folder RNS is imported from (found, not imported)."""
    return s.out_of("python3 -c 'import importlib.util, os; "
                    "spec = importlib.util.find_spec(\"RNS\"); "
                    "print(os.path.dirname(os.path.dirname(spec.origin)) if spec else \"\")'")


def _patch_state(s: MedicSetup) -> Tuple[Optional[bool], str, str]:
    """``(applied?, why, site dir)``: None when it cannot be told yet."""
    p = s.manifest["python"]["patch"]
    ver = _installed_versions(s, [p["package"]]).get(p["package"])
    if ver != p["version"]:
        return None, (f"{p['package']} is {ver or 'not installed'}; the patch is for "
                      f"{p['version']} (install the Python packages first)"), ""
    site_dir = _rns_site(s)
    if not site_dir:
        return None, "RNS cannot be imported", ""
    patch = os.path.join(s.tool_root, p["file"])
    code = s.conn.run(f"patch --dry-run -R -f -s -p{p['strip']} -d {_q(site_dir)} "
                      f"-i {_q(patch)}", timeout=60)[0]
    return code == 0, ("applied" if code == 0 else "not applied"), site_dir


def _check_rns_patch(s: MedicSetup) -> Tuple[bool, str]:
    applied, why, _site = _patch_state(s)
    return bool(applied), why


def _act_rns_patch(s: MedicSetup) -> StepResult:
    p = s.manifest["python"]["patch"]
    applied, why, site_dir = _patch_state(s)
    if applied is None:
        return StepResult("patch_reticulum", False, why)
    patch = os.path.join(s.tool_root, p["file"])
    if _sha256_file(patch) != p["sha256"]:
        return StepResult("patch_reticulum", False,
                          f"{p['file']} is not the patch the manifest pins (checksum differs)")
    code, out, err = s.conn.run(f"patch --forward -f -s -p{p['strip']} -d {_q(site_dir)} "
                                f"-i {_q(patch)}", timeout=60)
    if code != 0:
        return StepResult("patch_reticulum", False,
                          f"the patch did not apply to {site_dir} ({_tail(out, err)})")
    return StepResult("patch_reticulum", True,
                      "rnodeconf now names the Tracker, MeshPocket and EoRa-S3")


# -- touch, boot config, records ---------------------------------------------

def _check_touch(s: MedicSetup) -> Tuple[bool, str]:
    try:
        with open(s.path(KIVY_CONFIG), encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return False, "no Kivy config yet"
    if kivy_touch_cure(text) != text:
        return False, "Kivy's default config would put a second touch provider on the panel"
    return True, "one touch provider, pointer hidden"


def _act_touch(s: MedicSetup) -> StepResult:
    path = s.path(KIVY_CONFIG)
    if not os.path.isfile(path):
        # Kivy writes its default file on first import; make it do so now
        s.conn.run("KIVY_NO_ARGS=1 KIVY_NO_CONSOLELOG=1 KIVY_NO_FILELOG=1 "
                   "python3 -c 'import kivy.config'", timeout=180)
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return StepResult("touch_cure", False,
                          f"Kivy did not write {path} (is Kivy installed?)")
    cured = kivy_touch_cure(text)
    if cured != text:
        backup = path + ".bak-setup"
        if not os.path.exists(backup):
            with open(backup, "w", encoding="utf-8") as fh:
                fh.write(text)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(cured)
    return StepResult("touch_cure", True, "probesysfs off, pointer hidden")


def _card_helper_module():
    """assets/scripts/prepare_card.py, the root card helper: the boot-config
    lines a medic card gets live there, and so does the check for them."""
    from provisioning.pi_imager import PREPARE_CARD_SOURCE
    spec = importlib.util.spec_from_file_location("nm_prepare_card", PREPARE_CARD_SOURCE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _missing_boot_lines(s: MedicSetup) -> Tuple[List[str], str]:
    helper = _card_helper_module()
    code, text, _e = s.conn.run(f"cat {BOOT_CONFIG}")
    if code != 0:
        return list(helper.MEDIC_CONFIG_LINES), f"cannot read {BOOT_CONFIG}"
    missing = [ln for ln in helper.MEDIC_CONFIG_LINES
               if not helper._cfg_line_present(text or "", ln)]
    return missing, ""


def _check_boot_config(s: MedicSetup) -> Tuple[bool, str]:
    missing, why = _missing_boot_lines(s)
    if why:
        return False, why
    return not missing, ("UPS i2c and full USB current on" if not missing
                         else "missing " + ", ".join(missing))


def _act_boot_config(s: MedicSetup) -> StepResult:
    # THE MEDIC CARD'S OWN LINES (prepare_card.MEDIC_CONFIG_LINES): a clone's
    # card is baked with them by its parent; this card came from the
    # Raspberry Pi Imager, so they are added here, the same way.
    missing, why = _missing_boot_lines(s)
    if why:
        return StepResult("boot_config", False, why)
    block = ("\n[all]\n# Node Medic: UPS i2c + full USB current (as a medic card is baked)\n"
             + "\n".join(missing) + "\n")
    code, out, err = s.conn.run(s.priv(f"tee -a {BOOT_CONFIG}") +
                                f" >/dev/null <<'RNMEOF'\n{block}RNMEOF")
    if code != 0:
        return StepResult("boot_config", False, f"could not write {BOOT_CONFIG} ({_tail(err, out)})")
    return StepResult("boot_config", True,
                      f"added {', '.join(missing)} (takes effect after the restart)")


def _check_records(s: MedicSetup) -> Tuple[bool, str]:
    path = s.path(f"{clone.CLONE_DIR}/registry.json")
    return os.path.isfile(path), ("present" if os.path.isfile(path) else "no records file yet")


def _act_records(s: MedicSetup) -> StepResult:
    # What the clone's fresh-fleet road writes: an EMPTY registry, not none —
    # the app and final_verification both look for the file.
    path = s.path(f"{clone.CLONE_DIR}/registry.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("{}\n")
    return StepResult("start_records", True, "an empty set of records, ready for the first node")


# -- checks for the steps the clone itself performs -------------------------

def _check_identity(s: MedicSetup) -> Tuple[bool, str]:
    have = s.conn.run("test -f ~/.reticulum/storage/identity")[0] == 0
    if have:
        s.wf.fresh_identity_generated = True        # final_verification reads it
    return have, ("present" if have else "none yet")


def _check_autostart(s: MedicSetup) -> Tuple[bool, str]:
    missing = []
    enabled = s.out_of(f"systemctl is-enabled {KIOSK_UNIT} goodix-rebind.service")
    if enabled.split() != ["enabled", "enabled"]:
        missing.append("the kiosk and touch-retry units are not both enabled")
    unit = s.out_of(f"cat /etc/systemd/system/{KIOSK_UNIT}")
    if f"{s.home}/reticulum-tool/scripts/start_ui.sh" not in unit or "cage" not in unit:
        missing.append("the kiosk unit does not start this checkout under cage")
    for path, what in (("/etc/udev/rules.d/71-nodemedic-touch-only.rules", "touch-only rule"),
                       ("/etc/NetworkManager/conf.d/99-nodemedic-usb0.conf",
                        "NetworkManager rule for the cable-birth link"),
                       ("~/.icons/default/cursors/left_ptr", "empty pointer theme")):
        if s.conn.run(f"test -f {path}")[0] != 0:
            missing.append(f"no {what}")
    if s.conn.run("grep -q '^i2c-dev' /etc/modules")[0] != 0:
        missing.append("i2c-dev not loaded at boot")
    return not missing, ("boots into Node Medic" if not missing else "; ".join(missing))


def _boot_order(config_text: str) -> str:
    m = re.search(r"(?m)^BOOT_ORDER=(\S+)", config_text or "")
    return m.group(1).lower() if m else ""


def _check_bootorder(s: MedicSetup) -> Tuple[bool, str]:
    now = _boot_order(s.out_of("rpi-eeprom-config 2>/dev/null") or s.out_of(
        s.priv("rpi-eeprom-config") + " 2>/dev/null"))
    if now == clone.RECOVERY_BOOT_ORDER:
        return True, f"BOOT_ORDER={clone.RECOVERY_BOOT_ORDER}"
    # applied but not yet restarted: the staged image already carries it
    staged = _boot_order(s.out_of("rpi-eeprom-config /boot/firmware/pieeprom.upd 2>/dev/null"))
    if staged == clone.RECOVERY_BOOT_ORDER:
        return True, "baked; the boot chip takes it at the next restart"
    return False, (f"BOOT_ORDER is {now}" if now else "could not read the boot chip")


def _check_card_helper(s: MedicSetup) -> Tuple[bool, str]:
    from provisioning.pi_imager import PREPARE_CARD, PREPARE_CARD_SOURCE
    same = s.conn.run(f"cmp -s {_q(PREPARE_CARD_SOURCE)} {PREPARE_CARD}")[0] == 0
    return same, ("installed, same as the repository's" if same else
                  "missing, or older than the repository's copy")


def _check_radio_helper(s: MedicSetup) -> Tuple[bool, str]:
    from workflows.medic_radio import RADIO_HELPER, RADIO_HELPER_SOURCE
    same = s.conn.run(f"cmp -s {_q(RADIO_HELPER_SOURCE)} {RADIO_HELPER}")[0] == 0
    return same, ("installed, same as the repository's" if same else
                  "missing, or older than the repository's copy")


def _check_ssh_key(s: MedicSetup) -> Tuple[bool, str]:
    have = s.conn.run("test -f ~/.ssh/id_ed25519 && test -f ~/.ssh/id_ed25519.pub")[0] == 0
    return have, ("present" if have else "none yet")


def _check_final(s: MedicSetup) -> Tuple[bool, str]:
    if not s.wf.fresh_identity_generated:
        _check_identity(s)
    r = clone.final_verification(s.wf)
    return r.success, ("every part a medic needs is in place" if r.success
                       else local_words(r.message))


# -- arduino-cli, cores, libraries -------------------------------------------

def _arduino_cli_version(s: MedicSetup) -> str:
    m = re.search(r"Version:\s*([0-9][0-9A-Za-z.+-]*)", s.out_of("arduino-cli version"))
    return m.group(1) if m else ""


def _check_arduino_cli(s: MedicSetup) -> Tuple[bool, str]:
    want = s.manifest["arduino"]["cli"]["version"]
    have = _arduino_cli_version(s)
    return have == want, (f"arduino-cli {have}" if have else "not installed") + \
        ("" if have == want else f" (wants {want})")


def _act_arduino_cli(s: MedicSetup) -> StepResult:
    cli = s.manifest["arduino"]["cli"]
    tarball = os.path.join(s.path(CACHE_DIR), os.path.basename(cli["url"]))
    ok, why = _download(s, cli["url"], tarball, cli["sha256"], timeout=1200)
    if not ok:
        return StepResult("install_arduino_cli", False, why)
    dest = s.path(cli["install_to"])
    bindir = os.path.dirname(dest)
    code, out, err = s.conn.run(
        f"mkdir -p {_q(bindir)} && tar -xzf {_q(tarball)} -C {_q(bindir)} arduino-cli "
        f"&& chmod 755 {_q(dest)}", timeout=120)
    if code != 0:
        return StepResult("install_arduino_cli", False, f"could not unpack it ({_tail(err, out)})")
    return StepResult("install_arduino_cli", True, f"arduino-cli {cli['version']} in {bindir}")


def _installed_cores(s: MedicSetup) -> Dict[str, str]:
    return parse_core_list(s.out_of("arduino-cli core list --json", timeout=120))


def _platform_local(s: MedicSetup) -> Dict[str, Tuple[str, List[str]]]:
    """``{core: (its platform.local.txt path, the lines it must hold)}``."""
    ar = s.manifest["arduino"]
    out = {}
    for core, lines in (ar.get("platform_local") or {}).items():
        packager, arch = core.split(":", 1)
        out[core] = (s.path(f"~/.arduino15/packages/{packager}/hardware/{arch}/"
                            f"{ar['cores'][core]}/platform.local.txt"), list(lines))
    return out


def _missing_platform_lines(path: str, lines: List[str]) -> List[str]:
    try:
        with open(path, encoding="utf-8") as fh:
            have = {ln.strip() for ln in fh}
    except OSError:
        have = set()
    return [ln for ln in lines if ln.strip() not in have]


def _check_cores(s: MedicSetup) -> Tuple[bool, str]:
    want = s.manifest["arduino"]["cores"]
    have = _installed_cores(s)
    wrong = [f"{c} {have.get(c) or 'missing'}" for c, v in want.items() if have.get(c) != v]
    if wrong:
        return False, "not at the pinned version: " + ", ".join(wrong)
    unfixed = [core for core, (path, lines) in _platform_local(s).items()
               if _missing_platform_lines(path, lines)]
    if unfixed:
        return False, "pinned, but without its platform.local.txt fix: " + ", ".join(unfixed)
    return True, "all at Node Medic 1's versions"


def _fetch_json(s: MedicSetup, url: str) -> dict:
    code, out, _err = s.conn.run(f"curl -fsSL --retry 3 {_q(url)}", timeout=300)
    try:
        return json.loads(out) if code == 0 else {}
    except ValueError:
        return {}


def _board_manager_urls(s: MedicSetup) -> Tuple[List[str], List[str]]:
    """The board-manager URLs for installing the cores, with any index that
    lacks an aarch64 tool swapped for a local copy that borrows one
    (index_with_standins). Returns ``(urls, what was borrowed)``."""
    ar = s.manifest["arduino"]
    urls = list(ar["board_manager_urls"])
    standins = ar.get("aarch64_tool_standins") or {}
    if not standins:
        return urls, []
    indexes = {u: _fetch_json(s, u) for u in urls}

    def defines(index, packager):
        return any(p.get("name") == packager for p in index.get("packages", []))
    changes = []
    for want, have in standins.items():
        target_url = next((u for u, ix in indexes.items()
                           if defines(ix, want.split(":", 1)[0])), None)
        donor = next((ix for ix in indexes.values()
                      if defines(ix, have.split(":", 1)[0])), None)
        if not target_url or donor is None:
            continue
        patched, done = index_with_standins(indexes[target_url], donor, {want: have})
        if not done:
            continue
        local = os.path.join(s.path(CACHE_DIR), os.path.basename(target_url))
        os.makedirs(os.path.dirname(local), exist_ok=True)
        with open(local, "w", encoding="utf-8") as fh:
            json.dump(patched, fh)
        indexes[target_url] = patched
        urls[urls.index(target_url)] = "file://" + local
        changes += done
    return urls, changes


def _act_cores(s: MedicSetup) -> StepResult:
    want = s.manifest["arduino"]["cores"]
    urls, borrowed = _board_manager_urls(s)
    flag = "--additional-urls " + _q(",".join(urls))
    code, out, err = s.conn.run(f"arduino-cli core update-index {flag}", timeout=900)
    if code != 0:
        return StepResult("install_arduino_cores", False,
                          f"arduino-cli could not read the board indexes ({_tail(err, out)})")
    have = _installed_cores(s)
    failed = []
    for core, ver in want.items():
        if have.get(core) == ver:
            continue
        code, out, err = s.conn.run(f"arduino-cli core install {_q(core + '@' + ver)} {flag}",
                                    timeout=3600)
        if code != 0:
            failed.append(f"{core}@{ver} ({_tail(err, out, n=160)})")
    if failed:
        return StepResult("install_arduino_cores", False, "could not install " + "; ".join(failed))
    # THE HELTEC nRF52 CORE'S UF2 STEP cannot run off Windows (manifest
    # platform_local_note): switched off the way the core's own board-manager
    # platform file has it, or no HT-n5262 compile reaches its DFU .zip.
    for core, (path, lines) in _platform_local(s).items():
        missing = _missing_platform_lines(path, lines)
        if not missing:
            continue
        if not os.path.isdir(os.path.dirname(path)):
            return StepResult("install_arduino_cores", False,
                              f"{core} is not where arduino-cli installs it ({os.path.dirname(path)})")
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("# Node Medic (scripts/setup_medic.py): see assets/medic_manifest.json\n"
                     + "\n".join(missing) + "\n")
    note = f" ({'; '.join(borrowed)})" if borrowed else ""
    return StepResult("install_arduino_cores", True, "cores installed" + note)


def _check_libraries(s: MedicSetup) -> Tuple[bool, str]:
    want = s.manifest["arduino"]["libraries"]
    have = parse_lib_list(s.out_of("arduino-cli lib list --json", timeout=120))
    wrong = [f"{n} {have.get(n) or 'missing'}" for n, v in want.items() if have.get(n) != v]
    return not wrong, (f"{len(want)} libraries at Node Medic 1's versions" if not wrong
                       else "not at the pinned version: " + ", ".join(wrong[:6])
                       + (" ..." if len(wrong) > 6 else ""))


def _act_libraries(s: MedicSetup) -> StepResult:
    ar = s.manifest["arduino"]
    s.conn.run("test -f ~/.arduino15/arduino-cli.yaml || arduino-cli config init", timeout=60)
    for key, value in (ar.get("config") or {}).items():
        code, out, err = s.conn.run(f"arduino-cli config set {_q(key)} {_q(value)}", timeout=60)
        if code != 0:
            return StepResult("install_arduino_libraries", False,
                              f"could not set {key} ({_tail(err, out)})")
    code, out, err = s.conn.run("arduino-cli lib update-index", timeout=600)
    if code != 0:
        return StepResult("install_arduino_libraries", False,
                          f"arduino-cli could not read the library index ({_tail(err, out)})")
    have = parse_lib_list(s.out_of("arduino-cli lib list --json", timeout=120))
    git = ar.get("library_git_sources") or {}
    failed = []
    for name, ver in ar["libraries"].items():
        if have.get(name) == ver:
            continue
        # --no-deps: a dependency would otherwise come at its NEWEST version;
        # every library is pinned here, dependencies included
        cmd = (f"arduino-cli lib install --git-url {_q(git[name])}" if name in git else
               f"arduino-cli lib install --no-deps {_q(name + '@' + ver)}")
        code, out, err = s.conn.run(cmd, timeout=900)
        if code != 0:
            failed.append(f"{name}@{ver} ({_tail(err, out, n=120)})")
    if failed:
        return StepResult("install_arduino_libraries", False,
                          "could not install " + "; ".join(failed))
    return StepResult("install_arduino_libraries", True, "libraries installed")


# -- firmware trees and the images built from them ------------------------------

def _tree_state(s: MedicSetup, tree: dict) -> Tuple[str, str]:
    """missing | unborn | pinned | carried | other — and what was found."""
    path = s.path(tree["path"])
    if s.conn.run(f"test -e {_q(path)}")[0] != 0:
        return "missing", "is not here yet"
    if s.conn.run(f"test -d {_q(path + '/.git')}")[0] != 0:
        # a tree a parent medic carried across (history-free by design)
        return "carried", "is here without git history, so it is not compared with the pin"
    head = s.out_of(f"git -C {_q(path)} rev-parse -q --verify HEAD")
    if not head:
        return "unborn", "is an unfinished fetch"
    if head == tree["commit"]:
        return "pinned", f"is at {head[:7]}"
    return "other", f"is at {head[:7]}, not the pinned {tree['commit'][:7]}"


def _check_trees(s: MedicSetup) -> Tuple[bool, str]:
    trees = s.manifest["firmware_trees"]
    states = [(t, *_tree_state(s, t)) for t in trees]
    bad = [f"{t['path']} {why}" for t, st, why in states if st not in ("pinned", "carried")]
    carried = [t["path"] for t, st, _w in states if st == "carried"]
    if bad:
        return False, "; ".join(bad)
    return True, (f"{len(trees)} trees at their pinned commits" if not carried else
                  f"pinned; carried without history: {', '.join(carried)}")


def _act_trees(s: MedicSetup) -> StepResult:
    fetched, left = [], []
    for tree in s.manifest["firmware_trees"]:
        state, why = _tree_state(s, tree)
        if state in ("pinned", "carried"):
            continue
        path = s.path(tree["path"])
        if state == "other":
            # never over someone's work: a tree at another commit stays as it is
            left.append(f"{tree['path']} {why}; it was left as it is. Move it aside "
                        "and run the setup again to fetch the pinned tree")
            continue
        p = _q(path)
        code, out, err = s.conn.run(
            f"mkdir -p {_q(os.path.dirname(path))} && git init -q {p} && "
            f"{{ git -C {p} remote remove origin >/dev/null 2>&1; true; }} && "
            f"git -C {p} remote add origin {_q(tree['repo'])} && "
            f"git -C {p} fetch -q --depth 1 origin {tree['commit']} && "
            f"git -C {p} checkout -q -B {_q(tree['branch'])} FETCH_HEAD", timeout=1200)
        if code != 0:
            left.append(f"{tree['path']} could not be fetched ({_tail(err, out, n=140)})")
        else:
            fetched.append(tree["path"])
    if left:
        return StepResult("fetch_firmware_trees", False, "; ".join(left))
    return StepResult("fetch_firmware_trees", True,
                      "fetched " + ", ".join(fetched) if fetched else "all present")


def _missing_files(s: MedicSetup, paths) -> List[str]:
    """Those of *paths* not on disk; a path with ``*`` in it is a pattern that
    must match at least one file."""
    import glob
    return [p for p in paths
            if not (glob.glob(s.path(p)) if "*" in p else os.path.isfile(s.path(p)))]


def _check_custom_boards(s: MedicSetup) -> Tuple[bool, str]:
    builds = custom_board_builds(s.manifest["prebuilt"]["rnode_boards"])
    missing = [b["name"] for b in builds if _missing_files(s, b["artefacts"])]
    return not missing, ("built: " + ", ".join(b["name"] for b in builds) if not missing
                         else "not built yet: " + ", ".join(missing))


def _act_custom_boards(s: MedicSetup) -> StepResult:
    built, failed = [], []
    for b in custom_board_builds(s.manifest["prebuilt"]["rnode_boards"]):
        if not _missing_files(s, b["artefacts"]):
            continue
        tree = s.path(b["tree"])
        if s.conn.run(f"test -d {_q(tree)}")[0] != 0:
            failed.append(f"{b['name']}: its firmware tree {b['tree']} is missing")
            continue
        code, out, err = s.conn.run(f"cd {_q(tree)} && nice -n 10 {b['command']}",
                                    timeout=3600)
        gone = _missing_files(s, b["artefacts"])
        if code != 0 or gone:
            failed.append(f"{b['name']}: the compile did not produce "
                          f"{', '.join(os.path.basename(g) for g in gone) or 'its image'} "
                          f"({_tail(err, out, n=160)})")
        else:
            built.append(b["name"])
    if failed:
        return StepResult("build_custom_boards", False, "; ".join(failed))
    return StepResult("build_custom_boards", True, "built " + ", ".join(built))


def _v4_artefacts() -> List[str]:
    from workflows import rnode_v4_rgb as v4
    return [v4.BUILD_BIN, v4.BUILD_BOOTLOADER, v4.BUILD_PARTITIONS]


def _check_v4(s: MedicSetup) -> Tuple[bool, str]:
    missing = _missing_files(s, _v4_artefacts())
    return not missing, ("built" if not missing else "not built yet")


def _act_v4(s: MedicSetup) -> StepResult:
    from workflows import rnode_v4_rgb as v4
    tree = next(t for t in s.manifest["firmware_trees"] if t["path"] == v4.FIRMWARE_DIR)
    if _tree_state(s, tree)[0] not in ("pinned", "carried"):
        # _ensure_source would otherwise clone UPSTREAM into the empty path —
        # not the medic's fork
        return StepResult("build_v4_colour_image", False,
                          f"{v4.FIRMWARE_DIR} is not the pinned tree yet (fetch the "
                          "firmware trees first)")
    # The workflow's own source patches and compile — but NOT its
    # _ensure_toolchain, whose unversioned `lib install`s would move pinned
    # libraries to their newest versions.
    wf = v4.HeltecV4RGBWorkflow(s.conn, build_timeout=3600)
    for stage in (wf._ensure_source, wf._build_firmware):
        r = stage()
        if not r.success:
            return StepResult("build_v4_colour_image", False, r.message)
    return StepResult("build_v4_colour_image", True,
                      "built with the NeoPixel, boot-error and birth-cry patches")


def _appstate(s: MedicSetup) -> dict:
    try:
        with open(s.path("~/.platformio/appstate.json"), encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _setting_word(value) -> str:
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value).strip().lower()


def _platform_version(s: MedicSetup, name: str) -> str:
    try:
        with open(s.path(f"~/.platformio/platforms/{name}/platform.json"),
                  encoding="utf-8") as fh:
            return str(json.load(fh).get("version", ""))
    except (OSError, ValueError):
        return ""


def _check_platformio(s: MedicSetup) -> Tuple[bool, str]:
    from workflows import rtnode_build as rb
    pio = s.manifest["platformio"]
    want_core = s.manifest["python"]["packages"]["platformio"]
    missing = []
    # read off the installed package, not by running pio: pio writes its own
    # state on every start, and a check must change nothing
    have_core = _installed_versions(s, ["platformio"]).get("platformio")
    if have_core != want_core or s.conn.run("command -v pio")[0] != 0:
        missing.append(f"PlatformIO {want_core} is not installed (found {have_core or 'none'})")
    settings = _appstate(s).get("settings", {})
    for k, v in pio["settings"].items():
        if _setting_word(settings.get(k, "")) != _setting_word(v):
            missing.append(f"setting {k} is not {v}")
    for name, ver in pio["platforms"].items():
        if _platform_version(s, name) != ver:
            missing.append(f"platform {name} {_platform_version(s, name) or 'missing'} "
                           f"(wants {ver})")
    project = s.path(rb.RTNODE_PROJECT_DIR)
    for env in pio_envs():
        d = os.path.join(project, ".pio", "libdeps", env)
        if not (os.path.isdir(d) and os.listdir(d)):
            missing.append(f"packages for {env}")
    return not missing, ("ready for every RTNode-2400 env" if not missing
                         else "; ".join(missing))


def _act_platformio(s: MedicSetup) -> StepResult:
    from workflows import rtnode_build as rb
    pio = s.manifest["platformio"]
    for k, v in pio["settings"].items():
        # telemetry off and no upgrade check: offline builds go quiet and
        # stop saying "check your Internet connection" (Node Medic 1, 2026-08-27)
        s.conn.run(f"pio settings set {_q(k)} {_q(v)}", timeout=60)
    for name, ver in pio["platforms"].items():
        if _platform_version(s, name) == ver:
            continue
        code, out, err = s.conn.run(f"pio pkg install -g -p {_q(f'platformio/{name}@{ver}')}",
                                    timeout=2400)
        if code != 0:
            return StepResult("install_platformio", False,
                              f"PlatformIO could not install {name}@{ver} ({_tail(err, out)})")
    project = s.path(rb.RTNODE_PROJECT_DIR)
    if s.conn.run(f"test -f {_q(project + '/platformio.ini')}")[0] != 0:
        return StepResult("install_platformio", False,
                          f"{rb.RTNODE_PROJECT_DIR} has no platformio.ini (fetch the "
                          "firmware trees first)")
    failed = []
    for env in pio_envs():
        code, out, err = s.conn.run(f"pio pkg install -d {_q(project)} -e {_q(env)}",
                                    timeout=2400)
        if code != 0:
            failed.append(f"{env} ({_tail(err, out, n=140)})")
    if failed:
        return StepResult("install_platformio", False,
                          "could not fetch the packages for " + "; ".join(failed))
    return StepResult("install_platformio", True, f"{len(pio_envs())} envs ready")


def _check_rtnode_builds(s: MedicSetup) -> Tuple[bool, str]:
    missing = [b["name"] for b in rtnode_builds(s.manifest)
               if _missing_files(s, [b["artefact"]])]
    return not missing, ("both toolchains proven" if not missing
                         else "not built yet: " + ", ".join(missing))


def _act_rtnode_builds(s: MedicSetup) -> StepResult:
    failed = []
    for b in rtnode_builds(s.manifest):
        if not _missing_files(s, [b["artefact"]]):
            continue
        code, out, err = s.conn.run(
            f"cd {_q(s.path(b['dir']))} && nice -n 10 {b['command']}", timeout=3600)
        if code != 0 or _missing_files(s, [b["artefact"]]):
            failed.append(f"{b['name']} ({_tail(err, out, n=160)})")
    if failed:
        return StepResult("build_rtnode_images", False, "did not build: " + "; ".join(failed))
    return StepResult("build_rtnode_images", True, "compiled; the toolchains work offline now")


# -- the OS image, the field caches, the world map --------------------------------

def _check_image(s: MedicSetup) -> Tuple[bool, str]:
    img = s.manifest["pi_os_image"]
    path = s.path(img["path"])
    if s.conn.run(f"test -f {_q(path)}")[0] != 0:
        return False, "not here"
    got = _remote_sha256(s, path)
    if got != img["sha256"]:
        return False, (f"{img['path']} is not the pinned {img['release']} image (its "
                       "SHA-256 differs) — left as it is; move it aside and run the "
                       "setup again to fetch the pinned one")
    return True, f"Raspberry Pi OS Lite {img['release']}, checksum verified"


def _act_image(s: MedicSetup) -> StepResult:
    img = s.manifest["pi_os_image"]
    path = s.path(img["path"])
    if s.conn.run(f"test -f {_q(path)}")[0] == 0:
        return StepResult("fetch_pi_os_image", False, _check_image(s)[1])
    ok, why = _download(s, img["url"], path, img["sha256"], timeout=7200)
    return StepResult("fetch_pi_os_image", ok,
                      f"Raspberry Pi OS Lite {img['release']} ({why})" if ok else why)


_FIELD_KEYS = ("rnode_firmware", "phone_apps", "wheels", "debs")


def _check_field(s: MedicSetup) -> Tuple[bool, str]:
    from workflows import carry
    from workflows.parent_freeze import PARENT_REQUIREMENTS
    from workflows.updater import PINNED_FIRMWARE, RNODE_UPDATE_DIR
    from workflows.wheelhouse import DEB_CACHE, WHEELHOUSE
    statuses = {st.key: st for st in carry.audit(s.conn)}
    missing = [statuses[k].name for k in _FIELD_KEYS if k in statuses and not statuses[k].carried]
    if s.out_of(f"cat {RNODE_UPDATE_DIR}/.rnm_bundle_version") != PINNED_FIRMWARE:
        missing.append(f"the pinned RNode firmware bundle {PINNED_FIRMWARE}")
    if not all(s.conn.run(f"test -s {DEB_CACHE}/{n}.list")[0] == 0 for n in ("display", "radio")):
        missing.append("the .deb plan for a fresh card")
    if s.conn.run(f"test -s {WHEELHOUSE}/{PARENT_REQUIREMENTS}")[0] != 0:
        missing.append("this medic's own package versions, frozen for its clones")
    return not missing, ("aboard" if not missing else "missing: " + "; ".join(dict.fromkeys(missing)))


def _act_field(s: MedicSetup) -> StepResult:
    # Settings > Field readiness > Prepare for the field, pressed from here:
    # the pinned RNode firmware, the phone apps, the wheelhouse, this medic's
    # own versions frozen for its clones (the patched rns among them) and the
    # .deb plan a clone installs offline.
    from workflows import carry
    rep = carry.carry_all(s.conn)
    ok = rep.online and not rep.failed
    return StepResult("prepare_for_the_field", ok, rep.message or
                      ("done" if ok else "Field readiness could not finish"))


def _world_map_progress(s: MedicSetup) -> Tuple[int, int]:
    from ui.map_download import carried_tile_count
    path = s.path(WORLD_MAP_FILE)
    # only an existing file: sqlite would CREATE an empty one on connect, and a
    # check must change nothing
    have = (carried_tile_count(path) or 0) if os.path.isfile(path) else 0
    return have, int(s.manifest["field"]["world_map_tiles"])


def _check_world_map(s: MedicSetup) -> Tuple[bool, str]:
    have, total = _world_map_progress(s)
    if have >= total:
        return True, f"all {total:,} tiles"
    running = s.out_of("systemctl is-active world-map-fill") == "active"
    return False, (f"{have:,} of {total:,} tiles; the background download is "
                   + ("running" if running else "not running"))


def _act_world_map(s: MedicSetup) -> StepResult:
    # Node Medic 1's own resumable fill, as a service: it carries on across
    # restarts and exits in seconds once the world is complete.
    src = os.path.join(s.tool_root, "scripts", "world-map-fill.service")
    with open(src, encoding="utf-8") as fh:
        unit = render_world_map_unit(fh.read(), s.user, s.home)
    code, out, err = s.conn.run(s.priv(f"tee {WORLD_MAP_UNIT}") +
                                f" >/dev/null <<'RNMUNIT'\n{unit}RNMUNIT")
    if code == 0:
        code, out, err = s.conn.run(s.priv("sh -c 'systemctl daemon-reload && "
                                           "systemctl enable --now world-map-fill'"), timeout=60)
    if code != 0:
        return StepResult("world_map", False, f"could not start the world map service "
                                              f"({_tail(err, out)})")
    have, total = _world_map_progress(s)
    return StepResult("world_map", True,
                      f"{have:,} of {total:,} tiles so far; it carries on by itself, "
                      "across restarts, until the world is complete", skipped=True)


# -- after the restart --------------------------------------------------------

def _check_tool_running(s: MedicSetup) -> Tuple[bool, str]:
    up = s.out_of(f"systemctl is-active {KIOSK_UNIT}") == "active"
    return up, ("running on its screen" if up else
                "starts on its screen at the next restart")


def _check_never(_s: MedicSetup) -> Tuple[bool, str]:
    return False, "not restarted by the setup"


def _act_restart(s: MedicSetup) -> StepResult:
    bad = [o for o in s.results if o.status in ("failed", "missing")]
    if not s.reboot:
        if s.out_of(f"systemctl is-active {KIOSK_UNIT}") == "active":
            return StepResult("restart", True, "Node Medic is already running",
                              skipped=True)
        return StepResult("restart", True, "restart to start Node Medic: sudo reboot",
                          skipped=True)
    if bad:
        return StepResult("restart", True, f"not restarting: {len(bad)} step(s) above "
                                           "need attention first", skipped=True)
    code, out, err = s.conn.run(
        f"bash {_q(os.path.join(s.tool_root, 'scripts', 'ui_busy_guard.sh'))}")
    if code != 0:
        return StepResult("restart", True, f"not restarting: {_tail(err, out)}", skipped=True)
    # the clone's own restart: a reboot three seconds from now, detached
    clone.restart_into_tool(s.wf)
    return StepResult("restart", True,
                      "restarting in three seconds; Node Medic opens on its screen")


SETUP_STEPS: List[SetupStep] = [
    SetupStep("check_pi5", "Check this is a Raspberry Pi 5", _check_pi5,
              clone_fn=clone.verify_target_pi5, fatal=True),
    SetupStep("check_system", "Check the system: 64-bit Raspberry Pi OS Lite (trixie), "
              "Python 3.13, the medic's own user", _check_system, fatal=True),
    SetupStep("check_checkout", "Check Node Medic is at ~/reticulum-tool",
              _check_checkout, fatal=True),
    SetupStep("use_network_time", "Set the clock from the internet", _check_clock,
              _act_clock, run_only=True),
    SetupStep("install_screen_packages", "Install the screen packages (cage kiosk, "
              "libGL, Xwayland, SDL2, wlr-randr)", _check_screen_packages,
              _act_screen_packages, internet=True, root=True, needs_mb=400),
    SetupStep("install_system_packages", "Install the tool packages (git, pip, gpsd, "
              "uhubctl, rpiboot, sshpass, Dire Wolf ...)", _check_system_packages,
              _act_system_packages, internet=True, root=True, needs_mb=600),
    SetupStep("install_python_stack", "Install the Python packages at Node Medic 1's "
              "versions", _check_python, _act_python, internet=True, needs_mb=700),
    SetupStep("patch_reticulum", "Apply the medic's Reticulum patch (names the Tracker, "
              "MeshPocket, EoRa-S3)", _check_rns_patch, _act_rns_patch),
    SetupStep("touch_cure", "Give the touchscreen one input provider and hide the pointer",
              _check_touch, _act_touch),
    SetupStep("boot_config", "Turn on the UPS gauge's I2C and full USB current",
              _check_boot_config, _act_boot_config, root=True),
    SetupStep("start_records", "Start an empty set of records", _check_records, _act_records),
    SetupStep("generate_fresh_identity", "Give the medic its own Reticulum identity",
              _check_identity, clone.generate_fresh_identity,
              clone_fn=clone.generate_fresh_identity),
    SetupStep("configure_autostart", "Boot straight into Node Medic (cage kiosk, touch "
              "retry, cable-birth network rule)", _check_autostart,
              clone.configure_autostart, clone_fn=clone.configure_autostart, root=True),
    SetupStep("bake_recovery_bootorder", "Bake the recovery boot order into the boot chip",
              _check_bootorder, clone.bake_recovery_bootorder,
              clone_fn=clone.bake_recovery_bootorder, root=True),
    SetupStep("install_card_helper", "Install the root card-writing helper",
              _check_card_helper, clone.install_card_helper,
              clone_fn=clone.install_card_helper, root=True),
    SetupStep("install_radio_helper", "Install the root radio set-up helper",
              _check_radio_helper, clone.install_radio_helper,
              clone_fn=clone.install_radio_helper, root=True),
    SetupStep("ensure_ssh_keypair", "Give the medic its own SSH key", _check_ssh_key,
              clone.ensure_ssh_keypair, clone_fn=clone.ensure_ssh_keypair),
    SetupStep("install_arduino_cli", "Install arduino-cli (pinned, checksum checked)",
              _check_arduino_cli, _act_arduino_cli, internet=True, needs_mb=100),
    SetupStep("install_arduino_cores", "Install the board cores (ESP32 and three nRF52)",
              _check_cores, _act_cores, internet=True, needs_mb=4000),
    SetupStep("install_arduino_libraries", "Install the Arduino libraries at Node Medic "
              "1's versions", _check_libraries, _act_libraries, internet=True, needs_mb=200),
    SetupStep("fetch_firmware_trees", "Fetch the firmware sources at their pinned commits",
              _check_trees, _act_trees, internet=True, needs_mb=400),
    SetupStep("build_custom_boards", "Build the Tracker, EoRa-S3 and MeshPocket radio images",
              _check_custom_boards, _act_custom_boards, needs_mb=300),
    SetupStep("build_v4_colour_image", "Build the Heltec V4 colour image",
              _check_v4, _act_v4, needs_mb=200),
    SetupStep("install_platformio", "Prepare PlatformIO for RTNode-2400 builds",
              _check_platformio, _act_platformio, internet=True, needs_mb=3000),
    SetupStep("build_rtnode_images", "Compile RTNode-2400 once on each toolchain",
              _check_rtnode_builds, _act_rtnode_builds, internet=True, needs_mb=600),
    SetupStep("fetch_pi_os_image", "Fetch the Raspberry Pi OS Lite image for SD cards",
              _check_image, _act_image, internet=True, needs_mb=600),
    SetupStep("prepare_for_the_field", "Fetch the field caches (RNode firmware, phone "
              "apps, wheels, .debs)", _check_field, _act_field, internet=True,
              needs_mb=1200),
    SetupStep("world_map", "Download the world map", _check_world_map, _act_world_map,
              root=True, needs_mb=1500, background=True),
    SetupStep("final_verification", "Check the finished medic", _check_final,
              clone_fn=clone.final_verification),
    SetupStep("tool_running", "Node Medic running on its screen", _check_tool_running,
              after_restart=True),
    SetupStep("restart", "Restart into Node Medic", _check_never, _act_restart,
              run_only=True, trust_act=True),
]


@dataclass(frozen=True)
class Route:
    """How a medic set up from GitHub gets what one clone step gives a clone."""
    #: same — the clone's own function runs here, on this machine;
    #: stands_in — a step here does the parent's part, from the internet;
    #: not_here — nothing to do without a parent, and why.
    kind: str
    steps: Tuple[str, ...] = ()
    why: str = ""


#: Every step of the clone's ladder, accounted for. tests/test_medic_setup.py
#: fails when the clone gains a step this table does not name, so the GitHub
#: route cannot quietly fall behind the clone again.
CLONE_ROUTE: Dict[str, Route] = {
    "verify_target_pi5": Route("same", ("check_pi5",)),
    "carry_the_time": Route("stands_in", ("use_network_time",),
                            "the internet's clock (NTP) instead of the parent's"),
    "transfer_tool": Route("stands_in", ("check_checkout", "prepare_for_the_field"),
                           "the repository is already a git checkout; the carried "
                           "asset store (wheels, .debs, phone apps) is fetched"),
    "transfer_firmware_cache": Route("stands_in", ("prepare_for_the_field",),
                                     "the pinned RNode firmware bundle from GitHub"),
    "carry_the_toolchain": Route("stands_in", (
        "install_arduino_cli", "install_arduino_cores", "install_arduino_libraries",
        "fetch_firmware_trees", "build_custom_boards", "build_v4_colour_image",
        "install_platformio", "build_rtnode_images", "fetch_pi_os_image"),
        "each carried tree fetched at the manifest's pin, and its images built"),
    "install_dependencies": Route("stands_in", ("install_python_stack", "patch_reticulum"),
                                  "Node Medic 1's own versions from PyPI, then its "
                                  "Reticulum patch"),
    "carry_touch_cure": Route("stands_in", ("touch_cure",),
                              "Kivy's own default file with the two changes the "
                              "clone's code names: probesysfs off, pointer hidden"),
    "install_display_stack": Route("stands_in", ("install_screen_packages",),
                                   "the same DISPLAY_PACKAGES, from apt"),
    "install_carried_packages": Route("stands_in", ("install_system_packages",),
                                      "the same APT_PACKAGES, from apt"),
    "copy_monitoring_db": Route("stands_in", ("start_records",),
                                "an empty registry, as the clone's fresh-fleet road writes"),
    "copy_offline_maps": Route("stands_in", ("world_map",),
                               "the world map downloaded rather than handed down; the "
                               "keeper's own area is MAPS > Download offline map"),
    "copy_kin_roster": Route("not_here", why="a medic set up from GitHub has no fleet to "
                                             "inherit"),
    "generate_fresh_identity": Route("same", ("generate_fresh_identity",)),
    "stamp_lineage": Route("not_here", why="there is no parent to name; About reads the "
                                           "git checkout"),
    "record_child_trust": Route("not_here", why="it writes the PARENT's trust store and "
                                                "hands over the parent's signing key; "
                                                "there is no parent"),
    "configure_autostart": Route("same", ("configure_autostart",)),
    "bake_recovery_bootorder": Route("same", ("bake_recovery_bootorder",)),
    "install_card_helper": Route("same", ("install_card_helper",)),
    "install_radio_helper": Route("same", ("install_radio_helper",)),
    "harden_new_medic": Route("not_here", why="a clone's lock-down is proved from "
                              "OUTSIDE, by the parent's fresh logins; a medic set up "
                              "from GitHub has no parent, so its keeper applies the "
                              "same scripts by hand (provisioning/security/README.md)"),
    "remove_parent_key": Route("not_here", why="no parent ever held a key to this "
                               "medic"),
    "ensure_ssh_keypair": Route("same", ("ensure_ssh_keypair",)),
    "final_verification": Route("same", ("final_verification",)),
    "restart_into_tool": Route("stands_in", ("restart",),
                               "--reboot runs the clone's own restart; without it the "
                               "setup ends by saying to restart"),
    "confirm_tool_running": Route("stands_in", ("tool_running",),
                                  "--check after the restart says whether it is running"),
}


#: Setup steps that answer to no clone step, and why each exists.
SETUP_ONLY: Dict[str, str] = {
    "check_system": "a clone's card is written by its parent from the pinned image; "
                    "this card was written by hand, so its system, Python and user "
                    "are checked before anything is installed",
    "boot_config": "a clone's card is baked with these config.txt lines by its parent "
                   "(prepare_card MEDIC_CONFIG_LINES); this card came from the "
                   "Raspberry Pi Imager",
}


# --------------------------------------------------------------------------- #
# The entry point scripts/setup_medic.py calls.
# --------------------------------------------------------------------------- #

class _LoggedConnection(Connection):
    """The real LocalConnection, with every command and the tail of its output
    appended to LOG_PATH: a failure can be read afterwards, line by line."""

    def __init__(self, inner: Connection, log_path: str):
        self.inner = inner
        self.log_path = log_path

    def _log(self, text: str) -> None:
        try:
            with open(self.log_path, "a", encoding="utf-8") as fh:
                fh.write(text)
        except OSError:
            pass

    def run(self, command: str, timeout: int = 30):
        t0 = time.time()
        code, out, err = self.inner.run(command, timeout)
        self._log(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] $ {command[:600]}\n"
                  f"exit {code} after {time.time() - t0:.0f}s\n"
                  + ((out or "")[-3000:] + ("\n" if out else ""))
                  + ((err or "")[-3000:] + ("\n" if err else "")))
        return code, out, err

    def push_file(self, local_path: str, remote_path: str) -> bool:
        return self.inner.push_file(local_path, remote_path)

    def push_tree(self, local_dir: str, remote_dir: str, exclude=()) -> bool:
        return self.inner.push_tree(local_dir, remote_dir, exclude=exclude)


def run_setup(check_only: bool = False, reboot: bool = False) -> int:
    """Set up (or ``--check``) THIS machine. Returns the process exit code."""
    from transport.connection import LocalConnection
    conn = _LoggedConnection(LocalConnection(), os.path.expanduser(LOG_PATH))
    setup = MedicSetup(conn, check_only=check_only, reboot=reboot, local=True)
    if setup.local:
        _refresh_user_site()
    setup._write(("Checking this Node Medic against " if check_only else
                  "Setting up this Raspberry Pi as a Node Medic, from ")
                 + os.path.relpath(MANIFEST_PATH, TOOL_ROOT)
                 + (" (nothing will be changed).\n" if check_only else
                    ". Finished steps are skipped; it can take an hour or more.\n"))
    try:
        return setup.run_all()
    except KeyboardInterrupt:
        setup._write("\nStopped. Run the same command again to carry on: finished "
                     "steps are skipped.\n")
        return 130
