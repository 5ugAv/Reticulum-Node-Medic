"""Activate the node's login account directly on the card — no first-boot magic.

Twice in one evening a Pi came up unreachable because its first-boot config was
in a format the carried image doesn't act on (2026-08-01, birthing HOPE):

  1. ``custom.toml`` — inert: this image has no ``raspberrypi-sys-mods/firstboot``.
  2. cloud-init ``user-data`` — inert for a different reason: the image already
     ships the account. ``pi:x:1000:1000::/home/pi:/usr/sbin/nologin`` exists,
     already in adm/dialout/sudo/gpio/i2c/spi, with a locked password. It is
     not missing, it is DISABLED — and cloud-init's ``users:`` block tries to
     *create* a user, so there was nothing for it to do.

The medic holds the card in its hands. Rather than write a request that some
boot-time agent may or may not honour, do the thing itself: set the login shell,
set the password hash, install the key. Deterministic, verifiable before the
card ever leaves, and it works the same on any Debian-family image because it
only touches /etc/passwd, /etc/shadow and ~/.ssh — the files that actually
decide whether a login succeeds.

Everything here builds strings; the caller runs them against a mounted rootfs.
"""

from __future__ import annotations

import base64
import shlex
from typing import List, Optional

#: Standard shell for an interactive Pi account.
LOGIN_SHELL = "/bin/bash"

#: uid/gid the Pi images use for the first human account.
DEFAULT_UID = 1000

_ACTIVATE_SCRIPT = r'''
import os, sys, time

root, user, shell, pw_hash = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
keys = [k for k in sys.argv[5:] if k.strip()]
changed = []

def path(p):
    return os.path.join(root, p.lstrip("/"))

# --- /etc/passwd: give the account a real login shell ----------------------
pw_file = path("/etc/passwd")
lines = open(pw_file).read().splitlines(True)
found = False
uid = gid = None
for i, line in enumerate(lines):
    parts = line.rstrip("\n").split(":")
    if len(parts) < 7 or parts[0] != user:
        continue
    found = True
    uid, gid = parts[2], parts[3]
    if parts[6] != shell:
        parts[6] = shell
        lines[i] = ":".join(parts) + "\n"
        changed.append("shell")
    break
if not found:
    print("ACTIVATE_FAIL: no %s account in /etc/passwd" % user)
    raise SystemExit(2)
open(pw_file, "w").writelines(lines)

# --- /etc/shadow: set the password, clear the lock -------------------------
if pw_hash:
    sh_file = path("/etc/shadow")
    lines = open(sh_file).read().splitlines(True)
    days = str(int(time.time() // 86400))
    seen = False
    for i, line in enumerate(lines):
        parts = line.rstrip("\n").split(":")
        if not parts or parts[0] != user:
            continue
        seen = True
        if parts[1] != pw_hash:
            parts[1] = pw_hash
            parts[2] = days
            lines[i] = ":".join(parts) + "\n"
            changed.append("password")
        break
    if not seen:
        lines.append("%s:%s:%s:0:99999:7:::\n" % (user, pw_hash, days))
        changed.append("password")
    open(sh_file, "w").writelines(lines)
    os.chmod(sh_file, 0o640)

# --- ~/.ssh/authorized_keys ------------------------------------------------
if keys:
    home = path("/home/%s" % user)
    ssh_dir = os.path.join(home, ".ssh")
    os.makedirs(ssh_dir, exist_ok=True)
    ak = os.path.join(ssh_dir, "authorized_keys")
    existing = open(ak).read().splitlines() if os.path.exists(ak) else []
    merged = list(existing)
    for k in keys:
        if k.strip() not in [e.strip() for e in merged]:
            merged.append(k.strip())
            changed.append("key")
    open(ak, "w").write("\n".join(merged) + "\n")
    os.chmod(ssh_dir, 0o700)
    os.chmod(ak, 0o600)
    if uid is not None:
        # Ownership must be the TARGET system's uid, not ours - sshd refuses to
        # read an authorized_keys file the account does not own.
        for p in (home, ssh_dir, ak):
            try:
                os.chown(p, int(uid), int(gid))
            except Exception as exc:
                print("ACTIVATE_WARN: chown %s: %s" % (p, exc))

print("ACTIVATE_OK: " + (",".join(sorted(set(changed))) if changed
                         else "already active"))
'''


def activate_commands(root_mnt: str, username: str, password_hash: str,
                      authorized_keys: Optional[List[str]] = None,
                      shell: str = LOGIN_SHELL) -> List[str]:
    """Commands that turn the image's disabled account into a usable login on a
    rootfs mounted at *root_mnt*. Idempotent — re-running reports "already
    active" and changes nothing."""
    b64 = base64.b64encode(_ACTIVATE_SCRIPT.encode()).decode()
    args = " ".join(shlex.quote(a) for a in
                    [root_mnt, username, shell, password_hash or ""]
                    + [k for k in (authorized_keys or []) if k.strip()])
    return [f"echo {shlex.quote(b64)} | base64 -d | sudo python3 - {args}"]


def verify_commands(root_mnt: str, username: str) -> List[str]:
    """Read back what actually landed, so the card can be checked BEFORE it
    goes back in the Pi — the whole point of holding it in our hands."""
    q = shlex.quote(root_mnt)
    return [
        f"grep '^{username}:' {q}/etc/passwd",
        f"sudo grep '^{username}:' {q}/etc/shadow | cut -d: -f1,2 | cut -c1-40",
        f"sudo ls -la {q}/home/{username}/.ssh/authorized_keys 2>/dev/null "
        f"|| echo 'NO authorized_keys'",
    ]


def is_login_capable(passwd_line: str, shadow_line: str = "") -> bool:
    """Would this account actually be able to log in?

    The exact check that would have caught tonight's failure before the card
    was ever put back: a ``nologin`` shell or a locked password means no.
    """
    parts = (passwd_line or "").rstrip("\n").split(":")
    if len(parts) < 7:
        return False
    if "nologin" in parts[6] or parts[6].endswith("/false"):
        return False
    if shadow_line:
        sparts = shadow_line.rstrip("\n").split(":")
        if len(sparts) < 2:
            return False
        pw = sparts[1]
        # "!" / "*" / "!!" are locked; a real crypt starts with $
        if not pw.startswith("$"):
            return False
    return True
