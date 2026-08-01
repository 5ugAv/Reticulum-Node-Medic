"""Activating the node's login account on the card itself.

The script is executed for real against a fake rootfs tree, so these tests
exercise the actual logic that touches /etc/passwd and /etc/shadow rather than
asserting on command strings.
"""

import base64
import os
import stat
import subprocess
import sys

import pytest

from provisioning import rootfs_user as ru

KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFIXTUREKEY/xyz nodemedic@nodemedic"
HASH = "$6$FIXTUREsalt0001$fixture/hash.not.real.0001"

# Exactly what the carried image ships (read off the real rootfs, 2026-08-01):
# the account EXISTS, in all the right groups, but disabled.
SHIPPED_PASSWD = (
    "root:x:0:0:root:/root:/bin/bash\n"
    "daemon:x:1:1:daemon:/usr/sbin:/usr/sbin/nologin\n"
    "pi:x:1000:1000::/home/pi:/usr/sbin/nologin\n"
    "avahi:x:101:104:Avahi mDNS daemon:/run/avahi-daemon:/usr/sbin/nologin\n")
SHIPPED_SHADOW = ("root:*:20622:0:99999:7:::\n"
                  "pi:!:20622:0:99999:7:::\n"
                  "avahi:!:20622::::::\n")


@pytest.fixture
def rootfs(tmp_path):
    (tmp_path / "etc").mkdir()
    (tmp_path / "home" / "pi").mkdir(parents=True)
    (tmp_path / "etc" / "passwd").write_text(SHIPPED_PASSWD)
    (tmp_path / "etc" / "shadow").write_text(SHIPPED_SHADOW)
    return tmp_path


def _run(rootfs, user="pi", pw=HASH, keys=(KEY,)):
    cmd = ru.activate_commands(str(rootfs), user, pw, list(keys))[0]
    blob = cmd.split("echo ")[1].split(" |")[0].strip("'")
    script = base64.b64decode(blob).decode()
    args = [str(rootfs), user, ru.LOGIN_SHELL, pw] + [k for k in keys]
    return subprocess.run([sys.executable, "-"] + args, input=script,
                          capture_output=True, text=True)


# --- the failure this exists to prevent ------------------------------------

def test_the_shipped_account_cannot_log_in():
    """Both halves of tonight's bug, as a check: nologin shell AND locked
    password. This is the state HOPE booted into, twice."""
    pw_line = [l for l in SHIPPED_PASSWD.splitlines() if l.startswith("pi:")][0]
    sh_line = [l for l in SHIPPED_SHADOW.splitlines() if l.startswith("pi:")][0]
    assert ru.is_login_capable(pw_line, sh_line) is False


def test_after_activation_the_account_can_log_in(rootfs):
    p = _run(rootfs)
    assert "ACTIVATE_OK" in p.stdout, p.stdout + p.stderr
    pw_line = [l for l in (rootfs / "etc" / "passwd").read_text().splitlines()
               if l.startswith("pi:")][0]
    sh_line = [l for l in (rootfs / "etc" / "shadow").read_text().splitlines()
               if l.startswith("pi:")][0]
    assert ru.is_login_capable(pw_line, sh_line) is True


# --- the individual changes -------------------------------------------------

def test_the_nologin_shell_is_replaced(rootfs):
    _run(rootfs)
    line = [l for l in (rootfs / "etc" / "passwd").read_text().splitlines()
            if l.startswith("pi:")][0]
    assert line.endswith(":/bin/bash")
    assert "nologin" not in line


def test_uid_gid_and_home_are_left_alone(rootfs):
    """The image already put pi in adm/dialout/sudo/gpio by GID. Renumbering
    would silently drop every one of those memberships."""
    _run(rootfs)
    line = [l for l in (rootfs / "etc" / "passwd").read_text().splitlines()
            if l.startswith("pi:")][0]
    parts = line.split(":")
    assert parts[2] == "1000" and parts[3] == "1000"
    assert parts[5] == "/home/pi"


def test_the_locked_password_is_replaced_with_the_hash(rootfs):
    _run(rootfs)
    line = [l for l in (rootfs / "etc" / "shadow").read_text().splitlines()
            if l.startswith("pi:")][0]
    assert line.split(":")[1] == HASH


def test_other_accounts_are_untouched(rootfs):
    _run(rootfs)
    passwd = (rootfs / "etc" / "passwd").read_text()
    shadow = (rootfs / "etc" / "shadow").read_text()
    assert "root:x:0:0:root:/root:/bin/bash" in passwd
    assert "daemon:x:1:1:daemon:/usr/sbin:/usr/sbin/nologin" in passwd
    assert "avahi:!:20622::::::" in shadow
    assert len(passwd.splitlines()) == 4


def test_the_key_lands_with_permissions_sshd_will_accept(rootfs):
    """sshd silently ignores authorized_keys that is group/world writable."""
    _run(rootfs)
    ak = rootfs / "home" / "pi" / ".ssh" / "authorized_keys"
    assert ak.read_text().strip() == KEY
    assert stat.S_IMODE(ak.stat().st_mode) == 0o600
    assert stat.S_IMODE(ak.parent.stat().st_mode) == 0o700


def test_running_twice_changes_nothing(rootfs):
    _run(rootfs)
    first = ((rootfs / "etc" / "passwd").read_text(),
             (rootfs / "etc" / "shadow").read_text(),
             (rootfs / "home" / "pi" / ".ssh" / "authorized_keys").read_text())
    p = _run(rootfs)
    assert "already active" in p.stdout
    assert first == ((rootfs / "etc" / "passwd").read_text(),
                     (rootfs / "etc" / "shadow").read_text(),
                     (rootfs / "home" / "pi" / ".ssh" / "authorized_keys").read_text())


def test_an_existing_key_is_kept_not_clobbered(rootfs):
    ssh = rootfs / "home" / "pi" / ".ssh"
    ssh.mkdir()
    (ssh / "authorized_keys").write_text("ssh-rsa AAAAsomeoneelse them@host\n")
    _run(rootfs)
    text = (ssh / "authorized_keys").read_text()
    assert "them@host" in text and KEY in text


def test_a_missing_account_fails_loudly_rather_than_silently(rootfs):
    """Silence is exactly how this bug hid for a whole imaging run."""
    p = _run(rootfs, user="nosuchuser")
    assert p.returncode == 2
    assert "ACTIVATE_FAIL" in p.stdout


# --- the login-capability check --------------------------------------------

@pytest.mark.parametrize("shell,pw,expect", [
    ("/bin/bash", "$6$real$hash", True),
    ("/usr/sbin/nologin", "$6$real$hash", False),   # shell disabled
    ("/bin/bash", "!", False),                      # password locked
    ("/bin/bash", "*", False),                      # no password
    ("/bin/false", "$6$real$hash", False),
])
def test_login_capability_covers_both_ways_an_account_is_disabled(shell, pw, expect):
    assert ru.is_login_capable(f"pi:x:1000:1000::/home/pi:{shell}",
                               f"pi:{pw}:20622:0:99999:7:::") is expect


def test_verify_commands_read_back_all_three_things_that_matter():
    cmds = " ".join(ru.verify_commands("/mnt/root", "pi"))
    assert "/etc/passwd" in cmds and "/etc/shadow" in cmds
    assert "authorized_keys" in cmds


# --- the imager does it automatically now ----------------------------------

def _medic_run(argv, **kw):
    if argv[:2] == ["findmnt", "-no"]:
        return (0, "/dev/mmcblk0p2")
    if argv[:2] == ["lsblk", "-no"] and "PKNAME" in argv:
        return (0, "mmcblk0")
    if argv[:2] == ["lsblk", "-dno"]:
        return (0, "mmcblk0 59.5G disk mmc  0 \nsdb 29.7G disk usb  1 Reader")
    return (0, "")


def test_flash_activates_the_account_on_every_card_it_writes():
    """The regression that cost three trips to the card reader."""
    from provisioning import pi_imager
    seen = []
    ok, msg = pi_imager.flash(
        "/dev/sdb", "hope", "pi", "Fixture-pw-1?", image_path="/tmp/x.img.xz",
        run=_medic_run, run_shell=lambda c: (seen.append(c), (0, ""))[1],
        pw_hasher=lambda p: HASH, authorized_keys=[KEY])
    assert ok, msg
    joined = " ".join(seen)
    assert "/dev/sdb2" in joined, "rootfs never mounted - account not activated"
    assert "base64 -d | sudo python3" in joined
    # and it must run against the ROOTFS, not the boot partition
    assert any("mount /dev/sdb2" in c for c in seen)


def test_a_card_whose_account_cannot_be_activated_is_reported_as_not_ready():
    """A card that boots but refuses every login is worse than no card,
    because it looks like it worked."""
    from provisioning import pi_imager

    def shell(cmd):
        if "/dev/sdb2" in cmd and "mount" in cmd:
            return (1, "mount: unknown filesystem type")
        return (0, "")

    ok, msg = pi_imager.flash(
        "/dev/sdb", "hope", "pi", "Fixture-pw-1?", image_path="/tmp/x.img.xz",
        run=_medic_run, run_shell=shell, pw_hasher=lambda p: HASH,
        authorized_keys=[KEY])
    assert ok is False
    assert "not ready" in msg and "unreachable" in msg
