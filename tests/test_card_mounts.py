"""Card mounts are root's, pinned, and never follow the card's own links; sudo
never remembers a password for the app account.

The keeper, 2026-10-08. Two properties of the medic's scoped sudo policy
(provisioning/sudoers.d/nodemedic), both described in
provisioning/security/README.md:

* EVERY CARD MOUNT lives in a root-owned folder under /run/nodemedic, made by
  one exact `install -d` rule, and is mounted with ONE pinned option string —
  nosymfollow,nodev,nosuid,noexec — so a file root writes into that folder
  lands on the card and nowhere else. tee, touch and umount name exact files
  and folders. Nothing mounts, tees or touches in /tmp with sudo any more.
* `Defaults:<user> timestamp_timeout=0`: a password typed for the app account
  is never remembered, so only the exact rules ever run without one.

These tests hold the policy, provisioning/card_mount.py and every piece of code
that mounts a card to those properties, for the original medic's `nodemedic`
and a clone's `pi` alike.
"""

import ast
import json
import os
import re
import shlex
import shutil
import subprocess

import pytest

from provisioning import card_mount
from tests.sudoersutil import RENDER, ROOT, TEMPLATE, Cmnd, Policy, render, unescape
from tests.test_privileged_commands import privileged_sites

USERS = ("nodemedic", "pi")
RUN = "/run/nodemedic"
OPTS = "nosymfollow,nodev,nosuid,noexec"
#: The device patterns a card may be mounted from: a USB reader's partitions,
#: and a built-in second MMC controller's. One character class per position.
DEVICE_PATTERNS = {"/dev/sd[a-z][0-9]", "/dev/mmcblk1p[0-9]"}
#: The two tee targets that are not on a card.
SYSTEM_FILES = {"/sys/class/backlight/panel_backlight@1/brightness", "/etc/default/gpsd"}


#: Bare names the card code calls, as sudo's secure_path resolves them on the
#: medic (merged-usr Debian).
_ABS = {"install": "/usr/bin/install", "mount": "/usr/bin/mount",
        "umount": "/usr/bin/umount", "tee": "/usr/bin/tee", "touch": "/usr/bin/touch",
        "mkdir": "/usr/bin/mkdir", "sync": "/usr/bin/sync",
        "partprobe": "/usr/sbin/partprobe"}
_SUDO = re.compile(r"(?:^|[|;&{]\s*)sudo\s+(?:-n\s+)?([^|;&]+)")
_REDIRECT = re.compile(r"\s*\d*>>?\s*\S+")


def sudo_argvs(command):
    """The argv sudo receives for each `sudo ...` in a shell command string."""
    out = []
    for m in _SUDO.finditer(command):
        argv = shlex.split(_REDIRECT.sub("", m.group(1)))
        argv[0] = _ABS.get(argv[0], argv[0])
        out.append(argv)
    return out


@pytest.fixture(scope="module")
def policies(tmp_path_factory):
    out = {}
    for user in USERS:
        path = str(tmp_path_factory.mktemp("policy") / f"sudoers-{user}")
        out[user] = (Policy(render(user, path), user), path)
    return out


def _rules(policy, name):
    """Every rule in the grant for the program at *name* (an absolute path)."""
    return [c for c in policy.commands if c.path == name]


def _make_dir(name):
    return ["/usr/bin/install", "-d", "-m", "0755", "-o", "root", "-g", "root",
            RUN, f"{RUN}/{name}"]


def _mount(part, target, opts=OPTS):
    return ["/usr/bin/mount", "-o", opts, part, target]


# --------------------------------------------------------------------------- #
# provisioning/card_mount.py builds exactly what the policy grants
# --------------------------------------------------------------------------- #

def test_the_mount_points_are_root_owned_folders_under_run():
    for mnt in (card_mount.SD_BOOT, card_mount.PIBOOT, card_mount.RESEED,
                card_mount.PIROOT, card_mount.PIROOT_USER):
        assert os.path.dirname(mnt) == RUN == card_mount.RUN_DIR, mnt
    assert card_mount.OPTIONS == OPTS


@pytest.mark.parametrize("user", USERS)
def test_the_builders_say_word_for_word_what_the_policy_grants(policies, user):
    policy = policies[user][0]
    built = [card_mount.make_dir(card_mount.SD_BOOT), card_mount.make_dir(card_mount.PIBOOT),
             card_mount.mount("/dev/sda1", card_mount.SD_BOOT),
             card_mount.mount("/dev/mmcblk1p1", card_mount.SD_BOOT),
             card_mount.mount("/dev/sdb2", card_mount.PIBOOT)]
    for args in built:
        (argv,) = sudo_argvs("sudo -n " + args)
        assert policy.allows(argv), args


@pytest.mark.parametrize("mnt", [
    "/tmp/nm_sd_boot", "/tmp/rnm-piboot", RUN, f"{RUN}/", f"{RUN}/sd_boot/sub",
    f"{RUN}/../tmp", f"{RUN}/SD", f"{RUN}/sd boot", f"{RUN}/sd_boot;id", "/etc",
    "", "/run/nodemedic-harden"])
def test_the_builders_refuse_any_other_folder(mnt):
    with pytest.raises(ValueError):
        card_mount.make_dir(mnt)
    with pytest.raises(ValueError):
        card_mount.mount("/dev/sda1", mnt)


def test_the_partition_is_quoted():
    assert card_mount.mount("/dev/sda1; id", card_mount.SD_BOOT) == (
        f"mount -o {OPTS} '/dev/sda1; id' {RUN}/sd_boot")


# --------------------------------------------------------------------------- #
# The policy: every card rule pinned (structure)
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("user", USERS)
def test_every_card_rule_is_pinned(policies, user):
    policy = policies[user][0]
    mounted, made, unmounted = set(), set(), set()
    for c in _rules(policy, "/usr/bin/mount"):
        args = unescape(c.args or "")
        m = re.fullmatch(rf"-o {re.escape(OPTS)} (\S+) {re.escape(RUN)}/([a-z_]+)", args)
        assert m, f"a mount rule without the pinned shape: {c.spec}"
        assert m.group(1) in DEVICE_PATTERNS, f"an unpinned device: {c.spec}"
        assert not c.spans, c.spec
        mounted.add(m.group(2))
    for c in _rules(policy, "/usr/bin/install"):
        m = re.fullmatch(rf"-d -m 0755 -o root -g root {re.escape(RUN)} "
                         rf"{re.escape(RUN)}/([a-z_]+)", c.args or "")
        assert m, f"an install rule that is not a root-owned card folder: {c.spec}"
        made.add(m.group(1))
    for c in _rules(policy, "/usr/bin/umount"):
        m = re.fullmatch(rf"{re.escape(RUN)}/([a-z_]+)", c.args or "")
        assert m, f"an umount rule for anything but a card folder: {c.spec}"
        unmounted.add(m.group(1))
    assert mounted, "no card mount rule found at all"
    assert mounted == made == unmounted, (mounted, made, unmounted)
    for prog in ("/usr/bin/tee", "/usr/bin/touch"):
        for c in _rules(policy, prog):
            if c.args in SYSTEM_FILES:
                continue
            m = re.fullmatch(rf"{re.escape(RUN)}/([a-z_]+)/[A-Za-z0-9._-]+", c.args or "")
            assert m and m.group(1) in mounted, f"{prog} outside a card folder: {c.spec}"
            assert not c.spans, c.spec
    assert not _rules(policy, "/usr/bin/mkdir"), "mount points are made by install -d"


@pytest.mark.parametrize("user", USERS)
def test_no_card_rule_names_tmp(policies, user):
    policy = policies[user][0]
    for c in policy.commands:
        if os.path.basename(c.path) in ("mount", "umount", "tee", "touch", "mkdir",
                                        "install"):
            assert "/tmp" not in (c.args or ""), c.spec


# --------------------------------------------------------------------------- #
# The policy: what it refuses (behaviour, the way sudo matches)
# --------------------------------------------------------------------------- #

#: Mounts the policy must refuse: anything but the pinned options, anything but
#: a pinned folder, anything but a card's partition, anything appended.
_MOUNTS_REFUSED = [
    # no options, fewer, more, reordered, read-only, another flag
    ["/usr/bin/mount", "/dev/sda1", f"{RUN}/sd_boot"],
    _mount("/dev/sda1", f"{RUN}/sd_boot", "nodev,nosuid,noexec"),
    _mount("/dev/sda1", f"{RUN}/sd_boot", "nosymfollow,nodev,nosuid"),
    _mount("/dev/sda1", f"{RUN}/sd_boot", "nodev,nosymfollow,nosuid,noexec"),
    _mount("/dev/sda1", f"{RUN}/sd_boot", "ro," + OPTS),
    _mount("/dev/sda1", f"{RUN}/sd_boot", OPTS + ",bind"),
    _mount("/dev/sda1", f"{RUN}/sd_boot", OPTS + ",suid"),
    ["/usr/bin/mount", "--bind", "-o", OPTS, "/dev/sda1", f"{RUN}/sd_boot"],
    ["/usr/bin/mount", "-o", OPTS, "-o", "suid", "/dev/sda1", f"{RUN}/sd_boot"],
    ["/usr/bin/mount", "-o", OPTS, "/dev/sda1", f"{RUN}/sd_boot", "/etc"],
    ["/usr/bin/mount", "-o", OPTS, "/dev/sda1", "/dev/sdb1", f"{RUN}/sd_boot"],
    # any other folder
    _mount("/dev/sda1", "/tmp/nm_sd_boot"), _mount("/dev/sda1", "/tmp/rnm-piboot"),
    _mount("/dev/sda1", RUN), _mount("/dev/sda1", f"{RUN}/"),
    _mount("/dev/sda1", f"{RUN}/other"), _mount("/dev/sda1", f"{RUN}/sd_boot/sub"),
    _mount("/dev/sda1", f"{RUN}/sd_boot/../../../etc"), _mount("/dev/sda1", "/etc"),
    _mount("/dev/sda1", "/root"), _mount("/dev/sda1", "/boot/firmware"),
    _mount("/dev/sda1", f"{RUN}/reseed"), _mount("/dev/sda1", f"{RUN}/piroot"),
    # anything but a card's partition
    _mount("/dev/mmcblk0p1", f"{RUN}/sd_boot"), _mount("/dev/mmcblk0p2", f"{RUN}/piboot"),
    _mount("/dev/mmcblk1p1", f"{RUN}/piboot"), _mount("/dev/sda", f"{RUN}/sd_boot"),
    _mount("/dev/loop0", f"{RUN}/sd_boot"), _mount("/dev/nvme0n1p1", f"{RUN}/sd_boot"),
    _mount("/tmp/card.img", f"{RUN}/sd_boot"), _mount("/dev/sda10", f"{RUN}/sd_boot"),
    _mount("/dev/sdA1", f"{RUN}/sd_boot"),
]

#: Folder making, unmounting, writing and touching the policy must refuse.
_OTHERS_REFUSED = [
    ["/usr/bin/install", "-d", "-m", "0777", "-o", "root", "-g", "root", RUN, f"{RUN}/sd_boot"],
    ["/usr/bin/install", "-d", "-m", "0755", "-o", "{user}", "-g", "root", RUN, f"{RUN}/sd_boot"],
    ["/usr/bin/install", "-d", "-m", "0755", "-o", "root", "-g", "root", RUN, "/etc/x"],
    ["/usr/bin/install", "-d", "-m", "0755", "-o", "root", "-g", "root", RUN, f"{RUN}/sd_boot",
     "/etc/x"],
    ["/usr/bin/install", "-d", "-m", "0755", "-o", "root", "-g", "root", f"{RUN}/sd_boot"],
    ["/usr/bin/install", "-m", "0755", "/tmp/x", "/usr/local/bin/x"],
    ["/usr/bin/mkdir", "-p", f"{RUN}/sd_boot"], ["/usr/bin/mkdir", "-p", "/tmp/rnm-piboot"],
    ["/usr/bin/umount", "/"], ["/usr/bin/umount", "/boot/firmware"], ["/usr/bin/umount", RUN],
    ["/usr/bin/umount", "-l", f"{RUN}/sd_boot"], ["/usr/bin/umount", f"{RUN}/sd_boot", "/"],
    ["/usr/bin/umount", "/tmp/nm_sd_boot"],
    ["/usr/bin/tee", "/tmp/nm_sd_boot/config.txt"], ["/usr/bin/tee", "/tmp/rnm-piboot/custom.toml"],
    ["/usr/bin/tee", f"{RUN}/sd_boot/other.txt"], ["/usr/bin/tee", f"{RUN}/sd_boot"],
    ["/usr/bin/tee", f"{RUN}/piboot/../sd_boot/config.txt"],
    ["/usr/bin/tee", f"{RUN}/sd_boot/../../../etc/sudoers.d/zz"],
    ["/usr/bin/tee", "-a", f"{RUN}/sd_boot/config.txt"],
    ["/usr/bin/tee", f"{RUN}/sd_boot/config.txt", "/etc/shadow"],
    ["/usr/bin/tee", f"{RUN}/reseed/user-data"], ["/usr/bin/tee", f"{RUN}/piboot/user-data"],
    ["/usr/bin/touch", "/tmp/rnm-piboot/ssh"], ["/usr/bin/touch", f"{RUN}/sd_boot/ssh"],
    ["/usr/bin/touch", f"{RUN}/piboot/ssh", "/etc/x"],
    ["/usr/bin/touch", "-h", f"{RUN}/piboot/ssh"],
]


@pytest.mark.parametrize("user", USERS)
def test_a_mount_needs_the_pinned_options_and_a_pinned_folder(policies, user):
    policy = policies[user][0]
    granted = [a for a in _MOUNTS_REFUSED if policy.allows(a)]
    assert not granted, "granted but must not be:\n" + "\n".join(" ".join(a) for a in granted)
    # ...and the right ones are granted, so the refusals above mean something
    assert policy.allows(_mount("/dev/sda1", f"{RUN}/sd_boot"))
    assert policy.allows(_mount("/dev/mmcblk1p1", f"{RUN}/sd_boot"))
    assert policy.allows(_mount("/dev/sdb2", f"{RUN}/piboot"))


@pytest.mark.parametrize("user", USERS)
def test_tee_touch_umount_and_install_reach_only_the_pinned_places(policies, user):
    policy = policies[user][0]
    argvs = [[a.replace("{user}", user) for a in argv] for argv in _OTHERS_REFUSED]
    granted = [a for a in argvs if policy.allows(a)]
    assert not granted, "granted but must not be:\n" + "\n".join(" ".join(a) for a in granted)
    for argv in (_make_dir("sd_boot"), _make_dir("piboot"),
                 ["/usr/bin/umount", f"{RUN}/sd_boot"], ["/usr/bin/umount", f"{RUN}/piboot"],
                 ["/usr/bin/tee", f"{RUN}/sd_boot/config.txt"],
                 ["/usr/bin/tee", f"{RUN}/sd_boot/cmdline.txt"],
                 ["/usr/bin/tee", f"{RUN}/piboot/custom.toml"],
                 ["/usr/bin/touch", f"{RUN}/piboot/ssh"]):
        assert policy.allows(argv), " ".join(argv)


def test_the_matcher_reads_the_comma_escapes_like_sudo():
    """The option string is written with escaped commas in the policy; sudo
    unescapes them before matching, and so must this test's matcher."""
    rule = Cmnd(r"/usr/bin/mount -o nosymfollow\,nodev\,nosuid\,noexec "
                r"/dev/sd[a-z][0-9] /run/nodemedic/sd_boot")
    assert rule.matches(_mount("/dev/sdc1", f"{RUN}/sd_boot"))
    assert not rule.spans
    assert not rule.matches(_mount("/dev/sdc1", f"{RUN}/sd_boot", "nosymfollow"))


# --------------------------------------------------------------------------- #
# The code: nothing mounts, tees or touches in /tmp with sudo
# --------------------------------------------------------------------------- #

_FILE_VERBS = re.compile(r"\b(mount|umount|tee|touch|mkdir)\b")


def test_no_privileged_call_mounts_tees_or_touches_in_tmp():
    """Every privileged call in the code (the scan the policy guard uses):
    none that mounts, tees, touches or makes a folder names /tmp."""
    offenders = sorted(f"{p}: {s}" for p, s in privileged_sites()
                       if _FILE_VERBS.search(s) and "/tmp" in s)
    assert not offenders, "\n".join(offenders)


def _mounting_modules():
    """Production modules with a privileged call that mounts a card."""
    return sorted({p for p, s in privileged_sites() if re.search(r"\bmount\b", s)})


def test_no_mount_point_constant_or_default_lives_in_tmp():
    """The folders come from constants and default arguments, which the call
    scan above cannot see through: in every module that mounts a card with
    sudo, nothing named like a mount point is bound to a /tmp path."""
    mods = _mounting_modules()
    assert {"provisioning/sd_edit.py", "workflows/mitosis_card.py",
            "provisioning/pi_imager.py", "provisioning/cable_birth.py",
            "provisioning/card_forensics.py"} <= set(mods), mods
    named = re.compile(r"mnt|mount", re.I)
    found = []
    for rel in mods + ["provisioning/card_mount.py"]:
        tree = ast.parse(open(os.path.join(ROOT, rel), encoding="utf-8").read())
        for node in ast.walk(tree):
            pairs = []
            if isinstance(node, ast.Assign):
                pairs += [(t.id, node.value) for t in node.targets if isinstance(t, ast.Name)]
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                a = node.args
                pos = a.posonlyargs + a.args
                pairs += list(zip((x.arg for x in pos[len(pos) - len(a.defaults):]), a.defaults))
                pairs += [(x.arg, d) for x, d in zip(a.kwonlyargs, a.kw_defaults) if d]
            elif isinstance(node, ast.keyword) and node.arg:
                pairs.append((node.arg, node.value))
            for name, value in pairs:
                if named.search(name) and isinstance(value, ast.Constant) \
                        and isinstance(value.value, str) and value.value.startswith("/tmp"):
                    found.append(f"{rel}: {name} = {value.value}")
    assert not found, "\n".join(found)


# -- running the card code itself, with stand-ins for the shell ---------------

def test_sudo_argvs_reads_the_shapes_the_card_code_writes():
    assert sudo_argvs("echo QUJD | base64 -d | sudo -n tee /run/x/config.txt > /dev/null") == [
        ["/usr/bin/tee", "/run/x/config.txt"]]
    assert sudo_argvs("sudo partprobe /dev/sdb 2>/dev/null; sleep 1") == [
        ["/usr/sbin/partprobe", "/dev/sdb"]]
    assert sudo_argvs("sudo -n a b && { cat x ; } ; rc=$? ; sudo -n sync ; "
                      "sudo -n umount /m && echo ok") == [["a", "b"], ["/usr/bin/sync"],
                                                          ["/usr/bin/umount", "/m"]]


_MEDIC_DISK = {"name": "mmcblk0", "type": "disk",
               "children": [{"name": "mmcblk0p1", "fstype": "vfat", "path": "/dev/mmcblk0p1"},
                            {"name": "mmcblk0p2", "fstype": "ext4", "path": "/dev/mmcblk0p2"}]}


def _card(disk, sep=""):
    return {"name": disk, "type": "disk",
            "children": [{"name": f"{disk}{sep}1", "fstype": "vfat",
                          "path": f"/dev/{disk}{sep}1"},
                         {"name": f"{disk}{sep}2", "fstype": "ext4",
                          "path": f"/dev/{disk}{sep}2"}]}


def _sd_edit_commands(card):
    """Every command bake_reachability_via_sd runs, for a card nobody has
    mounted yet (the path that mounts it)."""
    from node_profile import NodeHardware
    from provisioning import sd_edit

    def run(cmd):
        if "findmnt" in cmd and "SOURCE" in cmd:
            return "/dev/mmcblk0p2\n"
        if "findmnt" in cmd:
            return ""
        if "lsblk" in cmd:
            return json.dumps({"blockdevices": [_MEDIC_DISK, card]})
        if "cat" in cmd and "config.txt" in cmd:
            return "dtparam=audio=on\n"
        if "cat" in cmd and "cmdline.txt" in cmd:
            return "console=tty1 rootwait\n"
        return ""
    calls = []

    def run_code(_run, cmd):
        calls.append(cmd)
        return 0, ""
    res = sd_edit.bake_reachability_via_sd(NodeHardware.PI_3A_PLUS, run=run, run_code=run_code)
    assert res.ok and res.changed, res.message
    return calls


def _mitosis_commands():
    from workflows import mitosis_card
    seen = []

    def spy(cmd):
        seen.append(cmd)
        return 0, ""
    mitosis_card.verify_medic_card("/dev/sda", "hawkeye", run_shell=spy)
    return seen


def _forensics_commands():
    """card_forensics.diagnose, with the card mounting where it asked."""
    from provisioning import card_forensics
    seen, asked = [], set()

    def run(cmd):
        seen.append(cmd)
        if cmd.startswith("findmnt -n -o SOURCE /"):
            return "/dev/mmcblk0p2\n"
        if cmd.startswith("findmnt -n -o TARGET"):
            part = cmd.split()[4]
            if part in asked:
                return card_mount.SD_BOOT + "\n"
            asked.add(part)
            return ""
        if cmd.startswith("lsblk -J"):
            return json.dumps({"blockdevices": [_MEDIC_DISK, _card("sda")]})
        if cmd.startswith("lsblk -n -o PATH,FSTYPE"):
            return "/dev/sda\n/dev/sda1 vfat\n/dev/sda2 ext4\n"
        if "cat " in cmd and "cmdline.txt" in cmd:
            return "console=tty1 rootwait\n__rc=0"
        if "cat " in cmd:
            return "x\n__rc=0"
        return "__rc=1"
    card_forensics.diagnose(run=run)
    return seen


def _unwired_card_commands():
    """The card builders no screen reaches yet: they still must not use /tmp."""
    from provisioning import cable_birth, pi_imager
    cmds = pi_imager.apply_config_commands("/dev/sdb", "toml=1", user_data="#cloud-config\n",
                                           network_config="version: 2\n")
    cmds += pi_imager.reseed_commands("/dev/sdb", "#cloud-config\n", "rpios-2",
                                      network_config="version: 2\n")
    cmds += pi_imager.activate_account_commands("/dev/sdb", "pi", "$6$x$y",
                                                authorized_keys=["ssh-ed25519 AAAA k"])
    cmds += cable_birth.bake_commands("/dev/sdb") + cable_birth.bake_commands("/dev/mmcblk1")
    return cmds + _forensics_commands()


def _every_card_command():
    return (_sd_edit_commands(_card("sda")) + _sd_edit_commands(_card("mmcblk1", "p"))
            + _mitosis_commands() + _unwired_card_commands())


def test_the_card_code_mounts_only_into_the_pinned_folders():
    argvs = [a for cmd in _every_card_command() for a in sudo_argvs(cmd)]
    folders = {card_mount.SD_BOOT, card_mount.PIBOOT, card_mount.RESEED,
               card_mount.PIROOT, card_mount.PIROOT_USER}
    mounts = [a for a in argvs if a[0] == "/usr/bin/mount"]
    assert len(mounts) >= 10, "the stand-ins stopped reaching the mounts"
    for argv in argvs:
        assert not any(arg == "/tmp" or arg.startswith("/tmp/") for arg in argv), argv
        if argv[0] == "/usr/bin/mount":
            # card_forensics alone adds `ro`: it only ever reads (and is
            # dormant until a read-only rule is granted)
            assert argv[1] == "-o" and argv[2] in (OPTS, "ro," + OPTS), argv
            assert len(argv) == 5 and argv[4] in folders, argv
        elif argv[0] == "/usr/bin/install":
            assert argv[:-1] == _make_dir("x")[:-1] and argv[-1] in folders, argv
        elif argv[0] == "/usr/bin/umount":
            assert len(argv) == 2 and argv[1] in folders, argv
        elif argv[0] in ("/usr/bin/tee", "/usr/bin/touch"):
            assert any(argv[-1].startswith(f + "/") for f in folders), argv


@pytest.mark.parametrize("user", USERS)
def test_what_the_medic_runs_on_a_card_is_granted_exactly(policies, user):
    """The two card paths that run on the medic itself (the BUILD node-card
    edit and the clone check): every sudo command they actually emit is in
    the policy, word for word — the code and the policy cannot drift."""
    policy = policies[user][0]
    cmds = (_sd_edit_commands(_card("sda")) + _sd_edit_commands(_card("mmcblk1", "p"))
            + _mitosis_commands())
    argvs = [a for cmd in cmds for a in sudo_argvs(cmd)]
    assert any(a[0] == "/usr/bin/tee" for a in argvs) and \
        sum(a[0] == "/usr/bin/mount" for a in argvs) == 4, argvs
    refused = [a for a in argvs if not policy.allows(a)]
    assert not refused, "\n".join(" ".join(a) for a in refused)


@pytest.mark.parametrize("user", USERS)
def test_the_dormant_read_only_inspection_is_still_not_granted(policies, user):
    """card_forensics says it is dormant BECAUSE no read-only mount is
    granted; if one ever is, that note (and its classification in
    tests/test_privileged_commands.py) must change with it."""
    policy = policies[user][0]
    ro = [a for cmd in _forensics_commands() for a in sudo_argvs(cmd)
          if a[0] == "/usr/bin/mount"]
    assert ro and not any(policy.allows(a) for a in ro)


# --------------------------------------------------------------------------- #
# sudo never remembers a password for the app account
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("user", USERS)
def test_sudo_never_remembers_a_password_for_the_app_account(policies, user):
    policy, path = policies[user]
    assert policy.defaults == ["timestamp_timeout=0"]
    text = open(path, encoding="utf-8").read()
    lines = [ln for ln in text.splitlines() if ln.startswith("Defaults")]
    assert lines == [f"Defaults:{user} timestamp_timeout=0"], lines


@pytest.mark.skipif(shutil.which("visudo") is None and not os.path.exists("/usr/sbin/visudo"),
                    reason="visudo not installed here")
@pytest.mark.parametrize("user", USERS)
def test_the_policy_with_the_defaults_passes_visudo(policies, user):
    visudo = shutil.which("visudo") or "/usr/sbin/visudo"
    path = policies[user][1]
    assert f"Defaults:{user} timestamp_timeout=0" in open(path, encoding="utf-8").read()
    r = subprocess.run([visudo, "-cf", path], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


@pytest.mark.parametrize("user", USERS)
def test_render_refuses_a_policy_that_would_remember_a_password(tmp_path, user):
    src = tmp_path / "nodemedic"
    lines = open(TEMPLATE, encoding="utf-8").read().splitlines(True)
    kept = [ln for ln in lines if not ln.startswith("Defaults:")]
    assert len(kept) == len(lines) - 1          # exactly the one line is gone
    src.write_text("".join(kept))
    out = tmp_path / "out"
    r = subprocess.run(["bash", RENDER, str(src), str(out), "panel_backlight@1", user],
                       capture_output=True, text=True)
    assert r.returncode != 0 and not out.exists(), r.stdout + r.stderr
    assert "timestamp_timeout=0" in r.stderr
