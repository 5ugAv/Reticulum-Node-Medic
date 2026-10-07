"""The hardening scripts a clone's last steps run — provisioning/security/.

The keeper, 2026-10-08: every clone ends with its parent's hardening. The
original medic was hardened by hand with these scripts, which named its user
(nodemedic) throughout; a clone runs as pi. These tests hold the generalised
scripts to the two promises that matter:

* with no --user they do exactly what they always did on the original medic;
* apply_all.sh, the root supervisor the clone flow starts, ends every run in
  one of two states — all three locks confirmed, or all three rolled back.

apply_all.sh is RUN here, not just read: a copy with its two system paths and
its poll interval rewritten, its siblings replaced by stubs that record their
calls, and stubs for the few system tools a laptop lacks or that need root.
"""

import os
import re
import stat
import subprocess
import time

import pytest

from tests.srcutil import ROOT, src

SEC = os.path.join(ROOT, "provisioning", "security")
SCRIPTS = sorted(f for f in os.listdir(SEC) if f.endswith(".sh"))


@pytest.mark.parametrize("name", SCRIPTS)
def test_every_script_parses(name):
    r = subprocess.run(["bash", "-n", os.path.join(SEC, name)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_the_scripts_are_all_here():
    for name in ("apply_all.sh", "apply_sshd.sh", "rollback_sshd.sh", "apply_firewall.sh",
                 "rollback_firewall.sh", "apply_sudoers.sh", "rollback_sudoers.sh",
                 "render_sudoers.sh"):
        assert name in SCRIPTS, name


# --------------------------------------------------------------------------- #
# The default user is the original medic's, exactly as before
# --------------------------------------------------------------------------- #

def test_without_a_user_the_scripts_mean_the_original_medic():
    assert 'NM_USER="nodemedic"' in src("provisioning/security/apply_sudoers.sh")
    assert 'NM_USER="nodemedic"' in src("provisioning/security/rollback_sudoers.sh")
    # apply_sshd.sh has always taken the sudo caller first
    assert 'USER_NAME="${SUDO_USER:-nodemedic}"' in src("provisioning/security/apply_sshd.sh")
    assert 'NM_USER="${4:-nodemedic}"' in src("provisioning/security/render_sudoers.sh")
    # the scoped file keeps its name for every user, so rollback finds it
    for name in ("apply_sudoers.sh", "rollback_sudoers.sh"):
        assert 'DST="/etc/sudoers.d/010-nodemedic"' in src(f"provisioning/security/{name}")
    # the self-revert is OFF unless asked for: a human re-run is unchanged
    assert 'REVERT_MIN=""' in src("provisioning/security/apply_sudoers.sh")


def _blanket_re(user):
    """apply_sudoers.sh's own BLANKET_RE line, evaluated by bash for *user*."""
    line = next(ln for ln in src("provisioning/security/apply_sudoers.sh").splitlines()
                if ln.startswith("BLANKET_RE="))
    r = subprocess.run(["bash", "-c", f'NM_USER={user}; {line}; printf "%s" "$BLANKET_RE"'],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout


@pytest.mark.parametrize("line, blanket", [
    ("pi ALL=(ALL) NOPASSWD: ALL", True),           # Raspberry Pi OS / the card bake
    ("pi ALL=(ALL) NOPASSWD:ALL", True),            # cloud-init
    ("pi ALL=(ALL:ALL) NOPASSWD: ALL", True),
    ("  pi   ALL = (ALL) NOPASSWD: ALL  ", True),
    ("pi ALL=(ALL) NOPASSWD: ALL # by hand", True),
    ("pi ALL=(ALL) NOPASSWD: ALL, /usr/bin/true", True),
    ("pi ALL=(root) NOPASSWD: NM_BACKLIGHT, NM_WIFI", False),   # the scoped grant
    ("pi ALL=(ALL) NOPASSWD: ALLOWED_THINGS", False),
    ("pi ALL=(ALL:ALL) ALL", False),                # password sudo stays
    ("# pi ALL=(ALL) NOPASSWD: ALL", False),
    ("pi2 ALL=(ALL) NOPASSWD: ALL", False),
    ("nodemedic ALL=(ALL) NOPASSWD: ALL", False),   # another user's
])
def test_the_blanket_grant_is_recognised_in_every_form_it_takes(tmp_path, line, blanket):
    f = tmp_path / "rule"
    f.write_text(line + "\n")
    r = subprocess.run(["grep", "-Eq", _blanket_re("pi"), str(f)])
    assert (r.returncode == 0) is blanket, line


def test_the_card_bakes_blanket_file_is_the_one_removed():
    """prepare-card writes Pi OS's per-user file for a clone's pi; apply_sudoers
    removes that name (and the original medic's hyphen form) explicitly."""
    assert 'f"010_{user}-nopasswd"' in src("assets/scripts/prepare_card.py")
    apply = src("provisioning/security/apply_sudoers.sh")
    assert '"/etc/sudoers.d/010_${NM_USER}-nopasswd"' in apply
    assert '"/etc/sudoers.d/010-${NM_USER}-nopasswd"' in apply


def test_every_safety_step_of_the_sudoers_apply_survives():
    apply = src("provisioning/security/apply_sudoers.sh")
    order = ['visudo -cf "$TMP" ||', 'cp -a /etc/sudoers.d/. "$BACKUP/"',
             'install -o root -g root -m 0440 "$TMP" "$DST"', "if ! visudo -c >/dev/null; then",
             'AFTER="$(sudo -l -U "$NM_USER"', "grep -q '/usr/bin/tee /sys/class/backlight'"]
    at = [apply.index(s) for s in order]
    assert at == sorted(at), "the apply's safety steps are out of order"
    assert "auto-restoring backup" in apply
    # the self-revert can be confirmed by the SCOPED user: a file in its home
    assert 'CONFIRM_SENTINEL="$USER_HOME/.nodemedic-sudo-confirmed"' in apply
    # ...and is reboot-durable: a service run at every boot, plus the deadline
    assert "WantedBy=multi-user.target" in apply and "OnActiveSec=${REVERT_MIN}min" in apply


def test_the_ssh_apply_still_refuses_without_a_usable_key():
    sshd = src("provisioning/security/apply_sshd.sh")
    pre = sshd[sshd.index("== pre-flight"):sshd.index("== back up")]
    assert '[ -s "$AK" ]' in pre                         # always
    assert 'ssh-keygen -l -f "$AK"' in pre               # when the caller proved the login
    assert "BatchMode=yes" in pre                        # otherwise, loopback
    assert sshd.index("== pre-flight") < sshd.index("install -o root -g root -m 0644")


def test_a_second_firewall_apply_rearms_its_revert():
    fw = src("provisioning/security/apply_firewall.sh")
    arm = fw[fw.index("== arm self-revert"):]
    assert arm.index("systemctl stop nodemedic-fw-revert.timer") < \
        arm.index("systemd-run --unit=nodemedic-fw-revert")


# --------------------------------------------------------------------------- #
# apply_all.sh, run
# --------------------------------------------------------------------------- #

_STUB = """#!/bin/bash
echo "$(basename "$0") $* REVERT_MIN=${REVERT_MIN:-}" >> "$CTL/calls"
name="$(basename "$0" .sh)"
[ "$name" = apply_sudoers ] && [ "${*: -1}" != confirm ] && [ ! -e "$CTL/early_$name" ] \
    && echo "BACKUP_DIR=/root/nodemedic-sudoers-backup-test"
if [ -e "$CTL/fail_$name" ] || [ -e "$CTL/early_$name" ]; then exit 1; fi
case "$*" in *confirm*) [ -e "$CTL/fail_confirm_$name" ] && exit 1;; esac
exit 0
"""

_TOOLS = {
    "id": '#!/bin/bash\n[ "$1" = -u ] && { echo 0; exit 0; }\nexec /usr/bin/id "$@"\n',
    "install": '#!/bin/bash\n# only the form apply_all.sh uses: install -d ... DIR\n'
               'for a; do d="$a"; done\nmkdir -p "$d"\n',
    "flock": '#!/bin/bash\n[ -e "$CTL/locked" ] && exit 1\nexit 0\n',
    "getent": '#!/bin/bash\necho "$2:x:1000:1000::$CTL/home:/bin/bash"\n',
    "logger": "#!/bin/bash\nexit 0\n",
}


def _executable(path, text):
    path.write_text(text)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


@pytest.fixture
def rig(tmp_path):
    """A copy of apply_all.sh among stubbed siblings, with stubbed tools."""
    kit, ctl, bin_ = tmp_path / "kit", tmp_path / "ctl", tmp_path / "bin"
    for d in (kit / "sshd_config.d", kit / "nftables", ctl / "home", bin_):
        d.mkdir(parents=True)
    script = src("provisioning/security/apply_all.sh")
    for real, fake in (('STATUS_DIR="/run/nodemedic-harden"', f'STATUS_DIR="{ctl}/run"'),
                       ('LOG="/var/log/nodemedic-harden.log"', f'LOG="{ctl}/harden.log"'),
                       ("    sleep 2\n", "    sleep 0.05\n")):
        assert real in script, real
        script = script.replace(real, fake)
    (kit / "apply_all.sh").write_text(script)
    for name in ("apply_sshd", "rollback_sshd", "apply_firewall", "rollback_firewall",
                 "apply_sudoers", "rollback_sudoers", "render_sudoers"):
        _executable(kit / f"{name}.sh", _STUB)
    (kit / "sshd_config.d" / "01-nodemedic-hardening.conf").write_text("x\n")
    (kit / "nftables" / "nodemedic-ssh.nft").write_text("x\n")
    (kit / "sudoers.nodemedic").write_text("x\n")
    for name, text in _TOOLS.items():
        _executable(bin_ / name, text)
    env = dict(os.environ, CTL=str(ctl), PATH=f"{bin_}:{os.environ['PATH']}")

    class Rig:
        home = ctl / "home"
        status = ctl / "run" / "status"

        @staticmethod
        def touch(name):
            (ctl / name).write_text("")

        @staticmethod
        def run(*args, verdict=None):
            p = subprocess.Popen(["bash", str(kit / "apply_all.sh"), *args], env=env,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            deadline = time.time() + 20
            while verdict and p.poll() is None and time.time() < deadline:
                if Rig.status.exists() and Rig.status.read_text().strip() == "applied":
                    (Rig.home / f".nodemedic-harden-{verdict}").write_text("")
                    break
                time.sleep(0.02)
            p.communicate(timeout=20)
            calls = (ctl / "calls").read_text().splitlines() if (ctl / "calls").exists() else []
            state = Rig.status.read_text().strip() if Rig.status.exists() else ""
            return p.returncode, state, calls
    return Rig


def _names(calls):
    return [c.split()[0] + (" confirm" if " confirm " in f" {c} " else "") for c in calls]


def test_apply_all_applies_three_then_confirms_on_the_verdict(rig):
    code, state, calls = rig.run("--user", "pi", "--window", "60", verdict="confirm")
    assert (code, state) == (0, "confirmed")
    assert _names(calls) == ["apply_sshd.sh", "apply_firewall.sh", "apply_sudoers.sh",
                             "apply_sudoers.sh confirm", "apply_firewall.sh confirm",
                             "apply_sshd.sh confirm"]
    assert "--user pi --caller-proved-key-login" in calls[0]
    # the inner self-reverts outlast the supervisor's own window (60 s -> 11 min)
    assert calls[0].endswith("REVERT_MIN=11") and calls[1].endswith("REVERT_MIN=11")
    assert "--self-revert 11" in calls[2] and calls[2].split()[-2].endswith("sudoers.nodemedic")
    assert not os.path.exists(rig.home / ".nodemedic-harden-confirm")


def test_apply_all_rolls_all_three_back_on_the_rollback_verdict(rig):
    code, state, calls = rig.run("--user", "pi", "--window", "60", verdict="rollback")
    assert code == 1 and state == "rolled-back: the checking medic found a problem"
    names = _names(calls)
    assert names[:3] == ["apply_sshd.sh", "apply_firewall.sh", "apply_sudoers.sh"]
    assert names[3:] == ["rollback_sudoers.sh", "rollback_firewall.sh", "rollback_sshd.sh"]
    # back to exactly the backup this run made
    assert "--user pi /root/nodemedic-sudoers-backup-test" in calls[3]
    assert not any("confirm" in c for c in names)


def test_apply_all_rolls_back_by_itself_when_no_verdict_comes(rig):
    code, state, calls = rig.run("--user", "pi", "--window", "60")
    assert code == 1 and state.startswith("rolled-back: no word from the checking medic")
    assert _names(calls)[3:] == ["rollback_sudoers.sh", "rollback_firewall.sh",
                                 "rollback_sshd.sh"]


def test_a_failed_apply_undoes_only_what_was_tried(rig):
    rig.touch("fail_apply_firewall")
    code, state, calls = rig.run("--user", "pi", "--window", "60", verdict="confirm")
    assert code == 1 and state == "rolled-back: the SSH firewall did not go in"
    assert _names(calls) == ["apply_sshd.sh", "apply_firewall.sh",
                             "rollback_firewall.sh", "rollback_sshd.sh"]


def test_a_sudoers_apply_that_failed_before_its_backup_is_not_rolled_back(rig):
    rig.touch("early_apply_sudoers")          # refused at validation: nothing changed
    code, state, calls = rig.run("--user", "pi", "--window", "60", verdict="confirm")
    assert code == 1 and state == "rolled-back: the scoped sudo rules did not go in"
    assert "rollback_sudoers.sh" not in _names(calls)
    assert _names(calls)[-2:] == ["rollback_firewall.sh", "rollback_sshd.sh"]


def test_a_confirm_that_fails_rolls_everything_back(rig):
    rig.touch("fail_confirm_apply_firewall")
    code, state, calls = rig.run("--user", "pi", "--window", "60", verdict="confirm")
    assert code == 1 and state == "rolled-back: the SSH firewall could not be made permanent"
    assert _names(calls)[-3:] == ["rollback_sudoers.sh", "rollback_firewall.sh",
                                  "rollback_sshd.sh"]


@pytest.mark.parametrize("args", [["--user", "Pi"], ["--user", "root;id"], ["--user", ""],
                                  ["--user", "pi", "--window", "5"],
                                  ["--user", "pi", "--window", "x"]])
def test_apply_all_refuses_odd_input_and_changes_nothing(rig, args):
    code, state, calls = rig.run(*args)
    assert code == 1 and state.startswith("rolled-back:") and "nothing was changed" in state
    assert calls == []


def test_only_one_supervisor_at_a_time(rig):
    rig.touch("locked")
    code, state, calls = rig.run("--user", "pi", "--window", "60", verdict="confirm")
    assert code == 1 and calls == [] and state == ""     # the running one's status is left alone


def test_a_stale_verdict_cannot_decide_a_new_run(rig):
    (rig.home / ".nodemedic-harden-confirm").write_text("")    # left by an earlier run
    code, state, _calls = rig.run("--user", "pi", "--window", "60")
    assert state.startswith("rolled-back: no word"), state
