"""A medic set up from GitHub alone must do everything Node Medic 1 does.

The keeper, 2026-10-08: "Anything cloned needs to be able to do everything that
the original did ... And anybody downloading it from GitHub needs to have all
that functionality as well." scripts/setup_medic.py is that route: the clone's
ladder with the internet standing in for the parent (workflows/medic_setup.py),
installing from one manifest (assets/medic_manifest.json).

These tests need no network and no Pi. They hold three things in place:

* the manifest is complete — every firmware folder the code builds from has a
  pinned source, every board BUILD flashes from a pre-built image has a build
  recipe, and every pin parses and agrees with the code that uses it;
* the setup stays aligned with the clone — every clone step is accounted for,
  and where the setup runs a clone step it runs the clone's own function;
* the run is honest — --check changes nothing, a refusal stops, a step that
  cannot start says why, finished work is never redone or overwritten.
"""
import copy
import hashlib
import io
import json
import os
import re

import pytest

from monitor.registry import NodeRegistry
from transport.connection import EmulatedConnection
from workflows import clone
from workflows import medic_setup as ms

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
MANIFEST = ms.load_manifest()
TREES = {t["path"]: t for t in MANIFEST["firmware_trees"]}


def _under_a_tree(path):
    return any(path == root or path.startswith(root.rstrip("/") + "/") for root in TREES)


# --------------------------------------------------------------------------- #
# The manifest is complete.
# --------------------------------------------------------------------------- #

def test_the_manifest_has_every_section():
    for key in ("system", "pi_os_image", "python", "arduino", "firmware_trees",
                "prebuilt", "platformio", "field"):
        assert key in MANIFEST, key
    assert MANIFEST["system"]["board"] == "Raspberry Pi 5"
    assert MANIFEST["system"]["tool_dir"] == clone.REMOTE_TOOL_DIR


def test_every_firmware_folder_the_code_builds_from_has_a_pinned_source():
    """The same set the clone parity test holds the clone to: a folder the code
    builds from that the manifest does not name is a board a GitHub medic
    cannot build."""
    from workflows import (rnode_boards, rnode_flash, rnode_nrf52_rgb,
                           rnode_v4_rgb, rtnode_build)
    need = {rnode_flash.TRACKER_BUILD_DIR, rnode_boards.DEFAULT_FIRMWARE_DIR,
            rnode_v4_rgb.FIRMWARE_DIR, rnode_nrf52_rgb.FIRMWARE_DIR,
            rtnode_build.RTNODE_PROJECT_DIR, rtnode_build.TECHO_PROJECT_DIR}
    need |= {b.build_dir for b in rnode_boards.RNODE_BOARDS.values()
             if getattr(b, "build_dir", "")}
    missing = sorted(p for p in need if not _under_a_tree(p))
    assert not missing, f"no pinned source for: {missing}"


def test_every_pinned_tree_is_one_a_clone_carries_without_history():
    """One architecture: what the GitHub route fetches is what a clone
    receives from its parent."""
    roots = [r for r, _why, _req in clone.CARRIED_TREES]
    for path in TREES:
        assert any(path == r or path.startswith(r + "/") for r in roots), path
        assert any(path == r or path.startswith(r + "/")
                   for r in clone.HISTORY_FREE_TREES), path


def test_every_board_built_here_has_a_build_recipe():
    from workflows import rnode_boards
    custom = {b.key for b in rnode_boards.custom_boards()}
    assert custom == set(MANIFEST["prebuilt"]["rnode_boards"])
    for b in ms.custom_board_builds(MANIFEST["prebuilt"]["rnode_boards"]):
        board = rnode_boards.get_board(b["key"])
        assert b["tree"] in TREES, b
        assert b["command"] == board.compile_command()
        assert "arduino-cli compile" in b["command"] and " -e " in b["command"]
        for art in b["artefacts"]:
            assert art.startswith(b["tree"] + "/build/"), art
        if board.flash_method == "serial_dfu":
            assert b["artefacts"] == [f"{board.build_dir}/{board.dfu_package}"]
        else:
            sketch = board.sketch or "RNode_Firmware.ino"
            assert {os.path.basename(a) for a in b["artefacts"]} == {
                f"{sketch}.bin", f"{sketch}.bootloader.bin", f"{sketch}.partitions.bin"}


def test_each_tree_builds_the_sketch_its_folder_is_named_for():
    """arduino-cli compiles the sketch named after its folder; the Tracker's
    tree holds RNode_Firmware.ino, the two CE trees RNode_Firmware_CE.ino."""
    from workflows import rnode_boards
    for b in ms.custom_board_builds(MANIFEST["prebuilt"]["rnode_boards"]):
        board = rnode_boards.get_board(b["key"])
        sketch = board.sketch or (board.dfu_package[:-len(".ino.zip")] + ".ino"
                                  if board.dfu_package else "RNode_Firmware.ino")
        assert os.path.basename(b["tree"]) + ".ino" == sketch, b


def test_the_v4_colour_image_builds_from_the_pinned_medic_cross_tree():
    from workflows import rnode_v4_rgb as v4
    assert MANIFEST["prebuilt"]["v4_colour"] is True
    tree = TREES[v4.FIRMWARE_DIR]
    assert tree["branch"] == "medic-cross"
    for art in ms._v4_artefacts():
        assert art.startswith(v4.FIRMWARE_DIR + "/build/")


_PEP440 = re.compile(r"^\d+(\.\d+)*((a|b|rc)\d+)?(\.post\d+)?(\.dev\d+)?$")


def test_every_pin_parses():
    for name, ver in MANIFEST["python"]["packages"].items():
        assert re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", name), name
        assert _PEP440.match(ver), (name, ver)
    ar = MANIFEST["arduino"]
    assert re.fullmatch(r"\d+\.\d+\.\d+", ar["cli"]["version"])
    assert re.fullmatch(r"[0-9a-f]{64}", ar["cli"]["sha256"])
    for core, ver in ar["cores"].items():
        assert re.fullmatch(r"[A-Za-z0-9_]+:[A-Za-z0-9_]+", core), core
        assert re.fullmatch(r"\d+\.\d+\.\d+", ver), (core, ver)
    for lib, ver in ar["libraries"].items():
        assert lib.strip() and re.fullmatch(r"\d+(\.\d+)+", ver), (lib, ver)
    for url in ar["board_manager_urls"] + [ar["cli"]["url"], MANIFEST["pi_os_image"]["url"]]:
        assert url.startswith("https://"), url
    for src in ar["library_git_sources"].values():
        assert re.fullmatch(r"https://github\.com/\S+#[0-9a-f]{40}", src), src
    for t in MANIFEST["firmware_trees"]:
        assert re.fullmatch(r"[0-9a-f]{40}", t["commit"]), t
        assert t["repo"].startswith("https://github.com/") and t["repo"].endswith(".git"), t
        assert t["branch"] and t["path"].startswith("~/"), t
    assert re.fullmatch(r"[0-9a-f]{64}", MANIFEST["pi_os_image"]["sha256"])
    assert re.fullmatch(r"[0-9a-f]{64}", MANIFEST["python"]["patch"]["sha256"])
    for name, ver in MANIFEST["platformio"]["platforms"].items():
        assert re.fullmatch(r"\d+\.\d+\.\d+", ver), (name, ver)


def test_the_rtnode_tree_pins_agree_with_the_build_workflow():
    from workflows import rtnode_build as rb
    t = TREES[rb.RTNODE_PROJECT_DIR]
    assert t["repo"] == rb.RTNODE_REPO_URL and t["branch"] == rb.RTNODE_BRANCH
    assert TREES[rb.TECHO_PROJECT_DIR]["repo"] == rb.RTNODE_REPO_URL
    assert MANIFEST["prebuilt"]["rtnode_proof_env"] in ms.pio_envs()
    assert rb.RTNODE_BUILD_ENV in ms.pio_envs()


def test_the_rtnode_builds_cover_every_nrf52_target():
    from workflows import rtnode_build as rb
    builds = ms.rtnode_builds(MANIFEST)
    nrf = [t for t in rb.RTNODE_TARGETS.values() if t.mechanism == "nrf_dfu"]
    assert len(builds) == 1 + len(nrf)
    # the tree's extra_script.py names the image rtnode_<custom_variant>: the
    # check is for the env's linked image, whatever it is called
    assert builds[0]["artefact"] == (f"{rb.RTNODE_PROJECT_DIR}/.pio/build/"
                                     f"{MANIFEST['prebuilt']['rtnode_proof_env']}/*.elf")
    for t in nrf:
        b = next(b for b in builds if b["command"] == f"make {t.build_env}")
        assert b["dir"] == rb.TECHO_PROJECT_DIR
        assert b["artefact"].endswith("-noalloc/RNode_Firmware.ino.zip")


def test_the_arduino_pins_agree_with_the_code_that_uses_them():
    from workflows import rnode_flash, rnode_v4_rgb as v4
    esp = MANIFEST["arduino"]["cores"]["esp32:esp32"]
    assert v4.ESP32_CORE == f"esp32:esp32@{esp}"
    assert f"/{esp}/" in rnode_flash.TRACKER_BOOT_APP0 and f"/{esp}/" in v4.BOOT_APP0
    libs = MANIFEST["arduino"]["libraries"]
    for lib in v4.ARDUINO_LIBS:
        assert lib in libs, f"{lib} is installed by the V4 build but not pinned"
    for name in MANIFEST["arduino"]["library_git_sources"]:
        assert name in libs
    # every board's own core is pinned
    from workflows import rnode_boards
    for b in rnode_boards.custom_boards():
        assert ":".join(b.fqbn.split(":")[:2]) in MANIFEST["arduino"]["cores"], b.fqbn


def test_the_rak_stand_in_is_for_a_pinned_core():
    cores = MANIFEST["arduino"]["cores"]
    for want, have in MANIFEST["arduino"]["aarch64_tool_standins"].items():
        assert want.split(":")[0] in {c.split(":")[0] for c in cores}
        assert want.split(":", 1)[1] == have.split(":", 1)[1]


def test_the_field_pins_agree_with_the_code():
    from ui.map_download import estimate_world
    from ui.map_tiles import MAPS_DIR
    from workflows.updater import PINNED_FIRMWARE
    assert MANIFEST["field"]["rnode_firmware"] == PINNED_FIRMWARE
    assert MANIFEST["field"]["world_map_tiles"] == estimate_world()[0]
    assert os.path.dirname(os.path.expanduser(ms.WORLD_MAP_FILE)) == MAPS_DIR


def test_the_os_image_is_the_release_the_clones_package_plan_is_made_for():
    from provisioning.pi_imager import IMAGE_CANDIDATES
    from workflows.wheelhouse import CLONE_BASE_ISSUE
    img = MANIFEST["pi_os_image"]
    assert img["release"] in CLONE_BASE_ISSUE
    assert os.path.expanduser(img["path"]) == IMAGE_CANDIDATES[0]
    assert os.path.basename(img["url"]).startswith(img["release"])
    assert img["url"].endswith("-arm64-lite.img.xz")


def test_the_reticulum_patch_is_the_pinned_one_and_touches_only_rns():
    p = MANIFEST["python"]["patch"]
    data = open(os.path.join(ROOT, p["file"]), "rb").read()
    assert hashlib.sha256(data).hexdigest() == p["sha256"]
    text = data.decode()
    targets = re.findall(r"(?m)^\+\+\+ (\S+)", text)
    assert targets and all(t.split("/", p["strip"])[-1].startswith("RNS/") for t in targets)
    for marker in ("PRODUCT_HELTEC_WIRELESS_TRACKER = 0xCB", "0xD2:", "0xD3:",
                   "serial_detect_timeout"):
        assert marker in text, marker
    assert MANIFEST["python"]["packages"]["rns"] == p["version"]


def test_the_medic_installs_rns_137_while_nodes_keep_138():
    """rns 1.3.8 from PyPI cannot name the Tracker (Node Medic 2, 2026-10-06):
    the medic's own pins say 1.3.7 + the patch. Nodes install the newest rns
    in the wheelhouse, which assets/requirements.txt still fills with 1.3.8,
    as on Node Medic 1."""
    req = os.path.join(ROOT, "assets", "requirements.txt")
    text = ms.medic_requirements(MANIFEST, req)
    lines = [ln for ln in text.splitlines() if ln and not ln.startswith("#")]
    assert "rns==1.3.7" in lines and "rns==1.3.8" not in lines
    for extra in ("Pillow==12.2.0", "smbus2==0.6.1", "cryptography"):
        assert extra in lines, extra
    assert "rns==1.3.8" in open(req).read()
    # requirements.txt and the manifest never disagree on a version both pin
    from workflows.parent_freeze import manifest_lines, norm_name
    pins = {norm_name(n): v for n, v in MANIFEST["python"]["packages"].items()}
    for line in manifest_lines(req):
        name, _sep, ver = line.partition("==")
        if ver and norm_name(name) in pins and norm_name(name) != "rns":
            assert pins[norm_name(name)] == ver, line


def test_the_python_pins_carry_the_tools_the_medic_shells_out_to():
    pins = MANIFEST["python"]["packages"]
    for name in ("rns", "lxmf", "Kivy", "platformio", "esptool", "adafruit-nrfutil", "segno"):
        assert name in pins, name
    assert MANIFEST["arduino"]["cli"]["version"] == "1.5.1"


# --------------------------------------------------------------------------- #
# The setup stays aligned with the clone.
# --------------------------------------------------------------------------- #

def _clone_step_names():
    return [n for n, _f in clone.CloneWorkflow(EmulatedConnection(), NodeRegistry()).steps]


def test_every_clone_step_is_accounted_for():
    """A step added to the clone must say how a GitHub medic gets the same
    thing (CLONE_ROUTE), or this fails: the two routes cannot drift apart."""
    names = _clone_step_names()
    assert set(names) == set(ms.CLONE_ROUTE), (
        f"unaccounted: {sorted(set(names) - set(ms.CLONE_ROUTE))}; "
        f"no longer in the clone: {sorted(set(ms.CLONE_ROUTE) - set(names))}")
    for name, route in ms.CLONE_ROUTE.items():
        assert route.kind in ("same", "stands_in", "not_here"), name
        assert route.steps or route.kind == "not_here", name
        assert route.kind == "same" or len(route.why) > 20, name


def test_where_the_setup_does_a_clone_step_it_runs_the_clones_own_function():
    steps = {s.name: s for s in ms.SETUP_STEPS}
    for name, route in ms.CLONE_ROUTE.items():
        for step_name in route.steps:
            assert step_name in steps, (name, step_name)
        if route.kind != "same":
            continue
        (step_name,) = route.steps
        step = steps[step_name]
        assert step.clone_fn is getattr(clone, name), name
        assert step.act in (None, step.clone_fn), name


def test_every_setup_step_answers_to_a_clone_step_or_says_why_not():
    named = {s for r in ms.CLONE_ROUTE.values() for s in r.steps}
    extra = {s.name for s in ms.SETUP_STEPS} - named
    assert extra == set(ms.SETUP_ONLY), extra
    assert all(len(why) > 30 for why in ms.SETUP_ONLY.values())


def test_the_reused_steps_keep_the_clones_order():
    order = _clone_step_names()
    reused = [s.clone_fn.__name__ for s in ms.SETUP_STEPS if s.clone_fn]
    assert reused == sorted(reused, key=order.index)


def _apt_commands(step_name, packages):
    c = EmulatedConnection(default_code=0, default_stdout="")
    s = ms.MedicSetup(c, home="/home/pi", stream=io.StringIO())
    step = next(st for st in ms.SETUP_STEPS if st.name == step_name)
    r = step.act(s)
    assert r.success
    installs = [h for h in c.history if "apt-get" in h and " install " in h]
    assert len(installs) == 1
    assert installs[0].split("--no-install-recommends ")[1].split() == list(packages)


def test_the_package_steps_install_exactly_the_clones_package_sets():
    from workflows.wheelhouse import APT_PACKAGES, DISPLAY_PACKAGES
    _apt_commands("install_screen_packages", DISPLAY_PACKAGES)
    _apt_commands("install_system_packages", APT_PACKAGES)


# --------------------------------------------------------------------------- #
# Pure helpers.
# --------------------------------------------------------------------------- #

KIVY_DEFAULT = """[kivy]
config_version = 27

[graphics]
fullscreen = 0
show_cursor = 1

[input]
mouse = mouse
%(name)s = probesysfs

[postproc]
double_tap_time = 250
"""


def test_the_touch_cure_takes_probesysfs_off_and_hides_the_pointer():
    cured = ms.kivy_touch_cure(KIVY_DEFAULT)
    assert "#%(name)s = probesysfs" in cured
    assert "show_cursor = 0" in cured and "show_cursor = 1" not in cured
    assert "mouse = mouse" in cured            # touch_input drops it at start-up
    assert "double_tap_time = 250" in cured
    assert ms.kivy_touch_cure(cured) == cured  # idempotent
    other = "[postproc]\nx = probesysfs\n"
    assert ms.kivy_touch_cure(other) == other  # only the [input] section


def test_the_rak_index_borrows_the_aarch64_nrfjprog():
    host = {"host": "aarch64-linux-gnu", "url": "https://x/nrfjprog-10.15.0-arm.tar.bz2",
            "archiveFileName": "nrfjprog-10.15.0-arm.tar.bz2", "checksum": "MD5:00",
            "size": "1"}
    rak = {"packages": [{"name": "rakwireless", "tools": [
        {"name": "nrfjprog", "version": "9.4.0",
         "systems": [{"host": "x86_64-pc-linux-gnu", "url": "https://x/linux64"}]}]}]}
    ada = {"packages": [{"name": "adafruit", "tools": [
        {"name": "nrfjprog", "version": "9.4.0", "systems": [host]}]}]}
    out, changes = ms.index_with_standins(rak, ada, MANIFEST["arduino"]["aarch64_tool_standins"])
    systems = out["packages"][0]["tools"][0]["systems"]
    assert host in systems and len(systems) == 2 and changes
    assert len(rak["packages"][0]["tools"][0]["systems"]) == 1     # input untouched
    again, none = ms.index_with_standins(out, ada, MANIFEST["arduino"]["aarch64_tool_standins"])
    assert again == out and not none


def test_the_arduino_listings_parse_in_both_shapes():
    new = json.dumps({"platforms": [{"id": "esp32:esp32", "installed_version": "2.0.17"},
                                    {"id": "x:y", "latest_version": "1.0"}]})
    old = json.dumps([{"id": "esp32:esp32", "installed": "2.0.17"}])
    assert ms.parse_core_list(new) == {"esp32:esp32": "2.0.17"}
    assert ms.parse_core_list(old) == {"esp32:esp32": "2.0.17"}
    assert ms.parse_core_list("not json") == {}
    libs = json.dumps({"installed_libraries": [{"library": {"name": "SD", "version": "1.3.0"}}]})
    assert ms.parse_lib_list(libs) == {"SD": "1.3.0"}
    assert ms.parse_lib_list(json.dumps([{"library": {"name": "SD", "version": "1.3.0"}}])) \
        == {"SD": "1.3.0"}


def test_the_world_map_unit_is_written_for_whoever_runs_the_setup():
    src = open(os.path.join(ROOT, "scripts", "world-map-fill.service")).read()
    unit = ms.render_world_map_unit(src, "keeper", "/home/keeper")
    assert "User=keeper" in unit and "nodemedic" not in unit.split("[Unit]", 1)[1]
    assert "ExecStart=/usr/bin/python3 /home/keeper/reticulum-tool/scripts/world_map_fill.py" in unit


def test_a_clone_step_message_reads_right_on_the_medic_itself():
    assert ms.local_words("Could not copy the card helper to the clone.") == \
        "Could not copy the card helper onto this medic."
    assert ms.local_words("Giving it its own door key did not finish. Press Retry.") == \
        "Giving it its own door key did not finish. Run the setup again."
    assert "This machine is not a Raspberry Pi 5" in ms.local_words(
        clone.verify_target_pi5(_wf_on("Model : Raspberry Pi 4")).message)


def _wf_on(cpuinfo):
    c = EmulatedConnection(default_code=0, default_stdout="")
    c.rule("/proc/cpuinfo", 0, cpuinfo)
    return clone.CloneWorkflow(c, NodeRegistry())


# --------------------------------------------------------------------------- #
# The run is honest.
# --------------------------------------------------------------------------- #

def _setup(conn, tmp_path, **kw):
    return ms.MedicSetup(conn, home=str(tmp_path), tool_root=ROOT, stream=io.StringIO(),
                         sleep=lambda _s: None, **kw)


def _files(root):
    return sorted(os.path.join(b, f) for b, _d, fs in os.walk(root) for f in fs)


MUTATING = ("apt-get", "pip3 install", "pip install", "tee ", "systemctl enable",
            "set-ntp", "git init", "curl -fL", "core install", "lib install",
            "config set", "patch --forward", "rnid", "ssh-keygen", "--apply", "reboot",
            "pio pkg", "pio run", "pio settings set", "make ", "arduino-cli compile",
            "mkdir", "mv -f", "rm -")


def test_check_mode_changes_nothing(tmp_path):
    c = EmulatedConnection(default_code=1, default_stdout="")
    s = _setup(c, tmp_path, check_only=True)
    code = s.run_all()
    assert code == 1
    assert not [h for h in c.history if any(m in h for m in MUTATING)], \
        [h for h in c.history if any(m in h for m in MUTATING)]
    assert not c.pushed and not c.pushed_trees
    assert _files(tmp_path) == []
    shown = {o.step.name for o in s.results}
    assert "restart" not in shown and "use_network_time" not in shown
    assert all(o.status in ("ok", "missing") for o in s.results)
    out = s.stream.getvalue()
    for o in s.results:                         # one line per step, each named
        assert o.step.title in out


def test_a_refusal_stops_the_run(tmp_path):
    c = EmulatedConnection(default_code=0, default_stdout="")
    c.rule("/proc/cpuinfo", 0, "Model : Raspberry Pi 4 Model B")
    s = _setup(c, tmp_path)
    assert s.run_all() == 2
    assert [o.status for o in s.results] == ["refused"]
    assert "not a Raspberry Pi 5" in s.results[0].detail
    assert "REFUSED" in s.stream.getvalue()


def _fake_step(name, done=False, result=None, **kw):
    """A step whose check reads a flag its act sets, unless the act fails or
    the step is a background job that is still going."""
    calls = []
    state = {"done": done}

    def check(_s):
        return state["done"], "done" if state["done"] else "not yet"

    def act(_s):
        calls.append(name)
        r = result or ms.StepResult(name, True, "did it")
        if r.success and not kw.get("background"):
            state["done"] = True
        return r
    return ms.SetupStep(name, name.replace("_", " "), check, act, **kw), calls


def test_a_step_that_needs_the_internet_says_so_and_the_rest_go_on(tmp_path):
    c = EmulatedConnection(default_code=0, default_stdout="")
    c.rule("curl -fsI", 7, "")                       # github.com unreachable
    c.rule("df -Pk", 0, str(64 * 1024 * 1024))
    s = _setup(c, tmp_path)
    online, online_calls = _fake_step("fetch_something", internet=True)
    local, local_calls = _fake_step("do_something_local")
    s.steps = [online, local]
    assert s.run_all() == 1
    assert [o.status for o in s.results] == ["failed", "ok"]
    assert "needs the internet" in s.results[0].detail
    assert online_calls == [] and local_calls == ["do_something_local"]


def test_a_step_without_disk_or_sudo_does_not_start(tmp_path):
    c = EmulatedConnection(default_code=0, default_stdout="")
    c.rule("df -Pk", 0, str(500 * 1024))             # 500 MB free
    c.rule("sudo -n true", 1, "")
    s = _setup(c, tmp_path)
    big, big_calls = _fake_step("big", needs_mb=4000)
    rooted, root_calls = _fake_step("rooted", root=True)
    s.steps = [big, rooted]
    s.run_all()
    assert "GB free" in s.results[0].detail and "sudo" in s.results[1].detail
    assert big_calls == [] and root_calls == []


def test_finished_work_is_not_redone(tmp_path):
    c = EmulatedConnection(default_code=0, default_stdout="")
    s = _setup(c, tmp_path)
    step, calls = _fake_step("already_there", done=True)
    s.steps = [step]
    assert s.run_all() == 0
    assert calls == [] and s.results[0].status == "already"
    assert "ok (already done)" in s.stream.getvalue()


def test_a_background_job_that_has_started_is_not_a_failure(tmp_path):
    c = EmulatedConnection(default_code=0, default_stdout="")
    s = _setup(c, tmp_path)
    step, _calls = _fake_step("long_job", background=True)
    s.steps = [step]
    assert s.run_all() == 0
    assert s.results[0].status == "running"


def test_the_restart_happens_only_when_asked_and_nothing_failed(tmp_path):
    restart = next(st for st in ms.SETUP_STEPS if st.name == "restart")
    c = EmulatedConnection(default_code=0, default_stdout="")
    s = _setup(c, tmp_path)
    s.steps = [restart]
    s.run_all()
    assert s.results[0].status == "skipped" and "sudo reboot" in s.results[0].detail
    assert not [h for h in c.history if "reboot" in h]

    c = EmulatedConnection(default_code=0, default_stdout="")
    s = _setup(c, tmp_path, reboot=True)
    bad, _ = _fake_step("broken", result=ms.StepResult("broken", False, "no"))
    s.steps = [bad, restart]
    s.run_all()
    assert s.results[1].status == "skipped" and "need attention" in s.results[1].detail
    assert not [h for h in c.history if "reboot" in h]

    c = EmulatedConnection(default_code=0, default_stdout="")
    c.rule("ui_busy_guard.sh", 3, "", "REFUSING to stop the UI: a board firmware flash")
    s = _setup(c, tmp_path, reboot=True)
    s.steps = [restart]
    s.run_all()
    assert "board firmware flash" in s.results[0].detail
    assert not [h for h in c.history if "reboot" in h]

    c = EmulatedConnection(default_code=0, default_stdout="")
    s = _setup(c, tmp_path, reboot=True)
    s.steps = [restart]
    s.run_all()
    assert s.results[0].status == "ok"
    assert [h for h in c.history if "sleep 3; reboot" in h]


def test_a_download_is_kept_only_when_its_checksum_is_the_pinned_one(tmp_path):
    c = EmulatedConnection(default_code=0, default_stdout="")
    c.rule("sha256sum", 0, "deadbeef  file")
    s = _setup(c, tmp_path)
    ok, why = ms._download(s, "https://example/f", str(tmp_path / "f"), "ab" * 32)
    assert not ok and "checksum" in why
    assert [h for h in c.history if h.startswith("rm -f")]
    assert not [h for h in c.history if h.startswith("mv -f")]


def test_a_tree_at_another_commit_is_never_touched(tmp_path):
    c = EmulatedConnection(default_code=0, default_stdout="")
    c.rule("rev-parse", 0, "1" * 40)
    s = _setup(c, tmp_path)
    r = ms._act_trees(s)
    assert not r.success and "left as it is" in r.message and "not the pinned" in r.message
    assert not [h for h in c.history if "git init" in h or "checkout" in h]


def test_a_missing_tree_is_fetched_at_exactly_its_pinned_commit(tmp_path):
    c = EmulatedConnection(default_code=0, default_stdout="")
    c.rule("test -e", 1, "")
    s = _setup(c, tmp_path)
    assert ms._act_trees(s).success
    fetches = [h for h in c.history if "git init" in h]
    assert len(fetches) == len(MANIFEST["firmware_trees"])
    for t in MANIFEST["firmware_trees"]:
        f = next(h for h in fetches if t["repo"] in h and t["commit"] in h)
        assert f"fetch -q --depth 1 origin {t['commit']}" in f
        assert f"checkout -q -B {t['branch']} FETCH_HEAD" in f


def test_the_patch_step_refuses_a_patch_file_that_is_not_the_pinned_one(tmp_path):
    man = copy.deepcopy(MANIFEST)
    man["python"]["patch"]["sha256"] = "0" * 64
    c = EmulatedConnection(default_code=0, default_stdout="")
    c.rule("importlib.metadata", 0, json.dumps({"rns": "1.3.7"}))
    c.rule("find_spec", 0, "/home/pi/.local/lib/python3.13/site-packages")
    c.rule("patch --dry-run -R", 1, "")
    s = ms.MedicSetup(c, manifest=man, home=str(tmp_path), tool_root=ROOT,
                      stream=io.StringIO())
    r = ms._act_rns_patch(s)
    assert not r.success and "checksum" in r.message
    assert not [h for h in c.history if "patch --forward" in h]


def test_the_patch_is_not_applied_to_another_rns(tmp_path):
    c = EmulatedConnection(default_code=0, default_stdout="")
    c.rule("importlib.metadata", 0, json.dumps({"rns": "1.3.8"}))
    s = _setup(c, tmp_path)
    done, why = ms._check_rns_patch(s)
    assert not done and "1.3.8" in why
    assert not ms._act_rns_patch(s).success
    assert not [h for h in c.history if h.startswith("patch")]


def test_the_python_step_installs_the_medic_pins(tmp_path):
    c = EmulatedConnection(default_code=0, default_stdout="")
    s = _setup(c, tmp_path)
    assert ms._act_python(s).success
    pip = [h for h in c.history if "install --user --break-system-packages" in h]
    assert len(pip) == 1
    req = pip[0].split("-r ")[1].strip().strip("'")
    text = open(req).read()
    assert "rns==1.3.7" in text and "platformio==6.1.19" in text
    assert req.startswith(str(tmp_path))


def test_the_touch_step_writes_kivys_defaults_cured(tmp_path):
    cfg = tmp_path / ".kivy" / "config.ini"
    cfg.parent.mkdir()
    cfg.write_text(KIVY_DEFAULT)
    s = _setup(EmulatedConnection(default_code=0, default_stdout=""), tmp_path)
    assert not ms._check_touch(s)[0]
    assert ms._act_touch(s).success
    assert ms._check_touch(s)[0]
    assert (tmp_path / ".kivy" / "config.ini.bak-setup").read_text() == KIVY_DEFAULT


def test_the_boot_config_step_adds_only_the_missing_medic_lines(tmp_path):
    c = EmulatedConnection(default_code=0, default_stdout="")
    c.rule("cat /boot/firmware/config.txt", 0,
           "[all]\n#dtparam=i2c_arm=on\nusb_max_current_enable=1\n")
    s = _setup(c, tmp_path)
    done, why = ms._check_boot_config(s)
    assert not done and "dtparam=i2c_arm=on" in why
    assert ms._act_boot_config(s).success
    tee = [h for h in c.history if "tee -a /boot/firmware/config.txt" in h]
    assert len(tee) == 1 and "dtparam=i2c_arm=on" in tee[0]
    assert "usb_max_current_enable=1" not in tee[0].split("RNMEOF", 1)[1]


def test_the_v4_build_never_reaches_for_upstream_or_unpinned_libraries(tmp_path):
    """Without the pinned tree, HeltecV4RGBWorkflow._ensure_source would clone
    upstream markqvist into the empty path; and its _ensure_toolchain would
    move pinned libraries to their newest versions. The setup uses neither."""
    c = EmulatedConnection(default_code=0, default_stdout="")
    c.rule("test -e", 1, "")
    s = _setup(c, tmp_path)
    r = ms._act_v4(s)
    assert not r.success and "pinned tree" in r.message
    assert not [h for h in c.history if "git clone" in h or "lib install" in h]


def test_the_field_step_presses_prepare_for_the_field(tmp_path, monkeypatch):
    from workflows import carry
    seen = {}

    def fake(conn, force=False, progress=None):
        seen["conn"] = conn
        return carry.CarryReport(online=True, message="Fetched: everything.")
    monkeypatch.setattr(carry, "carry_all", fake)
    c = EmulatedConnection(default_code=0, default_stdout="")
    s = _setup(c, tmp_path)
    r = ms._act_field(s)
    assert r.success and seen["conn"] is c


def test_the_world_map_check_creates_no_file(tmp_path):
    s = _setup(EmulatedConnection(default_code=0, default_stdout=""), tmp_path)
    done, why = ms._check_world_map(s)
    assert not done and f"of {MANIFEST['field']['world_map_tiles']:,}" in why
    assert _files(tmp_path) == []


@pytest.mark.parametrize("answer", [(0, ""), (1, "")])
def test_every_step_ends_in_a_sentence_whatever_the_machine_answers(tmp_path, monkeypatch,
                                                                     answer):
    """No step may crash: a machine that answers every command with silence
    (or with failure) still gets one sentence per step, never a traceback.
    Field readiness is stood in for — the real one would write into this
    host's own ~/reticulum-tool."""
    from workflows import carry
    monkeypatch.setattr(carry, "carry_all", lambda conn, force=False, progress=None:
                        carry.CarryReport(online=False, message="Offline."))
    code, out = answer
    c = EmulatedConnection(default_code=code, default_stdout=out)
    c.rule("/proc/cpuinfo", 0, "Model : Raspberry Pi 5 Model B Rev 1.0")
    s = _setup(c, tmp_path, reboot=False)
    for step in s.steps:
        if step.act is not None:
            r = step.act(s.wf) if step.act is step.clone_fn else step.act(s)
            assert isinstance(r, ms.StepResult) and r.message, step.name
        done, detail = s._check(step)
        assert not detail.startswith("could not check"), (step.name, detail)


def test_platformio_is_ready_when_its_own_files_say_so(tmp_path):
    """The appstate.json shape PlatformIO 6.1.19 itself writes (read off a real
    install in a throwaway core dir, 2026-10-08): typed settings. pio is never
    run by the check: it writes that very file on every start."""
    from workflows import rtnode_build as rb
    pio = tmp_path / ".platformio"
    (pio / "platforms" / "espressif32").mkdir(parents=True)
    (pio / "appstate.json").write_text(json.dumps(
        {"last_version": "6.1.19", "settings": {"enable_telemetry": False,
                                                "check_platformio_interval": 4294967}}))
    (pio / "platforms" / "espressif32" / "platform.json").write_text(
        json.dumps({"name": "espressif32", "version": "6.13.0"}))
    project = tmp_path / rb.RTNODE_PROJECT_DIR[2:]
    c = EmulatedConnection(default_code=0, default_stdout="")
    c.rule("importlib.metadata", 0, json.dumps({"platformio": "6.1.19"}))
    s = _setup(c, tmp_path)
    done, why = ms._check_platformio(s)
    assert not done and "packages for" in why          # no env prefetched yet
    for env in ms.pio_envs():
        (project / ".pio" / "libdeps" / env / "ArduinoJson").mkdir(parents=True)
    assert ms._check_platformio(s) == (True, "ready for every RTNode-2400 env")
    (pio / "appstate.json").write_text(json.dumps({"settings": {"enable_telemetry": True}}))
    done, why = ms._check_platformio(s)
    assert not done and "enable_telemetry" in why


def test_the_heltec_core_has_its_uf2_step_switched_off(tmp_path):
    """Heltec_nRF52 1.6.0 quotes `python .../uf2conv.py` into one word, so its
    UF2 step fails off Windows and no HT-n5262 compile (MeshPocket RNode, T114
    RTNode) reaches its DFU .zip — found compiling the MeshPocket tree off the
    Pi, 2026-10-08. The cores step switches that step off and checks it stays
    off."""
    from workflows import rnode_boards
    meshpocket = rnode_boards.get_board("heltec_meshpocket")
    core = ":".join(meshpocket.fqbn.split(":")[:2])
    assert MANIFEST["arduino"]["platform_local"][core] == ["recipe.objcopy.uf2.pattern="]
    installed = json.dumps({"platforms": [{"id": c, "installed_version": v}
                                          for c, v in MANIFEST["arduino"]["cores"].items()]})
    c = EmulatedConnection(default_code=0, default_stdout="")
    c.rule("core list --json", 0, installed)
    s = _setup(c, tmp_path)
    done, why = ms._check_cores(s)
    assert not done and "platform.local.txt" in why
    core_dir = tmp_path / ".arduino15/packages/Heltec_nRF52/hardware/Heltec_nRF52" / \
        MANIFEST["arduino"]["cores"][core]
    core_dir.mkdir(parents=True)
    assert ms._act_cores(s).success
    assert "recipe.objcopy.uf2.pattern=\n" in (core_dir / "platform.local.txt").read_text()
    assert ms._check_cores(s) == (True, "all at Node Medic 1's versions")
    assert ms._act_cores(s).success                      # a second run adds nothing
    assert (core_dir / "platform.local.txt").read_text().count("uf2.pattern") == 1


def test_arduino_cli_versions_read_as_the_real_tool_prints_them(tmp_path):
    """`arduino-cli version` as 1.5.1 prints it (run 2026-10-08)."""
    c = EmulatedConnection(default_code=0, default_stdout="")
    c.rule("arduino-cli version", 0,
           "arduino-cli  Version: 1.5.1 Commit: 01f3d4f2b Date: 2026-06-05T10:21:41Z")
    assert ms._check_arduino_cli(_setup(c, tmp_path)) == (True, "arduino-cli 1.5.1")
